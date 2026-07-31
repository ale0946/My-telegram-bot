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
    with open(SOURCES_FILE, "r") as f:
        return [line.strip() for line in f if line.strip()]



def translate_news(news_text):
    try:
        result = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": """
አንተ የሊቨርፑል እግር ኳስ ዜና አርታኢ ነህ።

የተሰጠውን ዜና ወደ ተፈጥሯዊ አማርኛ ቀይር።

ህጎች:
- ቃል በቃል አትተርጉም
- እንደ የስፖርት ጋዜጠኛ ጻፍ
- ከዜናው ውጪ መረጃ አትጨምር
- የተጫዋቾች እና ክለቦች ስም አትቀይር
- አጭርና ግልጽ አድርግ
"""
                },
                {
                    "role": "user",
                    "content": news_text
                }
            ],
            temperature=0.2,
            max_tokens=300
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
            return item.media_content[0]["url"]

        if hasattr(item, "media_thumbnail"):
            return item.media_thumbnail[0]["url"]

    except Exception:
        pass

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

            item = news.entries[0]

            if item.link != old_news:
                latest = item
                break


    if not latest:
        print("No new news")
        return



    news_text = latest.title

    if hasattr(latest, "summary"):
        news_text += "\n\n" + latest.summary


    translated = translate_news(news_text)



    live = get_live_matches()


    if live:
        live_text = "\n".join(live)

    else:
        live_text = "⚽ አሁን የሊቨርፑል ቀጥታ ጨዋታ የለም"



    text = f"""
📝 {translated}

{live_text}

📰 ምንጭ: Liverpool News

📢 @yegnaLiverpool
"""



    bot = Bot(token=TOKEN)


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



if __name__ == "__main__":

    asyncio.run(send_news())
