import os
import re
import time
import json
import hashlib
import logging
import sqlite3
from datetime import datetime, timezone

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

CHANNEL = os.getenv(
    "CHANNEL",
    "@yegnaLiverpool"
).strip()

GROQ_MODEL = os.getenv(
    "GROQ_MODEL",
    "openai/gpt-oss-120b"
).strip()

CHECK_EVERY = 5 * 60

# Minimum time between posts
MIN_POST_GAP = 15 * 60

# Maximum posts in one checking cycle
MAX_POSTS_PER_CYCLE = 1

# Only news newer than this many hours is considered
MAX_NEWS_AGE_HOURS = 48

DB_FILE = "liverpool_news.db"

SEND_STARTUP_TEST = os.getenv(
    "SEND_STARTUP_TEST",
    "true"
).lower() == "true"

REQUEST_TIMEOUT = 30


# =========================================================
# HTTP HEADERS
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
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9"
}


# =========================================================
# SOURCES
# =========================================================
#
# RSSHub X/Twitter routes can sometimes stop working.
# Therefore each source is isolated so one failure does
# not stop the whole bot.
#
# Keep this list limited.
# =========================================================

SOCIAL_SOURCES = [
    {
        "name": "Fabrizio Romano",
        "rss": "https://rsshub.app/twitter/user/FabrizioRomano"
    },
    {
        "Anfield Watch",
        "rss": "https://rsshub.app/twitter/user/AnfieldWatch"
    },
    {
        "Anfield Edition",
        "rss": "https://rsshub.app/twitter/user/AnfieldEdition"
    }
]


# =========================================================
# LIVERPOOL KEYWORDS
# =========================================================

LIVERPOOL_KEYWORDS = [

    "liverpool",
    "lfc",
    "anfield",

    "arne slot",
    "slot",

    "iraola",
    "andoni iraola",

    "salah",
    "mohamed salah",

    "van dijk",
    "virgil van dijk",
    "virgil",

    "alisson",

    "gakpo",
    "cody gakpo",

    "diaz",
    "luis diaz",

    "nunez",
    "darwin nunez",

    "mac allister",
    "alexis mac allister",

    "gravenberch",

    "szoboszlai",

    "frimpong",

    "wirtz",
    "florian wirtz",

    "konate",
    "ibrahima konate",

    "robertson",

    "elliott",

    "bradley",

    "jones",

    "chiesa",

    "endo",

    "ngumoha",

    "mamadou doumbia"
]


# =========================================================
# VALIDATION
# =========================================================

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN missing. Add BOT_TOKEN to your .env file."
    )

if not GROQ_API_KEY:
    raise RuntimeError(
        "GROQ_API_KEY missing. Add GROQ_API_KEY to your .env file."
    )


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

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_posted_at
        ON posted_news(posted_at)
    """)

    conn.commit()

    return conn


# =========================================================
# TEXT HELPERS
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

    text = clean_text(
        text
    ).lower()

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

def is_liverpool_related(text):

    text = clean_text(
        text
    ).lower()

    if not text:
        return False

    for keyword in LIVERPOOL_KEYWORDS:

        if keyword in text:
            return True

    return False


# =========================================================
# DATE HELPERS
# =========================================================

def entry_timestamp(entry):

    published = getattr(
        entry,
        "published_parsed",
        None
    )

    if published:

        try:
            return int(
                time.mktime(
                    published
                )
            )
        except Exception:
            pass

    updated = getattr(
        entry,
        "updated_parsed",
        None
    )

    if updated:

        try:
            return int(
                time.mktime(
                    updated
                )
            )
        except Exception:
            pass

    return 0


def is_recent(timestamp):

    if not timestamp:
        return True

    age = (
        time.time()
        - timestamp
    )

    return age <= (
        MAX_NEWS_AGE_HOURS * 3600
    )


# =========================================================
# SOCIAL RSS
# =========================================================

def get_social_news():

    news = []

    for source in SOCIAL_SOURCES:

        try:

            logger.info(
                "Checking source: %s",
                source["name"]
            )

            feed = feedparser.parse(
                source["rss"]
            )

            if getattr(
                feed,
                "bozo",
                False
            ):

                logger.warning(
                    "RSS warning from %s",
                    source["name"]
                )

            entries = getattr(
                feed,
                "entries",
                []
            )

            logger.info(
                "%s entries found: %s",
                source["name"],
                len(entries)
            )

            for entry in entries:

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

                link = clean_text(
                    getattr(
                        entry,
                        "link",
                        ""
                    )
                )

                if not title:
                    continue

                combined = (
                    title
                    + " "
                    + summary
                )

                if not is_liverpool_related(
                    combined
                ):
                    continue

                published_ts = entry_timestamp(
                    entry
                )

                if not is_recent(
                    published_ts
                ):
                    continue

                news.append({

                    "title": title,

                    "summary": summary,

                    "url": link,

                    "source_title": source["name"],

                    "published_ts": published_ts

                })

        except Exception as e:

            logger.warning(
                "Source failed [%s]: %s",
                source["name"],
                e
            )

    # =====================================================
    # DEDUPLICATE RSS RESULTS
    # =====================================================

    unique = {}

    for item in news:

        title_key = make_hash(
            normalize(
                item["title"]
            )
        )

        url_key = make_hash(
            normalize(
                item["url"]
            )
        )

        key = (
            title_key
            + url_key
        )

        if key not in unique:

            unique[key] = item

    result = list(
        unique.values()
    )

    result.sort(
        key=lambda x: x.get(
            "published_ts",
            0
        ),
        reverse=True
    )

    logger.info(
        "Unique Liverpool news found: %s",
        len(result)
    )

    return result


# =========================================================
# TELEGRAM API
# =========================================================

def telegram_api(
    method,
    data=None,
    files=None
):

    url = (
        "https://api.telegram.org/bot"
        f"{BOT_TOKEN}/{method}"
    )

    try:

        response = requests.post(
            url,
            data=data,
            files=files,
            timeout=40
        )

        try:

            result = response.json()

        except ValueError:

            logger.error(
                "Telegram returned invalid JSON. HTTP %s",
                response.status_code
            )

            return {
                "ok": False
            }

        if not result.get("ok"):

            logger.error(
                "Telegram API error: %s",
                result
            )

        return result

    except requests.RequestException as e:

        logger.error(
            "Telegram connection error: %s",
            e
        )

        return {
            "ok": False
        }

    except Exception as e:

        logger.exception(
            "Telegram unexpected error: %s",
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
    image_bytes,
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
                "liverpool.jpg",
                image_bytes,
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
            .get(
                "content-type",
                ""
            )
            .lower()
        )

        if (
            "image" not in content_type
            and not url.lower().endswith(
                (
                    ".jpg",
                    ".jpeg",
                    ".png",
                    ".webp"
                )
            )
        ):

            return None

        if len(response.content) < 15000:
            return None

        image_hash = hashlib.sha256(
            response.content
        ).hexdigest()

        return {

            "bytes": response.content,

            "hash": image_hash,

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

def image_used(
    image_hash
):

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
        (
            image_hash,
        )
    ).fetchone()

    conn.close()

    return row is not None


def save_image(
    image_hash,
    image_url
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
        VALUES
        (
            ?,
            ?,
            ?
        )
        """,
        (
            image_hash,
            image_url,
            int(time.time())
        )
    )

    conn.commit()
    conn.close()


# =========================================================
# NEWS DATABASE
# =========================================================

def make_news_fingerprint(
    title,
    url
):

    normalized_title = normalize(
        title
    )

    normalized_url = normalize(
        url
    )

    return make_hash(
        normalized_title
        + "|"
        + normalized_url
    )


def news_was_posted(
    title,
    url
):

    fingerprint = make_news_fingerprint(
        title,
        url
    )

    conn = get_db()

    row = conn.execute(
        """
        SELECT id
        FROM posted_news
        WHERE fingerprint=?
        LIMIT 1
        """,
        (
            fingerprint,
        )
    ).fetchone()

    conn.close()

    if row:
        return True

    # Extra title-only protection
    title_normalized = normalize(
        title
    )

    if title_normalized:

        rows = conn = get_db()

        recent = rows.execute(
            """
            SELECT title
            FROM posted_news
            ORDER BY posted_at DESC
            LIMIT 100
            """
        ).fetchall()

        rows.close()

        for row in recent:

            old_title = normalize(
                row[0]
            )

            if (
                title_normalized
                == old_title
            ):
                return True

    return False


def save_post(
    title,
    url,
    source,
    image_hash=""
):

    fingerprint = make_news_fingerprint(
        title,
        url
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
            ?,
            ?,
            ?,
            ?,
            ?,
            ?
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

    elapsed = (
        time.time()
        - int(row[0])
    )

    if elapsed >= MIN_POST_GAP:
        return True

    remaining = (
        MIN_POST_GAP
        - elapsed
    )

    logger.info(
        "Post gap active. %.0f seconds remaining.",
        remaining
    )

    return False


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
            timeout=REQUEST_TIMEOUT
        )

        if response.status_code != 200:

            logger.warning(
                "Article HTTP %s: %s",
                response.status_code,
                url
            )

            return None

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        # Remove unnecessary HTML
        for tag in soup(
            [
                "script",
                "style",
                "noscript",
                "svg"
            ]
        ):
            tag.decompose()

        paragraphs = []

        for paragraph in soup.find_all("p"):

            text = clean_text(
                paragraph.get_text()
            )

            if len(text) >= 40:

                paragraphs.append(
                    text
                )

        # Remove duplicate paragraphs
        seen = set()
        clean_paragraphs = []

        for paragraph in paragraphs:

            key = normalize(
                paragraph
            )

            if key in seen:
                continue

            seen.add(key)

            clean_paragraphs.append(
                paragraph
            )

        body = "\n".join(
            clean_paragraphs
        )

        # =================================================
        # IMAGE
        # =================================================

        image_url = None

        og_image = soup.find(
            "meta",
            property="og:image"
        )

        if og_image:

            image_url = (
                og_image.get(
                    "content"
                )
                or ""
            ).strip()

        if image_url:

            image_url = image_urljoin = (
                url
            )

            # Make absolute URL safely
            from urllib.parse import urljoin

            image_url = urljoin(
                response.url,
                og_image.get("content", "")
            )

        # =================================================
        # TITLE
        # =================================================

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

    except requests.RequestException as e:

        logger.warning(
            "Article request failed: %s",
            e
        )

        return None

    except Exception as e:

        logger.warning(
            "Article parsing failed: %s",
            e
        )

        return None


# =========================================================
# GROQ PROMPT
# =========================================================

NEWS_EDITOR_PROMPT = """
አንተ የLiverpool FC የአማርኛ ስፖርት ዜና አርታዒ ነህ።

የተሰጠህን SOURCE, TITLE እና ARTICLE ብቻ
ተጠቅመህ የተፈጥሯዊ፣ ግልጽ፣ ሙያዊ
የአማርኛ ዜና አዘጋጅ።

ጥብቅ ህጎች፦

1. ከARTICLE ውጭ ምንም እውነታ አትጨምር።

2. የማታውቀውን ስም፣ ቀን፣ ዋጋ፣ የዝውውር
   መጠን፣ ውል፣ ጉዳት፣ ጥቅስ ወይም ሌላ
   መረጃ አትፍጠር።

3. ዜናው Liverpool FCን በግልጽ ካልመለከተ
   decision = "REJECT" አድርግ።

4. የመረጃው ምንጭ እርግጠኛ እንዳልሆነ
   እንደተረጋገጠ አታቀርበው።

5. የዜናውን ዋና ነጥብ ብቻ አቅርብ።

6. በተፈጥሯዊ አማርኛ ጻፍ።

7. የእንግሊዝኛ headline አታስቀምጥ።

8. Hashtag አትጠቀም።

9. Markdown አትጠቀም።

10. ማስረጃ የሌለውን "ተረጋግጧል",
    "ይፋ ሆኗል", "ተፈራርሟል" ወዘተ
    አትጠቀም።

11. ከምንጩ የተገኘው ነገር ወሬ ወይም
    ሪፖርት ከሆነ እንደዚያው አቅርብ።

JSON ብቻ መልስ፦

{
    "decision": "POST",
    "headline": "",
    "body": "",
    "confidence": 95
}
"""


# =========================================================
# GROQ AI
# =========================================================

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

            temperature=0.1,

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

        if not content:
            return None

        data = json.loads(
            content
        )

        if not isinstance(
            data,
            dict
        ):
            return None

        return data

    except json.JSONDecodeError as e:

        logger.error(
            "Groq returned invalid JSON: %s",
            e
        )

        return None

    except Exception as e:

        logger.error(
            "Groq error: %s",
            e
        )

        return None


# =========================================================
# AI RESULT VALIDATION
# =========================================================

def validate_ai_result(
    result
):

    if not isinstance(
        result,
        dict
    ):
        return False

    decision = str(
        result.get(
            "decision",
            ""
        )
    ).upper().strip()

    if decision != "POST":
        return False

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

    try:

        confidence = float(
            result.get(
                "confidence",
                0
            )
        )

    except Exception:

        confidence = 0

    if not headline:
        return False

    if not body:
        return False

    if confidence < 75:
        return False

    # Must contain at least some Amharic
    amharic_count = len(
        re.findall(
            r"[\u1200-\u137f]",
            headline + " " + body
        )
    )

    if amharic_count < 10:

        logger.warning(
            "AI result contains too little Amharic."
        )

        return False

    return True


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

    footer = (
        "\n\n"
        "@yegnaLiverpool"
    )

    max_text = (
        1024
        - len(footer)
    )

    combined = (
        headline
        + "\n\n"
        + body
    )

    if len(combined) > max_text:

        combined = (
            combined[:max_text - 3]
            + "..."
        )

    return (
        combined
        + footer
    )


# =========================================================
# PROCESS NEWS
# =========================================================

def process_news(
    entry
):

    title = clean_text(
        entry.get(
            "title",
            ""
        )
    )

    summary = clean_text(
        entry.get(
            "summary",
            ""
        )
    )

    url = clean_text(
        entry.get(
            "url",
            ""
        )
    )

    source = clean_text(
        entry.get(
            "source_title",
            ""
        )
    )

    if not title:
        return False

    combined = (
        title
        + " "
        + summary
    )

    if not is_liverpool_related(
        combined
    ):

        logger.info(
            "Not Liverpool related. Skipped."
        )

        return False

    if news_was_posted(
        title,
        url
    ):

        logger.info(
            "Already posted. Skipped."
        )

        return False

    if not can_post():

        return False

    # =====================================================
    # FETCH ARTICLE
    # =====================================================

    article = fetch_article(
        url
    )

    if article:

        article_body = clean_text(
            article.get(
                "body",
                ""
            )
        )

        article_title = clean_text(
            article.get(
                "title",
                ""
            )
        )

        image_url = article.get(
            "image_url"
        )

        final_title = (
            article_title
            or title
        )

    else:

        article_body = summary

        image_url = None

        final_title = title

    # =====================================================
    # ARTICLE TOO SHORT
    # =====================================================

    if len(article_body) < 150:

        logger.info(
            "Article too short. Skipped: %s",
            title
        )

        return False

    # =====================================================
    # AI
    # =====================================================

    edited = ai_edit_news(

        final_title,

        article_body,

        source,

        url

    )

    if not validate_ai_result(
        edited
    ):

        logger.info(
            "AI rejected/failed validation: %s",
            title
        )

        return False

    headline = clean_text(
        edited["headline"]
    )

    body = clean_text(
        edited["body"]
    )

    caption = make_caption(
        headline,
        body
    )

    # =====================================================
    # IMAGE
    # =====================================================

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

                    image_hash = image[
                        "hash"
                    ]

                    save_image(

                        image[
                            "hash"
                        ],

                        image[
                            "url"
                        ]

                    )

            else:

                logger.info(
                    "Image already used. Sending text."
                )

    # =====================================================
    # TEXT FALLBACK
    # =====================================================

    if not success:

        success = telegram_send_message(
            caption
        )

    # =====================================================
    # SAVE ONLY AFTER TELEGRAM SUCCESS
    # =====================================================

    if success:

        save_post(

            headline,

            url,

            source,

            image_hash

        )

        logger.info(
            "SUCCESS: Posted -> %s",
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
        "Liverpool News Bot Started"
    )

    logger.info(
        "Channel: %s",
        CHANNEL
    )

    logger.info(
        "Check every: %s seconds",
        CHECK_EVERY
    )

    logger.info(
        "Minimum post gap: %s seconds",
        MIN_POST_GAP
    )

    logger.info(
        "=" * 60
    )

    # Initialize database
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
                "Startup test sent."
            )

        else:

            logger.warning(
                "Startup test failed."
            )

    # =====================================================
    # CONTINUOUS LOOP
    # =====================================================

    while True:

        try:

            logger.info(
                "Checking news sources..."
            )

            news = get_social_news()

            posted_count = 0

            for item in news:

                if (
                    posted_count
                    >= MAX_POSTS_PER_CYCLE
                ):
                    break

                try:

                    if process_news(
                        item
                    ):

                        posted_count += 1

                except Exception as e:

                    logger.exception(
                        "process_news error: %s",
                        e
                    )

            if posted_count:

                logger.info(
                    "Cycle finished. Posted: %s",
                    posted_count
                )

            else:

                logger.info(
                    "Cycle finished. Nothing posted."
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
