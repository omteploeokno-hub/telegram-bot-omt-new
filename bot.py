import os
import json
import sqlite3
import re
import asyncio
from datetime import datetime
from flask import Flask, request, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters
import gspread
from google.oauth2.service_account import Credentials

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get('TELEGRAM_TOKEN')
if not TOKEN:
    raise ValueError("TELEGRAM_TOKEN не установлен!")

SPREADSHEET_NAME = "Indev"

USERS = {
    6067555377: {
        "name": "Сергей Олегович",
        "sheet": "Сергей Олегович",
        "chat_id": None
    }
}

STATUSES = ["✅ Выполнена", "❌ Отказ", "🔄 Перенаправлена"]

# Глобальные переменные
telegram_app = None

# Flask приложение
flask_app = Flask(__name__)

# ========== БАЗА ДАННЫХ ==========
def init_db():
    conn = sqlite3.connect('drafts.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS drafts
                 (user_id INTEGER, row_number INTEGER, date TEXT, cost TEXT, 
                  delivery TEXT, expense TEXT, status TEXT, comment TEXT,
                  PRIMARY KEY (user_id, row_number))''')
    conn.commit()
    conn.close()

def save_draft(user_id, row_number, data):
    conn = sqlite3.connect('drafts.db')
    c = conn.cursor()
    c.execute('''INSERT OR REPLACE INTO drafts 
                 (user_id, row_number, date, cost, delivery, expense, status, comment)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
              (user_id, row_number, data.get('date', ''), data.get('cost', ''),
               data.get('delivery', ''), data.get('expense', ''), data.get('status', ''), data.get('comment', '')))
    conn.commit()
    conn.close()

def load_draft(user_id, row_number):
    conn = sqlite3.connect('drafts.db')
    c = conn.cursor()
    c.execute('SELECT date, cost, delivery, expense, status, comment FROM drafts WHERE user_id = ? AND row_number = ?',
              (user_id, row_number))
    row = c.fetchone()
    conn.close()
    if row:
        return {'date': row[0], 'cost': row[1], 'delivery': row[2], 'expense': row[3], 'status': row[4], 'comment': row[5]}
    return None

def delete_draft(user_id, row_number):
    conn = sqlite3.connect('drafts.db')
    c = conn.cursor()
    c.execute('DELETE FROM drafts WHERE user_id = ? AND row_number = ?', (user_id, row_number))
    conn.commit()
    conn.close()

# ========== GOOGLE SHEETS ==========
def get_sheet(worker_name):
    scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    creds_json = os.environ.get('GOOGLE_CREDENTIALS')
    if not creds_json:
        raise Exception("GOOGLE_CREDENTIALS не установлена!")
    creds_info = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
    client = gspread.authorize(creds)
    return client.open(SPREADSHEET_NAME).worksheet(worker_name)

def get_available_orders(sheet):
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

def update_order(sheet, row, data):
    sheet.update(f'G{row}', data['cost'])
    sheet.update(f'H{row}', data['delivery'])
    sheet.update(f'I{row}', data['expense'])
    sheet.update(f'O{row}', data['status'])
    sheet.update(f'P{row}', data['comment'])
    sheet.update(f'D{row}', datetime.now().strftime('%d.%m.%Y'))

# ========== КОМАНДЫ БОТА ==========
async def start(update: Update, context):
    user_id = update.effective_user.id
    if user_id not in USERS:
        await update.message.reply_text("⛔ Доступ запрещён.")
        return
    context.user_data['user_id'] = user_id
    context.user_data['worker'] = USERS[user_id]
    await update.message.reply_text(f"👋 Здравствуйте, {USERS[user_id]['name']}!\n\n📋 Нажмите /new для создания отчёта.")

async def new_report(update: Update, context):
    user_id = update.effective_user.id
    if user_id not in USERS:
        return
    sheet = get_sheet(USERS[user_id]['sheet'])
    orders = get_available_orders(sheet)
    if not orders:
        await update.message.reply_text("❌ Нет доступных заявок со статусом «В работе».")
        return
    context.user_data['orders'] = orders
    context.user_data['current_page'] = 0
    await show_orders(update, context)

async def show_orders(update: Update, context):
    orders = context.user_data['orders']
    page = context.user_data.get('current_page', 0)
    total_pages = (len(orders) + 9) // 10
    keyboard = []
    start_idx = page * 10
    end_idx = min(start_idx + 10, len(orders))
    for order in orders[start_idx:end_idx]:
        text = f"{order['id']} - {order['client']} - {order['address']}"
        keyboard.append([InlineKeyboardButton(text, callback_data=f"order_{order['row']}")])
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("◀️ Назад", callback_data="prev_page"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("Вперёд ▶️", callback_data="next_page"))
    if nav_buttons:
        keyboard.append(nav_buttons)
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
    if update.callback_query:
        await update.callback_query.edit_message_text("📋 Выберите заявку:", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text("📋 Выберите заявку:", reply_markup=InlineKeyboardMarkup(keyboard))

async def select_order(update: Update, context):
    query = update.callback_query
    await query.answer()
    
    if query.data == "prev_page":
        context.user_data['current_page'] -= 1
        await show_orders(update, context)
        return
    elif query.data == "next_page":
        context.user_data['current_page'] += 1
        await show_orders(update, context)
        return
    elif query.data == "cancel":
        await query.edit_message_text("❌ Отменено. Для нового отчёта нажмите /new")
        return
    
    row = int(query.data.split('_')[1])
    order = next((o for o in context.user_data['orders'] if o['row'] == row), None)
    if not order:
        await query.edit_message_text("❌ Ошибка: заявка не найдена.")
        return
    
    context.user_data['current_row'] = row
    context.user_data['current_order'] = order
    
    # Показываем форму для ввода даты
    await query.edit_message_text(
        f"📋 **Заявка:** {order['id']}\n"
        f"🏢 **Клиент:** {order['client']}\n"
        f"📍 **Адрес:** {order['address']}\n\n"
        f"✏️ Введите дату выполнения в формате ДД.ММ.ГГГГ:",
        parse_mode='Markdown'
    )
    return

# ========== ВЕБХУК ==========
@flask_app.route('/webhook', methods=['POST'])
def webhook():  # <-- Убрал async
    global telegram_app
    try:
        data = request.get_json()
        # Передаём update в очередь бота синхронно
        asyncio.run_coroutine_threadsafe(
            telegram_app.update_queue.put(Update.de_json(data, telegram_app.bot)),
            asyncio.get_event_loop()
        )
        return "OK", 200
    except Exception as e:
        print(f"❌ Ошибка в вебхуке: {e}")
        return "Internal Server Error", 500

@flask_app.route('/', methods=['GET'])
def home():
    return "Бот работает", 200

async def run_bot():
    global telegram_app
    init_db()
    
    # Создаём приложение без Updater (Flask будет принимать запросы)
    telegram_app = Application.builder().token(TOKEN).updater(None).build()
    
    # Добавляем обработчики
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CommandHandler("new", new_report))
    telegram_app.add_handler(CallbackQueryHandler(select_order, pattern="^(order_|prev_page|next_page|cancel)$"))
    
    # Инициализируем приложение
    await telegram_app.initialize()
    await telegram_app.start()
    
    # Устанавливаем вебхук
    import requests
    domain = os.environ.get('RAILWAY_PUBLIC_DOMAIN', '')
    if not domain:
        print("⚠️ RAILWAY_PUBLIC_DOMAIN не установлена!")
        print(f"Установите вебхук вручную:\nhttps://api.telegram.org/bot{TOKEN}/setWebhook?url=https://telegram-bot-omt-new-production.up.railway.app/webhook")
    else:
        webhook_url = f"https://{domain}/webhook"
        print(f"🔗 Устанавливаю вебхук: {webhook_url}")
        response = requests.get(f"https://api.telegram.org/bot{TOKEN}/setWebhook?url={webhook_url}")
        print(f"✅ Ответ: {response.json()}")
    
    # Запускаем Flask в отдельном потоке
    port = int(os.environ.get("PORT", 8080))
    print(f"✅ Бот запущен в режиме вебхука на порту {port}")
    
    # Flask работает в отдельном потоке
    await asyncio.to_thread(
        flask_app.run, host='0.0.0.0', port=port, debug=False, use_reloader=False
    )

if __name__ == '__main__':
    asyncio.run(run_bot())
