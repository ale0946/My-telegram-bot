import os
import feedparser
from telegram.ext import Application

TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = "@yegnaLiverpool"

RSS_URL = "https://www.thisisanfield.com/feed/"
FILE = "last_news.txt"


async def send_news(context):
    news = feedparser.parse(RSS_URL)

    if not news.entries:
        return

    item = news.entries[0]

    title = item.title
    link = item.link

    old_news = ""

    if os.path.exists(FILE):
        with open(FILE, "r") as f:
            old_news = f.read()

    # አስቀድሞ ከተላከ አይድገም
    if link == old_news:
        return

    text = f"""
🔴 LIVERPOOL NEWS

📰 {title}

🔗 {link}

@yegnaLiverpool
"""

    await context.bot.send_message(
        chat_id=CHANNEL_ID,
        text=text
    )

    # አዲሱን ዜና አስቀምጥ
    with open(FILE, "w") as f:
        f.write(link)


app = Application.builder().token(TOKEN).build()

app.job_queue.run_repeating(
    send_news,
    interval=300,
    first=5
)

app.run_polling()
