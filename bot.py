import os
import json
import hashlib
import asyncio
import aiohttp

from dotenv import load_dotenv
from telethon import TelegramClient, events
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
FOOTBALL_API_KEY = os.getenv("FOOTBALL_API_KEY")
API_HASH = os.getenv("API_HASH")
SOURCE_CHANNELS = [
    x.strip() for x in os.getenv("SOURCE_CHANNELS", "").split(",") if x.strip()
]

try:
    API_ID = int(os.getenv("API_ID", "0"))
    ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
except ValueError:
    API_ID = 0
    ADMIN_ID = 0

CHANNEL = "@yegnaLiverpool"
TEAM_ID = 40

os.makedirs("data", exist_ok=True)
FILE = "data/posted_news.json"

try:
    with open(FILE, "r", encoding="utf-8") as f:
        posted = set(json.load(f))
except Exception:
    posted = set()

news_enabled = True
live_enabled = True
last_live = {}


def save():
    with open(FILE, "w", encoding="utf-8") as f:
        json.dump(list(posted)[-5000:], f, ensure_ascii=False)


def clean_news(text):
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

    text = "\n".join(lines).strip()

    return f"{text}\n\n@yegnaLiverpool"


async def send_news(text, bot, photo=None):

    if not news_enabled:
        return

    news_id = hashlib.sha256(
        " ".join(text.lower().split()).encode()
    ).hexdigest()

    if news_id in posted:
        return

    caption = clean_news(text)

    try:
        if photo:
            await bot.send_photo(
                chat_id=CHANNEL,
                photo=photo,
                caption=caption[:1024]
            )
        else:
            await bot.send_message(
                chat_id=CHANNEL,
                text=caption
            )

        posted.add(news_id)
        save()
        print("✅ News sent")

    except Exception as e:
        print("❌ Send error:", e)


async def source_monitor(app):

    if not API_ID or not API_HASH:
        print("⚠️ API_ID/API_HASH missing")
        return

    if not SOURCE_CHANNELS:
        print("⚠️ SOURCE_CHANNELS missing")
        return

    client = TelegramClient(
        "liverpool_session",
        API_ID,
        API_HASH
    )

    await client.start()

    print("✅ Source monitor started")

    @client.on(events.NewMessage(chats=SOURCE_CHANNELS))
    async def new_message(event):

        try:
            text = event.raw_text or ""

            # Image exists
            if event.message.photo:
                photo = await event.message.download_media(
                    file=bytes
                )

                if text or photo:
                    await send_news(
                        text,
                        app.bot,
                        photo
                    )

            elif text:
                await send_news(
                    text,
                    app.bot
                )

        except Exception as e:
            print("❌ Source error:", e)

    await client.run_until_disconnected()


async def football_request(endpoint):

    if not FOOTBALL_API_KEY:
        return None

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://v3.football.api-sports.io/" + endpoint,
                headers={"x-apisports-key": FOOTBALL_API_KEY},
                timeout=20
            ) as r:
                if r.status != 200:
                    return None
                return await r.json()

    except Exception as e:
        print("API error:", e)
        return None


async def check_live(app):

    if not live_enabled:
        return

    data = await football_request(
        f"fixtures?team={TEAM_ID}&live=all"
    )

    if not data:
        return

    for game in data.get("response", []):

        fixture = game["fixture"]
        teams = game["teams"]
        goals = game["goals"]
        status = fixture["status"]

        fid = fixture["id"]

        state = (
            goals.get("home"),
            goals.get("away"),
            status.get("elapsed"),
            status.get("short")
        )

        if last_live.get(fid) == state:
            continue

        last_live[fid] = state

        home = teams["home"]["name"]
        away = teams["away"]["name"]

        message = (
            f"⚽ {home} {goals.get('home') or 0} - "
            f"{goals.get('away') or 0} {away}\n\n"
        )

        if status["short"] == "HT":
            message += "⏸️ የመጀመሪያው አጋማሽ ተጠናቋል"
        elif status["short"] in ["FT", "AET", "PEN"]:
            message += "🏁 ጨዋታው ተጠናቋል"
        else:
            message += f"⏱️ {status.get('elapsed') or 0}'\n🔴 ጨዋታው ቀጥሏል"

        message += "\n\n@yegnaLiverpool"

        try:
            await app.bot.send_message(
                chat_id=CHANNEL,
                text=message
            )
        except Exception as e:
            print("LIVE error:", e)


async def live_job(context):
    await check_live(context.application)


def admin(update):
    return update.effective_user and update.effective_user.id == ADMIN_ID


async def start(update, context):
    await update.message.reply_text("🔴 Liverpool Bot ተጀምሯል።")


async def status(update, context):
    if not admin(update):
        return

    await update.message.reply_text(
        f"📰 News: {'ON' if news_enabled else 'OFF'}\n"
        f"⚽ Live: {'ON' if live_enabled else 'OFF'}"
    )


async def on(update, context):
    global news_enabled

    if admin(update):
        news_enabled = True
        await update.message.reply_text("🟢 News ON")


async def off(update, context):
    global news_enabled

    if admin(update):
        news_enabled = False
        await update.message.reply_text("🔴 News OFF")


async def liveon(update, context):
    global live_enabled

    if admin(update):
        live_enabled = True
        await update.message.reply_text("⚽ Live ON")


async def liveoff(update, context):
    global live_enabled

    if admin(update):
        live_enabled = False
        await update.message.reply_text("⛔ Live OFF")


async def post_init(app):

    asyncio.create_task(
        source_monitor(app)
    )


def main():

    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN missing")

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("on", on))
    app.add_handler(CommandHandler("off", off))
    app.add_handler(CommandHandler("liveon", liveon))
    app.add_handler(CommandHandler("liveoff", liveoff))

    app.job_queue.run_repeating(
        live_job,
        interval=300,
        first=10
    )

    print("🔴 Liverpool Bot started")

    app.run_polling()


if __name__ == "__main__":
    main()
