```python
import os
import asyncio
import re
import html
import requests
import feedparser

from telegram import Bot
from groq import Groq


# =========================
# ENVIRONMENT VARIABLES
# =========================

TOKEN = os.getenv("BOT_TOKEN")
GROQ_KEY = os.getenv("GROQ_API_KEY")
API_KEY = os.getenv("FOOTBALL_API_KEY")


# =========================
# TELEGRAM CHANNELS
# =========================

CHANNEL_IDS = [
    "@yegnaLiverpool",
    "@yegnaLiverpoolET"
]


# =========================
# FILES
# =========================

NEWS_FILE = "last_news.txt"
SOURCES_FILE = "sources.txt"


# =========================
# GROQ CLIENT
# =========================

client = Groq(api_key=GROQ_KEY) if GROQ_KEY else None


# =========================
# GET NEWS SOURCES
# =========================

def get_sources():

    try:

        with open(
            SOURCES_FILE,
            "r",
            encoding="utf-8"
        ) as file:

            return [
                line.strip()
                for line in file
                if line.strip()
            ]

    except Exception as e:

        print("Sources error:", e)

        return []


# =========================
# CLEAN HTML
# =========================

def clean_html(text):

    if not text:
        return ""

    try:

        text = html.unescape(text)

        text = re.sub(
            r"<script.*?</script>",
            "",
            text,
            flags=re.DOTALL | re.IGNORECASE
        )

        text = re.sub(
            r"<style.*?</style>",
            "",
            text,
            flags=re.DOTALL | re.IGNORECASE
        )

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

    except Exception as e:

        print("HTML clean error:", e)

        return str(text).strip()


# =========================
# CHECK LIVERPOOL NEWS
# =========================

def is_liverpool_news(text):

    keywords = [
        "Liverpool",
        "Liverpool FC",
        "LFC",
        "Anfield",
        "Salah",
        "Mohamed Salah",
        "Mohamed",
        "Van Dijk",
        "Virgil van Dijk",
        "Iraola",
        "Andoni Iraola",
        "Arne Slot",
        "Slot",
        "Mac Allister",
        "Trent",
        "Alexander-Arnold",
        "Szoboszlai",
        "Gakpo",
        "Nunez",
        "Núñez",
        "Darwin Nunez",
        "Darwin Núñez",
        "Luis Diaz",
        "Luis Díaz",
        "Konate",
        "Konaté",
        "Alisson",
        "Robertson",
        "Andy Robertson",
        "Bradley",
        "Conor Bradley",
        "Gravenberch",
        "Wirtz",
        "Ekitike",
        "Leoni",
        "Giovanni Leoni"
    ]

    text = text.lower()

    for word in keywords:

        if word.lower() in text:

            return True

    return False


# =========================
# LAST NEWS
# =========================

def get_last_news():

    if os.path.exists(NEWS_FILE):

        try:

            with open(
                NEWS_FILE,
                "r",
                encoding="utf-8"
            ) as file:

                return file.read().strip()

        except Exception as e:

            print("Read last news error:", e)

    return ""


def save_last_news(link):

    try:

        with open(
            NEWS_FILE,
            "w",
            encoding="utf-8"
        ) as file:

            file.write(link)

    except Exception as e:

        print("Save news error:", e)


# =========================
# TRANSLATE NEWS
# =========================

def translate_news(news_text):

    if not GROQ_KEY or client is None:

        print("GROQ_API_KEY is missing")

        return None

    if not news_text.strip():

        print("News text is empty")

        return None

    try:

        result = client.chat.completions.create(

            model="llama-3.3-70b-versatile",

            messages=[

                {
                    "role": "system",

                    "content": """
አንተ የLiverpool FC የዜና አርታኢ ነህ።

ከታች የተሰጠህን የእንግሊዝኛ
Liverpool ዜና ወደ ተፈጥሯዊ፣
ግልጽ እና ትክክለኛ አማርኛ ተርጉም።

አስፈላጊ ደንቦች:

1. ርዕሱን ብቻ አትተርጉም።
   ርዕሱን እና የተሰጠውን የዜና ይዘት
   በሙሉ ተርጉም።

2. የዜናውን ትርጉም አትቀይር።

3. ከተሰጠው ዜና ውጭ ምንም
   አዲስ መረጃ አትጨምር።

4. የተጫዋቾችን ስም አትቀይር።
   ለምሳሌ:
   Mohamed Salah
   Virgil van Dijk
   Florian Wirtz
   Giovanni Leoni

5. የክለቦችን እና የውድድሮችን
   ስም በትክክል ጠብቅ።

6. ቁጥሮች፣ ዋጋዎች፣ ቀናት፣
   ውጤቶች እና ስታቲስቲክሶችን
   አትቀይር።

7. Liverpool → ሊቨርፑል
   Premier League → ፕሪሚየር ሊግ
   Champions League → ቻምፒየንስ ሊግ
   transfer → ዝውውር
   manager → አሰልጣኝ
   friendly → የዝግጅት ጨዋታ

8. ዜናውን ለTelegram ቻናል
   የሚመች አማርኛ አድርግ።

9. ርዕሱ ከሆነ በፊት 🔴 ወይም 🚨
   መጠቀም ትችላለህ።

10. የሌለ ጥቅስ፣ የሌለ መረጃ፣
    የሌለ ዋጋ ወይም የሌለ ዝርዝር
    አትፍጠር።

11. ከዜናው ውጭ ማብራሪያ
    ወይም የራስህን አስተያየት
    አትጨምር።

12. የመጨረሻ ምላሽህ
    የተተረጎመው የአማርኛ ዜና ብቻ ይሁን።

13. እንግሊዝኛውን ዜና
    እንዳለ አትድገም።
"""
                },

                {
                    "role": "user",
                    "content": news_text
                }

            ],

            temperature=0.1,

            max_tokens=2500
        )

        translated = (
            result.choices[0]
            .message.content
            .strip()
        )

        if not translated:

            print("Groq returned empty translation")

            return None

        return translated

    except Exception as e:

        print(
            "Groq translation error:",
            e
        )

        return None


# =========================
# FOOTBALL LIVE MATCHES
# =========================

def get_live_matches():

    if not API_KEY:

        print(
            "FOOTBALL_API_KEY is missing"
        )

        return []

    url = (
        "https://v3.football.api-sports.io/"
        "fixtures?live=all"
    )

    headers = {
        "x-apisports-key": API_KEY
    }

    try:

        response = requests.get(
            url,
            headers=headers,
            timeout=10
        )

        response.raise_for_status()

        data = response.json()

        matches = []

        for game in data.get(
            "response",
            []
        ):

            home = game["teams"]["home"]["name"]

            away = game["teams"]["away"]["name"]

            if (
                "Liverpool" in home
                or "Liverpool" in away
            ):

                home_score = (
                    game["goals"]["home"]
                )

                away_score = (
                    game["goals"]["away"]
                )

                elapsed = (
                    game["fixture"]["status"]
                    .get("elapsed")
                )

                if elapsed:

                    match_text = (
                        f"⚽ {home} "
                        f"{home_score}-{away_score} "
                        f"{away} "
                        f"({elapsed}')"
                    )

                else:

                    match_text = (
                        f"⚽ {home} "
                        f"{home_score}-{away_score} "
                        f"{away}"
                    )

                matches.append(
                    match_text
                )

        return matches

    except Exception as e:

        print(
            "Football API error:",
            e
        )

        return []


# =========================
# GET NEWS IMAGE
# =========================

def get_image(item):

    try:

        if hasattr(
            item,
            "media_content"
        ):

            if item.media_content:

                for media in item.media_content:

                    url = media.get("url")

                    if url:

                        return url


        if hasattr(
            item,
            "media_thumbnail"
        ):

            if item.media_thumbnail:

                for media in item.media_thumbnail:

                    url = media.get("url")

                    if url:

                        return url


        if hasattr(
            item,
            "enclosures"
        ):

            for enclosure in item.enclosures:

                image_type = (
                    enclosure
                    .get("type", "")
                    .lower()
                )

                if image_type.startswith(
                    "image"
                ):

                    url = enclosure.get(
                        "href"
                    )

                    if url:

                        return url

    except Exception as e:

        print(
            "Image error:",
            e
        )

    return None


# =========================
# SEND NEWS
# =========================

async def send_news():

    sources = get_sources()

    print(
        "Sources:",
        sources
    )

    if not sources:

        print(
            "No sources found"
        )

        return

    old_news = get_last_news()

    latest = None

    # =========================
    # SEARCH RSS SOURCES
    # =========================

    for source in sources:

        try:

            feed = feedparser.parse(
                source
            )

        except Exception as e:

            print(
                "RSS error:",
                e
            )

            continue

        for item in feed.entries:

            link = getattr(
                item,
                "link",
                ""
            )

            if not link:

                continue

            if link == old_news:

                continue

            title = clean_html(
                getattr(
                    item,
                    "title",
                    ""
                )
            )

            summary = clean_html(
                getattr(
                    item,
                    "summary",
                    ""
                )
            )

            content = (
                f"{title} {summary}"
            )

            if is_liverpool_news(
                content
            ):

                latest = item

                break

        if latest:

            break

    # =========================
    # NO NEW NEWS
    # =========================

    if not latest:

        print(
            "No new Liverpool news"
        )

        return

    # =========================
    # ORIGINAL NEWS
    # =========================

    title = clean_html(
        getattr(
            latest,
            "title",
            ""
        )
    )

    summary = clean_html(
        getattr(
            latest,
            "summary",
            ""
        )
    )

    link = getattr(
        latest,
        "link",
        ""
    )

    news_text = title

    if summary:

        news_text += (
            "\n\n" + summary
        )

    print(
        "Found Liverpool news:"
    )

    print(title)

    # =========================
    # TRANSLATE
    # =========================

    print(
        "Translating news..."
    )

    translated = translate_news(
        news_text
    )

    # =========================
    # NEVER SEND ENGLISH
    # =========================

    if not translated:

        print(
            "Translation failed."
        )

        print(
            "English news will NOT "
            "be sent to Telegram."
        )

        return

    # =========================
    # LIVE MATCHES
    # =========================

    live = get_live_matches()

    live_text = ""

    if live:

        live_text = (
            "\n\n"
            "🔴 LIVE\n"
            + "\n".join(live)
        )

    # =========================
    # SOURCE LINK
    # =========================

    source_text = ""

    if link:

        source_text = (
            "\n\n"
            "🔗 ምንጭ: "
            + link
        )

    # =========================
    # FINAL MESSAGE
    # =========================

    message = (
        translated
        + live_text
        + source_text
    )

    # =========================
    # TELEGRAM TOKEN CHECK
    # =========================

    if not TOKEN:

        print(
            "BOT_TOKEN is missing"
        )

        return

    bot = Bot(
        token=TOKEN
    )

    # =========================
    # SEND TO CHANNELS
    # =========================

    try:

        image = get_image(
            latest
        )

        for channel in CHANNEL_IDS:

            try:

                if image:

                    await bot.send_photo(

                        chat_id=channel,

                        photo=image,

                        caption=message

                    )

                else:

                    await bot.send_message(

                        chat_id=channel,

                        text=message

                    )

                print(
                    f"Sent successfully "
                    f"to {channel}"
                )

            except Exception as e:

                print(
                    f"Telegram error for "
                    f"{channel}: {e}"
                )

        # =========================
        # SAVE ONLY AFTER SEND
        # =========================

        save_last_news(
            link
        )

        print(
            "News sent successfully"
        )

    except Exception as e:

        print(
            "Telegram error:",
            e
        )


# =========================
# MAIN LOOP
# =========================

async def main():

    print(
        "Liverpool News Bot "
        "started 🚀"
    )

    while True:

        try:

            await send_news()

        except Exception as e:

            print(
                "Main loop error:",
                e
            )

        print(
            "Waiting 5 minutes..."
        )

        await asyncio.sleep(
            300
        )


# =========================
# START
# =========================

if __name__ == "__main__":

    asyncio.run(main())
```
