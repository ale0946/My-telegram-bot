
import os
import asyncio
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

client = Groq(api_key=GROQ_KEY)


# =========================
# GET NEWS SOURCES
# =========================

def get_sources():

    try:

        with open(SOURCES_FILE, "r", encoding="utf-8") as file:

            return [
                line.strip()
                for line in file
                if line.strip()
            ]

    except Exception as e:

        print("Sources error:", e)

        return []


# =========================
# CHECK LIVERPOOL NEWS
# =========================

def is_liverpool_news(text):

    keywords = [
        "Liverpool",
        "LFC",
        "Anfield",
        "Salah",
        "Mohamed Salah",
        "Van Dijk",
        "Virgil van Dijk",
        "Iraola",
        "Arne Slot",
        "Slot",
        "Mac Allister",
        "Trent",
        "Alexander-Arnold",
        "Szoboszlai",
        "Gakpo",
        "Nunez",
        "Darwin Nunez",
        "Luis Diaz",
        "Konate",
        "Alisson",
        "Robertson",
        "Bradley",
        "Gravenberch",
        "Wirtz",
        "Ekitike",
        "Leoni"
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

            with open(NEWS_FILE, "r", encoding="utf-8") as file:

                return file.read().strip()

        except Exception as e:

            print("Read last news error:", e)

    return ""


def save_last_news(link):

    try:

        with open(NEWS_FILE, "w", encoding="utf-8") as file:

            file.write(link)

    except Exception as e:

        print("Save news error:", e)


# =========================
# TRANSLATE NEWS WITH GROQ
# =========================

def translate_news(news_text):

    try:

        result = client.chat.completions.create(

            model="llama-3.3-70b-versatile",

            messages=[

                {
                    "role": "system",

                    "content": """
አንተ የLiverpool የዜና አርታኢ ነህ።

የተሰጠህን የLiverpool ዜና ወደ ተፈጥሯዊ፣
ግልጽ እና ለTelegram የሚመች አማርኛ ቀይር።

ደንቦች:

1. ከዜናው ውጭ ምንም አዲስ መረጃ አትጨምር።

2. የተጫዋቾችን፣ የአሰልጣኞችን እና የክለቦችን
   ስም በትክክል ጠብቅ።

3. የዜናውን ትርጉም አትቀይር።

4. አጭር እና ማራኪ የTelegram ዜና ቅርጽ ተጠቀም።

5. ከርዕሱ በፊት 🔴 ወይም 🚨 ተጠቀም።

6. የሌለ ጥቅስ፣ ዋጋ፣ ቀን፣ ምንጭ ወይም
   የተጫዋች መረጃ አትፍጠር።

7. የተሰጠው ዜና Liverpool ጋር የማይያያዝ
   ከሆነ አትተርጉም።
"""
                },

                {
                    "role": "user",
                    "content": news_text
                }

            ],

            temperature=0.1,
            max_tokens=1000
        )

        return result.choices[0].message.content.strip()

    except Exception as e:

        print("Groq error:", e)

        return news_text


# =========================
# FOOTBALL LIVE MATCHES
# =========================

def get_live_matches():

    if not API_KEY:

        print("FOOTBALL_API_KEY is missing")

        return []

    url = "https://v3.football.api-sports.io/fixtures?live=all"

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

        for game in data.get("response", []):

            home = game["teams"]["home"]["name"]

            away = game["teams"]["away"]["name"]

            if "Liverpool" in home or "Liverpool" in away:

                home_score = game["goals"]["home"]

                away_score = game["goals"]["away"]

                elapsed = game["fixture"]["status"].get("elapsed")

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

                matches.append(match_text)

        return matches

    except Exception as e:

        print("Football API error:", e)

        return []


# =========================
# GET NEWS IMAGE
# =========================

def get_image(item):

    try:

        if hasattr(item, "media_content"):

            if item.media_content:

                url = item.media_content[0].get("url")

                if url:

                    return url


        if hasattr(item, "media_thumbnail"):

            if item.media_thumbnail:

                url = item.media_thumbnail[0].get("url")

                if url:

                    return url


        if hasattr(item, "enclosures"):

            for enclosure in item.enclosures:

                if enclosure.get("type", "").startswith("image"):

                    return enclosure.get("href")

    except Exception as e:

        print("Image error:", e)

    return None


# =========================
# SEND NEWS
# =========================

async def send_news():

    sources = get_sources()

    print("Sources:", sources)

    if not sources:

        print("No sources found")

        return


    old_news = get_last_news()

    latest = None


    for source in sources:

        try:

            feed = feedparser.parse(source)

        except Exception as e:

            print("RSS error:", e)

            continue


        for item in feed.entries:

            link = getattr(item, "link", "")

            if not link:

                continue


            if link == old_news:

                continue


            title = getattr(item, "title", "")

            summary = getattr(item, "summary", "")

            content = f"{title} {summary}"


            if is_liverpool_news(content):

                latest = item

                break


        if latest:

            break


    if not latest:

        print("No new Liverpool news")

        return


    # =========================
    # NEWS TEXT
    # =========================

    news_text = getattr(latest, "title", "")

    summary = getattr(latest, "summary", "")

    if summary:

        news_text += "\n\n" + summary


    translated = translate_news(news_text)


    # =========================
    # LIVE MATCH
    # =========================

    live = get_live_matches()


    if live:

        live_text = (
            "\n\n"
            "🔴 LIVE\n"
            + "\n".join(live)
        )

    else:

        live_text = ""


    # =========================
    # FINAL MESSAGE
    # =========================

    message = translated + live_text


    # =========================
    # TELEGRAM
    # =========================

    bot = Bot(token=TOKEN)


    try:

        image = get_image(latest)


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
                    f"Sent successfully to {channel}"
                )


            except Exception as e:

                print(
                    f"Telegram error for {channel}:",
                    e
                )


        save_last_news(
            getattr(latest, "link", "")
        )


        print("News sent successfully")


    except Exception as e:

        print("Telegram error:", e)


# =========================
# MAIN LOOP
# =========================

async def main():

    print("Liverpool News Bot started 🚀")


    while True:

        try:

            await send_news()

        except Exception as e:

            print("Main loop error:", e)


        print("Waiting 5 minutes...")

        await asyncio.sleep(300)


# =========================
# START BOT
# =========================

if __name__ == "__main__":

    asyncio.run(main())
```
