import os
import asyncio
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler

TOKEN = os.environ.get('TELEGRAM_TOKEN')
if not TOKEN:
    raise ValueError("TELEGRAM_TOKEN не установлен!")

# Flask приложение
flask_app = Flask(__name__)

# Telegram бот
telegram_app = None

# ========== КОМАНДЫ ==========
async def start(update, context):
    keyboard = [[InlineKeyboardButton("🔘 Нажми меня", callback_data="test_callback")]]
    await update.message.reply_text(
        "👇 Нажми на кнопку:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def test_callback(update, context):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("✅ Успех! Callback обработан. Кнопки работают.")

# ========== ВЕБХУК (синхронная обработка) ==========
@flask_app.route('/webhook', methods=['POST'])
def webhook():
    global telegram_app
    try:
        data = request.get_json()
        update = Update.de_json(data, telegram_app.bot)
        
        # Обрабатываем update синхронно
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(telegram_app.process_update(update))
        
        return "OK", 200
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        return "Internal Server Error", 500

@flask_app.route('/')
def home():
    return "Бот работает", 200

# ========== ЗАПУСК ==========
def run_webhook():
    global telegram_app
    
    # Создаём приложение
    telegram_app = Application.builder().token(TOKEN).build()
    
    # Добавляем обработчики
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CallbackQueryHandler(test_callback, pattern="^test_callback$"))
    
    # Инициализируем
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(telegram_app.initialize())
    loop.run_until_complete(telegram_app.start())
    
    port = int(os.environ.get("PORT", 8080))
    print(f"✅ Тестовый бот запущен на порту {port}")
    flask_app.run(host='0.0.0.0', port=port)

if __name__ == '__main__':
    run_webhook()
