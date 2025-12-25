import os
import telebot
from telebot import types
from flask import Flask, request, abort
import requests
from datetime import datetime, date, timedelta
import psycopg
from psycopg.rows import dict_row
import time

# === Настройки из Environment Variables на Render ===
BOT_TOKEN = os.environ['BOT_TOKEN']
WEATHER_API_KEY = os.environ['WEATHER_API_KEY']
DATABASE_URL = os.environ['DATABASE_URL']

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# === База данных ===
def get_db_connection():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)

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
            telegram_id BIGINT REFERENCES users(telegram_id),
            run_date DATE NOT NULL,
            time_range TEXT,
            distance REAL,
            comment TEXT
        )
    ''')
    conn.commit()
    cur.close()
    conn.close()

init_db()

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
def get_weather(lat, lon):
    url = "https://api.openweathermap.org/data/2.5/weather"
    params = {'lat': lat, 'lon': lon, 'appid': WEATHER_API_KEY, 'units': 'metric', 'lang': 'ru'}
    try:
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            data = r.json()
            return f"{data['weather'][0]['description']}, {data['main']['temp']}°C"
        return "Не удалось получить погоду"
    except Exception as e:
        print(f"Ошибка погоды: {e}")
        return "Ошибка связи с сервисом погоды"

# === Обработчики бота ===
@bot.message_handler(commands=['start'])
def start(message):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT location FROM users WHERE telegram_id = %s", (message.from_user.id,))
    user = cur.fetchone()
    conn.close()
    if user:
        bot.send_message(message.chat.id, "С возвращением, лыжник! ❄️🏃‍♂️\nЧто будем делать?", reply_markup=main_menu())
    else:
        bot.send_message(message.chat.id,
                         "Привет! Это бот-календарь лыжных и беговых тренировок 🎿\n\n"
                         "Чтобы показывать погоду в день пробежки — поделись своей локацией 📍",
                         reply_markup=types.ReplyKeyboardRemove())
        bot.register_next_step_handler(message, save_location)

def save_location(message):
    if not message.location:
        bot.send_message(message.chat.id, "Пожалуйста, отправь локацию через скрепку 📎 → Локация")
        bot.register_next_step_handler(message, save_location)
        return
    location = f"{message.location.latitude},{message.location.longitude}"
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO users (telegram_id, location)
        VALUES (%s, %s)
        ON CONFLICT (telegram_id) DO UPDATE SET location = EXCLUDED.location
    """, (message.from_user.id, location))
    conn.commit()
    conn.close()
    bot.send_message(message.chat.id, "Локация сохранена! Теперь можно отмечать пробежки 🎉", reply_markup=main_menu())

# === Webhook ===
@app.route('/' + BOT_TOKEN, methods=['POST'])
def webhook():
    json_string = request.get_data(as_text=True)
    update = telebot.types.Update.de_json(json_string)
    bot.process_new_updates([update])
    return '', 200

@app.route('/', methods=['GET'])
def index():
    return "Бот работает! 🎿", 200

# === Запуск на Render ===
if __name__ == '__main__':
    print("Удаляем старый webhook...")
    bot.remove_webhook()
    time.sleep(1)
    webhook_url = f"https://{os.environ.get('RENDER_EXTERNAL_HOSTNAME')}/{BOT_TOKEN}"
    print(f"Устанавливаем webhook на: {webhook_url}")
    bot.set_webhook(url=webhook_url)
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
