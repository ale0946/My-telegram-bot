import os
import re
import json
import time
import hashlib
import logging
import asyncio
import html
from urllib.parse import quote_plus

import requests
import feedparser

from telegram import Bot
from telegram.constants import ParseMode
from groq import Groq


# =========================================================
# SETTINGS
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

CHECK_INTERVAL = 300  # 5 minutes
MAX_NEWS_PER_CHECK = 5

SEEN_FILE = "seen_news.json"


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)


# =========================================================
# CHECK SETTINGS
# =========================================================

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is missing")


CHANNEL_IDS = [
    "@yegnaLiverpool",
    "@yegnaLiverpoolET"
]
if not GROQ_API_KEY:
    raise ValueError("GROQ_API_KEY is missing")


bot = Bot(token=BOT_TOKEN)
groq = Groq(api_key=GROQ_API_KEY)


# =========================================================
# NEWS SOURCES
# =========================================================

SOURCE_SEARCHES = [
    "Liverpool FC official",
    "Paul Joyce Liverpool",
    "David Ornstein Liverpool",
    "James Pearce Liverpool",
    "Lewis Steele Liverpool",
    "Melissa Reddy Liverpool",
    "Fabrizio Romano Liverpool",
]


# =========================================================
# SEEN NEWS
# =========================================================

def load_seen_news():
    try:
        if not os.path.exists(SEEN_FILE):
            return set()

        with open(SEEN_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)

        return set(data)

    except Exception as e:
        logger.error("Could not load seen news: %s", e)
        return set()


def save_seen_news(seen):
    try:
        # Keep only the latest 1000 IDs
        latest = list(seen)[-1000:]

        with open(SEEN_FILE, "w", encoding="utf-8") as file:
            json.dump(latest, file, ensure_ascii=False, indent=2)

    except Exception as e:
        logger.error("Could not save seen news: %s", e)


seen_news = load_seen_news()


# =========================================================
# HELPERS
# =========================================================

def clean_text(text):
    if not text:
        return ""

    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def make_news_id(title, link):
    raw = f"{title.strip().lower()}|{link.strip().lower()}"

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()


def is_liverpool_news(title, summary=""):
    text = f"{title} {summary}".lower()

    keywords = [
        "liverpool",
        "liverpool fc",
        "reds",
        "anfield",
        "arne slot",
        "andoni iraola",
        "virgil van dijk",
        "mohamed salah",
        "alexis mac allister",
        "ryan gravenberch",
        "dominik szoboszlai",
        "florian wirtz",
        "jeremy jacquet",
        "giovanni leoni",
    ]

    return any(keyword in text for keyword in keywords)


# =========================================================
# GOOGLE NEWS RSS
# =========================================================

def get_google_news_rss(query):
    encoded_query = quote_plus(query)

    url = (
        "https://news.google.com/rss/search?"
        f"q={encoded_query}"
        "&hl=en-US"
        "&gl=US"
        "&ceid=US:en"
    )

    try:
        response = requests.get(
            url,
            timeout=20,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 "
                    "(Linux; Android 10) "
                    "AppleWebKit/537.36 "
                    "Chrome/149.0 Mobile Safari/537.36"
                )
            },
        )

        response.raise_for_status()

        return feedparser.parse(response.content)

    except Exception as e:
        logger.error(
            "RSS error for %s: %s",
            query,
            e
        )

        return None


# =========================================================
# FETCH NEWS
# =========================================================

def fetch_news():
    all_news = []

    for source in SOURCE_SEARCHES:

        logger.info("Searching: %s", source)

        feed = get_google_news_rss(source)

        if not feed:
            continue

        for entry in feed.entries[:10]:

            title = clean_text(
                getattr(entry, "title", "")
            )

            summary = clean_text(
                getattr(entry, "summary", "")
            )

            link = getattr(entry, "link", "")

            if not title or not link:
                continue

            if not is_liverpool_news(title, summary):
                continue

            news_id = make_news_id(
                title,
                link
            )

            if news_id in seen_news:
                continue

            all_news.append({
                "id": news_id,
                "title": title,
                "summary": summary,
                "link": link,
                "source_search": source,
            })

    return all_news


# =========================================================
# REMOVE DUPLICATE STORIES
# =========================================================

def remove_similar_news(news_list):
    unique = []
    titles = []

    for item in news_list:

        title = item["title"].lower()

        words = set(
            re.findall(
                r"\b[a-zA-Z]{4,}\b",
                title
            )
        )

        duplicate = False

        for existing_title in titles:

            existing_words = set(
                re.findall(
                    r"\b[a-zA-Z]{4,}\b",
                    existing_title
                )
            )

            if not words or not existing_words:
                continue

            intersection = words.intersection(
                existing_words
            )

            similarity = (
                len(intersection)
                / max(
                    len(words),
                    len(existing_words)
                )
            )

            if similarity >= 0.65:
                duplicate = True
                break

        if not duplicate:
            unique.append(item)
            titles.append(title)

    return unique


# =========================================================
# AI TRANSLATION + NEWS WRITING
# =========================================================

def create_amharic_news(item):

    title = item["title"]
    summary = item["summary"]

    prompt = f"""
You are a professional Liverpool FC news editor.

Rewrite the following Liverpool football news in natural,
clear and professional Amharic.

IMPORTANT RULES:

1. Write ONLY in Amharic except player names, club names,
   competition names and unavoidable football terms.
2. Do NOT invent information.
3. Do NOT add speculation.
4. Do NOT change numbers, transfer fees or dates.
5. Keep the meaning of the original news.
6. Make it suitable for a Telegram football channel.
7. Keep it concise but informative.
8. If the story is about a transfer, clearly explain the
   current status.
9. Do not say "according to AI".
10. Do not repeat the same sentence.
11. Do not use Markdown.
12. Start with a strong headline.
13. Include a short body.
14. At the end write exactly:
   ምንጭ: [source]

SOURCE:
{item["source_search"]}

TITLE:
{title}

SUMMARY:
{summary}
"""

    try:

        response = groq.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an expert Amharic "
                        "Liverpool FC football news editor."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            temperature=0.2,
            max_tokens=700,
        )

        result = response.choices[0].message.content.strip()

        return result

    except Exception as e:

        logger.error(
            "Groq error: %s",
            e
        )

        return None


# =========================================================
# TELEGRAM MESSAGE
# =========================================================

def build_telegram_message(amharic_text, link):

    safe_text = html.escape(amharic_text)

    message = (
        "🔴 <b>LIVERPOOL NEWS</b>\n\n"
        f"{safe_text}\n\n"
        f"🔗 <a href=\"{html.escape(link)}\">"
        "ሙሉ ዜናውን ያንብቡ"
        "</a>\n\n"
        "🔴 <b>YN Liverpool</b>"
    )

    return message


# =========================================================
# SEND TO TELEGRAM
# =========================================================

async def send_news(item):

    logger.info(
        "Preparing news: %s",
        item["title"]
    )

    amharic = await asyncio.to_thread(
        create_amharic_news,
        item
    )

    if not amharic:
        return False

    message = build_telegram_message(
        amharic,
        item["link"]
    )

    try:

        await bot.send_message(
            chat_id=CHANNEL_ID,
            text=message,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=False,
        )

        logger.info(
            "News posted successfully"
        )

        return True

    except Exception as e:

        logger.error(
            "Telegram error: %s",
            e
        )

        return False


# =========================================================
# NEWS CHECK
# =========================================================

async def check_news():

    global seen_news

    logger.info(
        "Checking for new Liverpool news..."
    )

    news = await asyncio.to_thread(
        fetch_news
    )

    if not news:

        logger.info(
            "No new Liverpool news found."
        )

        return

    logger.info(
        "Found %d new stories.",
        len(news)
    )

    news = remove_similar_news(news)

    # Newest/first results only
    news = news[:MAX_NEWS_PER_CHECK]

    for item in news:

        success = await send_news(item)

        if success:

            seen_news.add(
                item["id"]
            )

            save_seen_news(
                seen_news
            )

            # Avoid Telegram/API rate problems
            await asyncio.sleep(10)


# =========================================================
# MAIN LOOP
# =========================================================

async def main():

    logger.info(
        "🔴 Liverpool News Bot started!"
    )

    logger.info(
        "Checking every %d seconds.",
        CHECK_INTERVAL
    )

    while True:

        try:

            await check_news()

        except Exception as e:

            logger.exception(
                "Unexpected error: %s",
                e
            )

        await asyncio.sleep(
            CHECK_INTERVAL
        )


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    try:

        asyncio.run(
            main()
        )

    except KeyboardInterrupt:

        logger.info(
            "Bot stopped."
        )
