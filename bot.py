import os
import re
import json
import time
import hashlib
import logging
from difflib import SequenceMatcher
from urllib.parse import quote_plus

import requests
import feedparser

from groq import Groq
from telegram import Bot
from telegram.constants import ParseMode


# =========================================================
# CONFIGURATION
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# ሁለቱም የLiverpool ቻናሎች
CHANNELS = [
    "@yegnaLiverpool",
    "@yegnaLiverpoolET",
]

CHECK_INTERVAL = 300          # 5 minutes
MAX_NEWS_PER_CHECK = 3
SEEN_FILE = "seen_news.json"


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)


# =========================================================
# REQUIRED KEYS
# =========================================================

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is missing")

if not GROQ_API_KEY:
    raise ValueError("GROQ_API_KEY is missing")


bot = Bot(token=BOT_TOKEN)
groq = Groq(api_key=GROQ_API_KEY)


# =========================================================
# APPROVED SOURCES
# =========================================================

APPROVED_SOURCES = {
    "Liverpool FC Official": [
        "Liverpool FC",
        "Liverpool Football Club",
    ],

    "Paul Joyce": [
        "The Times",
        "Times",
    ],

    "David Ornstein": [
        "The Athletic",
        "Athletic",
    ],

    "James Pearce": [
        "The Athletic",
        "Athletic",
    ],

    "Lewis Steele": [
        "Daily Mail",
        "MailOnline",
        "DailyMail",
    ],

    "Melissa Reddy": [
        "Sky Sports",
        "Sky",
    ],

    "Fabrizio Romano": [
        "Fabrizio Romano",
    ],
}


# =========================================================
# SEARCH QUERIES
# =========================================================

SEARCH_QUERIES = [
    '"Liverpool FC" "Liverpool FC"',

    '"Liverpool" "Paul Joyce"',

    '"Liverpool" "David Ornstein"',

    '"Liverpool" "James Pearce"',

    '"Liverpool" "Lewis Steele"',

    '"Liverpool" "Melissa Reddy"',

    '"Liverpool" "Fabrizio Romano"',
]


# =========================================================
# LOAD SEEN NEWS
# =========================================================

def load_seen_news():

    try:

        if not os.path.exists(SEEN_FILE):
            return set()

        with open(
            SEEN_FILE,
            "r",
            encoding="utf-8"
        ) as file:

            data = json.load(file)

        if isinstance(data, list):
            return set(data)

        return set()

    except Exception as e:

        logger.error(
            "Could not load seen news: %s",
            e
        )

        return set()


def save_seen_news(seen):

    try:

        data = list(seen)[-1500:]

        with open(
            SEEN_FILE,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                data,
                file,
                ensure_ascii=False,
                indent=2
            )

    except Exception as e:

        logger.error(
            "Could not save seen news: %s",
            e
        )


seen_news = load_seen_news()


# =========================================================
# TEXT CLEANING
# =========================================================

def clean_text(text):

    if not text:
        return ""

    text = re.sub(
        r"<[^>]+>",
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
# NEWS ID
# =========================================================

def create_news_id(title, link):

    raw = (
        title.strip().lower()
        + "|"
        + link.strip().lower()
    )

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()


# =========================================================
# CHECK LIVERPOOL NEWS
# =========================================================

def is_liverpool_news(title, summary):

    text = (
        f"{title} {summary}"
    ).lower()

    keywords = [
        "liverpool",
        "liverpool fc",
        "reds",
        "anfield",
        "lfc",
        "arne slot",
        "andoni iraola",
        "virgil van dijk",
        "mohamed salah",
        "florian wirtz",
        "alexis mac allister",
        "ryan gravenberch",
        "dominik szoboszlai",
        "jeremy jacquet",
        "giovanni leoni",
    ]

    return any(
        keyword in text
        for keyword in keywords
    )


# =========================================================
# AMHARIC CHECK
# =========================================================

def amharic_ratio(text):

    if not text:
        return 0

    amharic_chars = len(
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

    return amharic_chars / letters


def is_good_amharic(text):

    if not text:
        return False

    ratio = amharic_ratio(text)

    # At least 35% Ge'ez characters
    return ratio >= 0.35


# =========================================================
# GOOGLE NEWS RSS
# =========================================================

def get_rss(query):

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
                "Mozilla/5.0"
            }
        )

        response.raise_for_status()

        return feedparser.parse(
            response.content
        )

    except Exception as e:

        logger.error(
            "RSS request failed: %s",
            e
        )

        return None


# =========================================================
# SOURCE DETECTION
# =========================================================

def get_source_name(entry):

    try:

        source = getattr(
            entry,
            "source",
            None
        )

        if source:

            name = getattr(
                source,
                "title",
                ""
            )

            return clean_text(name)

    except Exception:
        pass

    return ""


def detect_approved_source(
    title,
    summary,
    source_name
):

    combined = (
        f"{title} "
        f"{summary} "
        f"{source_name}"
    ).lower()

    for source, aliases in APPROVED_SOURCES.items():

        for alias in aliases:

            if alias.lower() in combined:

                return source

    return None


# =========================================================
# FETCH NEWS
# =========================================================

def fetch_news():

    results = []

    for query in SEARCH_QUERIES:

        logger.info(
            "Searching: %s",
            query
        )

        feed = get_rss(query)

        if not feed:
            continue

        for entry in feed.entries[:10]:

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

            link = getattr(
                entry,
                "link",
                ""
            )

            if not title or not link:
                continue

            if not is_liverpool_news(
                title,
                summary
            ):
                continue

            source_name = get_source_name(
                entry
            )

            approved_source = detect_approved_source(
                title,
                summary,
                source_name
            )

            if not approved_source:
                continue

            news_id = create_news_id(
                title,
                link
            )

            if news_id in seen_news:
                continue

            results.append({
                "id": news_id,
                "title": title,
                "summary": summary,
                "link": link,
                "source": approved_source,
                "source_name": source_name,
            })

    return results


# =========================================================
# REMOVE DUPLICATES
# =========================================================

def similarity(a, b):

    return SequenceMatcher(
        None,
        a.lower(),
        b.lower()
    ).ratio()


def remove_duplicate_news(news):

    unique = []

    for item in news:

        duplicate = False

        for existing in unique:

            title_score = similarity(
                item["title"],
                existing["title"]
            )

            content_score = similarity(
                item["summary"],
                existing["summary"]
            )

            if (
                title_score >= 0.70
                or content_score >= 0.78
            ):

                duplicate = True
                break

        if not duplicate:
            unique.append(item)

    return unique


# =========================================================
# GROQ - AMHARIC NEWS
# =========================================================

def generate_amharic_news(item):

    prompt = f"""
አንተ የLiverpool FC ባለሙያ የአማርኛ የስፖርት ዜና አዘጋጅ ነህ።

ከታች የተሰጠውን የLiverpool ዜና ወደ ተፈጥሯዊ፣
ግልጽ፣ ሙያዊ እና ለTelegram ተስማሚ አማርኛ ቀይር።

ጥብቅ ህጎች:

1. የርዕሱን 100% በአማርኛ ጻፍ።
2. የዜናውን ዋና ይዘት በአማርኛ ጻፍ።
3. English headline በፍጹም አትተው።
4. English paragraph በፍጹም አትተው።
5. የተጫዋች፣ የአሰልጣኝ፣ የክለብ እና የውድድር ስሞች
   በEnglish ሊቀሩ ይችላሉ።
6. ቁጥር፣ ዋጋ፣ ቀን እና የዝውውር መረጃ አትቀይር።
7. ያልተሰጠህን መረጃ አትፍጠር።
8. ወሬን እንደ እውነት አታቅርብ።
9. የዝውውር ዜና ከሆነ "ሪፖርት መሠረት"፣
   "ውይይት አለ"፣ "ተጠይቋል" እና "ይፈልጋል"
   የሚሉትን ሁኔታዎች በግልጽ አሳይ።
10. አትጨምር፣ አትገምት፣ አትዋሽ።
11. በአማርኛ በተፈጥሯዊ የስፖርት ጋዜጠኛ ቋንቋ ጻፍ።
12. ርዕሱ አጭር፣ ማራኪ እና የዜናውን ዋና ነገር የሚያሳይ ይሁን።
13. በመጨረሻ የምንጩን ስም በአማርኛ አሳይ።
14. Markdown አትጠቀም።
15. የምንጩን English headline እንደገና አታሳይ።

የሚመለሰው በዚህ ቅርጽ ብቻ ይሁን:

ርዕስ:
[አማርኛ ርዕስ]

ዜና:
[አማርኛ ዜና]

ምንጭ:
[{item["source"]}]

የዋናው ርዕስ:
{item["title"]}

የዋናው ይዘት:
{item["summary"]}

የተፈቀደው ምንጭ:
{item["source"]}
"""

    try:

        response = groq.chat.completions.create(
            model="llama-3.3-70b-versatile",

            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an expert Ethiopian "
                        "Amharic football news editor. "
                        "Your output must be predominantly "
                        "Amharic and the headline must be "
                        "written in Amharic."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],

            temperature=0.15,
            max_tokens=900,
        )

        result = (
            response
            .choices[0]
            .message
            .content
            .strip()
        )

        # If AI returned too much English,
        # ask it once again.
        if not is_good_amharic(result):

            retry_prompt = f"""
ይህን የLiverpool ዜና እንደገና ጻፍ።

በጣም አስፈላጊ:
- ርዕስ = አማርኛ
- ዜና = አማርኛ
- English ሀረጎችን አትተው
- የሰዎች/ክለቦች ስሞች ብቻ English ሊሆኑ ይችላሉ
- ምንም አዲስ መረጃ አትጨምር

የዋናው ዜና:
{item["title"]}

{item["summary"]}

ቅርጽ:

ርዕስ:
...

ዜና:
...

ምንጭ:
{item["source"]}
"""

            retry = groq.chat.completions.create(
                model="llama-3.3-70b-versatile",

                messages=[
                    {
                        "role": "system",
                        "content":
                        "Write the football news "
                        "in natural Amharic."
                    },
                    {
                        "role": "user",
                        "content":
                        retry_prompt,
                    },
                ],

                temperature=0.1,
                max_tokens=900,
            )

            result = (
                retry
                .choices[0]
                .message
                .content
                .strip()
            )

        return result

    except Exception as e:

        logger.error(
            "Groq error: %s",
            e
        )

        return None


# =========================================================
# FORMAT TELEGRAM MESSAGE
# =========================================================

def format_message(amharic_news, link):

    message = (
        "🔴 <b>LIVERPOOL NEWS</b>\n\n"
        f"{amharic_news}\n\n"
        "🔗 <a href=\""
        f"{link}"
        "\">የዋናውን ዜና ይመልከቱ</a>\n\n"
        "🔴 <b>YN Liverpool</b>"
    )

    return message


# =========================================================
# SEND TO ALL CHANNELS
# =========================================================

async def send_to_channels(message):

    success_count = 0

    for channel in CHANNELS:

        try:

            await bot.send_message(
                chat_id=channel,
                text=message,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=False,
            )

            logger.info(
                "Posted successfully to %s",
                channel
            )

            success_count += 1

        except Exception as e:

            logger.error(
                "Could not post to %s: %s",
                channel,
                e
            )

    return success_count


# =========================================================
# PROCESS ONE NEWS
# =========================================================

async def process_news(item):

    logger.info(
        "Preparing: %s",
        item["title"]
    )

    amharic_news = generate_amharic_news(
        item
    )

    if not amharic_news:

        logger.error(
            "AI did not generate news."
        )

        return False

    # Safety check
    if not is_good_amharic(
        amharic_news
    ):

        logger.error(
            "AI response is not sufficiently Amharic."
        )

        return False

    message = format_message(
        amharic_news,
        item["link"]
    )

    sent = await send_to_channels(
        message
    )

    # Mark as seen only if at least one
    # channel received the post.
    if sent > 0:

        seen_news.add(
            item["id"]
        )

        save_seen_news(
            seen_news
        )

        return True

    return False


# =========================================================
# CHECK NEWS
# =========================================================

async def check_news():

    logger.info(
        "Checking for new Liverpool news..."
    )

    news = fetch_news()

    if not news:

        logger.info(
            "No new approved Liverpool news."
        )

        return

    news = remove_duplicate_news(
        news
    )

    news = news[:MAX_NEWS_PER_CHECK]

    logger.info(
        "New unique stories: %d",
        len(news)
    )

    for item in news:

        await process_news(
            item
        )

        # Avoid sending too fast
        time.sleep(8)


# =========================================================
# MAIN LOOP
# =========================================================

async def main():

    logger.info(
        "🔴 Liverpool Amharic News Bot started!"
    )

    logger.info(
        "Channels: %s",
        ", ".join(CHANNELS)
    )

    while True:

        try:

            await check_news()

        except Exception as e:

            logger.exception(
                "Unexpected error: %s",
                e
            )

        logger.info(
            "Waiting 5 minutes..."
        )

        await __import__(
            "asyncio"
        ).sleep(
            CHECK_INTERVAL
        )


# =========================================================
# START BOT
# =========================================================

if __name__ == "__main__":

    import asyncio

    try:

        asyncio.run(
            main()
        )

    except KeyboardInterrupt:

        logger.info(
            "Bot stopped."
        )
