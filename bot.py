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

# ------------------------------------------------------------
# NEWS CONTROL
# ------------------------------------------------------------

# Check for new news every 5 minutes.
# This DOES NOT mean posting every 5 minutes.
CHECK_EVERY = 5 * 60

# Maximum 2 news posts in 30 minutes.
NEWS_WINDOW = 30 * 60
MAX_NEWS_PER_WINDOW = 2

# Minimum time between two posts.
MIN_NEWS_GAP = 15 * 60

# Very short RSS snippets are rejected.
MIN_SUMMARY_LENGTH = 250

# Prefer articles with substantial content.
MIN_ARTICLE_LENGTH = 500

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
# TRUSTED SOURCES ONLY
# ============================================================

TRUSTED_SOURCES = {
    "Liverpool FC Official": [
        "Liverpool FC",
        "Liverpool Football Club",
        "Liverpoolfc.com",
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

        with open(
            SEEN_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

        if isinstance(data, list):
            return set(data)

    except Exception as e:

        logger.error(
            "Memory load error: %s",
            e
        )

    return set()


def save_seen():

    try:

        data = list(seen_news)[-3000:]

        with open(
            SEEN_FILE,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2
            )

    except Exception as e:

        logger.error(
            "Memory save error: %s",
            e
        )


seen_news = load_seen()


# ============================================================
# TEXT CLEANING
# ============================================================

def clean_text(text):

    if not text:
        return ""

    text = html_lib.unescape(
        str(text)
    )

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
        "bradley barcola",
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
                "LiverpoolNewsBot/2.0"
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

def make_id(title, link=""):

    # Title-based ID prevents the same story
    # being posted again through another URL.

    value = normalize(title)

    return hashlib.sha256(
        value.encode("utf-8")
    ).hexdigest()


# ============================================================
# ARTICLE EXTRACTION
# ============================================================

def extract_article_text(url):

    try:

        response = requests.get(
            url,
            timeout=25,
            headers={
                "User-Agent":
                "Mozilla/5.0 (Linux; Android 10) "
                "AppleWebKit/537.36 "
                "Chrome/149.0 Mobile Safari/537.36"
            }
        )

        if response.status_code != 200:
            return ""

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        # Remove useless elements.
        for tag in soup([
            "script",
            "style",
            "noscript",
            "svg",
            "nav",
            "footer",
            "header",
            "form",
            "aside"
        ]):

            tag.decompose()

        paragraphs = []

        # ----------------------------------------------------
        # First preference: article tag
        # ----------------------------------------------------

        article = soup.find("article")

        if article:

            for p in article.find_all("p"):

                text = clean_text(
                    p.get_text(
                        " ",
                        strip=True
                    )
                )

                if len(text) >= 40:
                    paragraphs.append(text)

        # ----------------------------------------------------
        # Second preference: main tag
        # ----------------------------------------------------

        if len(" ".join(paragraphs)) < MIN_ARTICLE_LENGTH:

            main = soup.find("main")

            if main:

                paragraphs = []

                for p in main.find_all("p"):

                    text = clean_text(
                        p.get_text(
                            " ",
                            strip=True
                        )
                    )

                    if len(text) >= 40:
                        paragraphs.append(text)

        # ----------------------------------------------------
        # Third preference: normal paragraphs
        # ----------------------------------------------------

        if len(" ".join(paragraphs)) < MIN_ARTICLE_LENGTH:

            paragraphs = []

            for p in soup.find_all("p"):

                text = clean_text(
                    p.get_text(
                        " ",
                        strip=True
                    )
                )

                if len(text) >= 50:
                    paragraphs.append(text)

        # Remove duplicate paragraphs.
        result = []
        paragraph_seen = set()

        for p in paragraphs:

            key = normalize(p)

            if key in paragraph_seen:
                continue

            paragraph_seen.add(key)
            result.append(p)

        text = "\n\n".join(result)

        # Limit extremely huge pages.
        return text[:12000]

    except Exception as e:

        logger.warning(
            "Article extraction error: %s",
            e
        )

        return ""


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

            # ------------------------------------------------
            # Liverpool filter
            # ------------------------------------------------

            if not is_liverpool_news(
                title,
                summary
            ):
                continue

            # ------------------------------------------------
            # Trusted source filter
            # ------------------------------------------------

            trusted = detect_source(
                title,
                summary,
                source_name
            )

            if not trusted:

                logger.info(
                    "Rejected untrusted source: %s",
                    source_name
                )

                continue

            # ------------------------------------------------
            # Reject tiny RSS snippets
            # ------------------------------------------------

            if len(summary) < MIN_SUMMARY_LENGTH:

                logger.info(
                    "RSS snippet too short: %s",
                    title
                )

                # Don't immediately reject.
                # We will try the full article.
                article_text = extract_article_text(
                    link
                )

            else:

                article_text = extract_article_text(
                    link
                )

            # ------------------------------------------------
            # If full article exists, use it.
            # Otherwise use RSS summary.
            # ------------------------------------------------

            if len(article_text) >= MIN_ARTICLE_LENGTH:

                full_content = article_text

            elif len(summary) >= MIN_SUMMARY_LENGTH:

                full_content = summary

            else:

                logger.info(
                    "Rejected because article content "
                    "is too short: %s",
                    title
                )

                continue

            # ------------------------------------------------
            # ID
            # ------------------------------------------------

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

                "article": full_content,

                "link": link,

                "source": trusted,

                "source_name": source_name,
            })

    return collected


# ============================================================
# QUALITY FILTER
# ============================================================

def is_quality_news(item):

    title = clean_text(
        item.get(
            "title",
            ""
        )
    )

    article = clean_text(
        item.get(
            "article",
            ""
        )
    )

    if len(title) < 25:
        return False

    if len(article) < MIN_ARTICLE_LENGTH:
        return False

    if len(article.split()) < 80:
        return False

    return True


# ============================================================
# REMOVE DUPLICATES
# ============================================================

def remove_duplicates(items):

    unique = []

    for item in items:

        if not is_quality_news(item):

            logger.info(
                "Rejected low-quality news: %s",
                item["title"]
            )

            continue

        duplicate = False

        for old in unique:

            title_similarity = similarity(
                item["title"],
                old["title"]
            )

            content_similarity = similarity(
                item["article"],
                old["article"]
            )

            if (
                title_similarity >= 0.65
                or content_similarity >= 0.75
            ):

                logger.info(
                    "Duplicate rejected: %s",
                    item["title"]
                )

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

    return amharic_ratio(text) >= 0.45


# ============================================================
# GROQ TRANSLATION
# ============================================================

def translate_news(item):

    prompt = f"""
አንተ የLiverpool FC የአማርኛ ስፖርት ጋዜጠኛ ነህ።

የተሰጠህን የLiverpool FC ዜና በተፈጥሯዊ፣
ግልጽ እና በጥራት ያለ አማርኛ ለTelegram አዘጋጅ።

በጣም አስፈላጊ ህጎች:

1. ርዕሱ በአማርኛ ይሁን።
2. ዋናው የዜና ይዘት በአማርኛ ይሁን።
3. English headline አትተው።
4. English paragraph አትተው።
5. የሰው ስሞች English ሊቀሩ ይችላሉ።
6. Liverpool FC እና የክለቦች ስሞች English ሊቀሩ ይችላሉ።
7. ቁጥር፣ ዋጋ፣ ቀን፣ ስም እና እውነታ አትቀይር።
8. ያልተሰጠህን መረጃ አትፍጠር።
9. Rumour ከሆነ በግልጽ "ተዘግቧል"፣
   "ሪፖርት እንደሚለው" ወይም "የሚነገረው"
   በማለት አቅርብ።
10. የተረጋገጠ ዝውውር ካልሆነ እንደ የተጠናቀቀ
    ዝውውር አታቅርብ።
11. የምንጩን ስም አትፍጠር።
12. የተሰጠውን ዜና በቂ ዝርዝር አቅርብ።
13. በጣም አጭር አታድርገው።
14. ግን ያልተሰጠ መረጃ በመጨመር አታራዝመው።

የሚወጣው ቅርጽ ይህ ብቻ ይሁን:

ርዕስ:
[አማርኛ ርዕስ]

ዜና:
[በአማርኛ የተዘጋጀ ዋና ዜና]

ምንጭ:
[{item["source"]}]

የዋናው ርዕስ:
{item["title"]}

የዋናው ዜና ይዘት:
{item["article"]}
"""

    try:

        result = groq.chat.completions.create(

            model="llama-3.3-70b-versatile",

            messages=[

                {
                    "role": "system",
                    "content":
                    "You are a professional Ethiopian "
                    "Amharic football journalist. "
                    "Write natural Amharic."
                },

                {
                    "role": "user",
                    "content": prompt
                }
            ],

            temperature=0.05,

            max_tokens=1400
        )

        text = (
            result
            .choices[0]
            .message
            .content
            .strip()
        )

        # ----------------------------------------------------
        # Strong Amharic validation
        # ----------------------------------------------------

        if is_amharic(text):

            return text

        logger.warning(
            "Groq returned too much English. Retrying..."
        )

        # ----------------------------------------------------
        # Retry
        # ----------------------------------------------------

        retry = groq.chat.completions.create(

            model="llama-3.3-70b-versatile",

            messages=[

                {
                    "role": "system",
                    "content":
                    "Write ONLY natural Ethiopian "
                    "Amharic football news. "
                    "The headline must also be Amharic."
                },

                {
                    "role": "user",
                    "content": f"""
ይህን ዜና በአማርኛ ብቻ እንደገና አዘጋጀው።

English headline:
{item["title"]}

Article:
{item["article"]}

Source:
{item["source"]}

ቅርጽ:

ርዕስ:
[አማርኛ]

ዜና:
[አማርኛ]

ምንጭ:
[{item["source"]}]
"""
                }
            ],

            temperature=0.02,

            max_tokens=1400
        )

        text = (
            retry
            .choices[0]
            .message
            .content
            .strip()
        )

        if not is_amharic(text):

            logger.error(
                "News rejected after Amharic retry."
            )

            return None

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

            image_url = image.get(
                "content"
            )

            if image_url:
                return image_url

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
# SEND NEWS
# ============================================================

async def send_news(item):

    news = translate_news(
        item
    )

    if not news:

        logger.warning(
            "News was not translated."
        )

        return False

    if not is_amharic(news):

        logger.error(
            "News rejected: insufficient Amharic."
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
            .get(
                "status",
                {}
            )
            .get(
                "long",
                ""
            )
        )

        minute = (
            fixture
            .get(
                "status",
                {}
            )
            .get(
                "elapsed"
            )
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
                    "Live send error: %s",
                    e
                )


# ============================================================
# NEWS LOOP
# ============================================================

async def news_loop():

    window_start = time.time()

    posts_in_window = 0

    last_post_time = 0

    while True:

        try:

            now = time.time()

            # ------------------------------------------------
            # Reset 30-minute window
            # ------------------------------------------------

            if (
                now - window_start
                >= NEWS_WINDOW
            ):

                window_start = now

                posts_in_window = 0

                logger.info(
                    "30-minute news window reset."
                )

            # ------------------------------------------------
            # Maximum 2 news posts
            # ------------------------------------------------

            if (
                posts_in_window
                >= MAX_NEWS_PER_WINDOW
            ):

                remaining = (
                    NEWS_WINDOW
                    - (
                        now
                        - window_start
                    )
                )

                logger.info(
                    "Maximum news reached. "
                    "Waiting %.1f minutes.",
                    remaining / 60
                )

                await asyncio.sleep(
                    max(
                        60,
                        remaining
                    )
                )

                continue

            # ------------------------------------------------
            # Minimum gap
            # ------------------------------------------------

            if last_post_time:

                elapsed = (
                    now
                    - last_post_time
                )

                if elapsed < MIN_NEWS_GAP:

                    wait_time = (
                        MIN_NEWS_GAP
                        - elapsed
                    )

                    logger.info(
                        "Waiting %.1f minutes "
                        "before next news.",
                        wait_time / 60
                    )

                    await asyncio.sleep(
                        wait_time
                    )

                    continue

            # ------------------------------------------------
            # Search
            # ------------------------------------------------

            logger.info(
                "Checking trusted Liverpool news..."
            )

            news = fetch_news()

            news = remove_duplicates(
                news
            )

            if not news:

                logger.info(
                    "No quality new Liverpool news."
                )

                await asyncio.sleep(
                    CHECK_EVERY
                )

                continue

            # ------------------------------------------------
            # Best article first
            # ------------------------------------------------

            news.sort(

                key=lambda x: len(
                    x.get(
                        "article",
                        ""
                    )
                ),

                reverse=True
            )

            item = news[0]

            logger.info(
                "Selected news: %s",
                item["title"]
            )

            logger.info(
                "Trusted source: %s",
                item["source"]
            )

            # ------------------------------------------------
            # Send
            # ------------------------------------------------

            sent = await send_news(
                item
            )

            if sent:

                posts_in_window += 1

                last_post_time = time.time()

                logger.info(
                    "News posted successfully. "
                    "Current window: %d/%d",
                    posts_in_window,
                    MAX_NEWS_PER_WINDOW
                )

                await asyncio.sleep(
                    MIN_NEWS_GAP
                )

            else:

                logger.warning(
                    "News was not posted. "
                    "Trying again later."
                )

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

        # Live match check every 2 minutes.
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
        "Maximum: 2 news / 30 minutes"
    )

    logger.info(
        "Minimum gap: 15 minutes"
    )

    logger.info(
        "Trusted sources only"
    )

    logger.info(
        "Amharic translation enabled"
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
