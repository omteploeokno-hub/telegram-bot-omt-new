import os
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler

TOKEN = os.environ.get('TELEGRAM_TOKEN')
if not TOKEN:
    raise ValueError("TELEGRAM_TOKEN не установлен!")

# Flask приложение
app_flask = Flask(__name__)

# Telegram бот
telegram_app = Application.builder().token(TOKEN).build()

# ========== КОМАНДЫ ==========
async def start(update, context):
    keyboard = [[InlineKeyboardButton("📋 Показать заявку", callback_data="test_callback")]]
    await update.message.reply_text(
        "Нажми на кнопку, чтобы проверить callback:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def test_callback(update, context):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("✅ Callback получен! Бот работает правильно.")

# ========== ВЕБХУК ==========
@app_flask.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        update = Update.de_json(data, telegram_app.bot)
        telegram_app.update_queue.put_nowait(update)
        return "OK", 200
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        return "Internal Server Error", 500

@app_flask.route('/')
def home():
    return "Бот работает", 200

# ========== ЗАПУСК ==========
def main():
    # Добавляем обработчики
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CallbackQueryHandler(test_callback, pattern="^test_callback$"))
    
    # Инициализируем
    telegram_app.initialize()
    telegram_app.start()
    
    port = int(os.environ.get("PORT", 8080))
    print(f"✅ Тестовый бот запущен на порту {port}")
    app_flask.run(host='0.0.0.0', port=port)

if __name__ == '__main__':
    main()
