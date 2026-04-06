import os
import json
import datetime
import subprocess
import re
import telebot
from telebot import types
from stl import mesh as stl_mesh
import numpy as np

# ====================== НАСТРОЙКИ ======================
TOKEN = os.getenv('TOKEN')
GROUP_ID_STR = os.getenv('GROUP_ID')
MAX_FILE_SIZE_MB = 50

# Настройки OrcaSlicer
PROFILES_DIR = "/app/profiles"                    # Измените, если папка с профилями в другом месте
FILAMENT_COST = {"PLA": 0.08, "PETG": 0.09, "ABS": 0.10, "TPU": 0.12}  # руб/грамм
MACHINE_COST_PER_HOUR = 15                        # руб/час работы принтера

if not TOKEN or not GROUP_ID_STR:
    print("❌ Ошибка: TOKEN или GROUP_ID не заданы!")
    exit(1)

GROUP_ID = int(GROUP_ID_STR)
DATA_FILE = 'orders.json'

print(f"✅ Бот запущен. GROUP_ID = {GROUP_ID} | OrcaSlicer активен")

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


def get_user_orders(user_id):
    return user_orders.get(str(user_id), [])


# ====================== ИЗВЛЕЧЕНИЕ РАЗМЕРОВ ======================
def get_model_dimensions(file_path: str):
    """Возвращает ширину, длину и высоту в мм (округлено до 0.1 мм)"""
    try:
        your_mesh = stl_mesh.Mesh.from_file(file_path)
        min_coords = your_mesh.points.min(axis=0)
        max_coords = your_mesh.points.max(axis=0)

        width = round(max_coords[0] - min_coords[0], 1)
        length = round(max_coords[1] - min_coords[1], 1)
        height = round(max_coords[2] - min_coords[2], 1)

        return width, length, height
    except Exception as e:
        print(f"Ошибка извлечения размеров: {e}")
        return None, None, None


# ====================== РАСЧЁТ В ORCASLICER ======================
def slice_with_orca(file_path: str, params: dict):
    """Запускает OrcaSlicer и возвращает (время_печати, вес_пластика_г, ошибка)"""
    try:
        material = params['material']
        output_gcode = file_path.replace('.stl', '_output.gcode')

        cmd = [
            "orca-slicer",
            "--slice",
            "--load-printer", f"{PROFILES_DIR}/printer/your_printer.json",
            "--load-filaments", f"{PROFILES_DIR}/filament/{material}.json",
            "--load-settings", f"{PROFILES_DIR}/process/{material}_process.json",
            "--first-layer-height", str(params.get('first_layer_height', 0.2)),
            "--perimeters", str(params.get('perimeters', 3)),
            "--sparse-infill-density", str(params.get('infill', 20)),
            "--export-gcode", output_gcode,
            file_path
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)

        if result.returncode != 0:
            return None, None, f"Ошибка OrcaSlicer: {result.stderr[:300]}"

        # Читаем G-code
        with open(output_gcode, "r", encoding="utf-8", errors="ignore") as f:
            gcode = f.read()

        # Время печати
        time_match = re.search(r"; estimated printing time .*?(\d+h)?\s*(\d+m)?\s*(\d+s)?", gcode, re.I)
        print_time = "Неизвестно"
        if time_match:
            print_time = " ".join(g for g in time_match.groups() if g).strip()

        # Вес пластика
        weight_match = re.search(r"; filament used .*?\((\d+\.?\d*)g\)", gcode, re.I)
        weight_g = int(float(weight_match.group(1))) if weight_match else 0

        return print_time, weight_g, None

    except subprocess.TimeoutExpired:
        return None, None, "Таймаут расчёта (модель слишком большая)"
    except Exception as e:
        return None, None, f"Ошибка запуска OrcaSlicer: {str(e)}"


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


# ====================== ПРОВЕРКА STL ======================
def is_valid_stl(data: bytes):
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


# ====================== СТАРТ ======================
@bot.message_handler(commands=['start'])
def start(message):
    bot.send_message(message.chat.id,
        "👋 <b>Добро пожаловать в CapyTech 3D Print!</b>\n\n"
        "Нажмите «💰 Рассчитать стоимость» и отправьте файл **.stl**",
        parse_mode='HTML', reply_markup=get_main_keyboard())


# ====================== ОБРАБОТКА ФАЙЛА ======================
@bot.message_handler(content_types=['document'])
def handle_document(message):
    if not message.document:
        return

    filename = (message.document.file_name or '').lower()
    if not filename.endswith('.stl'):
        bot.reply_to(message, "❌ Принимаем только файлы **.stl**.", reply_markup=get_main_keyboard())
        return

    file_size_mb = message.document.file_size / (1024 * 1024)
    if file_size_mb > MAX_FILE_SIZE_MB:
        bot.reply_to(message, f"❌ Файл слишком большой ({file_size_mb:.1f} МБ). Максимум {MAX_FILE_SIZE_MB} МБ.",
                     reply_markup=get_main_keyboard())
        return

    try:
        file_info = bot.get_file(message.document.file_id)
        downloaded = bot.download_file(file_info.file_path)

        file_path = f"/tmp/{message.document.file_name}"
        with open(file_path, "wb") as f:
            f.write(downloaded)

        is_valid, triangles = is_valid_stl(downloaded)
        if not is_valid:
            bot.reply_to(message, "❌ Файл не является корректным STL-файлом.", reply_markup=get_main_keyboard())
            if os.path.exists(file_path):
                os.remove(file_path)
            return
    except Exception as e:
        print(f"Ошибка проверки файла: {e}")
        bot.reply_to(message, "⚠️ Ошибка при проверке файла.", reply_markup=get_main_keyboard())
        return

    # Извлекаем размеры
    width, length, height = get_model_dimensions(file_path)
    dim_text = f"📏 Размеры: **{width} × {length} × {height} мм**\n\n" if width else ""

    pending_orders[message.chat.id] = {
        'file_path': file_path,
        'filename': message.document.file_name,
        'description': message.caption or "Без описания",
        'triangles': triangles,
        'dimensions': {'width': width, 'length': length, 'height': height},
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
        f"✅ Файл .stl успешно принят!\n{dim_text}"
        "<b>Шаг 1 из 4:</b> Выберите материал",
        parse_mode='HTML',
        reply_markup=get_material_keyboard()
    )


# ====================== ОБРАБОТКА ТЕКСТА ======================
@bot.message_handler(content_types=['text'])
def handle_text(message):
    text = message.text.strip()
    chat_id = message.chat.id

    if text == "💰 Рассчитать стоимость":
        bot.send_message(chat_id, "📤 Отправьте файл модели **.stl** с описанием в подписи.", 
                         reply_markup=get_main_keyboard())
        return

    if text == "📋 Мои заказы":
        orders_list = get_user_orders(chat_id)
        if not orders_list:
            bot.send_message(chat_id,
                "📋 У вас пока нет заказов.\n\nОтправьте первую модель через кнопку «💰 Рассчитать стоимость».",
                reply_markup=get_main_keyboard())
            return

        response = "📋 <b>Ваши последние заказы:</b>\n\n"
        for order in reversed(orders_list[-10:]):
            status_emoji = {
                "Новый": "🆕", "В работе": "🔄", "Расчёт готов": "✅",
                "Готов к выдаче": "📦", "Выдан": "🎉", "Отклонён": "❌"
            }.get(order.get('status', 'Новый'), "📋")

            params = []
            dims = order.get('dimensions', {})
            if dims.get('width'):
                params.append(f"📏 {dims['width']}×{dims['length']}×{dims['height']} мм")
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

    # Обработка шагов параметров
    if chat_id in pending_orders:
        order = pending_orders[chat_id]

        if order['step'] == 'material':
            if text in ["PLA", "PETG", "ABS", "TPU"]:
                order['material'] = text
                order['step'] = 'first_layer'
                bot.send_message(chat_id, f"✅ Материал: <b>{text}</b>\n\nВведите высоту первого слоя (например: 0.2):", parse_mode='HTML')
            else:
                bot.send_message(chat_id, "Выберите материал из кнопок.", reply_markup=get_material_keyboard())
            return

        elif order['step'] == 'first_layer':
            try:
                order['first_layer_height'] = float(text.replace(',', '.'))
                order['step'] = 'perimeters'
                bot.send_message(chat_id, f"✅ Высота первого слоя: <b>{order['first_layer_height']} мм</b>\n\nУкажите количество периметров:", parse_mode='HTML')
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
                    bot.send_message(chat_id, "✅ Все параметры собраны!\nПодтвердите заказ:", 
                                     parse_mode='HTML', reply_markup=get_confirm_keyboard())
                else:
                    bot.send_message(chat_id, "Заполнение должно быть от 0 до 100")
            except:
                bot.send_message(chat_id, "Введите число от 0 до 100")
            return


# ====================== ОБРАБОТКА КНОПОК ======================
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    chat_id = call.message.chat.id

    if call.data == "confirm_order" and chat_id in pending_orders:
        order = pending_orders.pop(chat_id)
        bot.send_message(chat_id, "⏳ Запускаю расчёт в OrcaSlicer... Это может занять от 30 до 90 секунд.")

        print_time, weight_g, error = slice_with_orca(order['file_path'], order)

        if error:
            bot.send_message(chat_id, f"⚠️ Ошибка расчёта:\n{error}")
            return

        # Расчёт стоимости
        cost_per_gram = FILAMENT_COST.get(order['material'], 0.09)
        material_cost = round(weight_g * cost_per_gram, 0)

        hours = 0
        if "h" in print_time:
            hours += int(re.search(r'(\d+)h', print_time).group(1))
        if "m" in print_time:
            hours += int(re.search(r'(\d+)m', print_time).group(1)) / 60

        machine_cost = round(hours * MACHINE_COST_PER_HOUR, 0)
        total_cost = int(material_cost + machine_cost)

        dims = order.get('dimensions', {})
        dim_text = f"{dims.get('width')} × {dims.get('length')} × {dims.get('height')} мм" if dims.get('width') else "—"

        final_order = {
            'order_id': int(datetime.datetime.now().timestamp()),
            'filename': order['filename'],
            'description': order['description'],
            'triangles': order.get('triangles', 0),
            'dimensions': dims,
            'material': order['material'],
            'first_layer_height': order.get('first_layer_height'),
            'perimeters': order.get('perimeters'),
            'infill': order.get('infill'),
            'print_time': print_time,
            'filament_weight': weight_g,
            'estimated_cost': total_cost,
            'user_id': order['user_id'],
            'first_name': order['first_name'],
            'username': order.get('username'),
            'status': 'Расчёт готов',
            'date': datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
        }

        # Сохраняем заказ
        user_id_str = str(chat_id)
        if user_id_str not in user_orders:
            user_orders[user_id_str] = []
        user_orders[user_id_str].append(final_order)
        save_orders(user_orders)

        # Уведомление клиенту
        bot.send_message(chat_id,
            f"✅ <b>Расчёт готов!</b>\n\n"
            f"📎 {order['filename']}\n"
            f"📏 {dim_text}\n"
            f"⏱ Время печати: {print_time}\n"
            f"🧪 Пластик: {weight_g} г\n"
            f"💰 Примерная стоимость: <b>{total_cost} руб.</b>",
            parse_mode='HTML', reply_markup=get_main_keyboard())

        # Уведомление в группу
        user_link = f"<a href='tg://user?id={order['user_id']}'>{order['first_name']}</a>"
        if order.get('username'):
            user_link += f" (@{order['username']})"

        bot.send_message(GROUP_ID,
            f"📦 <b>Новый заказ с расчётом</b>\n\n"
            f"👤 {user_link}\n"
            f"📎 {order['filename']}\n"
            f"📏 {dim_text}\n"
            f"⏱ {print_time}\n"
            f"🧪 {weight_g} г\n"
            f"💰 ~{total_cost} руб.",
            parse_mode='HTML')

    elif call.data == "edit_params" and chat_id in pending_orders:
        pending_orders[chat_id]['step'] = 'material'
        bot.send_message(chat_id, "✏️ Параметры сброшены.\nВыберите материал заново:", 
                         reply_markup=get_material_keyboard())

    # Кнопки команды в группе
    elif call.data.startswith("team_"):
        parts = call.data.split('_')
        action = parts[1]
        order_id = int(parts[2])

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
                f"💰 Расчёт для заказа #{order_id}\n\nНапишите ответ reply на это сообщение.",
                parse_mode='Markdown', reply_to_message_id=call.message.message_id)
            target_order['waiting_calc'] = True

        elif action == "reject":
            bot.answer_callback_query(call.id)
            bot.send_message(GROUP_ID, f"❌ Укажите причину отклонения заказа #{order_id}:", 
                             reply_to_message_id=call.message.message_id)
            target_order['waiting_reject'] = True

    bot.answer_callback_query(call.id)


print("🚀 Бот CapyTech 3D Print запущен (только .stl + OrcaSlicer)")
bot.infinity_polling()
