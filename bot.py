import os
import asyncio
import json
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ConversationHandler
import gspread
from google.oauth2.service_account import Credentials

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get('TELEGRAM_TOKEN')
if not TOKEN:
    raise ValueError("TELEGRAM_TOKEN не установлен!")

SPREADSHEET_NAME = "Indev"
SHEET_NAME = "Сергей Олегович"

flask_app = Flask(__name__)
telegram_app = None
main_loop = None

STATUS, COST, DELIVERY, EXPENSE, CONFIRM = range(5)

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

# ========== КОМАНДЫ ==========
async def start(update, context):
    keyboard = [[InlineKeyboardButton("📋 Создать отчёт", callback_data="new_report")]]
    await update.message.reply_text(
        "👋 Здравствуйте! Для создания отчёта нажмите на кнопку:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def show_orders_or_empty(update, context, message_prefix=None):
    orders = get_available_orders()
    
    if orders:
        keyboard = []
        for order in orders:
            text = f"{order['id']} - {order['client']} - {order['address']}"
            keyboard.append([InlineKeyboardButton(text, callback_data=f"order_{order['row']}")])
        keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
        
        text = "📋 Выберите заявку:"
        if message_prefix:
            text = f"{message_prefix}\n\n{text}"
        
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        keyboard = [[InlineKeyboardButton("🔄 Проверить", callback_data="check_orders")]]
        keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
        
        text = "❌ Нет доступных заявок со статусом «В работе».\nНажмите «Проверить», чтобы обновить список."
        if message_prefix:
            text = f"{message_prefix}\n\n{text}"
        
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def new_report_callback(update, context):
    query = update.callback_query
    await query.answer()
    
    # Полный сброс данных и диалога
    context.user_data.clear()
    
    # Завершаем текущий диалог, если он есть
    if context._dispatcher and context._dispatcher.has_conversation(update.effective_user.id, update.effective_chat.id):
        await context._dispatcher.conversation_handler._conversations.clear()
    
    await show_orders_or_empty(query, context)

async def check_orders_callback(update, context):
    query = update.callback_query
    await query.answer()
    await show_orders_or_empty(query, context)

async def cancel_callback(update, context):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("❌ Отменено. Для создания нового отчёта нажмите /start")
    context.user_data.clear()
    
    # Завершаем текущий диалог
    if context._dispatcher and context._dispatcher.has_conversation(update.effective_user.id, update.effective_chat.id):
        await context._dispatcher.conversation_handler._conversations.clear()
    
    return ConversationHandler.END

async def select_order_callback(update, context):
    query = update.callback_query
    await query.answer()
    
    if query.data == "cancel":
        await query.edit_message_text("❌ Отменено. Для создания нового отчёта нажмите /start")
        context.user_data.clear()
        
        if context._dispatcher and context._dispatcher.has_conversation(update.effective_user.id, update.effective_chat.id):
            await context._dispatcher.conversation_handler._conversations.clear()
        
        return ConversationHandler.END
    
    # Принудительно завершаем старый диалог перед началом нового
    context.user_data.clear()
    
    if context._dispatcher and context._dispatcher.has_conversation(update.effective_user.id, update.effective_chat.id):
        await context._dispatcher.conversation_handler._conversations.clear()
    
    row = int(query.data.split('_')[1])
    
    orders = get_available_orders()
    order = next((o for o in orders if o['row'] == row), None)
    if not order:
        await query.edit_message_text("❌ Ошибка: заявка не найдена. Возможно, статус изменился.")
        return ConversationHandler.END
    
    context.user_data['row'] = row
    context.user_data['order_id'] = order['id']
    context.user_data['order_client'] = order['client']
    context.user_data['order_address'] = order['address']
    
    keyboard = [[InlineKeyboardButton(s, callback_data=f"status_{s}")] for s in STATUS_OPTIONS]
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
    
    await query.edit_message_text(
        f"📋 Заявка: {order['id']} - {order['client']} - {order['address']}\n\nУкажите статус заявки:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return STATUS

async def status_callback(update, context):
    query = update.callback_query
    await query.answer()
    
    if query.data == "cancel":
        await query.edit_message_text("❌ Отменено. Для создания нового отчёта нажмите /start")
        context.user_data.clear()
        
        if context._dispatcher and context._dispatcher.has_conversation(update.effective_user.id, update.effective_chat.id):
            await context._dispatcher.conversation_handler._conversations.clear()
        
        return ConversationHandler.END
    
    status = query.data.split('_')[1]
    context.user_data['status'] = status
    
    if status == "✅ Выполнена":
        await query.edit_message_text("Введите сумму заказа (только цифры):")
        return COST
    else:
        await query.edit_message_text("Введите сумму выезда/доставки (только цифры):")
        return DELIVERY

async def get_cost(update, context):
    try:
        cost = int(update.message.text.strip())
        if cost < 0:
            raise ValueError
        context.user_data['cost'] = cost
        await update.message.reply_text("Введите сумму выезда/доставки (только цифры):")
        return DELIVERY
    except ValueError:
        await update.message.reply_text("❌ Введите неотрицательное число. Попробуйте ещё раз:")
        return COST

async def get_delivery(update, context):
    try:
        delivery = int(update.message.text.strip())
        if delivery < 0:
            raise ValueError
        context.user_data['delivery'] = delivery
        
        if context.user_data['status'] == "✅ Выполнена":
            await update.message.reply_text("Введите расходы (только цифры):")
            return EXPENSE
        else:
            context.user_data['cost'] = delivery
            context.user_data['expense'] = 0
            return await show_confirmation(update, context)
    except ValueError:
        await update.message.reply_text("❌ Введите неотрицательное число. Попробуйте ещё раз:")
        return DELIVERY

async def get_expense(update, context):
    try:
        expense = int(update.message.text.strip())
        if expense < 0:
            raise ValueError
        context.user_data['expense'] = expense
        return await show_confirmation(update, context)
    except ValueError:
        await update.message.reply_text("❌ Введите неотрицательное число. Попробуйте ещё раз:")
        return EXPENSE

async def show_confirmation(update, context):
    data = context.user_data
    status = data['status']
    
    text = (
        f"📋 **Проверьте данные:**\n\n"
        f"Заявка: {data['order_id']} - {data['order_client']} - {data['order_address']}\n"
        f"📌 Статус: {status}\n"
        f"💰 Общая сумма заказа: {data['cost']} руб\n"
        f"   Из них: выезд/доставка {data['delivery']} руб, расходы {data['expense']} руб\n\n"
        f"Всё верно?"
    )
    
    keyboard = [
        [InlineKeyboardButton("✅ Да, всё верно", callback_data="confirm_yes")],
        [InlineKeyboardButton("✏️ Нет, заполнить заново", callback_data="confirm_no")],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel")]
    ]
    
    await update.message.reply_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))
    return CONFIRM

async def confirm_callback(update, context):
    query = update.callback_query
    await query.answer()
    
    if query.data == "cancel":
        await query.edit_message_text("❌ Отменено. Для создания нового отчёта нажмите /start")
        context.user_data.clear()
        
        if context._dispatcher and context._dispatcher.has_conversation(update.effective_user.id, update.effective_chat.id):
            await context._dispatcher.conversation_handler._conversations.clear()
        
        return ConversationHandler.END
    
    if query.data == "confirm_no":
        keyboard = [[InlineKeyboardButton(s, callback_data=f"status_{s}")] for s in STATUS_OPTIONS]
        keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
        
        await query.edit_message_text(
            f"📋 Заявка: {context.user_data['order_id']} - {context.user_data['order_client']} - {context.user_data['order_address']}\n\nУкажите статус заявки:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return STATUS
    
    # confirm_yes
    data = context.user_data
    row = data['row']
    
    status_value = data['status'].replace('✅ ', '').replace('❌ ', '').replace('🔄 ', '')
    
    update_order(row, {
        'cost': data['cost'],
        'delivery': data['delivery'],
        'expense': data['expense'],
        'status': status_value
    })
    
    success_message = (
        f"✅ Вы сохранили отчёт по заявке:\n"
        f"📋 ID: {data['order_id']}\n"
        f"🏢 Клиент: {data['order_client']}\n"
        f"📍 Адрес: {data['order_address']}\n\n"
        f"📌 Статус: {data['status']}\n"
        f"💰 Общая сумма заказа: {data['cost']} руб\n"
        f"   Из них: выезд/доставка {data['delivery']} руб, расходы {data['expense']} руб"
    )
    
    await query.edit_message_text(success_message)
    
    context.user_data.clear()
    
    if context._dispatcher and context._dispatcher.has_conversation(update.effective_user.id, update.effective_chat.id):
        await context._dispatcher.conversation_handler._conversations.clear()
    
    await show_orders_or_empty(query, context, "📊 Отчёт сохранён!")
    
    return ConversationHandler.END

async def cancel(update, context):
    context.user_data.clear()
    await update.message.reply_text("❌ Отменено. Для создания нового отчёта нажмите /start")
    return ConversationHandler.END

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
    
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(select_order_callback, pattern="^order_")],
        states={
            STATUS: [CallbackQueryHandler(status_callback, pattern="^(status_|cancel)")],
            COST: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_cost)],
            DELIVERY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_delivery)],
            EXPENSE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_expense)],
            CONFIRM: [CallbackQueryHandler(confirm_callback, pattern="^(confirm_yes|confirm_no|cancel)")],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CallbackQueryHandler(new_report_callback, pattern="^new_report$"))
    telegram_app.add_handler(CallbackQueryHandler(check_orders_callback, pattern="^check_orders$"))
    telegram_app.add_handler(CallbackQueryHandler(cancel_callback, pattern="^cancel$"))
    telegram_app.add_handler(conv_handler)
    
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
