import os
import json
import hashlib
import asyncio
import aiohttp

from dotenv import load_dotenv
from telethon import TelegramClient
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

load_dotenv()

# ==============================
# CONFIG
# ==============================

BOT_TOKEN = os.getenv("BOT_TOKEN")
FOOTBALL_API_KEY = os.getenv("FOOTBALL_API_KEY")
API_HASH = os.getenv("API_HASH")

try:
    API_ID = int(os.getenv("API_ID", "0"))
    ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
except:
    API_ID = 0
    ADMIN_ID = 0

TARGET_CHANNEL = "@yegnaLiverpool"

SOURCE_CHANNELS = [
    x.strip()
    for x in os.getenv("SOURCE_CHANNELS", "").split(",")
    if x.strip()
]

TEAM_ID = 40

# ==============================
# STORAGE
# ==============================

os.makedirs("data", exist_ok=True)

POSTED_FILE = "data/posted_news.json"

try:
    with open(POSTED_FILE, "r", encoding="utf-8") as f:
        posted_news = set(json.load(f))
except:
    posted_news = set()


def save_posted():
    with open(POSTED_FILE, "w", encoding="utf-8") as f:
        json.dump(
            list(posted_news)[-5000:],
            f,
            ensure_ascii=False
        )


# ==============================
# CLEAN TEXT
# ==============================

def clean_text(text):

    lines = []

    for line in text.splitlines():

        line = line.strip()
        low = line.lower()

        if not line:
            continue

        if "liverpool news" in low:
            continue

        if low.startswith("source:"):
            continue

        if low.startswith("ምንጭ:"):
            continue

        if "@yegnaLiverpool" in line:
            continue

        lines.append(line)

    result = "\n".join(lines).strip()

    if not result:
        result = "🔴 Liverpool News"

    return result + "\n\n@yegnaLiverpool"


# ==============================
# HASH
# ==============================

def make_hash(text):

    return hashlib.sha256(
        " ".join(text.lower().split()).encode("utf-8")
    ).hexdigest()


# ==============================
# TELEGRAM SOURCE
# ==============================

async def fetch_source_news(app):

    if not API_ID or not API_HASH:
        print("❌ API_ID/API_HASH missing")
        return

    if not SOURCE_CHANNELS:
        print("❌ SOURCE_CHANNELS missing")
        return

    client = TelegramClient(
        "liverpool_session",
        API_ID,
        API_HASH
    )

    try:

        await client.start()

        print("✅ Telegram source connected")

        for channel in SOURCE_CHANNELS:

            try:

                print(f"🔎 Checking {channel}")

                messages = await client.get_messages(
                    channel,
                    limit=5
                )

                for msg in reversed(messages):

                    if not msg:
                        continue

                    text = msg.text or ""

                    photo = None

                    # ==========================
                    # DOWNLOAD PHOTO
                    # ==========================

                    if msg.photo:

                        photo = await client.download_media(
                            msg,
                            file=bytes
                        )

                    if not text and not photo:
                        continue

                    # ==========================
                    # DUPLICATE
                    # ==========================

                    unique_text = (
                        text
                        + "_"
                        + str(msg.id)
                        + "_"
                        + channel
                    )

                    news_id = make_hash(unique_text)

                    if news_id in posted_news:
                        continue

                    caption = clean_text(text)

                    # ==========================
                    # SEND
                    # ==========================

                    if photo:

                        await app.bot.send_photo(
                            chat_id=TARGET_CHANNEL,
                            photo=photo,
                            caption=caption[:1024]
                        )

                    else:

                        await app.bot.send_message(
                            chat_id=TARGET_CHANNEL,
                            text=caption
                        )

                    posted_news.add(news_id)
                    save_posted()

                    print(
                        f"✅ Sent from {channel}"
                    )

            except Exception as e:

                print(
                    f"❌ Error reading {channel}:",
                    e
                )

    except Exception as e:

        print(
            "❌ Telegram connection error:",
            e
        )

    finally:

        await client.disconnect()


# ==============================
# FOOTBALL API
# ==============================

async def football_request(endpoint):

    if not FOOTBALL_API_KEY:
        return None

    try:

        async with aiohttp.ClientSession() as session:

            async with session.get(
                "https://v3.football.api-sports.io/"
                + endpoint,
                headers={
                    "x-apisports-key":
                    FOOTBALL_API_KEY
                },
                timeout=20
            ) as response:

                if response.status != 200:
                    return None

                return await response.json()

    except Exception as e:

        print(
            "Football API error:",
            e
        )

        return None


# ==============================
# LIVE MATCH
# ==============================

last_live = {}


async def check_live(app):

    data = await football_request(
        f"fixtures?team={TEAM_ID}&live=all"
    )

    if not data:
        return

    for game in data.get(
        "response",
        []
    ):

        fixture = game["fixture"]
        teams = game["teams"]
        goals = game["goals"]
        status = fixture["status"]

        fixture_id = fixture["id"]

        state = {
            "home": goals.get("home"),
            "away": goals.get("away"),
            "minute": status.get("elapsed"),
            "status": status.get("short")
        }

        if last_live.get(fixture_id) == state:
            continue

        last_live[fixture_id] = state

        home = teams["home"]["name"]
        away = teams["away"]["name"]

        message = (
            f"⚽ {home} "
            f"{goals.get('home') or 0} - "
            f"{goals.get('away') or 0} "
            f"{away}\n\n"
        )

        if status.get("short") == "HT":

            message += (
                "⏸️ የመጀመሪያው አጋማሽ "
                "ተጠናቋል"
            )

        elif status.get("short") in [
            "FT",
            "AET",
            "PEN"
        ]:

            message += (
                "🏁 ጨዋታው ተጠናቋል"
            )

        else:

            message += (
                f"⏱️ {status.get('elapsed') or 0}'\n"
                "🔴 ጨዋታው ቀጥሏል"
            )

        message += "\n\n@yegnaLiverpool"

        try:

            await app.bot.send_message(
                chat_id=TARGET_CHANNEL,
                text=message
            )

            print("⚽ LIVE sent")

        except Exception as e:

            print(
                "LIVE error:",
                e
            )


async def live_job(context):

    await check_live(
        context.application
    )


# ==============================
# ADMIN
# ==============================

def is_admin(update):

    return (
        update.effective_user
        and update.effective_user.id == ADMIN_ID
    )


async def start(update, context):

    await update.message.reply_text(
        "🔴 Liverpool Bot started"
    )


async def status(update, context):

    if not is_admin(update):
        return

    await update.message.reply_text(
        "🔴 Liverpool Bot is running"
    )


# ==============================
# STARTUP
# ==============================

async def post_init(app):

    # Check source channels immediately
    await fetch_source_news(app)

    print(
        "✅ Source check completed"
    )


# ==============================
# MAIN
# ==============================

def main():

    if not BOT_TOKEN:
        raise ValueError(
            "BOT_TOKEN missing"
        )

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    app.add_handler(
        CommandHandler(
            "status",
            status
        )
    )

    app.job_queue.run_repeating(
        live_job,
        interval=300,
        first=30
    )

    print(
        "🔴 Liverpool Bot started!"
    )

    app.run_polling()


if __name__ == "__main__":
    main()

