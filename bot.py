import os
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = "@yegnaLiverpool"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔴 Liverpool News Bot ተነስቷል 🚀")

async def test_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(
        chat_id=CHANNEL_ID,
        text="🔴 Liverpool News Bot በትክክል ተገናኝቷል! 🚀"
    )
    await update.message.reply_text("Channel ላይ መልእክት ተልኳል ✅")

app = Application.builder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("post", test_post))

app.run_polling()
