import os
import re
import json
import time
import random
import asyncio
import hashlib
import logging
import html as html_lib
from collections import deque
from difflib import SequenceMatcher
from urllib.parse import quote_plus

import requests
import feedparser

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

SEEN_FILE = "last_news.json"
POST_HISTORY_FILE = "post_history.json"

CHECK_EVERY = 180
MAX_POSTS_PER_30_MIN = 2
POST_WINDOW_SECONDS = 30 * 60

LIVERPOOL_TEAM_ID = 40

MIN_ARTICLE_WORDS = 35
MIN_ARTICLE_CHARS = 180

TRUSTED_SOURCES = {
    "Liverpool FC Official": [
        "liverpoolfc.com",
        "Liverpool FC",
        "Liverpool Football Club",
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

SEARCHES = [
    '"Liverpool FC" "Liverpool"',
    '"Liverpool" "Paul Joyce"',
    '"Liverpool" "David Ornstein"',
    '"Liverpool" "James Pearce"',
    '"Liverpool" "Lewis Steele"',
    '"Liverpool" "Melissa Reddy"',
    '"Liverpool" "Fabrizio Romano"',
]

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

seen_news = set()
post_history = deque()


def clean_text(text):
    if not text:
        return ""

    text = html_lib.unescape(str(text))
    text = re.sub(r"<[^>]+>", " ", text)
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


def similarity(a, b):
    return SequenceMatcher(
        None,
        normalize(a),
        normalize(b)
    ).ratio()


def load_memory():
    global seen_news

    try:
        if os.path.exists(SEEN_FILE):
            with open(
                SEEN_FILE,
                "r",
                encoding="utf-8"
            ) as f:
                data = json.load(f)

            if isinstance(data, list):
                seen_news = set(data[-5000:])

    except Exception as e:
        logger.error("Seen memory error: %s", e)


def save_memory():
    try:
        with open(
            SEEN_FILE,
            "w",
            encoding="utf-8"
        ) as f:
            json.dump(
                list(seen_news)[-5000:],
                f,
                ensure_ascii=False,
                indent=2
            )

    except Exception as e:
        logger.error("Save memory error: %s", e)


def load_post_history():
    global post_history

    try:
        if os.path.exists(POST_HISTORY_FILE):
            with open(
                POST_HISTORY_FILE,
                "r",
                encoding="utf-8"
            ) as f:
                data = json.load(f)

            if isinstance(data, list):
                now = time.time()

                for value in data:
                    try:
                        value = float(value)

                        if now - value < POST_WINDOW_SECONDS:
                            post_history.append(value)

                    except Exception:
                        pass

    except Exception as e:
        logger.error("Post history error: %s", e)


def save_post_history():
    try:
        with open(
            POST_HISTORY_FILE,
            "w",
            encoding="utf-8"
        ) as f:
            json.dump(
                list(post_history),
                f
            )

    except Exception as e:
        logger.error(
            "Post history save error: %s",
            e
        )


def clean_post_history():
    now = time.time()

    while post_history:
        if now - post_history[0] >= POST_WINDOW_SECONDS:
            post_history.popleft()
        else:
            break

    save_post_history()


def can_post():
    clean_post_history()

    return len(post_history) < MAX_POSTS_PER_30_MIN


def register_post():
    clean_post_history()

    post_history.append(time.time())

    save_post_history()


def is_liverpool_news(title, summary):
    text = (
        clean_text(title)
        + " "
        + clean_text(summary)
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
        "bradley barcola",
    ]

    return any(
        keyword in text
        for keyword in keywords
    )


def detect_source(title, summary, source_name, link):
    text = (
        f"{title} "
        f"{summary} "
        f"{source_name} "
        f"{link}"
    ).lower()

    if "liverpoolfc.com" in text:
        return "Liverpool FC Official"

    for source, aliases in TRUSTED_SOURCES.items():
        if source == "Liverpool FC Official":
            continue

        for alias in aliases:
            if alias.lower() in text:
                return source

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
                "User-Agent":
                "Mozilla/5.0 LiverpoolNewsBot/2.0"
            }
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


def make_id(title, link):
    value = (
        normalize(title)
        + "|"
        + link.lower().strip()
    )

    return hashlib.sha256(
        value.encode("utf-8")
    ).hexdigest()


def article_is_long_enough(title, summary):
    content = clean_text(
        f"{title} {summary}"
    )

    words = content.split()

    if len(words) < MIN_ARTICLE_WORDS:
        return False

    if len(content) < MIN_ARTICLE_CHARS:
        return False

    return True


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
                source_name,
                link
            )

            if not trusted:
                continue

            if not article_is_long_enough(
                title,
                summary
            ):
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

            if title_score >= 0.65:
                duplicate = True
                break

            if summary_score >= 0.72:
                duplicate = True
                break

        if not duplicate:
            unique.append(item)

    return unique


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
    return amharic_ratio(text) >= 0.55


def remove_bad_ai_headers(text):
    if not text:
        return ""

    text = text.replace(
        "==========",
        ""
    )

    text = text.replace(
        "====================",
        ""
    )

    text = re.sub(
        r"^\s*ርዕስ\s*:\s*",
        "",
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(
        r"\n\s*ዜና\s*:\s*",
        "\n",
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(
        r"\n\s*ምንጭ\s*:\s*.*$",
        "",
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(
        r"\n{3,}",
        "\n\n",
        text
    )

    return text.strip()


def translate_news(item):
    prompt = f"""
አንተ የLiverpool FC የአማርኛ ስፖርት ጋዜጠኛ ነህ።

የተሰጠህን ዜና በተፈጥሯዊ፣ ግልጽ እና
ለTelegram ተስማሚ በሆነ አማርኛ አዘጋጅ።

ጥብቅ ህጎች:

- ርዕሱ በአማርኛ ይሁን።
- የዜናው ዋና ይዘት በአማርኛ ይሁን።
- English headline አትተው።
- English paragraph አትተው።
- የሰው ስም በEnglish ሊቀር ይችላል።
- Liverpool FC እና የክለቦች ስሞች በEnglish ሊቀሩ ይችላሉ።
- ቁጥር፣ ዋጋ፣ ቀን እና እውነታ አትቀይር።
- መረጃ አትፍጠር።
- Rumour ከሆነ በግልጽ "ተዘግቧል" ወይም
  "ሪፖርት እንደሚለው" በማለት ጻፍ።
- የተረጋገጠ ዝውውር ካልሆነ እንደ confirmed transfer አታቅርበው።
- ዜናውን አጭር ብቻ አታድርገው፤ የተሰጠውን
  ዋና መረጃ በቂ ዝርዝር አካትት።
- ምንጩን አትፍጠር።
- ምንም "========" ወይም "====" አትጠቀም።
- ምንም Markdown heading አትጠቀም።
- በቀጥታ የሚለጠፍ የTelegram ዜና ጽሑፍ ብቻ ስጥ።

ዋናው ርዕስ:
{item["title"]}

ዋናው ይዘት:
{item["summary"]}

ታማኝ ምንጭ:
{item["source"]}
"""

    try:
        result = groq.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content":
                    "You are a professional Ethiopian "
                    "Amharic Liverpool football journalist. "
                    "Always write the news in natural Amharic."
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
            result
            .choices[0]
            .message
            .content
            .strip()
        )

        text = remove_bad_ai_headers(text)

        if is_amharic(text):
            return text

        logger.warning(
            "AI returned insufficient Amharic. Retrying."
        )

        retry = groq.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content":
                    "Write only natural Ethiopian "
                    "Amharic football news. "
                    "Do not write an English headline."
                },
                {
                    "role": "user",
                    "content": f"""
ይህን ዜና እንደ አማርኛ የLiverpool FC
ስፖርት ጋዜጠኛ በተፈጥሯዊ አማርኛ ጻፍ።

ርዕስ:
{item["title"]}

ይዘት:
{item["summary"]}

ምንጭ:
{item["source"]}

English ርዕስ አትተው።
ምንም ======== አትጠቀም።
ምንም ያልተሰጠ መረጃ አትጨምር።
"""
                }
            ],
            temperature=0.01,
            max_tokens=1200
        )

        text = (
            retry
            .choices[0]
            .message
            .content
            .strip()
        )

        text = remove_bad_ai_headers(text)

        if is_amharic(text):
            return text

        return None

    except Exception as e:
        logger.error(
            "Groq error: %s",
            e
        )

        return None


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

        match = re.search(
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
            response.text,
            flags=re.IGNORECASE
        )

        if match:
            return html_lib.unescape(
                match.group(1)
            )

        match = re.search(
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
            response.text,
            flags=re.IGNORECASE
        )

        if match:
            return html_lib.unescape(
                match.group(1)
            )

    except Exception as e:
        logger.warning(
            "Image error: %s",
            e
        )

    return None


def make_message(news, link, source):
    safe_news = html_lib.escape(
        news
    )

    safe_link = html_lib.escape(
        link,
        quote=True
    )

    safe_source = html_lib.escape(
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


async def send_news(item):
    if not can_post():
        logger.info(
            "30-minute limit reached. Waiting."
        )
        return False

    news = translate_news(item)

    if not news:
        logger.warning(
            "News rejected because translation failed."
        )
        return False

    if not is_amharic(news):
        logger.warning(
            "News rejected because it is not Amharic."
        )
        return False

    message = make_message(
        news,
        item["link"],
        item["source"]
    )

    image_url = get_image(
        item["link"]
    )

    sent_anywhere = False

    for channel in CHANNELS:
        try:
            if image_url and len(message) <= 1024:
                try:
                    await bot.send_photo(
                        chat_id=channel,
                        photo=image_url,
                        caption=message,
                        parse_mode=ParseMode.HTML
                    )
                except Exception as photo_error:
                    logger.warning(
                        "Photo failed for %s: %s",
                        channel,
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
                "News sent successfully to %s",
                channel
            )

            sent_anywhere = True

        except Exception as e:
            logger.error(
                "Telegram error for %s: %s",
                channel,
                e
            )

    if sent_anywhere:
        seen_news.add(
            item["id"]
        )

        save_memory()
        register_post()

        return True

    return False


def football_request(endpoint, params=None):
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
            logger.error(
                "Football API status: %s",
                response.status_code
            )
            return None

        return response.json()

    except Exception as e:
        logger.error(
            "Football API error: %s",
            e
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

        home_name = clean_text(
            home.get("name", "")
        )

        away_name = clean_text(
            away.get("name", "")
        )

        home_score = goals.get(
            "home"
        )

        away_score = goals.get(
            "away"
        )

        status = clean_text(
            fixture.get(
                "status",
                {}
            ).get(
                "long",
                ""
            )
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
            message += (
                f"⏱️ {minute}'\n"
            )

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
                    "Live send error for %s: %s",
                    channel,
                    e
                )


async def news_loop():
    while True:
        try:
            clean_post_history()

            if not can_post():
                logger.info(
                    "30-minute post limit reached."
                )

                await asyncio.sleep(
                    CHECK_EVERY
                )

                continue

            news = fetch_news()

            news = remove_duplicates(
                news
            )

            if not news:
                logger.info(
                    "No new trusted news found."
                )

                await asyncio.sleep(
                    CHECK_EVERY
                )

                continue

            news.sort(
                key=lambda x: (
                    len(x["summary"]),
                    len(x["title"])
                ),
                reverse=True
            )

            sent = False

            for item in news:
                if not can_post():
                    break

                sent = await send_news(
                    item
                )

                if sent:
                    await asyncio.sleep(10)

            if not sent:
                await asyncio.sleep(
                    CHECK_EVERY
                )

        except Exception as e:
            logger.exception(
                "News loop error: %s",
                e
            )

            await asyncio.sleep(
                CHECK_EVERY
            )


async def live_loop():
    while True:
        try:
            await send_live_matches()

        except Exception as e:
            logger.error(
                "Live loop error: %s",
                e
            )

        await asyncio.sleep(
            120
        )


async def main():
    logger.info(
        "Liverpool Amharic News Bot started"
    )

    logger.info(
        "Trusted sources enabled"
    )

    logger.info(
        "Maximum 2 news posts per 30 minutes"
    )

    await asyncio.gather(
        news_loop(),
        live_loop()
    )


if __name__ == "__main__":
    load_memory()
    load_post_history()

    try:
        asyncio.run(
            main()
        )

    except KeyboardInterrupt:
        logger.info(
            "Bot stopped"
        )
