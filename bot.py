import os
import json
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler
import gspread
from google.oauth2.service_account import Credentials

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get('TELEGRAM_TOKEN')
if not TOKEN:
    raise ValueError("TELEGRAM_TOKEN не установлен!")

SPREADSHEET_NAME = "Indev"
SHEET_NAME = "Сергей Олегович"

flask_app = Flask(__name__)
updater = None

# ========== GOOGLE SHEETS ==========
def get_worksheet():
    creds_json = os.environ.get('GOOGLE_CREDENTIALS')
    if not creds_json:
        raise Exception("GOOGLE_CREDENTIALS не установлена!")
    creds_info = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_info, 
        scopes=['https://www.googleapis.com/auth/spreadsheets',
                'https://www.googleapis.com/auth/drive'])
    client = gspread.authorize(creds)
    return client.open(SPREADSHEET_NAME).worksheet(SHEET_NAME)

def get_available_orders():
    """Возвращает список заявок со статусом 'В работе'"""
    sheet = get_worksheet()
    records = sheet.get_all_records()
    orders = []
    for idx, row in enumerate(records, start=2):
        if row.get('Статус заявки') == 'В работе':
            orders.append({
                'row': idx,
                'id': row.get('ID заявки', ''),
                'client': row.get('Клиент', ''),
                'address': row.get('Адрес', '')
            })
    return orders

def write_to_order(row_number, value=500):
    """Записывает значение в столбец G (Сумма) указанной строки"""
    sheet = get_worksheet()
    sheet.update(f'G{row_number}', [[value]])
    print(f"✅ Записано {value} в G{row_number}")

# ========== КОМАНДЫ ==========
def start(update, context):
    keyboard = [[InlineKeyboardButton("📋 Создать отчёт", callback_data="new_report")]]
    update.message.reply_text(
        "👋 Здравствуйте! Для создания отчёта нажмите на кнопку:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

def new_report_callback(update, context):
    query = update.callback_query
    query.answer()
    
    orders = get_available_orders()
    
    if not orders:
        query.edit_message_text("❌ Нет доступных заявок со статусом «В работе».")
        return
    
    keyboard = []
    for order in orders:
        text = f"{order['id']} - {order['client']} - {order['address']}"
        keyboard.append([InlineKeyboardButton(text, callback_data=f"order_{order['row']}")])
    
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
    
    query.edit_message_text(
        "📋 Выберите заявку:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

def select_order_callback(update, context):
    query = update.callback_query
    query.answer()
    
    if query.data == "cancel":
        query.edit_message_text("❌ Отменено. Для нового отчёта нажмите /start")
        return
    
    row = int(query.data.split('_')[1])
    
    # Записываем 500 в столбец G этой строки
    write_to_order(row, 500)
    
    query.edit_message_text(f"✅ В заявку (строка {row}) записано 500 в столбец «Сумма заказа».")

def cancel_callback(update, context):
    query = update.callback_query
    query.answer()
    query.edit_message_text("❌ Отменено. Для нового отчёта нажмите /start")

# ========== ВЕБХУК ==========
@flask_app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        update = Update.de_json(data, updater.bot)
        updater.dispatcher.process_update(update)
        return "OK", 200
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        return "Internal Server Error", 500

@flask_app.route('/')
def home():
    return "Бот работает", 200

# ========== ЗАПУСК ==========
def run_webhook():
    global updater
    
    updater = Updater(token=TOKEN, use_context=True)
    dp = updater.dispatcher
    
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CallbackQueryHandler(new_report_callback, pattern="^new_report$"))
    dp.add_handler(CallbackQueryHandler(select_order_callback, pattern="^order_"))
    dp.add_handler(CallbackQueryHandler(cancel_callback, pattern="^cancel$"))
    
    port = int(os.environ.get("PORT", 8080))
    print(f"✅ Бот запущен на порту {port}")
    flask_app.run(host='0.0.0.0', port=port)

if __name__ == '__main__':
    run_webhook()
