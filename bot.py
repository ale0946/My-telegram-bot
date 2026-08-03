import os
import re
import time
import json
import hashlib
import logging
import sqlite3
from datetime import datetime, timezone, timedelta
from urllib.parse import quote_plus, urljoin, urlparse

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

CHANNEL = "@yegnaLiverpool"

GROQ_MODEL = os.getenv(
    "GROQ_MODEL",
    "openai/gpt-oss-120b"
).strip()

CHECK_EVERY = 5 * 60
MIN_POST_GAP = 5 * 60

# Maximum age of an article
MAX_ARTICLE_AGE_HOURS = 24

DB_FILE = "liverpool_news.db"

# Set false if you do not want startup test messages
SEND_STARTUP_TEST = os.getenv(
    "SEND_STARTUP_TEST",
    "true"
).lower() == "true"

USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 10; K) "
    "AppleWebKit/537.36 "
    "(KHTML, like Gecko) "
    "Chrome/150.0 Mobile Safari/537.36"
)

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,image/avif,"
        "image/webp,*/*;q=0.8"
    ),
}


# =========================================================
# VALIDATION
# =========================================================

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN is missing from GitHub Secrets."
    )

if not GROQ_API_KEY:
    raise RuntimeError(
        "GROQ_API_KEY is missing from GitHub Secrets."
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

logger = logging.getLogger(
    "LiverpoolNewsBot"
)


# =========================================================
# TRUSTED SOURCES
# =========================================================

TRUSTED_DOMAINS = {
    "liverpoolfc.com": "Liverpool FC",
    "theathletic.com": "The Athletic",
    "thetimes.com": "The Times",
    "x.com": "Fabrizio Romano",
    "twitter.com": "Fabrizio Romano",
    "fabricioromano.com": "Fabrizio Romano",
}


# =========================================================
# DATABASE
# =========================================================

def get_db():

    conn = sqlite3.connect(
        DB_FILE,
        timeout=30
    )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS posted_news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fingerprint TEXT UNIQUE,
            title TEXT NOT NULL,
            url TEXT,
            source TEXT,
            image_hash TEXT,
            posted_at INTEGER NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS used_images (
            image_hash TEXT PRIMARY KEY,
            image_url TEXT,
            used_at INTEGER NOT NULL
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


def title_similarity(a, b):

    a_words = set(
        normalize(a).split()
    )

    b_words = set(
        normalize(b).split()
    )

    if not a_words or not b_words:
        return 0.0

    return (
        len(
            a_words.intersection(b_words)
        )
        /
        len(
            a_words.union(b_words)
        )
    )


# =========================================================
# LIVERPOOL FILTER
# =========================================================

LIVERPOOL_KEYWORDS = [
    "liverpool",
    "liverpool fc",
    "lfc",
    "anfield",

    "arne slot",
    "slot",

    "salah",
    "mohamed salah",

    "gakpo",
    "cody gakpo",

    "diaz",
    "luis diaz",

    "nunez",
    "darwin nunez",

    "szoboszlai",
    "mac allister",
    "gravenberch",
    "wirtz",
    "frimpong",

    "van dijk",
    "konate",
    "alisson",
    "robertson",

    "alexander-arnold",
    "alexander arnold",

    "bradley",
    "elliott",
    "jones",
    "chiesa",
    "endo",

    "iraola",

    # Additional Liverpool-related terms
    "merseyside",
    "reds"
]


def is_liverpool_related(text):

    text = clean_text(
        text
    ).lower()

    for keyword in LIVERPOOL_KEYWORDS:

        if keyword in text:
            return True

    return False


# =========================================================
# DOMAIN
# =========================================================

def get_domain(url):

    try:

        host = urlparse(
            url
        ).netloc.lower()

        return host.replace(
            "www.",
            ""
        )

    except Exception:

        return ""


def trusted_source(url):

    domain = get_domain(
        url
    )

    if not domain:
        return None

    for trusted_domain, name in TRUSTED_DOMAINS.items():

        if (
            domain == trusted_domain
            or domain.endswith(
                "." + trusted_domain
            )
        ):
            return name

    return None


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

        except Exception:

            result = {
                "ok": False,
                "description": response.text
            }

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
            "ok": False,
            "description": str(e)
        }


def telegram_get_me():

    return telegram_api(
        "getMe"
    )


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
                "liverpool_news.jpg",
                image_bytes,
                "image/jpeg"
            )
        }
    )

    return result.get(
        "ok",
        False
    )


def telegram_startup_test():

    message = (
        "🤖 Liverpool ዜና Bot ተነስቷል 🚀\n\n"
        "Bot-ው ከ Telegram ጋር ተገናኝቷል።\n"
        "ዋናው ቻናል: @yegnaLiverpool"
    )

    return telegram_send_message(
        message
    )


# =========================================================
# RESOLVE URL
# =========================================================

def resolve_url(url):

    try:

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=25,
            allow_redirects=True
        )

        final_url = response.url

        logger.info(
            "URL: %s -> %s",
            url,
            final_url
        )

        return final_url

    except Exception as e:

        logger.warning(
            "URL resolution failed: %s",
            e
        )

        return url


# =========================================================
# GOOGLE NEWS
# =========================================================

def get_google_news():

    queries = [
        "Liverpool FC",
        "Liverpool transfer",
        "Liverpool signing",
        "Liverpool contract",
        "Liverpool injury",
        "Liverpool manager",
        "Liverpool player",
    ]

    results = []

    for query in queries:

        logger.info(
            "RSS SEARCH: %s",
            query
        )

        rss_url = (
            "https://news.google.com/rss/search?"
            f"q={quote_plus(query)}"
            "&hl=en-US"
            "&gl=GB"
            "&ceid=GB:en"
        )

        try:

            response = requests.get(
                rss_url,
                headers=HEADERS,
                timeout=25
            )

            response.raise_for_status()

            feed = feedparser.parse(
                response.content
            )

            logger.info(
                "RSS RESULT [%s]: %s entries",
                query,
                len(feed.entries)
            )

            for entry in feed.entries:

                title = clean_text(
                    getattr(
                        entry,
                        "title",
                        ""
                    )
                )

                link = getattr(
                    entry,
                    "link",
                    ""
                )

                if not title or not link:
                    continue

                source_title = ""

                source_obj = getattr(
                    entry,
                    "source",
                    None
                )

                if source_obj:

                    source_title = clean_text(
                        getattr(
                            source_obj,
                            "title",
                            ""
                        )
                    )

                published = getattr(
                    entry,
                    "published_parsed",
                    None
                )

                # Google News summary
                summary = clean_text(
                    getattr(
                        entry,
                        "summary",
                        ""
                    )
                )

                results.append({
                    "title": title,
                    "url": link,
                    "source_title": source_title,
                    "summary": summary,
                    "published": published
                })

        except Exception as e:

            logger.warning(
                "Google News error [%s]: %s",
                query,
                e
            )

    unique = {}

    for item in results:

        key = (
            normalize(
                item["title"]
            )
            + "|"
            + item["url"]
        )

        unique[key] = item

    logger.info(
        "TOTAL UNIQUE RSS CANDIDATES: %s",
        len(unique)
    )

    return list(
        unique.values()
    )


# =========================================================
# META
# =========================================================

def extract_meta(
    soup,
    property_name=None,
    name=None
):

    tag = None

    if property_name:

        tag = soup.find(
            "meta",
            attrs={
                "property": property_name
            }
        )

    if not tag and name:

        tag = soup.find(
            "meta",
            attrs={
                "name": name
            }
        )

    if tag:

        return clean_text(
            tag.get(
                "content",
                ""
            )
        )

    return ""


# =========================================================
# ARTICLE IMAGE
# =========================================================

def extract_article_image(
    soup,
    page_url
):

    candidates = []

    for prop in [
        "og:image",
        "og:image:url"
    ]:

        value = extract_meta(
            soup,
            property_name=prop
        )

        if value:
            candidates.append(
                value
            )

    twitter_image = extract_meta(
        soup,
        name="twitter:image"
    )

    if twitter_image:

        candidates.append(
            twitter_image
        )

    article = soup.find(
        "article"
    )

    if article:

        for img in article.find_all(
            "img"
        ):

            src = (
                img.get("src")
                or img.get("data-src")
                or img.get("data-lazy-src")
                or img.get("data-original")
            )

            if src:
                candidates.append(
                    src
                )

    for img in soup.find_all(
        "img"
    ):

        src = (
            img.get("src")
            or img.get("data-src")
            or img.get("data-lazy-src")
        )

        if src:
            candidates.append(
                src
            )

    for image in candidates:

        image = image.strip()

        if not image:
            continue

        if image.startswith(
            "data:"
        ):
            continue

        image = urljoin(
            page_url,
            image
        )

        if image.startswith(
            "http://"
        ) or image.startswith(
            "https://"
        ):

            return image

    return None


# =========================================================
# FETCH ARTICLE
# =========================================================

def fetch_article(
    url,
    rss_summary=""
):

    try:

        logger.info(
            "FETCH ARTICLE: %s",
            url
        )

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=30,
            allow_redirects=True
        )

        logger.info(
            "ARTICLE HTTP STATUS: %s",
            response.status_code
        )

        if response.status_code != 200:

            logger.warning(
                "Article HTTP %s: %s",
                response.status_code,
                url
            )

            # Use RSS summary if available
            if len(rss_summary) >= 100:

                return {
                    "title": "",
                    "body": rss_summary,
                    "image_url": None,
                    "url": url
                }

            return None

        final_url = response.url

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        meta_title = extract_meta(
            soup,
            property_name="og:title"
        )

        meta_description = extract_meta(
            soup,
            property_name="og:description"
        )

        image_url = extract_article_image(
            soup,
            final_url
        )

        for tag in soup.find_all([
            "script",
            "style",
            "noscript",
            "svg",
            "iframe",
            "nav",
            "footer",
            "header",
            "form"
        ]):

            tag.decompose()

        title = meta_title

        if not title and soup.title:

            title = soup.title.get_text(
                " ",
                strip=True
            )

        article = soup.find(
            "article"
        )

        if article:

            paragraphs = [
                p.get_text(
                    " ",
                    strip=True
                )
                for p in article.find_all(
                    "p"
                )
            ]

        else:

            paragraphs = [
                p.get_text(
                    " ",
                    strip=True
                )
                for p in soup.find_all(
                    "p"
                )
            ]

        paragraphs = [
            clean_text(p)
            for p in paragraphs
            if len(
                clean_text(p)
            ) >= 25
        ]

        body = "\n".join(
            paragraphs
        )

        body = clean_text(
            body
        )

        # -------------------------------------------------
        # IMPORTANT FALLBACK
        # -------------------------------------------------

        if len(body) < 500:

            if len(rss_summary) >= 100:

                logger.info(
                    "ARTICLE BODY SHORT -> USING RSS SUMMARY"
                )

                body = clean_text(
                    rss_summary
                )

            elif meta_description:

                logger.info(
                    "ARTICLE BODY SHORT -> USING META DESCRIPTION"
                )

                body = clean_text(
                    meta_description
                )

        if len(body) > 14000:

            body = body[:14000]

        logger.info(
            "ARTICLE BODY LENGTH: %s",
            len(body)
        )

        return {
            "title": clean_text(
                title
            ),
            "body": body,
            "image_url": image_url,
            "url": final_url
        }

    except Exception as e:

        logger.warning(
            "Article fetch error: %s",
            e
        )

        if len(rss_summary) >= 100:

            logger.info(
                "FETCH FAILED -> USING RSS SUMMARY"
            )

            return {
                "title": "",
                "body": clean_text(
                    rss_summary
                ),
                "image_url": None,
                "url": url
            }

        return None


# =========================================================
# IMAGE DOWNLOAD
# =========================================================

def download_image(
    image_url
):

    if not image_url:
        return None

    try:

        response = requests.get(
            image_url,
            headers={
                **HEADERS,
                "Accept": (
                    "image/avif,image/webp,"
                    "image/apng,image/svg+xml,"
                    "image/*,*/*;q=0.8"
                )
            },
            timeout=25
        )

        if response.status_code != 200:
            return None

        content_type = response.headers.get(
            "Content-Type",
            ""
        ).lower()

        data = response.content

        if not data:
            return None

        if len(data) < 10_000:
            return None

        if len(data) > 15 * 1024 * 1024:
            return None

        if (
            content_type
            and not content_type.startswith(
                "image/"
            )
        ):
            return None

        image_hash = hashlib.sha256(
            data
        ).hexdigest()

        return {
            "bytes": data,
            "hash": image_hash,
            "url": image_url,
            "content_type": content_type
        }

    except Exception as e:

        logger.warning(
            "Image download error: %s",
            e
        )

        return None


# =========================================================
# IMAGE DATABASE
# =========================================================

def image_was_used(
    image_hash
):

    conn = get_db()

    row = conn.execute(
        """
        SELECT image_hash
        FROM used_images
        WHERE image_hash = ?
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

    conn = get_db()

    conn.execute(
        """
        INSERT OR IGNORE INTO used_images
        (
            image_hash,
            image_url,
            used_at
        )
        VALUES (?, ?, ?)
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

def news_was_posted(
    title,
    url
):

    fp = make_hash(
        normalize(title)
        + "|"
        + normalize(url)
    )

    conn = get_db()

    row = conn.execute(
        """
        SELECT id
        FROM posted_news
        WHERE fingerprint = ?
        LIMIT 1
        """,
        (
            fp,
        )
    ).fetchone()

    if row:

        conn.close()

        logger.info(
            "DUPLICATE: exact fingerprint"
        )

        return True

    recent = conn.execute(
        """
        SELECT title
        FROM posted_news
        ORDER BY posted_at DESC
        LIMIT 150
        """
    ).fetchall()

    conn.close()

    for old in recent:

        similarity = title_similarity(
            title,
            old[0]
        )

        if similarity >= 0.70:

            logger.info(
                "DUPLICATE: title similarity %.2f",
                similarity
            )

            return True

    return False


def save_post(
    title,
    url,
    source,
    image_hash=""
):

    fp = make_hash(
        normalize(title)
        + "|"
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
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            fp,
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

    if elapsed < MIN_POST_GAP:

        logger.info(
            "POST GAP: only %.0f seconds elapsed",
            elapsed
        )

        return False

    return True


# =========================================================
# ARTICLE AGE
# =========================================================

def is_recent(entry):

    published = entry.get(
        "published"
    )

    if not published:

        logger.info(
            "AGE: no publication date -> allowing"
        )

        return True

    try:

        timestamp = time.mktime(
            published
        )

        published_dt = datetime.fromtimestamp(
            timestamp,
            tz=timezone.utc
        )

        age = (
            datetime.now(timezone.utc)
            - published_dt
        )

        hours = age.total_seconds() / 3600

        logger.info(
            "ARTICLE AGE: %.1f hours",
            hours
        )

        return age <= timedelta(
            hours=MAX_ARTICLE_AGE_HOURS
        )

    except Exception as e:

        logger.warning(
            "Age check failed: %s",
            e
        )

        return True


# =========================================================
# GROQ EDITOR
# =========================================================

NEWS_EDITOR_PROMPT = """
አንተ ለLiverpool FC የአማርኛ Telegram ዜና
አርታኢ ነህ።

የተሰጠህን article በarticle ውስጥ ባለው
መረጃ ላይ ብቻ ተመስርተህ በተፈጥሯዊና
በሙያዊ አማርኛ አዘጋጅ።

ጥብቅ ደንቦች:

1. በarticle ውስጥ የሌለ እውነታ አትጨምር።
2. Quote አትፍጠር።
3. Transfer fee አትፍጠር።
4. Contract length አትፍጠር።
5. Injury አትፍጠር።
6. Date አትፍጠር።
7. Source አትፍጠር።
8. could/may/reportedly/according to የሚሉ
   ቃላት ያሳዩትን እርግጠኛ እውነታ
   እንደሆነ አትቀይር።
9. ዜናው Liverpool FC ጋር በግልጽ
   መያያዝ አለበት።
10. English headline አትጻፍ።
11. English paragraph አትጻፍ።
12. Machine translation አትስራ።
13. የአማርኛ የስፖርት ዘገባ ቋንቋ ተጠቀም።
14. የተጫዋቾችን ስም በትክክል ጠብቅ።
15. Headline አጭርና ግልጽ ይሁን።
16. Body 2-4 አጭር አንቀጾች ይሁን።
17. Hashtag አትጨምር።
18. Emoji አትጨምር።
19. Markdown አትጠቀም።
20. @yegnaLiverpool አትጨምር።
21. በቂ መረጃ ከሌለ REJECT አድርግ።
22. Liverpool FC ጋር በግልጽ ካልተያያዘ REJECT አድርግ።
23. የarticle መረጃ እርግጠኛ ካልሆነ
    ያልተረጋገጠውን እንደተረጋገጠ
    አታቅርብ።

JSON ብቻ መልስ:

{
  "decision": "POST" or "REJECT",
  "headline": "...",
  "body": "...",
  "confidence": 0-100,
  "reason": "..."
}
"""


def ai_edit_news(
    title,
    body,
    source,
    url
):

    logger.info(
        "AI CHECK START: %s",
        title
    )

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
            temperature=0.15,
            max_tokens=1600,
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

        raw = (
            completion
            .choices[0]
            .message
            .content
        )

        result = json.loads(
            raw
        )

        logger.info(
            "AI DECISION: %s | confidence=%s | reason=%s",
            result.get("decision"),
            result.get("confidence"),
            result.get("reason", "")
        )

        return result

    except Exception as e:

        logger.error(
            "Groq editor error: %s",
            e
        )

        return None


# =========================================================
# AMHARIC CHECK
# =========================================================

def amharic_ratio(text):

    if not text:
        return 0

    chars = re.findall(
        r"[\u1200-\u137F]",
        text
    )

    letters = re.findall(
        r"[A-Za-z\u1200-\u137F]",
        text
    )

    if not letters:
        return 0

    return len(chars) / len(letters)


def english_sentence_detected(text):

    english_words = re.findall(
        r"\b(the|this|that|with|from|will|has|have|"
        r"are|was|were|according|reportedly|"
        r"could|would|should|for|and|but)\b",
        text.lower()
    )

    return len(
        english_words
    ) >= 4


def valid_amharic_output(
    headline,
    body
):

    if not headline or not body:

        logger.info(
            "AMHARIC CHECK: empty headline/body"
        )

        return False

    text = (
        headline
        + " "
        + body
    )

    ratio = amharic_ratio(
        text
    )

    logger.info(
        "AMHARIC RATIO: %.2f",
        ratio
    )

    if ratio < 0.30:

        logger.info(
            "AMHARIC REJECT: ratio too low"
        )

        return False

    if english_sentence_detected(
        text
    ):

        logger.info(
            "AMHARIC REJECT: English sentence detected"
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

    caption = (
        f"{headline}\n\n"
        f"{body}\n\n"
        f"@yegnaLiverpool"
    )

    if len(caption) > 1020:

        caption = (
            caption[:1017]
            + "..."
        )

    return caption


# =========================================================
# PROCESS NEWS
# =========================================================

def process_news(entry):

    original_title = clean_text(
        entry.get(
            "title",
            ""
        )
    )

    google_url = entry.get(
        "url",
        ""
    )

    rss_summary = clean_text(
        entry.get(
            "summary",
            ""
        )
    )

    source_title = clean_text(
        entry.get(
            "source_title",
            ""
        )
    )

    logger.info(
        "=================================================="
    )

    logger.info(
        "CHECKING NEWS: %s",
        original_title
    )

    logger.info(
        "RSS SOURCE: %s",
        source_title
    )

    if not original_title or not google_url:

        logger.info(
            "REJECT: missing title or URL"
        )

        return False

    # -----------------------------------------------------
    # 1. RECENT
    # -----------------------------------------------------

    if not is_recent(entry):

        logger.info(
            "REJECT: article too old"
        )

        return False

    # -----------------------------------------------------
    # 2. RESOLVE URL
    # -----------------------------------------------------

    real_url = resolve_url(
        google_url
    )

    logger.info(
        "FINAL URL: %s",
        real_url
    )

    # -----------------------------------------------------
    # 3. TRUSTED SOURCE
    # -----------------------------------------------------

    source = trusted_source(
        real_url
    )

    if not source:

        logger.info(
            "REJECT: UNTRUSTED SOURCE"
        )

        logger.info(
            "DOMAIN FOUND: %s",
            get_domain(real_url)
        )

        return False

    logger.info(
        "TRUSTED SOURCE: %s",
        source
    )

    # -----------------------------------------------------
    # 4. QUICK LIVERPOOL CHECK
    # -----------------------------------------------------

    if not is_liverpool_related(
        original_title
    ):

        # Do NOT immediately reject.
        # The article title may not contain Liverpool,
        # while the actual article does.

        logger.info(
            "TITLE DOES NOT CONTAIN LIVERPOOL KEYWORD"
        )

        logger.info(
            "CONTINUE: checking full article"
        )

    else:

        logger.info(
            "TITLE LIVERPOOL CHECK: PASS"
        )

    # -----------------------------------------------------
    # 5. FETCH ARTICLE
    # -----------------------------------------------------

    article = fetch_article(
        real_url,
        rss_summary
    )

    if not article:

        logger.info(
            "REJECT: could not fetch article"
        )

        return False

    title = (
        article["title"]
        or original_title
    )

    body = article["body"]

    if len(body) < 250:

        logger.info(
            "REJECT: article body too short (%s chars)",
            len(body)
        )

        return False

    # -----------------------------------------------------
    # 6. FULL LIVERPOOL CHECK
    # -----------------------------------------------------

    combined = (
        title
        + " "
        + body
    )

    if not is_liverpool_related(
        combined
    ):

        logger.info(
            "REJECT: FULL ARTICLE NOT LIVERPOOL"
        )

        return False

    logger.info(
        "LIVERPOOL RELEVANCE: PASS"
    )

    # -----------------------------------------------------
    # 7. DUPLICATE
    # -----------------------------------------------------

    if news_was_posted(
        title,
        real_url
    ):

        logger.info(
            "REJECT: DUPLICATE NEWS"
        )

        return False

    logger.info(
        "DUPLICATE CHECK: PASS"
    )

    # -----------------------------------------------------
    # 8. POST GAP
    # -----------------------------------------------------

    if not can_post():

        logger.info(
            "WAIT: minimum post gap not reached"
        )

        return False

    logger.info(
        "POST GAP CHECK: PASS"
    )

    # -----------------------------------------------------
    # 9. AI
    # -----------------------------------------------------

    edited = ai_edit_news(
        title,
        body,
        source,
        real_url
    )

    if not edited:

        logger.info(
            "REJECT: AI returned nothing"
        )

        return False

    decision = str(
        edited.get(
            "decision",
            ""
        )
    ).upper()

    reason = clean_text(
        edited.get(
            "reason",
            ""
        )
    )

    if decision != "POST":

        logger.info(
            "REJECT BY AI"
        )

        logger.info(
            "AI REASON: %s",
            reason
        )

        return False

    # -----------------------------------------------------
    # 10. CONFIDENCE
    # -----------------------------------------------------

    try:

        confidence = int(
            edited.get(
                "confidence",
                0
            )
        )

    except Exception:

        confidence = 0

    logger.info(
        "AI CONFIDENCE: %s",
        confidence
    )

    if confidence < 85:

        logger.info(
            "REJECT: confidence below 85"
        )

        return False

    # -----------------------------------------------------
    # 11. AMHARIC
    # -----------------------------------------------------

    headline = clean_text(
        edited.get(
            "headline",
            ""
        )
    )

    body_am = clean_text(
        edited.get(
            "body",
            ""
        )
    )

    if not valid_amharic_output(
        headline,
        body_am
    ):

        logger.info(
            "REJECT: invalid Amharic output"
        )

        return False

    logger.info(
        "AMHARIC CHECK: PASS"
    )

    # -----------------------------------------------------
    # 12. IMAGE
    # -----------------------------------------------------

    image = None

    image_url = article.get(
        "image_url"
    )

    if image_url:

        logger.info(
            "IMAGE FOUND: %s",
            image_url
        )

        downloaded = download_image(
            image_url
        )

        if downloaded:

            if not image_was_used(
                downloaded["hash"]
            ):

                image = downloaded

                logger.info(
                    "IMAGE CHECK: NEW IMAGE"
                )

            else:

                logger.info(
                    "IMAGE CHECK: ALREADY USED"
                )

        else:

            logger.info(
                "IMAGE DOWNLOAD FAILED"
            )

    else:

        logger.info(
            "NO ARTICLE IMAGE"
        )

    # -----------------------------------------------------
    # 13. CAPTION
    # -----------------------------------------------------

    caption = make_caption(
        headline,
        body_am
    )

    logger.info(
        "READY TO SEND TELEGRAM"
    )

    # -----------------------------------------------------
    # 14. SEND
    # -----------------------------------------------------

    if image:

        logger.info(
            "TELEGRAM: sending PHOTO"
        )

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

            logger.info(
                "TELEGRAM PHOTO: SUCCESS"
            )

        else:

            logger.warning(
                "PHOTO FAILED -> sending TEXT"
            )

            success = telegram_send_message(
                caption
            )

            image_hash = ""

    else:

        logger.info(
            "TELEGRAM: sending TEXT"
        )

        success = telegram_send_message(
            caption
        )

        image_hash = ""

    if not success:

        logger.error(
            "TELEGRAM POST FAILED"
        )

        return False

    # -----------------------------------------------------
    # 15. SAVE
    # -----------------------------------------------------

    save_post(
        headline,
        real_url,
        source,
        image_hash
    )

    logger.info(
        "SUCCESS: POSTED TO @yegnaLiverpool"
    )

    logger.info(
        "=================================================="
    )

    return True


# =========================================================
# MAIN
# =========================================================

def run_bot():

    logger.info(
        "=========================================="
    )

    logger.info(
        "Liverpool Telegram News Bot"
    )

    logger.info(
        "MAIN CHANNEL: @yegnaLiverpool"
    )

    logger.info(
        "=========================================="
    )

    # -----------------------------------------------------
    # DATABASE
    # -----------------------------------------------------

    conn = get_db()
    conn.close()

    logger.info(
        "DATABASE: READY"
    )

    # -----------------------------------------------------
    # TELEGRAM CONNECTION
    # -----------------------------------------------------

    logger.info(
        "Testing Telegram connection..."
    )

    me = telegram_get_me()

    if not me.get("ok"):

        raise RuntimeError(
            "Telegram connection failed: "
            + str(me)
        )

    bot_username = me["result"].get(
        "username",
        "unknown"
    )

    logger.info(
        "Bot connected: @%s",
        bot_username
    )

    # -----------------------------------------------------
    # CHANNEL TEST
    # -----------------------------------------------------

    if SEND_STARTUP_TEST:

        logger.info(
            "Testing @yegnaLiverpool..."
        )

        test_result = telegram_send_message(
            "🤖 Liverpool News Bot ተገናኝቷል 🚀\n\n"
            "ይህ የሙከራ መልዕክት ከ Bot-ው ወደ "
            "@yegnaLiverpool ነው።"
        )

        if test_result:

            logger.info(
                "CHANNEL TEST SUCCESS"
            )

        else:

            raise RuntimeError(
                "Bot connected to Telegram, "
                "but cannot send messages to "
                "@yegnaLiverpool. "
                "Make sure the bot is ADMIN and "
                "has Post Messages permission."
            )

    # -----------------------------------------------------
    # MAIN LOOP
    # -----------------------------------------------------

    while True:

        try:

            logger.info(
                "------------------------------------------"
            )

            logger.info(
                "SEARCHING FOR LIVERPOOL NEWS..."
            )

            candidates = get_google_news()

            logger.info(
                "CANDIDATES FOUND: %s",
                len(candidates)
            )

            candidates.sort(
                key=lambda x: (
                    x.get("published")
                    or time.gmtime(0)
                ),
                reverse=True
            )

            posted = False

            for index, entry in enumerate(
                candidates,
                start=1
            ):

                logger.info(
                    "CANDIDATE %s/%s",
                    index,
                    len(candidates)
                )

                try:

                    if process_news(
                        entry
                    ):

                        posted = True

                        logger.info(
                            "NEWS POSTED - STOPPING THIS CYCLE"
                        )

                        break

                except Exception as e:

                    logger.exception(
                        "Candidate error: %s",
                        e
                    )

            if not posted:

                logger.info(
                    "NO SUITABLE NEW LIVERPOOL NEWS"
                )

        except Exception as e:

            logger.exception(
                "MAIN LOOP ERROR: %s",
                e
            )

        logger.info(
            "Sleeping 5 minutes..."
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
            "FATAL ERROR: %s",
            e
        )

        raise
