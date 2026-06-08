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
telegram_app = Application.builder().token(TOKEN).build()

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

# ========== ВЕБХУК ==========
@flask_app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        update = Update.de_json(data, telegram_app.bot)
        telegram_app.update_queue.put_nowait(update)
        return "OK", 200
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        return "Internal Server Error", 500

@flask_app.route('/')
def home():
    return "Бот работает", 200

# ========== ФОНТОВЫЙ ОБРАБОТЧИК ОЧЕРЕДИ ==========
async def process_updates():
    while True:
        try:
            update = await telegram_app.update_queue.get()
            await telegram_app.process_update(update)
        except Exception as e:
            print(f"Ошибка обработки: {e}")

def run_webhook():
    # Добавляем обработчики
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CallbackQueryHandler(test_callback, pattern="^test_callback$"))
    
    # Инициализируем
    telegram_app.initialize()
    telegram_app.start()
    
    # Запускаем фоновую задачу для обработки очереди
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(process_updates())
    
    port = int(os.environ.get("PORT", 8080))
    print(f"✅ Тестовый бот запущен на порту {port}")
    flask_app.run(host='0.0.0.0', port=port)

if __name__ == '__main__':
    run_webhook()
