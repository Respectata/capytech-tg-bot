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
    print("❌ Ошибка: TOKEN или GROUP_ID не заданы в переменных окружения Railway!")
    exit(1)

GROUP_ID = int(GROUP_ID_STR)
DATA_FILE = 'orders.json'  # Файл для хранения заказов

print(f"✅ Настройки загружены. GROUP_ID = {GROUP_ID}, Max size = {MAX_FILE_SIZE_MB} МБ")

bot = telebot.TeleBot(TOKEN)

# ====================== РАБОТА С ХРАНИЛИЩЕМ ======================
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

# Глобальное хранилище заказов (user_id → список заказов)
user_orders = load_orders()


def add_order(user_id, order_data):
    user_id_str = str(user_id)
    if user_id_str not in user_orders:
        user_orders[user_id_str] = []
    
    order_data['date'] = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
    order_data['status'] = "Новый"  # Можно потом менять статус
    
    user_orders[user_id_str].append(order_data)
    # Оставляем только последние 20 заказов на пользователя
    if len(user_orders[user_id_str]) > 20:
        user_orders[user_id_str] = user_orders[user_id_str][-20:]
    
    save_orders(user_orders)


def get_user_orders(user_id):
    return user_orders.get(str(user_id), [])


# ====================== КЛАВИАТУРА ======================
def get_main_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        types.KeyboardButton("💰 Рассчитать стоимость"),
        types.KeyboardButton("📋 Мои заказы")
    )
    markup.add(
        types.KeyboardButton("✍️ Написать нам"),
        types.KeyboardButton("❓ Помощь")
    )
    return markup


# ====================== СТАРТ И ПОМОЩЬ ======================
@bot.message_handler(commands=['start'])
def start(message):
    welcome = (
        "👋 <b>Добро пожаловать в CapyTech 3D Print!</b>\n\n"
        "Отправьте файл <b>.stl</b> или <b>.obj</b> с описанием в подписи — и мы быстро рассчитаем стоимость и сроки."
    )
    bot.send_message(message.chat.id, welcome, parse_mode='HTML', reply_markup=get_main_keyboard())


@bot.message_handler(commands=['help'])
def help_command(message):
    help_text = "❓ <b>Как сделать заказ:</b>\n\n1. Отправьте файл модели\n2. Напишите в подписи описание\n3. Нажмите «📋 Мои заказы», чтобы посмотреть историю."
    bot.send_message(message.chat.id, help_text, parse_mode='HTML', reply_markup=get_main_keyboard())


# ====================== ОБРАБОТКА ФАЙЛОВ ======================
@bot.message_handler(content_types=['document'])
def handle_document(message):
    if not message.document:
        return

    filename = (message.document.file_name or '').lower()
    file_size_mb = message.document.file_size / (1024 * 1024)

    if not filename.endswith(('.stl', '.obj')):
        bot.reply_to(message, "❌ Принимаем только .stl и .obj файлы.", reply_markup=get_main_keyboard())
        return

    if file_size_mb > MAX_FILE_SIZE_MB:
        bot.reply_to(message, 
            f"❌ Файл слишком большой!\nРазмер: <b>{file_size_mb:.1f} МБ</b>\nМаксимум: <b>{MAX_FILE_SIZE_MB} МБ</b>",
            parse_mode='HTML', reply_markup=get_main_keyboard())
        return

    try:
        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)

        is_valid = False
        triangles = 0
        file_type = filename.split('.')[-1].upper()

        if filename.endswith('.stl'):
            is_valid, triangles = get_stl_info(downloaded_file)  # функция из предыдущей версии
        else:
            is_valid = is_valid_obj(downloaded_file)

        if not is_valid:
            bot.reply_to(message, f"❌ Файл <b>{message.document.file_name}</b> некорректный.", 
                         parse_mode='HTML', reply_markup=get_main_keyboard())
            return

    except Exception as e:
        print(f"Ошибка: {e}")
        bot.reply_to(message, "⚠️ Ошибка при проверке файла.", reply_markup=get_main_keyboard())
        return

    # Пересылка в группу
    forwarded = bot.forward_message(GROUP_ID, message.chat.id, message.message_id)

    user = message.from_user
    description = message.caption or "Без описания"

    tri_text = f"📊 Треугольников: <b>{triangles:,}</b>\n" if triangles > 0 else ""

    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("✅ Принять", callback_data=f"accept_{forwarded.message_id}"),
        types.InlineKeyboardButton("💰 Рассчитать", callback_data=f"calc_{forwarded.message_id}"),
        types.InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{forwarded.message_id}")
    )

    notification = f"📦 <b>Новый заказ!</b>\n\n👤 {user.first_name} ({'@' + user.username if user.username else 'без username'})\n📝 {description}\n📎 {message.document.file_name}\n{tri_text}✅ Проверен"

    bot.send_message(GROUP_ID, notification, parse_mode='HTML', reply_markup=markup)

    # Сохраняем заказ
    order_data = {
        'order_id': forwarded.message_id,
        'filename': message.document.file_name,
        'description': description,
        'triangles': triangles,
        'status': 'Новый'
    }
    add_order(message.chat.id, order_data)

    reply_text = f"✅ <b>Заказ принят!</b>\n📊 Треугольников: <b>{triangles:,}</b>" if triangles else "✅ <b>Заказ принят!</b>"
    bot.reply_to(message, reply_text + "\nНаша команда скоро свяжется с расчётом.", 
                 parse_mode='HTML', reply_markup=get_main_keyboard())


# ====================== КНОПКА «МОИ ЗАКАЗЫ» ======================
@bot.message_handler(content_types=['text'])
def handle_text(message):
    text = message.text.strip()

    if text == "📋 Мои заказы":
        orders_list = get_user_orders(message.chat.id)
        
        if not orders_list:
            bot.send_message(message.chat.id, "📋 У вас пока нет заказов.\nОтправьте первую модель!", 
                             reply_markup=get_main_keyboard())
            return

        response = "📋 <b>Ваши последние заказы:</b>\n\n"
        for order in reversed(orders_list[-10:]):  # последние 10
            response += (
                f"🔹 <b>Заказ #{order['order_id']}</b> — {order['date']}\n"
                f"📎 {order['filename']}\n"
                f"📝 {order['description'][:100]}{'...' if len(order['description']) > 100 else ''}\n"
                f"📊 Треуг.: {order.get('triangles', 0):,} | Статус: <i>{order['status']}</i>\n\n"
            )

        bot.send_message(message.chat.id, response, parse_mode='HTML', reply_markup=get_main_keyboard())
        return

    # ... (остальные обработчики текста — 💰 Рассчитать, ✍️ Написать нам, ❓ Помощь и ответы команды — оставлены как были)

    # (Вставьте сюда остальную часть handle_text из предыдущей версии: обработка "💰 Рассчитать стоимость", "✍️ Написать нам", "❓ Помощь" и блок для команды в группе)

    else:
        # Обычное сообщение от клиента
        bot.send_message(GROUP_ID, f"✉️ Сообщение от {message.from_user.first_name}:\n{text}")
        bot.reply_to(message, "✅ Сообщение отправлено команде.", reply_markup=get_main_keyboard())


print("🚀 Бот CapyTech 3D Print запущен! Кнопка «Мои заказы» работает.")
bot.infinity_polling()
