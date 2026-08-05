import os
import time
import sqlite3
import logging
import requests

from dotenv import load_dotenv


# =====================================================
# CONFIG
# =====================================================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

CHANNEL = os.getenv(
    "CHANNEL",
    "@yegnaLiverpool"
).strip()

GROQ_API_KEY = os.getenv(
    "GROQ_API_KEY",
    ""
).strip()

GROQ_MODEL = os.getenv(
    "GROQ_MODEL",
    "openai/gpt-oss-120b"
).strip()


ESPN_TEAM_ID = "364"

LIVE_CHECK_EVERY = 30

DB_FILE = "live_bot.db"

REQUEST_TIMEOUT = 30


HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json"
}


if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN missing")


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(
    "LiverpoolLiveBot"
)


# =====================================================
# DATABASE
# =====================================================

def get_db():

    conn = sqlite3.connect(
        DB_FILE,
        timeout=30
    )

    conn.execute("""
    CREATE TABLE IF NOT EXISTS live_events(
        event_key TEXT PRIMARY KEY,
        text TEXT,
        created INTEGER
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS state(
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """)

    conn.commit()

    return conn



def get_state(key):

    conn = get_db()

    row = conn.execute(
        "SELECT value FROM state WHERE key=?",
        (key,)
    ).fetchone()

    conn.close()

    return row[0] if row else ""



def set_state(key,value):

    conn = get_db()

    conn.execute(
        """
        INSERT INTO state(key,value)
        VALUES(?,?)
        ON CONFLICT(key)
        DO UPDATE SET value=excluded.value
        """,
        (key,str(value))
    )

    conn.commit()
    conn.close()



def event_exists(key):

    conn = get_db()

    row = conn.execute(
        "SELECT event_key FROM live_events WHERE key=?",
        (key,)
    ).fetchone()

    conn.close()

    return row is not None



def save_event(key,text):

    conn = get_db()

    conn.execute(
        """
        INSERT OR IGNORE INTO live_events
        VALUES(?,?,?)
        """,
        (
            key,
            text,
            int(time.time())
        )
    )

    conn.commit()
    conn.close()
    # =====================================================
# TELEGRAM
# =====================================================

def telegram_send(text):

    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/sendMessage"
    )

    try:

        r = requests.post(
            url,
            data={
                "chat_id": CHANNEL,
                "text": text,
            },
            timeout=30
        )

        return r.json().get(
            "ok",
            False
        )

    except Exception as e:

        logger.error(
            "Telegram error: %s",
            e
        )

        return False



# =====================================================
# ESPN
# =====================================================

def get_schedule():

    url = (
        "https://site.api.espn.com/apis/site/v2/"
        "sports/soccer/eng.1/teams/"
        f"{ESPN_TEAM_ID}/schedule"
    )

    try:

        r = requests.get(
            url,
            headers=HEADERS,
            params={
                "limit":100
            },
            timeout=REQUEST_TIMEOUT
        )

        if r.status_code != 200:
            return None

        return r.json()


    except Exception as e:

        logger.error(
            "Schedule error: %s",
            e
        )

        return None



def is_live(event):

    try:

        return (
            event["status"]
            ["type"]
            ["state"]
            == "in"
        )

    except:

        return False



def find_live_match():

    data = get_schedule()

    if not data:
        return None


    for event in data.get(
        "events",
        []
    ):

        if is_live(event):

            return event


    return None



# =====================================================
# MATCH SUMMARY
# =====================================================

def get_summary(event_id):

    url = (
        "https://site.api.espn.com/apis/site/v2/"
        "sports/soccer/eng.1/summary"
    )

    try:

        r = requests.get(
            url,
            headers=HEADERS,
            params={
                "event": event_id
            },
            timeout=REQUEST_TIMEOUT
        )

        if r.status_code != 200:
            return None

        return r.json()


    except Exception as e:

        logger.error(
            "Summary error: %s",
            e
        )

        return None



def get_score(event):

    try:

        teams = (
            event["competitions"][0]
            ["competitors"]
        )

        home = ""
        away = ""

        for team in teams:

            name = (
                team["team"]
                ["displayName"]
            )

            score = team.get(
                "score",
                "0"
            )

            if team["homeAway"] == "home":

                home = f"{name} {score}"

            else:

                away = f"{name} {score}"


        return (
            f"{home} - {away}"
        )


    except:

        return ""



def get_status(event):

    try:

        return (
            event["status"]
            ["type"]
            ["shortDetail"]
        )

    except:

        return "LIVE"
  # =====================================================
# GROQ AMHARIC
# =====================================================

def groq_amharic(text):

    prompt = f"""
Translate this football LIVE update into natural
professional Amharic.

Rules:
- Do not add any information.
- Keep player names in English.
- Keep club names in English.
- Make it short like Telegram LIVE update.

Update:
{text}
"""

    try:

        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization":
                f"Bearer {GROQ_API_KEY}",

                "Content-Type":
                "application/json"
            },

            json={
                "model": GROQ_MODEL,

                "messages":[
                    {
                        "role":"user",
                        "content":prompt
                    }
                ],

                "temperature":0.1
            },

            timeout=40
        )

        data = r.json()

        return (
            data["choices"][0]
            ["message"]
            ["content"]
            .strip()
        )


    except Exception as e:

        logger.error(
            "Groq error: %s",
            e
        )

        return None



# =====================================================
# EVENTS
# =====================================================

def detect_event(play):

    text = play.get(
        "text",
        ""
    ).lower()


    if "goal" in text:
        return "goal"


    if "yellow card" in text:
        return "yellow"


    if (
        "red card" in text
        or "sent off" in text
    ):
        return "red"


    if (
        "substitution" in text
        or "substituted" in text
    ):
        return "substitution"


    if "var" in text:
        return "var"


    return None



def build_live_text(
    play,
    score,
    status
):

    return f"""
🔴 LIVE

{play.get("text","")}

📊 {score}
⏱️ {status}

@yegnaLiverpool
""".strip()



# =====================================================
# PROCESS LIVE
# =====================================================

def process_live(event):

    event_id = str(
        event.get("id")
    )


    score = get_score(
        event
    )

    status = get_status(
        event
    )


    sent = 0


    # SCORE CHANGE
    old_score = get_state(
        f"score_{event_id}"
    )


    # Ignore initial 0-0

    if (
        score
        and score != old_score
        and score not in [
            "",
            "Liverpool 0 - Opponent 0"
        ]
    ):

        if old_score:

            text = f"""
🔴 LIVE

📊 {score}
⏱️ {status}

@yegnaLiverpool
"""

            amharic = groq_amharic(
                text
            )


            if amharic:

                if telegram_send(
                    amharic
                ):

                    sent += 1


        set_state(
            f"score_{event_id}",
            score
        )



    summary = get_summary(
        event_id
    )


    if not summary:

        return sent



    for play in summary.get(
        "plays",
        []
    ):

        event_type = detect_event(
            play
        )


        if not event_type:
            continue


        play_id = play.get(
            "id",
            str(time.time())
        )


        key = (
            f"{event_id}|"
            f"{event_type}|"
            f"{play_id}"
        )


        if event_exists(
            key
        ):

            continue


        raw = build_live_text(
            play,
            score,
            status
        )


        amharic = groq_amharic(
            raw
        )


        if not amharic:
            continue


        if telegram_send(
            amharic
        ):

            save_event(
                key,
                amharic
            )

            sent += 1


    return sent
# =====================================================
# HALF TIME / FULL TIME
# =====================================================

def check_match_end(event):

    event_id = str(
        event.get("id")
    )

    try:

        status = event["status"]["type"]

        name = status.get(
            "name",
            ""
        ).lower()

        detail = status.get(
            "shortDetail",
            ""
        ).lower()


    except:

        return 0


    score = get_score(
        event
    )


    if (
        "halftime" in name
        or "half time" in detail
    ):

        key = (
            f"{event_id}|HT"
        )


        if not event_exists(key):

            text = f"""
⏸️ የመጀመሪያው አጋማሽ ተጠናቋል።

📊 {score}

@yegnaLiverpool
"""

            if telegram_send(text):

                save_event(
                    key,
                    text
                )

                return 1



    if (
        "fulltime" in name
        or "full time" in detail
        or detail == "ft"
    ):

        key = (
            f"{event_id}|FT"
        )


        if not event_exists(key):

            text = f"""
🏁 ጨዋታው ተጠናቋል።

📊 {score}

@yegnaLiverpool
"""

            if telegram_send(text):

                save_event(
                    key,
                    text
                )

                return 1


    return 0



# =====================================================
# LIVE CHECK
# =====================================================

def check_live():

    event = find_live_match()


    if not event:

        logger.info(
            "No Liverpool LIVE match"
        )

        return



    logger.info(
        "Liverpool LIVE: %s",
        event.get(
            "name",
            ""
        )
    )


    check_match_end(
        event
    )


    process_live(
        event
    )



# =====================================================
# MAIN LOOP
# =====================================================

def run_bot():

    logger.info(
        "🔴 Liverpool LIVE Bot Started"
    )

    logger.info(
        "Channel: %s",
        CHANNEL
    )


    conn = get_db()
    conn.close()


    last_check = 0


    while True:

        now = time.time()


        if (
            now - last_check
            >= LIVE_CHECK_EVERY
        ):

            try:

                check_live()


            except Exception as e:

                logger.exception(
                    "LIVE ERROR: %s",
                    e
                )


            last_check = now


        time.sleep(5)



# =====================================================
# START
# =====================================================

if __name__ == "__main__":

    try:

        run_bot()


    except KeyboardInterrupt:

        logger.info(
            "Bot stopped"
        )


    except Exception as e:

        logger.exception(
            "Fatal error: %s",
            e
        )
