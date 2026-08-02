import os
import re
import json
import time
import hashlib
import logging
import sqlite3
from datetime import datetime, timezone
from urllib.parse import quote_plus, urlparse

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

CHANNEL_ID = os.getenv(
    "CHANNEL_ID",
    "@yegnaLiverpool"
).strip()

GROQ_MODEL = os.getenv(
    "GROQ_MODEL",
    "openai/gpt-oss-120b"
).strip()

MAX_NEWS_AGE_HOURS = int(
    os.getenv("MAX_NEWS_AGE_HOURS", "24")
)

CHECK_INTERVAL_MINUTES = int(
    os.getenv("CHECK_INTERVAL_MINUTES", "5")
)

LIVERPOOL_TEAM_ID = "364"
DB_FILE = "news.db"


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

db.execute("""
CREATE TABLE IF NOT EXISTS live_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key TEXT UNIQUE,
    event_type TEXT,
    event_text TEXT,
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
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,image/avif,"
        "image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9"
}


# =========================================================
# TEXT HELPERS
# =========================================================

def clean_text(text):
    if not text:
        return ""

    text = BeautifulSoup(
        str(text),
        "html.parser"
    ).get_text(" ", strip=True)

    text = re.sub(r"\s+", " ", text)

    return text.strip()


def escape_html(text):
    if not text:
        return ""

    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def normalize_text(text):
    text = clean_text(text).lower()

    text = re.sub(
        r"https?://\S+",
        "",
        text
    )

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


def make_fingerprint(title, url=""):
    """
    URL ብቻ ላይ አንመሰረትም።
    ተመሳሳይ ዜና ከሌላ source URL ቢመጣም
    title fingerprint በመጠቀም እንዳይደገም ይረዳል።
    """

    normalized_title = normalize_text(title)

    raw = normalized_title

    if not raw:
        raw = normalize_text(url)

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()


def title_tokens(title):
    words = normalize_text(title).split()

    stop_words = {
        "the", "a", "an", "and", "or",
        "of", "to", "in", "on", "for",
        "with", "is", "are", "at", "from",
        "liverpool", "fc"
    }

    return {
        word
        for word in words
        if len(word) >= 3
        and word not in stop_words
    }


def titles_are_similar(title1, title2):
    a = title_tokens(title1)
    b = title_tokens(title2)

    if not a or not b:
        return False

    intersection = len(a & b)
    smaller = min(len(a), len(b))

    if smaller == 0:
        return False

    return (
        intersection / smaller
    ) >= 0.72


# =========================================================
# DATABASE NEWS CHECKS
# =========================================================

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


def similar_news_already_posted(title):
    rows = db.execute(
        """
        SELECT title
        FROM posted_news
        ORDER BY id DESC
        LIMIT 150
        """
    ).fetchall()

    for row in rows:
        old_title = row[0]

        if titles_are_similar(
            title,
            old_title
        ):
            return True

    return False


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
            datetime.now(
                timezone.utc
            ).isoformat()
        )
    )

    db.commit()


# =========================================================
# LIVE DATABASE
# =========================================================

def live_event_already_posted(event_key):
    row = db.execute(
        """
        SELECT 1
        FROM live_events
        WHERE event_key = ?
        LIMIT 1
        """,
        (event_key,)
    ).fetchone()

    return row is not None


def save_live_event(
    event_key,
    event_type,
    event_text
):
    db.execute(
        """
        INSERT OR IGNORE INTO live_events
        (event_key, event_type, event_text, posted_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            event_key,
            event_type,
            event_text,
            datetime.now(
                timezone.utc
            ).isoformat()
        )
    )

    db.commit()


# =========================================================
# DATE
# =========================================================

def parse_entry_time(entry):

    try:
        if (
            getattr(
                entry,
                "published_parsed",
                None
            )
        ):
            return datetime.fromtimestamp(
                time.mktime(
                    entry.published_parsed
                ),
                tz=timezone.utc
            )

        if (
            getattr(
                entry,
                "updated_parsed",
                None
            )
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

    now = datetime.now(
        timezone.utc
    )

    age = (
        now - published
    ).total_seconds()

    return age >= 0 and age <= (
        MAX_NEWS_AGE_HOURS * 3600
    )


# =========================================================
# GOOGLE NEWS
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
        response = requests.get(
            google_news_rss(query),
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
# URL
# =========================================================

def resolve_article_url(url):

    if not url:
        return ""

    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=20,
            allow_redirects=True
        )

        return response.url or url

    except Exception:
        return url


def get_domain(url):

    try:
        return urlparse(
            url
        ).netloc.lower().replace(
            "www.",
            ""
        )

    except Exception:
        return ""


# =========================================================
# SOURCE VALIDATION
# =========================================================

def source_domain_allowed(
    source_name,
    url
):

    domain = get_domain(
        resolve_article_url(url)
    )

    allowed = TRUSTED_SOURCES.get(
        source_name,
        []
    )

    for item in allowed:

        item = item.lower()

        if (
            domain == item
            or domain.endswith(
                "." + item
            )
        ):
            return True

    return False


# =========================================================
# IMAGE
# =========================================================

def is_valid_image_url(url):

    if not url:
        return False

    url = str(url).strip()

    return url.startswith(
        ("http://", "https://")
    )


def get_feed_image(entry):

    try:
        for media in getattr(
            entry,
            "media_content",
            []
        ):

            url = media.get(
                "url",
                ""
            )

            if is_valid_image_url(url):
                return url

    except Exception:
        pass

    try:
        for media in getattr(
            entry,
            "media_thumbnail",
            []
        ):

            url = media.get(
                "url",
                ""
            )

            if is_valid_image_url(url):
                return url

    except Exception:
        pass

    try:
        for enclosure in getattr(
            entry,
            "enclosures",
            []
        ):

            url = enclosure.get(
                "href",
                ""
            ) or enclosure.get(
                "url",
                ""
            )

            if is_valid_image_url(url):
                return url

    except Exception:
        pass

    try:
        summary = getattr(
            entry,
            "summary",
            ""
        )

        soup = BeautifulSoup(
            summary,
            "html.parser"
        )

        image = soup.find("img")

        if image:

            url = (
                image.get("src", "")
                or image.get("data-src", "")
            )

            if is_valid_image_url(url):
                return url

    except Exception:
        pass

    return ""


def get_page_image(url):

    if not url:
        return ""

    try:

        actual_url = resolve_article_url(
            url
        )

        response = requests.get(
            actual_url,
            headers=HEADERS,
            timeout=20
        )

        response.raise_for_status()

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        selectors = [
            (
                "meta",
                {
                    "property": "og:image"
                }
            ),
            (
                "meta",
                {
                    "name": "twitter:image"
                }
            ),
            (
                "meta",
                {
                    "name": "twitter:image:src"
                }
            )
        ]

        for tag, attrs in selectors:

            image = soup.find(
                tag,
                attrs=attrs
            )

            if image:

                image_url = image.get(
                    "content",
                    ""
                )

                if is_valid_image_url(
                    image_url
                ):
                    return image_url

    except Exception as e:

        logger.warning(
            "Page image error: %s",
            e
        )

    return ""


def get_article_image(
    entry,
    url
):

    image = get_feed_image(entry)

    if image:
        return image

    return get_page_image(url)


# =========================================================
# IMAGE DOWNLOAD
# =========================================================

def download_image(image_url):

    if not is_valid_image_url(
        image_url
    ):
        return None, None

    try:

        response = requests.get(
            image_url,
            headers=HEADERS,
            timeout=30,
            allow_redirects=True
        )

        response.raise_for_status()

        content = response.content

        if not content:
            return None, None

        if len(content) > 10 * 1024 * 1024:
            return None, None

        content_type = (
            response.headers
            .get(
                "Content-Type",
                ""
            )
            .lower()
        )

        valid_types = (
            "image/jpeg",
            "image/jpg",
            "image/png",
            "image/webp",
            "image/gif"
        )

        valid_signature = (
            content.startswith(b"\xff\xd8\xff")
            or content.startswith(b"\x89PNG")
            or content.startswith(b"RIFF")
            or content.startswith(b"GIF8")
        )

        if (
            not any(
                x in content_type
                for x in valid_types
            )
            and not valid_signature
        ):
            return None, None

        if "png" in content_type:
            filename = "liverpool_news.png"

        elif "webp" in content_type:
            filename = "liverpool_news.webp"

        elif "gif" in content_type:
            filename = "liverpool_news.gif"

        else:
            filename = "liverpool_news.jpg"

        return content, filename

    except Exception as e:

        logger.warning(
            "Image download failed: %s",
            e
        )

        return None, None


# =========================================================
# NEWS COLLECTION
# =========================================================

def collect_news():

    articles = []

    queries = [
        (
            "Liverpool FC Official",
            "site:liverpoolfc.com/news Liverpool"
        ),
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
        )
    ]

    for source_name, query in queries:

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

            # -------------------------------------------------
            # REAL SOURCE CHECK
            # -------------------------------------------------

            if not source_domain_allowed(
                source_name,
                url
            ):
                logger.info(
                    "Rejected wrong domain: %s | %s",
                    source_name,
                    url
                )
                continue

            image_url = get_article_image(
                entry,
                url
            )

            articles.append({
                "title": title,
                "url": url,
                "summary": summary,
                "source": source_name,
                "image_url": image_url
            })

    logger.info(
        "Collected %s trusted articles.",
        len(articles)
    )

    return articles


# =========================================================
# LIVERPOOL FILTER
# =========================================================

LIVERPOOL_KEYWORDS = [
    "liverpool",
    "liverpool fc",
    "lfc",
    "anfield",
    "reds",
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
# AI SYSTEM PROMPT
# =========================================================

SYSTEM_PROMPT = """
አንተ ለLiverpool FC የአማርኛ Telegram የዜና አርታኢ ነህ።

የተሰጠህን መረጃ ብቻ ተጠቅመህ
ተፈጥሯዊ፣ ግልጽ እና የስፖርት ዘገባ የሚመስል
አማርኛ ዜና አዘጋጅ።

ጥብቅ ህጎች፦

1. ከarticle ውጭ እውነታ አትጨምር።
2. Quote አትፍጠር።
3. Transfer fee አትፍጠር።
4. ቀን አትፍጠር።
5. Injury አትፍጠር።
6. Contract information አትፍጠር።
7. Rumour/report ከሆነ እንደተረጋገጠ አታቅርብ።
8. እርግጠኛ ያልሆነን መረጃ እርግጠኛ አታድርገው።
9. Liverpool FC ላይ በግልጽ ካልሆነ REJECT።
10. ተፈጥሯዊ የስፖርት አማርኛ ተጠቀም።
11. ቃል በቃል አትተርጉም።
12. English headline አትጻፍ።
13. English paragraph አትጻፍ።
14. Clickbait አትጠቀም።
15. ተመሳሳይ ሀሳብ አትደግም።
16. ዋናውን ነጥብ በግልጽ አማርኛ አቅርብ።
17. የሰዎችን ስም በተቻለ መጠን ትክክል ጠብቅ።
18. ምንም English sentence በheadline ወይም body ውስጥ አታስገባ።
19. የarticle summary በጣም አጭር ከሆነ እውነታ አትጨምር።
20. ዜናው የተረጋገጠ እንዳልሆነ ከሆነ በbody ውስጥ "ሪፖርት"፣ "ዘገባው እንደሚለው" ወይም ተመሳሳይ ቃል ተጠቀም።

JSON ብቻ መልስ።

Format:

{
  "decision": "POST" or "REJECT",
  "category": "news/transfer/rumour/injury/match/other",
  "headline": "አጭር የአማርኛ ርዕስ",
  "body": "የአማርኛ ዜና",
  "confidence": 0-100
}
"""


# =========================================================
# AI
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

    prompt = f"""
TRUSTED SOURCE:
{source}

TITLE:
{title}

ARTICLE SUMMARY:
{summary}

Liverpool FC ላይ የተመሰረተ ዜና ከሆነ ብቻ POST አድርግ።

የተሰጠውን መረጃ ብቻ ተጠቀም።
ምንም አዲስ እውነታ አትጨምር።

JSON only.
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
                        "content": prompt
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
# TELEGRAM CHECK
# =========================================================

def telegram_check_bot():

    url = (
        f"https://api.telegram.org/bot"
        f"{BOT_TOKEN}/getMe"
    )

    try:

        response = requests.get(
            url,
            timeout=20
        )

        if response.status_code != 200:
            return False

        data = response.json()

        if data.get("ok"):

            bot_info = data.get(
                "result",
                {}
            )

            logger.info(
                "Telegram connected: @%s",
                bot_info.get(
                    "username",
                    "unknown"
                )
            )

            return True

    except Exception as e:

        logger.exception(
            "Telegram check error: %s",
            e
        )

    return False


# =========================================================
# TELEGRAM TEXT
# =========================================================

def telegram_send_message(text):

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

        logger.info(
            "Telegram text status: %s",
            response.status_code
        )

        if response.status_code != 200:
            logger.error(
                "Telegram text error: %s",
                response.text
            )
            return False

        data = response.json()

        return data.get("ok") is True

    except Exception as e:

        logger.exception(
            "Telegram send error: %s",
            e
        )

        return False


# =========================================================
# TELEGRAM PHOTO
# =========================================================

def telegram_send_photo(
    image_url,
    caption
):

    image_data, filename = download_image(
        image_url
    )

    if not image_data:
        return False

    if len(caption) > 1024:
        caption = (
            caption[:1020]
            + "..."
        )

    url = (
        f"https://api.telegram.org/bot"
        f"{BOT_TOKEN}/sendPhoto"
    )

    files = {
        "photo": (
            filename or "liverpool_news.jpg",
            image_data,
            "image/jpeg"
        )
    }

    data = {
        "chat_id": CHANNEL_ID,
        "caption": caption,
        "parse_mode": "HTML"
    }

    try:

        response = requests.post(
            url,
            data=data,
            files=files,
            timeout=60
        )

        logger.info(
            "Telegram photo status: %s",
            response.status_code
        )

        if response.status_code != 200:
            logger.error(
                "Telegram photo error: %s",
                response.text
            )
            return False

        result = response.json()

        return result.get("ok") is True

    except Exception as e:

        logger.exception(
            "Telegram photo error: %s",
            e
        )

        return False


# =========================================================
# FORMAT NEWS
# =========================================================

def build_telegram_message(result):

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

    if not headline or not body:
        return None

    return (
        f"<b>{escape_html(headline)}</b>\n\n"
        f"{escape_html(body)}\n\n"
        f"<b>@yegnaLiverpool</b>"
    )


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

    summary = article.get(
        "summary",
        ""
    )

    image_url = article.get(
        "image_url",
        ""
    )

    source = article.get(
        "source",
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
            "Rejected non-Liverpool article: %s",
            title
        )
        return False

    # -----------------------------------------------------
    # Trusted source verification
    # -----------------------------------------------------

    if not source_domain_allowed(
        source,
        url
    ):
        return False

    # -----------------------------------------------------
    # Exact duplicate
    # -----------------------------------------------------

    fingerprint = make_fingerprint(
        title,
        url
    )

    if already_posted(
        fingerprint
    ):
        logger.info(
            "Exact duplicate skipped: %s",
            title
        )
        return False

    # -----------------------------------------------------
    # Similar duplicate
    # -----------------------------------------------------

    if similar_news_already_posted(
        title
    ):
        logger.info(
            "Similar news skipped: %s",
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

    result = ai_analyze(article)

    if not result:
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

    if decision != "POST":
        logger.info(
            "AI rejected: %s",
            title
        )
        return False

    if confidence < 75:
        logger.info(
            "Confidence too low: %s",
            confidence
        )
        return False

    message = build_telegram_message(
        result
    )

    if not message:
        return False

    # -----------------------------------------------------
    # PHOTO
    # -----------------------------------------------------

    if image_url:

        if telegram_send_photo(
            image_url,
            message
        ):

            save_posted(
                fingerprint,
                title,
                url,
                source
            )

            logger.info(
                "POSTED WITH IMAGE: %s",
                title
            )

            return True

        # fallback image

        fallback_image = get_page_image(
            url
        )

        if (
            fallback_image
            and fallback_image != image_url
        ):

            if telegram_send_photo(
                fallback_image,
                message
            ):

                save_posted(
                    fingerprint,
                    title,
                    url,
                    source
                )

                logger.info(
                    "POSTED WITH FALLBACK IMAGE: %s",
                    title
                )

                return True

    # -----------------------------------------------------
    # TEXT FALLBACK
    # -----------------------------------------------------

    if telegram_send_message(
        message
    ):

        save_posted(
            fingerprint,
            title,
            url,
            source
        )

        logger.info(
            "POSTED TEXT ONLY: %s",
            title
        )

        return True

    return False


# =========================================================
# CHECK NEWS
# =========================================================

def check_news():

    logger.info(
        "Checking trusted Liverpool news..."
    )

    articles = collect_news()

    if not articles:
        logger.info(
            "No recent news."
        )
        return

    posted_count = 0

    # Sort newest entries first when dates are available.
    # Google News already generally does this.

    for article in articles:

        if posted_count >= 3:
            break

        try:

            if process_article(
                article
            ):
                posted_count += 1

            time.sleep(2)

        except Exception as e:

            logger.exception(
                "Article error: %s",
                e
            )

    logger.info(
        "News finished. Posted: %s",
        posted_count
    )


# =========================================================
# ESPN
# =========================================================

def get_liverpool_scoreboard():

    url = (
        "https://site.api.espn.com/apis/site/v2/sports/"
        "soccer/eng.1/scoreboard"
    )

    try:

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=20
        )

        response.raise_for_status()

        return response.json()

    except Exception as e:

        logger.error(
            "ESPN error: %s",
            e
        )

        return None


# =========================================================
# FIND MATCH
# =========================================================

def find_liverpool_match(data):

    if not data:
        return None

    for event in data.get(
        "events",
        []
    ):

        for competition in event.get(
            "competitions",
            []
        ):

            for team in competition.get(
                "competitors",
                []
            ):

                team_id = str(
                    team.get(
                        "team",
                        {}
                    ).get(
                        "id",
                        ""
                    )
                )

                if team_id == LIVERPOOL_TEAM_ID:
                    return event

    return None


# =========================================================
# STATUS
# =========================================================

def get_match_status(event):

    status = event.get(
        "status",
        {}
    )

    type_data = status.get(
        "type",
        {}
    )

    return (
        type_data.get(
            "state",
            ""
        ),
        type_data.get(
            "name",
            ""
        ),
        type_data.get(
            "detail",
            ""
        )
    )


# =========================================================
# TEAMS
# =========================================================

def get_match_teams(event):

    competitions = event.get(
        "competitions",
        []
    )

    if not competitions:
        return None, None

    competitors = competitions[0].get(
        "competitors",
        []
    )

    home = None
    away = None

    for team in competitors:

        if team.get(
            "homeAway"
        ) == "home":
            home = team

        elif team.get(
            "homeAway"
        ) == "away":
            away = team

    return home, away


# =========================================================
# SCORE
# =========================================================

def match_score_text(
    home,
    away
):

    if not home or not away:
        return ""

    home_name = home.get(
        "team",
        {}
    ).get(
        "displayName",
        "Home"
    )

    away_name = away.get(
        "team",
        {}
    ).get(
        "displayName",
        "Away"
    )

    home_score = home.get(
        "score",
        "0"
    )

    away_score = away.get(
        "score",
        "0"
    )

    return (
        f"{home_name} {home_score} - "
        f"{away_score} {away_name}"
    )


# =========================================================
# LIVE EVENTS
# =========================================================

def extract_live_events(event):

    result = []

    competitions = event.get(
        "competitions",
        []
    )

    if not competitions:
        return result

    details = competitions[0].get(
        "details",
        []
    )

    for detail in details:

        athlete = detail.get(
            "athlete",
            {}
        )

        player = athlete.get(
            "displayName",
            ""
        )

        event_type = (
            detail.get(
                "type",
                {}
            ).get(
                "text",
                ""
            ).lower()
        )

        clock = detail.get(
            "clock",
            {}
        )

        minute = (
            clock.get(
                "displayValue",
                ""
            )
            or str(
                clock.get(
                    "value",
                    ""
                )
            )
        )

        team_id = str(
            detail.get(
                "team",
                {}
            ).get(
                "id",
                ""
            )
        )

        # አሁን የLiverpool ተጫዋች event ብቻ
        if team_id != LIVERPOOL_TEAM_ID:
            continue

        if "goal" in event_type:

            result.append({
                "type": "goal",
                "player": player,
                "minute": minute
            })

        elif "yellow" in event_type:

            result.append({
                "type": "yellow",
                "player": player,
                "minute": minute
            })

        elif "red" in event_type:

            result.append({
                "type": "red",
                "player": player,
                "minute": minute
            })

        elif (
            "substitution" in event_type
            or "substitute" in event_type
        ):

            result.append({
                "type": "substitution",
                "player": player,
                "minute": minute
            })

    return result


# =========================================================
# LIVE MESSAGE
# =========================================================

def build_live_message(
    event,
    event_type,
    extra_text=""
):

    home, away = get_match_teams(
        event
    )

    score = match_score_text(
        home,
        away
    )

    _, name, detail = get_match_status(
        event
    )

    if event_type == "start":

        return (
            "🔴 <b>የጨዋታ መጀመሪያ</b>\n\n"
            f"⚽ {escape_html(score)}\n\n"
            f"▶️ {escape_html(detail or name)}\n\n"
            "<b>@yegnaLiverpool</b>"
        )

    if event_type == "goal":

        return (
            "⚽ <b>ጎል!</b>\n\n"
            f"{escape_html(extra_text)}\n\n"
            f"⚽ {escape_html(score)}\n\n"
            "<b>@yegnaLiverpool</b>"
        )

    if event_type == "yellow":

        return (
            "🟨 <b>ቢጫ ካርድ</b>\n\n"
            f"{escape_html(extra_text)}\n\n"
            f"{escape_html(score)}\n\n"
            "<b>@yegnaLiverpool</b>"
        )

    if event_type == "red":

        return (
            "🟥 <b>ቀይ ካርድ</b>\n\n"
            f"{escape_html(extra_text)}\n\n"
            f"{escape_html(score)}\n\n"
            "<b>@yegnaLiverpool</b>"
        )

    if event_type == "substitution":

        return (
            "🔄 <b>ቅያሬ</b>\n\n"
            f"{escape_html(extra_text)}\n\n"
            f"{escape_html(score)}\n\n"
            "<b>@yegnaLiverpool</b>"
        )

    if event_type == "halftime":

        return (
            "⏸️ <b>እረፍት</b>\n\n"
            f"⚽ {escape_html(score)}\n\n"
            "<b>@yegnaLiverpool</b>"
        )

    if event_type == "fulltime":

        return (
            "🏁 <b>ጨዋታው ተጠናቋል</b>\n\n"
            f"⚽ {escape_html(score)}\n\n"
            "<b>@yegnaLiverpool</b>"
        )

    return (
        "⚽ <b>LIVE</b>\n\n"
        f"⚽ {escape_html(score)}\n\n"
        f"{escape_html(extra_text)}\n\n"
        "<b>@yegnaLiverpool</b>"
    )


# =========================================================
# LIVE MATCH
# =========================================================

def process_live_match():

    logger.info(
        "Checking Liverpool LIVE match..."
    )

    data = get_liverpool_scoreboard()

    if not data:
        return

    event = find_liverpool_match(
        data
    )

    if not event:

        logger.info(
            "No Liverpool match found."
        )

        return

    event_id = str(
        event.get(
            "id",
            ""
        )
    )

    state, name, detail = get_match_status(
        event
    )

    home, away = get_match_teams(
        event
    )

    logger.info(
        "Liverpool match: %s | state=%s",
        match_score_text(
            home,
            away
        ),
        state
    )

    # -----------------------------------------------------
    # PRE-MATCH
    # -----------------------------------------------------

    if state == "pre":

        key = (
            f"{event_id}|pre"
        )

        if not live_event_already_posted(
            key
        ):

            message = build_live_message(
                event,
                "start",
                "የLiverpool ጨዋታ ሊጀምር ነው።"
            )

            if telegram_send_message(
                message
            ):

                save_live_event(
                    key,
                    "pre",
                    message
                )

        return

    # -----------------------------------------------------
    # LIVE
    # -----------------------------------------------------

    if state == "in":

        # Events
        for item in extract_live_events(
            event
        ):

            event_key = (
                f"{event_id}|"
                f"{item['type']}|"
                f"{item.get('player','')}|"
                f"{item.get('minute','')}"
            )

            if live_event_already_posted(
                event_key
            ):
                continue

            player = item.get(
                "player",
                ""
            )

            minute = item.get(
                "minute",
                ""
            )

            if item["type"] == "goal":

                extra = (
                    f"⚽ {player} "
                    f"በ{minute}' ጎል አስቆጠረ።"
                )

            elif item["type"] == "yellow":

                extra = (
                    f"🟨 {player} "
                    f"በ{minute}' ቢጫ ካርድ ተመልክቷል።"
                )

            elif item["type"] == "red":

                extra = (
                    f"🟥 {player} "
                    f"በ{minute}' ቀይ ካርድ ተመልክቷል።"
                )

            else:

                extra = (
                    f"🔄 {player} "
                    f"በ{minute}' ቅያሬ ተደርጓል።"
                )

            message = build_live_message(
                event,
                item["type"],
                extra
            )

            if telegram_send_message(
                message
            ):

                save_live_event(
                    event_key,
                    item["type"],
                    message
                )

        # -------------------------------------------------
        # SCORE/TIME UPDATE
        # -------------------------------------------------

        status = event.get(
            "status",
            {}
        )

        clock = status.get(
            "displayClock",
            ""
        )

        if clock:

            key = (
                f"{event_id}|clock|{clock}"
            )

            if not live_event_already_posted(
                key
            ):

                message = build_live_message(
                    event,
                    "live",
                    f"⏱️ የጨዋታ ሰዓት፦ {clock}"
                )

                if telegram_send_message(
                    message
                ):

                    save_live_event(
                        key,
                        "clock",
                        message
                    )

        return

    # -----------------------------------------------------
    # FULL TIME
    # -----------------------------------------------------

    if state == "post":

        key = (
            f"{event_id}|fulltime"
        )

        if not live_event_already_posted(
            key
        ):

            message = build_live_message(
                event,
                "fulltime"
            )

            if telegram_send_message(
                message
            ):

                save_live_event(
                    key,
                    "fulltime",
                    message
                )


# =========================================================
# MAIN
# =========================================================

def main():

    logger.info(
        "===================================="
    )

    logger.info(
        "Liverpool News Bot starting..."
    )

    logger.info(
        "Channel: %s",
        CHANNEL_ID
    )

    logger.info(
        "Mode: DIRECT TELEGRAM API"
    )

    # -----------------------------------------------------
    # TELEGRAM
    # -----------------------------------------------------

    if not telegram_check_bot():

        logger.error(
            "Telegram bot connection failed."
        )

        return

    # -----------------------------------------------------
    # NEWS
    # -----------------------------------------------------

    try:

        check_news()

    except Exception as e:

        logger.exception(
            "News check failed: %s",
            e
        )

    # -----------------------------------------------------
    # LIVE
    # -----------------------------------------------------

    try:

        process_live_match()

    except Exception as e:

        logger.exception(
            "LIVE check failed: %s",
            e
        )

    logger.info(
        "Bot check finished."
    )

    logger.info(
        "===================================="
    )


# =========================================================
# START
# =========================================================

if __name__ == "__main__":
    main()
