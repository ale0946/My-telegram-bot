````python
import os
import re
import json
import time
import sqlite3
import hashlib
import logging
import requests
import feedparser
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
CHANNEL = os.getenv("CHANNEL", "@yegnaLiverpool").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN የለም")
if not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY የለም")

client = Groq(api_key=GROQ_API_KEY)

NEWS_CHECK_EVERY = 5 * 60
MIN_NEWS_GAP = 15 * 60
MAX_NEWS_PER_30_MIN = 2

DB = "news.db"

TRUSTED_SOURCES = {
    "Liverpool FC": ["liverpoolfc.com"],
    "Paul Joyce": ["thetimes.com"],
    "David Ornstein": ["theathletic.com"],
    "James Pearce": ["theathletic.com"],
    "Fabrizio Romano": [
        "x.com",
        "twitter.com",
        "fabricioromano.com"
    ]
}

RSS = [
    "https://news.google.com/rss/search?q=site%3Aliverpoolfc.com+Liverpool&hl=en-GB&gl=GB&ceid=GB%3Aen",
    "https://news.google.com/rss/search?q=site%3Atheathletic.com+Liverpool+Ornstein+Pearce&hl=en-GB&gl=GB&ceid=GB%3Aen",
    "https://news.google.com/rss/search?q=site%3Athetimes.com+Liverpool+Paul+Joyce&hl=en-GB&gl=GB&ceid=GB%3Aen",
    "https://news.google.com/rss/search?q=Fabrizio+Romano+Liverpool&hl=en-GB&gl=GB&ceid=GB%3Aen"
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

def db():
    con = sqlite3.connect(DB)
    con.execute("""
        CREATE TABLE IF NOT EXISTS posted (
            id TEXT PRIMARY KEY,
            title TEXT,
            url TEXT,
            created INTEGER
        )
    """)
    con.commit()
    return con

def clean(text):
    text = BeautifulSoup(text or "", "html.parser").get_text(" ")
    return re.sub(r"\s+", " ", text).strip()

def fingerprint(title, url):
    value = re.sub(r"\W+", " ", title.lower()) + url.lower()
    return hashlib.sha256(value.encode()).hexdigest()

def already_posted(fp):
    con = db()
    row = con.execute(
        "SELECT 1 FROM posted WHERE id=?",
        (fp,)
    ).fetchone()
    con.close()
    return row is not None

def save_post(fp, title, url):
    con = db()
    con.execute(
        "INSERT OR IGNORE INTO posted VALUES (?, ?, ?, ?)",
        (fp, title, url, int(time.time()))
    )
    con.commit()
    con.close()

def source_ok(url):
    u = url.lower()
    for domains in TRUSTED_SOURCES.values():
        if any(d in u for d in domains):
            return True
    return False

def get_news():
    items = []

    for rss in RSS:
        try:
            feed = feedparser.parse(rss)
            for x in feed.entries[:10]:
                title = clean(getattr(x, "title", ""))
                url = getattr(x, "link", "").strip()
                summary = clean(getattr(x, "summary", ""))

                if not title or not url:
                    continue
                if not source_ok(url):
                    continue

                items.append({
                    "title": title,
                    "url": url,
                    "summary": summary
                })
        except Exception as e:
            logging.error("RSS error: %s", e)

    return items

def article_text(url):
    try:
        r = requests.get(
            url,
            timeout=15,
            headers={
                "User-Agent":
                "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 Chrome/150 Safari/537.36"
            }
        )
        if r.status_code != 200:
            return ""

        soup = BeautifulSoup(r.text, "html.parser")

        for tag in soup([
            "script",
            "style",
            "nav",
            "footer",
            "header",
            "aside"
        ]):
            tag.decompose()

        text = clean(soup.get_text(" "))
        return text[:12000]
    except Exception:
        return ""

def analyze(item):
    extra = article_text(item["url"])
    content = (
        f"TITLE: {item['title']}\n"
        f"SUMMARY: {item['summary']}\n"
        f"ARTICLE: {extra}"
    )

    prompt = """
አንተ የLiverpool FC ዜና አርታኢ ነህ።

የሚከተለውን መረጃ ብቻ ተጠቀም።
እውነታ አትጨምር።
ያልተረጋገጠ ነገር እንደተረጋገጠ አታቅርብ።
Liverpool FC ጋር በግልጽ የማይያያዝ ዜና REJECT አድርግ።
የተጫዋች ዝውውር፣ ውል፣ ጉዳት ወይም ዋጋ ካልተጠቀሰ አትፍጠር።
የዜናው ዋና ነጥብ አስፈላጊ ከሆነ POST አድርግ።
ሙሉ በሙሉ ተፈጥሯዊ እና ሙያዊ አማርኛ ተጠቀም።
English headline አትጨምር።
Link አትጨምር።

JSON ብቻ መልስ፦
{
 "decision":"POST or REJECT",
 "category":"TRANSFER or MATCH or CLUB or PLAYER or OTHER",
 "headline":"አማርኛ ርዕስ",
 "body":"አማርኛ ዜና",
 "confidence":0
}
"""

    try:
        result = client.chat.completions.create(
            model=GROQ_MODEL,
            temperature=0.1,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": content}
            ]
        )

        text = result.choices[0].message.content.strip()
        text = re.sub(r"^```json|^```|```$", "", text).strip()

        return json.loads(text)

    except Exception as e:
        logging.error("AI error: %s", e)
        return None

def telegram(method, data):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    try:
        r = requests.post(url, data=data, timeout=20)
        if not r.ok:
            logging.error("Telegram: %s", r.text)
        return r.json()
    except Exception as e:
        logging.error("Telegram connection: %s", e)
        return None

def send_news(news, url):
    text = (
        f"🔴 {news['headline']}\n\n"
        f"{news['body']}\n\n"
        f"🔗 {url}"
    )

    return telegram(
        "sendMessage",
        {
            "chat_id": CHANNEL,
            "text": text,
            "disable_web_page_preview": False
        }
    )

def recent_count():
    now = int(time.time())
    con = db()
    count = con.execute(
        "SELECT COUNT(*) FROM posted WHERE created > ?",
        (now - 1800,)
    ).fetchone()[0]
    con.close()
    return count

def last_post_time():
    con = db()
    row = con.execute(
        "SELECT created FROM posted ORDER BY created DESC LIMIT 1"
    ).fetchone()
    con.close()
    return row[0] if row else 0

def process(item):
    fp = fingerprint(item["title"], item["url"])

    if already_posted(fp):
        return False

    result = analyze(item)

    if not result:
        return False

    if result.get("decision") != "POST":
        return False

    if int(result.get("confidence", 0)) < 75:
        return False

    if not result.get("headline") or not result.get("body"):
        return False

    if recent_count() >= MAX_NEWS_PER_30_MIN:
        return False

    last = last_post_time()

    if last and time.time() - last < MIN_NEWS_GAP:
        return False

    sent = send_news(result, item["url"])

    if sent and sent.get("ok"):
        save_post(
            fp,
            result["headline"],
            item["url"]
        )
        logging.info("Posted: %s", result["headline"])
        return True

    return False

def main():
    logging.info("🤖 Liverpool News Bot ተነስቷል 🚀")
    logging.info("Channel: %s", CHANNEL)

    while True:
        try:
            news = get_news()
            logging.info("Found %s trusted articles", len(news))

            for item in news:
                if process(item):
                    break

        except KeyboardInterrupt:
            logging.info("Bot stopped.")
            break

        except Exception as e:
            logging.exception("Main error: %s", e)

        time.sleep(NEWS_CHECK_EVERY)

if __name__ == "__main__":
    main()
````
