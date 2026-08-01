import os
import json
import re
import hashlib
import asyncio
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher

import feedparser
from groq import Groq
from telegram import Bot


# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# GitHub Secret CHANNEL_ID ካለ ይጠቀማል
# ከሌለ ይህን ይጠቀማል
CHANNEL_ID = os.getenv(
    "CHANNEL_ID",
    "@yegnaLiverpool"
)

NEWS_FILE = "posted_news.json"

# ከዚህ በላይ የቆየ ዜና አይላክም
MAX_NEWS_AGE_HOURS = 24

# በየ5 ደቂቃው ይፈትሻል
CHECK_INTERVAL = 300

# በአንድ check አንድ አዲስ ዜና
MAX_POSTS_PER_CHECK = 1


# =========================================================
# ENVIRONMENT CHECK
# =========================================================

if not BOT_TOKEN:
    raise RuntimeError(
        "❌ BOT_TOKEN Secret አልተገኘም።"
    )

if not GROQ_API_KEY:
    raise RuntimeError(
        "❌ GROQ_API_KEY Secret አልተገኘም።"
    )


# =========================================================
# CLIENTS
# =========================================================

bot = Bot(token=BOT_TOKEN)

groq = Groq(
    api_key=GROQ_API_KEY
)


# =========================================================
# TRUSTED SOURCES
# =========================================================
#
# Google News RSS እንደ RSS reader ብቻ እንጠቀማለን።
# Search queryዎቹ የተወሰኑትን የታመኑ ምንጮች
# ብቻ ለመፈለግ ናቸው።
# =========================================================

RSS_FEEDS = [

    {
        "name": "Liverpool FC Official",
        "query": (
            "site:liverpoolfc.com "
            "Liverpool FC"
        ),
    },

    {
        "name": "Paul Joyce",
        "query": (
            "\"Paul Joyce\" "
            "Liverpool"
        ),
    },

    {
        "name": "David Ornstein",
        "query": (
            "\"David Ornstein\" "
            "Liverpool"
        ),
    },

    {
        "name": "Fabrizio Romano",
        "query": (
            "\"Fabrizio Romano\" "
            "Liverpool"
        ),
    },

    {
        "name": "James Pearce",
        "query": (
            "\"James Pearce\" "
            "Liverpool"
        ),
    },
]


def build_google_news_url(query):
    return (
        "https://news.google.com/rss/search?"
        "q="
        + query.replace(" ", "+")
        + "&hl=en-GB&gl=GB&ceid=GB:en"
    )


# =========================================================
# HISTORY
# =========================================================

def load_posted_news():
    if not os.path.exists(NEWS_FILE):
        return []

    try:
        with open(
            NEWS_FILE,
            "r",
            encoding="utf-8"
        ) as file:

            data = json.load(file)

        if isinstance(data, list):
            return data

    except Exception as error:
        print(
            f"⚠️ History read error: {error}"
        )

    return []


def save_posted_news(data):

    # 1000 የመጨረሻ ዜናዎችን እንይዛለን
    data = data[-1000:]

    try:

        with open(
            NEWS_FILE,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                data,
                file,
                ensure_ascii=False,
                indent=2
            )

    except Exception as error:

        print(
            f"❌ History save error: {error}"
        )


posted_news = load_posted_news()


# =========================================================
# TEXT NORMALIZATION
# =========================================================

def normalize_text(text):

    if not text:
        return ""

    text = text.lower()

    # HTML tags ካሉ አስወግድ
    text = re.sub(
        r"<[^>]+>",
        " ",
        text
    )

    # URLs አስወግድ
    text = re.sub(
        r"https?://\S+",
        " ",
        text
    )

    # punctuation አስተካክል
    text = re.sub(
        r"[^\w\s]",
        " ",
        text,
        flags=re.UNICODE
    )

    # spaces አስተካክል
    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


def make_hash(text):

    normalized = normalize_text(text)

    return hashlib.sha256(
        normalized.encode("utf-8")
    ).hexdigest()


# =========================================================
# DUPLICATE DETECTION
# =========================================================

def is_duplicate(title, link):

    title_clean = normalize_text(title)

    title_hash = make_hash(title)

    link_clean = link.strip()

    for old in posted_news:

        old_hash = old.get(
            "title_hash",
            ""
        )

        old_title = normalize_text(
            old.get("title", "")
        )

        old_link = old.get(
            "link",
            ""
        ).strip()

        # 1. Exact title
        if title_hash == old_hash:
            return True

        # 2. Exact URL
        if link_clean and old_link:
            if link_clean == old_link:
                return True

        # 3. Similar title
        if (
            len(title_clean) >= 20
            and len(old_title) >= 20
        ):

            similarity = SequenceMatcher(
                None,
                title_clean,
                old_title
            ).ratio()

            if similarity >= 0.82:
                return True

    return False


def remember_news(
    title,
    link,
    source,
    published
):

    global posted_news

    posted_news.append(
        {
            "title": title,
            "title_hash": make_hash(title),
            "link": link,
            "source": source,
            "published": (
                published.isoformat()
                if published
                else ""
            ),
            "posted_at": datetime.now(
                timezone.utc
            ).isoformat()
        }
    )

    save_posted_news(
        posted_news
    )


# =========================================================
# DATE
# =========================================================

def get_entry_datetime(entry):

    try:

        if getattr(
            entry,
            "published_parsed",
            None
        ):

            return datetime(
                *entry.published_parsed[:6],
                tzinfo=timezone.utc
            )

        if getattr(
            entry,
            "updated_parsed",
            None
        ):

            return datetime(
                *entry.updated_parsed[:6],
                tzinfo=timezone.utc
            )

    except Exception as error:

        print(
            f"⚠️ Date error: {error}"
        )

    return None


def is_recent(entry):

    published = get_entry_datetime(
        entry
    )

    # Date ካልተገኘ አንቀበልም
    if not published:
        return False

    now = datetime.now(
        timezone.utc
    )

    age = now - published

    # Future date ካለ
    if age < timedelta(
        minutes=-10
    ):
        return False

    # 24 ሰዓት ካለፈ
    if age > timedelta(
        hours=MAX_NEWS_AGE_HOURS
    ):
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
    "dominik szoboszlai",
    "florian wirtz",
    "jeremy jacquet",
    "giovanni leoni",
]


def is_liverpool_news(
    title,
    summary
):

    text = normalize_text(
        title + " " + summary
    )

    for keyword in LIVERPOOL_KEYWORDS:

        if normalize_text(keyword) in text:
            return True

    return False


# =========================================================
# FETCH NEWS
# =========================================================

def fetch_news():

    candidates = []

    print(
        "🔎 Starting news search..."
    )

    for source in RSS_FEEDS:

        source_name = source["name"]

        try:

            url = build_google_news_url(
                source["query"]
            )

            print(
                f"🔎 Checking: {source_name}"
            )

            feed = feedparser.parse(
                url
            )

            if getattr(
                feed,
                "bozo",
                False
            ):

                print(
                    f"⚠️ Feed warning: "
                    f"{source_name}"
                )

            entries = getattr(
                feed,
                "entries",
                []
            )

            print(
                f"   Found {len(entries)} items"
            )

            for entry in entries[:30]:

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

                published = (
                    get_entry_datetime(
                        entry
                    )
                )

                if not title:
                    continue

                if not link:
                    continue

                # ወቅታዊ ነው?
                if not is_recent(entry):
                    continue

                # Liverpool ነው?
                if not is_liverpool_news(
                    title,
                    summary
                ):
                    continue

                # ቀድሞ ተልኳል?
                if is_duplicate(
                    title,
                    link
                ):
                    continue

                candidates.append(
                    {
                        "title": title,
                        "summary": summary,
                        "link": link,
                        "source": source_name,
                        "published": published
                    }
                )

        except Exception as error:

            print(
                f"❌ {source_name} error: "
                f"{error}"
            )

    # አዲሱን በመጀመሪያ
    candidates.sort(
        key=lambda item: (
            item["published"]
            or datetime.min.replace(
                tzinfo=timezone.utc
            )
        ),
        reverse=True
    )

    print(
        f"📰 New eligible news: "
        f"{len(candidates)}"
    )

    return candidates


# =========================================================
# GROQ AMHARIC WRITER
# =========================================================

def create_amharic_news(news):

    title = news["title"]

    summary = news["summary"]

    source = news["source"]

    prompt = f"""
አንተ የLiverpool FC የአማርኛ
ዜና አዘጋጅ ነህ።

ከታች ያለውን የዜና መረጃ ተጠቅመህ
Telegram ላይ ለመለጠፍ ተፈጥሯዊ፣
ግልጽ እና ሙያዊ የአማርኛ ዜና አዘጋጅ።

ጥብቅ ህጎች:

1. 100% በአማርኛ ጻፍ።
2. English headline አታስገባ።
3. English paragraph አታስገባ።
4. የሌለውን መረጃ አትፍጠር።
5. የተሰጠውን መረጃ ብቻ ተጠቀም።
6. የዜናውን ትርጉም አትቀይር።
7. ከ2-4 አጭር አንቀጾች ይሁን።
8. ርዕሱ አጭርና ማራኪ ይሁን።
9. የምንጩን ስም በመጨረሻ በአማርኛ ጻፍ።
10. Original English title አታሳይ።
11. English summary አታሳይ።

FORMAT:

🔴 LIVERPOOL NEWS

[አማርኛ ርዕስ]

[አማርኛ ዜና]

📰 ምንጭ: {source}

የዜና ርዕስ:
{title}

የዜና ማጠቃለያ:
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
                        "Amharic Liverpool FC "
                        "news editor. "
                        "Never invent facts."
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

        result = (
            response
            .choices[0]
            .message
            .content
            .strip()
        )

        if not result:
            print(
                "❌ Groq returned empty text."
            )
            return None

        return result

    except Exception as error:

        print(
            f"❌ Groq error: {error}"
        )

        return None


# =========================================================
# TELEGRAM SEND
# =========================================================

async def send_news(news):

    print(
        "📝 Preparing Telegram post..."
    )

    print(
        f"   Title: {news['title']}"
    )

    text = create_amharic_news(
        news
    )

    if not text:

        print(
            "❌ No text generated."
        )

        return False

    try:

        await bot.send_message(
            chat_id=CHANNEL_ID,
            text=text,
            disable_web_page_preview=False
        )

        # Telegram ላይ በትክክል ከተላከ
        # ብቻ history ውስጥ እንጨምራለን
        remember_news(
            title=news["title"],
            link=news["link"],
            source=news["source"],
            published=news["published"]
        )

        print(
            "✅ SUCCESS: "
            "News sent to Telegram."
        )

        return True

    except Exception as error:

        print(
            f"❌ TELEGRAM SEND ERROR: "
            f"{error}"
        )

        return False


# =========================================================
# TELEGRAM CONNECTION TEST
# =========================================================

async def test_telegram():

    try:

        me = await bot.get_me()

        print(
            "✅ Telegram bot connected:"
        )

        print(
            f"   @{me.username}"
        )

        print(
            f"📢 Target channel: "
            f"{CHANNEL_ID}"
        )

        return True

    except Exception as error:

        print(
            f"❌ Telegram connection failed: "
            f"{error}"
        )

        return False


# =========================================================
# MAIN NEWS LOOP
# =========================================================

async def news_loop():

    print(
        "======================================"
    )

    print(
        "🔴 LIVERPOOL AMHARIC NEWS BOT"
    )

    print(
        "======================================"
    )

    # Telegram connection
    telegram_ok = await test_telegram()

    if not telegram_ok:

        print(
            "❌ Bot cannot connect to Telegram."
        )

        return

    while True:

        try:

            news_list = fetch_news()

            if not news_list:

                print(
                    "⏭️ No new eligible "
                    "Liverpool news."
                )

            else:

                posts_sent = 0

                for news in news_list:

                    if posts_sent >= MAX_POSTS_PER_CHECK:
                        break

                    success = await send_news(
                        news
                    )

                    if success:

                        posts_sent += 1

                        # አንድ ብቻ እንዲላክ
                        break

                    # Telegram/Groq error ከሆነ
                    # ሌላ ዜና ሞክር
                    await asyncio.sleep(2)

        except Exception as error:

            print(
                f"❌ MAIN LOOP ERROR: "
                f"{error}"
            )

        print(
            f"⏳ Next check in "
            f"{CHECK_INTERVAL} seconds..."
        )

        await asyncio.sleep(
            CHECK_INTERVAL
        )


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    try:

        asyncio.run(
            news_loop()
        )

    except KeyboardInterrupt:

        print(
            "🛑 Bot stopped."
        )

    except Exception as error:

        print(
            f"❌ FATAL ERROR: {error}"
        )
