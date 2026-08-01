import os
import re
import json
import time
import random
import asyncio
import hashlib
import logging
import html as html_lib
from datetime import datetime, timezone
from difflib import SequenceMatcher
from urllib.parse import quote_plus

import requests
import feedparser
from bs4 import BeautifulSoup

from groq import Groq
from telegram import Bot
from telegram.constants import ParseMode


# ============================================================
# SETTINGS
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
FOOTBALL_API_KEY = os.getenv("FOOTBALL_API_KEY")

CHANNELS = [
    "@yegnaLiverpool",
    "@yegnaLiverpoolET",
]

NEWS_MIN_DELAY = 30 * 60
NEWS_MAX_DELAY = 60 * 60

CHECK_EVERY = 300
SEEN_FILE = "last_news.json"

# Liverpool FC API-Football team ID
LIVERPOOL_TEAM_ID = 40


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("liverpool_bot")


# ============================================================
# KEYS
# ============================================================

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is missing")

if not GROQ_API_KEY:
    raise ValueError("GROQ_API_KEY is missing")


bot = Bot(token=BOT_TOKEN)
groq = Groq(api_key=GROQ_API_KEY)


# ============================================================
# TRUSTED SOURCES
# ============================================================

TRUSTED_SOURCES = {
    "Liverpool FC Official": [
        "Liverpool FC",
        "Liverpool Football Club",
        "liverpoolfc.com",
    ],

    "Paul Joyce": [
        "Paul Joyce",
    ],

    "David Ornstein": [
        "David Ornstein",
    ],

    "James Pearce": [
        "James Pearce",
    ],

    "Lewis Steele": [
        "Lewis Steele",
    ],

    "Melissa Reddy": [
        "Melissa Reddy",
    ],

    "Fabrizio Romano": [
        "Fabrizio Romano",
    ],
}


# ============================================================
# SEARCHES
# ============================================================

SEARCHES = [
    '"Liverpool FC" "Liverpool"',
    '"Liverpool" "Paul Joyce"',
    '"Liverpool" "David Ornstein"',
    '"Liverpool" "James Pearce"',
    '"Liverpool" "Lewis Steele"',
    '"Liverpool" "Melissa Reddy"',
    '"Liverpool" "Fabrizio Romano"',
]


# ============================================================
# MEMORY
# ============================================================

def load_seen():

    try:
        if not os.path.exists(SEEN_FILE):
            return set()

        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, list):
            return set(data)

    except Exception as e:
        logger.error("Memory load error: %s", e)

    return set()


def save_seen():

    try:
        data = list(seen_news)[-2000:]

        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2
            )

    except Exception as e:
        logger.error("Memory save error: %s", e)


seen_news = load_seen()


# ============================================================
# TEXT
# ============================================================

def clean_text(text):

    if not text:
        return ""

    text = html_lib.unescape(str(text))

    text = re.sub(
        r"<[^>]*>",
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

    text = clean_text(text).lower()

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


def similarity(a, b):

    return SequenceMatcher(
        None,
        normalize(a),
        normalize(b)
    ).ratio()


# ============================================================
# LIVERPOOL FILTER
# ============================================================

def is_liverpool_news(title, summary):

    text = (
        title + " " + summary
    ).lower()

    keywords = [
        "liverpool",
        "liverpool fc",
        "lfc",
        "anfield",
        "reds",
        "arne slot",
        "andoni iraola",
        "virgil van dijk",
        "mohamed salah",
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
    ]

    return any(
        word in text
        for word in keywords
    )


# ============================================================
# SOURCE DETECTION
# ============================================================

def detect_source(title, summary, source):

    text = (
        f"{title} {summary} {source}"
    ).lower()

    for trusted_name, aliases in TRUSTED_SOURCES.items():

        for alias in aliases:

            if alias.lower() in text:
                return trusted_name

    return None


# ============================================================
# GOOGLE NEWS RSS
# ============================================================

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
                "Mozilla/5.0 "
                "LiverpoolNewsBot/1.0"
            }
        )

        response.raise_for_status()

        return feedparser.parse(
            response.content
        )

    except Exception as e:

        logger.error(
            "RSS error: %s",
            e
        )

        return None


# ============================================================
# NEWS ID
# ============================================================

def make_id(title, link):

    value = (
        normalize(title)
        + "|"
        + link.lower().strip()
    )

    return hashlib.sha256(
        value.encode("utf-8")
    ).hexdigest()


# ============================================================
# FETCH NEWS
# ============================================================

def fetch_news():

    collected = []

    for query in SEARCHES:

        logger.info(
            "Searching: %s",
            query
        )

        feed = get_google_news(query)

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

            news_id = make_id(
                title,
                link
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
            })

    return collected


# ============================================================
# REMOVE DUPLICATES
# ============================================================

def remove_duplicates(items):

    unique = []

    for item in items:

        duplicate = False

        for old in unique:

            title_similarity = similarity(
                item["title"],
                old["title"]
            )

            content_similarity = similarity(
                item["summary"],
                old["summary"]
            )

            if (
                title_similarity >= 0.72
                or content_similarity >= 0.80
            ):

                duplicate = True
                break

        if not duplicate:
            unique.append(item)

    return unique


# ============================================================
# AMHARIC VALIDATION
# ============================================================

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


def is_amharic(text):

    return amharic_ratio(text) >= 0.30


# ============================================================
# GROQ
# ============================================================

def translate_news(item):

    prompt = f"""
አንተ የLiverpool FC የአማርኛ ስፖርት ጋዜጠኛ ነህ።

የተሰጠህን ዜና ለTelegram ቻናል ተስማሚ
በሆነ ተፈጥሯዊ አማርኛ አዘጋጅ።

ጥብቅ ህጎች:

1. ርዕሱ በአማርኛ ብቻ ይሁን።
2. የዜናው ዋና ይዘት በአማርኛ ይሁን።
3. English headline እንደመጣ አትተው።
4. English paragraph አትተው።
5. የሰው ስም፣ የክለብ ስም እና የውድድር ስም
   English ሊቀር ይችላል።
6. ቁጥሮች፣ ዋጋዎች፣ ቀኖች እና እውነታዎችን አትቀይር።
7. ያልተሰጠህን መረጃ አትፍጠር።
8. Rumour ከሆነ እንደ rumour ግልጽ አድርግ።
9. የተረጋገጠ ዝውውር ካልሆነ
   "ተዘግቧል" ወይም "ሪፖርት እንደሚለው"
   በማለት ጻፍ።
10. የምንጩን ስም አትፍጠር።
11. አጭር፣ ግልጽ እና የስፖርት ጋዜጠኛ ቋንቋ ተጠቀም።

ቅርጹ ይህ ብቻ ይሁን:

ርዕስ:
[አማርኛ ርዕስ]

ዜና:
[አማርኛ ዜና]

ምንጭ:
[{item["source"]}]

ዋናው የዜና ርዕስ:
{item["title"]}

ዋናው ይዘት:
{item["summary"]}
"""

    try:

        result = groq.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content":
                    "Write Liverpool football news "
                    "in natural Ethiopian Amharic."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.10,
            max_tokens=900
        )

        text = (
            result
            .choices[0]
            .message
            .content
            .strip()
        )

        if not is_amharic(text):

            logger.warning(
                "AI returned too much English. Retrying..."
            )

            retry = groq.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {
                        "role": "system",
                        "content":
                        "You must write the entire "
                        "football news in Amharic. "
                        "Do not leave an English headline."
                    },
                    {
                        "role": "user",
                        "content":
                        f"""
በአማርኛ ብቻ እንደገና ጻፈው:

{item["title"]}

{item["summary"]}

ርዕስ:
...

ዜና:
...

ምንጭ:
{item["source"]}
"""
                    }
                ],
                temperature=0.05,
                max_tokens=900
            )

            text = (
                retry
                .choices[0]
                .message
                .content
                .strip()
            )

        return text

    except Exception as e:

        logger.error(
            "Groq error: %s",
            e
        )

        return None


# ============================================================
# IMAGE
# ============================================================

def get_image(url):

    try:

        response = requests.get(
            url,
            timeout=15,
            headers={
                "User-Agent":
                "Mozilla/5.0"
            }
        )

        if response.status_code != 200:
            return None

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        image = soup.find(
            "meta",
            property="og:image"
        )

        if image:
            return image.get("content")

    except Exception as e:

        logger.warning(
            "Image error: %s",
            e
        )

    return None


# ============================================================
# TELEGRAM MESSAGE
# ============================================================

def make_message(news, link):

    safe_news = html_lib.escape(
        news
    )

    safe_link = html_lib.escape(
        link,
        quote=True
    )

    return (
        "🔴 <b>LIVERPOOL NEWS</b>\n\n"
        f"{safe_news}\n\n"
        f"🔗 <a href=\"{safe_link}\">"
        "የዋናውን ዜና ይመልከቱ"
        "</a>\n\n"
        "🔴 <b>YN Liverpool</b>"
    )


# ============================================================
# SEND
# ============================================================

async def send_news(item):

    news = translate_news(item)

    if not news:
        return False

    if not is_amharic(news):

        logger.error(
            "News rejected: not enough Amharic."
        )

        return False

    message = make_message(
        news,
        item["link"]
    )

    success = False

    image_url = get_image(
        item["link"]
    )

    for channel in CHANNELS:

        try:

            if image_url:

                try:

                    await bot.send_photo(
                        chat_id=channel,
                        photo=image_url,
                        caption=message,
                        parse_mode=ParseMode.HTML
                    )

                except Exception:

                    await bot.send_message(
                        chat_id=channel,
                        text=message,
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=False
                    )

            else:

                await bot.send_message(
                    chat_id=channel,
                    text=message,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=False
                )

            logger.info(
                "News sent to %s",
                channel
            )

            success = True

        except Exception as e:

            logger.error(
                "Telegram error for %s: %s",
                channel,
                e
            )

    if success:

        seen_news.add(
            item["id"]
        )

        save_seen()

    return success


# ============================================================
# FOOTBALL API
# ============================================================

def football_request(endpoint, params=None):

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
            return None

        return response.json()

    except Exception as e:

        logger.error(
            "Football API error: %s",
            e
        )

        return None


# ============================================================
# LIVE MATCH
# ============================================================

async def send_live_matches():

    if not FOOTBALL_API_KEY:
        return

    data = football_request(
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

        status = (
            fixture
            .get("status", {})
            .get("long", "")
        )

        minute = (
            fixture
            .get("status", {})
            .get("elapsed")
        )

        message = (
            "🔴 <b>LIVERPOOL LIVE</b>\n\n"
            f"⚽ {html_lib.escape(home_name)} "
            f"{home_score if home_score is not None else 0}"
            " - "
            f"{away_score if away_score is not None else 0} "
            f"{html_lib.escape(away_name)}\n\n"
        )

        if minute:
            message += f"⏱️ {minute}'\n"

        message += (
            f"📌 {html_lib.escape(status)}"
        )

        for channel in CHANNELS:

            try:

                await bot.send_message(
                    chat_id=channel,
                    text=message,
                    parse_mode=ParseMode.HTML
                )

            except Exception as e:

                logger.error(
                    "Live send error: %s",
                    e
                )


# ============================================================
# NEWS LOOP
# ============================================================

async def news_loop():

    first_run = True

    while True:

        try:

            logger.info(
                "Checking trusted Liverpool news..."
            )

            news = fetch_news()

            news = remove_duplicates(
                news
            )

            if news:

                # First run: one story only
                # so the bot doesn't flood the channel.
                item = news[0]

                sent = await send_news(
                    item
                )

                if sent:

                    if first_run:

                        delay = random.randint(
                            NEWS_MIN_DELAY,
                            NEWS_MAX_DELAY
                        )

                        logger.info(
                            "Next news in %d minutes",
                            delay // 60
                        )

                        await asyncio.sleep(
                            delay
                        )

                    else:

                        delay = random.randint(
                            NEWS_MIN_DELAY,
                            NEWS_MAX_DELAY
                        )

                        logger.info(
                            "Next news in %d minutes",
                            delay // 60
                        )

                        await asyncio.sleep(
                            delay
                        )

                else:

                    await asyncio.sleep(
                        CHECK_EVERY
                    )

            else:

                logger.info(
                    "No new trusted Liverpool news."
                )

                await asyncio.sleep(
                    CHECK_EVERY
                )

            first_run = False

        except Exception as e:

            logger.exception(
                "News loop error: %s",
                e
            )

            await asyncio.sleep(
                CHECK_EVERY
            )


# ============================================================
# LIVE LOOP
# ============================================================

async def live_loop():

    while True:

        try:

            await send_live_matches()

        except Exception as e:

            logger.error(
                "Live loop error: %s",
                e
            )

        # Live checks every 2 minutes
        await asyncio.sleep(
            120
        )


# ============================================================
# MAIN
# ============================================================

async def main():

    logger.info(
        "===================================="
    )

    logger.info(
        "🔴 Liverpool Amharic News Bot started"
    )

    logger.info(
        "Channels: %s",
        ", ".join(CHANNELS)
    )

    logger.info(
        "News interval: 30-60 minutes"
    )

    logger.info(
        "Trusted sources only"
    )

    logger.info(
        "===================================="
    )

    await asyncio.gather(
        news_loop(),
        live_loop()
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    try:

        asyncio.run(
            main()
        )

    except KeyboardInterrupt:

        logger.info(
            "Bot stopped."
        )
