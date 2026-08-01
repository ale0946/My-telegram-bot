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
import tempfile

from difflib import SequenceMatcher
from urllib.parse import quote_plus, urljoin

from bs4 import BeautifulSoup
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

logger = logging.getLogger(__name__)


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

NEWS_CHECK_EVERY = 5 * 60
LIVE_CHECK_EVERY = 2 * 60

MAX_NEWS_PER_30_MIN = 2
MIN_NEWS_GAP = 15 * 60

SEEN_FILE = "seen_news_v3.json"
SENT_TIMES_FILE = "sent_times_v3.json"
LIVE_SEEN_FILE = "live_seen_v3.json"


# =========================================================
# TRUSTED SOURCES
# =========================================================

TRUSTED_SOURCES = {
    "Liverpool FC Official": [
        "Liverpool FC",
        "Liverpool Football Club",
        "Liverpoolfc.com",
        "Liverpoolfc"
    ],

    "Paul Joyce": [
        "Paul Joyce"
    ],

    "David Ornstein": [
        "David Ornstein"
    ],

    "James Pearce": [
        "James Pearce"
    ],

    "Lewis Steele": [
        "Lewis Steele"
    ],

    "Melissa Reddy": [
        "Melissa Reddy"
    ],

    "Fabrizio Romano": [
        "Fabrizio Romano"
    ]
}


# =========================================================
# SEARCHES
# =========================================================

SEARCHES = [

    '"Liverpool FC"',

    '"Liverpool" "Paul Joyce"',

    '"Liverpool" "David Ornstein"',

    '"Liverpool" "James Pearce"',

    '"Liverpool" "Lewis Steele"',

    '"Liverpool" "Melissa Reddy"',

    '"Liverpool" "Fabrizio Romano"',

    '"Liverpool FC" transfer',

    '"Liverpool FC" injury',

    '"Liverpool FC" manager',

    '"Liverpool FC" signing',

    '"Liverpool FC" contract',

    '"Liverpool FC" training',

    '"Liverpool FC" interview'
]


# =========================================================
# GLOBALS
# =========================================================

seen_news = set()
live_seen = set()
sent_times = []


# =========================================================
# CLIENTS
# =========================================================

bot = Bot(
    token=BOT_TOKEN
)

groq = Groq(
    api_key=GROQ_API_KEY
)


# =========================================================
# BASIC CHECK
# =========================================================

if not BOT_TOKEN:
    logger.error("BOT_TOKEN is missing.")

if not GROQ_API_KEY:
    logger.error("GROQ_API_KEY is missing.")


# =========================================================
# FILE HELPERS
# =========================================================

def load_json_list(filename):

    try:

        if not os.path.exists(filename):
            return []

        with open(
            filename,
            "r",
            encoding="utf-8"
        ) as file:

            data = json.load(file)

        if isinstance(data, list):
            return data

    except Exception as error:

        logger.error(
            "Load error %s: %s",
            filename,
            error
        )

    return []


def save_json_list(filename, data):

    try:

        with open(
            filename,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                list(data)[-5000:],
                file,
                ensure_ascii=False,
                indent=2
            )

    except Exception as error:

        logger.error(
            "Save error %s: %s",
            filename,
            error
        )


# =========================================================
# LOAD SAVED DATA
# =========================================================

seen_news = set(
    load_json_list(SEEN_FILE)
)

live_seen = set(
    load_json_list(LIVE_SEEN_FILE)
)


for value in load_json_list(
    SENT_TIMES_FILE
):

    try:

        timestamp = float(value)

        if (
            time.time() - timestamp
            < 30 * 60
        ):

            sent_times.append(
                timestamp
            )

    except Exception:

        continue


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

def similarity(first, second):

    first = normalize(first)
    second = normalize(second)

    if not first or not second:
        return 0.0

    return SequenceMatcher(
        None,
        first,
        second
    ).ratio()


# =========================================================
# LIVERPOOL FILTER
# =========================================================

def is_liverpool_news(
    title,
    summary
):

    text = (
        f"{title} {summary}"
    ).lower()

    keywords = [

        "liverpool",
        "liverpool fc",
        "lfc",
        "anfield",
        "reds",

        "arne slot",
        "andoni iraola",

        "mohamed salah",
        "virgil van dijk",
        "florian wirtz",
        "alexis mac allister",
        "ryan gravenberch",
        "dominik szoboszlai",
        "cody gakpo",
        "ibrahima konate",
        "andy robertson",
        "trent alexander-arnold",

        "giovanni leoni",
        "jeremy jacquet",
        "bradley barcola"
    ]

    return any(
        keyword in text
        for keyword in keywords
    )


# =========================================================
# TRUSTED SOURCE DETECTION
# =========================================================

def detect_source(
    title,
    summary,
    source_name
):

    text = (
        f"{title} "
        f"{summary} "
        f"{source_name}"
    ).lower()

    for trusted, aliases in TRUSTED_SOURCES.items():

        for alias in aliases:

            if alias.lower() in text:

                return trusted

    return None


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
                "Mozilla/5.0 LiverpoolNewsBot/10.0"
            }
        )

        response.raise_for_status()

        return feedparser.parse(
            response.content
        )

    except Exception as error:

        logger.error(
            "Google News RSS error: %s",
            error
        )

        return None


# =========================================================
# NEWS ID
# =========================================================

def make_news_id(
    title,
    summary,
    source
):

    normalized_title = normalize(
        title
    )

    normalized_summary = normalize(
        summary
    )

    normalized_source = normalize(
        source
    )

    value = (
        normalized_title
        + "|"
        + normalized_summary[:1000]
        + "|"
        + normalized_source
    )

    return hashlib.sha256(
        value.encode("utf-8")
    ).hexdigest()


# =========================================================
# FETCH NEWS
# =========================================================

def fetch_news_sync():

    collected = []

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

        for entry in feed.entries[:20]:

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

            trusted = detect_source(
                title,
                summary,
                source_name
            )

            if not trusted:

                logger.info(
                    "Rejected source: %s",
                    source_name
                )

                continue

            news_id = make_news_id(
                title,
                summary,
                trusted
            )

            if news_id in seen_news:
                continue

            collected.append({

                "id": news_id,

                "title": title,

                "summary": summary,

                "link": link,

                "source": trusted,

                "source_name": source_name,

                "entry": entry

            })

    return collected


async def fetch_news():

    return await asyncio.to_thread(
        fetch_news_sync
    )


# =========================================================
# REMOVE DUPLICATES
# =========================================================

def remove_duplicates(items):

    unique = []

    for item in items:

        duplicate = False

        for old in unique:

            title_score = similarity(
                item["title"],
                old["title"]
            )

            summary_score = similarity(
                item["summary"],
                old["summary"]
            )

            if title_score >= 0.60:

                duplicate = True
                break

            if (
                item["summary"]
                and old["summary"]
                and summary_score >= 0.72
            ):

                duplicate = True
                break

        if not duplicate:

            unique.append(item)

    return unique


# =========================================================
# AI OUTPUT CLEANING
# =========================================================

def remove_bad_format(text):

    if not text:
        return ""

    text = text.strip()

    text = re.sub(
        r"[=_\-]{4,}",
        "",
        text
    )

    text = re.sub(
        r"\n{3,}",
        "\n\n",
        text
    )

    return text.strip()


# =========================================================
# AMHARIC VALIDATION
# =========================================================

def amharic_ratio(text):

    if not text:
        return 0

    amharic = len(
        re.findall(
            r"[\u1200-\u137F]",
            text
        )
    )

    letters = len(
        re.findall(
            r"[A-Za-z\u1200-\u137F]",
            text
        )
    )

    if letters == 0:
        return 0

    return amharic / letters


def valid_amharic(text):

    if not text:
        return False

    text = remove_bad_format(
        text
    )

    ratio = amharic_ratio(
        text
    )

    if ratio < 0.55:

        logger.warning(
            "Amharic ratio too low: %.2f",
            ratio
        )

        return False

    english_words = re.findall(
        r"\b[A-Za-z]{6,}\b",
        text
    )

    if len(english_words) > 12:

        logger.warning(
            "Too many English words."
        )

        return False

    if re.search(
        r"[=_\-]{4,}",
        text
    ):

        return False

    return True


# =========================================================
# GROQ - AMHARIC NEWS WRITER
# =========================================================

def translate_news_sync(item):

    prompt = f"""
የLiverpool FC ዜና በተፈጥሯዊ የኢትዮጵያ
አማርኛ አዘጋጅ።

ይህ የTelegram የLiverpool ዜና ቻናል ነው።

አስፈላጊ ህጎች:

1. ርዕሱ በአማርኛ ብቻ ይሁን።

2. የዜናው ዋና ይዘት በአማርኛ ብቻ ይሁን።

3. English headline አትጻፍ።

4. English paragraph አትጻፍ።

5. የተጫዋች ስሞች፣ የክለብ ስሞች
   እና የስፖርት ስሞች English መቀረት ይችላሉ።

6. ቁጥሮች፣ ዋጋዎች፣ ቀኖች፣ ስሞች
   እና ዋና እውነታዎችን አትቀይር።

7. ከተሰጠው መረጃ ውጭ አትፍጠር።

8. የዝውውር ወሬ ከሆነ
   ወሬ መሆኑን ግልጽ አድርግ።

9. ዜናውን አታሳጥር።
   ዋና መረጃውን በበቂ ርዝመት አብራራ።

10. ተመሳሳይ ሀሳብን ደጋግመህ አትጻፍ።

11. Markdown አትጠቀም።

12. ===== ወይም ----- ወይም ____ ወይም
    ሌላ separator አትጠቀም።

13. የምንጩን ስም በዜናው ውስጥ አትጻፍ።
    ምንጩ ከAI መልስ ውጭ በPython ይጨመራል።

14. Link አትጻፍ።

15. "ርዕስ:"፣ "ዜና:"፣ "ምንጭ:"
    የሚሉ ምልክቶችን አትጨምር።

16. ከታች የተሰጠውን ይዘት ብቻ ተጠቀም።

17. የሰው ልጅ የጻፈው የስፖርት ዜና
    እንዲመስል ተፈጥሯዊ አማርኛ ተጠቀም።

18. የሚከተለውን JSON ብቻ መልስ:

{{
  "title": "የአማርኛ ርዕስ",
  "body": "ሙሉ የአማርኛ ዜና"
}}

ዋና ርዕስ:
{item["title"]}

ዋና ይዘት:
{item["summary"]}

ምንጭ:
{item["source"]}
"""

    try:

        result = groq.chat.completions.create(

            model="openai/gpt-oss-120b",

            messages=[

                {
                    "role": "system",
                    "content": (
                        "You are a professional Ethiopian "
                        "Amharic Liverpool FC sports journalist. "
                        "Return only valid JSON with title and body. "
                        "The title and body must be natural Ethiopian "
                        "Amharic. English is allowed only for proper "
                        "names such as Liverpool or player names."
                    )
                },

                {
                    "role": "user",
                    "content": prompt
                }

            ],

            temperature=0.05,

            max_tokens=1800,

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

        data = json.loads(
            raw
        )

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

            logger.error(
                "AI returned empty title/body."
            )

            return None

        combined = (
            title
            + "\n\n"
            + body
        )

        combined = remove_bad_format(
            combined
        )

        if not valid_amharic(
            combined
        ):

            logger.warning(
                "AI output failed Amharic validation."
            )

            return None

        return {
            "title": title,
            "body": body
        }

    except Exception as error:

        logger.error(
            "Groq translation error: %s",
            error
        )

        return None


async def translate_news(item):

    return await asyncio.to_thread(
        translate_news_sync,
        item
    )


# =========================================================
# IMAGE FROM RSS
# =========================================================

def get_rss_image(entry):

    try:

        media_content = getattr(
            entry,
            "media_content",
            []
        )

        if media_content:

            for media in media_content:

                url = media.get(
                    "url"
                )

                if url:
                    return url

        media_thumbnail = getattr(
            entry,
            "media_thumbnail",
            []
        )

        if media_thumbnail:

            for media in media_thumbnail:

                url = media.get(
                    "url"
                )

                if url:
                    return url

        enclosures = getattr(
            entry,
            "enclosures",
            []
        )

        if enclosures:

            for enclosure in enclosures:

                url = enclosure.get(
                    "href"
                )

                if url:

                    media_type = (
                        enclosure.get(
                            "type",
                            ""
                        )
                        .lower()
                    )

                    if (
                        media_type.startswith(
                            "image/"
                        )
                        or media_type == ""
                    ):

                        return url

    except Exception as error:

        logger.warning(
            "RSS image extraction error: %s",
            error
        )

    return None


# =========================================================
# IMAGE FROM ARTICLE PAGE
# =========================================================

def get_page_image(
    article_url
):

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

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        # og:image
        image = soup.find(
            "meta",
            property="og:image"
        )

        if image:

            image_url = image.get(
                "content"
            )

            if image_url:

                return urljoin(
                    response.url,
                    image_url
                )

        # twitter:image
        image = soup.find(
            "meta",
            attrs={
                "name": "twitter:image"
            }
        )

        if image:

            image_url = image.get(
                "content"
            )

            if image_url:

                return urljoin(
                    response.url,
                    image_url
                )

    except Exception as error:

        logger.warning(
            "Page image error: %s",
            error
        )

    return None


# =========================================================
# FIND IMAGE
# =========================================================

def get_image_url(item):

    # First try RSS image
    rss_image = get_rss_image(
        item.get(
            "entry"
        )
    )

    if rss_image:

        logger.info(
            "Image found from RSS."
        )

        return rss_image

    # Then try article page
    page_image = get_page_image(
        item.get(
            "link",
            ""
        )
    )

    if page_image:

        logger.info(
            "Image found from article page."
        )

        return page_image

    logger.info(
        "No article image found."
    )

    return None


# =========================================================
# DOWNLOAD IMAGE
# =========================================================

def download_image(
    image_url
):

    if not image_url:
        return None

    try:

        response = requests.get(
            image_url,
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

        if (
            "image" not in content_type
        ):

            return None

        extension = ".jpg"

        if "png" in content_type:
            extension = ".png"

        elif "webp" in content_type:
            extension = ".webp"

        elif "jpeg" in content_type:
            extension = ".jpg"

        temp_file = tempfile.NamedTemporaryFile(
            delete=False,
            suffix=extension
        )

        temp_file.write(
            response.content
        )

        temp_file.close()

        return temp_file.name

    except Exception as error:

        logger.warning(
            "Image download error: %s",
            error
        )

        return None


# =========================================================
# TELEGRAM MESSAGE
# =========================================================

def make_message(
    title,
    body,
    source
):

    title = remove_bad_format(
        title
    )

    body = remove_bad_format(
        body
    )

    safe_title = html.escape(
        title
    )

    safe_body = html.escape(
        body
    )

    safe_source = html.escape(
        source
    )

    return (
        "🔴 <b>LIVERPOOL NEWS</b>\n\n"
        f"<b>{safe_title}</b>\n\n"
        f"{safe_body}\n\n"
        f"📰 <b>ምንጭ:</b> {safe_source}\n\n"
        "🔴 <b>YN Liverpool</b>"
    )


# =========================================================
# SEND NEWS
# =========================================================

async def send_news(item):

    global sent_times

    logger.info(
        "Preparing news: %s",
        item["title"]
    )

    ai_news = await translate_news(
        item
    )

    if not ai_news:

        logger.error(
            "❌ Amharic translation failed."
        )

        return False

    title = ai_news["title"]
    body = ai_news["body"]

    message = make_message(
        title,
        body,
        item["source"]
    )

    image_url = await asyncio.to_thread(
        get_image_url,
        item
    )

    image_file = None

    if image_url:

        image_file = await asyncio.to_thread(
            download_image,
            image_url
        )

    try:

        # =================================================
        # SEND WITH PHOTO
        # =================================================

        if image_file:

            # Telegram photo caption limit is around 1024 chars.
            if len(message) <= 1000:

                try:

                    with open(
                        image_file,
                        "rb"
                    ) as photo:

                        await bot.send_photo(
                            chat_id=CHANNEL_ID,
                            photo=photo,
                            caption=message,
                            parse_mode=ParseMode.HTML
                        )

                    logger.info(
                        "✅ News + photo sent."
                    )

                except Exception as photo_error:

                    logger.warning(
                        "Photo send failed: %s",
                        photo_error
                    )

                    await bot.send_message(
                        chat_id=CHANNEL_ID,
                        text=message,
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=True
                    )

            else:

                # Photo first
                photo_caption = (
                    "🔴 <b>LIVERPOOL NEWS</b>\n\n"
                    f"<b>{html.escape(title)}</b>"
                )

                with open(
                    image_file,
                    "rb"
                ) as photo:

                    await bot.send_photo(
                        chat_id=CHANNEL_ID,
                        photo=photo,
                        caption=photo_caption,
                        parse_mode=ParseMode.HTML
                    )

                # Full news second
                await bot.send_message(
                    chat_id=CHANNEL_ID,
                    text=message,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True
                )

                logger.info(
                    "✅ Photo + full news sent."
                )

        # =================================================
        # NO PHOTO
        # =================================================

        else:

            await bot.send_message(
                chat_id=CHANNEL_ID,
                text=message,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True
            )

            logger.info(
                "✅ News sent without photo."
            )

        # =================================================
        # SAVE AS SEEN ONLY AFTER SUCCESS
        # =================================================

        seen_news.add(
            item["id"]
        )

        save_json_list(
            SEEN_FILE,
            seen_news
        )

        sent_times.append(
            time.time()
        )

        save_json_list(
            SENT_TIMES_FILE,
            sent_times
        )

        return True

    except Exception as error:

        logger.exception(
            "❌ Telegram send error: %s",
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
# NEWS SEND LIMIT
# =========================================================

def can_send_news():

    global sent_times

    now = time.time()

    sent_times = [
        timestamp
        for timestamp in sent_times
        if now - timestamp < 30 * 60
    ]

    save_json_list(
        SENT_TIMES_FILE,
        sent_times
    )

    if len(sent_times) >= MAX_NEWS_PER_30_MIN:

        logger.info(
            "30-minute limit reached: %s/%s",
            len(sent_times),
            MAX_NEWS_PER_30_MIN
        )

        return False

    if sent_times:

        elapsed = (
            now - sent_times[-1]
        )

        if elapsed < MIN_NEWS_GAP:

            remaining = (
                MIN_NEWS_GAP - elapsed
            )

            logger.info(
                "Waiting %.0f seconds.",
                remaining
            )

            return False

    return True


# =========================================================
# NEWS LOOP
# =========================================================

async def news_loop():

    while True:

        try:

            if not can_send_news():

                await asyncio.sleep(
                    NEWS_CHECK_EVERY
                )

                continue

            logger.info(
                "🔎 Checking Liverpool news..."
            )

            news = await fetch_news()

            if not news:

                logger.info(
                    "No new trusted news found."
                )

                await asyncio.sleep(
                    NEWS_CHECK_EVERY
                )

                continue

            news = remove_duplicates(
                news
            )

            logger.info(
                "Found %s unique articles.",
                len(news)
            )

            sent = False

            for item in news:

                if not can_send_news():
                    break

                success = await send_news(
                    item
                )

                if success:

                    sent = True

                    logger.info(
                        "✅ News successfully sent."
                    )

                    break

            if not sent:

                logger.info(
                    "No article was sent."
                )

            await asyncio.sleep(
                NEWS_CHECK_EVERY
            )

        except Exception as error:

            logger.exception(
                "❌ News loop error: %s",
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

    headers = {
        "x-apisports-key":
        FOOTBALL_API_KEY
    }

    try:

        response = requests.get(
            url,
            headers=headers,
            params=params or {},
            timeout=20
        )

        if response.status_code != 200:

            logger.error(
                "Football API status: %s",
                response.status_code
            )

            return None

        return response.json()

    except Exception as error:

        logger.error(
            "Football API error: %s",
            error
        )

        return None


# =========================================================
# LIVE MATCH
# =========================================================

async def send_live_matches():

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
            ""
        )

        away_name = away.get(
            "name",
            ""
        )

        home_score = goals.get(
            "home"
        )

        away_score = goals.get(
            "away"
        )

        status_data = fixture.get(
            "status",
            {}
        )

        status = status_data.get(
            "long",
            ""
        )

        minute = status_data.get(
            "elapsed"
        )

        fixture_id = fixture.get(
            "id"
        )

        live_key = (
            f"{fixture_id}|"
            f"{home_score}|"
            f"{away_score}|"
            f"{minute}|"
            f"{status}"
        )

        if live_key in live_seen:

            continue

        live_seen.add(
            live_key
        )

        if len(live_seen) > 2000:

            live_seen = set(
                list(live_seen)[-1000:]
            )

        save_json_list(
            LIVE_SEEN_FILE,
            live_seen
        )

        message = (
            "🔴 <b>LIVERPOOL LIVE</b>\n\n"
            f"⚽ {html.escape(home_name)} "
            f"{home_score if home_score is not None else 0}"
            " - "
            f"{away_score if away_score is not None else 0} "
            f"{html.escape(away_name)}\n\n"
        )

        if minute is not None:

            message += (
                f"⏱️ {minute}'\n"
            )

        message += (
            f"📌 {html.escape(status)}\n\n"
            "🔴 <b>YN Liverpool</b>"
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

            await send_live_matches()

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
        "========================================"
    )

    logger.info(
        "🔴 Liverpool Amharic News Bot"
    )

    logger.info(
        "✅ Bot started successfully"
    )

    logger.info(
        "📢 Channel: %s",
        CHANNEL_ID
    )

    logger.info(
        "🇪🇹 Amharic translation: ON"
    )

    logger.info(
        "🖼️ News photo: ON"
    )

    logger.info(
        "🔁 Duplicate protection: ON"
    )

    logger.info(
        "🔗 Telegram news links: OFF"
    )

    logger.info(
        "📰 News check: every 5 minutes"
    )

    logger.info(
        "⚽ Live check: every 2 minutes"
    )

    logger.info(
        "========================================"
    )

    await asyncio.gather(
        news_loop(),
        live_loop()
    )


# =========================================================
# RUN
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
