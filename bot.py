import telebot
from telebot import types

# === НАСТРОЙКИ ===
TOKEN = 'ВСТАВЬТЕ_СВОЙ_ТОКЕН_ЗДЕСЬ'
GROUP_ID = -1001234567890123   # ID вашей приватной группы

MAX_FILE_SIZE_MB = 50          # ← Максимальный размер файла в МБ (можно изменить)

bot = telebot.TeleBot(TOKEN)

# Хранилище заказов
orders = {}

# ====================== УЛУЧШЕННАЯ ПРОВЕРКА STL ======================
def get_stl_info(data: bytes):
    """Возвращает (is_valid, num_triangles, is_binary)"""
    if len(data) < 84:
        return False, 0, False

    file_size = len(data)

    # ASCII STL
    try:
        header = data[:200].decode('ascii', errors='ignore').strip().lower()
        if header.startswith('solid'):
            if any(kw in header for kw in ['facet normal', 'vertex', 'endfacet']):
                return True, 0, False  # ASCII — точное число треугольников не считаем
    except:
        pass

    # Binary STL
    try:
        num_triangles = int.from_bytes(data[80:84], byteorder='little')

        if num_triangles < 1 or num_triangles > 100_000_000:
            return False, 0, True

        expected_size = 84 + num_triangles * 50
        size_diff = abs(file_size - expected_size)

        if size_diff <= 500 or (file_size >= expected_size - 200 and file_size <= expected_size + 1000):
            return True, num_triangles, True
    except:
        pass

    return False, 0, True


def is_valid_obj(data: bytes) -> bool:
    try:
        text = data.decode('utf-8', errors='ignore').lower()
        if not text:
            return False
        lines = text.splitlines()[:150]
        has_vertex = any(line.startswith('v ') for line in lines)
        has_face = any(line.startswith('f ') for line in lines)
        return has_vertex and has_face
    except:
        return False
# =====================================================================

@bot.message_handler(commands=['start'])
def start(message):
    bot.send_message(message.chat.id,
        f"👋 Привет! Бот фермы 3D-печати.\n\n"
        f"Пришлите файл **.stl** или **.obj** (до {MAX_FILE_SIZE_MB} МБ).\n"
        "Мы проверяем реальное содержимое + показываем количество треугольников.")

@bot.message_handler(content_types=['document'])
def handle_document(message):
    if not message.document:
        return

    filename = (message.document.file_name or '').lower()
    extension = filename.split('.')[-1] if '.' in filename else ''

    if extension not in ('stl', 'obj'):
        bot.reply_to(message, "❌ Принимаем только файлы **.stl** и **.obj**.")
        return

    # Проверка размера файла
    file_size_mb = message.document.file_size / (1024 * 1024)
    if file_size_mb > MAX_FILE_SIZE_MB:
        bot.reply_to(message,
            f"❌ Файл слишком большой ({file_size_mb:.1f} МБ).\n"
            f"Максимальный размер — {MAX_FILE_SIZE_MB} МБ.\n"
            "Уменьшите разрешение модели и попробуйте снова.")
        return

    # Скачиваем файл
    try:
        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)

        is_valid = False
        triangles = 0
        file_type = extension.upper()

        if extension == 'stl':
            is_valid, triangles, is_binary = get_stl_info(downloaded_file)
        elif extension == 'obj':
            is_valid = is_valid_obj(downloaded_file)

        if not is_valid:
            bot.reply_to(message,
                f"❌ Файл **{message.document.file_name}** не является корректным {file_type}-файлом.\n"
                "Проверьте модель в Blender / Meshmixer и экспортируйте заново.")
            return

    except Exception as e:
        bot.reply_to(message, "⚠️ Ошибка при проверке файла. Попробуйте ещё раз.")
        print(f"Ошибка: {e}")
        return

    # === Файл прошёл проверку ===
    forwarded = bot.forward_message(GROUP_ID, message.chat.id, message.message_id)

    user = message.from_user
    username = f"@{user.username}" if user.username else "без username"
    description = message.caption or "Описание не добавлено"

    # Формируем информацию о треугольниках
    tri_info = ""
    if extension == 'stl':
        if triangles > 0:
            tri_info = f"📊 Треугольников: {triangles:,}"
        else:
            tri_info = "📊 Формат: ASCII STL"

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
        f"📎 Файл: {message.document.file_name} ✅\n"
        f"{tri_info}"
    )

    bot.send_message(GROUP_ID, notification, parse_mode='Markdown', reply_markup=markup)

    orders[forwarded.message_id] = {
        'client_chat_id': message.chat.id,
        'client_message_id': message.message_id,
        'filename': message.document.file_name,
        'description': description
    }

    reply_text = f"✅ Заказ принят!\nФайл **{file_type}** проверен."
    if triangles > 0:
        reply_text += f"\n📊 Треугольников: {triangles:,}"
    bot.reply_to(message, reply_text)

# ==================== Кнопки и ответы команды (без изменений) ====================
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

print("🚀 Бот с ограничением размера + количеством треугольников запущен...")
bot.infinity_polling()