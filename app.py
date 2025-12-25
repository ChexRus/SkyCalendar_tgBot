import datetime
import logging
import os
import asyncio
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

# Глобальные переменные для управления состоянием бота
application = None
application_lock = asyncio.Lock()
is_initialized = False

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

# ======================
# Инициализация бота
# ======================

def init_bot():
    """Инициализация бота (синхронная функция)"""
    global application, is_initialized
    
    if is_initialized:
        return application
    
    # Создаем Application
    application = Application.builder().token(os.environ["BOT_TOKEN"]).build()
    
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
    
    is_initialized = True
    logger.info("Bot application initialized successfully")
    
    return application

async def process_update_async(update: Update):
    """Асинхронная обработка обновления"""
    global application, is_initialized, application_lock
    
    async with application_lock:
        if not is_initialized:
            init_bot()
        
        if not application:
            logger.error("Application not initialized")
            return
        
        try:
            # Инициализируем application если еще не инициализирован
            if not hasattr(application, '_initialized') or not application._initialized:
                await application.initialize()
                logger.info("Application initialized for processing update")
            
            # Обрабатываем обновление
            await application.process_update(update)
            logger.info(f"Update processed: {update.update_id}")
            
        except Exception as e:
            logger.error(f"Error processing update: {e}")
            raise

# ======================
# Flask роуты
# ======================

@app.route(f"/{os.environ['BOT_TOKEN']}", methods=["POST"])
def webhook():
    """Обработчик вебхука"""
    if request.headers.get("content-type") != "application/json":
        abort(403)
    
    json_data = request.get_json(force=True)
    
    # Создаем update
    update = Update.de_json(json_data, init_bot().bot)
    
    try:
        # Обрабатываем update в новом event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(process_update_async(update))
        loop.close()
        
    except Exception as e:
        logger.error(f"Failed to process update: {e}")
        return "Internal Server Error", 500
    
    return "OK", 200

# Установка webhook вручную
async def _set_webhook_async():
    """Асинхронная установка вебхука"""
    init_bot()  # Инициализируем бота
    
    url = f"https://{os.environ.get('RENDER_EXTERNAL_HOSTNAME')}/{os.environ['BOT_TOKEN']}"
    try:
        await application.bot.set_webhook(url=url)
        logger.info(f"Webhook успешно установлен: {url}")
        return f"Webhook установлен успешно! ✅<br>URL: {url}<br><br>Теперь бот полностью работает."
    except Exception as e:
        logger.error(f"Ошибка установки webhook: {e}")
        return f"Ошибка: {str(e)}"

@app.route("/set-webhook")
def set_webhook():
    """Установка вебхука"""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(_set_webhook_async())
        loop.close()
        return result
    except Exception as e:
        return f"Ошибка: {str(e)}"

@app.route("/health")
def health():
    """Проверка здоровья приложения"""
    return "OK", 200

# Главная страница
@app.route("/")
def index():
    hostname = os.environ.get('RENDER_EXTERNAL_HOSTNAME', 'localhost:5000')
    webhook_url = f"https://{hostname}/{os.environ['BOT_TOKEN']}"
    
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>🏂 SkiCalendarBot</title>
        <style>
            body {{
                font-family: Arial, sans-serif;
                max-width: 800px;
                margin: 0 auto;
                padding: 20px;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                min-height: 100vh;
            }}
            .container {{
                background: rgba(255, 255, 255, 0.1);
                backdrop-filter: blur(10px);
                border-radius: 20px;
                padding: 40px;
                box-shadow: 0 8px 32px rgba(0, 0, 0, 0.1);
            }}
            h1 {{
                text-align: center;
                margin-bottom: 30px;
                font-size: 2.5em;
            }}
            .status {{
                background: rgba(255, 255, 255, 0.2);
                padding: 15px;
                border-radius: 10px;
                margin: 20px 0;
            }}
            .button {{
                display: inline-block;
                background: #00aa00;
                color: white;
                padding: 15px 30px;
                text-decoration: none;
                border-radius: 10px;
                font-size: 20px;
                font-weight: bold;
                margin: 10px 0;
                transition: all 0.3s;
                border: none;
                cursor: pointer;
            }}
            .button:hover {{
                background: #00cc00;
                transform: translateY(-2px);
                box-shadow: 0 5px 15px rgba(0, 170, 0, 0.4);
            }}
            .info {{
                background: rgba(255, 255, 255, 0.1);
                padding: 20px;
                border-radius: 10px;
                margin: 20px 0;
                font-family: monospace;
                word-break: break-all;
            }}
            .step {{
                margin: 25px 0;
                padding-left: 20px;
                border-left: 3px solid #00aa00;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🏂 SkiCalendarBot — всё готово!</h1>
            
            <div class="status">
                <p>✅ Бот запущен и готов к работе</p>
                <p>📍 Текущий сервер: {hostname}</p>
                <p>🤖 Токен бота: ••••••••••{os.environ['BOT_TOKEN'][-10:]}</p>
            </div>
            
            <div class="step">
                <h3>📝 Шаг 1: Установите webhook</h3>
                <p>Нажмите кнопку ниже для установки webhook:</p>
                <a href="/set-webhook" class="button">Установить webhook</a>
            </div>
            
            <div class="step">
                <h3>🔗 Шаг 2: Проверьте webhook URL</h3>
                <p>Ваш webhook URL:</p>
                <div class="info">{webhook_url}</div>
                <p>Для проверки можно использовать <a href="https://api.telegram.org/bot{os.environ['BOT_TOKEN']}/getWebhookInfo" style="color: #00ff00;">getWebhookInfo</a></p>
            </div>
            
            <div class="step">
                <h3>🚀 Шаг 3: Начните работу с ботом</h3>
                <p>Перейдите в Telegram и напишите боту команду:</p>
                <div class="info">/start</div>
            </div>
            
            <div class="step">
                <h3>🛠 Техническая информация</h3>
                <p><a href="/health" style="color: #00ff00;">Проверить здоровье приложения</a></p>
                <p><small>Приложение работает на Render.com с использованием Flask + python-telegram-bot</small></p>
            </div>
        </div>
    </body>
    </html>
    """

# Инициализируем бота при импорте
init_bot()
logger.info("SkiCalendarBot initialized successfully")

if __name__ == "__main__":
    # Для локального теста
    app.run(host="0.0.0.0", port=5000, debug=True)
