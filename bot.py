from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, CallbackQueryHandler
import datetime

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Я твой календарь-бот. /calendar чтобы выбрать дату")

# Кастомный календарь
def build_calendar(year=None, month=None):
    if year is None:
        year = datetime.datetime.now().year
    if month is None:
        month = datetime.datetime.now().month

    keyboard = []
    # Дни недели
    week_days = ["Mo","Tu","We","Th","Fr","Sa","Su"]
    keyboard.append([InlineKeyboardButton(d, callback_data="ignore") for d in week_days])

    # Дни месяца
    first_day = datetime.datetime(year, month, 1).weekday()  # 0=Monday
    days_in_month = (datetime.date(year, month+1, 1) - datetime.timedelta(days=1)).day if month < 12 else 31

    row = []
    for _ in range(first_day):
        row.append(InlineKeyboardButton(" ", callback_data="ignore"))
    for day in range(1, days_in_month+1):
        row.append(InlineKeyboardButton(str(day), callback_data=f"day_{day}"))
        if len(row) == 7:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    return InlineKeyboardMarkup(keyboard)

async def calendar_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    markup = build_calendar()
    await update.message.reply_text("Выберите дату:", reply_markup=markup)

async def calendar_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("day_"):
        day = data.split("_")[1]
        await query.edit_message_text(text=f"Вы выбрали: {day}")
