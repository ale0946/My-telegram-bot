# Liverpool Official X + LIVE Bot
# Official X photos + original English captions
# LIVE match updates

import os
import time
import hashlib
import sqlite3
import logging
import requests
from dotenv import load_dotenv

# =========================================================
# CONFIG
# =========================================================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHANNEL = os.getenv("CHANNEL", "@yegnaLiverpool").strip()

X_BEARER_TOKEN = os.getenv("X_BEARER_TOKEN", "").strip()
X_USERNAME = os.getenv("X_USERNAME", "LFC").strip()

LIVERPOOL_TEAM_ID = "364"

X_CHECK_EVERY = 60
LIVE_CHECK_EVERY = 60
X_MIN_POST_GAP = 5 * 60

DB_FILE = "liverpool_bot.db"
SEND_STARTUP_TEST = (
    os.getenv("SEND_STARTUP_TEST", "true").lower() == "true"
)

REQUEST_TIMEOUT = 30

USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 10) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
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

if not X_BEARER_TOKEN:
    raise RuntimeError(
        "X_BEARER_TOKEN missing. Add X_BEARER_TOKEN to your .env file."
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
    conn = sqlite3.connect(DB_FILE, timeout=30)

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

    return " ".join(str(text).split()).strip()


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


# =========================================================
# TELEGRAM
# =========================================================

def telegram_api(method, data=None, files=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"

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
            return {"ok": False}

        if not result.get("ok"):
            logger.error("Telegram API error: %s", result)

        return result

    except requests.RequestException as e:
        logger.error("Telegram connection error: %s", e)
        return {"ok": False}

    except Exception as e:
        logger.exception("Telegram unexpected error: %s", e)
        return {"ok": False}


def telegram_send_message(text):
    result = telegram_api(
        "sendMessage",
        data={
            "chat_id": CHANNEL,
            "text": text,
            "disable_web_page_preview": False,
        },
    )

    return result.get("ok", False)


def telegram_send_photo(image_bytes, caption):
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

    return result.get("ok", False)


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
            "hash": sha256_bytes(response.content),
            "url": url,
        }

    except Exception as e:
        logger.warning(
            "Image download failed: %s",
            e,
        )
        return None


# =========================================================
# DATABASE HELPERS
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


def save_image(image_hash, image_url):
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


def live_event_was_posted(event_key):
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


def save_live_event(event_key, event_text):
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


def get_state(key, default=""):
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

    return row[0] if row else default


def set_state(key, value):
    conn = get_db()

    conn.execute(
        """
        INSERT INTO bot_state(key, value)
        VALUES (?, ?)
        ON CONFLICT(key)
        DO UPDATE SET value=excluded.value
        """,
        (key, str(value)),
    )

    conn.commit()
    conn.close()


# =========================================================
# X API
# =========================================================

def x_headers():
    return {
        "Authorization": f"Bearer {X_BEARER_TOKEN}",
        "User-Agent": USER_AGENT,
    }


def x_api_get(url, params=None):
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
            e,
        )
        return None

    except Exception as e:
        logger.exception(
            "X API error: %s",
            e,
        )
        return None


def get_x_user_id():
    cached = get_state("x_user_id")

    if cached:
        return cached

    data = x_api_get(
        f"https://api.x.com/2/users/by/username/{X_USERNAME}"
    )

    if not data or not data.get("data"):
        logger.error(
            "Could not find X user @%s",
            X_USERNAME,
        )
        return None

    user_id = data["data"]["id"]

    set_state(
        "x_user_id",
        user_id,
    )

    return user_id


def get_official_x_posts():
    user_id = get_x_user_id()

    if not user_id:
        return []

    params = {
        "max_results": 10,
        "exclude": "retweets,replies",
        "tweet.fields": "created_at,attachments,text",
        "expansions": "attachments.media_keys",
        "media.fields": (
            "media_key,type,url,preview_image_url,width,height"
        ),
    }

    data = x_api_get(
        f"https://api.x.com/2/users/{user_id}/tweets",
        params=params,
    )

    if not data:
        return []

    tweets = data.get("data", [])
    media_list = data.get(
        "includes",
        {}
    ).get(
        "media",
        []
    )

    media_by_key = {
        item.get("media_key"): item
        for item in media_list
    }

    results = []

    for tweet in tweets:
        tweet_id = tweet.get("id")
        text = clean_text(
            tweet.get("text", "")
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

            if media.get("type") == "photo":
                image_url = media.get("url")
                break

        tweet_url = (
            f"https://x.com/{X_USERNAME}/status/{tweet_id}"
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

def make_x_caption(tweet_text):
    tweet_text = clean_text(tweet_text)

    if not tweet_text:
        return ""

    # ORIGINAL ENGLISH TEXT IS KEPT.
    footer = "\n\n@yegnaLiverpool"

    if len(tweet_text) + len(footer) <= 1024:
        return tweet_text + footer

    max_text = 1024 - len(footer) - 3

    return (
        tweet_text[:max_text].rstrip()
        + "..."
        + footer
    )


def can_post_x():
    last_post = get_state(
        "last_x_post_time",
        "0",
    )

    try:
        last_post = int(last_post)
    except Exception:
        last_post = 0

    return (
        time.time() - last_post
        >= X_MIN_POST_GAP
    )


def process_x_post(tweet):
    tweet_id = tweet["id"]

    if x_tweet_was_posted(tweet_id):
        return False

    if not can_post_x():
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

    caption = make_x_caption(text)

    image_hash = ""
    success = False

    # PHOTO + ORIGINAL X CAPTION
    if image_url:
        image = download_image(
            image_url
        )

        if image and not image_was_used(
            image["hash"]
        ):
            success = telegram_send_photo(
                image["bytes"],
                caption,
            )

            if success:
                image_hash = image["hash"]

                save_image(
                    image["hash"],
                    image["url"],
                )

    # TEXT FALLBACK
    if not success:
        fallback = caption

        if tweet_url and len(fallback) < 900:
            fallback += (
                f"\n\n{tweet_url}"
            )

        success = telegram_send_message(
            fallback
        )

    if success:
        save_x_post(
            tweet_id,
            text,
            tweet_url,
            image_hash,
        )

        set_state(
            "last_x_post_time",
            int(time.time()),
        )

        logger.info(
            "OFFICIAL X POST SENT: %s",
            tweet_id,
        )

        return True

    return False


def check_official_x():
    logger.info(
        "Checking Liverpool official X: @%s",
        X_USERNAME,
    )

    posts = get_official_x_posts()

    if not posts:
        logger.info(
            "No official X posts found."
        )
        return 0

    # Newest first from X API.
    # Reverse so posts are sent oldest -> newest.
    posts.reverse()

    posted = 0

    for tweet in posts:
        if process_x_post(tweet):
            posted += 1

    return posted


# =========================================================
# ESPN LIVE
# =========================================================

def get_liverpool_scoreboard():
    url = (
        "https://site.api.espn.com/apis/site/v2/"
        "sports/soccer/eng.1/scoreboard"
    )

    try:
        response = requests.get(
            url,
            headers=HEADERS,
            params={"limit": 100},
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:
            logger.warning(
                "ESPN scoreboard HTTP %s",
                response.status_code,
            )
            return None

        return response.json()

    except Exception as e:
        logger.warning(
            "ESPN scoreboard error: %s",
            e,
        )
        return None


def find_liverpool_match():
    data = get_liverpool_scoreboard()

    if not data:
        return None

    for event in data.get(
        "events",
        []
    ):
        competitions = event.get(
            "competitions",
            []
        )

        if not competitions:
            continue

        competition = competitions[0]

        for competitor in competition.get(
            "competitors",
            []
        ):
            team = competitor.get(
                "team",
                {}
            )

            if str(team.get("id")) == LIVERPOOL_TEAM_ID:
                return event

    return None


def match_is_live(event):
    if not event:
        return False

    status = (
        event.get("status", {})
        .get("type", {})
    )

    return (
        status.get("state") == "in"
        or status.get("shortDetail") == "LIVE"
    )


def get_score_line(event):
    competition = event.get(
        "competitions",
        [{}]
    )[0]

    competitors = competition.get(
        "competitors",
        []
    )

    home = None
    away = None

    for competitor in competitors:
        if competitor.get("homeAway") == "home":
            home = competitor
        elif competitor.get("homeAway") == "away":
            away = competitor

    if not home or not away:
        return None

    home_name = (
        home.get("team", {})
        .get("displayName")
        or "Home"
    )

    away_name = (
        away.get("team", {})
        .get("displayName")
        or "Away"
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


def get_match_status_text(event):
    status = (
        event.get("status", {})
        .get("type", {})
    )

    return clean_text(
        status.get("shortDetail")
        or status.get("detail")
        or "LIVE"
    )


def get_match_summary(event_id):
    url = (
        "https://site.api.espn.com/apis/site/v2/"
        "sports/soccer/eng.1/summary"
    )

    try:
        response = requests.get(
            url,
            headers=HEADERS,
            params={"event": event_id},
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:
            return None

        return response.json()

    except Exception as e:
        logger.warning(
            "ESPN summary error: %s",
            e,
        )
        return None


def is_important_live_play(play):
    text = clean_text(
        play.get("text", "")
    ).lower()

    if not text:
        return False

    important_words = [
        "goal",
        "penalty",
        "red card",
        "yellow card",
        "substitution",
        "substituted",
        "var",
        "own goal",
    ]

    return any(
        word in text
        for word in important_words
    )


def process_live_match(event):
    if not event:
        return 0

    event_id = str(
        event.get("id", "")
    )

    if not event_id:
        return 0

    summary = get_match_summary(
        event_id
    )

    posted = 0

    # SCORE CHANGE
    score = get_score_line(event)
    status = get_match_status_text(event)

    old_score = get_state(
        f"live_score_{event_id}",
        "",
    )

    if score and score != old_score:
        message = (
            "🔴 LIVE\n\n"
            f"{score}\n"
            f"⏱ {status}\n\n"
            "@yegnaLiverpool"
        )

        if telegram_send_message(message):
            set_state(
                f"live_score_{event_id}",
                score,
            )

            posted += 1

    # IMPORTANT LIVE EVENTS
    if summary:
        plays = summary.get(
            "plays",
            []
        )

        for play in plays:
            if not is_important_live_play(
                play
            ):
                continue

            play_id = str(
                play.get("id")
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
                f"{event_id}|play|{play_id}"
            )

            if live_event_was_posted(
                event_key
            ):
                continue

            play_text = clean_text(
                play.get(
                    "text",
                    ""
                )
            )

            if not play_text:
                continue

            message = (
                "🔴 LIVE — Liverpool\n\n"
                f"⚽ {play_text}\n\n"
                f"📊 {score or ''}\n"
                f"⏱ {status}\n\n"
                "@yegnaLiverpool"
            )

            if telegram_send_message(
                message
            ):
                save_live_event(
                    event_key,
                    play_text,
                )

                posted += 1

    return posted


def check_live():
    event = find_liverpool_match()

    if not event:
        logger.info(
            "No Liverpool match found."
        )
        return 0

    if not match_is_live(event):
        logger.info(
            "Liverpool match found, but it is not LIVE."
        )
        return 0

    logger.info(
        "Liverpool LIVE match detected: %s",
        event.get("name", ""),
    )

    return process_live_match(
        event
    )


# =========================================================
# CLEAN OLD LIVE EVENTS
# =========================================================

def cleanup_old_live_data():
    cutoff = int(
        time.time()
    ) - (
        7 * 24 * 60 * 60
    )

    conn = get_db()

    conn.execute(
        """
        DELETE FROM live_events
        WHERE posted_at < ?
        """,
        (cutoff,),
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
        CHANNEL,
    )
    logger.info(
        "Official X: @%s",
        X_USERNAME,
    )
    logger.info(
        "X check every %s seconds",
        X_CHECK_EVERY,
    )
    logger.info(
        "LIVE check every %s seconds",
        LIVE_CHECK_EVERY,
    )
    logger.info("=" * 60)

    conn = get_db()
    conn.close()

    if SEND_STARTUP_TEST:
        success = telegram_send_message(
            "🤖 Liverpool Official X + LIVE Bot ተጀምሯል 🔴🚀"
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
    last_cleanup = 0

    while True:
        now = time.time()

        # OFFICIAL X
        if (
            now - last_x_check
            >= X_CHECK_EVERY
        ):
            try:
                check_official_x()
            except Exception as e:
                logger.exception(
                    "Official X check error: %s",
                    e,
                )

            last_x_check = now

        # LIVE
        if (
            now - last_live_check
            >= LIVE_CHECK_EVERY
        ):
            try:
                check_live()
            except Exception as e:
                logger.exception(
                    "LIVE check error: %s",
                    e,
                )

            last_live_check = now

        # DATABASE CLEANUP
        if (
            now - last_cleanup
            >= 6 * 60 * 60
        ):
            try:
                cleanup_old_live_data()
            except Exception as e:
                logger.warning(
                    "Cleanup error: %s",
                    e,
                )

            last_cleanup = now

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
            e,
        )
        raise
