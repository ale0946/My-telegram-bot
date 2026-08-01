import os
import json
import re
import hashlib
import asyncio
from datetime import datetime, timezone, timedelta

import feedparser
from groq import Groq
from telegram import Bot


# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

CHANNEL_ID = "@yegnaLiverpool"

NEWS_FILE = "posted_news.json"

# የቆየ ዜና ከዚህ በላይ ከሆነ አንልክም
MAX_NEWS_AGE_HOURS = 24

# ዜናን ለመፈተሽ የሚደገምበት ጊዜ
CHECK_INTERVAL = 300  # 5 minutes


if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN አልተገኘም።")

if not GROQ_API_KEY:
    raise ValueError("GROQ_API_KEY አልተገኘም።")


bot = Bot(token=BOT_TOKEN)
groq = Groq(api_key=GROQ_API_KEY)


# =========================================================
# TRUSTED LIVERPOOL NEWS SOURCES
# =========================================================

RSS_FEEDS = [
    {
        "name": "Liverpool FC",
        "url": "https://www.liverpoolfc.com/news",
    },

    {
        "name": "Google News - Liverpool FC",
        "url": (
            "https://news.google.com/rss/search?"
            "q=Liverpool+FC&hl=en-GB&gl=GB&ceid=GB:en"
        ),
    },

    {
        "name": "Google News - Liverpool Transfer",
        "url": (
            "https://news.google.com/rss/search?"
            "q=Liverpool+transfer&hl=en-GB&gl=GB&ceid=GB:en"
        ),
    },
]


# =========================================================
# FILE / HISTORY
# =========================================================

def load_posted_news():
    if not os.path.exists(NEWS_FILE):
        return []

    try:
        with open(NEWS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, list):
            return data

        return []

    except Exception:
        return []


def save_posted_news(data):
    # 500 የመጨረሻ ዜናዎችን ብቻ እንይዛለን
    data = data[-500:]

    with open(NEWS_FILE, "w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )


posted_news = load_posted_news()


# =========================================================
# TEXT HELPERS
# =========================================================

def normalize_text(text):
    if not text:
        return ""

    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)

    return text


def make_hash(text):
    normalized = normalize_text(text)

    return hashlib.sha256(
        normalized.encode("utf-8")
    ).hexdigest()


def is_duplicate(title, summary):
    global posted_news

    title_hash = make_hash(title)

    # 1. በትክክል የተመዘገበ title
    for item in posted_news:
        if item.get("title_hash") == title_hash:
            return True

    # 2. በጣም ተመሳሳይ title
    new_title = normalize_text(title)

    for item in posted_news:
        old_title = normalize_text(
            item.get("title", "")
        )

        if not old_title:
            continue

        # ቀላል similarity
        new_words = set(new_title.split())
        old_words = set(old_title.split())

        if len(new_words) >= 4 and len(old_words) >= 4:
            intersection = len(
                new_words.intersection(old_words)
            )

            union = len(
                new_words.union(old_words)
            )

            similarity = intersection / union

            if similarity >= 0.70:
                return True

    return False


def remember_news(title, link):
    global posted_news

    posted_news.append(
        {
            "title": title,
            "title_hash": make_hash(title),
            "link": link,
            "posted_at": datetime.now(
                timezone.utc
            ).isoformat()
        }
    )

    save_posted_news(posted_news)


# =========================================================
# DATE CHECK
# =========================================================

def get_entry_datetime(entry):
    try:
        if getattr(entry, "published_parsed", None):
            return datetime(
                *entry.published_parsed[:6],
                tzinfo=timezone.utc
            )

        if getattr(entry, "updated_parsed", None):
            return datetime(
                *entry.updated_parsed[:6],
                tzinfo=timezone.utc
            )

    except Exception:
        pass

    return None


def is_recent(entry):
    published = get_entry_datetime(entry)

    # Date ካልተገኘ በቀጥታ አንቀበልም
    if not published:
        return False

    now = datetime.now(timezone.utc)

    age = now - published

    if age < timedelta(minutes=-10):
        return False

    if age > timedelta(hours=MAX_NEWS_AGE_HOURS):
        return False

    return True


# =========================================================
# LIVERPOOL FILTER
# =========================================================

LIVERPOOL_KEYWORDS = [
    "liverpool",
    "lfc",
    "anfield",
    "reds",
    "arne slot",
    "virgil van dijk",
    "mohamed salah",
    "alexis mac allister",
    "ryan gravenberch",
    "domin ik szoboszlai",
    "dominík szoboszlai",
    "florian wirtz",
    "jeremy jacquet",
    "giovanni leoni",
]


def is_liverpool_news(title, summary):
    text = normalize_text(
        f"{title} {summary}"
    )

    for keyword in LIVERPOOL_KEYWORDS:
        if keyword in text:
            return True

    return False


# =========================================================
# RSS FETCH
# =========================================================

def fetch_news():
    all_news = []

    for source in RSS_FEEDS:
        try:
            feed = feedparser.parse(
                source["url"]
            )

            for entry in feed.entries[:20]:

                title = entry.get(
                    "title",
                    ""
                ).strip()

                summary = entry.get(
                    "summary",
                    ""
                ).strip()

                link = entry.get(
                    "link",
                    ""
                ).strip()

                if not title or not link:
                    continue

                # ቆይቷል?
                if not is_recent(entry):
                    continue

                # Liverpool ነው?
                if not is_liverpool_news(
                    title,
                    summary
                ):
                    continue

                # Duplicate ነው?
                if is_duplicate(
                    title,
                    summary
                ):
                    continue

                all_news.append(
                    {
                        "title": title,
                        "summary": summary,
                        "link": link,
                        "source": source["name"],
                        "published": get_entry_datetime(
                            entry
                        )
                    }
                )

        except Exception as e:
            print(
                f"RSS error - {source['name']}: {e}"
            )

    # አዲሱን በመጀመሪያ
    all_news.sort(
        key=lambda x: x["published"]
        if x["published"]
        else datetime.min.replace(
            tzinfo=timezone.utc
        ),
        reverse=True
    )

    return all_news


# =========================================================
# GROQ - AMHARIC NEWS WRITER
# =========================================================

def create_amharic_news(news):
    title = news["title"]
    summary = news["summary"]

    prompt = f"""
አንተ የLiverpool FC የአማርኛ ዜና አዘጋጅ ነህ።

ከታች ያለውን ዜና ተጠቅመህ
Telegram ላይ ለመለጠፍ ተፈጥሯዊ,
ግልጽ እና አጭር የአማርኛ ዜና አዘጋጅ።

IMPORTANT:
- 100% በአማርኛ ጻፍ።
- English headline አታስገባ።
- English paragraph አታስገባ።
- የሌለውን መረጃ አትፍጠር።
- የዜናውን ትርጉም አትቀይር።
- ከርዕሱ እና ከsummary የተረጋገጠውን ብቻ ተጠቀም።
- ከ2-4 አጭር አንቀጾች ይሁን።
- የሚያስፈልግ ከሆነ 🔴⚽📰 ተጠቀም።

FORMAT:

🔴 LIVERPOOL NEWS

[የአማርኛ ርዕስ]

[የአማርኛ ዜና]

Source: {news['source']}

ORIGINAL TITLE:
{title}

SUMMARY:
{summary}
"""

    try:
        response = groq.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a professional "
                        "Amharic Liverpool FC news editor."
                    )
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.2,
            max_tokens=700
        )

        result = response.choices[0].message.content.strip()

        if not result:
            return None

        return result

    except Exception as e:
        print(f"Groq error: {e}")
        return None


# =========================================================
# SEND TELEGRAM
# =========================================================

async def send_news(news):
    text = create_amharic_news(news)

    if not text:
        return False

    try:
        await bot.send_message(
            chat_id=CHANNEL_ID,
            text=text,
            disable_web_page_preview=False
        )

        remember_news(
            news["title"],
            news["link"]
        )

        print(
            f"POSTED: {news['title']}"
        )

        return True

    except Exception as e:
        print(
            f"Telegram error: {e}"
        )

        return False


# =========================================================
# NEWS LOOP
# =========================================================

async def news_loop():
    print("🔴 Liverpool News Bot started!")

    while True:

        try:
            news_list = fetch_news()

            print(
                f"New eligible news: "
                f"{len(news_list)}"
            )

            # በአንድ check አንድ ዜና ብቻ
            # እንዳይጨናነቅ Telegram
            if news_list:

                latest_news = news_list[0]

                await send_news(
                    latest_news
                )

        except Exception as e:
            print(
                f"Main loop error: {e}"
            )

        await asyncio.sleep(
            CHECK_INTERVAL
        )


# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":
    asyncio.run(
        news_loop()
    )
