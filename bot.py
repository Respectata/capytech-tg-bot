import os
import json
import datetime
import telebot
from telebot import types

# ====================== НАСТРОЙКИ ======================
TOKEN = os.getenv('TOKEN')
GROUP_ID_STR = os.getenv('GROUP_ID')
MAX_FILE_SIZE_MB = 50

if not TOKEN or not GROUP_ID_STR:
    print("❌ Ошибка: TOKEN или GROUP_ID не заданы!")
    exit(1)

GROUP_ID = int(GROUP_ID_STR)
DATA_FILE = 'orders.json'

print(f"✅ Бот запущен. GROUP_ID = {GROUP_ID}")

bot = telebot.TeleBot(TOKEN)

# ====================== ХРАНИЛИЩЕ ======================
def load_orders():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_orders(data):
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

user_orders = load_orders()
pending_orders = {}  # chat_id → данные временного заказа


# ====================== КЛАВИАТУРЫ ======================
def get_main_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    markup.add("💰 Рассчитать стоимость")
    markup.add("📋 Мои заказы")
    return markup

def get_material_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add("PLA", "PETG")
    markup.add("ABS", "TPU")
    return markup

def get_confirm_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("✅ Подтвердить заказ", callback_data="confirm_order"),
        types.InlineKeyboardButton("✏️ Изменить параметры", callback_data="edit_params")
    )
    return markup


# ====================== ПРОВЕРКА ФАЙЛОВ ======================
def get_stl_info(data: bytes):
    if len(data) < 84:
        return False, 0
    try:
        header = data[:200].decode('ascii', errors='ignore').lower()
        if header.startswith('solid') and ('facet normal' in header or 'endfacet' in header):
            return True, 0
    except:
        pass
    try:
        num = int.from_bytes(data[80:84], 'little')
        if 1 <= num <= 100_000_000:
            if abs(len(data) - (84 + num * 50)) <= 600:
                return True, num
    except:
        pass
    return False, 0

def is_valid_obj(data: bytes) -> bool:
    try:
        text = data.decode('utf-8', errors='ignore').lower()
        lines = text.splitlines()[:150]
        return any(l.startswith('v ') for l in lines) and any(l.startswith('f ') for l in lines)
    except:
        return False


# ====================== СТАРТ ======================
@bot.message_handler(commands=['start'])
def start(message):
    bot.send_message(message.chat.id,
        "👋 <b>Добро пожаловать в CapyTech 3D Print!</b>\n\n"
        "Нажмите «💰 Рассчитать стоимость» и отправьте файл модели (.stl или .obj)",
        parse_mode='HTML', reply_markup=get_main_keyboard())


# ====================== ОБРАБОТКА ФАЙЛА ======================
@bot.message_handler(content_types=['document'])
def handle_document(message):
    if not message.document:
        return

    filename = (message.document.file_name or '').lower()
    if not filename.endswith(('.stl', '.obj')):
        bot.reply_to(message, "❌ Принимаем только .stl и .obj файлы.", reply_markup=get_main_keyboard())
        return

    file_size_mb = message.document.file_size / (1024 * 1024)
    if file_size_mb > MAX_FILE_SIZE_MB:
        bot.reply_to(message, f"❌ Файл слишком большой ({file_size_mb:.1f} МБ). Максимум {MAX_FILE_SIZE_MB} МБ.", 
                     reply_markup=get_main_keyboard())
        return

    try:
        file_info = bot.get_file(message.document.file_id)
        downloaded = bot.download_file(file_info.file_path)
        is_valid = False
        triangles = 0

        if filename.endswith('.stl'):
            is_valid, triangles = get_stl_info(downloaded)
        else:
            is_valid = is_valid_obj(downloaded)

        if not is_valid:
            bot.reply_to(message, "❌ Файл не является корректным STL или OBJ.", reply_markup=get_main_keyboard())
            return
    except Exception as e:
        print(f"Ошибка проверки файла: {e}")
        bot.reply_to(message, "⚠️ Ошибка при проверке файла.", reply_markup=get_main_keyboard())
        return

    # Создаём временный заказ
    pending_orders[message.chat.id] = {
        'filename': message.document.file_name,
        'description': message.caption or "Без описания",
        'triangles': triangles,
        'user_id': message.from_user.id,
        'first_name': message.from_user.first_name,
        'username': message.from_user.username,
        'step': 'material',
        'material': None,
        'first_layer_height': None,
        'perimeters': None,
        'infill': None
    }

    bot.reply_to(message,
        "✅ Файл успешно принят!\n\n"
        "<b>Шаг 1 из 4:</b> Выберите материал",
        parse_mode='HTML',
        reply_markup=get_material_keyboard()
    )


# ====================== УТОЧНЕНИЕ ПАРАМЕТРОВ ======================
@bot.message_handler(content_types=['text'])
def handle_text(message):
    text = message.text.strip()
    chat_id = message.chat.id

    if text == "💰 Рассчитать стоимость":
        bot.send_message(chat_id, "📤 Отправьте файл .stl или .obj с описанием в подписи.", 
                         reply_markup=get_main_keyboard())
        return

# ==================== КНОПКА «МОИ ЗАКАЗЫ» ====================
    if text == "📋 Мои заказы":
        orders_list = get_user_orders(chat_id)   # ваша функция из хранилища
        
        if not orders_list:
            bot.send_message(chat_id, 
                "📋 У вас пока нет заказов.\n\nОтправьте первую модель через кнопку «💰 Рассчитать стоимость».",
                reply_markup=get_main_keyboard())
            return

        response = "📋 <b>Ваши последние заказы:</b>\n\n"
        
        for order in reversed(orders_list[-10:]):   # показываем последние 10
            status_emoji = {
                "Новый": "🆕",
                "В работе": "🔄",
                "Расчёт готов": "✅",
                "Готов к выдаче": "📦",
                "Выдан": "🎉",
                "Отклонён": "❌"
            }.get(order.get('status', 'Новый'), "📋")

            params = []
            if order.get('material'): params.append(f"Мат: {order['material']}")
            if order.get('infill'): params.append(f"Заполн: {order['infill']}%")
            if order.get('perimeters'): params.append(f"Стенки: {order['perimeters']}")
            if order.get('print_time'): params.append(f"⏱ {order['print_time']}")
            if order.get('filament_weight'): params.append(f"🧪 {order['filament_weight']} г")
            if order.get('estimated_cost'): params.append(f"💰 ~{order['estimated_cost']} руб.")

            response += (
                f"{status_emoji} <b>Заказ #{order.get('order_id', '—')}</b> — {order.get('date', '')}\n"
                f"📎 {order.get('filename', '—')}\n"
                f"Статус: <b>{order.get('status', 'Новый')}</b>\n"
                f"{' | '.join(params) if params else 'Параметры не указаны'}\n\n"
            )

        bot.send_message(chat_id, response, parse_mode='HTML', reply_markup=get_main_keyboard())
        return

    # === Обработка шагов уточнения параметров ===
    if chat_id in pending_orders:
        order = pending_orders[chat_id]

        if order['step'] == 'material':
            if text in ["PLA", "PETG", "ABS", "TPU"]:
                order['material'] = text
                order['step'] = 'first_layer'
                bot.send_message(chat_id, f"✅ Материал выбран: <b>{text}</b>\n\nВведите высоту первого слоя (например: 0.2):", parse_mode='HTML')
            else:
                bot.send_message(chat_id, "Пожалуйста, выберите материал из кнопок.", reply_markup=get_material_keyboard())
            return

        elif order['step'] == 'first_layer':
            try:
                order['first_layer_height'] = float(text.replace(',', '.'))
                order['step'] = 'perimeters'
                bot.send_message(chat_id, f"✅ Высота первого слоя: <b>{order['first_layer_height']} мм</b>\n\nУкажите количество периметров (стенок):", parse_mode='HTML')
            except:
                bot.send_message(chat_id, "Введите число, например 0.2")
            return

        elif order['step'] == 'perimeters':
            try:
                order['perimeters'] = int(text)
                order['step'] = 'infill'
                bot.send_message(chat_id, f"✅ Периметров: <b>{order['perimeters']}</b>\n\nУкажите заполнение в % (0–100):", parse_mode='HTML')
            except:
                bot.send_message(chat_id, "Введите целое число")
            return

        elif order['step'] == 'infill':
            try:
                infill = int(text)
                if 0 <= infill <= 100:
                    order['infill'] = infill
                    order['step'] = 'confirm'
                    bot.send_message(chat_id,
                        "✅ Все параметры собраны!\n\nПроверьте и подтвердите заказ:",
                        parse_mode='HTML',
                        reply_markup=get_confirm_keyboard())
                else:
                    bot.send_message(chat_id, "Заполнение должно быть от 0 до 100")
            except:
                bot.send_message(chat_id, "Введите число от 0 до 100")
            return


# ====================== ОБРАБОТКА КНОПОК ======================
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    chat_id = call.message.chat.id

    # Кнопки подтверждения заказа
    if call.data == "confirm_order" and chat_id in pending_orders:
        order = pending_orders.pop(chat_id)

        final_order = {
            'order_id': int(datetime.datetime.now().timestamp()),
            'filename': order['filename'],
            'description': order['description'],
            'triangles': order.get('triangles', 0),
            'material': order['material'],
            'first_layer_height': order.get('first_layer_height'),
            'perimeters': order.get('perimeters'),
            'infill': order.get('infill'),
            'user_id': order['user_id'],
            'first_name': order['first_name'],
            'username': order.get('username'),
            'status': 'Новый',
            'date': datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
        }

        # Сохраняем заказ
        user_id_str = str(chat_id)
        if user_id_str not in user_orders:
            user_orders[user_id_str] = []
        user_orders[user_id_str].append(final_order)
        save_orders(user_orders)

        # Уведомление в группу
        user_link = f"<a href='tg://user?id={order['user_id']}'>{order['first_name']}</a>"
        if order.get('username'):
            user_link += f" (@{order['username']})"

        params_text = (
            f"Материал: {order['material']}\n"
            f"Первый слой: {order.get('first_layer_height')} мм\n"
            f"Периметры: {order.get('perimeters')}\n"
            f"Заполнение: {order.get('infill')}%"
        )

        notification = (
            f"📦 <b>Новый заказ #{final_order['order_id']}</b>\n\n"
            f"👤 {user_link}\n"
            f"📎 {order['filename']}\n"
            f"📝 {order['description']}\n"
            f"📊 Треугольников: {order.get('triangles', 0):,}\n\n"
            f"<b>Параметры:</b>\n{params_text}\n"
            f"Статус: <b>Новый</b>"
        )

        team_markup = types.InlineKeyboardMarkup(row_width=2)
        team_markup.add(
            types.InlineKeyboardButton("✅ Принять заказ", callback_data=f"team_accept_{final_order['order_id']}"),
            types.InlineKeyboardButton("💰 Рассчитать", callback_data=f"team_calc_{final_order['order_id']}"),
            types.InlineKeyboardButton("❌ Отклонить", callback_data=f"team_reject_{final_order['order_id']}")
        )

        bot.send_message(GROUP_ID, notification, parse_mode='HTML', reply_markup=team_markup)

        bot.send_message(chat_id, 
            "✅ Заказ успешно отправлен в работу!\nНаша команда уже видит все параметры и скоро пришлёт расчёт.",
            reply_markup=get_main_keyboard())

    elif call.data == "edit_params" and chat_id in pending_orders:
        # Сброс параметров и возврат к первому шагу
        pending_orders[chat_id]['step'] = 'material'
        bot.send_message(chat_id, "✏️ Параметры сброшены.\nВыберите материал заново:", 
                         reply_markup=get_material_keyboard())

    # Кнопки команды в группе
    elif call.data.startswith("team_"):
        parts = call.data.split('_')
        action = parts[1]
        order_id = int(parts[2])

        # Поиск заказа и клиента
        client_chat_id = None
        target_order = None
        for uid, ord_list in user_orders.items():
            for o in ord_list:
                if o.get('order_id') == order_id:
                    client_chat_id = int(uid)
                    target_order = o
                    break
            if client_chat_id:
                break

        if not client_chat_id:
            bot.answer_callback_query(call.id, "Заказ не найден")
            return

        if action == "accept":
            target_order['status'] = "В работе"
            save_orders(user_orders)
            bot.answer_callback_query(call.id, "Заказ принят в работу")
            bot.send_message(GROUP_ID, f"✅ Заказ #{order_id} принят в работу")
            bot.send_message(client_chat_id, f"✅ Ваш заказ #{order_id} принят в работу!", parse_mode='HTML')

        elif action == "calc":
            bot.answer_callback_query(call.id)
            bot.send_message(GROUP_ID,
                f"💰 Расчёт стоимости для заказа #{order_id}\n\n"
                "Напишите ответ reply на это сообщение:",
                parse_mode='Markdown',
                reply_to_message_id=call.message.message_id)
            target_order['waiting_calc'] = True

        elif action == "reject":
            bot.answer_callback_query(call.id)
            bot.send_message(GROUP_ID, f"❌ Напишите причину отклонения заказа #{order_id}:", 
                             reply_to_message_id=call.message.message_id)
            target_order['waiting_reject'] = True

    bot.answer_callback_query(call.id)


print("🚀 Бот запущен!")
bot.infinity_polling()
