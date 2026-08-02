import os
import json
import hashlib
import asyncio
import aiohttp
import tempfile

from dotenv import load_dotenv
from telethon import TelegramClient, events

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

load_dotenv()


# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
FOOTBALL_API_KEY = os.getenv("FOOTBALL_API_KEY")

try:
    ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
except ValueError:
    ADMIN_ID = 0

TARGET_CHANNEL = "@yegnaLiverpool"


# =========================================================
# TELEGRAM SOURCE CHANNELS
# =========================================================

SOURCE_CHANNELS = [
    x.strip()
    for x in os.getenv("SOURCE_CHANNELS", "").split(",")
    if x.strip()
]


# =========================================================
# TELEGRAM API
# =========================================================

try:
    API_ID = int(os.getenv("API_ID", "0"))
except ValueError:
    API_ID = 0

API_HASH = os.getenv("API_HASH")


# =========================================================
# LIVERPOOL TEAM ID
# =========================================================

LIVERPOOL_TEAM_ID = 40


# =========================================================
# DATA
# =========================================================

os.makedirs("data", exist_ok=True)

POSTED_FILE = "data/posted_news.json"

posted_news = set()


if os.path.exists(POSTED_FILE):

    try:

        with open(
            POSTED_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            posted_news = set(
                json.load(f)
            )

    except Exception:

        posted_news = set()


def save_posted_news():

    with open(
        POSTED_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            list(posted_news)[-5000:],
            f,
            ensure_ascii=False,
            indent=2
        )


# =========================================================
# BOT STATE
# =========================================================

news_enabled = True
live_enabled = True

last_live_state = {}


# =========================================================
# ADMIN CHECK
# =========================================================

def is_admin(update: Update):

    if not update.effective_user:
        return False

    return (
        update.effective_user.id
        == ADMIN_ID
    )


# =========================================================
# DUPLICATE CHECK
# =========================================================

def make_hash(text):

    clean = " ".join(
        text.lower().split()
    )

    return hashlib.sha256(
        clean.encode("utf-8")
    ).hexdigest()


# =========================================================
# FORMAT NEWS
# =========================================================

def format_news(text):

    cleaned_lines = []

    for line in text.splitlines():

        line = line.strip()

        if not line:
            continue

        lower = line.lower()

        # -------------------------------------------------
        # REMOVE LIVERPOOL NEWS HEADER
        # -------------------------------------------------

        if "liverpool news" in lower:
            continue

        # -------------------------------------------------
        # REMOVE SOURCE LINES
        # -------------------------------------------------

        if lower.startswith("source:"):
            continue

        if lower.startswith("ምንጭ:"):
            continue

        if lower.startswith("source -"):
            continue

        if lower.startswith("ምንጭ -"):
            continue

        # -------------------------------------------------
        # REMOVE OLD CHANNEL FOOTER
        # -------------------------------------------------

        if "@yegnaLiverpool" in line:
            continue

        cleaned_lines.append(line)

    clean_text = "\n".join(
        cleaned_lines
    ).strip()

    if not clean_text:

        return "@yegnaLiverpool"

    # -------------------------------------------------
    # FINAL FORMAT
    #
    # NEWS
    #
    # @yegnaLiverpool
    # -------------------------------------------------

    return (
        f"{clean_text}\n\n"
        f"@yegnaLiverpool"
    )


# =========================================================
# PUBLISH NEWS + IMAGE
# =========================================================

async def publish_news(
    text,
    bot,
    image_path=None
):

    if not news_enabled:
        return

    if not text and not image_path:
        return

    # -------------------------------------------------
    # DUPLICATE CHECK
    # -------------------------------------------------

    news_id = None

    if text:

        news_id = make_hash(text)

        if news_id in posted_news:

            print(
                "⏭️ Duplicate news skipped"
            )

            return

    # -------------------------------------------------
    # FORMAT NEWS
    # -------------------------------------------------

    message = ""

    if text:

        message = format_news(text)

    # -------------------------------------------------
    # SEND IMAGE + NEWS
    # -------------------------------------------------

    try:

        if image_path and os.path.exists(
            image_path
        ):

            print(
                "🖼️ Sending image + news..."
            )

            caption = message

            # Telegram photo caption limit
            if len(caption) > 1024:

                caption = (
                    caption[:1020]
                    + "..."
                )

            with open(
                image_path,
                "rb"
            ) as photo_file:

                await bot.send_photo(
                    chat_id=TARGET_CHANNEL,
                    photo=photo_file,
                    caption=caption
                )

            print(
                "✅ Image + news posted"
            )

        # -------------------------------------------------
        # IF THERE IS NO IMAGE
        # -------------------------------------------------

        else:

            if not message:

                return

            await bot.send_message(
                chat_id=TARGET_CHANNEL,
                text=message
            )

            print(
                "✅ News posted without image"
            )

        # -------------------------------------------------
        # SAVE AS POSTED
        # -------------------------------------------------

        if news_id:

            posted_news.add(
                news_id
            )

            save_posted_news()

    except Exception as e:

        print(
            "❌ News posting error:",
            e
        )


# =========================================================
# TELEGRAM SOURCE MONITOR
# =========================================================

telegram_client = None


async def start_source_monitor(
    application
):

    global telegram_client

    # -------------------------------------------------
    # CHECK API ID / HASH
    # -------------------------------------------------

    if not API_ID or not API_HASH:

        print(
            "⚠️ API_ID/API_HASH not configured."
        )

        return

    # -------------------------------------------------
    # CHECK SOURCES
    # -------------------------------------------------

    if not SOURCE_CHANNELS:

        print(
            "⚠️ No source channels configured."
        )

        return

    # -------------------------------------------------
    # START TELETHON
    # -------------------------------------------------

    telegram_client = TelegramClient(
        "liverpool_source_session",
        API_ID,
        API_HASH
    )

    await telegram_client.start()

    print(
        "✅ Telegram source monitor started."
    )

    # =================================================
    # NEW SOURCE MESSAGE
    # =================================================

    @telegram_client.on(
        events.NewMessage(
            chats=SOURCE_CHANNELS
        )
    )
    async def source_message(event):

        image_path = None

        try:

            # -------------------------------------------------
            # GET SOURCE TEXT
            # -------------------------------------------------

            text = event.raw_text or ""

            # -------------------------------------------------
            # GET SOURCE IMAGE
            # -------------------------------------------------

            if event.message.photo:

                print(
                    "🖼️ Source image detected"
                )

                temp_dir = tempfile.gettempdir()

                image_path = (
                    await event.message.download_media(
                        file=temp_dir
                    )
                )

                if image_path:

                    print(
                        "✅ Source image downloaded:"
                    )

                    print(
                        image_path
                    )

            # -------------------------------------------------
            # IGNORE EMPTY POSTS
            # -------------------------------------------------

            if not text and not image_path:

                return

            print(
                "📰 New source news received"
            )

            # -------------------------------------------------
            # PUBLISH
            # -------------------------------------------------

            await publish_news(
                text,
                application.bot,
                image_path
            )

        except Exception as e:

            print(
                "❌ Source error:",
                e
            )

        finally:

            # -------------------------------------------------
            # DELETE TEMP IMAGE
            # -------------------------------------------------

            if image_path:

                try:

                    if os.path.exists(
                        image_path
                    ):

                        os.remove(
                            image_path
                        )

                except Exception:

                    pass

    # -------------------------------------------------
    # KEEP MONITOR RUNNING
    # -------------------------------------------------

    await telegram_client.run_until_disconnected()


# =========================================================
# FOOTBALL API
# =========================================================

async def football_request(
    endpoint
):

    if not FOOTBALL_API_KEY:

        print(
            "⚠️ FOOTBALL_API_KEY missing."
        )

        return None

    url = (
        "https://v3.football.api-sports.io/"
        + endpoint
    )

    headers = {
        "x-apisports-key":
        FOOTBALL_API_KEY
    }

    try:

        async with aiohttp.ClientSession() as session:

            async with session.get(
                url,
                headers=headers,
                timeout=20
            ) as response:

                if response.status != 200:

                    print(
                        "Football API error:",
                        response.status
                    )

                    return None

                return await response.json()

    except Exception as e:

        print(
            "Football API request error:",
            e
        )

        return None


# =========================================================
# CHECK LIVERPOOL LIVE MATCH
# =========================================================

async def check_liverpool_live(
    application
):

    if not live_enabled:

        return

    data = await football_request(
        f"fixtures?team={LIVERPOOL_TEAM_ID}&live=all"
    )

    if not data:

        return

    fixtures = data.get(
        "response",
        []
    )

    # -------------------------------------------------
    # NO LIVE MATCH
    # -------------------------------------------------

    if not fixtures:

        return

    for fixture in fixtures:

        fixture_info = fixture.get(
            "fixture",
            {}
        )

        teams = fixture.get(
            "teams",
            {}
        )

        goals = fixture.get(
            "goals",
            {}
        )

        status = fixture_info.get(
            "status",
            {}
        )

        fixture_id = fixture_info.get(
            "id"
        )

        if not fixture_id:

            continue

        home = teams.get(
            "home",
            {}
        )

        away = teams.get(
            "away",
            {}
        )

        home_name = home.get(
            "name",
            "Home"
        )

        away_name = away.get(
            "name",
            "Away"
        )

        home_score = goals.get(
            "home"
        )

        away_score = goals.get(
            "away"
        )

        minute = status.get(
            "elapsed"
        )

        match_status = status.get(
            "short"
        )

        # -------------------------------------------------
        # CURRENT STATE
        # -------------------------------------------------

        current_state = {

            "home_score":
                home_score,

            "away_score":
                away_score,

            "minute":
                minute,

            "status":
                match_status
        }

        previous_state = (
            last_live_state.get(
                fixture_id
            )
        )

        # -------------------------------------------------
        # NOTHING CHANGED
        # -------------------------------------------------

        if previous_state == current_state:

            continue

        last_live_state[
            fixture_id
        ] = current_state

        # =================================================
        # LIVE MESSAGE
        # =================================================

        message = (

            f"⚽ {home_name} "
            f"{home_score or 0} - "
            f"{away_score or 0} "
            f"{away_name}\n\n"
        )

        if minute:

            message += (
                f"⏱️ {minute}'\n\n"
            )

        if match_status == "HT":

            message += (
                "⏸️ የመጀመሪያው አጋማሽ "
                "ተጠናቋል"
            )

        elif match_status in (
            "FT",
            "AET",
            "PEN"
        ):

            message += (
                "🏁 ጨዋታው ተጠናቋል"
            )

        else:

            message += (
                "🔴 ጨዋታው ቀጥሏል"
            )

        message += (
            "\n\n"
            "@yegnaLiverpool"
        )

        # -------------------------------------------------
        # SEND LIVE UPDATE
        # -------------------------------------------------

        try:

            await application.bot.send_message(
                chat_id=TARGET_CHANNEL,
                text=message
            )

            print(
                "⚽ LIVE update posted"
            )

        except Exception as e:

            print(
                "LIVE posting error:",
                e
            )


# =========================================================
# LIVE JOB
# =========================================================

async def live_job(
    context: ContextTypes.DEFAULT_TYPE
):

    await check_liverpool_live(
        context.application
    )


# =========================================================
# START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "🔴 Liverpool Admin Bot ተጀምሯል።"
    )


# =========================================================
# HELP
# =========================================================

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin(update):

        return

    await update.message.reply_text(

        "👑 ADMIN COMMANDS\n\n"

        "/status\n"
        "/on\n"
        "/off\n"
        "/liveon\n"
        "/liveoff\n"
        "/sources"
    )


# =========================================================
# STATUS
# =========================================================

async def status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin(update):

        return

    news_status = (

        "🟢 ON"
        if news_enabled
        else "🔴 OFF"
    )

    live_status = (

        "🟢 ON"
        if live_enabled
        else "🔴 OFF"
    )

    await update.message.reply_text(

        f"📰 News: {news_status}\n"
        f"⚽ Live: {live_status}\n"
        f"📢 Channel: {TARGET_CHANNEL}"
    )


# =========================================================
# NEWS ON
# =========================================================

async def news_on(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    global news_enabled

    if not is_admin(update):

        return

    news_enabled = True

    await update.message.reply_text(
        "🟢 News posting ON"
    )


# =========================================================
# NEWS OFF
# =========================================================

async def news_off(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    global news_enabled

    if not is_admin(update):

        return

    news_enabled = False

    await update.message.reply_text(
        "🔴 News posting OFF"
    )


# =========================================================
# LIVE ON
# =========================================================

async def live_on(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    global live_enabled

    if not is_admin(update):

        return

    live_enabled = True

    await update.message.reply_text(
        "⚽ LIVE monitoring ON"
    )


# =========================================================
# LIVE OFF
# =========================================================

async def live_off(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    global live_enabled

    if not is_admin(update):

        return

    live_enabled = False

    await update.message.reply_text(
        "⛔ LIVE monitoring OFF"
    )


# =========================================================
# SOURCES
# =========================================================

async def sources(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin(update):

        return

    if not SOURCE_CHANNELS:

        await update.message.reply_text(

            "⚠️ የዜና ምንጮች "
            "አልተጨመሩም።"
        )

        return

    source_list = "\n".join(

        f"• {channel}"
        for channel in SOURCE_CHANNELS
    )

    await update.message.reply_text(

        "📰 የTelegram ምንጮች:\n\n"
        + source_list
    )


# =========================================================
# STARTUP
# =========================================================

async def post_init(
    application
):

    asyncio.create_task(

        start_source_monitor(
            application
        )
    )


# =========================================================
# MAIN
# =========================================================

def main():

    # -------------------------------------------------
    # CHECK BOT TOKEN
    # -------------------------------------------------

    if not BOT_TOKEN:

        raise ValueError(
            "BOT_TOKEN አልተገኘም።"
        )

    # -------------------------------------------------
    # CHECK ADMIN ID
    # -------------------------------------------------

    if not ADMIN_ID:

        raise ValueError(
            "ADMIN_ID አልተገኘም።"
        )

    # -------------------------------------------------
    # CREATE APPLICATION
    # -------------------------------------------------

    application = (

        Application.builder()

        .token(
            BOT_TOKEN
        )

        .post_init(
            post_init
        )

        .build()
    )

    # =================================================
    # COMMANDS
    # =================================================

    application.add_handler(

        CommandHandler(
            "start",
            start
        )
    )

    application.add_handler(

        CommandHandler(
            "help",
            help_command
        )
    )

    application.add_handler(

        CommandHandler(
            "status",
            status
        )
    )

    application.add_handler(

        CommandHandler(
            "on",
            news_on
        )
    )

    application.add_handler(

        CommandHandler(
            "off",
            news_off
        )
    )

    application.add_handler(

        CommandHandler(
            "liveon",
            live_on
        )
    )

    application.add_handler(

        CommandHandler(
            "liveoff",
            live_off
        )
    )

    application.add_handler(

        CommandHandler(
            "sources",
            sources
        )
    )

    # =================================================
    # LIVE CHECK EVERY 5 MINUTES
    # =================================================

    application.job_queue.run_repeating(

        live_job,

        interval=300,

        first=10
    )

    # =================================================
    # START BOT
    # =================================================

    print(
        "🔴 Liverpool Admin Bot started!"
    )

    application.run_polling()


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    main()

