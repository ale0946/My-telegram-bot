import os
import re
import json
import time
import asyncio
import hashlib
import logging
import html as html_lib
from difflib import SequenceMatcher
from urllib.parse import quote_plus

import requests
import feedparser

from groq import Groq
from telegram import Bot
from telegram.constants import ParseMode

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
FOOTBALL_API_KEY = os.getenv("FOOTBALL_API_KEY")

CHANNELS = [
"@yegnaLiverpool",
"@yegnaLiverpoolET",
]

CHECK_EVERY = 300

# 1-2 quality news in 30 minutes

MIN_NEWS_GAP = 15 * 60
MAX_NEWS_PER_30_MIN = 2

SEEN_FILE = "last_news.json"
RATE_FILE = "news_rate.json"

LIVERPOOL_TEAM_ID = 40

logging.basicConfig(
level=logging.INFO,
format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("liverpool_bot")

if not BOT_TOKEN:
raise ValueError("BOT_TOKEN is missing")

if not GROQ_API_KEY:
raise ValueError("GROQ_API_KEY is missing")

bot = Bot(token=BOT_TOKEN)
groq = Groq(api_key=GROQ_API_KEY)

TRUSTED_SOURCES = {
"Liverpool FC Official": [
"liverpoolfc.com",
"Liverpool FC",
"Liverpool Football Club",
],

```
"Paul Joyce": [
    "Paul Joyce",
],

"David Ornstein": [
    "David Ornstein",
],

"James Pearce": [
    "James Pearce",
],

"Lewis Steele": [
    "Lewis Steele",
],

"Melissa Reddy": [
    "Melissa Reddy",
],

"Fabrizio Romano": [
    "Fabrizio Romano",
],
```

}

SEARCHES = [
'"Liverpool FC" "Liverpool"',
'"Liverpool" "Paul Joyce"',
'"Liverpool" "David Ornstein"',
'"Liverpool" "James Pearce"',
'"Liverpool" "Lewis Steele"',
'"Liverpool" "Melissa Reddy"',
'"Liverpool" "Fabrizio Romano"',
]

# ============================================================

# MEMORY

# ============================================================

def load_seen():

```
try:

    if not os.path.exists(SEEN_FILE):
        return set()

    with open(
        SEEN_FILE,
        "r",
        encoding="utf-8"
    ) as f:

        data = json.load(f)

    if isinstance(data, list):
        return set(data)

except Exception as e:

    logger.error(
        "Seen memory error: %s",
        e
    )

return set()
```

def save_seen():

```
try:

    with open(
        SEEN_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            list(seen_news)[-3000:],
            f,
            ensure_ascii=False,
            indent=2
        )

except Exception as e:

    logger.error(
        "Seen save error: %s",
        e
    )
```

seen_news = load_seen()

# ============================================================

# RATE MEMORY

# ============================================================

def load_sent_times():

```
try:

    if not os.path.exists(RATE_FILE):
        return []

    with open(
        RATE_FILE,
        "r",
        encoding="utf-8"
    ) as f:

        data = json.load(f)

    if isinstance(data, list):

        now = time.time()

        return [
            float(x)
            for x in data
            if now - float(x) < 30 * 60
        ]

except Exception as e:

    logger.error(
        "Rate memory error: %s",
        e
    )

return []
```

def save_sent_times():

```
try:

    with open(
        RATE_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            sent_times[-10:],
            f
        )

except Exception as e:

    logger.error(
        "Rate save error: %s",
        e
    )
```

sent_times = load_sent_times()

# ============================================================

# TEXT

# ============================================================

def clean_text(text):

```
if not text:
    return ""

text = html_lib.unescape(
    str(text)
)

text = re.sub(
    r"<[^>]*>",
    " ",
    text
)

text = re.sub(
    r"[\r\n\t]+",
    " ",
    text
)

text = re.sub(
    r"\s+",
    " ",
    text
)

return text.strip()
```

def normalize(text):

```
text = clean_text(text).lower()

text = re.sub(
    r"[^a-z0-9\u1200-\u137f ]",
    " ",
    text
)

text = re.sub(
    r"\s+",
    " ",
    text
)

return text.strip()
```

def similarity(a, b):

```
return SequenceMatcher(
    None,
    normalize(a),
    normalize(b)
).ratio()
```

# ============================================================

# LIVERPOOL FILTER

# ============================================================

def is_liverpool_news(title, summary):

```
text = (
    f"{title} {summary}"
).lower()

keywords = [

    "liverpool",
    "liverpool fc",
    "lfc",
    "anfield",
    "reds",

    "arne slot",
    "andoni iraola",

    "virgil van dijk",
    "mohamed salah",
    "florian wirtz",
    "alexis mac allister",
    "ryan gravenberch",
    "dominik szoboszlai",
    "cody gakpo",
    "ibrahima konate",
    "andy robertson",
    "trent alexander-arnold",

    "giovanni leoni",
    "jeremy jacquet",
    "bradley barcola",

]

return any(
    word in text
    for word in keywords
)
```

# ============================================================

# SOURCE

# ============================================================

def detect_source(title, summary, source):

```
source_text = clean_text(
    source
).lower()

title_text = clean_text(
    title
).lower()

summary_text = clean_text(
    summary
).lower()


# Official Liverpool source
if (
    "liverpoolfc.com" in source_text
    or "liverpool fc" in source_text
    or "liverpool football club" in source_text
):

    return "Liverpool FC Official"


# Journalists must appear in the actual
# article title/summary or source name.
journalist_sources = [
    ("Paul Joyce", "Paul Joyce"),
    ("David Ornstein", "David Ornstein"),
    ("James Pearce", "James Pearce"),
    ("Lewis Steele", "Lewis Steele"),
    ("Melissa Reddy", "Melissa Reddy"),
    ("Fabrizio Romano", "Fabrizio Romano"),
]


for name, trusted_name in journalist_sources:

    name_lower = name.lower()

    if (
        name_lower in source_text
        or name_lower in title_text
        or name_lower in summary_text
    ):

        return trusted_name


return None
```

# ============================================================

# GOOGLE NEWS

# ============================================================

def get_google_news(query):

```
url = (
    "https://news.google.com/rss/search?"
    f"q={quote_plus(query)}"
    "&hl=en-US"
    "&gl=US"
    "&ceid=US:en"
)

try:

    response = requests.get(
        url,
        timeout=20,
        headers={
            "User-Agent":
            "Mozilla/5.0 LiverpoolNewsBot/3.0"
        }
    )

    response.raise_for_status()

    return feedparser.parse(
        response.content
    )

except Exception as e:

    logger.error(
        "RSS error: %s",
        e
    )

    return None
```

# ============================================================

# NEWS ID

# ============================================================

def make_id(title, link):

```
value = (
    normalize(title)
    + "|"
    + link.lower().strip()
)

return hashlib.sha256(
    value.encode("utf-8")
).hexdigest()
```

# ============================================================

# FETCH NEWS

# ============================================================

def fetch_news():

```
collected = []

for query in SEARCHES:

    logger.info(
        "Searching: %s",
        query
    )

    feed = get_google_news(
        query
    )

    if not feed:
        continue


    for entry in feed.entries[:15]:

        title = clean_text(
            getattr(
                entry,
                "title",
                ""
            )
        )

        summary = clean_text(
            getattr(
                entry,
                "summary",
                ""
            )
        )

        link = clean_text(
            getattr(
                entry,
                "link",
                ""
            )
        )


        source_obj = getattr(
            entry,
            "source",
            None
        )

        source_name = ""


        if source_obj:

            source_name = clean_text(
                getattr(
                    source_obj,
                    "title",
                    ""
                )
            )


        if not title or not link:
            continue


        if not is_liverpool_news(
            title,
            summary
        ):

            continue


        trusted = detect_source(
            title,
            summary,
            source_name
        )


        if not trusted:
            continue


        news_id = make_id(
            title,
            link
        )


        if news_id in seen_news:
            continue


        collected.append({

            "id": news_id,

            "title": title,

            "summary": summary,

            "link": link,

            "source": trusted,

            "source_name": source_name,

        })


return collected
```

# ============================================================

# DUPLICATES

# ============================================================

def remove_duplicates(items):

```
unique = []

for item in items:

    duplicate = False

    for old in unique:

        title_score = similarity(
            item["title"],
            old["title"]
        )

        content_score = similarity(
            item["summary"],
            old["summary"]
        )


        if (
            title_score >= 0.68
            or content_score >= 0.78
        ):

            duplicate = True
            break


    if not duplicate:

        unique.append(item)


return unique
```

# ============================================================

# CLEAN AI OUTPUT

# ============================================================

def remove_bad_format(text):

```
if not text:
    return ""


# Remove separator lines
text = re.sub(
    r"(?m)^\s*[=\-_#*]{3,}\s*$",
    "",
    text
)


# Remove Markdown headings
text = re.sub(
    r"(?m)^\s*#{1,6}\s*",
    "",
    text
)


# Remove repeated blank lines
text = re.sub(
    r"\n{3,}",
    "\n\n",
    text
)


return text.strip()
```

# ============================================================

# AMHARIC CHECK

# ============================================================

def amharic_ratio(text):

```
if not text:
    return 0


amharic = len(
    re.findall(
        r"[\u1200-\u137F]",
        text
    )
)


letters = len(
    re.findall(
        r"[A-Za-z\u1200-\u137F]",
        text
    )
)


if letters == 0:
    return 0


return amharic / letters
```

def has_english_headline(text):

```
lines = text.splitlines()

for line in lines:

    line = line.strip()

    if not line:
        continue

    if line.startswith("ርዕስ"):
        continue

    if line.startswith("ዜና"):
        break

    english_count = len(
        re.findall(
            r"[A-Za-z]",
            line
        )
    )

    amharic_count = len(
        re.findall(
            r"[\u1200-\u137F]",
            line
        )
    )


    if (
        english_count > 8
        and english_count > amharic_count
    ):

        return True


return False
```

def valid_amharic_news(text):

```
if not text:
    return False


text = remove_bad_format(
    text
)


# Stronger Amharic requirement
if amharic_ratio(text) < 0.60:
    return False


# English headline is NOT allowed
if has_english_headline(text):
    return False


return True
```

# ============================================================

# GROQ TRANSLATION

# ============================================================

def translate_news(item):

```
prompt = f"""
```

አንተ በኢትዮጵያ የምትሰራ የLiverpool FC
የስፖርት ጋዜጠኛ ነህ።

ከታች የተሰጠውን የLiverpool ዜና
ወደ ተፈጥሯዊ፣ ግልጽ፣ የስፖርት ጋዜጠኛ
የሆነ አማርኛ ቀይር።

ይህ ቀጥተኛ የGoogle Translate ትርጉም
አይሁን። በአማርኛ እንደሚጽፍ የኢትዮጵያ
ስፖርት ጋዜጠኛ አዘጋጅ።

ጥብቅ ህጎች:

1. ርዕሱ በአማርኛ ብቻ ይሁን።

2. ዋናው ዜና በተፈጥሯዊ አማርኛ ይሁን።

3. የመጀመሪያውን English headline
   እንደገና አትጻፍ።

4. English paragraph አትተው።

5. የተጫዋች ስም፣ የክለብ ስም እና
   የውድድር ስም English ሊቀር ይችላል።

6. ቁጥሮች፣ ዋጋዎች፣ ቀኖች እና እውነታዎች
   አትቀይር።

7. ያልተሰጠህን መረጃ አትፍጠር።

8. የዝውውር ወሬ ከሆነ ወሬ መሆኑን
   ግልጽ አድርግ።

9. የተረጋገጠ ዝውውር ካልሆነ
   "ተዘግቧል"፣ "ሪፖርት እንደሚለው"
   ወይም "የሚነገረው" በማለት ግልጽ አድርግ።

10. የምንጩን ስም አትፍጠር።

11. ዜናውን በጣም አታሳጥር።
    ዋናውን መረጃ በቂ ርዝመት ግለጽ።

12. ===== ወይም ----- ወይም *** የሚመስሉ
    separator ምልክቶች አትጠቀም።

13. Markdown አትጠቀም።

14. የሚከተለውን ቅርጽ ብቻ ተጠቀም።

ርዕስ:
[ተፈጥሯዊ የአማርኛ ርዕስ]

ዜና:
[ዝርዝር እና ግልጽ የአማርኛ ዜና]

ምንጭ:
[{item["source"]}]

የመጀመሪያው ዜና:
{item["title"]}

የመጀመሪያው ይዘት:
{item["summary"]}
"""

```
try:

    result = groq.chat.completions.create(

        model="llama-3.3-70b-versatile",

        messages=[

            {
                "role": "system",

                "content":
                "You are an Ethiopian Amharic "
                "football journalist. "
                "Your output must be natural "
                "Amharic. Never copy an English "
                "headline into the final headline."
            },

            {
                "role": "user",
                "content": prompt
            }

        ],

        temperature=0.05,

        max_tokens=1400
    )


    text = (
        result
        .choices[0]
        .message
        .content
        .strip()
    )


    text = remove_bad_format(
        text
    )


    if valid_amharic_news(text):

        return text


    logger.warning(
        "First Amharic result rejected. Retrying..."
    )


    retry_prompt = f"""
```

ይህንን Liverpool FC ዜና በትክክለኛ
እና ተፈጥሯዊ የኢትዮጵያ አማርኛ ጻፍ።

አስፈላጊ:

* English headline በፍጹም አትጻፍ።
* English paragraph በፍጹም አትጻፍ።
* የተጫዋች ስሞች እና የክለብ ስሞች
  English ሊቀሩ ይችላሉ።
* ቁጥር እና እውነታ አትቀይር።
* ያልተሰጠ መረጃ አትጨምር።
* የዝውውር ወሬ ከሆነ ይህንን ግልጽ አድርግ።
* Separator አትጠቀም።
* Markdown አትጠቀም።

ቅርጽ:

ርዕስ:
[አማርኛ ርዕስ]

ዜና:
[አማርኛ ዜና]

ምንጭ:
[{item["source"]}]

የዜናው መረጃ:
{item["title"]}

{item["summary"]}
"""

```
    retry = groq.chat.completions.create(

        model="llama-3.3-70b-versatile",

        messages=[

            {
                "role": "system",

                "content":
                "Output natural Ethiopian Amharic "
                "football journalism only."
            },

            {
                "role": "user",
                "content": retry_prompt
            }

        ],

        temperature=0.01,

        max_tokens=1400
    )


    text = (
        retry
        .choices[0]
        .message
        .content
        .strip()
    )


    text = remove_bad_format(
        text
    )


    if not valid_amharic_news(text):

        logger.error(
            "AI output rejected again."
        )

        return None


    return text


except Exception as e:

    logger.error(
        "Groq error: %s",
        e
    )

    return None
```

# ============================================================

# IMAGE

# ============================================================

def get_image(url):

```
try:

    response = requests.get(

        url,

        timeout=15,

        headers={
            "User-Agent":
            "Mozilla/5.0"
        }

    )


    if response.status_code != 200:

        return None


    # No BeautifulSoup dependency.
    # Extract og:image directly.

    match = re.search(

        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',

        response.text,

        re.IGNORECASE
    )


    if match:

        return html_lib.unescape(
            match.group(1)
        )


    match = re.search(

        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',

        response.text,

        re.IGNORECASE
    )


    if match:

        return html_lib.unescape(
            match.group(1)
        )


except Exception as e:

    logger.warning(
        "Image error: %s",
        e
    )


return None
```

# ============================================================

# TELEGRAM MESSAGE

# ============================================================

def make_message(news, link):

```
news = remove_bad_format(
    news
)


safe_news = html_lib.escape(
    news
)


safe_link = html_lib.escape(
    link,
    quote=True
)


return (
    "🔴 <b>LIVERPOOL NEWS</b>\n\n"
    f"{safe_news}\n\n"
    f"🔗 <a href=\"{safe_link}\">"
    "የዋናውን ዜና ይመልከቱ"
    "</a>\n\n"
    "🔴 <b>YN Liverpool</b>"
)
```

# ============================================================

# RATE LIMIT

# ============================================================

def can_send_news():

```
global sent_times


now = time.time()


# Keep only last 30 minutes
sent_times = [

    t for t in sent_times

    if now - t < 30 * 60

]


save_sent_times()


# Maximum 2 per 30 minutes

if len(sent_times) >= MAX_NEWS_PER_30_MIN:

    return False


# Minimum 15 minutes between posts

if sent_times:

    last_time = max(
        sent_times
    )

    if now - last_time < MIN_NEWS_GAP:

        return False


return True
```

# ============================================================

# SEND NEWS

# ============================================================

async def send_news(item):

```
global sent_times


if not can_send_news():

    logger.info(
        "News rate limit active."
    )

    return False


logger.info(
    "Preparing news from %s",
    item["source"]
)


news = translate_news(
    item
)


if not news:

    logger.warning(
        "News rejected by Amharic validation."
    )

    return False


message = make_message(
    news,
    item["link"]
)


image_url = get_image(
    item["link"]
)


success_count = 0


for channel in CHANNELS:

    try:

        if image_url:

            try:

                await bot.send_photo(

                    chat_id=channel,

                    photo=image_url,

                    caption=message,

                    parse_mode=ParseMode.HTML

                )

            except Exception as photo_error:

                logger.warning(
                    "Photo failed for %s: %s",
                    channel,
                    photo_error
                )


                await bot.send_message(

                    chat_id=channel,

                    text=message,

                    parse_mode=ParseMode.HTML,

                    disable_web_page_preview=False

                )


        else:

            await bot.send_message(

                chat_id=channel,

                text=message,

                parse_mode=ParseMode.HTML,

                disable_web_page_preview=False

            )


        logger.info(
            "News sent successfully to %s",
            channel
        )


        success_count += 1


    except Exception as e:

        logger.error(

            "Telegram error for %s: %s",

            channel,

            e

        )


# Only mark as sent when at least
# one Telegram channel received it.

if success_count > 0:

    seen_news.add(
        item["id"]
    )

    save_seen()


    sent_times.append(
        time.time()
    )

    save_sent_times()


    logger.info(
        "News saved to memory."
    )


    return True


logger.error(
    "News was NOT delivered to Telegram."
)


return False
```

# ============================================================

# FOOTBALL API

# ============================================================

def football_request(
endpoint,
params=None
):

```
if not FOOTBALL_API_KEY:

    return None


url = (
    "https://v3.football.api-sports.io/"
    + endpoint
)


headers = {
    "x-apisports-key":
    FOOTBALL_API_KEY
}


try:

    response = requests.get(

        url,

        headers=headers,

        params=params or {},

        timeout=20

    )


    if response.status_code != 200:

        logger.error(
            "Football API status: %s",
            response.status_code
        )

        return None


    return response.json()


except Exception as e:

    logger.error(
        "Football API error: %s",
        e
    )

    return None
```

# ============================================================

# LIVE MATCH

# ============================================================

async def send_live_matches():

```
if not FOOTBALL_API_KEY:

    return


data = football_request(

    "fixtures",

    {
        "team": LIVERPOOL_TEAM_ID,
        "live": "all"
    }

)


if not data:

    return


fixtures = data.get(
    "response",
    []
)


for game in fixtures:

    fixture = game.get(
        "fixture",
        {}
    )


    teams = game.get(
        "teams",
        {}
    )


    goals = game.get(
        "goals",
        {}
    )


    home = teams.get(
        "home",
        {}
    )


    away = teams.get(
        "away",
        {}
    )


    home_name = home.get(
        "name",
        ""
    )


    away_name = away.get(
        "name",
        ""
    )


    home_score = goals.get(
        "home"
    )


    away_score = goals.get(
        "away"
    )


    status = (
        fixture
        .get("status", {})
        .get("long", "")
    )


    minute = (
        fixture
        .get("status", {})
        .get("elapsed")
    )


    message = (

        "🔴 <b>LIVERPOOL LIVE</b>\n\n"

        f"⚽ {html_lib.escape(home_name)} "

        f"{home_score if home_score is not None else 0}"

        " - "

        f"{away_score if away_score is not None else 0} "

        f"{html_lib.escape(away_name)}\n\n"

    )


    if minute:

        message += (
            f"⏱️ {minute}'\n"
        )


    message += (
        f"📌 {html_lib.escape(status)}"
    )


    for channel in CHANNELS:

        try:

            await bot.send_message(

                chat_id=channel,

                text=message,

                parse_mode=ParseMode.HTML

            )


        except Exception as e:

            logger.error(

                "Live Telegram error for %s: %s",

                channel,

                e

            )
```

# ============================================================

# NEWS LOOP

# ============================================================

async def news_loop():

```
while True:

    try:

        logger.info(
            "Checking Liverpool trusted news..."
        )


        if not can_send_news():

            logger.info(
                "30-minute news limit is active."
            )


            await asyncio.sleep(
                CHECK_EVERY
            )


            continue


        news = fetch_news()


        news = remove_duplicates(
            news
        )


        if not news:

            logger.info(
                "No new trusted Liverpool news."
            )


            await asyncio.sleep(
                CHECK_EVERY
            )


            continue


        # Send the first valid story.
        # If AI rejects it, try the next one.

        sent = False


        for item in news:

            if await send_news(item):

                sent = True

                break


        if sent:

            logger.info(
                "Next news check in 5 minutes."
            )


        await asyncio.sleep(
            CHECK_EVERY
        )


    except Exception as e:

        logger.exception(
            "News loop error: %s",
            e
        )


        await asyncio.sleep(
            CHECK_EVERY
        )
```

# ============================================================

# LIVE LOOP

# ============================================================

async def live_loop():

```
while True:

    try:

        await send_live_matches()

    except Exception as e:

        logger.error(
            "Live loop error: %s",
            e
        )


    await asyncio.sleep(
        120
    )
```

# ============================================================

# MAIN

# ============================================================

async def main():

```
logger.info(
    "Liverpool Amharic News Bot started"
)

logger.info(
    "Trusted sources only"
)

logger.info(
    "1-2 quality news per 30 minutes"
)

logger.info(
    "Amharic validation enabled"
)


await asyncio.gather(

    news_loop(),

    live_loop()

)
```

# ============================================================

# START

# ============================================================

if **name** == "**main**":

```
try:

    asyncio.run(
        main()
    )

except KeyboardInterrupt:

    logger.info(
        "Bot stopped."
    )
```
