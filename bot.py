```python
import os
import re
import time
import json
import hashlib
import logging
import sqlite3

from datetime import datetime, timezone
from urllib.parse import urljoin

import requests
import feedparser

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


# =========================================================
# HTTP
# =========================================================

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


# =========================================================
# LOGGING
# =========================================================

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
    "virgil van dijk",
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

    text = clean_text(text).lower()

    return any(
        word in text
        for word in LIVERPOOL_KEYWORDS
    )


# =========================================================
# SOCIAL RSS
# =========================================================

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

            for entry in feed.entries:

                title = clean_text(
                    getattr(
                        entry,
                        "title",
                        ""
                    )
                )

                summary = clean_text(
                    getattr(
                        entry,
                        "summary",
                        ""
                    )
                )

                link = getattr(
                    entry,
                    "link",
                    ""
                )

                if not title:
                    continue

                combined_text = (
                    title + " " + summary
                )

                if not is_liverpool_related(
                    combined_text
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

    # Remove duplicate titles
    unique = {}

    for item in news:

        key = make_hash(
            normalize(item["title"])
        )

        if key not in unique:
            unique[key] = item

    logger.info(
        "SOCIAL NEWS FOUND: %s",
        len(unique)
    )

    return list(unique.values())


# =========================================================
# TELEGRAM API
# =========================================================

def telegram_api(
    method,
    data=None,
    files=None
):

    url = (
        f"https://api.telegram.org/bot"
        f"{BOT_TOKEN}/{method}"
    )

    try:

        response = requests.post(
            url,
            data=data,
            files=files,
            timeout=40
        )

        result = response.json()

        if not result.get("ok"):

            logger.error(
                "Telegram API error: %s",
                result
            )

        return result

    except Exception as e:

        logger.error(
            "Telegram request failed: %s",
            e
        )

        return {
            "ok": False
        }


def telegram_send_message(text):

    result = telegram_api(

        "sendMessage",

        data={
            "chat_id": CHANNEL,
            "text": text,
            "disable_web_page_preview": True
        }

    )

    return result.get(
        "ok",
        False
    )


def telegram_send_photo(
    image,
    caption
):

    result = telegram_api(

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

    )

    return result.get(
        "ok",
        False
    )


# =========================================================
# IMAGE DOWNLOAD
# =========================================================

def download_image(url):

    if not url:
        return None

    try:

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=25
        )

        if response.status_code != 200:
            return None

        content_type = (
            response.headers
            .get("content-type", "")
            .lower()
        )

        if (
            "image" not in content_type
            and not url.lower().endswith(
                (".jpg", ".jpeg", ".png", ".webp")
            )
        ):
            return None

        if len(response.content) < 15000:
            return None

        return {
            "bytes": response.content,
            "hash": hashlib.sha256(
                response.content
            ).hexdigest(),
            "url": url
        }

    except Exception as e:

        logger.warning(
            "Image download failed: %s",
            e
        )

        return None


# =========================================================
# IMAGE DATABASE
# =========================================================

def image_used(image_hash):

    if not image_hash:
        return False

    conn = get_db()

    row = conn.execute(
        """
        SELECT image_hash
        FROM used_images
        WHERE image_hash=?
        LIMIT 1
        """,
        (image_hash,)
    ).fetchone()

    conn.close()

    return row is not None


def save_image(
    image_hash,
    url
):

    if not image_hash:
        return

    conn = get_db()

    conn.execute(
        """
        INSERT OR IGNORE INTO used_images
        (
            image_hash,
            image_url,
            used_at
        )
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

def news_was_posted(
    title,
    url
):

    fingerprint = make_hash(
        normalize(title)
        + normalize(url)
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
        normalize(title)
        + normalize(url)
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
        VALUES(?,?,?,?,?,?)
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

    elapsed = (
        time.time()
        - int(row[0])
    )

    return elapsed >= MIN_POST_GAP


# =========================================================
# ARTICLE FETCH
# =========================================================

def fetch_article(url):

    if not url:
        return None

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

        for paragraph in soup.find_all("p"):

            text = clean_text(
                paragraph.get_text()
            )

            if len(text) >= 40:
                paragraphs.append(text)

        body = "\n".join(
            paragraphs
        )

        image_url = None

        og_image = soup.find(
            "meta",
            property="og:image"
        )

        if og_image:

            image_url = og_image.get(
                "content"
            )

        title = ""

        if soup.title:
            title = clean_text(
                soup.title.get_text()
            )

        return {
            "title": title,
            "body": body[:14000],
            "image_url": image_url,
            "url": response.url
        }

    except Exception as e:

        logger.warning(
            "Article fetch failed: %s",
            e
        )

        return None


# =========================================================
# GROQ AI
# =========================================================

NEWS_EDITOR_PROMPT = """
አንተ የLiverpool FC የአማርኛ ስፖርት ዜና አርታዒ ነህ።

የተሰጠህን መረጃ ብቻ ተጠቅመህ
አጭር፣ ትክክለኛ፣ ተፈጥሯዊ እና
ሙያዊ የአማርኛ ዜና አዘጋጅ።

ጥብቅ ህጎች፦

1. ከተሰጠው መረጃ ውጭ ምንም ነገር አትፍጠር።
2. ስም፣ ቀን፣ ዋጋ፣ የዝውውር መጠን፣ ጉዳት፣
   ውል ወይም ጥቅስ አትጨምር።
3. ዜናው Liverpool FCን በግልጽ የሚመለከት
   ካልሆነ decision = "REJECT" አድርግ።
4. እርግጠኛ ያልሆነ መረጃ ካለ እንደተረጋገጠ
   አታቀርበው።
5. የዜናውን ዋና ነጥብ ብቻ አስቀምጥ።
6. በአማርኛ ብቻ ጻፍ።
7. የእንግሊዝኛ headline አትጨምር።
8. Markdown, hashtags ወይም የማያስፈልጉ
   separators አትጠቀም።

JSON ብቻ መልስ።

{
    "decision": "POST",
    "headline": "",
    "body": "",
    "confidence": 95
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

        content = (
            completion
            .choices[0]
            .message
            .content
        )

        return json.loads(
            content
        )

    except Exception as e:

        logger.error(
            "Groq error: %s",
            e
        )

        return None


# =========================================================
# CAPTION
# =========================================================

def make_caption(
    headline,
    body
):

    headline = clean_text(
        headline
    )

    body = clean_text(
        body
    )

    caption = (
        f"{headline}\n\n"
        f"{body}\n\n"
        f"@yegnaLiverpool"
    )

    if len(caption) > 1024:

        caption = (
            caption[:1020]
            + "..."
        )

    return caption


# =========================================================
# PROCESS NEWS
# =========================================================

def process_news(entry):

    title = clean_text(
        entry.get("title", "")
    )

    url = entry.get(
        "url",
        ""
    )

    source = entry.get(
        "source_title",
        ""
    )

    summary = clean_text(
        entry.get(
            "summary",
            ""
        )
    )

    if not title:
        return False

    if not is_liverpool_related(
        title + " " + summary
    ):
        return False

    if news_was_posted(
        title,
        url
    ):

        logger.info(
            "Duplicate news skipped."
        )

        return False

    if not can_post():

        logger.info(
            "Minimum post gap not reached."
        )

        return False

    article = fetch_article(
        url
    )

    if article:

        body = clean_text(
            article.get(
                "body",
                ""
            )
        )

        image_url = article.get(
            "image_url"
        )

        final_title = (
            article.get("title")
            or title
        )

    else:

        body = summary
        image_url = None
        final_title = title

    if len(body) < 150:

        logger.info(
            "Article too short. Skipping."
        )

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

        logger.info(
            "AI rejected news."
        )

        return False

    try:

        confidence = float(
            edited.get(
                "confidence",
                0
            )
        )

    except:

        confidence = 0

    if confidence < 75:

        logger.info(
            "AI confidence too low: %s",
            confidence
        )

        return False

    headline = clean_text(
        edited.get(
            "headline",
            ""
        )
    )

    edited_body = clean_text(
        edited.get(
            "body",
            ""
        )
    )

    if not headline or not edited_body:

        return False

    caption = make_caption(
        headline,
        edited_body
    )

    image_hash = ""
    success = False

    # =====================================================
    # SEND IMAGE
    # =====================================================

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

    # =====================================================
    # SEND TEXT IF IMAGE FAILED
    # =====================================================

    if not success:

        success = telegram_send_message(
            caption
        )

    # =====================================================
    # SAVE POST
    # =====================================================

    if success:

        save_post(

            headline,

            url,

            source,

            image_hash

        )

        logger.info(
            "Posted successfully: %s",
            headline
        )

        return True

    logger.error(
        "Telegram posting failed."
    )

    return False


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
        "Channel: %s",
        CHANNEL
    )

    logger.info(
        "Check interval: %s seconds",
        CHECK_EVERY
    )

    logger.info(
        "=" * 60
    )

    # Create database
    conn = get_db()
    conn.close()

    # =====================================================
    # STARTUP TEST
    # =====================================================

    if SEND_STARTUP_TEST:

        success = telegram_send_message(
            "🤖 Liverpool News Bot ተጀምሯል ✅"
        )

        if success:

            logger.info(
                "Startup test sent successfully."
            )

        else:

            logger.warning(
                "Startup test failed."
            )

    # =====================================================
    # LOOP
    # =====================================================

    while True:

        try:

            logger.info(
                "Checking social sources..."
            )

            news = get_social_news()

            # Newest first
            news.sort(
                key=lambda item: (
                    item.get("published")
                    or time.gmtime(0)
                ),
                reverse=True
            )

            posted = 0

            for item in news:

                if posted >= MAX_POSTS_PER_CYCLE:
                    break

                try:

                    if process_news(item):

                        posted += 1

                except Exception as e:

                    logger.exception(
                        "News processing error: %s",
                        e
                    )

            if posted == 0:

                logger.info(
                    "No new Liverpool news posted."
                )

            else:

                logger.info(
                    "Posted %s news item(s).",
                    posted
                )

        except Exception as e:

            logger.exception(
                "Main loop error: %s",
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
            "Bot stopped by user."
        )

    except Exception as e:

        logger.exception(
            "Fatal error: %s",
            e
        )

        raise


# =========================================================
# END OF FILE
# =========================================================
```
