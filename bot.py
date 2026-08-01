import os
import re
import json
import time
import html
import hashlib
import asyncio
import logging
import requests
import feedparser

from difflib import SequenceMatcher
from urllib.parse import quote_plus, urljoin

from groq import Groq
from telegram import Bot
from telegram.constants import ParseMode


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("LiverpoolBot")


# =========================================================
# ENVIRONMENT
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
FOOTBALL_API_KEY = os.getenv("FOOTBALL_API_KEY", "").strip()

CHANNEL_ID = os.getenv(
    "CHANNEL_ID",
    "@yegnaLiverpool"
).strip()

LIVERPOOL_TEAM_ID = 40


# =========================================================
# SETTINGS
# =========================================================

NEWS_CHECK_EVERY = 10 * 60
LIVE_CHECK_EVERY = 60

# Do not accept news older than 60 minutes
MAX_NEWS_AGE = 60 * 60

SEEN_FILE = "seen_news.json"
LIVE_FILE = "live_seen.json"


# =========================================================
# ONLY 5 TRUSTED SOURCES
# =========================================================

TRUSTED_REPORTERS = [
    "Paul Joyce",
    "David Ornstein",
    "James Pearce",
    "Fabrizio Romano"
]

OFFICIAL_ALIASES = [
    "Liverpool FC",
    "Liverpool Football Club",
    "Liverpoolfc.com",
    "Liverpoolfc"
]


# =========================================================
# SEARCHES
# =========================================================

SEARCHES = [

    # Liverpool official
    'site:liverpoolfc.com Liverpool',

    # Four trusted journalists
    '"Liverpool" "Paul Joyce"',
    '"Liverpool" "David Ornstein"',
    '"Liverpool" "James Pearce"',
    '"Liverpool" "Fabrizio Romano"'
]


# =========================================================
# GLOBALS
# =========================================================

seen_news = set()
live_seen = set()


# =========================================================
# CLIENTS
# =========================================================

bot = None
groq = None

if BOT_TOKEN:
    bot = Bot(token=BOT_TOKEN)

if GROQ_API_KEY:
    groq = Groq(api_key=GROQ_API_KEY)


# =========================================================
# FILE FUNCTIONS
# =========================================================

def load_list(filename):

    try:

        if not os.path.exists(filename):
            return []

        with open(
            filename,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

        if isinstance(data, list):
            return data

    except Exception as error:

        logger.warning(
            "Could not load %s: %s",
            filename,
            error
        )

    return []


def save_list(filename, values):

    try:

        with open(
            filename,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                list(values)[-5000:],
                f,
                ensure_ascii=False,
                indent=2
            )

    except Exception as error:

        logger.warning(
            "Could not save %s: %s",
            filename,
            error
        )


seen_news = set(
    load_list(SEEN_FILE)
)

live_seen = set(
    load_list(LIVE_FILE)
)


# =========================================================
# TEXT CLEANING
# =========================================================

def clean_text(text):

    if not text:
        return ""

    text = html.unescape(
        str(text)
    )

    text = re.sub(
        r"<[^>]+>",
        " ",
        text
    )

    text = re.sub(
        r"[\r\n\t]+",
        " ",
        text
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
        r"[^a-z0-9\u1200-\u137f ]",
        " ",
        text
    )

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


# =========================================================
# SIMILARITY
# =========================================================

def similarity(a, b):

    a = normalize(a)
    b = normalize(b)

    if not a or not b:
        return 0

    return SequenceMatcher(
        None,
        a,
        b
    ).ratio()


# =========================================================
# LIVERPOOL FILTER
# =========================================================

def is_liverpool_news(title, summary):

    text = (
        f"{title} {summary}"
    ).lower()

    keywords = [

        "liverpool",
        "lfc",
        "anfield",
        "reds",

        "mohamed salah",
        "virgil van dijk",
        "florian wirtz",
        "alexis mac allister",
        "ryan gravenberch",
        "dominik szoboszlai",
        "cody gakpo",
        "ibrahima konate",
        "alisson",

        "giovanni leoni",
        "jeremy jacquet",
        "bradley barcola",

        "arne slot",
        "andoni iraola"
    ]

    return any(
        keyword in text
        for keyword in keywords
    )


# =========================================================
# TRUSTED SOURCE DETECTION
# =========================================================

def detect_source(title, summary, source_name):

    combined = (
        f"{title} {summary} {source_name}"
    ).lower()

    # Liverpool Official
    for alias in OFFICIAL_ALIASES:

        if alias.lower() in source_name.lower():

            return "Liverpool FC Official"

    # Trusted journalists
    for reporter in TRUSTED_REPORTERS:

        if reporter.lower() in combined:

            return reporter

    return None


# =========================================================
# DATE
# =========================================================

def get_timestamp(entry):

    for field in [
        "published_parsed",
        "updated_parsed"
    ]:

        parsed = getattr(
            entry,
            field,
            None
        )

        if parsed:

            try:

                return time.mktime(
                    parsed
                )

            except Exception:
                pass

    return None


def is_fresh(entry):

    timestamp = get_timestamp(
        entry
    )

    if timestamp is None:
        return False

    age = time.time() - timestamp

    if age < -600:
        return False

    if age > MAX_NEWS_AGE:
        return False

    return True


# =========================================================
# GOOGLE NEWS RSS
# =========================================================

def get_google_news(query):

    url = (
        "https://news.google.com/rss/search?"
        f"q={quote_plus(query)}"
        "&hl=en-US"
        "&gl=US"
        "&ceid=US:en"
    )

    try:

        response = requests.get(
            url,
            timeout=20,
            headers={
                "User-Agent":
                "Mozilla/5.0 LiverpoolBot"
            }
        )

        response.raise_for_status()

        return feedparser.parse(
            response.content
        )

    except Exception as error:

        logger.warning(
            "RSS error: %s",
            error
        )

        return None


# =========================================================
# NEWS ID
# =========================================================

def news_id(title, summary, source):

    value = (
        normalize(title)
        + "|"
        + normalize(summary)[:1000]
        + "|"
        + normalize(source)
    )

    return hashlib.sha256(
        value.encode("utf-8")
    ).hexdigest()


# =========================================================
# FETCH NEWS
# =========================================================

def fetch_news_sync():

    results = []

    for query in SEARCHES:

        logger.info(
            "Searching: %s",
            query
        )

        feed = get_google_news(
            query
        )

        if not feed:
            continue

        for entry in feed.entries[:15]:

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

            source_obj = getattr(
                entry,
                "source",
                None
            )

            source_name = ""

            if source_obj:

                source_name = clean_text(
                    getattr(
                        source_obj,
                        "title",
                        ""
                    )
                )

            if not title or not link:
                continue

            # Liverpool only
            if not is_liverpool_news(
                title,
                summary
            ):
                continue

            # Fresh news only
            if not is_fresh(entry):
                continue

            # Trusted source only
            source = detect_source(
                title,
                summary,
                source_name
            )

            if not source:
                continue

            item_id = news_id(
                title,
                summary,
                source
            )

            if item_id in seen_news:
                continue

            results.append({

                "id": item_id,

                "title": title,

                "summary": summary,

                "link": link,

                "source": source,

                "source_name": source_name,

                "published_at":
                    get_timestamp(entry)

            })

    # Newest first
    results.sort(
        key=lambda item:
        item.get("published_at", 0),
        reverse=True
    )

    return results


async def fetch_news():

    return await asyncio.to_thread(
        fetch_news_sync
    )


# =========================================================
# DUPLICATE NEWS FILTER
# =========================================================

def remove_duplicates(items):

    unique = []

    for item in items:

        duplicate = False

        for old in unique:

            if similarity(
                item["title"],
                old["title"]
            ) >= 0.60:

                duplicate = True
                break

            if (
                item["summary"]
                and old["summary"]
                and similarity(
                    item["summary"],
                    old["summary"]
                ) >= 0.75
            ):

                duplicate = True
                break

        if not duplicate:
            unique.append(item)

    return unique


# =========================================================
# SHORT AMHARIC AI REPORT
# =========================================================

def translate_news_sync(item):

    if not groq:
        return None

    prompt = f"""
በታች የተሰጠውን Liverpool FC ዜና
ወደ ተፈጥሯዊ፣ ትክክለኛ እና ግልጽ
የኢትዮጵያ አማርኛ ቀይር።

ዋና ደንቦች:

1. ዜናውን አጭር አድርግ።
2. ዋናውን እውነታ ብቻ አስቀምጥ።
3. ከምንጩ ውጭ መረጃ አትጨምር።
4. ስም፣ ቁጥር፣ ዋጋ እና ቀን ካለ በትክክል ጠብቅ።
5. ወሬ ከሆነ እንደ ወሬ አቅርብ።
6. English headline አታስገባ።
7. English paragraph አታስገባ።
8. "LIVERPOOL NEWS" አታስገባ።
9. "ምንጭ" አትጻፍ።
10. @yegnaLiverpool አትጻፍ።
11. የተጫዋች ስም ካልተጠቀሰ አትጨምር።
12. የተሰጠውን ዜና አትድገም።

የምትመልሰው JSON ብቻ ይሁን:

{{
  "title": "አጭር የአማርኛ ርዕስ",
  "body": "አጭር የአማርኛ ዜና"
}}

TITLE:
{item["title"]}

CONTENT:
{item["summary"]}
"""

    try:

        result = groq.chat.completions.create(

            model="openai/gpt-oss-120b",

            messages=[

                {
                    "role": "system",
                    "content": (
                        "Write only a short, accurate "
                        "Amharic Liverpool FC news report. "
                        "Never invent facts."
                    )
                },

                {
                    "role": "user",
                    "content": prompt
                }
            ],

            temperature=0.05,

            max_tokens=900,

            response_format={
                "type": "json_object"
            }
        )

        raw = (
            result
            .choices[0]
            .message
            .content
            .strip()
        )

        data = json.loads(raw)

        title = clean_text(
            data.get("title", "")
        )

        body = clean_text(
            data.get("body", "")
        )

        if not title or not body:
            return None

        # Must contain Amharic
        if not re.search(
            r"[\u1200-\u137F]",
            title + body
        ):
            return None

        return {
            "title": title,
            "body": body
        }

    except Exception as error:

        logger.error(
            "Groq error: %s",
            error
        )

        return None


async def translate_news(item):

    return await asyncio.to_thread(
        translate_news_sync,
        item
    )


# =========================================================
# GET ORIGINAL ARTICLE IMAGE
# IMPORTANT:
# We DO NOT use Google News image.
# We open the original article and get og:image.
# =========================================================

def get_original_image(article_url):

    if not article_url:
        return None

    try:

        response = requests.get(
            article_url,
            timeout=20,
            headers={
                "User-Agent":
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "Chrome/130 Safari/537.36"
            },
            allow_redirects=True
        )

        if response.status_code != 200:
            return None

        final_url = response.url

        content_type = (
            response.headers
            .get(
                "content-type",
                ""
            )
            .lower()
        )

        if "text/html" not in content_type:
            return None

        page = response.text

        # og:image
        patterns = [

            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',

            r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image["\']'
        ]

        for pattern in patterns:

            match = re.search(
                pattern,
                page,
                re.IGNORECASE
            )

            if match:

                image_url = html.unescape(
                    match.group(1)
                ).strip()

                return urljoin(
                    final_url,
                    image_url
                )

    except Exception as error:

        logger.warning(
            "Original image error: %s",
            error
        )

    return None


# =========================================================
# DOWNLOAD IMAGE
# =========================================================

def download_image(url):

    if not url:
        return None

    try:

        response = requests.get(
            url,
            timeout=20,
            headers={
                "User-Agent":
                "Mozilla/5.0"
            }
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

        if "image" not in content_type:
            return None

        extension = ".jpg"

        if "png" in content_type:
            extension = ".png"

        elif "webp" in content_type:
            extension = ".webp"

        filename = (
            "news_"
            + hashlib.md5(
                url.encode()
            ).hexdigest()
            + extension
        )

        path = os.path.join(
            "/tmp",
            filename
        )

        with open(
            path,
            "wb"
        ) as f:

            f.write(
                response.content
            )

        return path

    except Exception as error:

        logger.warning(
            "Image download error: %s",
            error
        )

        return None


# =========================================================
# LINE-UP DETECTION
# =========================================================

def is_lineup_news(item):

    # ONLY official Liverpool source
    if item["source"] != "Liverpool FC Official":
        return False

    text = (
        item["title"]
        + " "
        + item["summary"]
    ).lower()

    lineup_words = [

        "lineup",
        "line-up",
        "starting xi",
        "starting 11",
        "starting eleven",
        "team news",
        "confirmed team",
        "teamsheet",
        "team sheet"
    ]

    return any(
        word in text
        for word in lineup_words
    )


# =========================================================
# TELEGRAM NEWS FORMAT
# =========================================================

def make_news_message(
    title,
    body,
    source
):

    return (
        f"<b>{html.escape(title)}</b>\n\n"
        f"{html.escape(body)}\n\n"
        f"<b>{html.escape(source)}</b>\n\n"
        "🔴 <b>@yegnaLiverpool</b>"
    )


# =========================================================
# SEND NEWS
# =========================================================

async def send_news(item):

    ai = await translate_news(
        item
    )

    if not ai:

        logger.warning(
            "AI translation failed: %s",
            item["title"]
        )

        return False

    title = ai["title"]
    body = ai["body"]

    message = make_news_message(
        title,
        body,
        item["source"]
    )

    # Always use ORIGINAL ARTICLE image.
    image_url = await asyncio.to_thread(
        get_original_image,
        item["link"]
    )

    image_file = None

    if image_url:

        image_file = await asyncio.to_thread(
            download_image,
            image_url
        )

    try:

        if image_file:

            with open(
                image_file,
                "rb"
            ) as photo:

                # Telegram photo caption limit
                if len(message) <= 1000:

                    await bot.send_photo(
                        chat_id=CHANNEL_ID,
                        photo=photo,
                        caption=message,
                        parse_mode=ParseMode.HTML
                    )

                else:

                    await bot.send_photo(
                        chat_id=CHANNEL_ID,
                        photo=photo,
                        caption=(
                            f"<b>{html.escape(title)}</b>"
                        ),
                        parse_mode=ParseMode.HTML
                    )

                    await bot.send_message(
                        chat_id=CHANNEL_ID,
                        text=message,
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=True
                    )

        else:

            await bot.send_message(
                chat_id=CHANNEL_ID,
                text=message,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True
            )

        # Save only after successful Telegram send
        seen_news.add(
            item["id"]
        )

        save_list(
            SEEN_FILE,
            seen_news
        )

        logger.info(
            "✅ News sent: %s",
            title
        )

        return True

    except Exception as error:

        logger.exception(
            "Telegram news error: %s",
            error
        )

        return False

    finally:

        if image_file:

            try:

                if os.path.exists(
                    image_file
                ):

                    os.remove(
                        image_file
                    )

            except Exception:
                pass


# =========================================================
# NEWS LOOP
# =========================================================

async def news_loop():

    while True:

        try:

            logger.info(
                "🔎 Checking trusted Liverpool news..."
            )

            news = await fetch_news()

            news = remove_duplicates(
                news
            )

            if news:

                # Send newest article only
                item = news[0]

                await send_news(
                    item
                )

            else:

                logger.info(
                    "No new trusted Liverpool news."
                )

        except Exception as error:

            logger.exception(
                "News loop error: %s",
                error
            )

        await asyncio.sleep(
            NEWS_CHECK_EVERY
        )


# =========================================================
# FOOTBALL API
# =========================================================

def football_request(
    endpoint,
    params=None
):

    if not FOOTBALL_API_KEY:
        return None

    url = (
        "https://v3.football.api-sports.io/"
        + endpoint
    )

    try:

        response = requests.get(
            url,
            headers={
                "x-apisports-key":
                FOOTBALL_API_KEY
            },
            params=params or {},
            timeout=20
        )

        if response.status_code != 200:

            logger.warning(
                "Football API status: %s",
                response.status_code
            )

            return None

        return response.json()

    except Exception as error:

        logger.warning(
            "Football API error: %s",
            error
        )

        return None


# =========================================================
# LIVE MATCH
# =========================================================

async def check_live_match():

    if not FOOTBALL_API_KEY:
        return

    data = await asyncio.to_thread(
        football_request,
        "fixtures",
        {
            "team": LIVERPOOL_TEAM_ID,
            "live": "all"
        }
    )

    if not data:
        return

    fixtures = data.get(
        "response",
        []
    )

    for game in fixtures:

        fixture = game.get(
            "fixture",
            {}
        )

        teams = game.get(
            "teams",
            {}
        )

        goals = game.get(
            "goals",
            {}
        )

        home = teams.get(
            "home",
            {}
        )

        away = teams.get(
            "away",
            {}
        )

        home_name = home.get(
            "name",
            "Home"
        )

        away_name = away.get(
            "name",
            "Away"
        )

        home_score = goals.get(
            "home"
        )

        away_score = goals.get(
            "away"
        )

        status = fixture.get(
            "status",
            {}
        )

        minute = status.get(
            "elapsed"
        )

        short_status = status.get(
            "short",
            ""
        )

        fixture_id = fixture.get(
            "id"
        )

        if not fixture_id:
            continue

        # Detect score/status changes
        state_key = (
            f"{fixture_id}|"
            f"{home_score}|"
            f"{away_score}|"
            f"{short_status}"
        )

        if state_key in live_seen:
            continue

        live_seen.add(
            state_key
        )

        save_list(
            LIVE_FILE,
            live_seen
        )

        # Goal alert
        old_score_keys = [
            key
            for key in live_seen
            if key.startswith(
                f"{fixture_id}|"
            )
        ]

        is_goal = len(
            old_score_keys
        ) > 1

        if is_goal:

            message = (
                "🔴 <b>LIVERPOOL GOAL!</b>\n\n"
                f"⚽ <b>{html.escape(home_name)}</b> "
                f"{home_score or 0} - "
                f"{away_score or 0} "
                f"<b>{html.escape(away_name)}</b>\n\n"
                f"⏱️ {minute or ''}'\n\n"
                "🔴 <b>@yegnaLiverpool</b>"
            )

        else:

            message = (
                "🔴 <b>LIVERPOOL LIVE</b>\n\n"
                f"⚽ <b>{html.escape(home_name)}</b> "
                f"{home_score or 0} - "
                f"{away_score or 0} "
                f"<b>{html.escape(away_name)}</b>\n\n"
                f"⏱️ {minute or ''}'\n\n"
                "🔴 <b>@yegnaLiverpool</b>"
            )

        try:

            await bot.send_message(
                chat_id=CHANNEL_ID,
                text=message,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True
            )

            logger.info(
                "⚽ Live update sent."
            )

        except Exception as error:

            logger.error(
                "Live Telegram error: %s",
                error
            )


# =========================================================
# LIVE LOOP
# =========================================================

async def live_loop():

    while True:

        try:

            await check_live_match()

        except Exception as error:

            logger.exception(
                "Live loop error: %s",
                error
            )

        await asyncio.sleep(
            LIVE_CHECK_EVERY
        )


# =========================================================
# MAIN
# =========================================================

async def main():

    if not BOT_TOKEN:

        logger.error(
            "❌ BOT_TOKEN is missing."
        )

        return

    if not GROQ_API_KEY:

        logger.error(
            "❌ GROQ_API_KEY is missing."
        )

        return

    logger.info(
        "======================================"
    )

    logger.info(
        "🔴 YN Liverpool Bot"
    )

    logger.info(
        "✅ Bot started"
    )

    logger.info(
        "📰 5 trusted sources only"
    )

    logger.info(
        "🇪🇹 Short Amharic news"
    )

    logger.info(
        "🖼️ Original article images only"
    )

    logger.info(
        "⚽ Liverpool live match updates"
    )

    logger.info(
        "======================================"
    )

    await asyncio.gather(
        news_loop(),
        live_loop()
    )


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    try:

        asyncio.run(
            main()
        )

    except KeyboardInterrupt:

        logger.info(
            "🛑 Bot stopped."
        )

    except Exception as error:

        logger.exception(
            "Fatal error: %s",
            error
        )
