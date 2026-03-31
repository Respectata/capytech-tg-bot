import os
import json
import datetime
import telebot
from telebot import types

# ====================== НАСТРОЙКИ ======================
TOKEN = os.getenv('TOKEN')
GROUP_ID_STR = os.getenv('GROUP_ID')
MAX_FILE_SIZE_MB = 100

if not TOKEN or not GROUP_ID_STR:
    print("❌ Ошибка: TOKEN или GROUP_ID не заданы!")
    exit(1)

GROUP_ID = int(GROUP_ID_STR)
DATA_FILE = 'orders.json'

print(f"✅ Бот запущен. GROUP_ID = {GROUP_ID}")

bot = telebot.TeleBot(TOKEN)

# ====================== ПРОВЕРКА ФАЙЛОВ ======================
def get_stl_info(data: bytes):
    """Возвращает (is_valid, num_triangles)"""
    if len(data) < 84:
        return False, 0

    # ASCII STL
    try:
        header = data[:200].decode('ascii', errors='ignore').lower()
        if header.startswith('solid') and ('facet normal' in header or 'endfacet' in header):
            return True, 0  # ASCII — точное число не считаем
    except:
        pass

    # Binary STL (самый распространённый)
    try:
        num_triangles = int.from_bytes(data[80:84], 'little')
        if num_triangles < 1 or num_triangles > 100_000_000:
            return False, 0

        expected_size = 84 + num_triangles * 50
        if abs(len(data) - expected_size) <= 600:
            return True, num_triangles
    except:
        pass

    return False, 0


def is_valid_obj(data: bytes) -> bool:
    try:
        text = data.decode('utf-8', errors='ignore').lower()
        lines = text.splitlines()[:150]
        has_v = any(line.startswith('v ') for line in lines)
        has_f = any(line.startswith('f ') for line in lines)
        return has_v and has_f
    except:
        return False

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

def add_order(user_id, order_data):
    user_id_str = str(user_id)
    if user_id_str not in user_orders:
        user_orders[user_id_str] = []
    
    order_data['date'] = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
    order_data['status'] = "Новый"
    
    user_orders[user_id_str].append(order_data)
    if len(user_orders[user_id_str]) > 20:
        user_orders[user_id_str] = user_orders[user_id_str][-20:]
    
    save_orders(user_orders)


def get_user_orders(user_id):
    return user_orders.get(str(user_id), [])


# ====================== КЛАВИАТУРА ======================
def get_main_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    markup.add("💰 Рассчитать стоимость")
    markup.add("📋 Мои заказы")
    return markup


# ====================== МАТЕРИАЛЫ ======================
materials = ["PLA", "PETG", "ABS", "TPU"]

def get_material_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    for mat in materials:
        markup.add(mat)
    return markup


# ====================== СТАРТ ======================
@bot.message_handler(commands=['start'])
def start(message):
    text = (
        "👋 <b>Добро пожаловать в CapyTech 3D Print!</b>\n\n"
        "Нажмите «💰 Рассчитать стоимость» и отправьте файл модели (.stl или .obj)."
    )
    bot.send_message(message.chat.id, text, parse_mode='HTML', reply_markup=get_main_keyboard())


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
        bot.reply_to(message, f"❌ Файл слишком большой ({file_size_mb:.1f} МБ). Максимум {MAX_FILE_SIZE_MB} МБ.", 
                     reply_markup=get_main_keyboard())
        return

    # Проверка валидности файла (оставляем вашу текущую функцию)
    try:
        file_info = bot.get_file(message.document.file_id)
        downloaded = bot.download_file(file_info.file_path)

        is_valid = False
        triangles = 0

        if filename.endswith('.stl'):
            is_valid, triangles = get_stl_info(downloaded)   # ваша функция
        else:
            is_valid = is_valid_obj(downloaded)

        if not is_valid:
            bot.reply_to(message, "❌ Файл не является корректным STL или OBJ.", reply_markup=get_main_keyboard())
            return
    except:
        bot.reply_to(message, "⚠️ Ошибка при проверке файла.", reply_markup=get_main_keyboard())
        return

    # Сохраняем базовый заказ и начинаем уточнение параметров
    temp_order = {
        'order_id': message.message_id,   # временный ID, обновим позже
        'filename': message.document.file_name,
        'description': message.caption or "Без описания",
        'triangles': triangles,
        'material': None,
        'first_layer_height': None,
        'perimeters': None,
        'infill': None
    }

    # Сохраняем в глобальное хранилище для этого пользователя (ожидание ответов)
    if 'temp_orders' not in globals():
        global temp_orders
        temp_orders = {}
    temp_orders[message.chat.id] = temp_order

    bot.reply_to(message,
        "✅ Файл принят!\n\n"
        "Теперь уточните параметры заказа.\n\n"
        "<b>Выберите материал:</b>",
        parse_mode='HTML',
        reply_markup=get_material_keyboard()
    )


# ====================== ОБРАБОТКА ПАРАМЕТРОВ ======================
@bot.message_handler(content_types=['text'])
def handle_text(message):
    text = message.text.strip()

    if text == "💰 Рассчитать стоимость":
        bot.send_message(message.chat.id,
            "📤 Отправьте файл модели (.stl или .obj) и добавьте описание в подпись (по желанию).",
            reply_markup=get_main_keyboard())
        return

    if text == "📋 Мои заказы":
        orders_list = get_user_orders(message.chat.id)
        if not orders_list:
            bot.send_message(message.chat.id, "📋 У вас пока нет заказов.", reply_markup=get_main_keyboard())
            return

        response = "📋 <b>Ваши последние заказы:</b>\n\n"
        for order in reversed(orders_list[-8:]):
            params = []
            if order.get('material'): params.append(f"Материал: {order['material']}")
            if order.get('infill'): params.append(f"Заполнение: {order['infill']}%")
            if order.get('perimeters'): params.append(f"Периметры: {order['perimeters']}")
            
            response += (
                f"🔹 <b>#{order['order_id']}</b> — {order['date']}\n"
                f"📎 {order['filename']}\n"
                f"Статус: <b>{order['status']}</b>\n"
                f"{' | '.join(params)}\n\n"
            )
        bot.send_message(message.chat.id, response, parse_mode='HTML', reply_markup=get_main_keyboard())
        return

    # === Уточнение параметров после отправки файла ===
    if message.chat.id in globals().get('temp_orders', {}):
        temp = temp_orders[message.chat.id]

        if temp['material'] is None:
            if text in materials:
                temp['material'] = text
                bot.send_message(message.chat.id, 
                    f"✅ Материал: <b>{text}</b>\n\n"
                    "Укажите высоту первого слоя (например: 0.2 мм или 0.28):",
                    parse_mode='HTML')
            else:
                bot.send_message(message.chat.id, "Пожалуйста, выберите материал из кнопок.", 
                                 reply_markup=get_material_keyboard())
            return

        elif temp['first_layer_height'] is None:
            try:
                height = float(text.replace(',', '.'))
                temp['first_layer_height'] = height
                bot.send_message(message.chat.id,
                    f"✅ Высота первого слоя: <b>{height} мм</b>\n\n"
                    "Укажите количество периметров (стенок), обычно 2–4:",
                    parse_mode='HTML')
            except:
                bot.send_message(message.chat.id, "Введите число, например: 0.2")
            return

        elif temp['perimeters'] is None:
            try:
                perimeters = int(text)
                temp['perimeters'] = perimeters
                bot.send_message(message.chat.id,
                    f"✅ Периметров: <b>{perimeters}</b>\n\n"
                    "Укажите заполнение в процентах (например: 20, 40, 100):",
                    parse_mode='HTML')
            except:
                bot.send_message(message.chat.id, "Введите целое число (например: 20)")
            return

        elif temp['infill'] is None:
            try:
                infill = int(text)
                if 0 <= infill <= 100:
                    temp['infill'] = infill
                    
                    # Финальное сохранение заказа
                    final_order = temp.copy()
                    final_order['order_id'] = int(datetime.datetime.now().timestamp())  # уникальный ID
                    
                    add_order(message.chat.id, final_order)
                    
                    # Уведомление в группу
                    forwarded = bot.forward_message(GROUP_ID, message.chat.id, message.message_id - len(materials)*2)  # приблизительно
                    
                    params_text = (
                        f"Материал: {temp['material']}\n"
                        f"Первый слой: {temp['first_layer_height']} мм\n"
                        f"Периметры: {temp['perimeters']}\n"
                        f"Заполнение: {temp['infill']}%"
                    )

                    notification = (
                        f"📦 <b>Новый заказ</b>\n\n"
                        f"👤 {message.from_user.first_name}\n"
                        f"📎 {temp['filename']}\n"
                        f"📝 {temp['description']}\n"
                        f"📊 Треугольников: {temp.get('triangles', 0):,}\n\n"
                        f"<b>Параметры:</b>\n{params_text}"
                    )

                    bot.send_message(GROUP_ID, notification, parse_mode='HTML')

                    bot.send_message(message.chat.id,
                        "✅ <b>Заказ успешно оформлен!</b>\n\n"
                        "Наша команда уже видит все параметры и скоро пришлёт расчёт стоимости.",
                        parse_mode='HTML', reply_markup=get_main_keyboard())

                    # Очищаем временный заказ
                    del temp_orders[message.chat.id]
                else:
                    bot.send_message(message.chat.id, "Заполнение должно быть от 0 до 100%")
            except:
                bot.send_message(message.chat.id, "Введите число от 0 до 100")
            return


print("🚀 Бот CapyTech 3D Print запущен!")
bot.infinity_polling()
