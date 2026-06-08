async def go_back(update, context):
    query = update.callback_query
    await query.answer()
    
    step = context.user_data.get('step')
    
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
        # Возврат к комментарию
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
        
        # Здесь используем query.edit_message_text, так как это callback
        await query.edit_message_text(prompt, reply_markup=InlineKeyboardMarkup(keyboard))
    
    else:
        await query.edit_message_text("❌ Нельзя вернуться назад. Начните с /start")
