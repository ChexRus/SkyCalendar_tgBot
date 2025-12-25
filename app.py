import datetime
import logging
import os
from collections import defaultdict

import httpx
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ======================
# Состояния диалога
# ======================

SELECT_DATE, INPUT_KM, SELECT_TIME = range(3)

# ======================
# Хранилище (in-memory)
# ======================

user_data_storage = defaultdict(list)  # user_id -> list[dict]
user_locations = {}  # user_id -> (lat, lon)

# ======================
# Вспомогательные функции
# ======================

def main_menu_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Добавить тренировку", callback_data="add_training")],
            [InlineKeyboardButton("Статистика", callback_data="stats")],
        ]
    )


async def get_temperature(lat: float, lon: float) -> float | None:
    try:
        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m",
        }
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            return r.json()["current"]["temperature_2m"]
    except Exception as e:
        logger.error(f"Temperature error: {e}")
        return None

# ======================
# Handlers
# ======================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    location_keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("Поделиться локацией", request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )

    await update.message.reply_text(
        "Привет! Я SkiCalendarBot ❄️\n"
        "Поделись локацией, чтобы я показывал температуру во время тренировок:",
        reply_markup=location_keyboard,
    )
    await update.message.reply_text(
        "Выбери действие:",
        reply_markup=main_menu_markup(),
    )


async def location_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_locations[update.effective_user.id] = (
        update.message.location.latitude,
        update.message.location.longitude,
    )
    await update.message.reply_text("Локация сохранена 🌡️")


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "add_training":
        calendar, step = DetailedTelegramCalendar(
            min_date=datetime.date(2020, 1, 1)
        ).build()
        await query.edit_message_text(
            f"Выбери дату: {LSTEP[step]}",
            reply_markup=calendar,
        )
        return SELECT_DATE

    if query.data == "stats":
        await show_stats(update, context)
        return ConversationHandler.END

    if query.data.startswith("time_"):
        time_map = {
            "time_morning": "Утро (8–12)",
            "time_day": "День (12–15)",
            "time_evening": "Вечер (15–18)",
            "time_night": "Ночь (18–22)",
        }

        user_id = query.from_user.id
        time_slot = time_map[query.data]
        date = context.user_data["date"]
        km = context.user_data["km"]

        temp = None
        if user_id in user_locations:
            temp = await get_temperature(*user_locations[user_id])

        user_data_storage[user_id].append(
            {
                "date": date,
                "km": km,
                "time": time_slot,
                "temp": temp,
            }
        )

        temp_text = f" ({temp}°C)" if temp is not None else ""
        await query.edit_message_text(
            f"Записал: {date} — {km} км, {time_slot}{temp_text} ✅",
            reply_markup=main_menu_markup(),
        )
        return ConversationHandler.END


async def calendar_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    result, keyboard, step = DetailedTelegramCalendar(
        min_date=datetime.date(2020, 1, 1)
    ).process(query.data)

    if not result:
        await query.edit_message_text(
            f"Выбери {LSTEP[step]}",
            reply_markup=keyboard,
        )
        return SELECT_DATE

    context.user_data["date"] = result
    await query.edit_message_text(
        f"Дата: {result}\nВведи километры:"
    )
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

    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Утро", callback_data="time_morning")],
            [InlineKeyboardButton("День", callback_data="time_day")],
            [InlineKeyboardButton("Вечер", callback_data="time_evening")],
            [InlineKeyboardButton("Ночь", callback_data="time_night")],
        ]
    )

    await update.message.reply_text(
        "Когда была тренировка?",
        reply_markup=keyboard,
    )
    return SELECT_TIME


async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    data = user_data_storage[user_id]

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
        await update.callback_query.edit_message_text(text, reply_markup=main_menu_markup())
    else:
        await update.message.reply_text(text, reply_markup=main_menu_markup())

# ======================
# Запуск
# ======================

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(button_handler, pattern="^(add_training|stats)$")],
        states={
            SELECT_DATE: [CallbackQueryHandler(calendar_handler)],
            INPUT_KM: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_km)],
            SELECT_TIME: [CallbackQueryHandler(button_handler, pattern="^time_")],
        },
        fallbacks=[],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.LOCATION, location_handler))
    app.add_handler(conv)

    app.run_polling()


if __name__ == "__main__":
    main()

