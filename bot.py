
import os
import feedparser
from telegram.ext import Application

TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = "@yegnaLiverpool"

RSS_URL = "https://www.thisisanfield.com/feed
