import os
import re
import json
import time
import hashlib
import logging
import sqlite3
from datetime import datetime, timezone
from urllib.parse import quote_plus

import feedparser
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from groq import Groq


# =========================================================
# CONFIG
# =========================================================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
CHANNEL_ID = os.getenv("CHANNEL_ID", "@yegnaLiverpool").strip()

GROQ_MODEL = os.getenv(
    "GROQ_MODEL",
    "openai/gpt-oss-120b"
).strip()

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

groq_client = Groq(
    api_key=GROQ_API_KEY
)


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
# HEADERS
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
# TEXT HELPERS
# =========================================================

def clean_text(text):
    if not text:
        return ""

    text = BeautifulSoup(
        text,
        "html.parser"
    ).get_text(
        " ",
        strip=True
    )

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
# DATE
# =========================================================

def parse_entry_time(entry):
    try:
        if (
            hasattr(entry, "published_parsed")
            and entry.published_parsed
        ):
            return datetime.fromtimestamp(
                time.mktime(
                    entry.published_parsed
                ),
                tz=timezone.utc
            )

        if (
            hasattr(entry, "updated_parsed")
            and entry.updated_parsed
        ):
            return datetime.fromtimestamp(
                time.mktime(
                    entry.updated_parsed
                ),
                tz=timezone.utc
            )

    except Exception as e:
        logger.warning(
            "Date parsing error: %s",
            e
        )

    return None


def is_recent(entry):
    published = parse_entry_time(entry)

    if not published:
        return True

    now = datetime.now(timezone.utc)

    age = (
        now - published
    ).total_seconds()

    return age <= (
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
# NEWS COLLECTION
# =========================================================

def collect_news():

    articles = []

    # -----------------------------------------------------
    # Liverpool FC Official
    # -----------------------------------------------------

    official_feed = get_google_news(
        "site:liverpoolfc.com/news Liverpool"
    )

    if official_feed:

        for entry in official_feed.entries[:10]:

            if not is_recent(entry):
                continue

            title = clean_text(
                getattr(
                    entry,
                    "title",
                    ""
                )
            )

            url = getattr(
                entry,
                "link",
                ""
            )

            summary = clean_text(
                getattr(
                    entry,
                    "summary",
                    ""
                )
            )

            if not title or not url:
                continue

            articles.append({
                "title": title,
                "url": url,
                "summary": summary,
                "source": "Liverpool FC Official"
            })

    # -----------------------------------------------------
    # Trusted Journalists
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

            if not is_recent(entry):
                continue

            title = clean_text(
                getattr(
                    entry,
                    "title",
                    ""
                )
            )

            url = getattr(
                entry,
                "link",
                ""
            )

            summary = clean_text(
                getattr(
                    entry,
                    "summary",
                    ""
                )
            )

            if not title or not url:
                continue

            articles.append({
                "title": title,
                "url": url,
                "summary": summary,
                "source": source_name
            })

    logger.info(
        "Collected %s articles.",
        len(articles)
    )

    return articles


# =========================================================
# LIVERPOOL FILTER
# =========================================================

LIVERPOOL_KEYWORDS = [
    "liverpool",
    "reds",
    "anfield",
    "lfc",
    "liverpool fc",
    "andoni iraola"
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
# AI PROMPT
# =========================================================

SYSTEM_PROMPT = """
You are the Liverpool FC News Assistant for an Amharic Telegram channel.

STRICT RULES:

1. Use ONLY information supplied in the article.
2. Never invent facts.
3. Never invent quotes.
4. Never invent transfer fees.
5. Never invent dates.
6. Never invent injuries.
7. Never invent contract information.
8. Never turn a rumour into a confirmed fact.
9. Preserve uncertainty when the source is uncertain.
10. The article must clearly concern Liverpool FC.
11. The output must be natural professional Amharic.
12. Do not output an English paragraph.
13. Do not output an English headline.
14. Do not use clickbait.
15. Do not repeat information unnecessarily.
16. If unsuitable, return REJECT.
17. Rumours must clearly be labelled as reports/rumours.

Return JSON only.

Format:

{
  "decision": "POST" or "REJECT",
  "category": "news/transfer/rumour/injury/match/other",
  "headline": "Amharic headline",
  "body": "Amharic body",
  "confidence": 0-100
}
"""


# =========================================================
# AI ANALYSIS
# =========================================================

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

    user_prompt = f"""
TRUSTED SOURCE:
{source}

TITLE:
{title}

ARTICLE SUMMARY:
{summary}

Analyze the article.

Return JSON only.
"""

    try:

        completion = (
            groq_client
            .chat
            .completions
            .create(
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
        )

        content = (
            completion
            .choices[0]
            .message
            .content
        )

        return json.loads(content)

    except Exception as e:

        logger.error(
            "Groq error: %s",
            e
        )

        return None


# =========================================================
# TELEGRAM SEND
# =========================================================

def telegram_send_message(
    text
):

    url = (
        f"https://api.telegram.org/bot"
        f"{BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": CHANNEL_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }

    try:

        response = requests.post(
            url,
            json=payload,
            timeout=30
        )

        if response.status_code != 200:

            logger.error(
                "Telegram API error: %s",
                response.text
            )

            return False

        data = response.json()

        if not data.get("ok"):

            logger.error(
                "Telegram rejected message: %s",
                data
            )

            return False

        return True

    except Exception as e:

        logger.error(
            "Telegram connection error: %s",
            e
        )

        return False


# =========================================================
# FORMAT MESSAGE
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

        "transfer":
            "🔄 ዝውውር",

        "rumour":
            "🟡 ወሬ / ዘገባ",

        "injury":
            "🏥 የጤና ሁኔታ",

        "match":
            "⚽ ጨዋታ",

        "news":
            "📰 ዜና",

        "other":
            "📰 ዜና"
    }

    label = category_map.get(
        category,
        "📰 ዜና"
    )

    # Escape only dangerous HTML chars
    headline = (
        headline
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )

    body = (
        body
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )

    source = (
        source
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )

    message = (
        "🔴 <b>LIVERPOOL NEWS</b>\n\n"
        f"<b>{headline}</b>\n\n"
        f"{body}\n\n"
        f"{label}\n"
        f"📰 ምንጭ: {source}\n"
        f'🔗 <a href="{url}">የመጀመሪያ ምንጭ</a>'
    )

    return message


# =========================================================
# PROCESS ARTICLE
# =========================================================

def process_article(article):

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
    # Liverpool check
    # -----------------------------------------------------

    if not appears_liverpool_related(
        title,
        summary
    ):

        logger.info(
            "Rejected: not Liverpool: %s",
            title
        )

        return False

    # -----------------------------------------------------
    # Duplicate
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

    result = ai_analyze(
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

    try:

        confidence = int(
            result.get(
                "confidence",
                0
            ) or 0
        )

    except Exception:

        confidence = 0

    logger.info(
        "AI decision=%s confidence=%s",
        decision,
        confidence
    )

    if decision != "POST":
        return False

    if confidence < 75:
        logger.info(
            "Low confidence: %s",
            confidence
        )
        return False

    # -----------------------------------------------------
    # Build
    # -----------------------------------------------------

    message = build_telegram_message(
        result,
        source,
        url
    )

    if not message:
        return False

    # -----------------------------------------------------
    # SEND DIRECTLY TO TELEGRAM
    # -----------------------------------------------------

    sent = telegram_send_message(
        message
    )

    if not sent:
        logger.error(
            "NOT POSTED: %s",
            title
        )
        return False

    # Save only AFTER successful Telegram post
    save_posted(
        fingerprint,
        title,
        url,
        source
    )

    logger.info(
        "✅ POSTED TO TELEGRAM: %s",
        title
    )

    return True


# =========================================================
# CHECK NEWS
# =========================================================

def check_news():

    logger.info(
        "===================================="
    )

    logger.info(
        "🔎 Checking Liverpool trusted sources..."
    )

    articles = collect_news()

    if not articles:

        logger.info(
            "No recent articles found."
        )

        return

    posted_count = 0

    for article in articles[:30]:

        try:

            posted = process_article(
                article
            )

            if posted:
                posted_count += 1

            time.sleep(2)

        except Exception as e:

            logger.exception(
                "Article processing error: %s",
                e
            )

    logger.info(
        "===================================="
    )

    logger.info(
        "Finished. Posted: %s",
        posted_count
    )


# =========================================================
# MAIN
# =========================================================

def main():

    logger.info(
        "🔴 Liverpool News Bot starting..."
    )

    logger.info(
        "Channel: %s",
        CHANNEL_ID
    )

    logger.info(
        "Mode: DIRECT TELEGRAM API"
    )

    logger.info(
        "Polling: DISABLED"
    )

    check_news()

    logger.info(
        "🏁 Bot finished."
    )


if __name__ == "__main__":
    main()
