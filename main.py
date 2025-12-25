import os
import telebot
from telebot import types
from flask import Flask, request, abort
import requests
from datetime import datetime, date
import psycopg2
from dotenv import load_dotenv

load_dotenv()

# === Настройки ===
BOT_TOKEN = os.getenv('BOT_TOKEN')
WEATHER_API_KEY = os.getenv('WEATHER_API_KEY')
DATABASE_URL = os.getenv('DATABASE_URL')

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# === Подключение к БД ===
def get_db_connection():
    conn = psycopg2.connect(DATABASE_URL)
    return conn

# Инициализация БД (таблицы создаются при первом запуске)
def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            telegram_id BIGINT PRIMARY KEY,
            location TEXT NOT NULL
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS runs (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT,
            run_date DATE,
            time_range TEXT,
            distance REAL,
            comment TEXT,
            FOREIGN KEY (telegram_id) REFERENCES users (telegram_id)
        )
    ''')
    conn.commit()
    cur.close()
    conn.close()

init_db()  # Создаём таблицы при старте

# === Клавиатуры ===
def main_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("Отметить сегодняшнюю пробежку")
    markup.add("Указать другую дату пробежки")
    markup.add("Просмотреть статистику")
    markup.add("Изменить локацию")
    return markup

def time_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row("6-10", "11-15")
    markup.row("16-20", "21-24")
    return markup

# === Погода ===
def get_weather(lat, lon, date_run):
    if date_run != date.today():
        return "Историческая погода недоступна (бесплатный план)"
    
    url = f"https://api.openweathermap.org/data/2.5/weather"
    params = {
        'lat': lat,
        'lon': lon,
        'appid': WEATHER_API_KEY,
        'units': 'metric',
        'lang': 'ru'
    }
    try:
        r = requests.get(url, params=params)
        data = r.json()
        temp = data['main']['temp']
        desc = data['weather'][0]['description']
        return f"{desc}, {temp}°C"
    except:
        return "Не удалось получить погоду"

# === Команды бота ===
@bot.message_handler(commands=['start'])
def start(message):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE telegram_id = %s", (message.from_user.id,))
    user = cur.fetchone()
    conn.close()

    if user:
        bot.send_message(message.chat.id, "С возвращением! Что делаем?", reply_markup=main_menu())
    else:
        bot.send_message(message.chat.id, "Привет! Это бот-календарь лыжных пробежек 🏃‍♂️❄️\n\nПоделись своей локацией (нажми скрепку → Локация), чтобы я показывал погоду в день пробежки.")
        bot.register_next_step_handler(message, get_location)

def get_location(message):
    if message.location:
        lat = message.location.latitude
        lon = message.location.longitude
        location = f"{lat},{lon}"
    else:
        bot.send_message(message.chat.id, "Пожалуйста, отправь локацию через кнопку (скрепка → Локация).")
        bot.register_next_step_handler(message, get_location)
        return

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO users (telegram_id, location) VALUES (%s, %s) ON CONFLICT (telegram_id) DO UPDATE SET location = %s",
                (message.from_user.id, location, location))
    conn.commit()
    conn.close()

    bot.send_message(message.chat.id, "Локация сохранена! Теперь можно отмечать пробежки.", reply_markup=main_menu())

@bot.message_handler(func=lambda m: m.text == "Отметить сегодняшнюю пробежку")
def run_today(message):
    ask_time(message, date.today())

@bot.message_handler(func=lambda m: m.text == "Указать другую дату пробежки")
def run_other_date(message):
    bot.send_message(message.chat.id, "Введи дату пробежки в формате ГГГГ-ММ-ДД (например: 2025-12-24)")
    bot.register_next_step_handler(message, ask_time_by_date)

def ask_time_by_date(message):
    try:
        run_date = datetime.strptime(message.text, "%Y-%m-%d").date()
        ask_time(message, run_date)
    except:
        bot.send_message(message.chat.id, "Неверный формат даты. Попробуй ещё раз: ГГГГ-ММ-ДД")
        bot.register_next_step_handler(message, ask_time_by_date)

def ask_time(message, run_date):
    bot.send_message(message.chat.id, "В какое время была пробежка?", reply_markup=time_menu())
    bot.register_next_step_handler(message, lambda m: ask_distance(m, run_date))

def ask_distance(message, run_date):
    time_range = message.text
    if time_range not in ["6-10", "11-15", "16-20", "21-24"]:
        bot.send_message(message.chat.id, "Выбери время из кнопок ниже.", reply_markup=time_menu())
        bot.register_next_step_handler(message, lambda m: ask_distance(m, run_date))
        return

    bot.send_message(message.chat.id, "Сколько километров пробежал(а)? (например: 12.5)")
    bot.register_next_step_handler(message, lambda m: ask_comment(m, run_date, time_range))

def ask_comment(message, run_date, time_range):
    try:
        distance = float(message.text.replace(',', '.'))
    except:
        bot.send_message(message.chat.id, "Введи число (например: 10 или 8.5)")
        bot.register_next_step_handler(message, lambda m: ask_comment(m, run_date, time_range))
        return

    bot.send_message(message.chat.id, "Хочешь добавить комментарий? (или напиши /skip)")
    bot.register_next_step_handler(message, lambda m: save_run(m, run_date, time_range, distance))

def save_run(message, run_date, time_range, distance):
    comment = message.text if message.text != "/skip" else None

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO runs (telegram_id, run_date, time_range, distance, comment) VALUES (%s, %s, %s, %s, %s)",
                (message.from_user.id, run_date, time_range, distance, comment))
    conn.commit()

    # Погода
    cur.execute("SELECT location FROM users WHERE telegram_id = %s", (message.from_user.id,))
    location = cur.fetchone()[0]
    lat, lon = location.split(',')
    weather = get_weather(lat, lon, run_date)

    conn.close()

    bot.send_message(message.chat.id,
                     f"✅ Пробежка {run_date} сохранена!\n"
                     f"📏 {distance} км | ⏰ {time_range}\n"
                     f"🌤️ Погода: {weather}\n"
                     f"💬 {comment or 'Без комментария'}",
                     reply_markup=main_menu())

@bot.message_handler(func=lambda m: m.text == "Просмотреть статистику")
def stats(message):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT run_date, distance, time_range, comment FROM runs WHERE telegram_id = %s ORDER BY run_date DESC", (message.from_user.id,))
    runs = cur.fetchall()

    if not runs:
        bot.send_message(message.chat.id, "Пока нет пробежек. Отметь первую!", reply_markup=main_menu())
        conn.close()
        return

    total = sum(r[1] for r in runs)
    week = sum(r[1] for r in runs if r[0] >= date.today() - datetime.timedelta(days=7))
    month = sum(r[1] for r in runs if r[0] >= date.today() - datetime.timedelta(days=30))

    text = f"📊 Твоя статистика:\n\n"
    text += f"За неделю: {week:.1f} км\n"
    text += f"За месяц: {month:.1f} км\n"
    text += f"Всего: {total:.1f} км\n\n"
    text += "Последние пробежки:\n"

    for r in runs[:10]:  # последние 10
        text += f"• {r[0]} — {r[1]} км ({r[2]}){' | ' + r[3] if r[3] else ''}\n"

    bot.send_message(message.chat.id, text, reply_markup=main_menu())
    conn.close()

@bot.message_handler(func=lambda m: m.text == "Изменить локацию")
def change_location(message):
    bot.send_message(message.chat.id, "Поделись новой локацией (скрепка → Локация)")
    bot.register_next_step_handler(message, get_location)

# === Webhook для Render ===
@app.route('/', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return ''
    else:
        abort(403)

@app.route('/')
def index():
    return "Бот работает!"

# === Запуск ===
if __name__ == '__main__':
    import time
    # Установка webhook при запуске на Render
    bot.remove_webhook()
    time.sleep(1)
    bot.set_webhook(url=f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME')}/")

    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
