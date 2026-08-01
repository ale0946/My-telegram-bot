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
from urllib.parse import quote_plus, urljoin, urlparse

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

NEWS_CHECK_EVERY = 15 * 60
LIVE_CHECK_EVERY = 2 * 60

# News must be less than 45 minutes old
MAX_NEWS_AGE = 45 * 60

# Maximum 2 news posts in 30 minutes
MAX_NEWS_PER_30_MIN = 2

# Minimum 15 minutes between posts
MIN_NEWS_GAP = 15 * 60

SEEN_FILE = "seen_news_v5.json"
SENT_TIMES_FILE = "sent_times_v5.json"
LIVE_SEEN_FILE = "live_seen_v5.json"


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
    '"Liverpool FC" signing',
    '"Liverpool FC" deal',
    '"Liverpool FC" agreement',
    '"Liverpool FC" bid',

    '"Liverpool FC" injury',
    '"Liverpool FC" injured',
    '"Liverpool FC" returns',
    '"Liverpool FC" unavailable',

    '"Liverpool FC" manager',
    '"Liverpool FC" contract',
    '"Liverpool FC" official',

    '"Liverpool FC" Champions League',
    '"Liverpool FC" Premier League',

    '"Liverpool FC" result',
    '"Liverpool FC" match'
]


# =========================================================
# IMPORTANT NEWS KEYWORDS
# =========================================================

IMPORTANT_KEYWORDS = [

    "transfer",
    "signing",
    "signed",
    "signs",
    "deal",
    "agreement",
    "agreed",
    "bid",
    "offer",
    "fee",
    "medical",
    "contract",
    "renew",
    "renewal",
    "departure",
    "leaves",
    "exit",
    "joins",
    "join",

    "injury",
    "injured",
    "fitness",
    "fit",
    "returns",
    "return",
    "ruled out",
    "unavailable",
    "doubt",
    "sidelined",
    "surgery",

    "manager",
    "coach",
    "appointed",
    "appointment",
    "official",
    "confirmed",
    "confirmation",

    "champions league",
    "premier league",
    "fa cup",
    "league cup",
    "carabao cup",
    "draw",
    "fixture",

    "match",
    "win",
    "won",
    "loss",
    "lost",
    "defeat",
    "score",
    "goal",

    "salah",
    "van dijk",
    "wirtz",
    "mac allister",
    "szoboszlai",
    "alisson",
    "gakpo",
    "konate",
    "gravenberch",
    "jacquet",
    "leoni",
    "bajcetic",
    "endo"
]


# =========================================================
# LOW IMPORTANCE
# =========================================================

LOW_IMPORTANCE_KEYWORDS = [

    "visit",
    "visited",
    "photos",
    "gallery",
    "behind the scenes",
    "training",
    "training session",
    "interview",
    "quiz",
    "fun",
    "challenge",
    "throwback",
    "social media",
    "instagram",
    "tiktok",
    "youtube",
    "birthday",
    "merchandise",
    "shirt",
    "kit",
    "commercial"
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

bot = None
groq = None

if BOT_TOKEN:
    bot = Bot(token=BOT_TOKEN)

if GROQ_API_KEY:
    groq = Groq(api_key=GROQ_API_KEY)


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
# LOAD DATA
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
# SENTENCE DUPLICATE CLEANER
# =========================================================

def remove_repeated_sentences(text):

    if not text:
        return ""

    text = clean_text(text)

    # Split on sentence-ending punctuation
    parts = re.split(
        r"(?<=[.!?።፣])\s+",
        text
    )

    final_parts = []

    for part in parts:

        part = part.strip()

        if len(part) < 5:
            continue

        duplicate = False

        for old in final_parts:

            score = similarity(
                part,
                old
            )

            if score >= 0.82:

                duplicate = True
                break

        if not duplicate:

            final_parts.append(
                part
            )

    return " ".join(
        final_parts
    ).strip()


# =========================================================
# LIVERPOOL FILTER
# =========================================================

def is_liverpool_news(title, summary):

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
        "bradley barcola",

        "stefan bajcetic",
        "wataru endo"
    ]

    return any(
        keyword in text
        for keyword in keywords
    )


# =========================================================
# IMPORTANT NEWS FILTER
# =========================================================

def is_important_news(title, summary):

    text = (
        f"{title} {summary}"
    ).lower()

    important = any(
        keyword in text
        for keyword in IMPORTANT_KEYWORDS
    )

    if not important:
        return False

    low_importance = any(
        keyword in text
        for keyword in LOW_IMPORTANCE_KEYWORDS
    )

    strong_keywords = [

        "transfer",
        "signing",
        "deal",
        "agreement",
        "bid",
        "offer",
        "injury",
        "injured",
        "ruled out",
        "unavailable",
        "returns",
        "contract",
        "official",
        "confirmed",
        "appointed",
        "champions league",
        "premier league",
        "draw",
        "fixture",
        "win",
        "won",
        "loss",
        "lost",
        "defeat"
    ]

    strong = any(
        keyword in text
        for keyword in strong_keywords
    )

    if low_importance and not strong:
        return False

    return True


# =========================================================
# TRUSTED SOURCE DETECTION
# =========================================================

def detect_source(
    title,
    summary,
    source_name,
    author=""
):

    combined = (
        f"{title} "
        f"{summary} "
        f"{source_name} "
        f"{author}"
    ).lower()

    # Official Liverpool source
    if any(
        alias.lower() in combined
        for alias in TRUSTED_SOURCES[
            "Liverpool FC Official"
        ]
    ):

        # Be careful:
        # "Liverpool FC" in the article text itself
        # does not automatically mean official source.
        source_lower = (
            source_name or ""
        ).lower()

        if any(
            alias.lower() in source_lower
            for alias in TRUSTED_SOURCES[
                "Liverpool FC Official"
            ]
        ):

            return "Liverpool FC Official"

    # Reporters
    for reporter in [

        "Paul Joyce",
        "David Ornstein",
        "James Pearce",
        "Lewis Steele",
        "Melissa Reddy",
        "Fabrizio Romano"

    ]:

        if reporter.lower() in combined:

            return reporter

    return None


# =========================================================
# DATE / FRESHNESS
# =========================================================

def get_entry_timestamp(entry):

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


def is_fresh_news(entry):

    published_at = get_entry_timestamp(
        entry
    )

    if published_at is None:

        logger.info(
            "Rejected article with no publication time."
        )

        return False

    now = time.time()

    age = now - published_at

    if age < -10 * 60:

        logger.info(
            "Rejected invalid future article."
        )

        return False

    if age > MAX_NEWS_AGE:

        logger.info(
            "Rejected old article: %.1f minutes old.",
            age / 60
        )

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
                "Mozilla/5.0 LiverpoolNewsBot/12.0"
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

    value = (
        normalize(title)
        + "|"
        + normalize(summary)[:1200]
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

            author = clean_text(
                getattr(
                    entry,
                    "author",
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

            if not is_important_news(
                title,
                summary
            ):

                logger.info(
                    "Rejected non-important news: %s",
                    title
                )

                continue

            if not is_fresh_news(
                entry
            ):
                continue

            trusted = detect_source(
                title,
                summary,
                source_name,
                author
            )

            if not trusted:

                logger.info(
                    "Rejected untrusted source: %s | author=%s",
                    source_name,
                    author
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

                "author": author,

                "entry": entry,

                "published_at":
                    get_entry_timestamp(
                        entry
                    )

            })

    collected.sort(
        key=lambda x:
        x.get("published_at", 0),
        reverse=True
    )

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

            # Strong title similarity
            if title_score >= 0.58:

                duplicate = True
                break

            # Strong summary similarity
            if (
                item["summary"]
                and old["summary"]
                and summary_score >= 0.70
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

    if ratio < 0.50:

        logger.warning(
            "Amharic ratio too low: %.2f",
            ratio
        )

        return False

    return True


# =========================================================
# GROQ TRANSLATION
# =========================================================

def translate_news_sync(item):

    if not groq:

        logger.error(
            "GROQ_API_KEY is missing."
        )

        return None

    prompt = f"""
ከታች የተሰጠውን የLiverpool FC ዜና
ብቻ በተፈጥሯዊ፣ ግልጽ እና ትክክለኛ
የኢትዮጵያ አማርኛ የTelegram ዜና ቅርጽ አዘጋጅ።

በጣም አስፈላጊ:

1. የተሰጠውን መረጃ ብቻ ተጠቀም።
2. አዲስ እውነታ አትፍጠር።
3. ከሌላ የራስህ እውቀት መረጃ አትጨምር።
4. ስም፣ ዋጋ፣ ቀን፣ ቡድን፣ ውጤት ወይም ሌላ መረጃ ካልተጠቀሰ አትጨምር።
5. የዝውውር ወሬ ከሆነ እንደ ወሬ አቅርብ።
6. የተረጋገጠ ዜና ካልሆነ "ተዘግቧል"፣ "እንደተገለጸው" ወይም ተመሳሳይ ቃል ተጠቀም።
7. የተሰጠው ዜና አጭር ከሆነ በራስህ ሀሳብ ረጅም አታድርገው።
8. አንድን ሀሳብ በተደጋጋሚ አትጻፍ።
9. አንድ ሰው/ክለብ/ተጫዋች በአንድ ዜና ውስጥ ብዙ ጊዜ ተጠቅሶ ካለ በተደጋጋሚ አትጻፈው።
10. የዜናውን ዋና ነጥብ ጠብቅ።

የTelegram ቅርጽ:

ርዕስ:
አጭር፣ ግልጽ እና የዜናውን ዋና ነጥብ የሚያሳይ የአማርኛ ርዕስ።

ዜና:
በተፈጥሯዊ አማርኛ 1-3 አጭር አንቀጾች።
የሌለውን መረጃ አትጨምር።
የተደጋገመውን ይዘት አትድገም።

እነዚህን በAI output ውስጥ በፍጹም አታስገባ:

- 🔴 LIVERPOOL NEWS
- ርዕስ:
- ዜና:
- ምንጭ:
- Source:
- 🔗
- @yegnaLiverpool
- Link
- English headline
- English paragraph

የተጫዋቾች፣ የክለቦች እና የሰዎች ስሞች
በEnglish እንዲቀሩ ይችላሉ።

በመጨረሻ የሚፈለገው JSON ብቻ ነው:

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
                    "content": (
                        "You are a professional Ethiopian "
                        "Amharic football news editor. "
                        "Translate and rewrite only the "
                        "provided Liverpool FC news. "
                        "Never invent facts. "
                        "Never repeat sentences. "
                        "Return only valid JSON."
                    )
                },

                {
                    "role": "user",
                    "content": prompt
                }

            ],

            temperature=0.05,

            max_tokens=1600,

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

        # Remove accidental repeated sentences
        body = remove_repeated_sentences(
            body
        )

        title = remove_repeated_sentences(
            title
        )

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

        # Final repeated-content protection
        if similarity(
            title,
            body
        ) >= 0.75:

            logger.warning(
                "AI title/body are too similar."
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
# ORIGINAL ARTICLE IMAGE ONLY
# =========================================================

def get_page_image(article_url):

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

        final_url = response.url

        # IMPORTANT:
        # If we are still on Google News, do not use
        # its image.
        if "news.google.com" in (
            urlparse(final_url).netloc.lower()
        ):

            logger.warning(
                "Still on Google News. Image rejected."
            )

            return None

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        # 1. og:image
        image = soup.find(
            "meta",
            property="og:image"
        )

        if image:

            image_url = image.get(
                "content"
            )

            if image_url:

                image_url = urljoin(
                    final_url,
                    image_url
                )

                # Reject Google image URLs
                if "news.google.com" not in image_url.lower():

                    return image_url

        # 2. twitter:image
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

                image_url = urljoin(
                    final_url,
                    image_url
                )

                if "news.google.com" not in image_url.lower():

                    return image_url

    except Exception as error:

        logger.warning(
            "Original page image error: %s",
            error
        )

    return None


# =========================================================
# FIND IMAGE
# =========================================================

def get_image_url(item):

    article_url = item.get(
        "link",
        ""
    )

    # ONLY original article image.
    # We deliberately do NOT use Google News RSS image.
    image = get_page_image(
        article_url
    )

    if image:

        logger.info(
            "✅ Original article image found."
        )

        return image

    logger.info(
        "ℹ️ No original article image found. "
        "News will be sent without a photo."
    )

    return None


# =========================================================
# DOWNLOAD IMAGE
# =========================================================

def download_image(image_url):

    if not image_url:
        return None

    try:

        # Never download Google News images
        if "news.google.com" in image_url.lower():

            logger.warning(
                "❌ Google News image rejected."
            )

            return None

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

        if "image" not in content_type:
            return None

        extension = ".jpg"

        if "png" in content_type:
            extension = ".png"

        elif "webp" in content_type:
            extension = ".webp"

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
        f"<b>{safe_title}</b>\n\n"
        f"{safe_body}\n\n"
        f"<b>{safe_source}</b>\n\n"
        "🔴 <b>@yegnaLiverpool</b>"
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

        if image_file and len(message) <= 1000:

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
                    "✅ News + ORIGINAL photo sent."
                )

            except Exception as photo_error:

                logger.warning(
                    "Photo failed: %s",
                    photo_error
                )

                await bot.send_message(
                    chat_id=CHANNEL_ID,
                    text=message,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True
                )

        elif image_file:

            # Telegram photo caption limit
            photo_caption = (
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

            await bot.send_message(
                chat_id=CHANNEL_ID,
                text=message,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True
            )

        else:

            # No image = NO WRONG IMAGE.
            await bot.send_message(
                chat_id=CHANNEL_ID,
                text=message,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True
            )

        # Save ONLY after successful Telegram send
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
# SEND LIMIT
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
            "30-minute limit reached."
        )

        return False

    if sent_times:

        elapsed = (
            now - sent_times[-1]
        )

        if elapsed < MIN_NEWS_GAP:

            logger.info(
                "Minimum news gap not reached."
            )

            return False

    return True


# =========================================================
# NEWS LOOP
# =========================================================

async def news_loop():

    while True:

        try:

            logger.info(
                "🔎 Checking fresh important Liverpool news..."
            )

            news = await fetch_news()

            if not news:

                logger.info(
                    "No fresh important trusted news."
                )

            else:

                news = remove_duplicates(
                    news
                )

                logger.info(
                    "Fresh unique articles: %s",
                    len(news)
                )

                if can_send_news():

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
                                "✅ Important news sent."
                            )

                            break

                    if not sent:

                        logger.info(
                            "No article was sent."
                        )

                else:

                    logger.info(
                        "News waiting because of "
                        "15-minute gap/limit."
                    )

        except Exception as error:

            logger.exception(
                "❌ News loop error: %s",
                error
            )

        logger.info(
            "⏳ Next news check in 15 minutes."
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
# LIVE MATCHES
# =========================================================

async def send_live_matches():

    global live_seen

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
        "🕐 Fresh news only: ON"
    )

    logger.info(
        "🔥 Important news only: ON"
    )

    logger.info(
        "🔁 Duplicate protection: ON"
    )

    logger.info(
        "🖼️ Google News image: BLOCKED"
    )

    logger.info(
        "🖼️ Original article image: ON"
    )

    logger.info(
        "📰 News check: every 15 minutes"
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

