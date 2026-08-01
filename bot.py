import os
import re
import json
import time
import asyncio
import hashlib
import logging
import html as html_lib
from difflib import SequenceMatcher
from urllib.parse import quote_plus

import requests
import feedparser
from bs4 import BeautifulSoup

from groq import Groq
from telegram import Bot
from telegram.constants import ParseMode


BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
FOOTBALL_API_KEY = os.getenv("FOOTBALL_API_KEY")

CHANNELS = [
    "@yegnaLiverpool",
    "@yegnaLiverpoolET",
]

CHECK_EVERY = 300

MIN_NEWS_GAP = 15 * 60
MAX_NEWS_PER_30_MIN = 2

LIVE_CHECK_EVERY = 120

SEEN_FILE = "last_news.json"
LIVE_SEEN_FILE = "last_live.json"

LIVERPOOL_TEAM_ID = 40


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("liverpool_bot")


if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is missing")

if not GROQ_API_KEY:
    raise ValueError("GROQ_API_KEY is missing")


bot = Bot(token=BOT_TOKEN)
groq = Groq(api_key=GROQ_API_KEY)


TRUSTED_SOURCES = {
    "Liverpool FC Official": [
        "liverpool fc",
        "liverpool football club",
        "liverpoolfc.com",
    ],
    "Paul Joyce": [
        "paul joyce",
    ],
    "David Ornstein": [
        "david ornstein",
    ],
    "James Pearce": [
        "james pearce",
    ],
    "Lewis Steele": [
        "lewis steele",
    ],
    "Melissa Reddy": [
        "melissa reddy",
    ],
    "Fabrizio Romano": [
        "fabrizio romano",
    ],
}


SEARCHES = [
    '"Liverpool FC" "Liverpool"',
    '"Liverpool" "Paul Joyce"',
    '"Liverpool" "David Ornstein"',
    '"Liverpool" "James Pearce"',
    '"Liverpool" "Lewis Steele"',
    '"Liverpool" "Melissa Reddy"',
    '"Liverpool" "Fabrizio Romano"',
]


seen_news = set()
sent_times = []
live_seen = set()


def load_json_set(filename):
    try:
        if not os.path.exists(filename):
            return set()

        with open(filename, "r", encoding="utf-8") as file:
            data = json.load(file)

        if isinstance(data, list):
            return set(data)

    except Exception as error:
        logger.error("Memory load error: %s", error)

    return set()


def save_json_set(filename, data):
    try:
        with open(filename, "w", encoding="utf-8") as file:
            json.dump(
                list(data)[-3000:],
                file,
                ensure_ascii=False,
                indent=2
            )

    except Exception as error:
        logger.error("Memory save error: %s", error)


seen_news = load_json_set(SEEN_FILE)
live_seen = load_json_set(LIVE_SEEN_FILE)


def clean_text(text):
    if not text:
        return ""

    text = html_lib.unescape(str(text))
    text = re.sub(r"<[^>]*>", " ", text)
    text = re.sub(r"[\r\n\t]+", " ", text)
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def normalize(text):
    text = clean_text(text).lower()

    text = re.sub(
        r"[^a-z0-9\u1200-\u137f ]",
        " ",
        text
    )

    text = re.sub(r"\s+", " ", text)

    return text.strip()


def similarity(first, second):
    return SequenceMatcher(
        None,
        normalize(first),
        normalize(second)
    ).ratio()


def is_liverpool_news(title, summary):
    text = f"{title} {summary}".lower()

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
        "bradley barcola",
    ]

    return any(word in text for word in keywords)


def detect_source(title, summary, source):
    text = f"{title} {summary} {source}".lower()

    for trusted_name, aliases in TRUSTED_SOURCES.items():
        for alias in aliases:
            if alias in text:
                return trusted_name

    return None


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
                "User-Agent": "Mozilla/5.0 LiverpoolNewsBot/3.0"
            }
        )

        response.raise_for_status()

        return feedparser.parse(response.content)

    except Exception as error:
        logger.error("RSS error: %s", error)
        return None


def make_id(title, link):
    value = normalize(title) + "|" + link.lower().strip()

    return hashlib.sha256(
        value.encode("utf-8")
    ).hexdigest()


def fetch_news():
    collected = []

    for query in SEARCHES:
        logger.info("Searching: %s", query)

        feed = get_google_news(query)

        if not feed:
            continue

        for entry in feed.entries[:15]:
            title = clean_text(
                getattr(entry, "title", "")
            )

            summary = clean_text(
                getattr(entry, "summary", "")
            )

            link = clean_text(
                getattr(entry, "link", "")
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

            if not is_liverpool_news(title, summary):
                continue

            trusted = detect_source(
                title,
                summary,
                source_name
            )

            if not trusted:
                continue

            news_id = make_id(title, link)

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


def remove_duplicates(items):
    unique = []

    for item in items:
        duplicate = False

        for old in unique:
            title_score = similarity(
                item["title"],
                old["title"]
            )

            content_score = similarity(
                item["summary"],
                old["summary"]
            )

            if (
                title_score >= 0.65
                or content_score >= 0.75
            ):
                duplicate = True
                break

        if not duplicate:
            unique.append(item)

    return unique


def remove_bad_format(text):
    if not text:
        return ""

    text = re.sub(
        r"(?m)^\s*[=_-]{3,}\s*$",
        "",
        text
    )

    text = re.sub(
        r"\n\s*[=_-]{3,}\s*\n",
        "\n",
        text
    )

    text = re.sub(
        r"[ \t]+",
        " ",
        text
    )

    text = re.sub(
        r"\n{3,}",
        "\n\n",
        text
    )

    return text.strip()


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


def valid_amharic_news(text):
    if not text:
        return False

    text = remove_bad_format(text)

    if amharic_ratio(text) < 0.55:
        return False

    english_words = re.findall(
        r"\b[A-Za-z]{5,}\b",
        text
    )

    if len(english_words) > 12:
        return False

    if re.search(r"[=_-]{4,}", text):
        return False

    return True


def translate_news(item):
    prompt = f"""
አንተ የLiverpool FC የአማርኛ ስፖርት ጋዜጠኛ ነህ።

ከታች ያለውን ዜና በተፈጥሯዊ፣
ግልጽ እና ትክክለኛ የኢትዮጵያ አማርኛ
ለTelegram ቻናል አዘጋጅ።

ጥብቅ ህጎች:

1. ርዕሱ በአማርኛ ይሁን።
2. ዋናው ዜና በአማርኛ ይሁን።
3. English headline አትተው።
4. English paragraph አትተው።
5. የተጫዋች ስም እና የክለብ ስም English ሊቀር ይችላል።
6. ቁጥር፣ ዋጋ፣ ቀን እና እውነታ አትቀይር።
7. ያልተሰጠህን መረጃ አትፍጠር።
8. የዝውውር ወሬ ከሆነ ወሬ መሆኑን ግልጽ አድርግ።
9. ዜናውን አታሳጥር። ዋናውን መረጃ በበቂ ርዝመት ግለጽ።
10. ===== ወይም ----- ወይም ___ የሚመስሉ separator ምልክቶች አትጠቀም።
11. Markdown አትጠቀም።
12. የምንጩን ስም አትቀይር።
13. የተሰጠውን መረጃ ብቻ ተጠቀም።

ይህንን ቅርጽ ተጠቀም:

ርዕስ:
[አማርኛ ርዕስ]

ዜና:
[በቂ ርዝመት ያለው አማርኛ ዜና]

ምንጭ:
[{item["source"]}]

የመጀመሪያው ርዕስ:
{item["title"]}

የመጀመሪያው ይዘት:
{item["summary"]}
"""

    try:
        result = groq.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an Ethiopian Amharic "
                        "Liverpool football journalist. "
                        "Write natural, accurate Amharic."
                    )
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.05,
            max_tokens=1200
        )

        text = (
            result.choices[0]
            .message.content
            .strip()
        )

        text = remove_bad_format(text)

        if not valid_amharic_news(text):
            logger.warning(
                "AI output rejected. Retrying."
            )

            retry = groq.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Write natural Ethiopian "
                            "Amharic football news only. "
                            "No English headline. "
                            "No separators."
                        )
                    },
                    {
                        "role": "user",
                        "content": f"""
ይህንን ዜና በተፈጥሯዊ አማርኛ እንደገና ጻፍ።

ርዕስ:
{item["title"]}

ይዘት:
{item["summary"]}

ምንጭ:
{item["source"]}

የተጫዋች ስም እና የክለብ ስም
English ሊቀር ይችላል።

English headline አትተው።
Separator አትጠቀም።
"""
                    }
                ],
                temperature=0.02,
                max_tokens=1200
            )

            text = (
                retry.choices[0]
                .message.content
                .strip()
            )

            text = remove_bad_format(text)

        if not valid_amharic_news(text):
            logger.error(
                "News rejected because of Amharic validation."
            )
            return None

        return text

    except Exception as error:
        logger.error(
            "Groq error: %s",
            error
        )
        return None


def get_image(url):
    try:
        response = requests.get(
            url,
            timeout=15,
            headers={
                "User-Agent": "Mozilla/5.0"
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

    except Exception as error:
        logger.warning(
            "Image error: %s",
            error
        )

    return None


def make_message(news, link):
    news = remove_bad_format(news)

    safe_news = html_lib.escape(news)

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


def can_send_news():
    now = time.time()

    global sent_times

    sent_times = [
        timestamp
        for timestamp in sent_times
        if now - timestamp < 30 * 60
    ]

    if len(sent_times) >= MAX_NEWS_PER_30_MIN:
        return False

    if sent_times:
        if now - sent_times[-1] < MIN_NEWS_GAP:
            return False

    return True


async def send_news(item):
    if not can_send_news():
        logger.info(
            "News rate limit active."
        )
        return False

    news = translate_news(item)

    if not news:
        return False

    message = make_message(
        news,
        item["link"]
    )

    image_url = get_image(
        item["link"]
    )

    success = False

    for channel in CHANNELS:
        try:
            if image_url and len(message) <= 1000:
                try:
                    await bot.send_photo(
                        chat_id=channel,
                        photo=image_url,
                        caption=message,
                        parse_mode=ParseMode.HTML
                    )

                except Exception as photo_error:
                    logger.warning(
                        "Photo failed: %s",
                        photo_error
                    )

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

        except Exception as error:
            logger.error(
                "Telegram error for %s: %s",
                channel,
                error
            )

    if success:
        seen_news.add(item["id"])
        save_json_set(
            SEEN_FILE,
            seen_news
        )

        sent_times.append(
            time.time()
        )

    return success


def football_request(endpoint, params=None):
    if not FOOTBALL_API_KEY:
        return None

    url = (
        "https://v3.football.api-sports.io/"
        + endpoint
    )

    headers = {
        "x-apisports-key": FOOTBALL_API_KEY
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

        live_seen.add(live_key)

        if len(live_seen) > 1000:
            live_seen.clear()

        save_json_set(
            LIVE_SEEN_FILE,
            live_seen
        )

        message = (
            "🔴 <b>LIVERPOOL LIVE</b>\n\n"
            f"⚽ {html_lib.escape(home_name)} "
            f"{home_score if home_score is not None else 0}"
            " - "
            f"{away_score if away_score is not None else 0} "
            f"{html_lib.escape(away_name)}\n\n"
        )

        if minute is not None:
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

                logger.info(
                    "Live update sent to %s",
                    channel
                )

            except Exception as error:
                logger.error(
                    "Live Telegram error for %s: %s",
                    channel,
                    error
                )


async def news_loop():
    while True:
        try:
            if not can_send_news():
                logger.info(
                    "News rate limit active. Waiting..."
                )

                await asyncio.sleep(
                    CHECK_EVERY
                )

                continue

            logger.info(
                "Checking trusted Liverpool news..."
            )

            news = fetch_news()

            news = remove_duplicates(
                news
            )

            if not news:
                logger.info(
                    "No new trusted Liverpool news."
                )

                await asyncio.sleep(
                    CHECK_EVERY
                )

                continue

            sent = False

            for item in news:
                if await send_news(item):
                    sent = True
                    break

            if sent:
                logger.info(
                    "Next news check in 5 minutes."
                )

            await asyncio.sleep(
                CHECK_EVERY
            )

        except Exception as error:
            logger.exception(
                "News loop error: %s",
                error
            )

            await asyncio.sleep(
                CHECK_EVERY
            )


async def live_loop():
    while True:
        try:
            await send_live_matches()

        except Exception as error:
            logger.error(
                "Live loop error: %s",
                error
            )

        await asyncio.sleep(
            LIVE_CHECK_EVERY
        )


async def main():
    logger.info(
        "Liverpool Amharic News Bot started"
    )

    logger.info(
        "Trusted sources only"
    )

    logger.info(
        "Maximum 2 news per 30 minutes"
    )

    logger.info(
        "Minimum gap: 15 minutes"
    )

    await asyncio.gather(
        news_loop(),
        live_loop()
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())

    except KeyboardInterrupt:
        logger.info(
            "Bot stopped."
        )
