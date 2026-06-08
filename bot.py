import os
import asyncio
import json
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler
import gspread
from google.oauth2.service_account import Credentials

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get('TELEGRAM_TOKEN')
if not TOKEN:
    raise ValueError("TELEGRAM_TOKEN не установлен!")

SPREADSHEET_NAME = "Indev"
SHEET_NAME = "Сергей Олегович"

# Flask приложение
flask_app = Flask(__name__)

# Telegram бот
telegram_app = None

# ========== GOOGLE SHEETS ==========
def write_to_google_sheets():
    """Записывает число 1000 в столбец G строки 2"""
    try:
        # Получаем ключ из переменной окружения
        creds_json = os.environ.get('GOOGLE_CREDENTIALS')
        if not creds_json:
            print("❌ GOOGLE_CREDENTIALS не установлена!")
            return False
        
        creds_info = json.loads(creds_json)
        creds = Credentials.from_service_account_info(creds_info, 
            scopes=['https://www.googleapis.com/auth/spreadsheets',
                           'https://www.googleapis.com/auth/drive'])
        
        client = gspread.authorize(creds)
        sheet = client.open(SPREADSHEET_NAME).worksheet(SHEET_NAME)
        
        # Записываем 1000 в ячейку G2
        sheet.update('G2', 1000)
        print("✅ Записано 1000 в G2")
        return True
    except Exception as e:
        print(f"❌ Ошибка Google Sheets: {e}")
        return False

# ========== КОМАНДЫ ==========
async def start(update, context):
    keyboard = [[InlineKeyboardButton("🔘 Записать 1000 в таблицу", callback_data="test_callback")]]
    await update.message.reply_text(
        "👇 Нажми на кнопку, чтобы записать 1000 в Google Таблицу:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def test_callback(update, context):
    query = update.callback_query
    await query.answer()
    
    # Пишем в Google Sheets
    success = write_to_google_sheets()
    
    if success:
        await query.edit_message_text("✅ Успех! 1000 записано в Google Таблицу (строка 2, столбец G).")
    else:
        await query.edit_message_text("❌ Ошибка при записи в Google Таблицу. Проверь логи.")

# ========== ВЕБХУК ==========
@flask_app.route('/webhook', methods=['POST'])
def webhook():
    global telegram_app
    try:
        data = request.get_json()
        update = Update.de_json(data, telegram_app.bot)
        
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
    
    telegram_app = Application.builder().token(TOKEN).build()
    
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CallbackQueryHandler(test_callback, pattern="^test_callback$"))
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(telegram_app.initialize())
    loop.run_until_complete(telegram_app.start())
    
    port = int(os.environ.get("PORT", 8080))
    print(f"✅ Бот запущен на порту {port}")
    flask_app.run(host='0.0.0.0', port=port)

if __name__ == '__main__':
    run_webhook()
