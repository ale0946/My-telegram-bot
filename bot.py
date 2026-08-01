import os
import asyncio
import requests
import hashlib
import json
import re

from telegram import Bot
from groq import Groq


# =========================================================
# ENVIRONMENT VARIABLES
# =========================================================

TOKEN = os.getenv("BOT_TOKEN")
GROQ_KEY = os.getenv("GROQ_API_KEY")
FOOTBALL_API_KEY = os.getenv("FOOTBALL_API_KEY")
X_BEARER_TOKEN = os.getenv("X_BEARER_TOKEN")


# =========================================================
# TELEGRAM CHANNELS
# =========================================================

CHANNEL_IDS = [
    "@yegnaLiverpool",
    "@yegnaLiverpoolET"
]


# =========================================================
# SETTINGS
# =========================================================

CHECK_INTERVAL = 20 * 60

X_SEARCH_URL = "https://api.x.com/2/tweets/search/recent"

HISTORY_FILE = "posted_news.json"

MAX_HISTORY = 200


# =========================================================
# ONLY THESE 7 SOURCES
# =========================================================

TRUSTED_ACCOUNTS = {
    "LFC": "Liverpool FC Official",
    "_pauljoyce": "Paul Joyce",
    "David_Ornstein": "David Ornstein",
    "JamesPearceLFC": "James Pearce",
    "LewisSteele_": "Lewis Steele",
    "MelissaReddy_": "Melissa Reddy",
    "FabrizioRomano": "Fabrizio Romano"
}


# =========================================================
# GROQ CLIENT
# =========================================================

client = Groq(
    api_key=GROQ_KEY
) if GROQ_KEY else None


# =========================================================
# CHECK ENVIRONMENT
# =========================================================

def check_environment():

    missing = []

    if not TOKEN:
        missing.append("BOT_TOKEN")

    if not GROQ_KEY:
        missing.append("GROQ_API_KEY")

    if not X_BEARER_TOKEN:
        missing.append("X_BEARER_TOKEN")

    if missing:

        print(
            "Missing environment variables:",
            ", ".join(missing)
        )

        return False

    return True


# =========================================================
# HISTORY
# =========================================================

def load_history():

    if not os.path.exists(HISTORY_FILE):

        return []

    try:

        with open(
            HISTORY_FILE,
            "r",
            encoding="utf-8"
        ) as file:

            data = json.load(file)

            if isinstance(data, list):
                return data

            return []

    except Exception as e:

        print("History read error:", e)

        return []


def save_history(history):

    try:

        history = history[-MAX_HISTORY:]

        with open(
            HISTORY_FILE,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                history,
                file,
                ensure_ascii=False,
                indent=2
            )

    except Exception as e:

        print("History save error:", e)


# =========================================================
# NORMALIZE TEXT
# =========================================================

def normalize_text(text):

    if not text:
        return ""

    text = text.lower()

    text = re.sub(
        r"https?://\S+",
        "",
        text
    )

    text = re.sub(
        r"@\w+",
        "",
        text
    )

    text = re.sub(
        r"#\w+",
        "",
        text
    )

    text = re.sub(
        r"[^a-z0-9\s]",
        " ",
        text
    )

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


# =========================================================
# HASH
# =========================================================

def news_hash(text):

    normalized = normalize_text(text)

    words = normalized.split()

    stop_words = {
        "the",
        "a",
        "an",
        "and",
        "or",
        "to",
        "of",
        "in",
        "on",
        "for",
        "with",
        "from",
        "at",
        "by",
        "is",
        "are",
        "was",
        "were",
        "has",
        "have",
        "had"
    }

    words = [
        word
        for word in words
        if word not in stop_words
    ]

    normalized = " ".join(words)

    return hashlib.sha256(
        normalized.encode("utf-8")
    ).hexdigest()


# =========================================================
# LIVERPOOL KEYWORDS
# =========================================================

LIVERPOOL_KEYWORDS = [

    "liverpool",
    "lfc",
    "anfield",

    "salah",
    "mohamed salah",

    "van dijk",
    "virgil van dijk",

    "iraola",

    "arne slot",

    "mac allister",
    "alexis mac allister",

    "trent",
    "alexander-arnold",

    "szoboszlai",

    "gakpo",

    "nunez",
    "darwin nunez",

    "diaz",
    "luis diaz",

    "konate",
    "ibrahima konate",

    "alisson",
    "alisson becker",

    "robertson",
    "andy robertson",

    "bradley",
    "conor bradley",

    "gravenberch",

    "wirtz",
    "florian wirtz",

    "ekitike",
    "hugo ekitike",

    "leoni",
    "giovanni leoni"
]


# =========================================================
# LIVERPOOL CHECK
# =========================================================

def is_liverpool_news(text):

    lower_text = text.lower()

    for keyword in LIVERPOOL_KEYWORDS:

        if keyword in lower_text:

            return True

    return False


# =========================================================
# SEARCH X
# =========================================================

def search_x():

    if not X_BEARER_TOKEN:

        print(
            "X_BEARER_TOKEN missing"
        )

        return {}


    account_query = " OR ".join(
        f"from:{username}"
        for username in TRUSTED_ACCOUNTS
    )


    query = (
        f"({account_query}) "
        f"-is:retweet "
        f"-is:reply "
        f"lang:en"
    )


    params = {

        "query": query,

        "max_results": 100,

        "sort_order": "recency",

        "tweet.fields": (
            "id,"
            "text,"
            "created_at,"
            "author_id,"
            "attachments,"
            "public_metrics"
        ),

        "expansions": (
            "author_id,"
            "attachments.media_keys"
        ),

        "user.fields": (
            "username,"
            "name"
        ),

        "media.fields": (
            "type,"
            "url,"
            "preview_image_url,"
            "alt_text"
        )
    }


    headers = {

        "Authorization":
            f"Bearer {X_BEARER_TOKEN}"
    }


    try:

        response = requests.get(

            X_SEARCH_URL,

            headers=headers,

            params=params,

            timeout=20
        )


        if response.status_code != 200:

            print(
                "X API error:",
                response.status_code,
                response.text
            )

            return {}


        return response.json()


    except Exception as e:

        print(
            "X request error:",
            e
        )

        return {}


# =========================================================
# GET POSTS
# =========================================================

def get_posts():

    data = search_x()

    if not data:

        return []


    posts = data.get(
        "data",
        []
    )


    includes = data.get(
        "includes",
        {}
    )


    users = {

        user["id"]: user

        for user in includes.get(
            "users",
            []
        )
    }


    media = {

        item["media_key"]: item

        for item in includes.get(
            "media",
            []
        )
    }


    results = []


    for post in posts:

        author_id = post.get(
            "author_id"
        )


        author = users.get(
            author_id,
            {}
        )


        username = author.get(
            "username",
            ""
        )


        if username not in TRUSTED_ACCOUNTS:

            continue


        text = post.get(
            "text",
            ""
        ).strip()


        if not text:

            continue


        # LFC official posts are allowed,
        # but other accounts must mention Liverpool-related content.

        if username != "LFC":

            if not is_liverpool_news(text):

                continue


        image_url = None


        attachments = post.get(
            "attachments",
            {}
        )


        media_keys = attachments.get(
            "media_keys",
            []
        )


        for key in media_keys:

            media_item = media.get(
                key,
                {}
            )


            media_type = media_item.get(
                "type"
            )


            if media_type == "photo":

                image_url = media_item.get(
                    "url"
                )

                break


            if media_type in [
                "video",
                "animated_gif"
            ]:

                image_url = media_item.get(
                    "preview_image_url"
                )

                if image_url:

                    break


        results.append({

            "id": post.get(
                "id"
            ),

            "text": text,

            "author": TRUSTED_ACCOUNTS[
                username
            ],

            "username": username,

            "created_at": post.get(
                "created_at",
                ""
            ),

            "image": image_url,

            "url":
                f"https://x.com/{username}/status/{post.get('id')}",

            "metrics":
                post.get(
                    "public_metrics",
                    {}
                )
        })


    return results


# =========================================================
# EXACT DUPLICATE CHECK
# =========================================================

def is_exact_duplicate(
    post,
    history
):

    post_id = post.get(
        "id",
        ""
    )


    text = post.get(
        "text",
        ""
    )


    current_hash = news_hash(
        text
    )


    for item in history:

        if not isinstance(
            item,
            dict
        ):

            continue


        if item.get(
            "id"
        ) == post_id:

            return True


        if item.get(
            "hash"
        ) == current_hash:

            return True


    return False


# =========================================================
# AI SEMANTIC DUPLICATE CHECK
# =========================================================

def is_semantic_duplicate(
    new_text,
    history
):

    if not client:

        return False


    old_texts = []


    for item in history:

        if not isinstance(
            item,
            dict
        ):

            continue


        old_text = item.get(
            "text",
            ""
        )


        if old_text:

            old_texts.append(
                old_text
            )


    if not old_texts:

        return False


    # Keep the request small.

    old_texts = old_texts[-30:]


    history_text = "\n".join(

        f"{index + 1}. {text}"

        for index, text in enumerate(
            old_texts
        )
    )


    prompt = f"""
You are a strict duplicate-news detector
for a Liverpool FC Telegram channel.

NEW POST:
{new_text}

RECENTLY POSTED NEWS:
{history_text}

Question:

Is the NEW POST reporting the SAME underlying news/event
as any of the recently posted news?

Examples of SAME news:

- Same transfer development
- Same player signing
- Same player leaving
- Same injury update
- Same Liverpool announcement
- Same fixture announcement
- Same Champions League draw/update
- Same manager decision
- Same contract development
- Same event described using different words
- Same story reported by different journalists

Important:

If the wording is different but the underlying event is the same,
answer YES.

If it is genuinely a new development or different event,
answer NO.

Return ONLY:

YES

or

NO
"""


    try:

        result = client.chat.completions.create(

            model="llama-3.3-70b-versatile",

            messages=[

                {
                    "role":
                        "system",

                    "content":
                        "Return ONLY YES or NO."
                },

                {
                    "role":
                        "user",

                    "content":
                        prompt
                }
            ],

            temperature=0,

            max_tokens=5
        )


        answer = (
            result
            .choices[0]
            .message
            .content
            .strip()
            .upper()
        )


        if answer == "YES":

            return True


        return False


    except Exception as e:

        print(
            "Semantic duplicate error:",
            e
        )

        return False


# =========================================================
# IMPORTANT NEWS CHECK
# =========================================================

def is_important_news(
    text,
    author
):

    if not client:

        return False


    prompt = f"""
You are a strict Liverpool FC news editor.

SOURCE:
{author}

POST:
{text}

Decide if this is important enough to publish
on a Liverpool FC Telegram news channel.

POST ONLY genuinely important news:

- Official Liverpool FC announcement
- Confirmed Liverpool transfer
- Major transfer development
- Agreement reached
- Here We Go / deal completed
- Major injury or return
- Manager news
- Player signing
- Contract decision
- Important team news
- Official match result
- Major fixture news
- Champions League news
- Major club statement
- Major breaking Liverpool news

DO NOT POST:

- Ordinary opinions
- Jokes
- Generic football posts
- Unrelated football news
- Old news
- Repeated news
- Rumours with no meaningful new development
- Simple player photos
- Training photos without important information
- Promotional posts
- Generic motivational posts
- Routine posts without important news

If the post is only a photo or simple match update
without important information, return NO.

Return ONLY:

YES

or

NO
"""


    try:

        result = client.chat.completions.create(

            model="llama-3.3-70b-versatile",

            messages=[

                {
                    "role":
                        "system",

                    "content":
                        "Return ONLY YES or NO."
                },

                {
                    "role":
                        "user",

                    "content":
                        prompt
                }
            ],

            temperature=0,

            max_tokens=5
        )


        answer = (
            result
            .choices[0]
            .message
            .content
            .strip()
            .upper()
        )


        return answer == "YES"


    except Exception as e:

        print(
            "Importance check error:",
            e
        )

        return False


# =========================================================
# TRANSLATE NEWS
# =========================================================

def translate_news(text, author):

    if not client:
        print("GROQ_API_KEY is missing")
        return None

    try:

        result = client.chat.completions.create(
            model="llama-3.3-70b-versatile",

            messages=[
                {
                    "role": "system",
                    "content": """
አንተ የLiverpool FC ዜና ወደ አማርኛ የምትተረጉም አርታኢ ነህ።

የተሰጠህን የእንግሊዝኛ ዜና በተፈጥሯዊ፣
ግልጽ እና ትክክለኛ አማርኛ ተርጉም።

አስፈላጊ ህጎች:

- የመጨረሻው ውጤት 100% በአማርኛ መሆን አለበት።
- የእንግሊዝኛውን ርዕስ በEnglish አትመልስ።
- የዜናውን ትርጉም አትቀይር።
- ከተሰጠው ዜና ውጭ መረጃ አትጨምር።
- የተጫዋቾችን ስም እንዳለ ጠብቅ።
- የክለቦችን ስም እንዳለ ጠብቅ።
- Liverpool ን እንደ "ሊቨርፑል" መጻፍ ትችላለህ።
- ለTelegram የሚመች አጭር የዜና ቅርጽ ተጠቀም።
- በመጀመሪያ 🔴 ወይም 🚨 አስቀምጥ።
- ምንጩን ወይም የX ሊንክን አትጨምር።
- ማብራሪያ አትስጥ።
- "Here is the translation" ወይም ተመሳሳይ English ሀረግ አትጠቀም።

በጣም አስፈላጊ:
የመጨረሻው መልስ አማርኛ ዜና ብቻ ይሁን።
"""
                },

                {
                    "role": "user",
                    "content": f"""
ምንጭ: {author}

የሚተረጎመው ዜና:

{text}
"""
                }
            ],

            temperature=0.1,
            max_tokens=1000
        )

        
        translated = result.choices[0].message.content.strip()

        print("ORIGINAL:", text)
        print("TRANSLATED:", translated)

        # አማርኛ ካልመጣ ዜናውን አትላክ
        amharic_count = sum(

# አማርኛ ካልመጣ ዜናውን አትላክ
amharic_count = sum(
    1 for char in translated
    if "\u1200" <= char <= "\u137F"
)

if amharic_count < 10:

    print("Translation is NOT Amharic. News skipped.")

    return None

        return translated

    except Exception as e:

        print("Groq translation error:", e)

        # English news እንዳይለቀቅ
        return None


    prompt = f"""
አንተ የLiverpool FC የTelegram ዜና አርታኢ ነህ።

ምንጭ:
{author}

የዜናው ጽሑፍ:
{text}

ይህንን ዜና ወደ ተፈጥሯዊ፣
ግልጽ እና ሙያዊ አማርኛ ቀይር።

ደንቦች:

1. የተሰጠውን መረጃ ብቻ ተጠቀም።

2. ከራስህ መረጃ አትጨምር።

3. የተጫዋቾችን ስም በትክክል ጠብቅ።

4. የክለቦችን ስም በትክክል ጠብቅ።

5. የዜናውን ትርጉም አትቀይር።

6. እንግሊዝኛውን በቀጥታ አትተው።
   የመጨረሻው ውጤት በአማርኛ ይሁን።

7. ለTelegram የሚመች አጭር የዜና ቅርጽ ተጠቀም።

8. ከርዕሱ መጀመሪያ 🔴 ወይም 🚨 ተጠቀም።

9. የሌለ ዋጋ፣ ቀን፣ ጥቅስ፣
   ስም ወይም ሌላ መረጃ አትፍጠር።

10. ከዜናው ውጭ ማብራሪያ አትጨምር።

11. "ምንጭ:" ብለህ አትጻፍ።

12. የX post ላይ ያለውን ማስታወቂያ፣
    hashtag ወይም አላስፈላጊ ነገር አትድገም።
"""


    try:

        result = client.chat.completions.create(

            model="llama-3.3-70b-versatile",

            messages=[

                {
                    "role":
                        "system",

                    "content":
                        "Write the final news in natural Amharic."
                },

                {
                    "role":
                        "user",

                    "content":
                        prompt
                }
            ],

            temperature=0.1,

            max_tokens=700
        )


        translated = (
            result
            .choices[0]
            .message
            .content
            .strip()
        )


        if not has_amharic(
            translated
        ):

            print(
                "Translation rejected."
            )

            return None


        return translated


    except Exception as e:

        print(
            "Groq translation error:",
            e
        )

        return None


# =========================================================
# AMHARIC CHECK
# =========================================================

def has_amharic(text):

    count = 0


    for char in text:

        if (
            "\u1200"
            <= char
            <= "\u137F"
        ):

            count += 1


    return count >= 5


# =========================================================
# LIVE MATCHES
# =========================================================

def get_live_matches():

    if not FOOTBALL_API_KEY:

        return []


    url = (
        "https://v3.football.api-sports.io/"
        "fixtures?live=all"
    )


    headers = {

        "x-apisports-key":
            FOOTBALL_API_KEY
    }


    try:

        response = requests.get(

            url,

            headers=headers,

            timeout=10
        )


        response.raise_for_status()


        data = response.json()


        matches = []


        for game in data.get(
            "response",
            []
        ):

            home = game[
                "teams"
            ][
                "home"
            ][
                "name"
            ]


            away = game[
                "teams"
            ][
                "away"
            ][
                "name"
            ]


            if (
                "Liverpool"
                not in home
                and
                "Liverpool"
                not in away
            ):

                continue


            home_score = game[
                "goals"
            ][
                "home"
            ]


            away_score = game[
                "goals"
            ][
                "away"
            ]


            elapsed = game[
                "fixture"
            ][
                "status"
            ].get(
                "elapsed"
            )


            if elapsed:

                matches.append(
                    f"⚽ {home} "
                    f"{home_score}-"
                    f"{away_score} "
                    f"{away} "
                    f"({elapsed}')"
                )

            else:

                matches.append(
                    f"⚽ {home} "
                    f"{home_score}-"
                    f"{away_score} "
                    f"{away}"
                )


        return matches


    except Exception as e:

        print(
            "Football API error:",
            e
        )

        return []


# =========================================================
# SEND TO TELEGRAM
# =========================================================

async def send_to_telegram(
    post,
    translated
):

    bot = Bot(
        token=TOKEN
    )


    message = translated


    live = get_live_matches()


    if live:

        message += (
            "\n\n"
            "🔴 LIVE\n"
            +
            "\n".join(live)
        )


    for channel in CHANNEL_IDS:

        try:

            image = post.get(
                "image"
            )


            if image:

                await bot.send_photo(

                    chat_id=channel,

                    photo=image,

                    caption=message
                )

            else:

                await bot.send_message(

                    chat_id=channel,

                    text=message
                )


            print(
                f"Sent successfully to {channel}"
            )


        except Exception as e:

            print(
                f"Telegram error {channel}:",
                e
            )


# =========================================================
# SAVE POST
# =========================================================

def add_to_history(
    history,
    post
):

    item = {

        "id":
            post.get(
                "id",
                ""
            ),

        "hash":
            news_hash(
                post.get(
                    "text",
                    ""
                )
            ),

        "text":
            post.get(
                "text",
                ""
            ),

        "author":
            post.get(
                "author",
                ""
            ),

        "url":
            post.get(
                "url",
                ""
            )
    }


    history.append(
        item
    )


    save_history(
        history
    )


# =========================================================
# PROCESS NEWS
# =========================================================

async def process_news():

    print(
        "\nChecking trusted X sources..."
    )


    history = load_history()


    posts = get_posts()


    if not posts:

        print(
            "No posts found."
        )

        return


    posts.sort(

        key=lambda post:
            post.get(
                "created_at",
                ""
            ),

        reverse=True
    )


    for post in posts:

        print(
            "\nChecking:",
            post["author"]
        )

        print(
            post["text"]
        )


        # -------------------------------------------------
        # EXACT DUPLICATE
        # -------------------------------------------------

        if is_exact_duplicate(
            post,
            history
        ):

            print(
                "Skipped: exact duplicate."
            )

            continue


        # -------------------------------------------------
        # SEMANTIC DUPLICATE
        # -------------------------------------------------

        if is_semantic_duplicate(
            post["text"],
            history
        ):

            print(
                "Skipped: same news/event already posted."
            )

            # Save only the ID so the same X post
            # will not be checked again.

            history.append({

                "id":
                    post.get(
                        "id",
                        ""
                    ),

                "hash":
                    news_hash(
                        post.get(
                            "text",
                            ""
                        )
                    ),

                "text":
                    post.get(
                        "text",
                        ""
                    ),

                "author":
                    post.get(
                        "author",
                        ""
                    ),

                "url":
                    post.get(
                        "url",
                        ""
                    )
            })

            save_history(
                history
            )

            continue


        # -------------------------------------------------
        # IMPORTANT NEWS
        # -------------------------------------------------

        important = is_important_news(

            post["text"],

            post["author"]
        )


        if not important:

            print(
                "Skipped: not important news."
            )

            continue


        # -------------------------------------------------
        # TRANSLATE
        # -------------------------------------------------

        translated = translate_news(

            post["text"],

            post["author"]
        )


        if not translated:

            print(
                "Skipped: translation failed."
            )

            continue


        # -------------------------------------------------
        # SEND
        # -------------------------------------------------

        await send_to_telegram(

            post,

            translated
        )


        # -------------------------------------------------
        # SAVE AS POSTED
        # -------------------------------------------------

        add_to_history(

            history,

            post
        )


        print(
            "\nIMPORTANT NEWS SENT SUCCESSFULLY 🔴"
        )


        # Only one news per 20-minute check.

        break


    save_history(
        history
    )


# =========================================================
# MAIN LOOP
# =========================================================

async def main():

    print(
        "===================================="
    )

    print(
        "Liverpool News Bot started 🚀"
    )

    print(
        "Checking every 20 minutes."
    )

    print(
        "Only 7 trusted sources."
    )

    print(
        "Duplicate protection: ON."
    )

    print(
        "Important news filter: ON."
    )

    print(
        "Amharic translation: ON."
    )

    print(
        "===================================="
    )


    while True:

        try:

            await process_news()

        except Exception as e:

            print(
                "Main loop error:",
                e
            )


        print(
            "\nWaiting 20 minutes..."
        )


        await asyncio.sleep(
            CHECK_INTERVAL
        )


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    if check_environment():

        asyncio.run(
            main()
        )

