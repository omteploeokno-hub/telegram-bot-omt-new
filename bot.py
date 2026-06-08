import os
import json
import sqlite3
import re
import asyncio
from datetime import datetime
from flask import Flask, request
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
app_flask = Flask(__name__)

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
    draft = load_draft(context.user_data['user_id'], row)
    context.user_data['draft'] = draft or {}
    if draft:
        filled = []
        if draft.get('date'): filled.append("дата")
        if draft.get('cost'): filled.append("стоимость")
        if draft.get('delivery'): filled.append("выезд")
        if draft.get('expense'): filled.append("расходы")
        if draft.get('status'): filled.append("статус")
        await query.edit_message_text(
            f"⚠️ **Внимание!** Эта заявка уже частично заполнена.\n"
            f"Введены: {', '.join(filled)}.\n\n"
            f"Проверьте данные перед отправкой.\n\n"
            f"📋 Заявка: {order['id']}\n"
            f"Клиент: {order['client']}\n"
            f"Адрес: {order['address']}",
            parse_mode='Markdown'
        )
    else:
        await query.edit_message_text(
            f"📋 Заявка: {order['id']}\n"
            f"Клиент: {order['client']}\n"
            f"Адрес: {order['address']}\n\n"
            f"Заполните форму:",
            parse_mode='Markdown'
        )
    await show_form(update, context)

async def show_form(update: Update, context):
    draft = context.user_data.get('draft', {})
    text = "📝 **Форма отчёта:**\n\n"
    text += f"📅 Дата выполнения: {draft.get('date', '❌ не заполнено')}\n"
    text += f"💰 Стоимость: {draft.get('cost', '❌ не заполнено')}\n"
    text += f"🚚 Выезд/доставка: {draft.get('delivery', '❌ не заполнено')}\n"
    text += f"📦 Расходы: {draft.get('expense', '❌ не заполнено')}\n"
    text += f"📌 Статус: {draft.get('status', '❌ не выбран')}\n"
    text += f"💬 Комментарий: {draft.get('comment', '—')}\n\n"
    keyboard = [
        [InlineKeyboardButton("📅 Дата", callback_data="edit_date"),
         InlineKeyboardButton("💰 Стоимость", callback_data="edit_cost")],
        [InlineKeyboardButton("🚚 Выезд", callback_data="edit_delivery"),
         InlineKeyboardButton("📦 Расходы", callback_data="edit_expense")],
        [InlineKeyboardButton("📌 Статус", callback_data="edit_status"),
         InlineKeyboardButton("💬 Комментарий", callback_data="edit_comment")],
        [InlineKeyboardButton("💾 Сохранить черновик", callback_data="save_draft"),
         InlineKeyboardButton("📤 Отправить", callback_data="submit")],
        [InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")]
    ]
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

async def edit_field(update: Update, context, field, prompt, validator=None):
    context.user_data['edit_field'] = field
    context.user_data['validator'] = validator
    await update.callback_query.edit_message_text(prompt, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="cancel_edit")]]))

def validate_date(date_str):
    return re.match(r'^\d{2}\.\d{2}\.\d{4}$', date_str)

def validate_number(num_str):
    return num_str.isdigit() and int(num_str) >= 0

async def handle_text_input(update: Update, context):
    field = context.user_data.get('edit_field')
    value = update.message.text.strip()
    if field == 'edit_date' and not validate_date(value):
        await update.message.reply_text("❌ Неверный формат. Используйте ДД.ММ.ГГГГ")
        return
    elif field in ['edit_cost', 'edit_delivery', 'edit_expense'] and not validate_number(value):
        await update.message.reply_text("❌ Введите неотрицательное число (только цифры)")
        return
    field_name = field.replace('edit_', '')
    context.user_data['draft'][field_name] = value
    save_draft(context.user_data['user_id'], context.user_data['current_row'], context.user_data['draft'])
    await update.message.reply_text("✅ Сохранено!")
    await show_form(update, context)

async def edit_status_callback(update: Update, context):
    keyboard = [[InlineKeyboardButton(s, callback_data=f"set_status_{s}")] for s in STATUSES]
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel_edit")])
    await update.callback_query.edit_message_text("Выберите статус:", reply_markup=InlineKeyboardMarkup(keyboard))

async def set_status(update: Update, context):
    query = update.callback_query
    await query.answer()
    status = query.data.split('_', 2)[2]
    context.user_data['draft']['status'] = status
    save_draft(context.user_data['user_id'], context.user_data['current_row'], context.user_data['draft'])
    await query.edit_message_text("✅ Статус сохранён!")
    await show_form(update, context)

async def edit_comment_callback(update: Update, context):
    context.user_data['edit_field'] = 'edit_comment'
    await update.callback_query.edit_message_text("💬 Введите комментарий (можно пропустить):", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏭️ Пропустить", callback_data="skip_comment")], [InlineKeyboardButton("❌ Отмена", callback_data="cancel_edit")]]))

async def handle_comment_input(update: Update, context):
    field = context.user_data.get('edit_field')
    if field == 'edit_comment':
        value = update.message.text.strip()
        context.user_data['draft']['comment'] = value
        save_draft(context.user_data['user_id'], context.user_data['current_row'], context.user_data['draft'])
        await update.message.reply_text("✅ Комментарий сохранён!")
        await show_form(update, context)

async def skip_comment(update: Update, context):
    query = update.callback_query
    await query.answer()
    context.user_data['draft']['comment'] = ''
    save_draft(context.user_data['user_id'], context.user_data['current_row'], context.user_data['draft'])
    await query.edit_message_text("✅ Комментарий пропущен!")
    await show_form(update, context)

async def submit_report(update: Update, context):
    draft = context.user_data.get('draft', {})
    errors = []
    if not draft.get('date'):
        errors.append("📅 Дата выполнения")
    if not draft.get('cost') or not draft.get('cost').isdigit() or int(draft.get('cost')) < 0:
        errors.append("💰 Стоимость")
    if not draft.get('delivery') or not draft.get('delivery').isdigit() or int(draft.get('delivery')) < 0:
        errors.append("🚚 Выезд/доставка")
    if not draft.get('expense') or not draft.get('expense').isdigit() or int(draft.get('expense')) < 0:
        errors.append("📦 Расходы")
    if not draft.get('status'):
        errors.append("📌 Статус")
    if draft.get('status') == "🔄 Перенаправлена" and not draft.get('comment'):
        errors.append("💬 Комментарий (обязателен для статуса «Перенаправлена»)")
    if errors:
        await update.callback_query.edit_message_text(
            f"❌ Заполните обязательные поля:\n" + "\n".join(f"• {e}" for e in errors),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад к форме", callback_data="back_to_form")]])
        )
        return
    context.user_data['confirm_data'] = draft.copy()
    text = "✅ **Проверьте данные:**\n\n"
    text += f"📅 Дата: {draft['date']}\n💰 Стоимость: {draft['cost']} руб\n🚚 Выезд: {draft['delivery']} руб\n📦 Расходы: {draft['expense']} руб\n📌 Статус: {draft['status']}\n💬 Комментарий: {draft.get('comment', '—')}\n\nВсё верно?"
    keyboard = [
        [InlineKeyboardButton("✅ Подтверждаю", callback_data="confirm_yes")],
        [InlineKeyboardButton("✏️ Редактировать", callback_data="back_to_form")],
        [InlineKeyboardButton("❌ Отмена", callback_data="main_menu")]
    ]
    await update.callback_query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

async def confirm_save(update: Update, context):
    query = update.callback_query
    await query.answer()
    if query.data == "confirm_yes":
        sheet = get_sheet(context.user_data['worker']['sheet'])
        update_order(sheet, context.user_data['current_row'], context.user_data['confirm_data'])
        delete_draft(context.user_data['user_id'], context.user_data['current_row'])
        if context.user_data['worker'].get('chat_id'):
            report_text = f"📊 **Новый отчёт**\n👤 Работник: {context.user_data['worker']['name']}\n📋 Заявка: {context.user_data['current_order']['id']}\n🏢 Клиент: {context.user_data['current_order']['client']}\n📍 Адрес: {context.user_data['current_order']['address']}\n📅 Дата: {context.user_data['confirm_data']['date']}\n💰 Стоимость: {context.user_data['confirm_data']['cost']} руб\n🚚 Выезд: {context.user_data['confirm_data']['delivery']} руб\n📦 Расходы: {context.user_data['confirm_data']['expense']} руб\n📌 Статус: {context.user_data['confirm_data']['status']}\n💬 Комментарий: {context.user_data['confirm_data'].get('comment', '—')}"
            await context.bot.send_message(chat_id=context.user_data['worker']['chat_id'], text=report_text, parse_mode='Markdown')
        await query.edit_message_text("✅ Отчёт сохранён!\n\nСпасибо за работу. Для нового отчёта нажмите /new")
        context.user_data.clear()
    elif query.data == "back_to_form":
        await show_form(update, context)
    elif query.data == "main_menu":
        await query.edit_message_text("🏠 Главное меню. Для нового отчёта нажмите /new")

async def save_draft_callback(update: Update, context):
    save_draft(context.user_data['user_id'], context.user_data['current_row'], context.user_data['draft'])
    await update.callback_query.edit_message_text("💾 Черновик сохранён!")
    await show_form(update, context)

async def main_menu(update: Update, context):
    await update.callback_query.edit_message_text("🏠 Главное меню. Для нового отчёта нажмите /new")

async def cancel_edit(update: Update, context):
    context.user_data.pop('edit_field', None)
    await show_form(update, context)

async def back_to_form(update: Update, context):
    await show_form(update, context)

# ========== ВЕБХУК ==========
@app_flask.route('/webhook', methods=['POST'])
def webhook():
    global telegram_app
    try:
        data = request.get_json()
        update = Update.de_json(data, telegram_app.bot)
        
        # Создаём новый event loop для каждого запроса
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(telegram_app.process_update(update))
        
        return "OK", 200
    except Exception as e:
        print(f"❌ Ошибка в вебхуке: {e}")
        import traceback
        traceback.print_exc()
        return "Internal Server Error", 500

@app_flask.route('/', methods=['GET'])
def home():
    return "Бот работает", 200

def run_webhook():
    global telegram_app
    init_db()
    
    telegram_app = Application.builder().token(TOKEN).build()
    
    # Добавляем обработчики
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CommandHandler("new", new_report))
    telegram_app.add_handler(CallbackQueryHandler(select_order, pattern="^(order_|prev_page|next_page|cancel)$"))
    telegram_app.add_handler(CallbackQueryHandler(edit_status_callback, pattern="^edit_status$"))
    telegram_app.add_handler(CallbackQueryHandler(set_status, pattern="^set_status_"))
    telegram_app.add_handler(CallbackQueryHandler(edit_comment_callback, pattern="^edit_comment$"))
    telegram_app.add_handler(CallbackQueryHandler(skip_comment, pattern="^skip_comment$"))
    telegram_app.add_handler(CallbackQueryHandler(submit_report, pattern="^submit$"))
    telegram_app.add_handler(CallbackQueryHandler(save_draft_callback, pattern="^save_draft$"))
    telegram_app.add_handler(CallbackQueryHandler(main_menu, pattern="^main_menu$"))
    telegram_app.add_handler(CallbackQueryHandler(cancel_edit, pattern="^cancel_edit$"))
    telegram_app.add_handler(CallbackQueryHandler(back_to_form, pattern="^back_to_form$"))
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_input))
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_comment_input))
    
    # Устанавливаем вебхук
    import requests
    domain = os.environ.get('RAILWAY_PUBLIC_DOMAIN', '')
    if not domain:
        print("⚠️ RAILWAY_PUBLIC_DOMAIN не установлена!")
        print("Установите вебхук вручную:")
        print(f"https://api.telegram.org/bot{TOKEN}/setWebhook?url=https://telegram-bot-omt-production.up.railway.app/webhook")
    else:
        webhook_url = f"https://{domain}/webhook"
        print(f"🔗 Устанавливаю вебхук: {webhook_url}")
        response = requests.get(f"https://api.telegram.org/bot{TOKEN}/setWebhook?url={webhook_url}")
        print(f"✅ Ответ: {response.json()}")
    
    port = int(os.environ.get("PORT", 8080))
    print(f"✅ Бот запущен в режиме вебхука на порту {port}")
    app_flask.run(host='0.0.0.0', port=port)

if __name__ == '__main__':
    run_webhook()
