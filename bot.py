```python
import os
import time
import hashlib
import sqlite3
import logging
import requests
import re

from bs4 import BeautifulSoup
from urllib.parse import urljoin
from dotenv import load_dotenv


# =========================================================
# CONFIG
# =========================================================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

CHANNEL = os.getenv(
    "CHANNEL",
    "@yegnaLiverpool"
).strip()

X_BEARER_TOKEN = os.getenv(
    "X_BEARER_TOKEN",
    ""
).strip()

X_USERNAME = os.getenv(
    "X_USERNAME",
    "LFC"
).strip()

# ESPN Liverpool Team ID
LIVERPOOL_TEAM_ID = "364"
ESPN_TEAM_ID = "364"

# =========================================================
# CHECK INTERVALS
# =========================================================

X_CHECK_EVERY = 60
LIVE_CHECK_EVERY = 60

# Official Liverpool website check
LFC_SITE_CHECK_EVERY = 5 * 60

# =========================================================
# IMPORTANT:
# ONE NORMAL POST EVERY 15 MINUTES
# X + OFFICIAL LIVERPOOL WEBSITE SHARE THIS LIMIT
# LIVE EVENTS ARE NOT BLOCKED BY THIS TIMER
# =========================================================

NORMAL_POST_MIN_GAP = 15 * 60

# Database
DB_FILE = "liverpool_bot.db"

# Startup message
SEND_STARTUP_TEST = (
    os.getenv(
        "SEND_STARTUP_TEST",
        "true"
    ).lower() == "true"
)

REQUEST_TIMEOUT = 30

USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 10) "
    "AppleWebKit/537.36 "
    "(KHTML, like Gecko) "
    "Chrome/150.0 Mobile Safari/537.36"
)

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json",
}


# =========================================================
# VALIDATION
# =========================================================

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN missing. Add BOT_TOKEN to your .env file."
    )


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
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
        CREATE TABLE IF NOT EXISTS posted_x(
            tweet_id TEXT PRIMARY KEY,
            tweet_text TEXT,
            tweet_url TEXT,
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
        CREATE TABLE IF NOT EXISTS live_events(
            event_key TEXT PRIMARY KEY,
            event_text TEXT,
            posted_at INTEGER
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS bot_state(
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    conn.commit()

    return conn


# =========================================================
# HELPERS
# =========================================================

def clean_text(text):

    if not text:
        return ""

    return " ".join(
        str(text).split()
    ).strip()


def sha256_bytes(data):

    return hashlib.sha256(
        data
    ).hexdigest()


# =========================================================
# TELEGRAM
# =========================================================

def telegram_api(
    method,
    data=None,
    files=None
):

    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/{method}"
    )

    try:

        response = requests.post(
            url,
            data=data,
            files=files,
            timeout=40,
        )

        try:
            result = response.json()

        except ValueError:

            logger.error(
                "Telegram returned invalid JSON. HTTP %s",
                response.status_code,
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
            "disable_web_page_preview": False,
        },
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
            "caption": caption[:1024],
        },
        files={
            "photo": (
                "liverpool.jpg",
                image_bytes,
                "image/jpeg",
            )
        },
    )

    return result.get(
        "ok",
        False
    )


# =========================================================
# DATABASE STATE HELPERS
# =========================================================

def get_state(
    key,
    default=""
):

    conn = get_db()

    row = conn.execute(
        """
        SELECT value
        FROM bot_state
        WHERE key=?
        LIMIT 1
        """,
        (key,),
    ).fetchone()

    conn.close()

    return (
        row[0]
        if row
        else default
    )


def set_state(
    key,
    value
):

    conn = get_db()

    conn.execute(
        """
        INSERT INTO bot_state(
            key,
            value
        )
        VALUES (?, ?)

        ON CONFLICT(key)
        DO UPDATE SET
            value=excluded.value
        """,
        (
            key,
            str(value)
        ),
    )

    conn.commit()
    conn.close()


# =========================================================
# GLOBAL NORMAL POST LIMIT
# =========================================================

def can_post_normal():

    last_post = get_state(
        "last_normal_post_time",
        "0"
    )

    try:

        last_post = int(
            last_post
        )

    except Exception:

        last_post = 0

    return (
        time.time()
        - last_post
        >= NORMAL_POST_MIN_GAP
    )


def save_normal_post_time():

    set_state(
        "last_normal_post_time",
        int(time.time())
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
            timeout=25,
        )

        if response.status_code != 200:

            logger.warning(
                "Image HTTP %s",
                response.status_code,
            )

            return None

        content_type = response.headers.get(
            "content-type",
            ""
        ).lower()

        if "image" not in content_type:
            return None

        if len(response.content) < 5000:
            return None

        return {
            "bytes": response.content,
            "hash": sha256_bytes(
                response.content
            ),
            "url": url,
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

def image_was_used(image_hash):

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
        (image_hash,),
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
        VALUES (?, ?, ?)
        """,
        (
            image_hash,
            image_url,
            int(time.time()),
        ),
    )

    conn.commit()
    conn.close()


# =========================================================
# OFFICIAL LIVERPOOL WEBSITE PHOTOS
# =========================================================

LFC_NEWS_URL = "https://www.liverpoolfc.com/news"


def get_lfc_news_page():

    try:

        response = requests.get(
            LFC_NEWS_URL,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html",
            },
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:

            logger.warning(
                "Liverpool website HTTP %s",
                response.status_code,
            )

            return None

        return response.text

    except Exception as e:

        logger.warning(
            "Liverpool website error: %s",
            e,
        )

        return None


def extract_lfc_article_links(html):

    if not html:
        return []

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    links = []

    for a in soup.find_all(
        "a",
        href=True
    ):

        href = a.get(
            "href",
            ""
        ).strip()

        if not href:
            continue

        if href.startswith("/"):
            href = urljoin(
                "https://www.liverpoolfc.com",
                href,
            )

        if not href.startswith(
            "https://www.liverpoolfc.com/"
        ):
            continue

        if "/news/" not in href:
            continue

        href = href.split("#")[0]

        if href not in links:
            links.append(href)

    return links[:30]


def is_lfc_photo_article(soup):

    title = ""

    if soup.title:

        title = clean_text(
            soup.title.get_text(
                " ",
                strip=True
            )
        )

    text = clean_text(
        soup.get_text(
            " ",
            strip=True
        )
    ).lower()

    photo_words = [
        "photos",
        "photo gallery",
        "gallery",
        "in photos",
        "training photos",
        "pictures",
    ]

    if any(
        word in title.lower()
        for word in photo_words
    ):
        return True

    if any(
        word in text[:5000]
        for word in photo_words
    ):
        return True

    return False


def extract_lfc_article(url):

    try:

        response = requests.get(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html",
            },
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:
            return None

        soup = BeautifulSoup(
            response.text,
            "html.parser",
        )

        if not is_lfc_photo_article(soup):
            return None

        # TITLE
        title = ""

        h1 = soup.find("h1")

        if h1:

            title = clean_text(
                h1.get_text(
                    " ",
                    strip=True
                )
            )

        if not title and soup.title:

            title = clean_text(
                soup.title.get_text(
                    " ",
                    strip=True
                )
            )

        # DESCRIPTION
        description = ""

        meta = soup.find(
            "meta",
            attrs={
                "name": "description"
            },
        )

        if meta:

            description = clean_text(
                meta.get(
                    "content",
                    ""
                )
            )

        # IMAGES
        image_urls = []

        for img in soup.find_all("img"):

            src = (
                img.get("src")
                or img.get("data-src")
                or img.get("data-lazy-src")
            )

            if not src:
                continue

            src = urljoin(
                url,
                src,
            )

            if not src.startswith("http"):
                continue

            lower = src.lower()

            if any(
                bad in lower
                for bad in [
                    "logo",
                    "icon",
                    "avatar",
                    "placeholder",
                ]
            ):
                continue

            if src not in image_urls:
                image_urls.append(src)

        # OPEN GRAPH IMAGE
        og = soup.find(
            "meta",
            property="og:image",
        )

        if og:

            og_url = og.get(
                "content",
                "",
            )

            if og_url:

                og_url = urljoin(
                    url,
                    og_url,
                )

                if og_url not in image_urls:

                    image_urls.insert(
                        0,
                        og_url,
                    )

        if not title:
            return None

        if not image_urls:
            return None

        return {
            "url": url,
            "title": title,
            "description": description,
            "image_urls": image_urls[:15],
        }

    except Exception as e:

        logger.warning(
            "LFC article parse error: %s",
            e,
        )

        return None


def lfc_article_was_posted(url):

    conn = get_db()

    row = conn.execute(
        """
        SELECT tweet_id
        FROM posted_x
        WHERE tweet_url=?
        LIMIT 1
        """,
        (url,),
    ).fetchone()

    conn.close()

    return row is not None


def save_lfc_article(
    url,
    title,
    image_hash=""
):

    conn = get_db()

    conn.execute(
        """
        INSERT OR IGNORE INTO posted_x
        (
            tweet_id,
            tweet_text,
            tweet_url,
            image_hash,
            posted_at
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            "lfc_site|" + hashlib.sha256(
                url.encode("utf-8")
            ).hexdigest(),
            title,
            url,
            image_hash,
            int(time.time()),
        ),
    )

    conn.commit()
    conn.close()


def make_lfc_site_caption(article):

    title = clean_text(
        article.get("title", "")
    )

    description = clean_text(
        article.get("description", "")
    )

    lines = []

    if title:
        lines.append(title)

    if (
        description
        and description != title
    ):
        lines.append(description)

    lines.append("")
    lines.append("@yegnaLiverpool")

    caption = "\n\n".join(lines)

    return caption[:1024]


def process_lfc_site_article(article):

    if not article:
        return False

    # GLOBAL 15-MINUTE LIMIT
    if not can_post_normal():

        logger.info(
            "⏳ Official Liverpool photo skipped: "
            "15-minute post gap has not passed."
        )

        return False

    url = article.get(
        "url",
        "",
    )

    if not url:
        return False

    if lfc_article_was_posted(url):
        return False

    caption = make_lfc_site_caption(
        article
    )

    image_urls = article.get(
        "image_urls",
        [],
    )

    for image_url in image_urls:

        image = download_image(
            image_url
        )

        if not image:
            continue

        if image_was_used(
            image["hash"]
        ):
            continue

        success = telegram_send_photo(
            image["bytes"],
            caption,
        )

        if success:

            save_image(
                image["hash"],
                image["url"]
            )

            save_lfc_article(
                url,
                article.get(
                    "title",
                    "",
                ),
                image["hash"],
            )

            # Start the global 15-minute timer
            save_normal_post_time()

            logger.info(
                "OFFICIAL LFC PHOTO SENT: %s",
                article.get(
                    "title",
                    "",
                ),
            )

            return True

    return False


def check_official_liverpool_site():

    logger.info(
        "Checking Official Liverpool website photos..."
    )

    # Do not even scan/post if 15 minutes
    # have not passed.
    if not can_post_normal():

        logger.info(
            "⏳ Official Liverpool website "
            "check blocked by 15-minute limit."
        )

        return 0

    html = get_lfc_news_page()

    if not html:
        return 0

    links = extract_lfc_article_links(
        html
    )

    if not links:
        return 0

    posted = 0

    for url in links:

        article = extract_lfc_article(
            url
        )

        if not article:
            continue

        if process_lfc_site_article(
            article
        ):

            posted += 1

        # ONE normal post maximum
        if posted >= 1:
            break

    return posted


# =========================================================
# X DATABASE
# =========================================================

def x_tweet_was_posted(tweet_id):

    conn = get_db()

    row = conn.execute(
        """
        SELECT tweet_id
        FROM posted_x
        WHERE tweet_id=?
        LIMIT 1
        """,
        (tweet_id,),
    ).fetchone()

    conn.close()

    return row is not None


def save_x_post(
    tweet_id,
    tweet_text,
    tweet_url,
    image_hash=""
):

    conn = get_db()

    conn.execute(
        """
        INSERT OR IGNORE INTO posted_x
        (
            tweet_id,
            tweet_text,
            tweet_url,
            image_hash,
            posted_at
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            tweet_id,
            tweet_text,
            tweet_url,
            image_hash,
            int(time.time()),
        ),
    )

    conn.commit()
    conn.close()


# =========================================================
# X API
# =========================================================

def x_headers():

    return {
        "Authorization": (
            f"Bearer {X_BEARER_TOKEN}"
        ),
        "User-Agent": USER_AGENT,
    }


def x_api_get(
    url,
    params=None
):

    try:

        response = requests.get(
            url,
            headers=x_headers(),
            params=params,
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:

            logger.error(
                "X API HTTP %s: %s",
                response.status_code,
                response.text[:500],
            )

            return None

        return response.json()

    except requests.RequestException as e:

        logger.error(
            "X API request failed: %s",
            e
        )

        return None

    except Exception as e:

        logger.exception(
            "X API error: %s",
            e
        )

        return None


def get_x_user_id():

    cached = get_state(
        "x_user_id"
    )

    if cached:
        return cached

    data = x_api_get(
        "https://api.x.com/2/users/"
        f"by/username/{X_USERNAME}"
    )

    if (
        not data
        or not data.get("data")
    ):

        logger.error(
            "Could not find X user @%s",
            X_USERNAME
        )

        return None

    user_id = data["data"]["id"]

    set_state(
        "x_user_id",
        user_id
    )

    return user_id


def get_official_x_posts():

    user_id = get_x_user_id()

    if not user_id:
        return []

    params = {

        "max_results": 10,

        "exclude": (
            "retweets,replies"
        ),

        "tweet.fields": (
            "created_at,attachments,text"
        ),

        "expansions": (
            "attachments.media_keys"
        ),

        "media.fields": (
            "media_key,type,url,"
            "preview_image_url,width,height"
        ),
    }

    data = x_api_get(
        f"https://api.x.com/2/users/"
        f"{user_id}/tweets",
        params=params,
    )

    if not data:
        return []

    tweets = data.get(
        "data",
        []
    )

    media_list = (
        data.get(
            "includes",
            {}
        ).get(
            "media",
            []
        )
    )

    media_by_key = {
        item.get("media_key"): item
        for item in media_list
    }

    results = []

    for tweet in tweets:

        tweet_id = tweet.get(
            "id"
        )

        text = clean_text(
            tweet.get(
                "text",
                ""
            )
        )

        if not tweet_id:
            continue

        attachments = tweet.get(
            "attachments",
            {}
        )

        media_keys = attachments.get(
            "media_keys",
            []
        )

        image_url = None

        for media_key in media_keys:

            media = media_by_key.get(
                media_key,
                {}
            )

            if (
                media.get("type")
                == "photo"
            ):

                image_url = media.get(
                    "url"
                )

                break

        tweet_url = (
            f"https://x.com/"
            f"{X_USERNAME}/status/"
            f"{tweet_id}"
        )

        results.append({

            "id": tweet_id,

            "text": text,

            "image_url": image_url,

            "url": tweet_url,

            "created_at": tweet.get(
                "created_at",
                ""
            ),
        })

    return results


# =========================================================
# OFFICIAL X POSTING
# =========================================================

def make_x_caption(
    tweet_text
):

    tweet_text = clean_text(
        tweet_text
    )

    if not tweet_text:
        return ""

    footer = (
        "\n\n@yegnaLiverpool"
    )

    if (
        len(tweet_text)
        + len(footer)
        <= 1024
    ):

        return (
            tweet_text
            + footer
        )

    max_text = (
        1024
        - len(footer)
        - 3
    )

    return (
        tweet_text[:max_text]
        .rstrip()
        + "..."
        + footer
    )


def process_x_post(
    tweet
):

    tweet_id = tweet["id"]

    if x_tweet_was_posted(
        tweet_id
    ):
        return False

    # GLOBAL 15-MINUTE NORMAL POST LIMIT
    if not can_post_normal():

        logger.info(
            "⏳ X post skipped: "
            "15-minute normal-post gap has not passed."
        )

        return False

    text = tweet.get(
        "text",
        ""
    )

    image_url = tweet.get(
        "image_url"
    )

    tweet_url = tweet.get(
        "url",
        ""
    )

    caption = make_x_caption(
        text
    )

    image_hash = ""

    success = False

    # =====================================================
    # PHOTO + ORIGINAL X CAPTION
    # =====================================================

    if image_url:

        image = download_image(
            image_url
        )

        if (
            image
            and not image_was_used(
                image["hash"]
            )
        ):

            success = (
                telegram_send_photo(
                    image["bytes"],
                    caption
                )
            )

            if success:

                image_hash = (
                    image["hash"]
                )

                save_image(
                    image["hash"],
                    image["url"]
                )

    # =====================================================
    # TEXT FALLBACK
    # =====================================================

    if not success:

        fallback = caption

        if (
            tweet_url
            and len(fallback) < 900
        ):

            fallback += (
                f"\n\n{tweet_url}"
            )

        success = (
            telegram_send_message(
                fallback
            )
        )

    if success:

        save_x_post(
            tweet_id,
            text,
            tweet_url,
            image_hash,
        )

        # IMPORTANT:
        # Start global 15-minute timer
        # only after successful normal post.
        save_normal_post_time()

        logger.info(
            "OFFICIAL X POST SENT: %s",
            tweet_id
        )

        return True

    return False


def check_official_x():

    logger.info(
        "Checking Liverpool official X: @%s",
        X_USERNAME
    )

    # ONE NORMAL POST ONLY
    if not can_post_normal():

        logger.info(
            "⏳ X check blocked: "
            "15-minute normal-post gap has not passed."
        )

        return 0

    posts = (
        get_official_x_posts()
    )

    if not posts:

        logger.info(
            "No official X posts found."
        )

        return 0

    # X returns newest first.
    # Oldest -> newest
    posts.reverse()

    posted = 0

    for tweet in posts:

        if process_x_post(
            tweet
        ):

            posted += 1

            # ONE POST MAXIMUM
            break

    return posted


# =========================================================
# LIVE DATABASE
# =========================================================

def live_event_was_posted(
    event_key
):

    conn = get_db()

    row = conn.execute(
        """
        SELECT event_key
        FROM live_events
        WHERE event_key=?
        LIMIT 1
        """,
        (event_key,),
    ).fetchone()

    conn.close()

    return row is not None


def save_live_event(
    event_key,
    event_text
):

    conn = get_db()

    conn.execute(
        """
        INSERT OR IGNORE INTO live_events
        (
            event_key,
            event_text,
            posted_at
        )
        VALUES (?, ?, ?)
        """,
        (
            event_key,
            event_text,
            int(time.time()),
        ),
    )

    conn.commit()
    conn.close()


# =========================================================
# ESPN LIVE
# =========================================================

def get_liverpool_schedule():

    url = (
        "https://site.api.espn.com/apis/site/v2/"
        "sports/soccer/eng.1/teams/"
        f"{ESPN_TEAM_ID}/schedule"
    )

    try:

        response = requests.get(
            url,
            headers=HEADERS,
            params={
                "limit": 100
            },
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:

            logger.warning(
                "ESPN Liverpool schedule HTTP %s",
                response.status_code
            )

            return None

        return response.json()

    except Exception as e:

        logger.warning(
            "ESPN Liverpool schedule error: %s",
            e
        )

        return None


def match_is_live(
    event
):

    if not event:
        return False

    status = (
        event.get(
            "status",
            {}
        ).get(
            "type",
            {}
        )
    )

    state = str(
        status.get(
            "state",
            ""
        )
    ).lower()

    return state == "in"


def find_liverpool_live_match():

    data = (
        get_liverpool_schedule()
    )

    if not data:
        return None

    for event in data.get(
        "events",
        []
    ):

        if not event:
            continue

        if not match_is_live(
            event
        ):
            continue

        competitions = event.get(
            "competitions",
            []
        )

        if not competitions:
            continue

        competition = competitions[0]

        for competitor in (
            competition.get(
                "competitors",
                []
            )
        ):

            team = competitor.get(
                "team",
                {}
            )

            team_id = str(
                team.get(
                    "id",
                    ""
                )
            )

            if (
                team_id
                == ESPN_TEAM_ID
            ):

                return event

    return None


def get_match_competition(
    event
):

    competitions = event.get(
        "competitions",
        []
    )

    if not competitions:
        return ""

    competition = (
        competitions[0]
    )

    return clean_text(
        competition.get(
            "type",
            {}
        ).get(
            "text",
            ""
        )
    )


def get_score_line(
    event
):

    competition = event.get(
        "competitions",
        [{}]
    )[0]

    competitors = (
        competition.get(
            "competitors",
            []
        )
    )

    home = None
    away = None

    for competitor in competitors:

        if (
            competitor.get(
                "homeAway"
            )
            == "home"
        ):

            home = competitor

        elif (
            competitor.get(
                "homeAway"
            )
            == "away"
        ):

            away = competitor

    if not home or not away:
        return None

    home_name = (
        home.get(
            "team",
            {}
        ).get(
            "displayName"
        )
        or "Home"
    )

    away_name = (
        away.get(
            "team",
            {}
        ).get(
            "displayName"
        )
        or "Away"
    )

    home_score = str(
        home.get(
            "score",
            "0"
        )
    )

    away_score = str(
        away.get(
            "score",
            "0"
        )
    )

    return (
        f"{home_name} "
        f"{home_score} - "
        f"{away_score} "
        f"{away_name}"
    )


def get_match_status_text(
    event
):

    status = (
        event.get(
            "status",
            {}
        ).get(
            "type",
            {}
        )
    )

    return clean_text(
        status.get(
            "shortDetail"
        )
        or status.get(
            "detail"
        )
        or "LIVE"
    )


def get_match_summary(
    event_id
):

    url = (
        "https://site.api.espn.com/apis/site/v2/"
        "sports/soccer/eng.1/summary"
    )

    try:

        response = requests.get(
            url,
            headers=HEADERS,
            params={
                "event": event_id
            },
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:

            logger.warning(
                "ESPN summary HTTP %s",
                response.status_code
            )

            return None

        return response.json()

    except Exception as e:

        logger.warning(
            "ESPN summary error: %s",
            e
        )

        return None


# =========================================================
# LIVE PLAYER
# =========================================================

def get_player_name_from_play(
    play
):

    participants = play.get(
        "participants",
        []
    )

    for participant in participants:

        athlete = participant.get(
            "athlete",
            {}
        )

        name = (
            athlete.get(
                "displayName"
            )
            or athlete.get(
                "fullName"
            )
            or athlete.get(
                "shortName"
            )
        )

        if name:

            return clean_text(
                name
            )

    return ""


# =========================================================
# LIVE EVENT DETECTION
# =========================================================

def detect_live_event_type(
    play
):

    text = clean_text(
        play.get(
            "text",
            ""
        )
    ).lower()

    if not text:
        return None

    if (
        "goal" in text
        and "disallowed" not in text
        and "cancelled" not in text
        and "missed" not in text
    ):

        return "goal"

    if (
        "red card" in text
        or "sent off" in text
        or "second yellow" in text
    ):

        return "red_card"

    if "yellow card" in text:

        return "yellow_card"

    if (
        "substitution" in text
        or "substituted" in text
        or "substitution on" in text
        or "substitution off" in text
    ):

        return "substitution"

    if (
        "var" in text
        or "video assistant referee" in text
    ):

        return "var"

    return None


# =========================================================
# AMHARIC LIVE MESSAGE
# =========================================================

def amharic_live_message(
    event_type,
    play,
    score,
    status
):

    raw_text = clean_text(
        play.get(
            "text",
            ""
        )
    )

    player_name = (
        get_player_name_from_play(
            play
        )
    )

    if event_type == "goal":

        if player_name:

            headline = (
                f"⚽ ጎል! "
                f"{player_name} "
                f"ጎል አስቆጠረ!"
            )

        else:

            headline = "⚽ ጎል!"

    elif event_type == "red_card":

        if player_name:

            headline = (
                f"🟥 ቀይ ካርድ! "
                f"{player_name} "
                f"ቀይ ካርድ ተመዝግቦበታል።"
            )

        else:

            headline = (
                "🟥 ቀይ ካርድ! "
                "ተጫዋች ቀይ ካርድ "
                "ተመዝግቦበታል።"
            )

    elif event_type == "yellow_card":

        if player_name:

            headline = (
                f"🟨 ቢጫ ካርድ! "
                f"{player_name} "
                f"ቢጫ ካርድ ተመዝግቦበታል።"
            )

        else:

            headline = (
                "🟨 ቢጫ ካርድ "
                "ተመዝግቧል።"
            )

    elif event_type == "substitution":

        if player_name:

            headline = (
                f"🔄 ቅያሪ! "
                f"{player_name} "
                f"በተጫዋች ቅያሪ ውስጥ "
                f"ተሳትፏል።"
            )

        else:

            headline = (
                "🔄 የተጫዋች ቅያሪ "
                "ተደርጓል።"
            )

    elif event_type == "var":

        headline = (
            "🖥️ VAR ምርመራ "
            "እየተደረገ ነው።"
        )

    else:

        headline = (
            "🔴 LIVE\n\n"
            f"{raw_text}"
        )

    return (
        "🔴 LIVE\n\n"
        f"{headline}\n\n"
        f"📊 {score or ''}\n"
        f"⏱️ {status}\n\n"
        "@yegnaLiverpool"
    )


# =========================================================
# HALF TIME / FULL TIME
# =========================================================

def process_match_status(
    event,
    score,
    status
):

    event_id = str(
        event.get(
            "id",
            ""
        )
    )

    if not event_id:
        return 0

    status_type = (
        event.get(
            "status",
            {}
        ).get(
            "type",
            {}
        )
    )

    status_name = str(
        status_type.get(
            "name",
            ""
        )
    ).lower()

    short_detail = str(
        status_type.get(
            "shortDetail",
            ""
        )
    ).lower()

    # HALF TIME
    is_half_time = (
        "halftime" in status_name
        or (
            "half" in short_detail
            and "time" in short_detail
        )
    )

    if is_half_time:

        event_key = (
            f"{event_id}|half_time"
        )

        if not live_event_was_posted(
            event_key
        ):

            message = (
                "⏸️ የመጀመሪያው አጋማሽ "
                "ተጠናቋል።\n\n"
                f"📊 {score or ''}\n\n"
                "@yegnaLiverpool"
            )

            if telegram_send_message(
                message
            ):

                save_live_event(
                    event_key,
                    message
                )

                return 1

    # FULL TIME
    is_full_time = (
        "fulltime" in status_name
        or "full time" in short_detail
        or "ft" == short_detail.strip()
    )

    if is_full_time:

        event_key = (
            f"{event_id}|full_time"
        )

        if not live_event_was_posted(
            event_key
        ):

            message = (
                "🏁 ጨዋታው ተጠናቋል።\n\n"
                f"📊 {score or ''}\n\n"
                "@yegnaLiverpool"
            )

            if telegram_send_message(
                message
            ):

                save_live_event(
                    event_key,
                    message
                )

                return 1

    return 0


# =========================================================
# PROCESS LIVE MATCH
# =========================================================

def process_live_match(
    event
):

    if not event:
        return 0

    event_id = str(
        event.get(
            "id",
            ""
        )
    )

    if not event_id:
        return 0

    summary = (
        get_match_summary(
            event_id
        )
    )

    score = get_score_line(
        event
    )

    status = get_match_status_text(
        event
    )

    competition = (
        get_match_competition(
            event
        )
    )

    posted = 0

    # SCORE CHANGE
    old_score = get_state(
        f"live_score_{event_id}",
        ""
    )

    if (
        score
        and score != old_score
    ):

        message = (
            "🔴 LIVE\n\n"
            f"📊 {score}\n"
            f"⏱️ {status}\n\n"
            "@yegnaLiverpool"
        )

        if telegram_send_message(
            message
        ):

            set_state(
                f"live_score_{event_id}",
                score
            )

            posted += 1

            logger.info(
                "LIVE SCORE UPDATE SENT | "
                "%s | %s",
                competition,
                score
            )

    # HALF / FULL TIME
    posted += (
        process_match_status(
            event,
            score,
            status
        )
    )

    # IMPORTANT EVENTS
    if not summary:
        return posted

    plays = summary.get(
        "plays",
        []
    )

    for play in plays:

        event_type = (
            detect_live_event_type(
                play
            )
        )

        if not event_type:
            continue

        play_id = str(
            play.get(
                "id"
            )
            or (
                str(
                    play.get(
                        "clock",
                        {}
                    )
                )
                + "|"
                + play.get(
                    "text",
                    ""
                )
            )
        )

        event_key = (
            f"{event_id}|"
            f"{event_type}|"
            f"{play_id}"
        )

        if live_event_was_posted(
            event_key
        ):
            continue

        message = (
            amharic_live_message(
                event_type,
                play,
                score,
                status
            )
        )

        if telegram_send_message(
            message
        ):

            save_live_event(
                event_key,
                message
            )

            posted += 1

            logger.info(
                "LIVE EVENT SENT | "
                "%s | %s",
                event_type,
                play.get(
                    "text",
                    ""
                )
            )

    return posted


# =========================================================
# CHECK LIVE
# =========================================================

def check_live():

    event = (
        find_liverpool_live_match()
    )

    if not event:

        logger.info(
            "No Liverpool LIVE match."
        )

        return 0

    logger.info(
        "Liverpool LIVE match detected: %s",
        event.get(
            "name",
            ""
        )
    )

    return process_live_match(
        event
    )


# =========================================================
# CLEAN OLD LIVE EVENTS
# =========================================================

def cleanup_old_live_data():

    cutoff = (
        int(time.time())
        - (
            7 * 24 * 60 * 60
        )
    )

    conn = get_db()

    conn.execute(
        """
        DELETE FROM live_events
        WHERE posted_at < ?
        """,
        (cutoff,)
    )

    conn.commit()
    conn.close()


# =========================================================
# MAIN LOOP
# =========================================================

def run_bot():

    logger.info("=" * 60)

    logger.info(
        "Liverpool Official X + LIVE Bot Started"
    )

    logger.info(
        "Channel: %s",
        CHANNEL
    )

    logger.info(
        "Official X: @%s",
        X_USERNAME
    )

    logger.info(
        "Official Liverpool website photos: ENABLED"
    )

    logger.info(
        "LIVE source: ESPN"
    )

    logger.info(
        "Normal posts: 1 every 15 minutes maximum"
    )

    logger.info(
        "LIVE language: Amharic"
    )

    logger.info(
        "Player names: English"
    )

    logger.info(
        "Club names: English"
    )

    logger.info("=" * 60)

    # Initialize database
    conn = get_db()
    conn.close()

    # =====================================================
    # STARTUP TEST
    # =====================================================

    if SEND_STARTUP_TEST:

        success = telegram_send_message(
            "🤖 Liverpool Official X + LIVE Bot "
            "ተጀምሯል 🔴🚀"
        )

        if success:

            logger.info(
                "Startup test sent."
            )

        else:

            logger.warning(
                "Startup test failed."
            )

    last_x_check = 0
    last_live_check = 0
    last_lfc_site_check = 0
    last_cleanup = 0

    # =====================================================
    # MAIN LOOP
    # =====================================================

    while True:

        now = time.time()

        # =================================================
        # OFFICIAL X
        # =================================================

        if (
            now - last_x_check
            >= X_CHECK_EVERY
        ):

            try:

                check_official_x()

            except Exception as e:

                logger.exception(
                    "Official X check error: %s",
                    e
                )

            last_x_check = now

        # =================================================
        # LIVE MATCH
        # =================================================

        if (
            now - last_live_check
            >= LIVE_CHECK_EVERY
        ):

            try:

                check_live()

            except Exception as e:

                logger.exception(
                    "LIVE check error: %s",
                    e
                )

            last_live_check = now

        # =================================================
        # OFFICIAL LIVERPOOL WEBSITE PHOTOS
        # =================================================

        if (
            now - last_lfc_site_check
            >= LFC_SITE_CHECK_EVERY
        ):

            try:

                posted = (
                    check_official_liverpool_site()
                )

                if posted:

                    logger.info(
                        "Official Liverpool photo "
                        "posted: %s",
                        posted
                    )

                else:

                    logger.info(
                        "No new Official Liverpool "
                        "photo found."
                    )

            except Exception as e:

                logger.exception(
                    "Official Liverpool website "
                    "check error: %s",
                    e
                )

            last_lfc_site_check = now

        # =================================================
        # DATABASE CLEANUP
        # =================================================

        if (
            now - last_cleanup
            >= 6 * 60 * 60
        ):

            try:

                cleanup_old_live_data()

            except Exception as e:

                logger.warning(
                    "Cleanup error: %s",
                    e
                )

            last_cleanup = now

        # =================================================
        # SMALL SLEEP
        # =================================================

        time.sleep(5)


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
```
