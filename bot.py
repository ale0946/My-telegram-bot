import os
import requests
import feedparser
import asyncio

from telegram import Bot
from groq import Groq


TOKEN = os.getenv("BOT_TOKEN")
GROQ_KEY = os.environ["GROQ_API_KEY"]
API_KEY = os.environ["FOOTBALL_API_KEY"]

CHANNEL_ID = "@yegnaLiverpool"

FILE = "last_news.txt"
SOURCES_FILE = "sources.txt"


client = Groq(api_key=GROQ_KEY)


def get_sources():
    try:
        with open(SOURCES_FILE, "r") as f:
            return [line.strip() for line in f if line.strip()]
    except Exception as e:
        print("Sources Error:", e)
        return []


def is_liverpool_news(text):
    keywords = [
        "Liverpool",
        "LFC",
        "Anfield",
        "Slot",
        "Iraola",
        "Van Dijk",
        "Salah",
        "Trent",
        "Mac Allister"
    ]

    return any(word.lower() in text.lower() for word in keywords)


def translate_news(news_text):
    try:
        result = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": """
አንተ የሊቨርፑል የእግር ኳስ ዜና አርታኢ ነህ።

ይህን ዜና ወደ ተፈጥሯዊ አማርኛ ቀይር።

ህጎች:
- እንደ ስፖርት ጋዜጠኛ ጻፍ
- ቃል በቃል አትተርጉም
- ከዜናው ውጪ መረጃ አትጨምር
- የተጫዋች እና ክለብ ስም አትቀይር
- አጭርና ግልጽ አድርግ
- የተዛቡ ቃላት ካሉ በትክክለኛ የእግር ኳስ ቃላት አስተካክል
- የአሰልጣኞች እና የተጫዋቾች ስም በትክክል ጻፍ
- የዜናውን ትርጉም አትቀይር
- ከእንግሊዝኛ ርዕስ የሚመጡ የተሳሳቱ ቃላትን አስተካክል
- እንደ የሊቨርፑል ይፋዊ ዜና ገጽ ቅርጽ አቅርብ
- ዜናውን በቂ ዝርዝር አቅርብ
- 2 እስከ 4 አንቀጽ ያለ የስፖርት ዘገባ ጻፍ
- ዋና ዋና ነጥቦችን አትተው
- የርዕሱ ቃላት ተበላሽተው ከመጡ በትክክለኛ የእግር ኳስ ቃላት አስተካክል
- ርዕስ ላይ የማይገባ ቃል አትጨምር
- የአሰልጣኝ እና የተጫዋች ስም ትክክል አድርግ
"""
                },
                {
                    "role": "user",
                    "content": news_text
                }
            ],
            temperature=0.2,
            max_tokens=1200
        )

        return result.choices[0].message.content.strip()

    except Exception as e:
        print("Groq Error:", e)
        return news_text


def get_live_matches():

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

        data = response.json()

        matches = []

        for game in data.get("response", []):

            home = game["teams"]["home"]["name"]
            away = game["teams"]["away"]["name"]

            if "Liverpool" in home or "Liverpool" in away:

                home_score = game["goals"]["home"]
                away_score = game["goals"]["away"]

                matches.append(
                    f"⚽ {home} {home_score}-{away_score} {away}"
                )

        return matches

    except Exception as e:
        print("Football API Error:", e)
        return [] 
def get_image(item):
    try:
        if hasattr(item, "media_content"):

            media = item.media_content

            if media:
                return media[0]["url"]

        if hasattr(item, "media_thumbnail"):

            thumb = item.media_thumbnail

            if thumb:
                return thumb[0]["url"]

    except Exception as e:
        print("Image Error:", e)

    return None



async def send_news():

    old_news = ""

    if os.path.exists(FILE):

        with open(FILE, "r") as f:
            old_news = f.read().strip()



    latest = None


    for source in get_sources():

        news = feedparser.parse(source)


        if news.entries:

            for item in news.entries:

                if item.link != old_news:

                    content = item.title

                    if hasattr(item, "summary"):
                        content += " " + item.summary


                    if is_liverpool_news(content):

                        latest = item
                        break


        if latest:
            break



    if not latest:

        print("No new Liverpool news")

        return



    news_text = latest.title


    if hasattr(latest, "summary"):

        news_text += "\n\n" + latest.summary



    translated = translate_news(news_text)



    live = get_live_matches()


    live_text = ""

    if live:

        live_text = "\n\n" + "\n".join(live)



    text = f"""
🔴 Liverpool News

📝 {translated}
{live_text}

📰 ምንጭ: Liverpool News

📢 @yegnaLiverpool
"""



    bot = Bot(token=TOKEN)



    try:

        image = get_image(latest)



        if image:

            await bot.send_photo(
                chat_id=CHANNEL_ID,
                photo=image,
                caption=text
            )

        else:

            await bot.send_message(
                chat_id=CHANNEL_ID,
                text=text
            )



        with open(FILE, "w") as f:

            f.write(latest.link)



        print("News sent successfully")



    except Exception as e:

        print("Telegram Error:", e)





async def main():

    while True:

        await send_news()

        print("Waiting 5 minutes...")

        await asyncio.sleep(300)



if __name__ == "__main__":

    asyncio.run(main())
