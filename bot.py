import telebot
from telebot import types

# === НАСТРОЙКИ ===
TOKEN = 'ВСТАВЬТЕ_СВОЙ_ТОКЕН_ЗДЕСЬ'
GROUP_ID = -1001234567890123   # ID вашей приватной группы

bot = telebot.TeleBot(TOKEN)

# Хранилище заказов
orders = {}

# ====================== ПРОВЕРКА ФОРМАТА ФАЙЛОВ ======================
def is_valid_stl(data: bytes) -> bool:
    if len(data) < 84:
        return False
    
    # 1. ASCII STL
    try:
        header = data[:80].decode('ascii', errors='ignore').strip().lower()
        if header.startswith('solid'):
            # Дополнительная проверка на наличие треугольников
            return b'facet normal' in data or b'endfacet' in data
    except:
        pass
    
    # 2. Binary STL
    try:
        num_triangles = int.from_bytes(data[80:84], 'little')
        expected_size = 84 + num_triangles * 50
        # Размер должен примерно совпадать (иногда бывают маленькие дополнения)
        return len(data) >= expected_size - 100 and len(data) <= expected_size + 100
    except:
        return False

def is_valid_obj(data: bytes) -> bool:
    try:
        text = data.decode('utf-8', errors='ignore').lower().strip()
        if not text:
            return False
        
        lines = text.splitlines()[:100]  # проверяем первые 100 строк
        has_vertex = any(line.startswith('v ') for line in lines)
        has_face = any(line.startswith('f ') for line in lines)
        
        return has_vertex or has_face
    except:
        return False
# =====================================================================

@bot.message_handler(commands=['start'])
def start(message):
    bot.send_message(message.chat.id,
        "👋 Привет! Это бот 3D-печати фермы.\n\n"
        "Пришлите файл модели **.stl** или **.obj** и в подписи напишите описание задачи.\n"
        "Мы проверяем не только расширение, но и содержимое файла.")

@bot.message_handler(content_types=['document'])
def handle_document(message):
    if not message.document:
        return
    
    filename = (message.document.file_name or '').lower()
    
    if not filename.endswith(('.stl', '.obj')):
        bot.reply_to(message, "❌ Принимаем только файлы **.stl** и **.obj**.")
        return

    # === СКАЧИВАЕМ И ПРОВЕРЯЕМ ФАЙЛ ===
    try:
        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        is_valid = False
        file_type = ""
        
        if filename.endswith('.stl'):
            is_valid = is_valid_stl(downloaded_file)
            file_type = "STL"
        elif filename.endswith('.obj'):
            is_valid = is_valid_obj(downloaded_file)
            file_type = "OBJ"
        
        if not is_valid:
            bot.reply_to(message,
                f"❌ Файл **{message.document.file_name}** не является корректным {file_type}-файлом.\n"
                "Пожалуйста, проверьте модель в программе (Blender, Meshmixer и т.д.) и отправьте заново.")
            return
            
    except Exception as e:
        bot.reply_to(message, "⚠️ Ошибка при проверке файла. Попробуйте отправить ещё раз.")
        print(f"Ошибка скачивания: {e}")
        return

    # === Если файл валидный — обрабатываем как раньше ===
    forwarded = bot.forward_message(GROUP_ID, message.chat.id, message.message_id)
    
    user = message.from_user
    username = f"@{user.username}" if user.username else "без username"
    description = message.caption or "Описание не добавлено"
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("✅ Принять заказ", callback_data=f"accept_{forwarded.message_id}"),
        types.InlineKeyboardButton("💰 Рассчитать", callback_data=f"calc_{forwarded.message_id}"),
        types.InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{forwarded.message_id}")
    )
    
    notification = (
        f"📦 **Новый заказ!**\n\n"
        f"👤 От: {user.first_name} {user.last_name or ''} ({username})\n"
        f"ID заказа: `{forwarded.message_id}`\n"
        f"📝 Описание: {description}\n"
        f"📎 Файл: {message.document.file_name} ✅ (проверен)"
    )
    
    bot.send_message(GROUP_ID, notification, parse_mode='Markdown', reply_markup=markup)
    
    orders[forwarded.message_id] = {
        'client_chat_id': message.chat.id,
        'client_message_id': message.message_id,
        'filename': message.document.file_name,
        'description': description
    }
    
    bot.reply_to(message, "✅ Заказ принят и проверен!\nФайл корректный. Наша команда уже видит его.")

# (остальной код — callback_handler и handle_team_reply — остаётся **точно таким же**, как в предыдущей версии)

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    if call.message.chat.id != GROUP_ID:
        return
    
    data = call.data.split('_')
    action = data[0]
    group_msg_id = int(data[1])
    
    if group_msg_id not in orders:
        bot.answer_callback_query(call.id, "Заказ уже обработан.")
        return
    
    order = orders[group_msg_id]
    client_chat_id = order['client_chat_id']
    
    if action == "accept":
        bot.answer_callback_query(call.id, "Заказ принят в работу!")
        bot.send_message(GROUP_ID, f"✅ Заказ #{group_msg_id} принят в работу @{call.from_user.username or call.from_user.first_name}")
        
    elif action == "calc":
        bot.answer_callback_query(call.id)
        msg = bot.send_message(
            GROUP_ID,
            f"💰 Расчёт для заказа #{group_msg_id}\n\n"
            "Напишите стоимость и сроки в формате:\n"
            "`Цена: 1500 руб.\nСрок: 3 дня\nКомментарий: ...`",
            parse_mode='Markdown',
            reply_to_message_id=call.message.message_id
        )
        orders[group_msg_id]['waiting_calc'] = True
        orders[group_msg_id]['calc_msg_id'] = msg.message_id
        
    elif action == "reject":
        bot.answer_callback_query(call.id)
        bot.send_message(
            GROUP_ID,
            f"❌ Укажите причину отклонения заказа #{group_msg_id} и отправьте сообщение.",
            reply_to_message_id=call.message.message_id
        )
        orders[group_msg_id]['waiting_reject'] = True

@bot.message_handler(content_types=['text'])
def handle_team_reply(message):
    if message.chat.id != GROUP_ID:
        return
    
    for msg_id, order in list(orders.items()):
        if order.get('waiting_calc') and message.reply_to_message and message.reply_to_message.message_id == order.get('calc_msg_id'):
            bot.send_message(
                order['client_chat_id'],
                f"✅ Ваш заказ принят!\n\n"
                f"📎 Модель: {order['filename']}\n"
                f"📝 Описание: {order['description']}\n\n"
                f"💰 **Расчёт от нашей команды:**\n{message.text}",
                parse_mode='Markdown'
            )
            bot.send_message(GROUP_ID, "✅ Расчёт отправлен клиенту.")
            order.pop('waiting_calc', None)
            order.pop('calc_msg_id', None)
            return
            
        elif order.get('waiting_reject'):
            bot.send_message(
                order['client_chat_id'],
                f"❌ К сожалению, ваш заказ не может быть принят.\n\nПричина:\n{message.text}",
            )
            bot.send_message(GROUP_ID, "❌ Отклонение отправлено клиенту.")
            order.pop('waiting_reject', None)
            return

print("🚀 Бот с проверкой реального формата STL/OBJ запущен...")
bot.infinity_polling()
