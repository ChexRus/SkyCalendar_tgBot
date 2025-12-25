import datetime
import logging
import os
from collections import defaultdict
import requests
from flask import Flask, request, abort
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)
from telegram_bot_calendar import DetailedTelegramCalendar, LSTEP

# Состояния диалога
SELECT_DATE, INPUT_KM, SELECT_TIME = range(3)

# Хранение данных в памяти
user_data_storage = defaultdict(list)  # user_id -> list[dict]
user_locations = {}  # user_id -> (lat, lon)

app = Flask(__name__)

# Логирование
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Основной объект бота
application = Application.builder().token(os.environ["BOT_TOKEN"]).build()

# ======================
# Обработчики бота (без изменений)
# ======================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[KeyboardButton("Поделиться локацией", request_location=True)]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    main_keyboard = [
        [InlineKeyboardButton("Добавить тренировку", callback_data="add_training")],
        [InlineKeyboardButton("Статистика", callback_data="stats")],
    ]
    main_markup = InlineKeyboardMarkup(main_keyboard)
    await update.message.reply_text(
        "Привет! Я SkiCalendarBot ❄️🏂\nПоделись локацией, чтобы я показывал температуру во время тренировок:",
        reply_markup=reply_markup,
    )
    await update.message.reply_text("Выбери действие:", reply_markup=main_markup)

async def location_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lat = update.message.location.latitude
    lon = update.message.location.longitude
    user_locations[user_id] = (lat, lon)
    await update.message.reply_text("Локация сохранена! Теперь буду показывать температуру 🌡️")

async def get_temperature(lat: float, lon: float) -> float | None:
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m"
        response = requests.get(url, timeout=10).json()
        return response["current"]["temperature_2m"]
    except Exception as e:
        logger.error(f"Error getting temperature: {e}")
        return None

# ... (остальные handlers: button_handler, calendar_handler, input_km, show_stats, main_menu_markup — без изменений)

# Регистрация хендлеров (без изменений)
conv_handler = ConversationHandler(
    entry_points=[CallbackQueryHandler(button_handler, pattern="^(add_training|stats)$")],
    states={
        SELECT_DATE: [CallbackQueryHandler(calendar_handler)],
        INPUT_KM: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_km)],
        SELECT_TIME: [CallbackQueryHandler(button_handler, pattern="^time_")],
    },
    fallbacks=[],
    allow_reentry=True,
)

application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.LOCATION, location_handler))
application.add_handler(conv_handler)

# ======================
# Инициализация Application
# ======================
async def _initialize_app():
    await application.initialize()
    await application.start()
    logger.info("Application initialized and started")

import asyncio
asyncio.run(_initialize_app())

# ======================
# Flask роуты
# ======================
@app.route(f"/{os.environ['BOT_TOKEN']}", methods=["POST"])
def webhook():
    if request.headers.get("content-type") != "application/json":
        abort(403)
    json_data = request.get_json(force=True)
    update = Update.de_json(json_data, application.bot)
    application.update_queue.put_nowait(update)
    return "OK", 200

# Асинхронный роут для установки webhook (исправлено)
@app.route("/set-webhook")
async def set_webhook():
    url = f"https://{os.environ.get('RENDER_EXTERNAL_HOSTNAME')}/{os.environ['BOT_TOKEN']}"
    try:
        await application.bot.set_webhook(url=url)
        logger.info(f"Webhook установлен: {url}")
        return "Webhook установлен успешно! ✅"
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        return f"Ошибка: {str(e)}"

# Главная страница (без изменений)
@app.route("/")
def index():
    return """
    <h2 style="color: #0088cc;">🏂 SkiCalendarBot — всё готово!</h2>
    <p>Бот работает на Render.com и отвечает на сообщения.</p>
    <p>После деплоя/обновления нажми кнопку один раз:</p>
    <a href="/set-webhook">
        <button style="font-size:20px; padding:15px 30px; background:#00aa00; color:white; border:none; border-radius:10px; cursor:pointer;">
            Установить webhook
        </button>
    </a>
    <hr>
    <p>Готово? Пиши боту команду /start 🚀</p>
    """

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
