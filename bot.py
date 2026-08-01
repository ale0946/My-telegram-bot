    if not X_BEARER_TOKEN:

        print("X_BEARER_TOKEN missing")

        return []


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

            return []


        return response.json()


    except Exception as e:

        print("X request error:", e)

        return []


# =========================================================
# EXTRACT X POSTS
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
        )


        if not text:

            continue


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


            if media_item.get(
                "type"
            ) == "photo":

                image_url = media_item.get(
                    "url"
                )

                break


            if media_item.get(
                "type"
            ) in [
                "video",
                "animated_gif"
            ]:

                image_url = media_item.get(
                    "preview_image_url"
                )

                if image_url:

                    break


        results.append({

            "id": post.get("id"),

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
# IMPORTANT NEWS FILTER
# =========================================================

def classify_news(text, author):

    if not client:

        return False


    prompt = f"""
You are a strict Liverpool FC news editor.

Source:
{author}

Post:
{text}

Decide whether this is IMPORTANT Liverpool FC news
that should be posted to a Telegram Liverpool news channel.

POST only if it is genuinely important, such as:

- official Liverpool FC announcement
- confirmed transfer
- major transfer development
- Here We Go / deal agreed
- major injury or return
- manager news
- player signing
- contract decision
- important team news
- official match result
- major fixture news
- Champions League news
- major club statement
- major breaking Liverpool news

DO NOT POST:

- ordinary opinions
- jokes
- generic football posts
- unrelated football news
- old news
- repeated news
- speculation without meaningful new information
- simple player photos
- training photos without important news
- promotional posts
- routine matchday posts unless they contain important information

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
                        "Return only YES or NO."
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
# TRANSLATE
# =========================================================

def translate_news(text, author):

    if not client:

        return None


    prompt = f"""
አንተ የLiverpool FC የTelegram ዜና አርታኢ ነህ።

የሚከተለውን የታመነ የX ምንጭ ዜና
በተፈጥሯዊ፣ ግልጽ እና ሙያዊ አማርኛ አዘጋጅ።

ምንጭ:
{author}

ደንቦች:

1. የተሰጠውን መረጃ ብቻ ተጠቀም።

2. ከራስህ መረጃ አትጨምር።

3. የተጫዋች፣ የአሰልጣኝ እና የክለብ ስሞችን
   በትክክል ጠብቅ።

4. የዜናውን ትርጉም አትቀይር።

5. እንግሊዝኛውን በቀጥታ አትተው።
   የመጨረሻው ውጤት አማርኛ መሆን አለበት።

6. ለTelegram የሚመች አጭር የዜና ቅርጽ ተጠቀም።

7. ከርዕሱ መጀመሪያ 🔴 ወይም 🚨 ተጠቀም።

8. የሌለ ዋጋ፣ ቀን፣ ጥቅስ፣
   ስም ወይም ሌላ መረጃ አትፍጠር።

9. ከዜናው ውጭ ማብራሪያ አትጨምር።

10. "ምንጭ:" ብለህ አትጻፍ።

የሚተረጎመው:

{text}
"""


    try:

        result = client.chat.completions.create(

            model="llama-3.3-70b-versatile",

            messages=[

                {
                    "role":
                        "system",

                    "content":
                        "Translate and edit strictly into Amharic."
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


        if not has_amharic(translated):

            print(
                "Translation rejected: not Amharic"
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

    amharic_count = 0


    for char in text:

        if (
            "\u1200"
            <= char
            <= "\u137F"
        ):

            amharic_count += 1


    return amharic_count >= 5


# =========================================================
# LIVE MATCH
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
# SEND TELEGRAM
# =========================================================

async def send_to_telegram(
    post,
    translated
):

    bot = Bot(
        token=TOKEN
    )


    message = translated


    # -----------------------------------------------------
    # LIVE MATCH
    # -----------------------------------------------------

    live = get_live_matches()


    if live:

        message += (
            "\n\n"
            "🔴 LIVE\n"
            +
            "\n".join(live)
        )


    # -----------------------------------------------------
    # SEND
    # -----------------------------------------------------

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
# PROCESS NEWS
# =========================================================

async def process_news():

    print(
        "Checking trusted X sources..."
    )


    history = load_history()


    posts = get_posts()


    if not posts:

        print(
            "No new posts found."
        )

        return


    # newest first

    posts.sort(

        key=lambda x:
        x.get(
            "created_at",
            ""
        ),

        reverse=True
    )


    for post in posts:

        post_id = post[
            "id"
        ]


        text_hash = news_hash(
            post[
                "text"
            ]
        )


        # -------------------------------------------------
        # DUPLICATE CHECK
        # -------------------------------------------------

        if post_id in history:

            continue


        if text_hash in history:

            continue


        print(
            "Checking:",
            post["author"]
        )


        print(
            post["text"]
        )


        # -------------------------------------------------
        # IMPORTANT NEWS
        # -------------------------------------------------

        important = classify_news(

            post["text"],

            post["author"]
        )


        if not important:

            print(
                "Skipped: not important"
            )

            history.append(
                post_id
            )

            history.append(
                text_hash
            )

            continue


        # -------------------------------------------------
        # TRANSLATION
        # -------------------------------------------------

        translated = translate_news(

            post["text"],

            post["author"]
        )


        if not translated:

            print(
                "Skipped: translation failed"
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
        # SAVE
        # -------------------------------------------------

        history.append(
            post_id
        )

        history.append(
            text_hash
        )


        save_history(
            history
        )


        print(
            "Important Liverpool news sent."
        )


        # ONLY ONE NEWS PER CHECK

        break


    save_history(
        history
    )


# =========================================================
# MAIN
# =========================================================

async def main():

    print(
        "Liverpool News Bot started 🚀"
    )


    print(
        "Checking every 20 minutes."
    )


    print(
        "Sources: Liverpool FC + 6 trusted reporters."
    )


    while True:

        try:

            await process_news()

        except Exception as e:

            print(
                "Main error:",
                e
            )


        print(
            "Waiting 20 minutes..."
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
