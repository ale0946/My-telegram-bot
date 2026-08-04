import os
import re
import time
import json
import hashlib
import logging
import sqlite3
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin, urlparse

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

CHANNEL = "@yegnaLiverpool"

GROQ_MODEL = os.getenv(
    "GROQ_MODEL",
    "openai/gpt-oss-120b"
).strip()

CHECK_EVERY = 5 * 60
MIN_POST_GAP = 5 * 60
MAX_POSTS_PER_CYCLE = 1

DB_FILE = "liverpool_news.db"

SEND_STARTUP_TEST = os.getenv(
    "SEND_STARTUP_TEST",
    "true"
).lower() == "true"

USER_AGENT = (
    "Mozilla/5.0 "
    "(Linux; Android 10) "
    "AppleWebKit/537.36 "
    "(KHTML, like Gecko) "
    "Chrome/137.0 Mobile Safari/537.36"
)

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9"
}

# =========================================================
# SOCIAL SOURCES
# =========================================================

SOCIAL_SOURCES = [
    {
        "name": "Fabrizio Romano",
        "rss": "https://rsshub.app/twitter/user/FabrizioRomano"
    },
    {
        "name": "Anfield Edition",
        "rss": "https://rsshub.app/twitter/user/AnfieldEdition"
    },
    {
        "name": "Anfield Watch",
        "rss": "https://rsshub.app/twitter/user/AnfieldWatch"
    },
    {
        "name": "DaveOCKOP",
        "rss": "https://rsshub.app/twitter/user/DaveOCKOP"
    },
    {
        "name": "The Anfield Talk",
        "rss": "https://rsshub.app/twitter/user/TheAnfieldTalk"
    },
    {
        "name": "Empire of the Kop",
        "rss": "https://rsshub.app/twitter/user/empireofthekop"
    }
]

# =========================================================
# VALIDATION
# =========================================================

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN missing")

if not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY missing")

client = Groq(
    api_key=GROQ_API_KEY
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("LiverpoolBot")
# =========================================================
# DATABASE
# =========================================================

def get_db():

    conn = sqlite3.connect(
        DB_FILE,
        timeout=30
    )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS posted_news(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fingerprint TEXT UNIQUE,
            title TEXT,
            url TEXT,
            source TEXT,
            image_hash TEXT,
            posted_at INTEGER
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS used_images(
            image_hash TEXT PRIMARY KEY,
            image_url TEXT,
            used_at INTEGER
        )
    """)

    conn.commit()

    return conn


# =========================================================
# TEXT
# =========================================================

def clean_text(text):

    if not text:
        return ""

    text = BeautifulSoup(
        str(text),
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


def normalize(text):

    text = clean_text(text).lower()

    text = re.sub(
        r"[^a-z0-9\u1200-\u137f\s]",
        " ",
        text
    )

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


def make_hash(text):

    return hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()


# =========================================================
# LIVERPOOL FILTER
# =========================================================

LIVERPOOL_KEYWORDS = [

    "liverpool",
    "lfc",
    "reds",
    "anfield",

    "slot",
    "arne slot",

    "salah",
    "mohamed salah",

    "van dijk",
    "virgil",

    "gakpo",
    "diaz",
    "nunez",

    "mac allister",
    "gravenberch",
    "szoboszlai",

    "frimpong",
    "wirtz",

    "konate",
    "alisson",

    "robertson",
    "elliott",

    "bradley",
    "jones",

    "chiesa",
    "endo"
]


def is_liverpool_related(text):

    text = clean_text(
        text
    ).lower()

    for word in LIVERPOOL_KEYWORDS:

        if word in text:
            return True

    return False


# =========================================================
# SOCIAL RSS
# =========================================================

import feedparser


def get_social_news():

    news = []

    for source in SOCIAL_SOURCES:

        try:

            logger.info(
                "Checking %s",
                source["name"]
            )

            feed = feedparser.parse(
                source["rss"]
            )

            for item in feed.entries:

                title = clean_text(
                    getattr(
                        item,
                        "title",
                        ""
                    )
                )

                link = getattr(
                    item,
                    "link",
                    ""
                )

                summary = clean_text(
                    getattr(
                        item,
                        "summary",
                        ""
                    )
                )

                if not title:
                    continue

                if not is_liverpool_related(
                    title + " " + summary
                ):
                    continue

                news.append({

                    "title": title,

                    "summary": summary,

                    "url": link,

                    "source_title": source["name"],

                    "published": getattr(
                        item,
                        "published_parsed",
                        None
                    )

                })

        except Exception as e:

            logger.warning(
                "%s failed: %s",
                source["name"],
                e
            )

    logger.info(
        "SOCIAL POSTS FOUND: %s",
        len(news)
    )

    return news
    SOCIAL_SOURCES = [
    {
        "name": "Fabrizio Romano",
        "rss": "https://rsshub.app/twitter/user/FabrizioRomano"
    },
    {
        "name": "Anfield Edition",
        "rss": "https://rsshub.app/twitter/user/AnfieldEdition"
    },
    {
        "name": "Anfield Watch",
        "rss": "https://rsshub.app/twitter/user/AnfieldWatch"
    },
    {
        "name": "DaveOCKOP",
        "rss": "https://rsshub.app/twitter/user/DaveOCKOP"
    },
    {
        "name": "The Anfield Talk",
        "rss": "https://rsshub.app/twitter/user/TheAnfieldTalk"
    },
    {
        "name": "Empire of the Kop",
        "rss": "https://rsshub.app/twitter/user/empireofthekop"
    }
]


def get_social_news():

    news = []

    for source in SOCIAL_SOURCES:

        try:

            logger.info("Checking %s", source["name"])

            feed = feedparser.parse(source["rss"])

            for entry in feed.entries:

                title = clean_text(
                    getattr(entry, "title", "")
                )

                summary = clean_text(
                    getattr(entry, "summary", "")
                )

                link = getattr(
                    entry,
                    "link",
                    ""
                )

                if not title:
                    continue

                if not is_liverpool_related(
                    title + " " + summary
                ):
                    continue

                news.append({
                    "title": title,
                    "summary": summary,
                    "url": link,
                    "source_title": source["name"],
                    "published": getattr(
                        entry,
                        "published_parsed",
                        None
                    )
                })

        except Exception as e:

            logger.warning(
                "%s failed: %s",
                source["name"],
                e
            )

    unique = {}

    for item in news:

        key = make_hash(
            normalize(item["title"])
        )

        unique[key] = item

    logger.info(
        "SOCIAL NEWS FOUND: %s",
        len(unique)
    )

    return list(unique.values())
    # =========================================================
# TELEGRAM API
# =========================================================

def telegram_api(method, data=None, files=None):

    url = (
        f"https://api.telegram.org/bot"
        f"{BOT_TOKEN}/{method}"
    )

    try:

        r = requests.post(
            url,
            data=data,
            files=files,
            timeout=40
        )

        result = r.json()

        if not result.get("ok"):

            logger.error(result)

        return result

    except Exception as e:

        logger.error(e)

        return {
            "ok": False
        }


def telegram_send_message(text):

    return telegram_api(

        "sendMessage",

        data={

            "chat_id": CHANNEL,

            "text": text,

            "disable_web_page_preview": True

        }

    ).get("ok", False)


def telegram_send_photo(
    image,
    caption
):

    return telegram_api(

        "sendPhoto",

        data={

            "chat_id": CHANNEL,

            "caption": caption

        },

        files={

            "photo": (

                "image.jpg",

                image,

                "image/jpeg"

            )

        }

    ).get("ok", False)


# =========================================================
# IMAGE DOWNLOAD
# =========================================================

def download_image(url):

    if not url:

        return None

    try:

        r = requests.get(

            url,

            headers=HEADERS,

            timeout=25

        )

        if r.status_code != 200:

            return None

        if len(r.content) < 15000:

            return None

        return {

            "bytes": r.content,

            "hash": hashlib.sha256(
                r.content
            ).hexdigest(),

            "url": url

        }

    except:

        return None


# =========================================================
# IMAGE DATABASE
# =========================================================

def image_used(image_hash):

    conn = get_db()

    row = conn.execute(

        "SELECT image_hash FROM used_images WHERE image_hash=?",

        (image_hash,)

    ).fetchone()

    conn.close()

    return row is not None


def save_image(image_hash, url):

    conn = get_db()

    conn.execute(

        """

        INSERT OR IGNORE INTO used_images

        VALUES(?,?,?)

        """,

        (

            image_hash,

            url,

            int(time.time())

        )

    )

    conn.commit()

    conn.close()
    # =========================================================
# NEWS DATABASE
# =========================================================

def news_was_posted(title, url):

    fingerprint = make_hash(
        normalize(title) + normalize(url)
    )

    conn = get_db()

    row = conn.execute(
        """
        SELECT id
        FROM posted_news
        WHERE fingerprint=?
        LIMIT 1
        """,
        (fingerprint,)
    ).fetchone()

    conn.close()

    return row is not None


def save_post(
    title,
    url,
    source,
    image_hash=""
):

    fingerprint = make_hash(
        normalize(title) + normalize(url)
    )

    conn = get_db()

    conn.execute(
        """
        INSERT OR IGNORE INTO posted_news
        (
            fingerprint,
            title,
            url,
            source,
            image_hash,
            posted_at
        )
        VALUES
        (
            ?,?,?,?,?,?
        )
        """,
        (
            fingerprint,
            title,
            url,
            source,
            image_hash,
            int(time.time())
        )
    )

    conn.commit()

    conn.close()


# =========================================================
# POST GAP
# =========================================================

def can_post():

    conn = get_db()

    row = conn.execute(
        """
        SELECT posted_at
        FROM posted_news
        ORDER BY posted_at DESC
        LIMIT 1
        """
    ).fetchone()

    conn.close()

    if not row:
        return True

    return (
        time.time()
        - int(row[0])
    ) >= MIN_POST_GAP


# =========================================================
# ARTICLE FETCH
# =========================================================

def fetch_article(url):

    try:

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=30
        )

        if response.status_code != 200:
            return None

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        paragraphs = []

        for p in soup.find_all("p"):

            text = clean_text(
                p.get_text()
            )

            if len(text) >= 40:

                paragraphs.append(text)

        body = "\n".join(paragraphs)

        image = None

        og = soup.find(
            "meta",
            property="og:image"
        )

        if og:

            image = og.get(
                "content"
            )

        return {

            "title": clean_text(
                soup.title.text
                if soup.title else ""
            ),

            "body": body[:14000],

            "image_url": image,

            "url": response.url

        }

    except Exception as e:

        logger.warning(e)

        return None
 # =========================================================
# GROQ AI
# =========================================================

NEWS_EDITOR_PROMPT = """
አንተ የLiverpool FC የአማርኛ ስፖርት ዜና አርታዒ ነህ።

የተሰጠህን መረጃ ብቻ ተጠቅመህ አጭር፣
ትክክለኛ እና ተፈጥሯዊ የአማርኛ ዜና አዘጋጅ።

JSON ብቻ መልስ።

{
 "decision":"POST",
 "headline":"",
 "body":"",
 "confidence":95
}
"""


def ai_edit_news(
    title,
    body,
    source,
    url
):

    prompt = f"""

SOURCE:
{source}

TITLE:
{title}

ARTICLE:
{body}

URL:
{url}

"""

    try:

        completion = client.chat.completions.create(

            model=GROQ_MODEL,

            temperature=0.2,

            response_format={
                "type": "json_object"
            },

            messages=[

                {
                    "role": "system",
                    "content": NEWS_EDITOR_PROMPT
                },

                {
                    "role": "user",
                    "content": prompt
                }

            ]

        )

        return json.loads(

            completion
            .choices[0]
            .message
            .content

        )

    except Exception as e:

        logger.error(e)

        return None


# =========================================================
# CAPTION
# =========================================================

def make_caption(
    headline,
    body
):

    caption = (

        headline

        + "\n\n"

        + body

        + "\n\n@yegnaLiverpool"

    )

    if len(caption) > 1024:

        caption = caption[:1020] + "..."

    return caption
# =========================================================
# PROCESS NEWS
# =========================================================

def process_news(entry):

    title = clean_text(
        entry["title"]
    )

    url = entry["url"]

    source = entry["source_title"]

    summary = clean_text(
        entry.get(
            "summary",
            ""
        )
    )

    if not title:

        return False

    if news_was_posted(
        title,
        url
    ):

        logger.info(
            "Duplicate"
        )

        return False

    if not can_post():

        logger.info(
            "Waiting post gap"
        )

        return False

    article = fetch_article(
        url
    )

    if article:

        body = article["body"]

        image_url = article["image_url"]

        final_title = (
            article["title"]
            or title
        )

    else:

        body = summary

        image_url = None

        final_title = title

    if len(body) < 150:

        return False

    edited = ai_edit_news(

        final_title,

        body,

        source,

        url

    )

    if not edited:

        return False

    if edited.get(
        "decision"
    ) != "POST":

        return False

    caption = make_caption(

        edited["headline"],

        edited["body"]

    )

    image_hash = ""

    success = False

    if image_url:

        image = download_image(
            image_url
        )

        if image:

            if not image_used(
                image["hash"]
            ):

                success = telegram_send_photo(

                    image["bytes"],

                    caption

                )

                if success:

                    save_image(

                        image["hash"],

                        image["url"]

                    )

                    image_hash = image["hash"]

    if not success:

        success = telegram_send_message(
            caption
        )

    if success:

        save_post(

            edited["headline"],

            url,

            source,

            image_hash

        )

        logger.info(
            "Posted successfully."
        )

        return True

    return False
    # =========================================================
# MAIN LOOP
# =========================================================

def run_bot():

    logger.info(
        "===================================="
    )

    logger.info(
        "Liverpool
    # =========================================================
# MAIN LOOP
# =========================================================

def run_bot():

    logger.info(
        "=" * 60
    )

    logger.info(
        "Liverpool Social News Bot Started"
    )

    logger.info(
        "Channel : %s",
        CHANNEL
    )

    logger.info(
        "=" * 60
    )

    conn = get_db()
    conn.close()

    if SEND_STARTUP_TEST:

        telegram_send_message(
            "🤖 Liverpool News Bot ተጀምሯል ✅"
        )

    while True:

        try:

            logger.info(
                "Checking social sources..."
            )

            news = get_social_news()

            news.sort(
                key=lambda x: (
                    x.get("published")
                    or time.gmtime(0)
                ),
                reverse=True
            )

            posted = 0

            for item in news:

                try:

                    if process_news(item):

                        posted += 1

                        if posted >= MAX_POSTS_PER_CYCLE:

                            break

                except Exception as e:

                    logger.exception(e)

            if posted == 0:

                logger.info(
                    "No new Liverpool news."
                )

        except Exception as e:

            logger.exception(
                "Main Loop Error: %s",
                e
            )

        logger.info(
            "Sleeping %s seconds...",
            CHECK_EVERY
        )

        time.sleep(
            CHECK_EVERY
        )


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    try:

        run_bot()

    except KeyboardInterrupt:

        logger.info(
            "Bot stopped."
        )

    except Exception as e:

        logger.exception(
            "Fatal Error: %s",
            e
        )

        raise
    # ===== END OF FILE =====
