from flask import Flask, request
from bot import ApplicationBuilder, start, calendar_command, calendar_callback

import os

TOKEN = os.environ.get("BOT_TOKEN")

app = Flask(__name__)

application = ApplicationBuilder().token(TOKEN).build()

# Регистрируем хэндлеры
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("calendar", calendar_command))
application.add_handler(CallbackQueryHandler(calendar_callback))

@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    update = request.get_json(force=True)
    application.update_queue.put(update)
    return "OK"

@app.route("/")
def index():
    return "Bot is running"
