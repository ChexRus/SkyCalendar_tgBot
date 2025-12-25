import os
import telebot
from telebot import types
from flask import Flask, request
import psycopg
from psycopg.rows import dict_row

# Настройки
BOT_TOKEN = os.environ['BOT_TOKEN']
DATABASE_URL = os.environ['DATABASE_URL']

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# Подключение к базе
def get_db_connection():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)

# Инициализация таблиц
def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            telegram_id BIGINT PRIMARY KEY,
            location TEXT,
            state TEXT
        )
    ''')
    conn.commit()
    cur.close()
    conn.close()

init_db()

# Клавиатура
def main_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("Отправить локацию")
    return markup

# Сохраняем состояние пользователя
def set_user_state(user_id, state, location=None):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO users (telegram_id, state, location)
        VALUES (%s, %s, %s)
        ON CONFLICT (telegram_id) DO UPDATE SET state = EXCLUDED.state, location = COALESCE(EXCLUDED.location, users.location)
    """, (user_id, state, location))
    conn.commit()
    conn.close()

def get_user_state(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE telegram_id = %s", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row

# Обработчик сообщений
@bot.message_handler(func=lambda m: True)
def handle_all(message):
    user_id = message.from_user.id
    state = get_user_state(user_id)

    # Команда /start
    if message.text == '/start':
        bot.send_message(message.chat.id, "Привет! Бот работает! 🎿\nОтправьте локацию для начала.", reply_markup=main_menu())
        set_user_state(user_id, 'waiting_location')
        return

    # Если ждем локацию
    if state and state['state'] == 'waiting_location':
        if message.location:
            location = f"{message.location.latitude},{message.location.longitude}"
            set_user_state(user_id, None, location=location)
            bot.send_message(message.chat.id, f"Локация сохранена: {location}", reply_markup=main_menu())
        else:
            bot.send_message(message.chat.id, "Пожалуйста, отправьте локацию через скрепку 📎 → Локация")
        return

    # Любое другое сообщение
    bot.send_message(message.chat.id, "Используйте меню или отправьте /start.", reply_markup=main_menu())

# Webhook
@app.route('/' + BOT_TOKEN, methods=['POST'])
def webhook():
    json_string = request.get_data(as_text=True)
    update = telebot.types.Update.de_json(json_string)
    bot.process_new_updates([update])
    return '', 200

@app.route('/', methods=['GET'])
def index():
    return "Бот работает! 🎿", 200

# Запуск на Render
if __name__ == '__main__':
    import time
    bot.remove_webhook()
    time.sleep(1)
    webhook_url = f"https://{os.environ.get('RENDER_EXTERNAL_HOSTNAME')}/{BOT_TOKEN}"
    bot.set_webhook(url=webhook_url)
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
