import datetime
import logging
from collections import defaultdict

import requests  # Для погоды
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
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

# Состояния
SELECT_DATE, INPUT_KM, SELECT_TIME = range(3)

# Хранение: user_id -> list[{'date': date, 'km': float, 'time_slot': str, 'temp': float or None}]
user_data_storage = defaultdict(list)
# Локации: user_id -> (lat, lon)
user_locations = {}

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[KeyboardButton("Поделиться локацией", request_location=True)]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    
    main_keyboard = [
        [InlineKeyboardButton("Добавить тренировку", callback_data="add_training")],
        [InlineKeyboardButton("Статистика", callback_data="stats")],
    ]
    main_markup = InlineKeyboardMarkup(main_keyboard)
    
    await update.message.reply_text(
        "Привет! Я SkiCalendarBot 🏂\nПоделись своей локацией (для показа температуры при тренировках):",
        reply_markup=reply_markup
    )
    await update.message.reply_text("Выбери действие:", reply_markup=main_markup)

async def location_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lat = update.message.location.latitude
    lon = update.message.location.longitude
    user_locations[user_id] = (lat, lon)
    await update.message.reply_text(f"Локация сохранена! (примерно {lat:.2f}, {lon:.2f})\nТеперь могу показывать температуру 🌡️")

async def get_temperature(lat: float, lon: float) -> float | None:
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m"
        response = requests.get(url, timeout=10).json()
        return response["current"]["temperature_2m"]
    except:
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

    # Выбор времени
    elif query.data.startswith("time_"):
        time_slot = {"time_morning": "Утро (8-12)", "time_day": "День (12-15)",
                     "time_evening": "Вечер (15-18)", "time_night": "Ночь (18-22)"}[query.data]
        context.user_data["time_slot"] = time_slot

        date = context.user_data["selected_date"]
        km = context.user_data["km"]

        user_id = query.from_user.id
        temp = None
        if user_id in user_locations:
            temp = await get_temperature(*user_locations[user_id])

        user_data_storage[user_id].append({"date": date, "km": km, "time_slot": time_slot, "temp": temp})

        temp_text = f" ({temp}°C)" if temp is not None else ""
        await query.edit_message_text(
            f"Записал: {date} — {km} км в {time_slot}{temp_text} ✅\nЧто дальше?",
            reply_markup=main_menu_markup()
        )
        return ConversationHandler.END

async def calendar_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    result, key, step = DetailedTelegramCalendar(min_date=datetime.date(2020, 1, 1)).process(query.data)
    if not result and key:
        await query.edit_message_text(f"Выбери {LSTEP[step]}", reply_markup=key)
    elif result:
        context.user_data["selected_date"] = result
        await query.edit_message_text(f"Выбрана дата: {result}\nВведи км (например, 15.5):")
        return INPUT_KM

async def input_km(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        km = float(update.message.text.replace(",", "."))
        if km <= 0: raise ValueError
    except ValueError:
        await update.message.reply_text("Неверно. Введи число > 0 (например, 12.3):")
        return INPUT_KM

    context.user_data["km"] = km
    date = context.user_data["selected_date"]

    keyboard = [
        [InlineKeyboardButton("Утро (8-12)", callback_data="time_morning")],
        [InlineKeyboardButton("День (12-15)", callback_data="time_day")],
        [InlineKeyboardButton("Вечер (15-18)", callback_data="time_evening")],
        [InlineKeyboardButton("Ночь (18-22)", callback_data="time_night")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(f"Когда катался {date}? Выбери время:", reply_markup=reply_markup)
    return SELECT_TIME

async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.callback_query else update.message.from_user.id
    trainings = user_data_storage[user_id]
    if not trainings:
        text = "Нет записей. Добавь тренировку!"
    else:
        total_km = sum(t["km"] for t in trainings)
        today = datetime.date.today()
        month_start = today.replace(day=1)
        month_km = sum(t["km"] for t in trainings if t["date"] >= month_start)
        text = f"📊 Статистика:\nОбщий пробег: {total_km:.1f} км\nЗа месяц: {month_km:.1f} км\nТренировок: {len(trainings)}"

    reply_markup = main_menu_markup()
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)

def main_menu_markup():
    keyboard = [
        [InlineKeyboardButton("Добавить тренировку", callback_data="add_training")],
        [InlineKeyboardButton("Статистика", callback_data="stats")],
    ]
    return InlineKeyboardMarkup(keyboard)

def main():
    application = Application.builder().token("8585818586:AAH4Z55pcyUW09nfGltDVGCaQikI9Rp2ND4").build()

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

    application.run_polling()

if __name__ == "__main__":
    main()
