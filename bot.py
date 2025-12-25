import os
import datetime
import logging
from collections import defaultdict

import httpx
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

# ======================
# Конфигурация
# ======================

BOT_TOKEN = os.environ["BOT_TOKEN"]
WEBHOOK_SECRET = BOT_TOKEN  # используем токен как путь

PORT = int(os.environ.get("PORT", 5000))
HOSTNAME = os.environ.get("RENDER_EXTERNAL_HOSTNAME")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ======================
# Flask
# ======================

app = Flask(__name__)

# ======================
# Telegram состояния
# ======================

SELECT_DATE, INPUT_KM, SELECT_TIME = range(3)

# ======================
# Хранилище (in-memory)
# ======================

user_trainings = defaultdict(list)
user_locations = {}

# ======================
# Вспомогательные функции
# ======================

def main_menu():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Добавить тренировку", callback_data="add")],
            [InlineKeyboardButton("Статистика", callback_data="stats")],
        ]
    )


async def get_temperature(lat: float, lon: float) -> float | None:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "current": "temperature_2m",
                },
            )
            return r.json()["current"]["temperature_2m"]
    except Exception as e:
        logger.error(f"Temperature error: {e}")
        return None

# ======================
# Handlers
# ======================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    location_kb = ReplyKeyboardMarkup(
        [[KeyboardButton("Поделиться локацией", request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )

    await update.message.reply_text(
        "Привет! ❄️\nПоделись локацией для отображения температуры.",
        reply_markup=location_kb,
    )
    await update.message.reply_text("Выбери действие:", reply_markup=main_menu())


async def save_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_locations[update.effective_user.id] = (
        update.message.location.latitude,
        update.message.location.longitude,
    )
    await update.message.reply_text("Локация сохранена 🌡️")


async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "add":
        calendar, step = DetailedTelegramCalendar(
            min_date=datetime.date(2020, 1, 1)
        ).build()
        await query.edit_message_text(
            f"Выбери дату ({LSTEP[step]}):",
            reply_markup=calendar,
        )
        return SELECT_DATE

    if query.data == "stats":
        await show_stats(update, context)
        return ConversationHandler.END

    if query.data.startswith("time_"):
        time_map = {
            "time_morning": "Утро",
            "time_day": "День",
            "time_evening": "Вечер",
            "time_night": "Ночь",
        }

        user_id = query.from_user.id
        date = context.user_data["date"]
        km = context.user_data["km"]
        time_slot = time_map[query.data]

        temp = None
        if user_id in user_locations:
            temp = await get_temperature(*user_locations[user_id])

        user_trainings[user_id].append(
            {
                "date": date,
                "km": km,
                "time": time_slot,
                "temp": temp,
            }
        )

        t = f" ({temp}°C)" if temp is not None else ""
        await query.edit_message_text(
            f"Записано: {date} — {km} км, {time_slot}{t} ✅",
            reply_markup=main_menu(),
        )
        return ConversationHandler.END


async def calendar_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    result, keyboard, step = DetailedTelegramCalendar(
        min_date=datetime.date(2020, 1, 1)
    ).process(query.data)

    if not result:
        await query.edit_message_text(
            f"Выбери {LSTEP[step]}:",
            reply_markup=keyboard,
        )
        return SELECT_DATE

    context.user_data["date"] = result
    await query.edit_message_text("Введи километры:")
    return INPUT_KM


async def input_km(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        km = float(update.message.text.replace(",", "."))
        if km <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("Введи положительное число:")
        return INPUT_KM

    context.user_data["km"] = km

    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Утро", callback_data="time_morning")],
            [InlineKeyboardButton("День", callback_data="time_day")],
            [InlineKeyboardButton("Вечер", callback_data="time_evening")],
            [InlineKeyboardButton("Ночь", callback_data="time_night")],
        ]
    )

    await update.message.reply_text("Когда была тренировка?", reply_markup=kb)
    return SELECT_TIME


async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    data = user_trainings[user_id]

    if not data:
        text = "Пока нет тренировок."
    else:
        total = sum(t["km"] for t in data)
        month_start = datetime.date.today().replace(day=1)
        month = sum(t["km"] for t in data if t["date"] >= month_start)
        text = (
            f"📊 Статистика\n"
            f"Всего: {total:.1f} км\n"
            f"За месяц: {month:.1f} км\n"
            f"Тренировок: {len(data)}"
        )

    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=main_menu())
    else:
        await update.message.reply_text(text, reply_markup=main_menu())

# ======================
# Telegram Application
# ======================

telegram_app = Application.builder().token(BOT_TOKEN).build()

conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(menu_handler)],
    states={
        SELECT_DATE: [CallbackQueryHandler(calendar_handler)],
        INPUT_KM: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_km)],
        SELECT_TIME: [CallbackQueryHandler(menu_handler, pattern="^time_")],
    },
    fallbacks=[],
)

telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(MessageHandler(filters.LOCATION, save_location))
telegram_app.add_handler(conv)

# ======================
# Webhook
# ======================

@app.route(f"/{WEBHOOK_SECRET}", methods=["POST"])
async def telegram_webhook():
    if request.headers.get("content-type") != "application/json":
        abort(403)

    update = Update.de_json(request.json, telegram_app.bot)
    await telegram_app.process_update(update)
    return "OK", 200


@app.route("/health")
def health():
    return {"status": "ok"}, 200

# ======================
# Запуск
# ======================

@app.before_serving
async def startup():
    await telegram_app.initialize()
    await telegram_app.bot.set_webhook(
        url=f"https://{HOSTNAME}/{WEBHOOK_SECRET}"
    )
    logger.info("Webhook установлен")


@app.after_serving
async def shutdown():
    await telegram_app.shutdown()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
