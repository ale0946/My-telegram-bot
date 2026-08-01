import os
import json
import asyncio
from datetime import datetime
import requests
import feedparser
from groq import Groq
from telegram import Bot
from telegram.ext import ApplicationBuilder

TOKEN = os.getenv("BOT_TOKEN")
GROQ_KEY = os.getenv("GROQ_API_KEY")
CHANNEL = os.getenv("CHANNEL_ID", "@yegnaLiverpool")

bot = Bot(TOKEN)
groq = Groq(api_key=GROQ_KEY)
SEEN_FILE = "seen.json"

NEWS_FEEDS = [
    'https://news.google.com/rss/search?q="Liverpool"+site:liverpoolfc.com&hl=en-GB&gl=GB&ceid=GB:en',
    'https://news.google.com/rss/search?q="Liverpool"+"Paul+Joyce"&hl=en-GB&gl=GB&ceid=GB:en',
    'https://news.google.com/rss/search?q="Liverpool"+"David+Ornstein"&hl=en-GB&gl=GB&ceid=GB:en',
    'https://news.google.com/rss/search?q="Liverpool"+"Fabrizio+Romano"&hl=en-GB&gl=GB&ceid=GB:en',
    'https://news.google.com/rss/search?q="Liverpool"+"James+Pearce"&hl=en-GB&gl=GB&ceid=GB:en'
]

def load_seen():
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except:
        return set()

def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(list(seen)[-500:], f, ensure_ascii=False)

def translate_news(title, summary):
    prompt = f"""
አንተ የLiverpool ዜና አማርኛ አዘጋጅ ነህ።
የተሰጠውን ዜና ብቻ ተጠቅመህ አጭርና ጥራት ያለው የአማርኛ Telegram ዜና ጻፍ።

ደንቦች:
- English headline አትጻፍ።
- English paragraph አትጻፍ።
- የሌለ መረጃ አትጨምር።
- ምሳሌ ላይ የተጠቀሰ ተጫዋች የዜናው አካል ካልሆነ አታስገባ።
- ዜናውን በተፈጥሯዊ አማርኛ ጻፍ።
- 2-4 አጭር አንቀጾች ብቻ።

ርዕስ:
{title}

ዝርዝር:
{summary}
"""
    r = groq.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2
    )
    return r.choices[0].message.content.strip()

async def send_news():
    seen = load_seen()
    for url in NEWS_FEEDS:
        feed = await asyncio.to_thread(feedparser.parse, url)
        for item in feed.entries[:5]:
            link = item.get("link", "")
            title = item.get("title", "")
            summary = item.get("summary", "")
            if not link or link in seen:
                continue
            if "Liverpool" not in title and "Liverpool" not in summary:
                continue

            try:
                text = await asyncio.to_thread(
                    translate_news, title, summary
                )
                await bot.send_message(
                    chat_id=CHANNEL,
                    text=f"🔴 LIVERPOOL NEWS\n\n{text}"
                )
                seen.add(link)
                save_seen(seen)
            except Exception as e:
                print("News error:", e)

async def get_match():
    date = datetime.utcnow().strftime("%Y%m%d")
    url = (
        "https://site.api.espn.com/apis/site/v2/sports/"
        f"soccer/eng.1/scoreboard?dates={date}"
    )
    try:
        data = await asyncio.to_thread(
            lambda: requests.get(url, timeout=15).json()
        )
        for event in data.get("events", []):
            comps = event.get("competitions", [{}])[0].get("competitors", [])
            if any("Liverpool" in c.get("team", {}).get("displayName", "")
                   for c in comps):
                return event
    except Exception as e:
        print("Match error:", e)
    return None

def match_text(event):
    comp = event["competitions"][0]
    teams = comp["competitors"]
    home = next((x for x in teams if x.get("homeAway") == "home"), {})
    away = next((x for x in teams if x.get("homeAway") == "away"), {})
    status = event.get("status", {}).get("type", {})
    return (
        f"🔴 LIVERPOOL LIVE\n\n"
        f"⚽ {home.get('team', {}).get('displayName', '')} "
        f"{home.get('score', '0')} - "
        f"{away.get('score', '0')} "
        f"{away.get('team', {}).get('displayName', '')}\n\n"
        f"📌 {status.get('detail', '')}"
    )

async def send_lineup(event):
    eid = event.get("id")
    url = (
        "https://site.api.espn.com/apis/site/v2/sports/"
        f"soccer/eng.1/summary?event={eid}"
    )
    try:
        data = await asyncio.to_thread(
            lambda: requests.get(url, timeout=15).json()
        )
        rosters = data.get("rosters", [])
        for team in rosters:
            name = team.get("team", {}).get("displayName", "")
            if "Liverpool" not in name:
                continue
            players = []
            for p in team.get("roster", []):
                athlete = p.get("athlete", {}).get("displayName")
                starter = p.get("starter")
                if athlete and starter:
                    players.append(athlete)
            if players:
                await bot.send_message(
                    CHANNEL,
                    "📋 LIVERPOOL LINEUP\n\n" +
                    "\n".join(f"• {p}" for p in players)
                )
    except Exception as e:
        print("Lineup error:", e)

async def live_check():
    event = await get_match()

    # Liverpool ጨዋታ በሌለበት ቀን LIVE አይላክም
    if not event:
        return

    state = event.get("status", {}).get("type", {}).get("name", "")
    key = f"live_{event.get('id')}_{state}_{event.get('competitions',[{}])[0].get('competitors',[{}])[0].get('score')}"

    seen = load_seen()
    if key in seen:
        return

    await bot.send_message(CHANNEL, match_text(event))
    seen.add(key)
    save_seen(seen)

    if state in ("STATUS_SCHEDULED", "STATUS_PRE"):
        await send_lineup(event)

async def news_job(context):
    await send_news()

async def live_job(context):
    await live_check()

async def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.job_queue.run_repeating(
        news_job, interval=300, first=10
    )

    app.job_queue.run_repeating(
        live_job, interval=60, first=20
    )

    print("🔴 Liverpool News Bot Started")
    await app.initialize()
    await app.start()
    await app.updater.start_polling()

    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
