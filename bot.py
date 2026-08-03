import os
import re
import time
import json
import hashlib
import logging
import sqlite3
from datetime import datetime, timezone, timedelta
from urllib.parse import quote_plus, urljoin

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

CHANNEL = os.getenv("CHANNEL", "@yegnaLiverpoolET").strip()

GROQ_MODEL = os.getenv(
    "GROQ_MODEL",
    "llama-3.3-70b-versatile"
).strip()

NEWS_CHECK_EVERY = 5 * 60

# At least 5 minutes between normal posts
MIN_POST_GAP = 5 * 60

# Don't process very old articles
MAX_ARTICLE_AGE_HOURS = 18

# Similarity threshold
TITLE_SIMILARITY_THRESHOLD = 0.78

# Maximum article length sent to AI
MAX_ARTICLE_CHARS = 12000

DB_FILE = "liverpool_bot.db"

USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 10) "
    "AppleWebKit/537.36 Chrome/150.0 Safari/537.36"
)

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "en-US,en;q=0.9",
}


# =========================================================
# TRUSTED SOURCES
# =========================================================

TRUSTED_SOURCES = {
    "Liverpool FC": [
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
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("LiverpoolBot")


# =========================================================
# VALIDATION
# =========================================================

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

if not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY is missing")


client = Groq(api_key=GROQ_API_KEY)


# =========================================================
# DATABASE
# =========================================================

def db():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS posted_news (
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
        CREATE TABLE IF NOT EXISTS used_images (
            image_hash TEXT PRIMARY KEY,
            url TEXT,
            used_at INTEGER
        )
    """)

    conn.commit()
    return conn


# =========================================================
# TEXT HELPERS
# =========================================================

def clean_text(text):
    if not text:
        return ""

    text = BeautifulSoup(text, "html.parser").get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize(text):
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def fingerprint(title, url=""):
    raw = normalize(title) + "|" + normalize(url)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def similarity(a, b):
    a_words = set(normalize(a).split())
    b_words = set(normalize(b).split())

    if not a_words or not b_words:
        return 0

    return len(a_words & b_words) / len(a_words | b_words)


def valid_liverpool_text(text):
    text = text.lower()

    keywords = [
        "liverpool",
        "liverpool fc",
        "reds",
        "anfield",
        "slot",
        "arne slot",
        "salah",
        "van dijk",
        "alexander-arnold",
        "alexander arnold",
        "gakpo",
        "diaz",
        "nunez",
        "szoboszlai",
        "mac allister",
        "gravenberch",
        "wirtz",
        "frimpong",
        "konate",
        "alisson",
        "elliott",
        "bradley",
        "robertson",
        "jones",
        "endo",
        "chiesa",
        "iraola",
    ]

    return any(word in text for word in keywords)


# =========================================================
# SOURCE VALIDATION
# =========================================================

def source_is_trusted(url, source_name):
    url_lower = url.lower()

    for trusted_name, domains in TRUSTED_SOURCES.items():
        for domain in domains:
            if domain in url_lower:
                return trusted_name

    source_lower = source_name.lower()

    for trusted_name in TRUSTED_SOURCES:
        if trusted_name.lower() in source_lower:
            return trusted_name

    return None


# =========================================================
# GOOGLE NEWS RSS
# =========================================================

def get_news_feed():

    queries = [
        '"Liverpool FC"',
        'Liverpool transfer',
        'Liverpool injury',
        'Liverpool Arne Slot',
        'Liverpool signing',
        'Liverpool contract',
    ]

    entries = []

    for query in queries:

        rss_url = (
            "https://news.google.com/rss/search?"
            f"q={quote_plus(query)}"
            "&hl=en-US&gl=US&ceid=US:en"
        )

        try:
            response = requests.get(
                rss_url,
                headers=HEADERS,
                timeout=20
            )

            response.raise_for_status()

            feed = feedparser.parse(response.content)

            for entry in feed.entries:

                title = clean_text(
                    getattr(entry, "title", "")
                )

                link = getattr(entry, "link", "")

                published = getattr(
                    entry,
                    "published_parsed",
                    None
                )

                if not title or not link:
                    continue

                entries.append({
                    "title": title,
                    "url": link,
                    "published": published
                })

        except Exception as e:
            logger.warning(
                "RSS error for %s: %s",
                query,
                e
            )

    return entries


# =========================================================
# ARTICLE FETCHING
# =========================================================

def fetch_article(url):

    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=25,
            allow_redirects=True
        )

        if response.status_code != 200:
            return None

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        # Remove useless elements
        for tag in soup([
            "script",
            "style",
            "noscript",
            "iframe",
            "svg",
            "nav",
            "footer",
            "header",
            "form"
        ]):
            tag.decompose()

        title = ""

        og_title = soup.find(
            "meta",
            property="og:title"
        )

        if og_title:
            title = og_title.get("content", "")

        if not title and soup.title:
            title = soup.title.get_text(" ", strip=True)

        # Try article body first
        paragraphs = []

        article = soup.find("article")

        if article:
            paragraphs = [
                p.get_text(" ", strip=True)
                for p in article.find_all("p")
            ]

        # Fallback
        if not paragraphs:
            paragraphs = [
                p.get_text(" ", strip=True)
                for p in soup.find_all("p")
            ]

        paragraphs = [
            clean_text(p)
            for p in paragraphs
            if len(clean_text(p)) > 30
        ]

        body = "\n".join(paragraphs)

        # Meta description fallback
        if len(body) < 500:
            meta = soup.find(
                "meta",
                attrs={"name": "description"}
            )

            if meta:
                body += "\n" + meta.get(
                    "content",
                    ""
                )

        # Find image
        image_url = get_article_image(
            soup,
            url
        )

        final_url = response.url

        return {
            "title": clean_text(title),
            "body": clean_text(body)[:MAX_ARTICLE_CHARS],
            "image_url": image_url,
            "final_url": final_url,
        }

    except Exception as e:
        logger.warning(
            "Article fetch failed: %s",
            e
        )
        return None


# =========================================================
# IMAGE EXTRACTION
# =========================================================

def get_article_image(soup, page_url):

    # 1. OpenGraph
    for prop in [
        "og:image",
        "og:image:url",
        "twitter:image"
    ]:

        tag = soup.find(
            "meta",
            property=prop
        )

        if not tag:
            tag = soup.find(
                "meta",
                attrs={"name": prop}
            )

        if tag:
            image = tag.get("content", "").strip()

            if image:
                return urljoin(
                    page_url,
                    image
                )

    # 2. Article image
    article = soup.find("article")

    if article:

        for img in article.find_all("img"):

            src = (
                img.get("src")
                or img.get("data-src")
                or img.get("data-lazy-src")
            )

            if src:
                return urljoin(
                    page_url,
                    src
                )

    # 3. Any reasonable image
    for img in soup.find_all("img"):

        src = (
            img.get("src")
            or img.get("data-src")
            or img.get("data-lazy-src")
        )

        if not src:
            continue

        src_lower = src.lower()

        if any(
            x in src_lower
            for x in [
                ".jpg",
                ".jpeg",
                ".png",
                ".webp"
            ]
        ):
            return urljoin(
                page_url,
                src
            )

    return None


# =========================================================
# IMAGE DOWNLOAD + VALIDATION
# =========================================================

def download_image(image_url):

    if not image_url:
        return None

    try:

        response = requests.get(
            image_url,
            headers=HEADERS,
            timeout=20,
            stream=True
        )

        if response.status_code != 200:
            return None

        content_type = response.headers.get(
            "Content-Type",
            ""
        ).lower()

        if not content_type.startswith("image/"):
            return None

        data = response.content

        if len(data) < 5000:
            return None

        if len(data) > 15 * 1024 * 1024:
            return None

        image_hash = hashlib.sha256(data).hexdigest()

        return {
            "data": data,
            "hash": image_hash,
            "url": image_url,
        }

    except Exception as e:
        logger.warning(
            "Image download failed: %s",
            e
        )
        return None


def image_already_used(image_hash):

    conn = db()

    row = conn.execute(
        """
        SELECT 1
        FROM used_images
        WHERE image_hash = ?
        LIMIT 1
        """,
        (image_hash,)
    ).fetchone()

    conn.close()

    return row is not None


def save_used_image(image_hash, url):

    conn = db()

    conn.execute(
        """
        INSERT OR IGNORE INTO used_images
        (image_hash, url, used_at)
        VALUES (?, ?, ?)
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
# DUPLICATE NEWS CHECK
# =========================================================

def news_already_posted(title, url):

    fp = fingerprint(title, url)

    conn = db()

    row = conn.execute(
        """
        SELECT title, url
        FROM posted_news
        WHERE fingerprint = ?
        LIMIT 1
        """,
        (fp,)
    ).fetchone()

    if row:
        conn.close()
        return True

    # Strong title similarity check
    rows = conn.execute(
        """
        SELECT title
        FROM posted_news
        ORDER BY posted_at DESC
        LIMIT 100
        """
    ).fetchall()

    conn.close()

    for row in rows:

        old_title = row[0]

        if similarity(
            title,
            old_title
        ) >= TITLE_SIMILARITY_THRESHOLD:

            return True

    return False


# =========================================================
# POST GAP
# =========================================================

def last_post_time():

    conn = db()

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
        return 0

    return row[0]


def can_post():

    last = last_post_time()

    if not last:
        return True

    elapsed = time.time() - last

    return elapsed >= MIN_POST_GAP


# =========================================================
# AI NEWS EDITOR
# =========================================================

AI_SYSTEM_PROMPT = """
You are the senior editor of a professional Ethiopian
Liverpool football news channel.

Your job is NOT literal translation.

You must understand the supplied article and rewrite it
into natural, professional, fluent Amharic suitable for
a serious football news Telegram channel.

ABSOLUTE RULES:

1. Use ONLY facts contained in the supplied article.
2. NEVER invent a player quote.
3. NEVER invent a transfer fee.
4. NEVER invent a contract length.
5. NEVER invent an injury.
6. NEVER invent a date.
7. NEVER invent a source.
8. NEVER claim a journalist said something unless the
   supplied article says it.
9. Never turn speculation into fact.
10. Preserve words such as reportedly, could, may,
    expected, understood, according to, etc.
11. The news MUST clearly concern Liverpool FC.
12. Do not add information from your own knowledge.
13. Do not use English headline.
14. Do not leave an English paragraph.
15. Do not produce word-for-word Amharic that sounds
    machine translated.
16. Use natural Ethiopian football terminology.
17. Keep player names and club names recognizable.
18. Make the headline short and news-like.
19. The body should normally be 2-4 concise paragraphs.
20. Do not add hashtags.
21. Do not add a source line.
22. Do not add @yegnaLiverpoolET.
23. Do not use markdown.
24. Do not use emojis.

IMPORTANT:
If the article does not contain enough reliable
information to make a Liverpool news report, REJECT it.

Return ONLY valid JSON:

{
  "decision": "POST" or "REJECT",
  "headline": "...",
  "body": "...",
  "confidence": 0-100,
  "reason": "..."
}
"""


def ai_edit_news(article):

    prompt = f"""
ARTICLE TITLE:
{article["title"]}

ARTICLE BODY:
{article["body"]}

SOURCE:
{article["source"]}

URL:
{article["url"]}
"""

    try:

        completion = client.chat.completions.create(
            model=GROQ_MODEL,
            temperature=0.15,
            max_tokens=1400,
            response_format={
                "type": "json_object"
            },
            messages=[
                {
                    "role": "system",
                    "content": AI_SYSTEM_PROMPT
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        )

        content = completion.choices[0].message.content

        result = json.loads(content)

        if not isinstance(result, dict):
            return None

        return result

    except Exception as e:
        logger.error(
            "AI error: %s",
            e
        )
        return None


# =========================================================
# AI IMAGE MATCHER
# =========================================================

IMAGE_SYSTEM_PROMPT = """
You are an image relevance checker for a Liverpool FC
football news channel.

Compare the NEWS with the IMAGE DESCRIPTION.

The image must be directly relevant to the news.

For example:
- A Cody Gakpo transfer story should use a photo of
  Cody Gakpo, Liverpool-related transfer context,
  or a clearly relevant image.
- A Virgil van Dijk story should not use Mohamed Salah.
- A Liverpool manager story should not use an unrelated
  player.
- A Tottenham transfer story about a Liverpool player
  must still focus on the correct player.

Do NOT guess.

Return ONLY JSON:

{
  "match": true or false,
  "confidence": 0-100,
  "reason": "..."
}
"""


def ai_check_image(news_title, news_body, image_url):

    # We use the image URL and page metadata as a first
    # relevance layer. Actual image recognition is kept
    # conservative.

    prompt = f"""
NEWS TITLE:
{news_title}

NEWS:
{news_body}

IMAGE URL:
{image_url}

Does this image URL clearly appear to correspond to
the news subject?

Return JSON only.
"""

    try:

        completion = client.chat.completions.create(
            model=GROQ_MODEL,
            temperature=0,
            max_tokens=300,
            response_format={
                "type": "json_object"
            },
            messages=[
                {
                    "role": "system",
                    "content": IMAGE_SYSTEM_PROMPT
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        )

        result = json.loads(
            completion.choices[0].message.content
        )

        return result

    except Exception as e:
        logger.warning(
            "Image AI check failed: %s",
            e
        )

        return {
            "match": False,
            "confidence": 0,
            "reason": "Image could not be verified."
        }


# =========================================================
# TELEGRAM
# =========================================================

def telegram_request(method, payload):

    url = (
        f"https://api.telegram.org/bot"
        f"{BOT_TOKEN}/{method}"
    )

    response = requests.post(
        url,
        data=payload,
        timeout=30
    )

    return response.json()


def send_photo(image_data, caption):

    url = (
        f"https://api.telegram.org/bot"
        f"{BOT_TOKEN}/sendPhoto"
    )

    files = {
        "photo": (
            "liverpool.jpg",
            image_data,
            "image/jpeg"
        )
    }

    data = {
        "chat_id": CHANNEL,
        "caption": caption
    }

    response = requests.post(
        url,
        data=data,
        files=files,
        timeout=40
    )

    result = response.json()

    if not result.get("ok"):
        logger.error(
            "Telegram photo error: %s",
            result
        )

    return result.get("ok", False)


def send_message(text):

    result = telegram_request(
        "sendMessage",
        {
            "chat_id": CHANNEL,
            "text": text
        }
    )

    if not result.get("ok"):
        logger.error(
            "Telegram message error: %s",
            result
        )

    return result.get("ok", False)


# =========================================================
# SAVE POST
# =========================================================

def save_post(
    title,
    url,
    source,
    image_hash=""
):

    conn = db()

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
            fingerprint(title, url),
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
# SOURCE EXTRACTION
# =========================================================

def get_source_name(url):

    trusted = source_is_trusted(
        url,
        ""
    )

    return trusted or "Unknown"


# =========================================================
# ARTICLE AGE
# =========================================================

def is_recent(entry):

    published = entry.get("published")

    if not published:
        return True

    try:

        published_dt = datetime.fromtimestamp(
            time.mktime(published),
            tz=timezone.utc
        )

        age = (
            datetime.now(timezone.utc)
            - published_dt
        )

        return age <= timedelta(
            hours=MAX_ARTICLE_AGE_HOURS
        )

    except Exception:
        return True


# =========================================================
# NEWS PROCESSOR
# =========================================================

def process_candidate(entry):

    title = clean_text(
        entry.get("title", "")
    )

    url = entry.get("url", "")

    if not title or not url:
        return False

    logger.info(
        "Checking: %s",
        title
    )

    # -----------------------------------------------------
    # Recent
    # -----------------------------------------------------

    if not is_recent(entry):
        logger.info("Rejected: old article")
        return False

    # -----------------------------------------------------
    # Trusted source
    # -----------------------------------------------------

    source = get_source_name(url)

    if source == "Unknown":
        logger.info(
            "Rejected: untrusted source"
        )
        return False

    # -----------------------------------------------------
    # Liverpool relevance
    # -----------------------------------------------------

    if not valid_liverpool_text(title):
        logger.info(
            "Rejected: not Liverpool related"
        )
        return False

    # -----------------------------------------------------
    # Duplicate
    # -----------------------------------------------------

    if news_already_posted(
        title,
        url
    ):
        logger.info(
            "Rejected: duplicate"
        )
        return False

    # -----------------------------------------------------
    # 5 minute gap
    # -----------------------------------------------------

    if not can_post():
        logger.info(
            "Waiting: 5 minute posting gap"
        )
        return False

    # -----------------------------------------------------
    # Fetch article
    # -----------------------------------------------------

    article = fetch_article(url)

    if not article:
        logger.info(
            "Rejected: article fetch failed"
        )
        return False

    body = article["body"]

    if len(body) < 300:
        logger.info(
            "Rejected: article body too short"
        )
        return False

    # -----------------------------------------------------
    # Check relevance again using full article
    # -----------------------------------------------------

    combined = (
        article["title"]
        + " "
        + body
    )

    if not valid_liverpool_text(
        combined
    ):
        logger.info(
            "Rejected: full article not Liverpool"
        )
        return False

    # -----------------------------------------------------
    # AI editorial processing
    # -----------------------------------------------------

    ai_article = ai_edit_news({
        "title": article["title"] or title,
        "body": body,
        "source": source,
        "url": article["final_url"]
    })

    if not ai_article:
        return False

    if ai_article.get("decision") != "POST":
        logger.info(
            "Rejected by AI: %s",
            ai_article.get("reason", "")
        )
        return False

    confidence = int(
        ai_article.get(
            "confidence",
            0
        )
    )

    if confidence < 85:
        logger.info(
            "Rejected: low AI confidence %s",
            confidence
        )
        return False

    headline = clean_text(
        ai_article.get(
            "headline",
            ""
        )
    )

    body_am = clean_text(
        ai_article.get(
            "body",
            ""
        )
    )

    if len(headline) < 10:
        return False

    if len(body_am) < 80:
        return False

    # -----------------------------------------------------
    # Ensure English headline wasn't returned
    # -----------------------------------------------------

    english_words = re.findall(
        r"\b(the|a|an|is|are|has|have|will|to|for|of|and)\b",
        headline.lower()
    )

    if len(english_words) >= 2:
        logger.info(
            "Rejected: English headline"
        )
        return False

    # -----------------------------------------------------
    # IMAGE
    # -----------------------------------------------------

    image_url = article.get(
        "image_url"
    )

    image = None

    if image_url:

        image = download_image(
            image_url
        )

    # If source page has no valid image,
    # do NOT attach an unrelated image.
    if image:

        if image_already_used(
            image["hash"]
        ):
            logger.info(
                "Rejected image: already used"
            )
            image = None

    # -----------------------------------------------------
    # Caption
    # -----------------------------------------------------

    caption = (
        f"{headline}\n\n"
        f"{body_am}\n\n"
        f"@yegnaLiverpoolET"
    )

    # Telegram caption limit
    caption = caption[:1020]

    # -----------------------------------------------------
    # Send
    # -----------------------------------------------------

    if image:

        success = send_photo(
            image["data"],
            caption
        )

        if not success:
            return False

        save_used_image(
            image["hash"],
            image["url"]
        )

        image_hash = image["hash"]

    else:

        success = send_message(
            caption
        )

        if not success:
            return False

        image_hash = ""

    # -----------------------------------------------------
    # Save
    # -----------------------------------------------------

    save_post(
        headline,
        article["final_url"],
        source,
        image_hash
    )

    logger.info(
        "POSTED: %s",
        headline
    )

    return True


# =========================================================
# MAIN LOOP
# =========================================================

def run():

    logger.info(
        "=========================================="
    )

    logger.info(
        "Liverpool News Bot started 🚀"
    )

    logger.info(
        "Channel: %s",
        CHANNEL
    )

    logger.info(
        "News check: every %s seconds",
        NEWS_CHECK_EVERY
    )

    logger.info(
        "Minimum post gap: %s seconds",
        MIN_POST_GAP
    )

    logger.info(
        "=========================================="
    )

    db()

    while True:

        try:

            entries = get_news_feed()

            logger.info(
                "Found %s candidate articles",
                len(entries)
            )

            # newest first
            entries = list(
                reversed(entries)
            )

            posted = False

            for entry in entries:

                if process_candidate(
                    entry
                ):

                    posted = True
                    break

            if not posted:
                logger.info(
                    "No suitable new Liverpool news."
                )

        except KeyboardInterrupt:

            logger.info(
                "Bot stopped."
            )

            break

        except Exception as e:

            logger.exception(
                "Main loop error: %s",
                e
            )

        time.sleep(
            NEWS_CHECK_EVERY
        )


# =========================================================
# START
# =========================================================

if __name__ == "__main__":
    run()
