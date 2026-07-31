
            if "Liverpool" in home or "Liverpool" in away:
                home_score = game["goals"]["home"]
                away_score = game["goals"]["away"]

                matches.append(
                    f"⚽ {home} {home_score}-{away_score} {away}"
                )

        return matches

    except Exception as e:
        print("Football API Error:", e)
        return []


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


    translated = translate_news(latest.title)


    live = get_live_matches()
    def get_image(item):
    try:
        if "media_content" in item:
            return item.media_content[0]["url"]

        if "media_thumbnail" in item:
            return item.media_thumbnail[0]["url"]

    except:
        pass

    return None

    if live:
        live_text = "\n".join(live)
    else:
        live_text = "⚽ አሁን የሊቨርፑል ቀጥታ ጨዋታ የለም"


    text = f"""
🚨🔴 የሊቨርፑል ዜና

📝 {translated}

⚽ Live:
{live_text}

📰 ምንጭ: Liverpool News

📢 @yegnaLiverpool
"""


    bot = Bot(token=TOKEN)

    image = get_image(latest)

if image:
    await bot.send_photo(
        chat_id=CHANNEL_ID,
        photo=image,
        caption=text
    )
else:
    await bot.send_message(
        chat_id=CHANNEL_ID,
        text=text
    )


    with open(FILE, "w") as f:
        f.write(latest.link)


    print("News sent successfully")


if __name__ == "__main__":
    asyncio.run(send_news())
