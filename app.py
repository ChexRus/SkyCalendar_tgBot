# Функция установки webhook
async def _set_webhook_async():
    url = f"https://{os.environ.get('RENDER_EXTERNAL_HOSTNAME')}/{os.environ['BOT_TOKEN']}"
    try:
        await application.bot.set_webhook(url=url)
        logger.info(f"Webhook успешно установлен: {url}")
        return "Webhook установлен успешно! ✅ Теперь бот работает."
    except Exception as e:
        logger.error(f"Ошибка при установке webhook: {e}")
        return f"Ошибка: {str(e)}"

# Роут для установки webhook (синхронный, безопасный для Gunicorn)
@app.route("/set-webhook")
def set_webhook():
    import asyncio
    loop = asyncio.get_event_loop_policy().new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        result = loop.run_until_complete(_set_webhook_async())
    finally:
        # НЕ закрываем loop — это важно!
        pass
    return result

# Главная страница
@app.route("/")
def index():
    return """
    <h2>🚀 SkiCalendarBot работает!</h2>
    <p>Бот успешно запущен на Render.</p>
    <p><strong>Важно:</strong> после каждого нового деплоя (обновления кода) нужно один раз установить webhook.</p>
    <a href="/set-webhook">
        <button style="font-size:20px; padding:15px 30px; background:#00aa00; color:white; border:none; border-radius:10px; cursor:pointer;">
            Установить webhook сейчас
        </button>
    </a>
    <hr>
    <p>После нажатия кнопки бот начнёт получать сообщения.</p>
    <p>Текущий статус: если кнопка показывает успех — всё готово!</p>
    """
