import os
import json
import re
import hashlib
import asyncio
import aiohttp

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

ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

# MAIN CHANNEL - DON'T CHANGE
MAIN_CHANNEL = "@yegnaLiverpool"

# TEST CHANNEL
TEST_CHANNEL = "@yegnaLiverpoolET"

# During testing, bot posts here.
# After testing, change only this line to:
# TARGET_CHANNEL = MAIN_CHANNEL
TARGET_CHANNEL = TEST_CHANNEL

# Telegram source channels
SOURCE_CHANNELS = [
    x.strip()
    for x in os.getenv("SOURCE_CHANNELS", "").split(",")
    if x.strip()
]

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH")

LIVERPOOL_TEAM_ID = 40

# =========================================================
# DATA
# =========================================================

os.makedirs("data", exist_ok=True)

POSTED_FILE = "data/posted_news.json"
SOURCE_STATE_FILE = "data/source_state.json"

posted_news = set()
source_state = {}

try:
    if os.path.exists(POSTED_FILE):
        with open(
            POSTED_FILE,
            "r",
            encoding="utf-8"
        ) as f:
            posted_news = set(json.load(f))
except Exception:
    posted_news = set()

try:
    if os.path.exists(SOURCE_STATE_FILE):
        with open(
            SOURCE_STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:
            source_state = json.load(f)
except Exception:
    source_state = {}


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


def save_source_state():

    with open(
        SOURCE_STATE_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            source_state,
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
# ADMIN
# =========================================================

def is_admin(update: Update):

    if not update.effective_user:
        return False

    return update.effective_user.id == ADMIN_ID


# =========================================================
# LIVERPOOL FILTER
# =========================================================

LIVERPOOL_KEYWORDS = [

    "liverpool",
    "liverpool fc",
    "lfc",
    "anfield",
    "merseyside",

    "ሊቨርፑል",
    "ሊቨርፑል ኤፍሲ",
    "አንፊልድ",

    "arne slot",
    "slot",
    "አርኔ ስሎት",

    "andoni iraola",
    "iraola",
    "ኢራዎላ",

    "mohamed salah",
    "salah",
    "ሳላህ",

    "virgil van dijk",
    "van dijk",
    "ቫን ዳይክ",

    "alisson",
    "አሊሰን",

    "trent",
    "alexander-arnold",

    "mac allister",
    "ማክ አሊስተር",

    "szoboszlai",
    "ሶቦስላይ",

    "luis diaz",
    "diaz",
    "ዲያዝ",

    "darwin nunez",
    "nunez",
    "ኑኔዝ",

    "gakpo",
    "ጋክፖ",

    "diogo jota",
    "jota",
    "ጆታ",

    "florian wirtz",
    "wirtz",

    "frimpong",

    "milos kerkez",
    "kerkez",
]


def is_liverpool_news(text):

    if not text:
        return False

    lower = text.lower()

    for keyword in LIVERPOOL_KEYWORDS:

        if keyword.lower() in lower:
            return True

    return False


# =========================================================
# CLEAN TEXT
# =========================================================

def clean_news_text(text):

    if not text:
        return ""

    lines = []

    for line in text.splitlines():

        line = line.strip()

        if not line:
            continue

        lower = line.lower()

        # Remove headers
        if "liverpool news" in lower:
            continue

        if "🔴 liverpool news" in lower:
            continue

        # Remove source labels
        if lower.startswith("source:"):
            continue

        if lower.startswith("ምንጭ:"):
            continue

        if lower.startswith("source -"):
            continue

        # Remove Telegram links
        if "t.me/" in lower:
            continue

        if "telegram.me/" in lower:
            continue

        # Remove ALL @usernames
        line = re.sub(
            r"@\w+",
            "",
            line
        ).strip()

        # Remove common source/footer text
        if lower in [
            "follow us",
            "join us",
            "subscribe",
            "follow",
            "join",
            "ይከተሉን",
            "ተቀላቀሉ",
        ]:
            continue

        if line:
            lines.append(line)

    return "\n".join(lines).strip()


# =========================================================
# DUPLICATE HASH
# =========================================================

def make_hash(
    channel,
    post_id,
    text
):

    raw = (
        f"{channel}|"
        f"{post_id}|"
        f"{text}"
    )

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()


# =========================================================
# IMAGE DOWNLOAD
# =========================================================

async def download_image(
    url
):

    if not url:
        return None

    try:

        async with aiohttp.ClientSession() as session:

            async with session.get(
                url,
                timeout=20
            ) as response:

                if response.status != 200:
                    return None

                return await response.read()

    except Exception as e:

        print(
            "❌ Image download error:",
            e
        )

        return None


# =========================================================
# PUBLISH NEWS
# =========================================================

async def publish_news(
    text,
    image_url,
    channel,
    post_id,
    bot
):

    if not news_enabled:
        return

    if not text and not image_url:
        return

    # Liverpool filter
    if not is_liverpool_news(text):

        print(
            "⛔ Not Liverpool news - skipped"
        )

        return

    # Clean source username / links
    clean_text = clean_news_text(text)

    if not clean_text and not image_url:
        return

    # Duplicate check
    news_id = make_hash(
        channel,
        post_id,
        text
    )

    if news_id in posted_news:

        print(
            "⏭️ Duplicate - skipped"
        )

        return

    # Final footer
    if clean_text:

        final_text = (
            f"{clean_text}\n\n"
            f"@yegnaLiverpool"
        )

    else:

        final_text = "@yegnaLiverpool"

    try:

        # =================================================
        # IMAGE
        # =================================================

        if image_url:

            image = await download_image(
                image_url
            )

            if image:

                # Telegram caption max 1024
                if len(final_text) <= 1024:

                    await bot.send_photo(
                        chat_id=TARGET_CHANNEL,
                        photo=image,
                        caption=final_text
                    )

                else:

                    await bot.send_photo(
                        chat_id=TARGET_CHANNEL,
                        photo=image,
                        caption=final_text[:1024]
                    )

                    await bot.send_message(
                        chat_id=TARGET_CHANNEL,
                        text=final_text[1024:]
                    )

            else:

                await bot.send_message(
                    chat_id=TARGET_CHANNEL,
                    text=final_text
                )

        # =================================================
        # TEXT ONLY
        # =================================================

        else:

            await bot.send_message(
                chat_id=TARGET_CHANNEL,
                text=final_text
            )

        posted_news.add(
            news_id
        )

        save_posted_news()

        print(
            f"✅ NEWS POSTED: "
            f"{channel}/{post_id}"
        )

    except Exception as e:

        print(
            "❌ Telegram posting error:",
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

    if not API_ID or not API_HASH:

        print(
            "⚠️ API_ID/API_HASH missing."
        )

        return

    if not SOURCE_CHANNELS:

        print(
            "⚠️ SOURCE_CHANNELS is empty."
        )

        return

    telegram_client = TelegramClient(
        "liverpool_source_session",
        API_ID,
        API_HASH
    )

    await telegram_client.start()

    print(
        "✅ Source monitor started."
    )

    @telegram_client.on(
        events.NewMessage(
            chats=SOURCE_CHANNELS
        )
    )
    async def source_message(event):

        try:

            text = event.raw_text or ""

            # =================================================
            # IMPORTANT:
            # ONLY NEW MESSAGE
            # =================================================

            channel_name = ""

            try:

                chat = await event.get_chat()

                channel_name = (
                    getattr(
                        chat,
                        "username",
                        None
                    )
                    or str(
                        getattr(
                            chat,
                            "id",
                            ""
                        )
                    )
                )

            except Exception:

                channel_name = "unknown"

            post_id = str(
                event.id
            )

            # Already handled?
            source_key = (
                f"{channel_name}:{post_id}"
            )

            if source_key in source_state:

                return

            # Mark as seen immediately
            source_state[
                source_key
            ] = True

            save_source_state()

            # =================================================
            # IMAGE
            # =================================================

            image_url = None

            if event.message.photo:

                try:

                    downloaded = (
                        await event.download_media(
                            file=bytes
                        )
                    )

                    # Telethon returns bytes
                    if downloaded:

                        import base64

                        image_url = (
                            "data:image/jpeg;base64,"
                            + base64.b64encode(
                                downloaded
                            ).decode()
                        )

                except Exception as e:

                    print(
                        "Image extraction error:",
                        e
                    )

            print(
                "📰 New source post:",
                channel_name,
                post_id
            )

            # -------------------------------------------------
            # If image is available as bytes, publish separately
            # -------------------------------------------------

            if image_url and image_url.startswith(
                "data:"
            ):

                # We handle this directly
                if is_liverpool_news(text):

                    clean_text = clean_news_text(
                        text
                    )

                    if clean_text:

                        final_text = (
                            f"{clean_text}\n\n"
                            "@yegnaLiverpool"
                        )

                    else:

                        final_text = (
                            "@yegnaLiverpool"
                        )

                    news_id = make_hash(
                        channel_name,
                        post_id,
                        text
                    )

                    if news_id not in posted_news:

                        try:

                            import base64

                            image_bytes = base64.b64decode(
                                image_url.split(
                                    ",",
                                    1
                                )[1]
                            )

                            await application.bot.send_photo(
                                chat_id=TARGET_CHANNEL,
                                photo=image_bytes,
                                caption=final_text[:1024]
                            )

                            if len(final_text) > 1024:

                                await application.bot.send_message(
                                    chat_id=TARGET_CHANNEL,
                                    text=final_text[1024:]
                                )

                            posted_news.add(
                                news_id
                            )

                            save_posted_news()

                            print(
                                "✅ Image + news posted"
                            )

                        except Exception as e:

                            print(
                                "❌ Image posting error:",
                                e
                            )

                else:

                    print(
                        "⛔ Non-Liverpool post skipped."
                    )

                return

            # Normal text-only post
            await publish_news(
                text,
                None,
                channel_name,
                post_id,
                application.bot
            )

        except Exception as e:

            print(
                "❌ Source message error:",
                e
            )

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
# LIVE MATCH
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

        current_state = {
            "home_score": home_score,
            "away_score": away_score,
            "minute": minute,
            "status": match_status
        }

        previous_state = last_live_state.get(
            fixture_id
        )

        if previous_state == current_state:
            continue

        last_live_state[
            fixture_id
        ] = current_state

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
            "\n\n@yegnaLiverpool"
        )

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
                "❌ LIVE posting error:",
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
# COMMANDS
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "🔴 Liverpool Bot በስራ ላይ ነው።"
    )


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
        f"🧪 Test: {TEST_CHANNEL}\n"
        f"🔴 Main: {MAIN_CHANNEL}\n"
        f"📢 Current: {TARGET_CHANNEL}"
    )


async def news_on(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    global news_enabled

    if not is_admin(update):
        return

    news_enabled = True

    await update.message.reply_text(
        "🟢 News ON"
    )


async def news_off(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    global news_enabled

    if not is_admin(update):
        return

    news_enabled = False

    await update.message.reply_text(
        "🔴 News OFF"
    )


async def live_on(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    global live_enabled

    if not is_admin(update):
        return

    live_enabled = True

    await update.message.reply_text(
        "⚽ LIVE ON"
    )


async def live_off(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    global live_enabled

    if not is_admin(update):
        return

    live_enabled = False

    await update.message.reply_text(
        "⛔ LIVE OFF"
    )


async def sources(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin(update):
        return

    if not SOURCE_CHANNELS:

        await update.message.reply_text(
            "⚠️ SOURCE_CHANNELS ባዶ ነው።"
        )

        return

    source_list = "\n".join(
        f"• {channel}"
        for channel in SOURCE_CHANNELS
    )

    await update.message.reply_text(
        "📰 Sources:\n\n"
        + source_list
    )


# =========================================================
# STARTUP
# =========================================================

async def post_init(
    application
):

    print(
        "🔴 Liverpool Bot started!"
    )

    asyncio.create_task(
        start_source_monitor(
            application
        )
    )


# =========================================================
# MAIN
# =========================================================

def main():

    if not BOT_TOKEN:

        raise ValueError(
            "BOT_TOKEN አልተገኘም።"
        )

    if not ADMIN_ID:

        raise ValueError(
            "ADMIN_ID አልተገኘም።"
        )

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

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

    # LIVE check every 5 minutes
    application.job_queue.run_repeating(
        live_job,
        interval=300,
        first=10
    )

    print(
        "🚀 Liverpool Bot running..."
    )

    application.run_polling()


if __name__ == "__main__":
    main()
