import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, CallbackQueryHandler
from db import create_pool, add_user

import config

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await add_user(context.bot_data['pool'], user.id, user.username)
    
    keyboard = [
        [InlineKeyboardButton("Привет 🌟", callback_data="hello")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Привет! Я твой SkyCalendar Bot.", reply_markup=reply_markup)

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(text=f"Вы нажали: {query.data}")

async def main():
    app = ApplicationBuilder().token(config.TOKEN).build()
    
    # Подключаем базу данных
    pool = await create_pool()
    app.bot_data['pool'] = pool

    # Хэндлеры
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))
    
    # Запуск бота
    await app.start()
    await app.updater.start_polling()
    await app.updater.idle()

if __name__ == "__main__":
    asyncio.run(main())
