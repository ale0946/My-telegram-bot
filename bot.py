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

MAX_NEWS_AGE = 60 * 60

SEEN_FILE = "seen_news.json"
LIVE_FILE = "live_seen.json"


# =========================================================
# TRUSTED SOURCES
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

    'site:liverpoolfc.com Liverpool',

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

bot = None
groq = None


# =========================================================
# CLIENTS
# =========================================================

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

    source_lower = source_name.lower()

    # Official Liverpool
    for alias in OFFICIAL_ALIASES:

        if alias.lower() in source_lower:

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

            if not is_liverpool_news(
                title,
                summary
            ):
                continue

            if not is_fresh(entry):
                continue

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

    results.sort(
        key=lambda item:
        item.get(
            "published_at",
            0
        ),
        reverse=True
    )

    return results


async def fetch_news():

    return await asyncio.to_thread(
        fetch_news_sync
    )


# =========================================================
# DUPLICATE FILTER
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
# AMHARIC AI
# =========================================================

def translate_news_sync(item):

    if not groq:
        return None

    prompt = f"""
የሚከተለውን የLiverpool FC ዜና
ወደ ተፈጥሯዊ እና ግልጽ አማርኛ ቀይር።

ደንቦች:

- አጭር አድርግ።
- ዋናውን እውነታ ብቻ አስቀምጥ።
- ከተሰጠው መረጃ ውጭ ነገር አትፍጠር።
- ስሞችን፣ ቁጥሮችን፣ ዋጋዎችን እና ቀኖችን በትክክል ጠብቅ።
- ወሬ ከሆነ ወሬ መሆኑን ጠብቅ።
- English headline አታስገባ።
- English paragraph አታስገባ።
- "LIVERPOOL NEWS" አትጻፍ።
- "ምንጭ" አትጻፍ።
- @yegnaLiverpool አትጻፍ።
- የሌለውን ተጫዋች አትጨምር።

JSON ብቻ መልስ:

{{
    "title": "የአማርኛ ርዕስ",
    "body": "የአማርኛ ዜና"
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
                    "content":
                    "Write accurate short Amharic Liverpool FC news."
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
            data.get(
                "title",
                ""
            )
        )

        body = clean_text(
            data.get(
                "body",
                ""
            )
        )

        if not title or not body:
            return None

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
# ARTICLE URL RESOLVER
# =========================================================

def resolve_article_url(url):

    if not url:
        return None

    try:

        response = requests.get(
            url,
            timeout=20,
            headers={
                "User-Agent":
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "Chrome/130 Safari/537.36"
            },
            allow_redirects=True
        )

        if response.status_code != 200:
            return None

        return response.url

    except Exception as error:

        logger.warning(
            "URL resolve error: %s",
            error
        )

        return None


# =========================================================
# IMAGE URL CLEANER
# =========================================================

def clean_image_url(url, base_url):

    if not url:
        return None

    url = html.unescape(
        url
    ).strip()

    url = url.replace(
        "\\/",
        "/"
    )

    if url.startswith("//"):

        url = "https:" + url

    return urljoin(
        base_url,
        url
    )


# =========================================================
# FIND IMAGE IN JSON-LD
# =========================================================

def find_jsonld_images(page, base_url):

    images = []

    scripts = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>'
        r'(.*?)'
        r'</script>',
        page,
        re.IGNORECASE | re.DOTALL
    )

    for script in scripts:

        try:

            data = json.loads(
                html.unescape(
                    script.strip()
                )
            )

        except Exception:
            continue

        objects = []

        if isinstance(data, dict):

            objects.append(data)

            graph = data.get(
                "@graph"
            )

            if isinstance(
                graph,
                list
            ):

                objects.extend(
                    graph
                )

        elif isinstance(
            data,
            list
        ):

            objects.extend(
                data
            )

        for obj in objects:

            if not isinstance(
                obj,
                dict
            ):
                continue

            image = obj.get(
                "image"
            )

            if isinstance(
                image,
                str
            ):

                images.append(
                    clean_image_url(
                        image,
                        base_url
                    )
                )

            elif isinstance(
                image,
                dict
            ):

                value = image.get(
                    "url"
                )

                if value:

                    images.append(
                        clean_image_url(
                            value,
                            base_url
                        )
                    )

            elif isinstance(
                image,
                list
            ):

                for value in image:

                    if isinstance(
                        value,
                        str
                    ):

                        images.append(
                            clean_image_url(
                                value,
                                base_url
                            )
                        )

                    elif isinstance(
                        value,
                        dict
                    ):

                        value = value.get(
                            "url"
                        )

                        if value:

                            images.append(
                                clean_image_url(
                                    value,
                                    base_url
                                )
                            )

    return [
        image
        for image in images
        if image
    ]


# =========================================================
# FIND ARTICLE IMAGES
# =========================================================

def find_article_images(page, base_url):

    images = []

    # -----------------------------------------------------
    # 1. OG IMAGE
    # -----------------------------------------------------

    patterns = [

        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',

        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',

        r'<meta[^>]+property=["\']og:image:url["\'][^>]+content=["\']([^"\']+)',

        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image:url["\']'
    ]

    for pattern in patterns:

        matches = re.findall(
            pattern,
            page,
            re.IGNORECASE
        )

        for match in matches:

            image = clean_image_url(
                match,
                base_url
            )

            if image:
                images.append(
                    image
                )


    # -----------------------------------------------------
    # 2. TWITTER IMAGE
    # -----------------------------------------------------

    twitter_patterns = [

        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)',

        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image["\']',

        r'<meta[^>]+property=["\']twitter:image["\'][^>]+content=["\']([^"\']+)'
    ]

    for pattern in twitter_patterns:

        matches = re.findall(
            pattern,
            page,
            re.IGNORECASE
        )

        for match in matches:

            image = clean_image_url(
                match,
                base_url
            )

            if image:
                images.append(
                    image
                )


    # -----------------------------------------------------
    # 3. JSON-LD ARTICLE IMAGE
    # -----------------------------------------------------

    images.extend(
        find_jsonld_images(
            page,
            base_url
        )
    )


    # -----------------------------------------------------
    # 4. ARTICLE / MAIN IMAGE
    # -----------------------------------------------------

    article_patterns = [

        r'<article[^>]*>.*?<img[^>]+src=["\']([^"\']+)',

        r'<main[^>]*>.*?<img[^>]+src=["\']([^"\']+)',

        r'<img[^>]+class=["\'][^"\']*(?:featured|hero|article|post)[^"\']*["\'][^>]+src=["\']([^"\']+)',

        r'<img[^>]+src=["\']([^"\']+)[^>]+class=["\'][^"\']*(?:featured|hero|article|post)[^"\']*["\']'
    ]

    for pattern in article_patterns:

        matches = re.findall(
            pattern,
            page,
            re.IGNORECASE | re.DOTALL
        )

        for match in matches[:5]:

            image = clean_image_url(
                match,
                base_url
            )

            if image:
                images.append(
                    image
                )


    # -----------------------------------------------------
    # REMOVE DUPLICATES
    # -----------------------------------------------------

    unique = []

    for image in images:

        if image not in unique:

            unique.append(
                image
            )

    return unique


# =========================================================
# CHECK IMAGE URL
# =========================================================

def check_image_url(image_url):

    if not image_url:
        return False

    try:

        response = requests.get(
            image_url,
            timeout=15,
            headers={
                "User-Agent":
                "Mozilla/5.0"
            },
            stream=True
        )

        content_type = (
            response.headers
            .get(
                "content-type",
                ""
            )
            .lower()
        )

        return (
            response.status_code == 200
            and "image/" in content_type
        )

    except Exception:

        return False


# =========================================================
# GET ARTICLE IMAGE
#
# IMPORTANT:
# This function takes the image FROM THE ORIGINAL ARTICLE.
# It does NOT use Google News thumbnail.
# =========================================================

def get_original_image(article_url):

    if not article_url:
        return None

    try:

        final_url = resolve_article_url(
            article_url
        )

        if not final_url:
            final_url = article_url

        logger.info(
            "Original article: %s",
            final_url
        )

        response = requests.get(
            final_url,
            timeout=25,
            headers={
                "User-Agent":
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "Chrome/130 Safari/537.36",
                "Accept":
                "text/html,application/xhtml+xml"
            },
            allow_redirects=True
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
            "text/html" not in content_type
            and "application/xhtml" not in content_type
        ):
            return None

        page = response.text

        images = find_article_images(
            page,
            response.url
        )

        if not images:

            logger.warning(
                "No article image found."
            )

            return None

        # -------------------------------------------------
        # Try each candidate.
        # First valid article image wins.
        # -------------------------------------------------

        for image_url in images:

            if check_image_url(
                image_url
            ):

                logger.info(
                    "🖼️ Article image found: %s",
                    image_url
                )

                return image_url

        logger.warning(
            "Article images found, but none were downloadable."
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
            timeout=25,
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

        elif "jpeg" in content_type:

            extension = ".jpg"

        filename = (
            "liverpool_news_"
            + hashlib.md5(
                url.encode(
                    "utf-8"
                )
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
# TELEGRAM FORMAT
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

    if not bot:
        return False

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

    # -----------------------------------------------------
    # GET IMAGE FROM ORIGINAL ARTICLE
    # -----------------------------------------------------

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

        # -------------------------------------------------
        # WITH ARTICLE IMAGE
        # -------------------------------------------------

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

        # -------------------------------------------------
        # NO IMAGE
        # -------------------------------------------------

        else:

            logger.warning(
                "⚠️ No matching article image. "
                "Sending text only."
            )

            await bot.send_message(
                chat_id=CHANNEL_ID,
                text=message,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True
            )

        # Save only AFTER successful send

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

        state_key = (
            f"{fixture_id}|"
            f"{home_score}|"
            f"{away_score}|"
            f"{short_status}"
        )

        if state_key in live_seen:
            continue

        # Check whether a previous state existed
        previous_states = [
            key
            for key in live_seen
            if key.startswith(
                f"{fixture_id}|"
            )
        ]

        is_goal = len(
            previous_states
        ) > 0 and (
            home_score is not None
            or away_score is not None
        )

        live_seen.add(
            state_key
        )

        save_list(
            LIVE_FILE,
            live_seen
        )

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
        "📰 Trusted Liverpool sources"
    )

    logger.info(
        "🇪🇹 Amharic news"
    )

    logger.info(
        "🖼️ Matching original article images"
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
