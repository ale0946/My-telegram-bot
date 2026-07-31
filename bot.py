import os
import asyncio
import requests
import feedparser

from telegram import Bot
from groq import Groq


TOKEN = os.getenv("BOT_TOKEN")
GROQ_KEY = os.getenv("GROQ_API_KEY")
API_KEY = os.getenv("FOOTBALL_API_KEY")


CHANNEL_ID = "@yegnaLiverpool"

NEWS_FILE = "last_news.txt"
SOURCES_FILE = "sources.txt"


client = Groq(api_key=GROQ_KEY)



def get_sources():

    try:
        with open(SOURCES_FILE, "r") as file:
            return [
                line.strip()
                for line in file
                if line.strip()
            ]

    except Exception as e:
        print("Sources error:", e)
        return []



def is_liverpool_news(text):

    keywords = [
        "Liverpool",
        "LFC",
        "Anfield",
        "Salah",
        "Van Dijk",
        "Iraola",
        "Slot",
        "Mac Allister",
        "Trent"
    ]


    text = text.lower()


    for word in keywords:

        if word.lower() in text:
            return True


    return False



def get_last_news():

    if os.path.exists(NEWS_FILE):

        with open(NEWS_FILE, "r") as file:
            return file.read().strip()

    return ""



def save_last_news(link):

    with open(NEWS_FILE, "w") as file:
        file.write(link)
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
- እንደ ስፖርት ጋዜጠኛ ጻፍ
- ቃል በቃል አትተርጉም
- ከዜናው ውጪ መረጃ አትጨምር
- የተጫዋችና የክለብ ስም አትቀይር
- ግልጽና ሙያዊ አድርግ
- የተሳሳቱ የትርጉም ቃላትን በትክክለኛ የእግር ኳስ ቃላት አስተካክል
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

        print("Groq error:", e)

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

        print("Football API error:", e)

        return []





def get_image(item):

    try:

        if hasattr(item, "media_content"):

            if item.media_content:

                return item.media_content[0]["url"]



        if hasattr(item, "media_thumbnail"):

            if item.media_thumbnail:

                return item.media_thumbnail[0]["url"]


    except Exception as e:

        print("Image error:", e)


    return None
async def send_news():

    old_news = get_last_news()

    latest = None


    for source in get_sources():

        feed = feedparser.parse(source)


        for item in feed.entries:

            if item.link == old_news:
                continue


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



    message = f"""
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

                caption=message

            )


        else:

            await bot.send_message(

                chat_id=CHANNEL_ID,

                text=message

            )



        save_last_news(latest.link)


        print("News sent successfully")



    except Exception as e:

        print("Telegram error:", e)





async def main():


    while True:


        await send_news()


        print("Waiting 5 minutes...")


        await asyncio.sleep(300)





if __name__ == "__main__":

    asyncio.run(main())
