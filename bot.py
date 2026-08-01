import os
import re
import json
import time
import html
import hashlib
import asyncio
import logging
import requests
import feedparser

from difflib import SequenceMatcher
from urllib.parse import quote_plus

from bs4 import BeautifulSoup
from groq import Groq

from telegram import Bot
from telegram.constants import ParseMode


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)


# =========================================================
# ENVIRONMENT
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
FOOTBALL_API_KEY = os.getenv("FOOTBALL_API_KEY", "").strip()

CHANNEL_ID = os.getenv(
    "CHANNEL_ID",
    "@yegnaLiverpool"
).strip()

LIVERPOOL_TEAM_ID = 40


# =========================================================
# SETTINGS
# =========================================================

NEWS_CHECK_EVERY = 5 * 60
LIVE_CHECK_EVERY = 2 * 60

MAX_NEWS_PER_30_MIN = 2
MIN_NEWS_GAP = 15 * 60

SEEN_FILE = "seen_news_v2.json"
SENT_TIMES_FILE = "sent_times_v2.json"
LIVE_SEEN_FILE = "live_seen_v2.json"


# =========================================================
# TRUSTED SOURCES
# =========================================================

TRUSTED_SOURCES = {
    "Liverpool FC Official": [
        "Liverpool FC",
        "Liverpool Football Club",
        "Liverpoolfc.com"
    ],

    "Paul Joyce": [
        "Paul Joyce"
    ],

    "David Ornstein": [
        "David Ornstein"
    ],

    "James Pearce": [
        "James Pearce"
    ],

    "Lewis Steele": [
        "Lewis Steele"
    ],

    "Melissa Reddy": [
        "Melissa Reddy"
    ],

    "Fabrizio Romano": [
        "Fabrizio Romano"
    ]
}


# =========================================================
# SEARCHES
# =========================================================

SEARCHES = [
    '"Liverpool FC" "Liverpool FC"',
    '"Liverpool" "Paul Joyce"',
    '"Liverpool" "David Ornstein"',
    '"Liverpool" "James Pearce"',
    '"Liverpool" "Lewis Steele"',
    '"Liverpool" "Melissa Reddy"',
    '"Liverpool" "Fabrizio Romano"',
    '"Liverpool FC" transfer',
    '"Liverpool FC" injury',
    '"Liverpool FC" manager',
    '"Liverpool FC" signing',
    '"Liverpool FC" contract'
]


# =========================================================
# GLOBALS
# =========================================================

seen_news = set()
sent_times = []
live_seen = set()


# =========================================================
# CLIENTS
# =========================================================

bot = Bot(
    token=BOT_TOKEN
)

groq = Groq(
    api_key=GROQ_API_KEY
)


# =========================================================
# BASIC CHECK
# =========================================================

if not BOT_TOKEN:
    logger.error("BOT_TOKEN is missing.")

if not GROQ_API_KEY:
    logger.error("GROQ_API_KEY is missing."

# =========================================================
# FILE HELPERS
# =========================================================

def load_json_list(filename):

    try:

        if not os.path.exists(filename):
            return []

        with open(
            filename,
            "r",
            encoding="utf-8"
        ) as file:

            data = json.load(file)

        if isinstance(data, list):
            return data

    except Exception as error:

        logger.error(
            "Load error %s: %s",
            filename,
            error
        )

    return []


def save_json_list(filename, data):

    try:

        with open(
            filename,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                list(data)[-5000:],
                file,
                ensure_ascii=False,
                indent=2
            )

    except Exception as error:

        logger.error(
            "Save error %s: %s",
            filename,
            error
        )


# =========================================================
# LOAD SAVED DATA
# =========================================================

seen_news = set(
    load_json_list(SEEN_FILE)
)

live_seen = set(
    load_json_list(LIVE_SEEN_FILE)
)

sent_times = []

for value in load_json_list(
    SENT_TIMES_FILE
):

    try:

        timestamp = float(value)

        if time.time() - timestamp < 30 * 60:
            sent_times.append(timestamp)

    except Exception:

        continue


# =========================================================
# TEXT CLEANING
# =========================================================

def clean_text(text):

    if not text:
        return ""

    text = html.unescape(
        str(text)
    )

    text = re.sub(
        r"<[^>]+>",
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


def normalize(text):

    text = clean_text(
        text
    ).lower()

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


# =========================================================
# SIMILARITY
# =========================================================

def similarity(first, second):

    first = normalize(first)
    second = normalize(second)

    if not first or not second:
        return 0.0

    return SequenceMatcher(
        None,
        first,
        second
    ).ratio()


# =========================================================
# LIVERPOOL FILTER
# =========================================================

def is_liverpool_news(
    title,
    summary
):

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

        "mohamed salah",
        "virgil van dijk",
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
        "bradley barcola"
    ]

    return any(
        keyword in text
        for keyword in keywords
    )


# =========================================================
# TRUSTED SOURCE DETECTION
# =========================================================

def detect_source(
    title,
    summary,
    source_name
):

    text = (
        f"{title} "
        f"{summary} "
        f"{source_name}"
    ).lower()

    for trusted, aliases in TRUSTED_SOURCES.items():

        for alias in aliases:

            if alias.lower() in text:
                return trusted

    return None
