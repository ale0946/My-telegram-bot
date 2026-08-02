import os
import json
import hashlib
import requests

from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

TARGET_CHANNEL = "@yegnaLiverpool"

SOURCE_CHANNELS = [
    x.strip()
    for x in os.getenv("SOURCE_CHANNELS", "").split(",")
    if x.strip()
]

# =========================================================
# DATA
# =========================================================

os.makedirs("data", exist_ok=True)

POSTED_FILE = "data/posted_news.json"

try:
    with open(POSTED_FILE, "r", encoding="utf-8") as f:
        posted_news = set(json.load(f))
except Exception:
    posted_news = set()


def save_posted():
    with open(POSTED_FILE, "w", encoding="utf-8") as f:
        json.dump(
            list(posted_news)[-5000:],
            f,
            ensure_ascii=False,
            indent=2
        )


# =========================================================
# CLEAN NEWS
# =========================================================

def clean_text(text):
    if not text:
        return ""

    lines = []

    for line in text.splitlines():
        line = line.strip()

        if not line:
            continue

        low = line.lower()

        # Remove unwanted headers
        if "liverpool news" in low:
            continue

        # Remove source footer
        if low.startswith("source:"):
            continue

        if low.startswith("ምንጭ:"):
            continue

        # Remove our own channel
        if "@yegnaLiverpool" in line:
            continue

        lines.append(line)

    result = "\n".join(lines).strip()

    if not result:
        return ""

    return result


# =========================================================
# HASH
# =========================================================

def make_hash(channel, post_id, text):
    raw = f"{channel}:{post_id}:{text}"
    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()


# =========================================================
# GET TELEGRAM PUBLIC PREVIEW
# =========================================================

def get_channel_posts(channel):

    username = channel.replace("@", "").strip()

    url = f"https://t.me/s/{username}"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Linux; Android 10) "
            "AppleWebKit/537.36 "
            "Chrome/149.0 Mobile Safari/537.36"
        )
    }

    try:
        response = requests.get(
            url,
            headers=headers,
            timeout=30
        )

        if response.status_code != 200:
            print(
                f"❌ {channel} HTTP:",
                response.status_code
            )
            return []

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        posts = []

        for message in soup.select(
            ".tgme_widget_message"
        ):

            data_post = message.get(
                "data-post",
                ""
            )

            if not data_post:
                continue

            # Post ID
            try:
                post_id = data_post.split("/")[-1]
            except Exception:
                continue

            # TEXT
            text_element = message.select_one(
                ".tgme_widget_message_text"
            )

            text = ""

            if text_element:
                text = text_element.get_text(
                    "\n",
                    strip=True
                )

            # IMAGE
            image_url = None

            photo = message.select_one(
                ".tgme_widget_message_photo_wrap"
            )

            if photo:

                style = photo.get(
                    "style",
                    ""
                )

                if "background-image" in style:

                    start = style.find(
                        "url('"
                    )

                    if start == -1:
                        start = style.find(
                            'url("'
                        )

                    if start != -1:

                        start += 5

                        end = style.find(
                            "'",
                            start
                        )

                        if end == -1:
                            end = style.find(
                                '"',
                                start
                            )

                        if end != -1:
                            image_url = style[
                                start:end
                            ]

            # Some Telegram previews expose IMG
            if not image_url:

                img = message.select_one(
                    ".tgme_widget_message_photo_wrap img"
                )

                if img:
                    image_url = img.get("src")

            posts.append({
                "channel": channel,
                "post_id": post_id,
                "text": text,
                "image": image_url
            })

        return posts

    except Exception as e:

        print(
            f"❌ Error reading {channel}:",
            e
        )

        return []


# =========================================================
# DOWNLOAD IMAGE
# =========================================================

def download_image(url):

    if not url:
        return None

    try:

        response = requests.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0"
            },
            timeout=30
        )

        if response.status_code != 200:
            return None

        return response.content

    except Exception as e:

        print(
            "❌ Image download error:",
            e
        )

        return None


# =========================================================
# SEND NEWS
# =========================================================

async def send_news(
    app,
    post
):

    channel = post["channel"]
    post_id = post["post_id"]
    original_text = post["text"]
    image_url = post["image"]

    text = clean_text(
        original_text
    )

    # No text and no image = ignore
    if not text and not image_url:
        return

    news_hash = make_hash(
        channel,
        post_id,
        original_text
    )

    if news_hash in posted_news:
        return

    # ==========================================
    # FINAL CAPTION
    # ==========================================

    final_text = (
        f"{text}\n\n"
        f"@yegnaLiverpool"
    )

    # Telegram photo caption max = 1024
    caption = final_text[:1024]

    try:

        if image_url:

            image = download_image(
                image_url
            )

            if image:

                await app.bot.send_photo(
                    chat_id=TARGET_CHANNEL,
                    photo=image,
                    caption=caption
                )

                # If text was longer than caption limit
                if len(final_text) > 1024:

                    await app.bot.send_message(
                        chat_id=TARGET_CHANNEL,
                        text=final_text[1024:]
                    )

            else:

                await app.bot.send_message(
                    chat_id=TARGET_CHANNEL,
                    text=final_text
                )

        else:

            await app.bot.send_message(
                chat_id=TARGET_CHANNEL,
                text=final_text
            )

        posted_news.add(
            news_hash
        )

        save_posted()

        print(
            f"✅ SENT: {channel} / {post_id}"
        )

    except Exception as e:

        print(
            "❌ Telegram send error:",
            e
        )


# =========================================================
# CHECK ALL SOURCES
# =========================================================

async def check_sources(app):

    if not SOURCE_CHANNELS:

        print(
            "❌ SOURCE_CHANNELS is empty"
        )

        return

    print(
        "🔎 Checking Telegram sources..."
    )

    for channel in SOURCE_CHANNELS:

        print(
            f"📡 Checking {channel}"
        )

        posts = get_channel_posts(
            channel
        )

        if not posts:

            print(
                f"⚠️ No posts found: {channel}"
            )

            continue

        # Check newest posts first
        for post in reversed(posts):

            await send_news(
                app,
                post
            )

    print(
        "✅ Source checking finished"
    )


# =========================================================
# ADMIN
# =========================================================

def is_admin(update):

    return (
        update.effective_user
        and update.effective_user.id == ADMIN_ID
    )


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "🔴 Liverpool News Bot በስራ ላይ ነው።"
    )


async def status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin(update):
        return

    await update.message.reply_text(
        "🟢 Bot: ON\n"
        f"📢 Target: {TARGET_CHANNEL}\n"
        f"📰 Sources: {len(SOURCE_CHANNELS)}"
    )


async def check_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin(update):
        return

    await update.message.reply_text(
        "🔎 Source channels እየተፈተሹ ነው..."
    )

    await check_sources(
        context.application
    )

    await update.message.reply_text(
        "✅ Checking finished."
    )


# =========================================================
# STARTUP
# =========================================================

async def post_init(app):

    print(
        "🔴 Liverpool Bot started!"
    )

    # Check sources immediately
    await check_sources(app)


# =========================================================
# MAIN
# =========================================================

def main():

    if not BOT_TOKEN:
        raise ValueError(
            "BOT_TOKEN missing"
        )

    if not ADMIN_ID:
        raise ValueError(
            "ADMIN_ID missing"
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
            "status",
            status
        )
    )

    application.add_handler(
        CommandHandler(
            "check",
            check_command
        )
    )

    print(
        "🚀 Liverpool News Bot running..."
    )

    application.run_polling()


if __name__ == "__main__":
    main()
