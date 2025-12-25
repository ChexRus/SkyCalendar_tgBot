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
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Токен бота
BOT_TOKEN = os.environ["BOT_TOKEN"]

# ======================
# Обработчики бота
# ======================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
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
    """Обработчик геолокации"""
    user_id = update.effective_user.id
    lat = update.message.location.latitude
    lon = update.message.location.longitude
    user_locations[user_id] = (lat, lon)
    await update.message.reply_text("Локация сохранена! Теперь буду показывать температуру 🌡️")

async def get_temperature(lat: float, lon: float) -> float | None:
    """Получение температуры по координатам"""
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m"
        response = requests.get(url, timeout=10).json()
        return response["current"]["temperature_2m"]
    except Exception as e:
        logger.error(f"Error getting temperature: {e}")
        return None

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопок"""
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
    """Обработчик календаря"""
    query = update.callback_query
    result, key, step = DetailedTelegramCalendar(min_date=datetime.date(2020, 1, 1)).process(query.data)
    if not result and key:
        await query.edit_message_text(f"Выбери {LSTEP[step]}", reply_markup=key)
    elif result:
        context.user_data["selected_date"] = result
        await query.edit_message_text(f"Выбрана дата: {result}\nВведи пройденные километры (например, 15.5):")
        return INPUT_KM

async def input_km(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ввода километров"""
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
    """Показать статистику"""
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
    """Клавиатура главного меню"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Добавить тренировку", callback_data="add_training")],
        [InlineKeyboardButton("Статистика", callback_data="stats")],
    ])

# ======================
# Создание и настройка Application
# ======================

def create_application():
    """Создает Application с обработчиками"""
    # Создаем Application
    app_instance = Application.builder().token(BOT_TOKEN).build()
    
    # Регистрация ConversationHandler
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(button_handler, pattern="^(add_training|stats)$")],
        states={
            SELECT_DATE: [CallbackQueryHandler(calendar_handler)],
            INPUT_KM: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_km)],
            SELECT_TIME: [CallbackQueryHandler(button_handler, pattern="^time_")],
        },
        fallbacks=[],
        allow_reentry=True,
        per_message=True,  # Добавляем для отслеживания сообщений
    )
    
    # Регистрируем все обработчики
    app_instance.add_handler(CommandHandler("start", start))
    app_instance.add_handler(MessageHandler(filters.LOCATION, location_handler))
    app_instance.add_handler(conv_handler)
    
    return app_instance

# ======================
# Обработка обновлений
# ======================

async def process_update_async(update_data: dict):
    """Асинхронная обработка одного обновления"""
    try:
        # Создаем новое Application для каждого обновления
        application = create_application()
        
        # Создаем объект Update из JSON данных
        bot = application.bot
        update = Update.de_json(update_data, bot)
        
        # Инициализируем application
        await application.initialize()
        
        # Обрабатываем обновление
        await application.process_update(update)
        
        # Завершаем работу application
        await application.shutdown()
        
        logger.info(f"Successfully processed update {update.update_id}")
        
    except Exception as e:
        logger.error(f"Error processing update: {e}")
        raise

# ======================
# Flask роуты
# ======================

@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    """Обработчик вебхука от Telegram"""
    if request.headers.get("content-type") != "application/json":
        abort(403)
    
    # Получаем JSON данные
    json_data = request.get_json(force=True)
    
    # Создаем новый event loop для этого запроса
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        # Асинхронно обрабатываем обновление
        loop.run_until_complete(process_update_async(json_data))
        return "OK", 200
    except Exception as e:
        logger.error(f"Failed to process update: {e}")
        return "Internal Server Error", 500
    finally:
        loop.close()

async def set_webhook_async():
    """Асинхронная установка webhook"""
    try:
        # Создаем временное Application только для установки webhook
        temp_app = Application.builder().token(BOT_TOKEN).build()
        await temp_app.initialize()
        
        # Формируем URL для webhook
        hostname = os.environ.get('RENDER_EXTERNAL_HOSTNAME')
        if not hostname:
            raise ValueError("RENDER_EXTERNAL_HOSTNAME not set")
        
        url = f"https://{hostname}/{BOT_TOKEN}"
        
        # Устанавливаем webhook
        await temp_app.bot.set_webhook(url=url)
        
        # Завершаем работу временного приложения
        await temp_app.shutdown()
        
        logger.info(f"Webhook установлен: {url}")
        return True, url
    except Exception as e:
        logger.error(f"Ошибка установки webhook: {e}")
        return False, str(e)

@app.route("/set-webhook")
def set_webhook():
    """Страница для установки webhook"""
    # Создаем новый event loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        # Выполняем асинхронную установку webhook
        success, result = loop.run_until_complete(set_webhook_async())
        
        if success:
            return f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>✅ Webhook установлен</title>
                <style>
                    body {{
                        font-family: Arial, sans-serif;
                        max-width: 800px;
                        margin: 0 auto;
                        padding: 40px;
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
                        text-align: center;
                    }}
                    h1 {{
                        font-size: 2.5em;
                        margin-bottom: 20px;
                    }}
                    .success {{
                        font-size: 4em;
                        margin: 20px 0;
                    }}
                    .url-box {{
                        background: rgba(255, 255, 255, 0.2);
                        padding: 15px;
                        border-radius: 10px;
                        margin: 20px 0;
                        font-family: monospace;
                        word-break: break-all;
                        text-align: left;
                    }}
                    .button {{
                        display: inline-block;
                        background: #00aa00;
                        color: white;
                        padding: 15px 30px;
                        text-decoration: none;
                        border-radius: 10px;
                        font-size: 18px;
                        font-weight: bold;
                        margin: 20px 10px;
                        transition: all 0.3s;
                    }}
                    .button:hover {{
                        background: #00cc00;
                        transform: translateY(-2px);
                    }}
                    .telegram-btn {{
                        background: #0088cc;
                    }}
                    .telegram-btn:hover {{
                        background: #006699;
                    }}
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="success">✅</div>
                    <h1>Webhook установлен успешно!</h1>
                    <p>Теперь ваш бот готов принимать сообщения через Telegram.</p>
                    
                    <div class="url-box">
                        <strong>Webhook URL:</strong><br>
                        {result}
                    </div>
                    
                    <p>Для проверки можно перейти по ссылке:</p>
                    <p>
                        <a href="https://api.telegram.org/bot{BOT_TOKEN}/getWebhookInfo" 
                           target="_blank" 
                           class="button">
                            Проверить статус Webhook
                        </a>
                    </p>
                    
                    <p>Теперь перейдите в Telegram и начните общение с ботом:</p>
                    <p>
                        <a href="https://t.me/skicalendar_bot" 
                           target="_blank" 
                           class="button telegram-btn">
                            Открыть в Telegram
                        </a>
                    </p>
                    
                    <p><a href="/" class="button">Вернуться на главную</a></p>
                </div>
            </body>
            </html>
            """
        else:
            return f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>❌ Ошибка</title>
                <style>
                    body {{
                        font-family: Arial, sans-serif;
                        max-width: 800px;
                        margin: 0 auto;
                        padding: 40px;
                        background: linear-gradient(135deg, #ff6b6b 0%, #ffa8a8 100%);
                        color: white;
                        min-height: 100vh;
                    }}
                    .container {{
                        background: rgba(255, 255, 255, 0.1);
                        backdrop-filter: blur(10px);
                        border-radius: 20px;
                        padding: 40px;
                        box-shadow: 0 8px 32px rgba(0, 0, 0, 0.1);
                        text-align: center;
                    }}
                    h1 {{
                        font-size: 2.5em;
                        margin-bottom: 20px;
                    }}
                    .error {{
                        font-size: 4em;
                        margin: 20px 0;
                    }}
                    .error-box {{
                        background: rgba(255, 255, 255, 0.2);
                        padding: 20px;
                        border-radius: 10px;
                        margin: 20px 0;
                        text-align: left;
                        font-family: monospace;
                    }}
                    .button {{
                        display: inline-block;
                        background: #dc3545;
                        color: white;
                        padding: 15px 30px;
                        text-decoration: none;
                        border-radius: 10px;
                        font-size: 18px;
                        font-weight: bold;
                        margin: 20px 0;
                        transition: all 0.3s;
                    }}
                    .button:hover {{
                        background: #c82333;
                        transform: translateY(-2px);
                    }}
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="error">❌</div>
                    <h1>Ошибка установки Webhook</h1>
                    
                    <div class="error-box">
                        <strong>Ошибка:</strong><br>
                        {result}
                    </div>
                    
                    <p>Попробуйте:</p>
                    <ol style="text-align: left; max-width: 500px; margin: 0 auto;">
                        <li>Проверить, что переменная окружения BOT_TOKEN установлена</li>
                        <li>Проверить, что Render установил RENDER_EXTERNAL_HOSTNAME</li>
                        <li>Подождать 1-2 минуты после деплоя</li>
                        <li>Попробовать еще раз</li>
                    </ol>
                    
                    <p><a href="/" class="button">Вернуться на главную</a></p>
                </div>
            </body>
            </html>
            """
    finally:
        loop.close()

@app.route("/")
def index():
    """Главная страница"""
    webhook_url = f"/{BOT_TOKEN}"
    full_url = f"https://{os.environ.get('RENDER_EXTERNAL_HOSTNAME', 'ваш-домен.onrender.com')}{webhook_url}"
    
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>🏂 SkiCalendarBot</title>
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
                max-width: 1000px;
                margin: 0 auto;
                padding: 20px;
                line-height: 1.6;
                color: #333;
                background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
                min-height: 100vh;
            }}
            .container {{
                background: white;
                border-radius: 20px;
                padding: 50px;
                box-shadow: 0 15px 35px rgba(0,0,0,0.1);
                margin-top: 20px;
            }}
            header {{
                text-align: center;
                margin-bottom: 40px;
            }}
            h1 {{
                color: #2c3e50;
                font-size: 3em;
                margin-bottom: 10px;
            }}
            .subtitle {{
                color: #7f8c8d;
                font-size: 1.2em;
                margin-bottom: 30px;
            }}
            .emoji {{
                font-size: 4em;
                margin: 20px 0;
            }}
            .step {{
                display: flex;
                align-items: flex-start;
                margin: 30px 0;
                padding: 25px;
                background: #f8f9fa;
                border-radius: 15px;
                border-left: 5px solid #3498db;
            }}
            .step-number {{
                display: flex;
                align-items: center;
                justify-content: center;
                background: #3498db;
                color: white;
                width: 40px;
                height: 40px;
                border-radius: 50%;
                font-size: 1.5em;
                font-weight: bold;
                margin-right: 20px;
                flex-shrink: 0;
            }}
            .step-content {{
                flex-grow: 1;
            }}
            .step-title {{
                color: #2c3e50;
                font-size: 1.5em;
                margin-bottom: 10px;
            }}
            .button {{
                display: inline-block;
                background: #2ecc71;
                color: white;
                padding: 18px 35px;
                text-decoration: none;
                border-radius: 10px;
                font-size: 1.2em;
                font-weight: bold;
                margin: 10px 5px;
                transition: all 0.3s;
                border: none;
                cursor: pointer;
            }}
            .button:hover {{
                background: #27ae60;
                transform: translateY(-3px);
                box-shadow: 0 7px 20px rgba(46, 204, 113, 0.3);
            }}
            .button.secondary {{
                background: #3498db;
            }}
            .button.secondary:hover {{
                background: #2980b9;
            }}
            .code {{
                background: #2c3e50;
                color: #ecf0f1;
                padding: 18px;
                border-radius: 10px;
                font-family: 'Courier New', monospace;
                margin: 15px 0;
                overflow-x: auto;
                font-size: 1em;
            }}
            .info-box {{
                background: #e8f4fc;
                border: 2px solid #3498db;
                border-radius: 10px;
                padding: 20px;
                margin: 30px 0;
            }}
            .warning {{
                background: #fff3cd;
                border: 2px solid #ffc107;
                border-radius: 10px;
                padding: 20px;
                margin: 30px 0;
            }}
            .success {{
                background: #d4edda;
                border: 2px solid #28a745;
                border-radius: 10px;
                padding: 20px;
                margin: 30px 0;
            }}
            .url-display {{
                background: #f8f9fa;
                border: 2px dashed #6c757d;
                border-radius: 10px;
                padding: 15px;
                margin: 15px 0;
                word-break: break-all;
                font-family: monospace;
            }}
            .stats {{
                display: flex;
                justify-content: space-around;
                margin: 40px 0;
                text-align: center;
            }}
            .stat {{
                padding: 20px;
            }}
            .stat-number {{
                font-size: 2.5em;
                font-weight: bold;
                color: #3498db;
            }}
            .stat-label {{
                color: #7f8c8d;
                margin-top: 5px;
            }}
            footer {{
                text-align: center;
                margin-top: 50px;
                color: #7f8c8d;
                font-size: 0.9em;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <header>
                <div class="emoji">🏂❄️</div>
                <h1>SkiCalendarBot</h1>
                <div class="subtitle">Отслеживай свои лыжные тренировки с температурой в реальном времени</div>
            </header>
            
            <div class="info-box">
                <h3>📋 Краткая информация</h3>
                <p>Этот бот поможет вам вести дневник лыжных тренировок, отслеживать пройденные километры и температуру во время тренировок.</p>
            </div>
            
            <div class="stats">
                <div class="stat">
                    <div class="stat-number">{len(user_data_storage)}</div>
                    <div class="stat-label">Пользователей</div>
                </div>
                <div class="stat">
                    <div class="stat-number">{sum(len(trainings) for trainings in user_data_storage.values())}</div>
                    <div class="stat-label">Тренировок</div>
                </div>
                <div class="stat">
                    <div class="stat-number">{len(user_locations)}</div>
                    <div class="stat-label">Локаций</div>
                </div>
            </div>
            
            <h2>🚀 Начало работы</h2>
            
            <div class="step">
                <div class="step-number">1</div>
                <div class="step-content">
                    <div class="step-title">Установите Webhook</div>
                    <p>Нажмите кнопку ниже, чтобы установить связь между Telegram и вашим ботом на Render.</p>
                    <p><strong>Важно:</strong> Делайте это после каждого деплоя или перезапуска приложения.</p>
                    <a href="/set-webhook" class="button">Установить Webhook</a>
                </div>
            </div>
            
            <div class="step">
                <div class="step-number">2</div>
                <div class="step-content">
                    <div class="step-title">Проверьте Webhook URL</div>
                    <p>После установки ваш Webhook URL будет:</p>
                    <div class="url-display">{full_url}</div>
                    <p>Вы можете проверить статус Webhook:</p>
                    <a href="https://api.telegram.org/bot{BOT_TOKEN}/getWebhookInfo" 
                       target="_blank" 
                       class="button secondary">
                        Проверить статус
                    </a>
                </div>
            </div>
            
            <div class="step">
                <div class="step-number">3</div>
                <div class="step-content">
                    <div class="step-title">Начните общение с ботом</div>
                    <p>Откройте Telegram и напишите боту команду:</p>
                    <div class="code">/start</div>
                    <p>Или перейдите по прямой ссылке:</p>
                    <a href="https://t.me/skicalendar_bot" 
                       target="_blank" 
                       class="button">
                        Открыть в Telegram
                    </a>
                </div>
            </div>
            
            <div class="warning">
                <h3>⚠️ Важные замечания</h3>
                <ul>
                    <li>Данные хранятся в памяти и сбрасываются при перезапуске приложения</li>
                    <li>Для продакшена рекомендуется использовать базу данных (Redis/PostgreSQL)</li>
                    <li>Приложение автоматически перезапускается каждые 24 часа на бесплатном плане Render</li>
                </ul>
            </div>
            
            <div class="success">
                <h3>✅ Бот готов к работе!</h3>
                <p>После выполнения всех шагов вы можете:</p>
                <ul>
                    <li>Добавлять тренировки с датой и временем</li>
                    <li>Делиться локацией для получения температуры</li>
                    <li>Смотреть статистику по пробегам</li>
                    <li>Отслеживать прогресс за месяц</li>
                </ul>
            </div>
            
            <div class="info-box">
                <h3>🔧 Техническая информация</h3>
                <p><strong>Сервер:</strong> Render.com</p>
                <p><strong>Порт:</strong> {os.environ.get('PORT', '5000')}</p>
                <p><strong>Домен:</strong> {os.environ.get('RENDER_EXTERNAL_HOSTNAME', 'Не задан')}</p>
                <p><strong>Статус приложения:</strong> <a href="/health" style="color: #3498db;">Проверить здоровье</a></p>
            </div>
            
            <footer>
                <p>SkiCalendarBot • Сделано с ❤️ для лыжников и сноубордистов</p>
                <p>При возникновении проблем проверьте логи в панели Render</p>
            </footer>
        </div>
    </body>
    </html>
    """

@app.route("/health")
def health():
    """Проверка здоровья приложения"""
    return {
        "status": "healthy",
        "service": "SkiCalendarBot",
        "timestamp": datetime.datetime.now().isoformat(),
        "users_count": len(user_data_storage),
        "trainings_count": sum(len(trainings) for trainings in user_data_storage.values()),
        "locations_count": len(user_locations),
        "memory_usage": "in-memory storage"
    }, 200

# ======================
# Запуск приложения
# ======================

if __name__ == "__main__":
    # Получаем порт из переменных окружения (для Render)
    port = int(os.environ.get("PORT", 5000))
    
    logger.info(f"Starting SkiCalendarBot on port {port}")
    logger.info(f"Bot token: ••••••••••{BOT_TOKEN[-10:]}")
    logger.info(f"Webhook URL: https://{os.environ.get('RENDER_EXTERNAL_HOSTNAME', 'localhost')}:{port}/{BOT_TOKEN}")
    
    # Запускаем Flask приложение
    app.run(host="0.0.0.0", port=port)
