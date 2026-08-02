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

# ---------------------------------------------------------
# IMPORTANT:
# 5 minutes between news posts
# ---------------------------------------------------------

CHECK_INTERVAL_MINUTES = int(
    os.getenv("CHECK_INTERVAL_MINUTES", "5")
)

# 5 minutes between individual news posts
NEWS_POST_DELAY_SECONDS = (
    CHECK_INTERVAL_MINUTES * 60
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

    smaller = min(
        len(a),
        len(b)
    )

    if smaller == 0:
        return False

    return (
        intersection / smaller
    ) >= 0.72


# =========================================================
# DATABASE NEWS
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

        if titles_are_similar(
            title,
            row[0]
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

        if getattr(
            entry,
            "published_parsed",
            None
        ):

            return datetime.fromtimestamp(
                time.mktime(
                    entry.published_parsed
                ),
                tz=timezone.utc
            )

        if getattr(
            entry,
            "updated_parsed",
            None
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

    published = parse_entry_time(
        entry
    )

    if not published:
        return True

    now = datetime.now(
        timezone.utc
    )

    age = (
        now - published
    ).total_seconds()

    return (
        age >= 0
        and age <= (
            MAX_NEWS_AGE_HOURS * 3600
        )
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

        return (
            response.url
            or url
        )

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

def domain_matches(
    domain,
    allowed_domains
):

    domain = (
        domain
        .lower()
        .replace("www.", "")
    )

    for allowed in allowed_domains:

        allowed = (
            allowed
            .lower()
            .replace("www.", "")
        )

        if (
            domain == allowed
            or domain.endswith(
                "." + allowed
            )
        ):
            return True

    return False


def source_domain_allowed(
    source_name,
    url,
    rss_source_domain=""
):

    allowed = TRUSTED_SOURCES.get(
        source_name,
        []
    )

    # -----------------------------------------------------
    # First: RSS source domain
    # -----------------------------------------------------

    if rss_source_domain:

        if domain_matches(
            rss_source_domain,
            allowed
        ):
            return True

    # -----------------------------------------------------
    # Second: original URL
    # -----------------------------------------------------

    original_domain = get_domain(
        url
    )

    if domain_matches(
        original_domain,
        allowed
    ):
        return True

    # -----------------------------------------------------
    # Third: resolved URL
    # -----------------------------------------------------

    resolved = resolve_article_url(
        url
    )

    resolved_domain = get_domain(
        resolved
    )

    if domain_matches(
        resolved_domain,
        allowed
    ):
        return True

    return False
    # =========================================================
# IMAGE
# =========================================================

def is_valid_image_url(url):

    if not url:
        return False

    return str(url).strip().startswith(
        (
            "http://",
            "https://"
        )
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

            url = (
                enclosure.get(
                    "href",
                    ""
                )
                or enclosure.get(
                    "url",
                    ""
                )
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
                image.get(
                    "src",
                    ""
                )
                or image.get(
                    "data-src",
                    ""
                )
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
            {
                "property": "og:image"
            },
            {
                "name": "twitter:image"
            },
            {
                "name": "twitter:image:src"
            }
        ]

        for attrs in selectors:

            image = soup.find(
                "meta",
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

    image = get_feed_image(
        entry
    )

    if image:
        return image

    return get_page_image(
        url
    )


# =========================================================
# DOWNLOAD IMAGE
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

        if len(content) > (
            10 * 1024 * 1024
        ):
            return None, None

        content_type = (
            response.headers
            .get(
                "Content-Type",
                ""
            )
            .lower()
        )

        valid_signature = (
            content.startswith(
                b"\xff\xd8\xff"
            )
            or content.startswith(
                b"\x89PNG"
            )
            or content.startswith(
                b"RIFF"
            )
            or content.startswith(
                b"GIF8"
            )
        )

        valid_types = (
            "image/jpeg",
            "image/jpg",
            "image/png",
            "image/webp",
            "image/gif"
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

            filename = (
                "liverpool_news.png"
            )

        elif "webp" in content_type:

            filename = (
                "liverpool_news.webp"
            )

        elif "gif" in content_type:

            filename = (
                "liverpool_news.gif"
            )

        else:

            filename = (
                "liverpool_news.jpg"
            )

        return content, filename

    except Exception as e:

        logger.warning(
            "Image download failed: %s",
            e
        )

        return None, None


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

        logger.info(
            "Searching: %s",
            source_name
        )

        feed = get_google_news(
            query
        )

        if not feed:

            logger.warning(
                "No feed: %s",
                source_name
            )

            continue

        for entry in feed.entries[:10]:

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
            # RECENT
            # -------------------------------------------------

            if not is_recent(entry):

                logger.info(
                    "Old article skipped: %s",
                    title
                )

                continue

            # -------------------------------------------------
            # RSS SOURCE
            # -------------------------------------------------

            rss_source_domain = ""

            try:

                rss_source = getattr(
                    entry,
                    "source",
                    None
                )

                if rss_source:

                    rss_source_domain = (
                        getattr(
                            rss_source,
                            "href",
                            ""
                        )
                    )

                    if rss_source_domain:

                        rss_source_domain = (
                            get_domain(
                                rss_source_domain
                            )
                        )

            except Exception:
                pass

            # -------------------------------------------------
            # SOURCE CHECK
            # -------------------------------------------------

            if not source_domain_allowed(
                source_name,
                url,
                rss_source_domain
            ):

                logger.info(
                    "Rejected wrong source: %s | %s | rss=%s",
                    source_name,
                    url,
                    rss_source_domain
                )

                continue

            # -------------------------------------------------
            # LIVERPOOL CHECK
            # -------------------------------------------------

            if not appears_liverpool_related(
                title,
                summary
            ):

                logger.info(
                    "Non-Liverpool skipped: %s",
                    title
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
                "Trusted article accepted: %s",
                title
            )

    logger.info(
        "Collected %s trusted articles.",
        len(articles)
    )

    return articles


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

    prompt = f"""
TRUSTED SOURCE:
{article.get("source", "")}

TITLE:
{article.get("title", "")}

ARTICLE SUMMARY:
{article.get("summary", "")}

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
# IMAGE
# =========================================================

def is_valid_image_url(url):

    if not url:
        return False

    return str(url).strip().startswith(
        (
            "http://",
            "https://"
        )
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

            url = (
                enclosure.get(
                    "href",
                    ""
                )
                or enclosure.get(
                    "url",
                    ""
                )
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
                image.get(
                    "src",
                    ""
                )
                or image.get(
                    "data-src",
                    ""
                )
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
            {
                "property": "og:image"
            },
            {
                "name": "twitter:image"
            },
            {
                "name": "twitter:image:src"
            }
        ]

        for attrs in selectors:

            image = soup.find(
                "meta",
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

    image = get_feed_image(
        entry
    )

    if image:
        return image

    return get_page_image(
        url
    )


# =========================================================
# DOWNLOAD IMAGE
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

        if len(content) > (
            10 * 1024 * 1024
        ):
            return None, None

        content_type = (
            response.headers
            .get(
                "Content-Type",
                ""
            )
            .lower()
        )

        valid_signature = (
            content.startswith(
                b"\xff\xd8\xff"
            )
            or content.startswith(
                b"\x89PNG"
            )
            or content.startswith(
                b"RIFF"
            )
            or content.startswith(
                b"GIF8"
            )
        )

        valid_types = (
            "image/jpeg",
            "image/jpg",
            "image/png",
            "image/webp",
            "image/gif"
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

            filename = (
                "liverpool_news.png"
            )

        elif "webp" in content_type:

            filename = (
                "liverpool_news.webp"
            )

        elif "gif" in content_type:

            filename = (
                "liverpool_news.gif"
            )

        else:

            filename = (
                "liverpool_news.jpg"
            )

        return content, filename

    except Exception as e:

        logger.warning(
            "Image download failed: %s",
            e
        )

        return None, None


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

        logger.info(
            "Searching: %s",
            source_name
        )

        feed = get_google_news(
            query
        )

        if not feed:

            logger.warning(
                "No feed: %s",
                source_name
            )

            continue

        for entry in feed.entries[:10]:

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
            # RECENT
            # -------------------------------------------------

            if not is_recent(entry):

                logger.info(
                    "Old article skipped: %s",
                    title
                )

                continue

            # -------------------------------------------------
            # RSS SOURCE
            # -------------------------------------------------

            rss_source_domain = ""

            try:

                rss_source = getattr(
                    entry,
                    "source",
                    None
                )

                if rss_source:

                    rss_source_domain = (
                        getattr(
                            rss_source,
                            "href",
                            ""
                        )
                    )

                    if rss_source_domain:

                        rss_source_domain = (
                            get_domain(
                                rss_source_domain
                            )
                        )

            except Exception:
                pass

            # -------------------------------------------------
            # SOURCE CHECK
            # -------------------------------------------------

            if not source_domain_allowed(
                source_name,
                url,
                rss_source_domain
            ):

                logger.info(
                    "Rejected wrong source: %s | %s | rss=%s",
                    source_name,
                    url,
                    rss_source_domain
                )

                continue

            # -------------------------------------------------
            # LIVERPOOL CHECK
            # -------------------------------------------------

            if not appears_liverpool_related(
                title,
                summary
            ):

                logger.info(
                    "Non-Liverpool skipped: %s",
                    title
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
                "Trusted article accepted: %s",
                title
            )

    logger.info(
        "Collected %s trusted articles.",
        len(articles)
    )

    return articles


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

    prompt = f"""
TRUSTED SOURCE:
{article.get("source", "")}

TITLE:
{article.get("title", "")}

ARTICLE SUMMARY:
{article.get("summary", "")}

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
# TELEGRAM API
# =========================================================

def telegram_api(
    method,
    payload=None
):

    url = (
        f"https://api.telegram.org/bot"
        f"{BOT_TOKEN}/{method}"
    )

    try:

        response = requests.post(
            url,
            json=payload or {},
            timeout=30
        )

        logger.info(
            "Telegram %s HTTP %s",
            method,
            response.status_code
        )

        try:
            data = response.json()

        except Exception:

            logger.error(
                "Telegram returned non-JSON: %s",
                response.text[:500]
            )

            return None

        if not data.get("ok"):

            logger.error(
                "TELEGRAM API ERROR | method=%s | %s",
                method,
                json.dumps(
                    data,
                    ensure_ascii=False
                )
            )

        return data

    except Exception as e:

        logger.exception(
            "Telegram request failed: %s",
            e
        )

        return None


# =========================================================
# TELEGRAM CONNECTION + CHANNEL TEST
# =========================================================

def telegram_check_bot():

    data = telegram_api(
        "getMe"
    )

    if not data or not data.get("ok"):

        logger.error(
            "BOT TOKEN IS NOT WORKING."
        )

        return False

    bot_info = data.get(
        "result",
        {}
    )

    logger.info(
        "Telegram bot connected: @%s",
        bot_info.get(
            "username",
            "unknown"
        )
    )

    # -----------------------------------------------------
    # Check channel access
    # -----------------------------------------------------

    chat_data = telegram_api(
        "getChat",
        {
            "chat_id": CHANNEL_ID
        }
    )

    if not chat_data or not chat_data.get(
        "ok"
    ):

        logger.error(
            "BOT CANNOT ACCESS CHANNEL: %s",
            CHANNEL_ID
        )

        return False

    chat = chat_data.get(
        "result",
        {}
    )

    logger.info(
        "Channel found: %s | type=%s",
        chat.get(
            "title",
            CHANNEL_ID
        ),
        chat.get(
            "type",
            ""
        )
    )

    # -----------------------------------------------------
    # Check bot membership/admin
    # -----------------------------------------------------

    bot_id = bot_info.get(
        "id"
    )

    if bot_id:

        member_data = telegram_api(
            "getChatMember",
            {
                "chat_id": CHANNEL_ID,
                "user_id": bot_id
            }
        )

        if member_data and member_data.get(
            "ok"
        ):

            member = member_data.get(
                "result",
                {}
            )

            status = member.get(
                "status",
                ""
            )

            logger.info(
                "Bot channel status: %s",
                status
            )

            if status not in (
                "administrator",
                "creator"
            ):

                logger.error(
                    "BOT IS NOT ADMIN IN CHANNEL."
                )

                return False

        else:

            logger.error(
                "Could not verify bot channel membership."
            )

            return False

    return True


# =========================================================
# TELEGRAM TEXT
# =========================================================

def telegram_send_message(text):

    data = telegram_api(
        "sendMessage",
        {
            "chat_id": CHANNEL_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False
        }
    )

    return (
        data is not None
        and data.get("ok") is True
    )


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

        logger.warning(
            "Could not download image."
        )

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
            "Telegram sendPhoto HTTP %s",
            response.status_code
        )

        result = response.json()

        if not result.get("ok"):

            logger.error(
                "TELEGRAM PHOTO ERROR: %s",
                json.dumps(
                    result,
                    ensure_ascii=False
                )
            )

        return result.get(
            "ok"
        ) is True

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
    # Liverpool
    # -----------------------------------------------------

    if not appears_liverpool_related(
        title,
        summary
    ):

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
            "Exact duplicate skipped: %s",
            title
        )

        return False

    if similar_news_already_posted(
        title
    ):

        logger.info(
            "Similar duplicate skipped: %s",
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
    # IMAGE
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

    logger.error(
        "FAILED TO POST ARTICLE: %s",
        title
    )

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

        logger.warning(
            "NO TRUSTED ARTICLES FOUND."
        )

        return

    posted_count = 0

    for article in articles:

        if posted_count >= 3:
            break

        try:

            if process_article(
                article
            ):

                posted_count += 1

                # -------------------------------------------------
                # IMPORTANT:
                # Wait 5 minutes before posting the NEXT news.
                # -------------------------------------------------

                if posted_count < 3:

                    logger.info(
                        "News posted. Waiting %s minutes before next news...",
                        CHECK_INTERVAL_MINUTES
                    )

                    time.sleep(
                        NEWS_POST_DELAY_SECONDS
                    )

            else:

                # Rejected/duplicate article:
                # Do NOT wait 5 minutes because nothing was posted.
                logger.info(
                    "Article was not posted. Checking next candidate."
                )

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
