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
SHEET_NAME = "Сергей Олегович"

# Часовой пояс Екатеринбург (UTC+5)
EKATERINBURG_TZ = timezone(timedelta(hours=5))

flask_app = Flask(__name__)
telegram_app = None
main_loop = None

STATUS_OPTIONS = ["✅ Выполнена", "❌ Отказ", "🔄 Перенаправлена"]

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

def update_order(row, data):
    sheet = get_worksheet()
    sheet.update(values=[[data['cost']]], range_name=f'G{row}')
    sheet.update(values=[[data['delivery']]], range_name=f'H{row}')
    sheet.update(values=[[data['expense']]], range_name=f'I{row}')
    sheet.update(values=[[data['status']]], range_name=f'O{row}')
    sheet.update(values=[[data['date']]], range_name=f'D{row}')
    sheet.update(values=[[data['comment']]], range_name=f'P{row}')

# ========== КОМАНДЫ ==========
async def start(update, context):
    keyboard = [[InlineKeyboardButton("📋 Создать отчёт", callback_data="new_report")]]
    await update.message.reply_text(
        "👋 Здравствуйте! Для создания отчёта нажмите на кнопку:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def show_orders_or_empty(update, context, message_prefix=None):
    orders = get_available_orders()
    
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
    
    # Убеждаемся, что данные заявки не потеряны
    if 'order_id' not in context.user_data:
        await query.edit_message_text("❌ Ошибка: данные заявки утеряны. Начните с /start")
        context.user_data.clear()
        return
    
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
            f"📋 Заявка: {context.user_data['order_id']} - {context.user_data['order_client']} - {context.user_data['order_address']}\n\nУкажите дату выполнения:",
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
            f"📋 Заявка: {context.user_data['order_id']} - {context.user_data['order_client']} - {context.user_data['order_address']}\n\nУкажите дату выполнения:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    elif step in ['cost', 'delivery', 'expense']:
        context.user_data['step'] = 'status'
        keyboard = [[InlineKeyboardButton(s, callback_data=f"status_{s}")] for s in STATUS_OPTIONS]
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back")])
        keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
        
        await query.edit_message_text(
            f"📋 Заявка: {context.user_data['order_id']} - {context.user_data['order_client']} - {context.user_data['order_address']}\n📅 Дата: {context.user_data['date']}\n\nУкажите статус заявки:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    elif step == 'waiting_comment':
        status = context.user_data.get('status')
        if status == "✅ Выполнена":
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
    row = int(query.data.split('_')[1])
    
    orders = get_available_orders()
    order = next((o for o in orders if o['row'] == row), None)
    if not order:
        await query.edit_message_text("❌ Ошибка: заявка не найдена. Возможно, статус изменился.")
        return
    
    context.user_data['row'] = row
    context.user_data['order_id'] = order['id']
    context.user_data['order_client'] = order['client']
    context.user_data['order_address'] = order['address']
    context.user_data['step'] = 'date'
    
    keyboard = [
        [InlineKeyboardButton("📅 Сегодня", callback_data="date_today")],
        [InlineKeyboardButton("📆 Указать другую дату", callback_data="date_other")]
    ]
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back")])
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
    
    await query.edit_message_text(
        f"📋 Заявка: {order['id']} - {order['client']} - {order['address']}\n\nУкажите дату выполнения:",
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
    
    text = f"📋 Заявка: {context.user_data['order_id']} - {context.user_data['order_client']} - {context.user_data['order_address']}\n📅 Дата: {context.user_data['date']}\n\nУкажите статус заявки:"
    
    if hasattr(update, 'callback_query') and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def status_callback(update, context):
    query = update.callback_query
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
        context.user_data['step'] = 'cost'
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back")]]
        await query.edit_message_text(
            "Введите сумму заказа (только цифры):",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        context.user_data['step'] = 'delivery'
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back")]]
        await query.edit_message_text(
            "Введите сумму выезда/доставки (только цифры):",
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
    
    text = (
        f"📋 **Проверьте данные:**\n\n"
        f"Заявка: {data['order_id']} - {data['order_client']} - {data['order_address']}\n"
        f"📅 Дата: {data['date']}\n"
        f"📌 Статус: {status}\n"
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
        keyboard = [[InlineKeyboardButton(s, callback_data=f"status_{s}")] for s in STATUS_OPTIONS]
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back")])
        keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
        await query.edit_message_text(
            f"📋 Заявка: {context.user_data['order_id']} - {context.user_data['order_client']} - {context.user_data['order_address']}\n📅 Дата: {context.user_data['date']}\n\nУкажите статус заявки:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        context.user_data['step'] = 'status'
        return
    
    # confirm_yes
    data = context.user_data
    row = data['row']
    status_value = data['status'].replace('✅ ', '').replace('❌ ', '').replace('🔄 ', '')
    
    update_order(row, {
        'cost': data['cost'],
        'delivery': data['delivery'],
        'expense': data['expense'],
        'status': status_value,
        'date': data['date'],
        'comment': data.get('comment', '')
    })
    
    success_message = (
        f"✅ Вы сохранили отчёт по заявке:\n"
        f"📋 ID: {data['order_id']}\n"
        f"🏢 Клиент: {data['order_client']}\n"
        f"📍 Адрес: {data['order_address']}\n\n"
        f"📅 Дата: {data['date']}\n"
        f"📌 Статус: {data['status']}\n"
        f"💰 Общая сумма заказа: {data['cost']} руб\n"
        f"   Из них: выезд/доставка {data['delivery']} руб, расходы {data['expense']} руб\n"
        f"💬 Комментарий: {data.get('comment', '—')}"
    )
    await query.edit_message_text(success_message)
    
    await asyncio.sleep(2)
    
    context.user_data.clear()
    
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
    telegram_app.add_handler(CallbackQueryHandler(select_order_callback, pattern="^order_"))
    telegram_app.add_handler(CallbackQueryHandler(date_callback, pattern="^date_"))
    telegram_app.add_handler(CallbackQueryHandler(status_callback, pattern="^status_"))
    telegram_app.add_handler(CallbackQueryHandler(confirm_callback, pattern="^confirm_"))
    telegram_app.add_handler(CallbackQueryHandler(go_back, pattern="^back$"))
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
