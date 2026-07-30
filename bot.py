import os
import feedparser
from telegram.ext import Application

TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = "@yegnaLiverpool"

RSS_URL = "https://www.thisisanfield.com/feed/"

async def send_news(context):
    news = feedparser.parse(RSS_URL)

    if news.entries:
        item = news.entries[0]

        text = f"""
🔴 LIVERPOOL NEWS

📰 {item.title}

🔗 {item.link}

@yegnaLiverpool
"""

        await context.bot.send_message(
            chat_id=CHANNEL_ID,
            text=text
        )

app = Application.builder().token(TOKEN).build()

# 5 ደቂቃ ሁሉ አዲስ ዜና ይፈትሻል
app.job_queue.run_repeating(send_news, interval=300, first=5)

app.run_polling()
