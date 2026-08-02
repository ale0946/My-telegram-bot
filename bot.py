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
    raise RuntimeError(
        "BOT_TOKEN is missing."
    )

if not GROQ_API_KEY:
    raise RuntimeError(
        "GROQ_API_KEY is missing."
    )

if not CHANNEL_ID:
    raise RuntimeError(
        "CHANNEL_ID is missing."
    )


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
    )
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
            datetime.now(
                timezone.utc
            ).isoformat()
        )
    )

    db.commit()


# =========================================================
# LIVE EVENT DATABASE
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
# DATE HELPERS
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
# IMAGE HELPERS
# =========================================================

def get_feed_image(entry):
    try:
        media_content = getattr(
            entry,
            "media_content",
            []
        )

        for media in media_content:
            image_url = media.get(
                "url",
                ""
            )

            if image_url:
                return image_url

    except Exception:
        pass

    try:
        media_thumbnail = getattr(
            entry,
            "media_thumbnail",
            []
        )

        for media in media_thumbnail:
            image_url = media.get(
                "url",
                ""
            )

            if image_url:
                return image_url

    except Exception:
        pass

    try:
        enclosures = getattr(
            entry,
            "enclosures",
            []
        )

        for enclosure in enclosures:
            image_url = enclosure.get(
                "href",
                ""
            )

            if image_url:
                return image_url

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
            image_url = image.get(
                "src",
                ""
            )

            if image_url:
                return image_url

    except Exception:
        pass

    return ""


def get_page_image(url):
    if not url:
        return ""

    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=20
        )

        response.raise_for_status()

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        og_image = soup.find(
            "meta",
            property="og:image"
        )

        if og_image:
            image_url = og_image.get(
                "content",
                ""
            )

            if image_url:
                return image_url

        twitter_image = soup.find(
            "meta",
            attrs={
                "name": "twitter:image"
            }
        )

        if twitter_image:
            image_url = twitter_image.get(
                "content",
                ""
            )

            if image_url:
                return image_url

    except Exception as e:
        logger.warning(
            "Could not get page image: %s",
            e
        )

    return ""


def get_article_image(entry, url):
    image_url = get_feed_image(entry)

    if image_url:
        return image_url

    return get_page_image(url)


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

            image_url = get_article_image(
                entry,
                url
            )

            articles.append({
                "title": title,
                "url": url,
                "summary": summary,
                "source": "Liverpool FC Official",
                "image_url": image_url
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
# AI SYSTEM PROMPT
# =========================================================

SYSTEM_PROMPT = """
አንተ ለLiverpool FC የአማርኛ Telegram የዜና አርታኢ ነህ።

ዋና ዓላማህ የተሰጠህን የዜና መረጃ ብቻ ተጠቅመህ
ግልጽ፣ ተፈጥሯዊ እና የስፖርት ዘገባ የሚመስል
አማርኛ ማዘጋጀት ነው።

ጥብቅ ህጎች፦

1. ከተሰጠው article ውጭ ምንም እውነታ አትጨምር።
2. ምንም quote አትፍጠር።
3. የዝውውር ዋጋ አትፍጠር።
4. ቀን አትፍጠር።
5. ጉዳት አትፍጠር።
6. የውል መረጃ አትፍጠር።
7. Rumour/report ከሆነ እንደተረጋገጠ አትጻፍ።
8. እርግጠኛ ያልሆነ መረጃ እርግጠኛ እንደሆነ አታቅርብ።
9. ዜናው በግልጽ Liverpool FC ላይ ካልሆነ REJECT በል።
10. አማርኛው ተፈጥሯዊ የስፖርት አማርኛ ይሁን።
11. እንግሊዝኛን ቃል በቃል አትተርጉም።
12. English headline አትጻፍ።
13. English paragraph አትጻፍ።
14. Clickbait አትጠቀም።
15. ተመሳሳይ ሀሳብን ደጋግመህ አትጻፍ።
16. የዜናውን ዋና ነጥብ በግልጽ አማርኛ አቅርብ።
17. ስሞችን በተቻለ መጠን ትክክል ጠብቅ።

JSON ብቻ መልስ።

Format:

{
  "decision": "POST" or "REJECT",
  "category": "news/transfer/rumour/injury/match/other",
  "headline": "አጭር ተፈጥሯዊ የአማርኛ ርዕስ",
  "body": "ተፈጥሯዊ የአማርኛ ዜና ይዘት",
  "confidence": 0-100
}

POST ማለት ለTelegram ተስማሚ ነው።
REJECT ማለት አትለጥፍ።
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

ይህን ዜና በተሰጡት ህጎች መሰረት ተንትነው።

የመጨረሻው ውጤት ተፈጥሯዊ የሆነ
የስፖርት አማርኛ ይሁን።

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
# TELEGRAM SEND MESSAGE
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
        "disable_web_page_preview": True
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
# TELEGRAM SEND PHOTO
# =========================================================

def telegram_send_photo(
    image_url,
    caption
):

    if not image_url:
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{BOT_TOKEN}/sendPhoto"
    )

    payload = {
        "chat_id": CHANNEL_ID,
        "photo": image_url,
        "caption": caption,
        "parse_mode": "HTML"
    }

    try:

        response = requests.post(
            url,
            json=payload,
            timeout=30
        )

        if response.status_code != 200:

            logger.error(
                "Telegram photo error: %s",
                response.text
            )

            return False

        data = response.json()

        if not data.get("ok"):

            logger.error(
                "Telegram rejected photo: %s",
                data
            )

            return False

        return True

    except Exception as e:

        logger.error(
            "Telegram photo connection error: %s",
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

    headline = escape_html(
        headline
    )

    body = escape_html(
        body
    )

    return (
        f"<b>{headline}</b>\n\n"
        f"{body}\n\n"
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
    # Duplicate check
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
    # Build message
    # -----------------------------------------------------

    message = build_telegram_message(
        result
    )

    if not message:
        return False

    # -----------------------------------------------------
    # PHOTO FIRST
    # -----------------------------------------------------

    if image_url:

        sent = telegram_send_photo(
            image_url,
            message
        )

        if sent:

            save_posted(
                fingerprint,
                title,
                url,
                article.get(
                    "source",
                    ""
                )
            )

            logger.info(
                "POSTED WITH IMAGE: %s",
                title
            )

            return True

        logger.warning(
            "Image failed. Trying text post..."
        )

    # -----------------------------------------------------
    # TEXT FALLBACK
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

    save_posted(
        fingerprint,
        title,
        url,
        article.get(
            "source",
            ""
        )
    )

    logger.info(
        "POSTED TEXT ONLY: %s",
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
        "Checking Liverpool trusted sources..."
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
        "News finished. Posted: %s",
        posted_count
    )


# =========================================================
# ESPN LIVE DATA
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
            "ESPN scoreboard error: %s",
            e
        )

        return None


# =========================================================
# FIND LIVERPOOL MATCH
# =========================================================

def find_liverpool_match(data):

    if not data:
        return None

    events = data.get(
        "events",
        []
    )

    for event in events:

        competitions = event.get(
            "competitions",
            []
        )

        for competition in competitions:

            competitors = competition.get(
                "competitors",
                []
            )

            for team in competitors:

                team_info = team.get(
                    "team",
                    {}
                )

                team_id = str(
                    team_info.get(
                        "id",
                        ""
                    )
                )

                if team_id == LIVERPOOL_TEAM_ID:

                    return event

    return None


# =========================================================
# MATCH STATUS
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

    state = type_data.get(
        "state",
        ""
    )

    name = type_data.get(
        "name",
        ""
    )

    detail = type_data.get(
        "detail",
        ""
    )

    return state, name, detail


# =========================================================
# MATCH TEAMS
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
# MATCH SCORE TEXT
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
# LIVE EVENT EXTRACTION
# =========================================================

def extract_live_events(event):

    result = []

    competitions = event.get(
        "competitions",
        []
    )

    if not competitions:
        return result

    competition = competitions[0]

    details = competition.get(
        "details",
        []
    )

    for detail in details:

        athlete = detail.get(
            "athlete",
            {}
        )

        player_name = athlete.get(
            "displayName",
            ""
        )

        type_data = detail.get(
            "type",
            {}
        )

        event_type = type_data.get(
            "text",
            ""
        ).lower()

        clock = detail.get(
            "clock",
            {}
        )

        display_value = clock.get(
            "displayValue",
            ""
        )

        if not display_value:

            display_value = (
                detail
                .get(
                    "clock",
                    {}
                )
                .get(
                    "value",
                    ""
                )
            )

        team = detail.get(
            "team",
            {}
        )

        team_id = str(
            team.get(
                "id",
                ""
            )
        )

        if team_id != LIVERPOOL_TEAM_ID:
            continue

        if "goal" in event_type:

            result.append({
                "type": "goal",
                "player": player_name,
                "minute": display_value,
                "raw": detail
            })

        elif "yellow" in event_type:

            result.append({
                "type": "yellow",
                "player": player_name,
                "minute": display_value,
                "raw": detail
            })

        elif "red" in event_type:

            result.append({
                "type": "red",
                "player": player_name,
                "minute": display_value,
                "raw": detail
            })

        elif (
            "substitution" in event_type
            or "substitute" in event_type
        ):

            result.append({
                "type": "substitution",
                "player": player_name,
                "minute": display_value,
                "raw": detail
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

    state, name, detail = get_match_status(
        event
    )

    if event_type == "start":

        message = (
            "🔴 <b>የጨዋታ መጀመሪያ</b>\n\n"
            f"⚽ {escape_html(score)}\n\n"
            f"▶️ {escape_html(detail or name)}\n\n"
            "<b>@yegnaLiverpool</b>"
        )

    elif event_type == "goal":

        message = (
            "⚽ <b>ጎል!</b>\n\n"
            f"{escape_html(score)}\n\n"
            f"{escape_html(extra_text)}\n\n"
            "<b>@yegnaLiverpool</b>"
        )

    elif event_type == "yellow":

        message = (
            "🟨 <b>ቢጫ ካርድ</b>\n\n"
            f"{escape_html(extra_text)}\n\n"
            f"{escape_html(score)}\n\n"
            "<b>@yegnaLiverpool</b>"
        )

    elif event_type == "red":

        message = (
            "🟥 <b>ቀይ ካርድ</b>\n\n"
            f"{escape_html(extra_text)}\n\n"
            f"{escape_html(score)}\n\n"
            "<b>@yegnaLiverpool</b>"
        )

    elif event_type == "substitution":

        message = (
            "🔄 <b>ቅያሬ</b>\n\n"
            f"{escape_html(extra_text)}\n\n"
            f"{escape_html(score)}\n\n"
            "<b>@yegnaLiverpool</b>"
        )

    elif event_type == "halftime":

        message = (
            "⏸️ <b>እረፍት</b>\n\n"
            f"⚽ {escape_html(score)}\n\n"
            "<b>@yegnaLiverpool</b>"
        )

    elif event_type == "fulltime":

        message = (
            "🏁 <b>ጨዋታው ተጠናቋል</b>\n\n"
            f"⚽ {escape_html(score)}\n\n"
            "<b>@yegnaLiverpool</b>"
        )

    else:

        message = (
            "⚽ <b>LIVE</b>\n\n"
            f"{escape_html(score)}\n\n"
            f"{escape_html(extra_text)}\n\n"
            "<b>@yegnaLiverpool</b>"
        )

    return message


# =========================================================
# PROCESS LIVE MATCH
# =========================================================

def process_live_match():

    logger.info(
        "Checking Liverpool LIVE match..."
    )

    data = get_liverpool_scoreboard()

    if not data:

        logger.warning(
            "No live data received."
        )

        return

    event = find_liverpool_match(
        data
    )

    if not event:

        logger.info(
            "No Liverpool match today."
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
        "Liverpool match found: %s",
        match_score_text(
            home,
            away
        )
    )

    # -----------------------------------------------------
    # PRE-MATCH
    # -----------------------------------------------------

    if state == "pre":

        status_key = (
            f"{event_id}|status|pre"
        )

        if not live_event_already_posted(
            status_key
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
                    status_key,
                    "start",
                    message
                )

    # -----------------------------------------------------
    # LIVE
    # -----------------------------------------------------

    elif state == "in":

        live_events = extract_live_events(
            event
        )

        for item in live_events:

            event_key = (
                f"{event_id}|"
                f"{item['type']}|"
                f"{item.get('player', '')}|"
                f"{item.get('minute', '')}"
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
        # SCORE / TIME UPDATE
        # -------------------------------------------------

        minute_key = (
            event
            .get(
                "status",
                {}
            )
            .get(
                "displayClock",
                ""
            )
        )

        if minute_key:

            update_key = (
                f"{event_id}|score|{minute_key}"
            )

            if not live_event_already_posted(
                update_key
            ):

                message = build_live_message(
                    event,
                    "live",
                    f"⏱️ የጨዋታ ሁኔታ፦ {minute_key}"
                )

                if telegram_send_message(
                    message
                ):

                    save_live_event(
                        update_key,
                        "score_update",
                        message
                    )

    # -----------------------------------------------------
    # FULL TIME
    # -----------------------------------------------------

    elif state == "post":

        fulltime_key = (
            f"{event_id}|fulltime"
        )

        if not live_event_already_posted(
            fulltime_key
        ):

            message = build_live_message(
                event,
                "fulltime"
            )

            if telegram_send_message(
                message
            ):

                save_live_event(
                    fulltime_key,
                    "fulltime",
                    message
                )

    else:

        logger.info(
            "Liverpool match state: %s",
            state
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

    logger.info(
        "Polling: DISABLED"
    )

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
    # LIVE MATCH
    # -----------------------------------------------------

    try:

        process_live_match()

    except Exception as e:

        logger.exception(
            "LIVE match check failed: %s",
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
