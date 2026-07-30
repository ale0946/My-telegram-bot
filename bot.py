import os
import feedparser
from telegram import Bot

TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = "@yegnaLiverpool"

RSS_URL = "https://www.thisisanfield.com/feed/"
FILE = "last_news.txt"


async def send_news():
    news = feedparser.parse(RSS_URL)

    if not news.entries:
        return

    item = news.entries[0]

    title = item.title
    link = item.link

    old_news = ""

    if os.path.exists(FILE):
        with open(FILE, "r") as f:
            old_news = f.read().strip()

    if link == old_news:
        print("No new news")
        return

    text = f"""
🔴 የሊቨርፑል ዜና

📰 {title}

🔗 {link}

📢 @yegnaLiverpool
"""

    bot = Bot(token=TOKEN)

    await bot.send_message(
        chat_id=CHANNEL_ID,
        text=text
    )

    with open(FILE, "w") as f:
        f.write(link)

    print("News sent successfully")


if __name__ == "__main__":
    import asyncio
    asyncio.run(send_news())
