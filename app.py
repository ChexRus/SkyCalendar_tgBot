import os
import logging
import datetime
import asyncio
import requests
import asyncpg

from flask import Flask, request, abort
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, ConversationHandler, filters
)
from telegram_bot_calendar import DetailedTelegramCalendar, LSTEP

# ======================
# Настройка логов
# ======================
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ======================
# Flask app
# ======================
app = Flask(__name__)

# ======================
# Настройки бота
# ======================
BOT_TOKEN = os.environ["BOT_TOKEN"]
DATABASE_URL = os.environ["DATABASE_URL"]

# Состояния диалога
SELECT_DATE, INPUT_KM, SELECT_TIME = range(3)

# ======================
# Хелперы для БД
# ======================
async def init_db():
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS trainings (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            date DATE,
            km REAL,
            time_slot TEXT,
            temp REAL
        )
    """)
    await conn.close()

async def save_training(user_id, date, km, time_slot, temp):
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute(
        "INSERT INTO trainings(user_id, date, km, time_slot, temp) VALUES($1,$2,$3,$4,$5)",
        user_id, date, km, time_slot, temp
    )
    await conn.close()

async def get_stats(user_id):
    conn = await asyncpg.connect(DATABASE_URL)
    rows = await conn.fetch("SELECT date, km, time_slot, temp FROM trainings WHERE user_id=$1", user_id)
    await conn.close()
    return rows

# ======================
# Хелперы для интерфейса
# ======================
def main_menu_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Добавить тренировку", callback_data="add_training")],
        [InlineKeyboardButton("Статистика", callback_data="stats")]
    ])

async def get_temperature(lat: float, lon: float) -> float | None:
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m"
        response = requests.get(url, timeout=10).json()
        return response["current"]["temperature_2m"]
    except Exception as e:
        logger.error(f"Error getting temperature: {e}")
        return None

# ======================
# Обработчики бота
# ======================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[KeyboardButton("Поделиться локацией", request_location=True)]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

    main_keyboard = [
        [InlineKeyboardButton("Добавить тренировку", callback_data="add_training")],
        [InlineKeyboardButton("Статистика", callback_data="stats")]
    ]
    main_markup = InlineKeyboardMarkup(main_keyboard)

    await update.message.reply_text(
        "Привет! Я SkiCalendarBot ❄️🏂\nПоделись локацией, чтобы я показывал температуру:",
        reply_markup=reply_markup
    )
    await update.message.reply_text("Выбери действие:", reply_markup=main_markup)

async def location_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lat = update.message.location.latitude
    lon = update.message.location.longitude
    context.user_data["location"] = (lat, lon)
    await update.message.reply_text("Локация сохранена! 🌡️")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "add_training":
        calendar, step = DetailedTelegramCalendar(min_date=datetime.date(2020,1,1)).build()
        await query.edit_message_text(f"Выбери дату тренировки: {LSTEP[step]}", reply_markup=calendar)
        return SELECT_DATE

    elif query.data == "stats":
        user_id = query.from_user.id
        rows = await get_stats(user_id)
        if not rows:
            text = "Пока нет записей. Добавь первую тренировку!"
        else:
            total_km = sum(r["km"] for r in rows)
            text = f"📊 Статистика:\nОбщий пробег: {total_km:.1f} км\nТренировок: {len(rows)}"
        await query.edit_message_text(text, reply_markup=main_menu_markup())
        return ConversationHandler.END

    elif query.data.startswith("time_"):
        time_map = {
            "time_morning": "Утро (8–12)",
            "time_day": "День (12–15)",
            "time_evening": "Вечер (15–18)",
            "time_night": "Ночь (18–22)"
        }
        time_slot = time_map[query.data]
        context.user_data["time_slot"] = time_slot

        date = context.user_data["selected_date"]
        km = context.user_data["km"]
        user_id = query.from_user.id
        temp = None
        if "location" in context.user_data:
            temp = await get_temperature(*context.user_data["location"])

        await save_training(user_id, date, km, time_slot, temp)
        temp_text = f" ({temp}°C)" if temp else ""
        await query.edit_message_text(
            f"Записал: {date} — {km} км в {time_slot}{temp_text} ✅\nЧто дальше?",
            reply_markup=main_menu_markup()
        )
        return ConversationHandler.END

async def calendar_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    result, key, step = DetailedTelegramCalendar(min_date=datetime.date(2020,1,1)).process(query.data)
    if not result and key:
        await query.edit_message_text(f"Выбери {LSTEP[step]}", reply_markup=key)
    elif result:
        context.user_data["selected_date"] = result
        await query.edit_message_text(f"Выбрана дата: {result}\nВведи пройденные километры (например, 15.5):")
        return INPUT_KM

async def input_km(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        km = float(update.message.text.replace(",", "."))
        if km <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("Ошибка. Введи положительное число (например, 12.3):")
        return INPUT_KM

    context.user_data["km"] = km

    keyboard = [
        [InlineKeyboardButton("Утро (8–12)", callback_data="time_morning")],
        [InlineKeyboardButton("День (12–15)", callback_data="time_day")],
        [InlineKeyboardButton("Вечер (15–18)", callback_data="time_evening")],
        [InlineKeyboardButton("Ночь (18–22)", callback_data="time_night")]
    ]
    await update.message.reply_text("Когда была тренировка? Выбери время:", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_TIME

# ======================
# Настройка Application
# ======================
def create_application():
    app_instance = Application.builder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(button_handler, pattern="^(add_training|stats)$")],
        states={
            SELECT_DATE: [CallbackQueryHandler(calendar_handler)],
            INPUT_KM: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_km)],
            SELECT_TIME: [CallbackQueryHandler(button_handler, pattern="^time_")]
        },
        fallbacks=[],
        allow_reentry=True
    )

    app_instance.add_handler(CommandHandler("start", start))
    app_instance.add_handler(MessageHandler(filters.LOCATION, location_handler))
    app_instance.add_handler(conv_handler)
    return app_instance

bot_app = create_application()

# ======================
# Flask вебхуки
# ======================
@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    if request.headers.get("content-type") != "application/json":
        abort(403)
    json_data = request.get_json(force=True)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        update = Update.de_json(json_data, bot_app.bot)
        loop.run_until_complete(bot_app.process_update(update))
        return "OK", 200
    finally:
        loop.close()

@app.route("/")
def index():
    return "SkiCalendarBot is running ✅"

# ======================
# Запуск
# ======================
if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply()
    asyncio.get_event_loop().run_until_complete(init_db())

    port = int(os.environ.get("PORT", 5000))
    bot_app.run_polling()  # Можно временно для локального теста
    app.run(host="0.0.0.0", port=port)
