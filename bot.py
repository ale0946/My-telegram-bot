import os
import requests
import feedparser
import asyncio
from telegram import Bot

TOKEN = os.getenv("BOT_TOKEN")
API_KEY = os.getenv("FOOTBALL_API_KEY")

CHANNEL_ID = "@yegnaLiverpool"

FILE = "last_news.txt"
SOURCES_FILE = "sources.txt"


def get_sources():
    with open(SOURCES_FILE, "r") as f:
        return [line.strip() for line in f if line.strip()]


def get_live_matches():
    url = "https://v3.football.api-sports.io/fixtures?live=all"

    headers = {
        "x-apisports-key": API_KEY
    }

    response = requests.get(url, headers=headers)
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

    live = get_live_matches()

    if live:
        live_text = "\n".join(live)
    else:
        live_text = "የአሁን የሊቨርፑል ቀጥታ ጨዋታ የለም"


    text = f"""
🚨🔴 የሊቨርፑል ዜና

📝 {latest.title}

⚽ Live:
{live_text}

📰 ምንጭ: Liverpool News

📢 @yegnaLiverpool
"""


    bot = Bot(token=TOKEN)

    await bot.send_message(
        chat_id=CHANNEL_ID,
        text=text
    )


    with open(FILE, "w") as f:
        f.write(latest.link)

    print("News sent successfully")


if __name__ == "__main__":
    asyncio.run(send_news())
