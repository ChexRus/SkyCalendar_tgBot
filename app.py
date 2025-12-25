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

# Хранение данных в памяти (пока)
user_data_storage = defaultdict(list)  # user_id -> list[dict]
user_locations = {}  # user_id -> (lat, lon)

app = Flask(__name__)

# Настройка логирования
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Основной объект бота
application = Application.builder().token(os.environ["BOT_TOKEN"]).build()

# ======================
# Обработчики бота
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

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "add_training":
        calendar, step = DetailedTelegramCalendar(min_date=datetime.date(2020, 1, 1)).build()
        await query.edit_message_text(f"Выбери дату тренировки: {LSTEP[step]}", reply_markup=calendar)
        return SELECT_DATE

    elif query.data == "stats":
        await show_stats(update, context)
        return ConversationHandler.END

    elif query.data.startswith("time_"):
        time_slot_map = {
            "time_morning": "Утро (8–12)",
            "time_day": "День (12–15)",
            "time_evening": "Вечер (15–18)",
            "time_night": "Ночь (18–22)",
        }
        time_slot = time_slot_map[query.data]
        context.user_data["time_slot"] = time_slot

        date = context.user_data["selected_date"]
        km = context.user_data["km"]
        user_id = query.from_user.id

        temp = None
        if user_id in user_locations:
            temp = await get_temperature(*user_locations[user_id])

        user_data_storage[user_id].append({
            "date": date,
            "km": km,
            "time_slot": time_slot,
            "temp": temp,
        })

        temp_text = f" ({temp}°C)" if temp is not None else ""
        await query.edit_message_text(
            f"Записал: {date} — {km} км в {time_slot}{temp_text} ✅\nЧто дальше?",
            reply_markup=main_menu_markup(),
        )
        return ConversationHandler.END

async def calendar_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    result, key, step = DetailedTelegramCalendar(min_date=datetime.date(2020, 1, 1)).process(query.data)
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
        [InlineKeyboardButton("Ночь (18–22)", callback_data="time_night")],
    ]
    await update.message.reply_text("Когда была тренировка? Выбери время:", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_TIME

async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.callback_query else update.message.from_user.id
    trainings = user_data_storage[user_id]
    if not trainings:
        text = "Пока нет записей. Добавь первую тренировку!"
    else:
        total_km = sum(t["km"] for t in trainings)
        today = datetime.date.today()
        month_start = today.replace(day=1)
        month_km = sum(t["km"] for t in trainings if t["date"] >= month_start)
        text = f"📊 Статистика:\nОбщий пробег: {total_km:.1f} км\nЗа текущий месяц: {month_km:.1f} км\nТренировок: {len(trainings)}"

    reply_markup = main_menu_markup()
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)

def main_menu_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Добавить тренировку", callback_data="add_training")],
        [InlineKeyboardButton("Статистика", callback_data="stats")],
    ])

# Регистрация хендлеров
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
# Flask роуты
# ======================

@app.route(f"/{os.environ['BOT_TOKEN']}", methods=["POST"])
async def webhook():
    if request.headers.get("content-type") != "application/json":
        abort(403)
    json_data = request.get_json(force=True)
    update = Update.de_json(json_data, application.bot)
    await application.process_update(update)
    return "OK", 200

# Внутренняя асинхронная функция установки webhook
async def _set_webhook_async():
    url = f"https://{os.environ.get('RENDER_EXTERNAL_HOSTNAME')}/{os.environ['BOT_TOKEN']}"
    try:
        await application.bot.set_webhook(url=url)
        logger.info(f"Webhook успешно установлен: {url}")
        return "Webhook установлен успешно! ✅ Теперь бот получает сообщения."
    except Exception as e:
        logger.error(f"Ошибка установки webhook: {e}")
        return f"Ошибка: {str(e)}"

# Ручная установка webhook (синхронный роут — работает с Gunicorn)
@app.route("/set-webhook")
def set_webhook():
    import asyncio
    loop = asyncio.get_event_loop_policy().new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        result = loop.run_until_complete(_set_webhook_async())
    finally:
        # Не закрываем loop — это важно!
        pass
    return result

# Главная страница
@app.route("/")
def index():
    return """
    <h2 style="color: #0088cc;">🏂 SkiCalendarBot работает!</h2>
    <p>Бот успешно запущен на Render.com</p>
    <p><strong>После каждого обновления кода</strong> нужно один раз нажать кнопку ниже:</p>
    <a href="/set-webhook">
        <button style="font-size:20px; padding:15px 30px; background:#00aa00; color:white; border:none; border-radius:10px; cursor:pointer;">
            Установить webhook сейчас
        </button>
    </a>
    <hr>
    <p>После нажатия бот начнёт отвечать на сообщения в Telegram.</p>
    <p>Ты молодец — бот готов к использованию! 🚀</p>
    """

if __name__ == "__main__":
    # Для локального теста
    import asyncio
    asyncio.run(_set_webhook_async())
    app.run(host="0.0.0.0", port=5000)
