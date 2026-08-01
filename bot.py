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
from urllib.parse import quote_plus

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

SEEN_FILE = "seen_news_v2.json"
SENT_TIMES_FILE = "sent_times_v2.json"
LIVE_SEEN_FILE = "live_seen_v2.json"


# =========================================================
# TRUSTED SOURCES
# =========================================================

TRUSTED_SOURCES = {
    "Liverpool FC Official": [
        "Liverpool FC",
        "Liverpool Football Club",
        "Liverpoolfc.com"
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
    '"Liverpool FC" "Liverpool FC"',
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
    '"Liverpool FC" contract'
]


# =========================================================
# GLOBALS
# =========================================================

seen_news = set()
sent_times = []
live_seen = set()


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

sent_times = []

for value in load_json_list(
    SENT_TIMES_FILE
):

    try:

        timestamp = float(value)

        if time.time() - timestamp < 30 * 60:
            sent_times.append(timestamp)

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
                "Mozilla/5.0 LiverpoolNewsBot/7.0"
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
    source
):

    value = (
        normalize(title)
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

            trusted = detect_source(
                title,
                summary,
                source_name
            )

            if not trusted:
                continue

            news_id = make_news_id(
                title,
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

                "source_name": source_name

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

            # ተመሳሳይ ርዕስ
            if title_score >= 0.65:

                duplicate = True
                break

            # ተመሳሳይ ይዘት
            if (
                item["summary"]
                and old["summary"]
                and summary_score >= 0.78
            ):

                duplicate = True
                break

        if not duplicate:

            unique.append(item)

    return unique


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


def clean_ai_output(text):

    if not text:
        return ""

    text = text.strip()

    text = re.sub(
        r"^(ርዕስ|Title)\s*:\s*",
        "",
        text,
        flags=re.I
    )

    text = re.sub(
        r"^(ዜና|News)\s*:\s*",
        "",
        text,
        flags=re.I
    )

    text = re.sub(
        r"^(ምንጭ|Source)\s*:\s*.*$",
        "",
        text,
        flags=re.I | re.M
    )

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


def valid_amharic(text):

    if not text:
        return False

    text = clean_ai_output(
        text
    )

    if amharic_ratio(text) < 0.50:

        logger.warning(
            "Amharic ratio too low: %.2f",
            amharic_ratio(text)
        )

        return False

    return True
# =========================================================
# GROQ - AMHARIC NEWS WRITER
# =========================================================

def translate_news_sync(item):

    prompt = f"""
አንተ የLiverpool FC የአማርኛ ስፖርት
ጋዜጠኛ ነህ።

ከታች የተሰጠውን ዜና በተፈጥሯዊ፣
ግልጽ እና ትክክለኛ የኢትዮጵያ
አማርኛ አዘጋጅ።

ጥብቅ ህጎች:

1. ርዕሱ በአማርኛ ይሁን።
2. ዋናው ዜና በአማርኛ ይሁን።
3. English headline አትተው።
4. English paragraph አትተው።
5. የተጫዋች ስም እና የክለብ ስም
   English ሊቀሩ ይችላሉ።
6. ዋጋ፣ ቁጥር፣ ቀን እና እውነታ
   አትቀይር።
7. ያልተሰጠህን መረጃ አትፍጠር።
8. የዝውውር ወሬ ከሆነ ወሬ መሆኑን
   በግልጽ አሳይ።
9. ዜናውን አታሳጥር።
10. ተመሳሳይ ሀሳብ አትድገም።
11. Markdown አትጠቀም።
12. ===== ወይም ----- ወይም ____ የሚል
    separator አትጠቀም።
13. የምንጩን ስም አትቀይር።

የመልስ ቅርጽ:

ርዕስ:
[አማርኛ ርዕስ]

ዜና:
[ሙሉ እና በቂ የሆነ አማርኛ ዜና]

ምንጭ:
[{item["source"]}]

የመጀመሪያው ርዕስ:
{item["title"]}

የመጀመሪያው ይዘት:
{item["summary"]}
"""

    try:

        result = groq.chat.completions.create(

            model="openai/gpt-oss-120b",

            messages=[

                {
                    "role": "system",
                    "content": (
                        "You are an Ethiopian Amharic "
                        "Liverpool football journalist. "
                        "Write natural Ethiopian Amharic."
                    )
                },

                {
                    "role": "user",
                    "content": prompt
                }

            ],

            temperature=0.10,

            max_tokens=1800
        )

        text = (
            result
            .choices[0]
            .message
            .content
            .strip()
        )

        text = clean_ai_output(
            text
        )

        if valid_amharic(text):

            return text

        logger.warning(
            "AI output was not sufficiently Amharic."
        )

        return None

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
# TELEGRAM MESSAGE
# =========================================================

def make_message(news, link, source):

    news = clean_ai_output(news)

    safe_news = html.escape(news)

    safe_link = html.escape(
        link,
        quote=True
    )

    safe_source = html.escape(
        source
    )

    return (
        "🔴 <b>LIVERPOOL NEWS</b>\n\n"
        f"{safe_news}\n\n"
        f"📰 <b>ምንጭ:</b> {safe_source}\n\n"
        f"🔗 <a href=\"{safe_link}\">"
        "የዋናውን ዜና ይመልከቱ"
        "</a>\n\n"
        "🔴 <b>YN Liverpool</b>"
    )


# =========================================================
# SEND NEWS TO TELEGRAM
# =========================================================

async def send_news(item):

    global sent_times

    news = await translate_news(item)

    if not news:

        logger.error(
            "Amharic translation failed."
        )

        return False

    message = make_message(
        news,
        item["link"],
        item["source"]
    )

    try:

        await bot.send_message(
            chat_id=CHANNEL_ID,
            text=message,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=False
        )

        logger.info(
            "✅ News sent to Telegram: %s",
            item["title"]
        )

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
            sent    
        save_json_list(
            SENT_TIMES_FILE,
            sent_times
        )

        return True

    except Exception as error:

        logger.error(
            "❌ Telegram send error: %s",
            error
        )

        return False


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

    if len(sent_times) >= MAX_NEWS_PER_30_MIN:

        logger.info(
            "30-minute news limit reached."
        )

        return False

    if sent_times:

        elapsed = (
            now - sent_times[-1]
        )

        if elapsed < MIN_NEWS_GAP:

            logger.info(
                "Waiting before next news."
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
                "🔎 Checking for new Liverpool news..."
            )

            news = await fetch_news()

            if not news:

                logger.info(
                    "No new news found."
                )

                await asyncio.sleep(
                    NEWS_CHECK_EVERY
                )

                continue

            news = remove_duplicates(
                news
            )

            logger.info(
                "Found %s new articles.",
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
# START BOT
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
        "📰 News check: every 5 minutes"
    )

    logger.info(
        "🇪🇹 Amharic translation: ON"
    )

    logger.info(
        "🔁 Duplicate protection: ON"
    )

    logger.info(
        "========================================"
    )

    await news_loop()


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
