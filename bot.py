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
pending_orders = {}  # chat_id → временный заказ


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
    if len(data) < 84: return False, 0
    try:
        header = data[:200].decode('ascii', errors='ignore').lower()
        if header.startswith('solid') and ('facet normal' in header or 'endfacet' in header):
            return True, 0
    except: pass
    try:
        num = int.from_bytes(data[80:84], 'little')
        if 1 <= num <= 100_000_000:
            if abs(len(data) - (84 + num * 50)) <= 600:
                return True, num
    except: pass
    return False, 0

def is_valid_obj(data: bytes) -> bool:
    try:
        text = data.decode('utf-8', errors='ignore').lower()
        lines = text.splitlines()[:150]
        return any(line.startswith('v ') for line in lines) and any(line.startswith('f ') for line in lines)
    except:
        return False


# ====================== СТАРТ ======================
@bot.message_handler(commands=['start'])
def start(message):
    bot.send_message(message.chat.id,
        "👋 <b>Добро пожаловать в CapyTech 3D Print!</b>\n\n"
        "Нажмите «💰 Рассчитать стоимость» и отправьте файл .stl или .obj",
        parse_mode='HTML', reply_markup=get_main_keyboard())


# ====================== ОБРАБОТКА ФАЙЛА ======================
@bot.message_handler(content_types=['document'])
def handle_document(message):
    # ... (проверка расширения, размера, валидности STL/OBJ — как в предыдущей версии)

    if not message.document or not (message.document.file_name or '').lower().endswith(('.stl', '.obj')):
        bot.reply_to(message, "❌ Принимаем только .stl и .obj файлы.", reply_markup=get_main_keyboard())
        return

    # Проверка размера и валидности (вставьте ваш код проверки здесь)

    # Создаём pending заказ
    pending_orders[message.chat.id] = {
        'filename': message.document.file_name,
        'description': message.caption or "Без описания",
        'triangles': 0,  # обновится при проверке
        'user_id': message.from_user.id,
        'first_name': message.from_user.first_name,
        'username': message.from_user.username,
        'step': 'material'
    }

    bot.reply_to(message, "✅ Файл принят!\n\n<b>Шаг 1:</b> Выберите материал:", 
                 parse_mode='HTML', reply_markup=get_material_keyboard())


# ====================== УТОЧНЕНИЕ ПАРАМЕТРОВ ======================
@bot.message_handler(content_types=['text'])
def handle_text(message):
    text = message.text.strip()
    chat_id = message.chat.id

    if text == "💰 Рассчитать стоимость":
        bot.send_message(chat_id, "📤 Отправьте файл модели (.stl или .obj).", reply_markup=get_main_keyboard())
        return

    if text == "📋 Мои заказы":
        # Ваш код вывода заказов (как раньше)
        orders_list = user_orders.get(str(chat_id), [])
        if not orders_list:
            bot.send_message(chat_id, "📋 У вас пока нет заказов.", reply_markup=get_main_keyboard())
            return
        # ... (вывод списка заказов)
        return

    # Уточнение параметров
    if chat_id in pending_orders:
        order = pending_orders[chat_id]

        if order['step'] == 'material':
            if text in ["PLA", "PETG", "ABS", "TPU"]:
                order['material'] = text
                order['step'] = 'first_layer'
                bot.send_message(chat_id, f"✅ Материал: <b>{text}</b>\n\nВведите высоту первого слоя (например: 0.2):", parse_mode='HTML')
            return

        elif order['step'] == 'first_layer':
            try:
                order['first_layer_height'] = float(text.replace(',', '.'))
                order['step'] = 'perimeters'
                bot.send_message(chat_id, "✅ Высота первого слоя сохранена.\n\nСколько периметров (стенок)?", parse_mode='HTML')
            except:
                bot.send_message(chat_id, "Введите число, например 0.2")
            return

        elif order['step'] == 'perimeters':
            try:
                order['perimeters'] = int(text)
                order['step'] = 'infill'
                bot.send_message(chat_id, "✅ Периметры сохранены.\n\nУкажите заполнение в процентах (0-100):", parse_mode='HTML')
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
                        "✅ Параметры собраны!\n\nПодтвердите заказ или измените параметры.",
                        reply_markup=get_confirm_keyboard())
            except:
                bot.send_message(chat_id, "Введите число от 0 до 100")
            return


# ====================== КНОПКИ ПОДТВЕРЖДЕНИЯ ======================
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    if call.data == "confirm_order" and call.message.chat.id in pending_orders:
        order = pending_orders.pop(call.message.chat.id)
        
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
            'username': order['username'],
            'status': 'Новый'
        }

        add_order(call.message.chat.id, final_order)

        # Красивое уведомление в группу с ссылкой на клиента
        user_link = f"<a href='tg://user?id={order['user_id']}'>{order['first_name']}</a>"
        if order['username']:
            user_link += f" (@{order['username']})"

        params = (
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
            f"<b>Параметры:</b>\n{params}\n"
            f"Статус: <b>Новый</b>"
        )

        team_markup = types.InlineKeyboardMarkup(row_width=2)
        team_markup.add(
            types.InlineKeyboardButton("✅ Принять заказ", callback_data=f"team_accept_{final_order['order_id']}"),
            types.InlineKeyboardButton("💰 Рассчитать", callback_data=f"team_calc_{final_order['order_id']}"),
            types.InlineKeyboardButton("❌ Отклонить", callback_data=f"team_reject_{final_order['order_id']}")
        )

        bot.send_message(GROUP_ID, notification, parse_mode='HTML', reply_markup=team_markup)

        bot.send_message(call.message.chat.id, 
            "✅ Заказ успешно отправлен команде!\nМы свяжемся с расчётом в ближайшее время.",
            reply_markup=get_main_keyboard())

    elif call.data == "edit_params" and call.message.chat.id in pending_orders:
        pending_orders[call.message.chat.id]['step'] = 'material'
        bot.send_message(call.message.chat.id, "✏️ Параметры сброшены. Выберите материал заново:", 
                         reply_markup=get_material_keyboard())

    # ====================== КНОПКИ КОМАНДЫ ======================
    elif call.data.startswith("team_"):
        action, order_id_str = call.data.split('_', 1)
        order_id = int(order_id_str)

        # Находим заказ
        client_chat_id = None
        for uid, orders_list in user_orders.items():
            for o in orders_list:
                if o.get('order_id') == order_id:
                    client_chat_id = int(uid)
                    current_order = o
                    break
            if client_chat_id:
                break

        if not client_chat_id:
            bot.answer_callback_query(call.id, "Заказ не найден")
            return

        if action == "team_accept":
            current_order['status'] = "В работе"
            save_orders(user_orders)
            bot.answer_callback_query(call.id, "Заказ принят в работу")
            bot.send_message(GROUP_ID, f"✅ Заказ #{order_id} принят в работу @{call.from_user.username}")
            bot.send_message(client_chat_id, f"✅ Ваш заказ #{order_id} принят в работу!", parse_mode='HTML')

        elif action == "team_calc":
            bot.answer_callback_query(call.id)
            bot.send_message(GROUP_ID,
                f"💰 Расчёт для заказа #{order_id}\n\n"
                "Напишите расчёт reply на это сообщение в формате:\n"
                "Цена: XXX руб.\nСрок: X дней\nКомментарий...",
                parse_mode='Markdown',
                reply_to_message_id=call.message.message_id)
            current_order['waiting_calc'] = True

        elif action == "team_reject":
            bot.answer_callback_query(call.id)
            bot.send_message(GROUP_ID, f"❌ Укажите причину отклонения заказа #{order_id}:", 
                             reply_to_message_id=call.message.message_id)
            current_order['waiting_reject'] = True

    bot.answer_callback_query(call.id)


# ====================== ОТВЕТЫ КОМАНДЫ (reply) ======================
@bot.message_handler(content_types=['text'])
def handle_team_replies(message):
    if message.chat.id != GROUP_ID:
        return

    for uid, orders_list in user_orders.items():
        for order in orders_list:
            if order.get('waiting_calc') and message.reply_to_message:
                client_chat_id = int(uid)
                bot.send_message(client_chat_id,
                    f"✅ <b>Расчёт по вашему заказу</b>\n\n"
                    f"📎 {order['filename']}\n"
                    f"💰 <b>От команды:</b>\n{message.text}",
                    parse_mode='HTML')
                bot.send_message(GROUP_ID, "✅ Расчёт отправлен клиенту.")
                order.pop('waiting_calc', None)
                save_orders(user_orders)
                return

            elif order.get('waiting_reject') and message.reply_to_message:
                client_chat_id = int(uid)
                bot.send_message(client_chat_id, f"❌ Ваш заказ отклонён.\n\nПричина:\n{message.text}")
                bot.send_message(GROUP_ID, "❌ Отклонение отправлено клиенту.")
                order.pop('waiting_reject', None)
                save_orders(user_orders)
                return


print("🚀 Бот CapyTech 3D Print запущен с полной обработкой кнопок команды!")
bot.infinity_polling()
