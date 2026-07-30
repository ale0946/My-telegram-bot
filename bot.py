import requests

API_KEY = os.getenv("FOOTBALL_API_KEY")
import os
import feedparser
from telegram import Bot

TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = "@yegnaLiverpool"

FILE = "last_news.txt"
SOURCES_FILE = "sources.txt"


def get_sources():
    with open(SOURCES_FILE, "r") as f:
        return [line.strip() for line in f if line.strip()]


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

    title = latest.title

    text = f"""
🚨🔴 የሊቨርፑል ዜና

📝 {title}

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
    import asyncio
    asyncio.run(send_news())
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
            score_home = game["goals"]["home"]
            score_away = game["goals"]["away"]

            matches.append(
                f"⚽ {home} {score_home}-{score_away} {away}"
            )

    return matches
