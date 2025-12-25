import os
import telebot
from telebot import types
from flask import Flask, request, abort
import requests
from datetime import datetime, date, timedelta
import psycopg2

# === Настройки из Environment Variables Render ===
BOT_TOKEN = os.environ['BOT_TOKEN']
WEATHER_API_KEY = os.environ['WEATHER_API_KEY']
DATABASE_URL = os.environ['DATABASE_URL']

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# === Подключение к БД ===
def get_db_connection():
    conn = psycopg2.connect(DATABASE_URL)
    return conn

# === Инициализация таблиц ===
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

init_db()  # Создаём таблицы при запуске

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
    params = {
        'lat': lat,
        'lon': lon,
        'appid': WEATHER_API_KEY,
        'units': 'metric',
        'lang': 'ru'
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            data = r.json()
            temp = data['main']['temp']
            desc = data['weather'][0]['description']
            return f"{desc}, {temp}°C"
        else:
            return "Не удалось получить погоду"
    except:
        return "Ошибка связи с погодой"

# === Обработчики ===
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
                         "Привет! Это бот для учёта лыжных (и беговых) тренировок.\n\n"
                         "Чтобы я показывал погоду в день пробежки — поделись своей локацией 📍\n"
                         "(нажми на скрепку 📎 → Локация)", 
                         reply_markup=types.ReplyKeyboardRemove())
        bot.register_next_step_handler(message, save_location)

def save_location(message):
    if not message.location:
        bot.send_message(message.chat.id, "Пожалуйста, отправь локацию через кнопку 📎 → Локация")
        bot.register_next_step_handler(message, save_location)
        return

    location = f"{message.location.latitude},{message.location.longitude}"
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO users (telegram_id, location) 
        VALUES (%s, %s) 
        ON CONFLICT (telegram_id) DO UPDATE SET location = %s
    """, (message.from_user.id, location, location))
    conn.commit()
    conn.close()

    bot.send_message(message.chat.id, "Локация сохранена! Теперь можно отмечать пробежки 🎿", reply_markup=main_menu())

@bot.message_handler(func=lambda m: m.text == "Отметить сегодняшнюю пробежку")
def run_today(message):
    process_run_date(message, date.today())

@bot.message_handler(func=lambda m: m.text == "Указать другую дату пробежки")
def run_other(message):
    msg = bot.send_message(message.chat.id, "Введите дату в формате ГГГГ-ММ-ДД (например: 2025-12-24)")
    bot.register_next_step_handler(msg, process_other_date)

def process_other_date(message):
    try:
        run_date = datetime.strptime(message.text.strip(), "%Y-%m-%d").date()
        process_run_date(message, run_date)
    except:
        msg = bot.send_message(message.chat.id, "Неверный формат. Попробуй ещё: ГГГГ-ММ-ДД")
        bot.register_next_step_handler(msg, process_other_date)

def process_run_date(message, run_date):
    msg = bot.send_message(message.chat.id, f"Пробежка {run_date}\nВ какое время?", reply_markup=time_menu())
    bot.register_next_step_handler(msg, lambda m: process_time(m, run_date))

def process_time(message, run_date):
    if message.text not in ["6-10", "11-15", "16-20", "21-24"]:
        msg = bot.send_message(message.chat.id, "Выбери из кнопок:", reply_markup=time_menu())
        bot.register_next_step_handler(msg, lambda m: process_time(m, run_date))
        return
    
    bot.register_next_step_handler(message, lambda m: process_distance(m, run_date, message.text))

def process_distance(message, run_date, time_range):
    try:
        distance = float(message.text.replace(',', '.'))
        if distance <= 0:
            raise ValueError
    except:
        msg = bot.send_message(message.chat.id, "Введи положительное число километров (например: 15 или 8.5)")
        bot.register_next_step_handler(msg, lambda m: process_distance(m, run_date, time_range))
        return

    msg = bot.send_message(message.chat.id, "Комментарий к пробежке? (или напиши /skip)")
    bot.register_next_step_handler(msg, lambda m: save_run(m, run_date, time_range, distance))

def save_run(message, run_date, time_range, distance):
    comment = message.text if message.text != "/skip" else None

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT location FROM users WHERE telegram_id = %s", (message.from_user.id,))
    loc = cur.fetchone()[0]
    lat, lon = loc.split(',')
    weather = get_weather(lat, lon) if run_date == date.today() else "История недоступна"

    cur.execute("""
        INSERT INTO runs (telegram_id, run_date, time_range, distance, comment)
        VALUES (%s, %s, %s, %s, %s)
    """, (message.from_user.id, run_date, time_range, distance, comment))
    conn.commit()
    conn.close()

    bot.send_message(message.chat.id,
                     f"✅ Пробежка сохранена!\n"
                     f"📅 {run_date} | ⏰ {time_range}\n"
                     f"📏 {distance} км\n"
                     f"🌤️ {weather}\n"
                     f"💬 {comment or '—'}",
                     reply_markup=main_menu())

@bot.message_handler(func=lambda m: m.text == "Просмотреть статистику")
def show_stats(message):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT run_date, distance, time_range, comment 
        FROM runs 
        WHERE telegram_id = %s 
        ORDER BY run_date DESC
    """, (message.from_user.id,))
    runs = cur.fetchall()
    conn.close()

    if not runs:
        bot.send_message(message.chat.id, "Нет записей. Отметь первую пробежку!", reply_markup=main_menu())
        return

    total = sum(r[1] for r in runs)
    week = sum(r[1] for r in runs if r[0] >= date.today() - timedelta(days=7))
    month = sum(r[1] for r in runs if r[0] >= date.today() - timedelta(days=30))

    text = f"📊 Статистика:\n\n"
    text += f"За неделю: {week:.1f} км\n"
    text += f"За месяц: {month:.1f} км\n"
    text += f"Всего: {total:.1f} км\n\n"
    text += "Последние пробежки:\n"

    for r in runs[:15]:
        comment = f" | {r[3]}" if r[3] else ""
        text += f"• {r[0]} — {r[1]} км ({r[2]}){comment}\n"

    bot.send_message(message.chat.id, text, reply_markup=main_menu())

@bot.message_handler(func=lambda m: m.text == "Изменить локацию")
def change_loc(message):
    bot.send_message(message.chat.id, "Отправь новую локацию 📍", reply_markup=types.ReplyKeyboardRemove())
    bot.register_next_step_handler(message, save_location)

# === Webhook ===
@app.route('/', methods=['GET', 'POST'])
def webhook():
    if request.method == 'POST':
        if request.headers.get('content-type') == 'application/json':
            update = telebot.types.Update.de_json(request.get_data().as_text())
            bot.process_new_updates([update])
            return '', 200
        else:
            abort(403)
    else:
        return "Бот работает! 🎿"

# === Запуск на Render ===
if __name__ == '__main__':
    import time
    bot.remove_webhook()
    time.sleep(1)
    webhook_url = f"https://{os.environ.get('RENDER_EXTERNAL_HOSTNAME')}/"
    bot.set_webhook(url=webhook_url)
    # Render использует gunicorn
    # Но если запуск вручную — порт
    port = int(os.environ.get('PORT', 5000))
    from gunicorn.app.wsgiapp import run
    # В реальности Render запускает через gunicorn автоматически
    app.run(host='0.0.0.0', port=port)
