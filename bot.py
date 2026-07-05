import os
import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
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

# Часовой пояс Екатеринбург (UTC+5)
EKATERINBURG_TZ = timezone(timedelta(hours=5))

flask_app = Flask(__name__)
telegram_app = None
main_loop = None

STATUS_OPTIONS = ["✅ Выполнена", "❌ Отказ", "🔄 Перенаправлена"]
PAYMENT_OPTIONS = [
    ("individual", "Оплату получил мастер"),
    ("legal", "Оплату получила организация")
]

# ========== ПОЛЬЗОВАТЕЛИ ==========
USERS = {
    6067555377: {
        "name": "Тест",
        "sheet": "Тест",
        "chat_id": None
    },
    5518656277: {
        "name": "Сергей Олегович",
        "sheet": "Сергей Олегович",
        "chat_id": -5446818397
    },
    1004439700: {
        "name": "Виктор",
        "sheet": "Виктор",
        "chat_id": None
    }
}

# ========== GOOGLE SHEETS ==========
def get_worksheet(sheet_name):
    creds_json = os.environ.get('GOOGLE_CREDENTIALS')
    if not creds_json:
        raise Exception("GOOGLE_CREDENTIALS не установлена!")
    creds_info = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_info,
        scopes=['https://www.googleapis.com/auth/spreadsheets',
                'https://www.googleapis.com/auth/drive'])
    client = gspread.authorize(creds)
    return client.open(SPREADSHEET_NAME).worksheet(sheet_name)

def get_next_empty_row(sheet):
    all_values = sheet.get_all_values()
    for idx, row in enumerate(all_values, start=1):
        if all(cell == '' for cell in row):
            return idx
    return len(all_values) + 1

def get_available_orders(sheet_name):
    sheet = get_worksheet(sheet_name)
    records = sheet.get_all_records()
    orders = []
    for idx, row in enumerate(records, start=2):
        if row.get('Статус заявки') == 'В работе':
            orders.append({
                'row': idx,
                'id': row.get('ID заявки', ''),
                'client': row.get('Клиент', ''),
                'address': row.get('Адрес', ''),
                'receipt_date': row.get('Дата поступления', '')
            })
    return orders

def update_order(sheet_name, row, data):
    sheet = get_worksheet(sheet_name)
    sheet.update(values=[[data['cost']]], range_name=f'G{row}')
    sheet.update(values=[[data['delivery']]], range_name=f'H{row}')
    sheet.update(values=[[data['expense']]], range_name=f'I{row}')
    sheet.update(values=[[data['status']]], range_name=f'O{row}')
    sheet.update(values=[[data['date']]], range_name=f'D{row}')
    sheet.update(values=[[data.get('comment', '')]], range_name=f'P{row}')
    sheet.update(values=[[data.get('payment_type', '')]], range_name=f'R{row}')

# ========== НОВАЯ ФУНКЦИЯ: ОТПРАВКА В ЛОГИ ==========
async def send_log_message(order_id, master_name, action_text):
    """Отправляет сообщение в группу логов"""
    try:
        logs_chat_id = -5316127083
        now = datetime.now(EKATERINBURG_TZ)
        date_time_str = now.strftime("%d.%m.%Y %H:%M UTC+5")
        
        log_text = (
            f"🟢 {date_time_str} {action_text} ID #{order_id}\n\n"
            f"<i>Действие совершил: \"{master_name}\"</i>"
        )
        
        await telegram_app.bot.send_message(
            chat_id=logs_chat_id, 
            text=log_text, 
            parse_mode='HTML'
        )
        print(f"DEBUG: уведомление отправлено в группу логов: {action_text}")
    except Exception as e:
        print(f"DEBUG: не удалось отправить уведомление в логи: {e}")

# ========== НОВАЯ ФУНКЦИЯ: ДОБАВЛЕНИЕ КОММЕНТАРИЯ В ОБЩИЙ ПУЛ ==========
def add_comment_to_general_pool(order_id, comment, status_name):
    """Добавляет комментарий в Общий пул заявок для статусов Выполнена/Отказ"""
    try:
        general_sheet = get_worksheet("Общий пул заявок")
        
        # Находим строку с заявкой
        all_ids = general_sheet.col_values(1)
        general_row = None
        for idx, val in enumerate(all_ids, start=1):
            if val == order_id:
                general_row = idx
                break
        
        if not general_row:
            print(f"DEBUG: заявка {order_id} не найдена в общем пуле")
            return
        
        # Формируем комментарий с префиксом
        comment_text = f"{status_name}: {comment}" if comment else f"{status_name}"
        
        # Ищем первую пустую ячейку в столбцах J-R
        for col in range(10, 19):
            cell_value = general_sheet.cell(general_row, col).value
            if not cell_value:
                general_sheet.update(
                    range_name=f'{chr(64 + col)}{general_row}', 
                    values=[[comment_text]]
                )
                print(f"DEBUG: комментарий добавлен в столбец {chr(64 + col)}")
                break
                
    except Exception as e:
        print(f"DEBUG: ошибка при добавлении комментария в общий пул: {e}")

# ========== ПЕРЕНАПРАВЛЕНИЕ ==========
async def redirect_order(data, sheet_name):
    """Перенаправляет заявку в Первичный пул заявок"""
    print("DEBUG: redirect_order вызван")
    
    try:
        primary_sheet = get_worksheet("Первичный пул заявок")
        primary_row = get_next_empty_row(primary_sheet)
        
        master_sheet = get_worksheet(sheet_name)
        order_id = data['order_id']
        
        all_ids = master_sheet.col_values(1)
        master_row = None
        for idx, val in enumerate(all_ids, start=1):
            if val == order_id:
                master_row = idx
                break
        
        if not master_row:
            print(f"DEBUG: заявка {order_id} не найдена в листе мастера")
            return
        
        source = master_sheet.cell(master_row, 2).value
        receipt_date = master_sheet.cell(master_row, 3).value
        client = master_sheet.cell(master_row, 5).value
        address = master_sheet.cell(master_row, 6).value
        
        now = datetime.now(EKATERINBURG_TZ).strftime("%d.%m.%Y")
        
        primary_sheet.update(range_name=f'A{primary_row}', values=[[order_id]])
        primary_sheet.update(range_name=f'B{primary_row}', values=[[source]])
        primary_sheet.update(range_name=f'C{primary_row}', values=[[receipt_date]])
        primary_sheet.update(range_name=f'E{primary_row}', values=[[client]])
        primary_sheet.update(range_name=f'F{primary_row}', values=[[address]])
        primary_sheet.update(range_name=f'H{primary_row}', values=[["Да"]])
        primary_sheet.update(range_name=f'I{primary_row}', values=[[now]])
        
        print(f"DEBUG: заявка {order_id} скопирована в Первичный пул, строка {primary_row}")
        
        general_sheet = get_worksheet("Общий пул заявок")
        all_ids_general = general_sheet.col_values(1)
        general_row = None
        for idx, val in enumerate(all_ids_general, start=1):
            if val == order_id:
                general_row = idx
                break
        
        if general_row:
            general_sheet.update(range_name=f'G{general_row}', values=[["На перенаправление"]])
            general_sheet.update(range_name=f'H{general_row}', values=[[""]])
            
            comment = data.get('comment', '')
            comment_text = f"Перенаправлена: {comment}"
            for col in range(10, 19):
                cell_value = general_sheet.cell(general_row, col).value
                if not cell_value:
                    general_sheet.update(range_name=f'{chr(64 + col)}{general_row}', values=[[comment_text]])
                    print(f"DEBUG: комментарий добавлен в столбец {chr(64 + col)}")
                    break
            
            print(f"DEBUG: общий пул обновлён, строка {general_row}")
        else:
            print(f"DEBUG: заявка {order_id} не найдена в общем пуле")
        
        # ========== ОТПРАВКА В ЛОГИ (используем новую функцию) ==========
        master_name = sheet_name
        await send_log_message(order_id, master_name, "заявка отправлена на перенаправление")
            
    except Exception as e:
        print(f"DEBUG: ошибка при перенаправлении: {e}")

# ========== КОМАНДЫ ==========
async def start(update, context):
    user_id = update.effective_user.id
    
    if user_id not in USERS:
        await update.message.reply_text("⛔ Доступ запрещён. Обратитесь к администратору.")
        return
    
    context.user_data['user_id'] = user_id
    context.user_data['sheet_name'] = USERS[user_id]['sheet']
    context.user_data['user_name'] = USERS[user_id]['name']
    
    keyboard = [[InlineKeyboardButton("📋 Создать отчёт", callback_data="new_report")]]
    await update.message.reply_text(
        f"👋 Здравствуйте, {USERS[user_id]['name']}!\n\n📋 Для создания отчёта нажмите на кнопку:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def show_orders_or_empty(update, context, message_prefix=None):
    sheet_name = context.user_data.get('sheet_name')
    if not sheet_name:
        if isinstance(update, Update):
            await update.message.reply_text("❌ Ошибка: не удалось определить ваш лист. Начните с /start")
        else:
            await update.edit_message_text("❌ Ошибка: не удалось определить ваш лист. Начните с /start")
        return
    
    orders = get_available_orders(sheet_name)
    
    refresh_button = [InlineKeyboardButton("🔄 Обновить", callback_data="check_orders")]
    
    if orders:
        keyboard = []
        for order in orders:
            text = f"{order['id']} - {order['client']} - {order['address']}"
            keyboard.append([InlineKeyboardButton(text, callback_data=f"order_{order['row']}")])
        keyboard.append(refresh_button)
        keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
        
        text = "📋 Выберите заявку:"
        if message_prefix:
            text = f"{message_prefix}\n\n{text}"
        
        if isinstance(update, Update):
            await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        keyboard = [refresh_button]
        keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
        
        text = "❌ Нет доступных заявок со статусом «В работе»."
        if message_prefix:
            text = f"{message_prefix}\n\n{text}"
        
        if isinstance(update, Update):
            await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def new_report_callback(update, context):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    
    user_id = update.effective_user.id
    if user_id not in USERS:
        await query.edit_message_text("⛔ Доступ запрещён.")
        return
    
    context.user_data['user_id'] = user_id
    context.user_data['sheet_name'] = USERS[user_id]['sheet']
    context.user_data['user_name'] = USERS[user_id]['name']
    
    await show_orders_or_empty(query, context)

async def check_orders_callback(update, context):
    query = update.callback_query
    await query.answer()
    await show_orders_or_empty(query, context)

async def cancel_handler(update, context):
    context.user_data.clear()
    
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("❌ Отменено. Для создания нового отчёта нажмите /start")
    else:
        await update.message.reply_text("❌ Отменено. Для создания нового отчёта нажмите /start")

async def go_back(update, context):
    query = update.callback_query
    await query.answer()
    
    step = context.user_data.get('step')
    status = context.user_data.get('status')
    is_executed = status == "✅ Выполнена"
    
    if step == 'date':
        await show_orders_or_empty(query, context)
    
    elif step == 'waiting_date':
        context.user_data['step'] = 'date'
        keyboard = [
            [InlineKeyboardButton("📅 Сегодня", callback_data="date_today")],
            [InlineKeyboardButton("📆 Указать другую дату", callback_data="date_other")]
        ]
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back")])
        keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
        
        await query.edit_message_text(
            f"📋 Заявка: {context.user_data['order_id']} - {context.user_data['order_client']} - {context.user_data['order_address']}\n📅 Дата поступления: {context.user_data['receipt_date']}\n\nУкажите дату выполнения:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    elif step == 'status':
        context.user_data['step'] = 'date'
        keyboard = [
            [InlineKeyboardButton("📅 Сегодня", callback_data="date_today")],
            [InlineKeyboardButton("📆 Указать другую дату", callback_data="date_other")]
        ]
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back")])
        keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
        
        await query.edit_message_text(
            f"📋 Заявка: {context.user_data['order_id']} - {context.user_data['order_client']} - {context.user_data['order_address']}\n📅 Дата поступления: {context.user_data['receipt_date']}\n\nУкажите дату выполнения:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    elif step == 'payment':
        context.user_data['step'] = 'status'
        keyboard = [[InlineKeyboardButton(s, callback_data=f"status_{s}")] for s in STATUS_OPTIONS]
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back")])
        keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
        
        await query.edit_message_text(
            f"📋 Заявка: {context.user_data['order_id']} - {context.user_data['order_client']} - {context.user_data['order_address']}\n📅 Дата поступления: {context.user_data['receipt_date']}\n📅 Дата выполнения: {context.user_data['date']}\n\nУкажите статус заявки:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    elif step == 'cost':
        if is_executed:
            context.user_data['step'] = 'payment'
            keyboard = []
            for value, label in PAYMENT_OPTIONS:
                keyboard.append([InlineKeyboardButton(label, callback_data=f"payment_{value}")])
            keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back")])
            keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
            await query.edit_message_text(
                f"📋 Заявка: {context.user_data['order_id']} - {context.user_data['order_client']} - {context.user_data['order_address']}\n"
                f"📅 Дата поступления: {context.user_data['receipt_date']}\n"
                f"📅 Дата выполнения: {context.user_data['date']}\n"
                f"📌 Статус: {status}\n\n"
                f"Кто получил оплату?",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            context.user_data['step'] = 'status'
            keyboard = [[InlineKeyboardButton(s, callback_data=f"status_{s}")] for s in STATUS_OPTIONS]
            keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back")])
            keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
            await query.edit_message_text(
                f"📋 Заявка: {context.user_data['order_id']} - {context.user_data['order_client']} - {context.user_data['order_address']}\n📅 Дата поступления: {context.user_data['receipt_date']}\n📅 Дата выполнения: {context.user_data['date']}\n\nУкажите статус заявки:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
    
    elif step == 'delivery':
        if is_executed:
            context.user_data['step'] = 'cost'
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back")]]
            await query.edit_message_text(
                "Введите сумму заказа (только цифры):",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            context.user_data['step'] = 'status'
            keyboard = [[InlineKeyboardButton(s, callback_data=f"status_{s}")] for s in STATUS_OPTIONS]
            keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back")])
            keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
            await query.edit_message_text(
                f"📋 Заявка: {context.user_data['order_id']} - {context.user_data['order_client']} - {context.user_data['order_address']}\n📅 Дата поступления: {context.user_data['receipt_date']}\n📅 Дата выполнения: {context.user_data['date']}\n\nУкажите статус заявки:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
    
    elif step == 'expense':
        if is_executed:
            context.user_data['step'] = 'delivery'
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back")]]
            await query.edit_message_text(
                "Введите сумму выезда/доставки (только цифры):",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            context.user_data['step'] = 'status'
            keyboard = [[InlineKeyboardButton(s, callback_data=f"status_{s}")] for s in STATUS_OPTIONS]
            keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back")])
            keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
            await query.edit_message_text(
                f"📋 Заявка: {context.user_data['order_id']} - {context.user_data['order_client']} - {context.user_data['order_address']}\n📅 Дата поступления: {context.user_data['receipt_date']}\n📅 Дата выполнения: {context.user_data['date']}\n\nУкажите статус заявки:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
    
    elif step == 'waiting_comment':
        status = context.user_data.get('status')
        is_exec = status == "✅ Выполнена"
        
        if is_exec:
            context.user_data['step'] = 'expense'
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back")]]
            await query.edit_message_text(
                "Введите расходы (только цифры):",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            context.user_data['step'] = 'delivery'
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back")]]
            await query.edit_message_text(
                "Введите сумму выезда/доставки (только цифры):",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
    
    elif step == 'confirm':
        await proceed_to_comment(update, context)
    
    else:
        await query.edit_message_text("❌ Нельзя вернуться назад. Начните с /start")

async def select_order_callback(update, context):
    query = update.callback_query
    await query.answer()
    
    if query.data == "cancel":
        await cancel_handler(update, context)
        return
    
    if query.data == "back":
        await go_back(update, context)
        return
    
    context.user_data.clear()
    
    user_id = update.effective_user.id
    if user_id not in USERS:
        await query.edit_message_text("⛔ Доступ запрещён.")
        return
    
    context.user_data['user_id'] = user_id
    context.user_data['sheet_name'] = USERS[user_id]['sheet']
    context.user_data['user_name'] = USERS[user_id]['name']
    
    row = int(query.data.split('_')[1])
    
    sheet_name = context.user_data['sheet_name']
    orders = get_available_orders(sheet_name)
    order = next((o for o in orders if o['row'] == row), None)
    if not order:
        await query.edit_message_text("❌ Ошибка: заявка не найдена. Возможно, статус изменился.")
        return
    
    context.user_data['row'] = row
    context.user_data['order_id'] = order['id']
    context.user_data['order_client'] = order['client']
    context.user_data['order_address'] = order['address']
    context.user_data['receipt_date'] = order.get('receipt_date', '—')
    context.user_data['step'] = 'date'
    
    keyboard = [
        [InlineKeyboardButton("📅 Сегодня", callback_data="date_today")],
        [InlineKeyboardButton("📆 Указать другую дату", callback_data="date_other")]
    ]
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back")])
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
    
    await query.edit_message_text(
        f"📋 Заявка: {order['id']} - {order['client']} - {order['address']}\n📅 Дата поступления: {order.get('receipt_date', '—')}\n\nУкажите дату выполнения:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def date_callback(update, context):
    query = update.callback_query
    await query.answer()
    
    if query.data == "cancel":
        await cancel_handler(update, context)
        return
    
    if query.data == "back":
        await go_back(update, context)
        return
    
    if query.data == "date_today":
        today = datetime.now(EKATERINBURG_TZ).strftime("%d.%m.%Y")
        context.user_data['date'] = today
        await proceed_to_status(update, context)
    
    elif query.data == "date_other":
        context.user_data['step'] = 'waiting_date'
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back")]]
        await query.edit_message_text(
            "Введите дату в формате ДД.ММ.ГГГГ\n"
            "Например: 15.06.2026\n\n"
            "❌ Для отмены нажмите /cancel",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

async def handle_date_input(update, context):
    if context.user_data.get('step') != 'waiting_date':
        return
    
    date_str = update.message.text.strip()
    if not re.match(r'^\d{2}\.\d{2}\.\d{4}$', date_str):
        await update.message.reply_text(
            "❌ Неверный формат. Введите дату в формате ДД.ММ.ГГГГ\n"
            "Например: 15.06.2026\n\n"
            "Попробуйте ещё раз:"
        )
        return
    
    try:
        input_date = datetime.strptime(date_str, "%d.%m.%Y").date()
        today = datetime.now(EKATERINBURG_TZ).date()
        
        if input_date > today:
            await update.message.reply_text(
                f"❌ Дата {date_str} находится в будущем.\n"
                f"Сегодня: {today.strftime('%d.%m.%Y')}\n\n"
                "Пожалуйста, введите корректную дату (сегодня или ранее):"
            )
            return
        
        context.user_data['date'] = date_str
        await proceed_to_status(update, context)
    except ValueError:
        await update.message.reply_text("❌ Неверная дата. Проверьте день и месяц.\n\nПопробуйте ещё раз:")

async def proceed_to_status(update, context):
    context.user_data['step'] = 'status'
    
    keyboard = [[InlineKeyboardButton(s, callback_data=f"status_{s}")] for s in STATUS_OPTIONS]
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back")])
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
    
    text = f"📋 Заявка: {context.user_data['order_id']} - {context.user_data['order_client']} - {context.user_data['order_address']}\n📅 Дата поступления: {context.user_data['receipt_date']}\n📅 Дата выполнения: {context.user_data['date']}\n\nУкажите статус заявки:"
    
    if hasattr(update, 'callback_query') and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def status_callback(update, context):
    query = update.callback_query
    if not query:
        await update.message.reply_text("❌ Ошибка: не удалось обработать выбор.")
        return
    
    await query.answer()
    
    if query.data == "cancel":
        await cancel_handler(update, context)
        return
    
    if query.data == "back":
        await go_back(update, context)
        return
    
    status = query.data.split('_')[1]
    context.user_data['status'] = status
    
    if status == "✅ Выполнена":
        context.user_data['step'] = 'payment'
        keyboard = []
        for value, label in PAYMENT_OPTIONS:
            keyboard.append([InlineKeyboardButton(label, callback_data=f"payment_{value}")])
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back")])
        keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
        await query.edit_message_text(
            f"📋 Заявка: {context.user_data['order_id']} - {context.user_data['order_client']} - {context.user_data['order_address']}\n"
            f"📅 Дата поступления: {context.user_data['receipt_date']}\n"
            f"📅 Дата выполнения: {context.user_data['date']}\n"
            f"📌 Статус: {status}\n\n"
            f"Кто получил оплату?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        # Отказ или Перенаправлена
        context.user_data['step'] = 'delivery'
        context.user_data['payment_type'] = ""
        context.user_data['payment_type_display'] = "—"
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back")]]
        await query.edit_message_text(
            "Введите сумму выезда/доставки (только цифры):",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

async def payment_callback(update, context):
    query = update.callback_query
    await query.answer()
    
    if query.data == "cancel":
        await cancel_handler(update, context)
        return
    
    if query.data == "back":
        await go_back(update, context)
        return
    
    payment_value = query.data.split('_')[1]
    
    if payment_value == "individual":
        context.user_data['payment_type'] = "Ф"
        context.user_data['payment_type_display'] = "💵 Оплату получил я"
    else:
        context.user_data['payment_type'] = "Ю"
        context.user_data['payment_type_display'] = "🏢 Оплату получила организация"
    
    context.user_data['step'] = 'cost'
    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back")]]
    await query.edit_message_text(
        "Введите сумму заказа (только цифры):",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_text(update, context):
    step = context.user_data.get('step')
    
    if step == 'waiting_date':
        await handle_date_input(update, context)
        return
    
    if step == 'cost':
        try:
            cost = int(update.message.text.strip())
            if cost < 0:
                raise ValueError
            context.user_data['cost'] = cost
            context.user_data['step'] = 'delivery'
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back")]]
            await update.message.reply_text(
                "Введите сумму выезда/доставки (только цифры):",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except ValueError:
            await update.message.reply_text("❌ Введите неотрицательное число. Попробуйте ещё раз:")
    
    elif step == 'delivery':
        try:
            delivery = int(update.message.text.strip())
            if delivery < 0:
                raise ValueError
            context.user_data['delivery'] = delivery
            
            if context.user_data.get('status') == "✅ Выполнена":
                context.user_data['step'] = 'expense'
                keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back")]]
                await update.message.reply_text(
                    "Введите расходы (только цифры):",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            else:
                context.user_data['cost'] = delivery
                context.user_data['expense'] = 0
                await proceed_to_comment(update, context)
        except ValueError:
            await update.message.reply_text("❌ Введите неотрицательное число. Попробуйте ещё раз:")
    
    elif step == 'expense':
        try:
            expense = int(update.message.text.strip())
            if expense < 0:
                raise ValueError
            context.user_data['expense'] = expense
            await proceed_to_comment(update, context)
        except ValueError:
            await update.message.reply_text("❌ Введите неотрицательное число. Попробуйте ещё раз:")
    
    elif step == 'waiting_comment':
        comment = update.message.text.strip()
        
        status = context.user_data.get('status')
        is_required = status != "✅ Выполнена"
        
        if is_required and not comment:
            await update.message.reply_text(
                "❌ Комментарий обязателен для статуса «Отказ» или «Перенаправлена».\n"
                "Пожалуйста, введите причину:"
            )
            return
        
        context.user_data['comment'] = comment
        await show_confirmation(update, context)
    
    else:
        await update.message.reply_text("Начните с /start")

async def proceed_to_comment(update, context):
    status = context.user_data.get('status')
    is_required = status != "✅ Выполнена"
    
    context.user_data['step'] = 'waiting_comment'
    
    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back")]]
    
    if is_required:
        prompt = (
            "💬 Введите комментарий (обязательно):\n\n"
            f"Если выбран статус «{status}», необходимо ввести причину."
        )
    else:
        prompt = (
            "💬 Введите комментарий (необязательно):\n\n"
            "Дополнительная информация о заявке (обратная связь от клиента / какая-либо иная важная информация)\n\n"
            "Если не хотите оставлять комментарий, просто нажмите /skip"
        )
    
    if hasattr(update, 'callback_query') and update.callback_query:
        await update.callback_query.edit_message_text(prompt, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(prompt, reply_markup=InlineKeyboardMarkup(keyboard))

async def skip_comment(update, context):
    if context.user_data.get('step') != 'waiting_comment':
        await update.message.reply_text("Начните с /start")
        return
    
    status = context.user_data.get('status')
    is_required = status != "✅ Выполнена"
    
    if is_required:
        await update.message.reply_text("❌ Комментарий обязателен для этого статуса. Введите комментарий:")
        return
    
    context.user_data['comment'] = ""
    await show_confirmation(update, context)

async def show_confirmation(update, context):
    data = context.user_data
    status = data['status']
    comment = data.get('comment', '')
    comment_display = comment if comment else "—"
    payment_display = data.get('payment_type_display', '—')
    
    text = (
        f"📋 **Проверьте данные:**\n\n"
        f"Заявка: {data['order_id']} - {data['order_client']} - {data['order_address']}\n"
        f"📅 Дата поступления: {data['receipt_date']}\n"
        f"📅 Дата выполнения: {data['date']}\n"
        f"📌 Статус: {status}\n"
    )
    
    if status == "✅ Выполнена":
        text += f"💳 Тип оплаты: {payment_display}\n"
    
    text += (
        f"💰 Общая сумма заказа: {data['cost']} руб\n"
        f"   Из них: выезд/доставка {data['delivery']} руб, расходы {data['expense']} руб\n"
        f"💬 Комментарий: {comment_display}\n\n"
        f"Всё верно?"
    )
    
    keyboard = [
        [InlineKeyboardButton("✅ Да, всё верно", callback_data="confirm_yes")],
        [InlineKeyboardButton("✏️ Нет, заполнить заново", callback_data="confirm_no")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back")],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel")]
    ]
    
    context.user_data['step'] = 'confirm'
    await update.message.reply_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

async def confirm_callback(update, context):
    query = update.callback_query
    await query.answer()
    
    if query.data == "cancel":
        await cancel_handler(update, context)
        return
    
    if query.data == "back":
        await go_back(update, context)
        return
    
    if query.data == "confirm_no":
        context.user_data['step'] = 'status'
        keyboard = [[InlineKeyboardButton(s, callback_data=f"status_{s}")] for s in STATUS_OPTIONS]
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back")])
        keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
        await query.edit_message_text(
            f"📋 Заявка: {context.user_data['order_id']} - {context.user_data['order_client']} - {context.user_data['order_address']}\n📅 Дата поступления: {context.user_data['receipt_date']}\n📅 Дата выполнения: {context.user_data['date']}\n\nУкажите статус заявки:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    
    # confirm_yes
    data = context.user_data
    sheet_name = context.user_data['sheet_name']
    row = data['row']
    status_value = data['status'].replace('✅ ', '').replace('❌ ', '').replace('🔄 ', '')
    
    # Записываем в лист мастера
    update_order(sheet_name, row, {
        'cost': data['cost'],
        'delivery': data['delivery'],
        'expense': data['expense'],
        'status': status_value,
        'date': data['date'],
        'comment': data.get('comment', ''),
        'payment_type': data.get('payment_type', '')
    })
    
    # ========== ОБРАБОТКА РАЗНЫХ СТАТУСОВ ==========
    
    # 1. Если статус "Перенаправлена" - выполняем полную логику перенаправления
    if data['status'] == "🔄 Перенаправлена":
        await redirect_order(data, sheet_name)
    
    # 2. Если статус "Выполнена" или "Отказ" - добавляем комментарий в общий пул и отправляем лог
    else:
        # Добавляем комментарий в "Общий пул заявок"
        if data['status'] == "✅ Выполнена":
            add_comment_to_general_pool(data['order_id'], data.get('comment', ''), "Выполнена")
            # Отправляем в группу логов
            master_name = context.user_data.get('user_name', 'Неизвестно')
            await send_log_message(data['order_id'], master_name, "заявка выполнена")
            
        elif data['status'] == "❌ Отказ":
            add_comment_to_general_pool(data['order_id'], data.get('comment', ''), "Отказ")
            # Отправляем в группу логов
            master_name = context.user_data.get('user_name', 'Неизвестно')
            await send_log_message(data['order_id'], master_name, "заявка отклонена")
    
    # ========== ОТПРАВКА ОТЧЕТОВ В ЧАТЫ ==========
    user_id = update.effective_user.id
    chat_id = USERS.get(user_id, {}).get('chat_id')
    if chat_id:
        report_text = (
            f"👤 Мастер: {context.user_data.get('user_name', 'Неизвестно')}\n\n"
            f"<b>Дата выполнения:</b> {data['date']}\n\n"
            f"ID заявки: {data['order_id']}\n\n"
            f"Адрес: {data['order_address']}\n"
            f"Клиент: {data['order_client']}\n\n"
            f"<b>Общая сумма:</b> <b>{data['cost']} руб</b>\n"
            f"<b>Выезд:</b> <b>{data['delivery']} руб</b>\n"
            f"<b>Расходы:</b> <b>{data['expense']} руб</b>\n\n"
            f"<b>Комментарий к заявке:</b> <i>{data.get('comment', '—')}</i>\n\n"
        )
        
        if data.get('status') == "✅ Выполнена":
            report_text += f"<b>Способ оплаты:</b> {data.get('payment_type_display', '—')}"
        
        try:
            await context.bot.send_message(chat_id=chat_id, text=report_text, parse_mode='HTML')
        except Exception as e:
            print(f"⚠️ Не удалось отправить сообщение в чат {chat_id}: {e}")
    
    # ========== УСПЕШНОЕ СООБЩЕНИЕ МАСТЕРУ ==========
    success_message = (
        f"✅ Вы сохранили отчёт по заявке:\n"
        f"📋 ID: {data['order_id']}\n"
        f"🏢 Клиент: {data['order_client']}\n"
        f"📍 Адрес: {data['order_address']}\n\n"
        f"📅 Дата поступления: {data['receipt_date']}\n"
        f"📅 Дата выполнения: {data['date']}\n"
        f"📌 Статус: {data['status']}\n"
    )
    
    if data.get('status') == "✅ Выполнена":
        success_message += f"💳 Тип оплаты: {data.get('payment_type_display', '—')}\n"
    
    success_message += (
        f"💰 Общая сумма заказа: {data['cost']} руб\n"
        f"   Из них: выезд/доставка {data['delivery']} руб, расходы {data['expense']} руб\n"
        f"💬 Комментарий: {data.get('comment', '—')}"
    )
    
    if data['status'] == "🔄 Перенаправлена":
        success_message += "\n\n🔄 Заявка отправлена на перенаправление."
    
    await query.edit_message_text(success_message)
    
    await asyncio.sleep(2)
    
    context.user_data.clear()
    
    user_id = update.effective_user.id
    if user_id in USERS:
        context.user_data['user_id'] = user_id
        context.user_data['sheet_name'] = USERS[user_id]['sheet']
        context.user_data['user_name'] = USERS[user_id]['name']
    
    keyboard = [[InlineKeyboardButton("📋 Создать новый отчёт", callback_data="new_report")]]
    await query.edit_message_text(
        "Что дальше?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ========== ВЕБХУК ==========
@flask_app.route('/webhook', methods=['POST'])
def webhook():
    global telegram_app, main_loop
    try:
        data = request.get_json()
        update = Update.de_json(data, telegram_app.bot)
        asyncio.run_coroutine_threadsafe(
            telegram_app.process_update(update),
            main_loop
        )
        return "OK", 200
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        return "Internal Server Error", 500

@flask_app.route('/')
def home():
    return "Бот работает", 200

# ========== ЗАПУСК ==========
def run_webhook():
    global telegram_app, main_loop
    
    telegram_app = Application.builder().token(TOKEN).build()
    
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CommandHandler("cancel", cancel_handler))
    telegram_app.add_handler(CommandHandler("skip", skip_comment))
    telegram_app.add_handler(CallbackQueryHandler(new_report_callback, pattern="^new_report$"))
    telegram_app.add_handler(CallbackQueryHandler(check_orders_callback, pattern="^check_orders$"))
    telegram_app.add_handler(CallbackQueryHandler(cancel_handler, pattern="^cancel$"))
    telegram_app.add_handler(CallbackQueryHandler(go_back, pattern="^back$"))
    telegram_app.add_handler(CallbackQueryHandler(select_order_callback, pattern="^order_"))
    telegram_app.add_handler(CallbackQueryHandler(date_callback, pattern="^date_"))
    telegram_app.add_handler(CallbackQueryHandler(payment_callback, pattern="^payment_"))
    telegram_app.add_handler(CallbackQueryHandler(status_callback, pattern="^status_"))
    telegram_app.add_handler(CallbackQueryHandler(confirm_callback, pattern="^confirm_"))
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    main_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(main_loop)
    main_loop.run_until_complete(telegram_app.initialize())
    main_loop.run_until_complete(telegram_app.start())
    
    port = int(os.environ.get("PORT", 8080))
    print(f"✅ Бот запущен на порту {port}")
    
    def run_flask():
        flask_app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
    
    import threading
    threading.Thread(target=run_flask, daemon=True).start()
    
    main_loop.run_forever()

if __name__ == '__main__':
    run_webhook()
