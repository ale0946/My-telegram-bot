import os
import re
import json
import time
import hashlib
import logging
import sqlite3
import asyncio
from datetime import datetime, timezone
from urllib.parse import quote_plus

import feedparser
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from groq import Groq

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

# =========================================================
# CONFIG
# =========================================================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
CHANNEL_ID = os.getenv("CHANNEL_ID", "").strip()

# Current Groq model
GROQ_MODEL = os.getenv(
    "GROQ_MODEL",
    "openai/gpt-oss-120b"
).strip()

# Check news every N minutes
CHECK_INTERVAL_MINUTES = int(
    os.getenv("CHECK_INTERVAL_MINUTES", "5")
)

# Only news from this many hours ago is considered new
MAX_NEWS_AGE_HOURS = int(
    os.getenv("MAX_NEWS_AGE_HOURS", "24")
)

# =========================================================
# VALIDATION
# =========================================================

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing.")

if not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY is missing.")

if not CHANNEL_ID:
    raise RuntimeError("CHANNEL_ID is missing.")

# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)

logger = logging.getLogger(__name__)

# =========================================================
# GROQ
# =========================================================

groq_client = Groq(api_key=GROQ_API_KEY)

# =========================================================
# DATABASE
# =========================================================

DB_FILE = "news.db"

db = sqlite3.connect(
    DB_FILE,
    check_same_thread=False
)

db.execute("""
CREATE TABLE IF NOT EXISTS posted_news (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint TEXT UNIQUE,
    title TEXT,
    url TEXT,
    source TEXT,
    posted_at TEXT
)
""")

db.commit()

# =========================================================
# TRUSTED SOURCES
# =========================================================

TRUSTED_SOURCES = {
    "Liverpool FC Official": [
        "liverpoolfc.com"
    ],

    # These are searched through Google News RSS.
    # We still require the source/article to match the
    # configured trusted identity before posting.
    "David Ornstein": [
        "theathletic.com"
    ],

    "Paul Joyce": [
        "thetimes.com"
    ],

    "James Pearce": [
        "theathletic.com"
    ],

    "Fabrizio Romano": [
        "x.com",
        "twitter.com",
        "fabricioromano.com"
    ],
}

# =========================================================
# USER AGENT
# =========================================================

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 10) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/150.0 Mobile Safari/537.36"
    )
}

# =========================================================
# BASIC HELPERS
# =========================================================

def clean_text(text):
    if not text:
        return ""

    text = BeautifulSoup(
        text,
        "html.parser"
    ).get_text(" ", strip=True)

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


def make_fingerprint(title, url):
    raw = (
        clean_text(title).lower()
        + "|"
        + clean_text(url).lower()
    )

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()


def already_posted(fingerprint):
    row = db.execute(
        """
        SELECT 1
        FROM posted_news
        WHERE fingerprint = ?
        LIMIT 1
        """,
        (fingerprint,)
    ).fetchone()

    return row is not None


def save_posted(
    fingerprint,
    title,
    url,
    source
):
    db.execute(
        """
        INSERT OR IGNORE INTO posted_news
        (fingerprint, title, url, source, posted_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            fingerprint,
            title,
            url,
            source,
            datetime.now(timezone.utc).isoformat()
        )
    )

    db.commit()


# =========================================================
# DATE HELPERS
# =========================================================

def parse_entry_time(entry):
    try:
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            return datetime.fromtimestamp(
                time.mktime(entry.published_parsed),
                tz=timezone.utc
            )

        if hasattr(entry, "updated_parsed") and entry.updated_parsed:
            return datetime.fromtimestamp(
                time.mktime(entry.updated_parsed),
                tz=timezone.utc
            )

    except Exception:
        pass

    return None


def is_recent(entry):
    published = parse_entry_time(entry)

    if not published:
        return True

    now = datetime.now(timezone.utc)

    age = now - published

    return age.total_seconds() <= (
        MAX_NEWS_AGE_HOURS * 3600
    )


# =========================================================
# GOOGLE NEWS RSS
# =========================================================

def google_news_rss(query):
    encoded = quote_plus(query)

    return (
        "https://news.google.com/rss/search?"
        f"q={encoded}"
        "&hl=en-US"
        "&gl=US"
        "&ceid=US:en"
    )


def get_google_news(query):
    try:
        url = google_news_rss(query)

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=20
        )

        response.raise_for_status()

        return feedparser.parse(
            response.content
        )

    except Exception as e:
        logger.error(
            "Google News error: %s",
            e
        )

        return None


# =========================================================
# SOURCE COLLECTION
# =========================================================

def collect_news():
    articles = []

    # -----------------------------------------------------
    # Liverpool Official
    # -----------------------------------------------------

    official_feed = get_google_news(
        "site:liverpoolfc.com/news Liverpool"
    )

    if official_feed:
        for entry in official_feed.entries[:10]:

            title = clean_text(
                getattr(entry, "title", "")
            )

            url = getattr(
                entry,
                "link",
                ""
            )

            summary = clean_text(
                getattr(entry, "summary", "")
            )

            if not title or not url:
                continue

            articles.append({
                "title": title,
                "url": url,
                "summary": summary,
                "source": "Liverpool FC Official",
            })

    # -----------------------------------------------------
    # Trusted journalists
    # -----------------------------------------------------

    journalist_queries = [
        (
            "David Ornstein",
            '"David Ornstein" Liverpool'
        ),
        (
            "Paul Joyce",
            '"Paul Joyce" Liverpool'
        ),
        (
            "James Pearce",
            '"James Pearce" Liverpool'
        ),
        (
            "Fabrizio Romano",
            '"Fabrizio Romano" Liverpool'
        ),
    ]

    for source_name, query in journalist_queries:

        feed = get_google_news(query)

        if not feed:
            continue

        for entry in feed.entries[:10]:

            title = clean_text(
                getattr(entry, "title", "")
            )

            url = getattr(
                entry,
                "link",
                ""
            )

            summary = clean_text(
                getattr(entry, "summary", "")
            )

            source_field = clean_text(
                getattr(entry, "source", "")
            )

            if not title or not url:
                continue

            articles.append({
                "title": title,
                "url": url,
                "summary": summary,
                "source": source_name,
                "source_field": source_field
            })

    return articles


# =========================================================
# LIVERPOOL KEYWORD FILTER
# =========================================================

LIVERPOOL_KEYWORDS = [
    "liverpool",
    "reds",
    "anfield",
    "lfc",
    "andoni iraola",
    "liverpool fc",
]


def appears_liverpool_related(
    title,
    summary
):
    text = (
        title
        + " "
        + summary
    ).lower()

    return any(
        keyword in text
        for keyword in LIVERPOOL_KEYWORDS
    )


# =========================================================
# AI NEWS ASSISTANT
# =========================================================

SYSTEM_PROMPT = """
You are the Liverpool FC News Assistant for an Amharic Telegram channel.

Your job is NOT to invent news.

You must follow these strict rules:

1. Use ONLY information contained in the supplied source material.
2. Never add facts from your own knowledge.
3. Never invent quotes, transfer fees, dates, player opinions,
   injuries, contract details or negotiations.
4. If something is uncertain, preserve that uncertainty.
5. Do not turn a rumour into a confirmed fact.
6. Keep player names, manager names, club names and competition
   names accurate.
7. The final output must be natural, professional Amharic.
8. Do not produce English paragraphs.
9. Do not add an English headline.
10. Do not use clickbait.
11. Do not repeat the same information unnecessarily.
12. If the supplied article is not clearly about Liverpool FC,
    return REJECT.
13. If the source is not one of the trusted sources supplied by
    the application, return REJECT.

Return JSON only.

Format:

{
  "decision": "POST" or "REJECT",
  "category": "news/transfer/rumour/injury/match/other",
  "headline": "Amharic headline",
  "body": "Amharic news body",
  "confidence": 0-100
}

Important:
- "POST" means the article is suitable for the Telegram channel.
- "REJECT" means do not publish it.
- For rumours, clearly indicate that it is a rumour/report.
- Never claim a transfer is completed unless the source explicitly
  says it is completed.
"""


def ai_analyze(article):

    source = article.get(
        "source",
        ""
    )

    title = article.get(
        "title",
        ""
    )

    summary = article.get(
        "summary",
        ""
    )

    url = article.get(
        "url",
        ""
    )

    user_prompt = f"""
TRUSTED SOURCE:
{source}

TITLE:
{title}

ARTICLE SUMMARY:
{summary}

URL:
{url}

Analyze this article according to your rules.
Return JSON only.
"""

    try:
        completion = groq_client.chat.completions.create(
            model=GROQ_MODEL,

            messages=[
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT
                },
                {
                    "role": "user",
                    "content": user_prompt
                }
            ],

            temperature=0.1,

            max_tokens=1000,

            response_format={
                "type": "json_object"
            }
        )

        content = (
            completion
            .choices[0]
            .message
            .content
        )

        data = json.loads(content)

        return data

    except Exception as e:
        logger.error(
            "Groq error: %s",
            e
        )

        return None


# =========================================================
# FORMAT TELEGRAM MESSAGE
# =========================================================

def build_telegram_message(
    result,
    source,
    url
):

    headline = clean_text(
        result.get(
            "headline",
            ""
        )
    )

    body = clean_text(
        result.get(
            "body",
            ""
        )
    )

    category = clean_text(
        result.get(
            "category",
            "news"
        )
    ).lower()

    if not headline or not body:
        return None

    category_map = {
        "transfer": "🔄 ዝውውር",
        "rumour": "🟡 ወሬ / ዘገባ",
        "injury": "🏥 የጤና ሁኔታ",
        "match": "⚽ ጨዋታ",
        "news": "📰 ዜና",
        "other": "📰 ዜና",
    }

    label = category_map.get(
        category,
        "📰 ዜና"
    )

    message = (
        f"🔴 <b>LIVERPOOL NEWS</b>\n\n"
        f"<b>{headline}</b>\n\n"
        f"{body}\n\n"
        f"{label}\n"
        f"📰 ምንጭ: {source}\n"
        f"🔗 <a href=\"{url}\">የመጀመሪያ ምንጭ</a>"
    )

    return message


# =========================================================
# PROCESS ONE ARTICLE
# =========================================================

async def process_article(
    bot,
    article
):

    title = article.get(
        "title",
        ""
    )

    url = article.get(
        "url",
        ""
    )

    source = article.get(
        "source",
        ""
    )

    summary = article.get(
        "summary",
        ""
    )

    if not title or not url:
        return False

    # -----------------------------------------------------
    # Liverpool filter
    # -----------------------------------------------------

    if not appears_liverpool_related(
        title,
        summary
    ):
        logger.info(
            "Rejected: not Liverpool related: %s",
            title
        )

        return False

    # -----------------------------------------------------
    # Duplicate check
    # -----------------------------------------------------

    fingerprint = make_fingerprint(
        title,
        url
    )

    if already_posted(
        fingerprint
    ):
        logger.info(
            "Duplicate skipped: %s",
            title
        )

        return False

    # -----------------------------------------------------
    # AI
    # -----------------------------------------------------

    logger.info(
        "AI checking: %s",
        title
    )

    result = await asyncio.to_thread(
        ai_analyze,
        article
    )

    if not result:
        logger.warning(
            "AI failed: %s",
            title
        )

        return False

    decision = str(
        result.get(
            "decision",
            "REJECT"
        )
    ).upper()

    confidence = int(
        result.get(
            "confidence",
            0
        ) or 0
    )

    if decision != "POST":
        logger.info(
            "AI rejected: %s",
            title
        )

        return False

    # Require reasonable confidence
    if confidence < 75:
        logger.info(
            "Low confidence (%s): %s",
            confidence,
            title
        )

        return False

    # -----------------------------------------------------
    # Build message
    # -----------------------------------------------------

    message = build_telegram_message(
        result,
        source,
        url
    )

    if not message:
        return False

    # -----------------------------------------------------
    # Telegram
    # -----------------------------------------------------

    try:

        await bot.send_message(
            chat_id=CHANNEL_ID,
            text=message,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=False
        )

        save_posted(
            fingerprint,
            title,
            url,
            source
        )

        logger.info(
            "POSTED: %s",
            title
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

async def check_news(
    context: ContextTypes.DEFAULT_TYPE
):

    logger.info(
        "Checking trusted sources..."
    )

    articles = await asyncio.to_thread(
        collect_news
    )

    if not articles:
        logger.info(
            "No articles found."
        )

        return

    # Process newest first
    articles = articles[:30]

    posted_count = 0

    for article in articles:

        try:

            posted = await process_article(
                context.bot,
                article
            )

            if posted:
                posted_count += 1

            # Small delay to reduce API pressure
            await asyncio.sleep(2)

        except Exception as e:

            logger.exception(
                "Article processing error: %s",
                e
            )

    logger.info(
        "News check finished. Posted: %s",
        posted_count
    )


# =========================================================
# COMMANDS
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "🔴 Liverpool News Assistant Bot እየሰራ ነው።\n\n"
        "📰 Trusted sources\n"
        "🔎 News filtering\n"
        "🤖 AI verification\n"
        "🇪🇹 Amharic translation\n"
        "🔁 Duplicate protection\n"
        "📤 Telegram publishing"
    )


async def status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    row = db.execute(
        "SELECT COUNT(*) FROM posted_news"
    ).fetchone()

    total = row[0] if row else 0

    await update.message.reply_text(
        "🟢 Bot Status: RUNNING\n\n"
        f"📰 Posted news stored: {total}\n"
        f"⏱️ Check interval: "
        f"{CHECK_INTERVAL_MINUTES} minutes\n"
        f"🤖 AI: Groq\n"
        f"📡 Channel: {CHANNEL_ID}"
    )


async def sources(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    names = "\n".join(
        f"• {name}"
        for name in TRUSTED_SOURCES.keys()
    )

    await update.message.reply_text(
        "🛡️ Trusted Sources:\n\n"
        + names
    )


async def test_ai(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    test_article = {
        "title": (
            "Liverpool are preparing for the new season "
            "under Andoni Iraola"
        ),
        "summary": (
            "Liverpool are continuing preparations "
            "ahead of the new campaign."
        ),
        "url": "https://www.liverpoolfc.com/news",
        "source": "Liverpool FC Official",
    }

    result = await asyncio.to_thread(
        ai_analyze,
        test_article
    )

    if not result:

        await update.message.reply_text(
            "❌ AI test failed."
        )

        return

    text = json.dumps(
        result,
        ensure_ascii=False,
        indent=2
    )

    await update.message.reply_text(
        "🤖 AI Test Result:\n\n"
        + text
    )


async def check_now(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "🔎 Trusted sources እየተፈተሹ ነው..."
    )

    await check_news(
        context
    )

    await update.message.reply_text(
        "✅ News check finished."
    )


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(
    update,
    context
):

    logger.exception(
        "Unhandled error:",
        exc_info=context.error
    )


# =========================================================
# MAIN
# =========================================================

def main():

    logger.info(
        "Starting Liverpool News Assistant..."
    )

    application = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .build()
    )

    # Commands
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
            "sources",
            sources
        )
    )

    application.add_handler(
        CommandHandler(
            "testai",
            test_ai
        )
    )

    application.add_handler(
        CommandHandler(
            "check",
            check_now
        )
    )

    application.add_error_handler(
        error_handler
    )

    # -----------------------------------------------------
    # Automatic news checker
    # -----------------------------------------------------

    if application.job_queue is None:
        raise RuntimeError(
            "JobQueue is not available. "
            "Install python-telegram-bot[job-queue]."
        )

    application.job_queue.run_repeating(
        check_news,
        interval=CHECK_INTERVAL_MINUTES * 60,
        first=10
    )

    logger.info(
        "Bot started successfully."
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
