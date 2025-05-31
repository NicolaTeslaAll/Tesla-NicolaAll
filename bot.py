import os
import tempfile
import zipfile
import shutil
import logging
import time
from PIL import Image, ImageEnhance, ImageOps
import telebot
from telebot import types
import requests
from io import BytesIO
import threading

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Токен бота из переменной окружения
TOKEN = os.environ.get('7714752663:AAETb0MsNFWKVHBzDO4vMJFWoJtu9xr56IY')
bot = telebot.TeleBot(TOKEN)

# Варианты цветов для перекраски с RGB значениями
COLOR_OPTIONS = {
    'Красный': (255, 0, 0),
    'Синий': (0, 0, 255),
    'Зеленый': (0, 255, 0),
    'Желтый': (255, 255, 0),
    'Фиолетовый': (128, 0, 128),
    'Розовый': (255, 192, 203),
    'Оранжевый': (255, 165, 0),
    'Голубой': (0, 255, 255),
    'Лайм': (50, 205, 50),
    'Маджента': (255, 0, 255),
    'Бирюзовый': (0, 128, 128),
    'Лавандовый': (230, 230, 250),
    'Коричневый': (165, 42, 42),
    'Бордовый': (128, 0, 0),
    'Темно-синий': (0, 0, 128),
    'Оливковый': (128, 128, 0),
    'Золотой': (255, 215, 0),
    'Серебряный': (192, 192, 192),
    'Аквамарин': (64, 224, 208),
    'Индиго': (75, 0, 130),
}

# Хранилище данных пользователей
user_data = {}
# Блокировка для потокобезопасного доступа к user_data
user_data_lock = threading.Lock()

# Таймаут очистки данных пользователя (2 часа)
CLEANUP_TIMEOUT = 7200  # секунды

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    bot.reply_to(message, 
                 "Добро пожаловать в бот для перекраски текстур Minecraft!\n\n"
                 "Отправьте мне .mcpack файл, и я помогу перекрасить его в различные цвета.\n"
                 "Размер файла должен быть меньше 20МБ.")

@bot.message_handler(content_types=['document'])
def handle_document(message):
    file_info = bot.get_file(message.document.file_id)
    
    # Проверка расширения файла
    if not message.document.file_name.endswith('.mcpack'):
        bot.reply_to(message, "Пожалуйста, отправьте файл с расширением .mcpack.")
        return
    
    # Проверка размера файла (20МБ = 20 * 1024 * 1024 байт)
    if file_info.file_size > 20 * 1024 * 1024:
        bot.reply_to(message, "Размер файла превышает лимит в 20МБ. Пожалуйста, отправьте файл меньшего размера.")
        return
    
    status_message = bot.reply_to(message, "Получен ваш .mcpack файл. Обработка...")
    
    try:
        # Скачивание файла
        file_path = download_file(file_info.file_path)
        
        # Распаковка файла
        extract_dir = extract_mcpack(file_path)
        
        # Поиск всех PNG файлов для перекраски
        target_paths = find_png_files(extract_dir)
        
        if not target_paths:
            bot.edit_message_text("В .mcpack файле не найдено PNG изображений.", 
                                 chat_id=message.chat.id, 
                                 message_id=status_message.message_id)
            cleanup_temp_files(extract_dir, file_path)
            return
        
        # Сохранение данных для этого пользователя
        with user_data_lock:
            user_data[message.chat.id] = {
                'extract_dir': extract_dir,
                'original_file': file_path,
                'original_file_name': message.document.file_name,
                'target_paths': target_paths,
                'timestamp': time.time(),
                'status_message_id': status_message.message_id
            }
        
        # Планирование очистки
        schedule_cleanup(message.chat.id)
        
        # Обновление статусного сообщения
        bot.edit_message_text(f"Найдено {len(target_paths)} PNG файлов для перекраски.", 
                             chat_id=message.chat.id, 
                             message_id=status_message.message_id)
        
        # Предоставление опций цветов
        send_color_options(message.chat.id)
        
    except Exception as e:
        logger.error(f"Ошибка обработки файла: {e}")
        bot.edit_message_text(f"Ошибка обработки вашего файла: {str(e)}", 
                             chat_id=message.chat.id, 
                             message_id=status_message.message_id)
        cleanup_user_data(message.chat.id)

def download_file(file_path):
    """Скачивание файла с серверов Telegram"""
    file_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_path}"
    response = requests.get(file_url)
    
    if response.status_code != 200:
        raise Exception("Не удалось скачать файл с серверов Telegram")
    
    # Сохранение во временный файл
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.mcpack')
    temp_file.write(response.content)
    temp_file.close()
    
    return temp_file.name

def extract_mcpack(file_path):
    """Распаковка .mcpack файла (переименованный .zip)"""
    # Создание временной директории для распаковки
    extract_dir = tempfile.mkdtemp()
    
    # Переименование .mcpack в .zip для распаковки
    zip_path = os.path.join(os.path.dirname(file_path), 
                           os.path.basename(file_path).replace('.mcpack', '.zip'))
    shutil.copy2(file_path, zip_path)
    
    # Распаковка zip файла
    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(extract_dir)
    except zipfile.BadZipFile:
        raise Exception("Файл не является корректным .mcpack (zip) файлом.")
    
    # Очистка zip файла
    os.remove(zip_path)
    
    return extract_dir

def find_png_files(directory):
    """Поиск всех PNG файлов в директории и поддиректориях"""
    png_files = []
    
    for root, _, files in os.walk(directory):
        for file in files:
            if file.lower().endswith('.png'):
                # Получение относительного пути от директории распаковки
                rel_path = os.path.relpath(os.path.join(root, file), directory)
                png_files.append(rel_path)
    
    return png_files

def send_color_options(chat_id):
    """Отправка инлайн клавиатуры с опциями цветов"""
    markup = types.InlineKeyboardMarkup(row_width=3)
    
    # Создание кнопок для каждой опции цвета
    buttons = []
    for color_name, _ in COLOR_OPTIONS.items():
        buttons.append(types.InlineKeyboardButton(color_name, callback_data=f"color_{color_name}"))
    
    # Добавление кнопок в разметку
    markup.add(*buttons)
    
    # Добавление кнопки отмены
    markup.add(types.InlineKeyboardButton("Отмена", callback_data="cancel"))
    
    bot.send_message(chat_id, "Выберите цвет для перекраски:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('color_'))
def handle_color_selection(call):
    color_name = call.data.split('_')[1]
    chat_id = call.message.chat.id
    
    with user_data_lock:
        if chat_id not in user_data:
            bot.answer_callback_query(call.id, "Сессия истекла. Пожалуйста, отправьте .mcpack файл снова.")
            return
    
    bot.answer_callback_query(call.id, f"Перекраска в {color_name}...")
    status_message = bot.edit_message_text(f"Перекраска в {color_name}... Пожалуйста, подождите.", 
                                        chat_id=chat_id, 
                                        message_id=call.message.message_id)
    
    try:
        # Получение данных пользователя
        with user_data_lock:
            extract_dir = user_data[chat_id]['extract_dir']
            original_file_name = user_data[chat_id]['original_file_name']
            target_paths = user_data[chat_id]['target_paths']
            user_data[chat_id]['timestamp'] = time.time()  # Обновление временной метки
        
        # Перекраска изображений
        recolor_images(extract_dir, target_paths, COLOR_OPTIONS[color_name])
        
        # Создание нового .mcpack файла
        new_mcpack_path = create_mcpack(extract_dir, original_file_name, color_name)
        
        # Обновление статусного сообщения
        bot.edit_message_text(f"Перекраска завершена! Отправляю новый .mcpack файл...", 
                             chat_id=chat_id, 
                             message_id=status_message.message_id)
        
        # Отправка нового .mcpack файла
        with open(new_mcpack_path, 'rb') as mcpack_file:
            new_file_name = os.path.basename(new_mcpack_path)
            bot.send_document(chat_id, mcpack_file, caption=f"Перекрашено в {color_name}", 
                             visible_file_name=new_file_name)
        
        # Очистка нового .mcpack файла
        os.remove(new_mcpack_path)
        
        # Предоставление опций цветов снова
        send_color_options(chat_id)
        
    except Exception as e:
        logger.error(f"Ошибка перекраски: {e}")
        bot.edit_message_text(f"Ошибка перекраски вашего файла: {str(e)}", 
                             chat_id=chat_id, 
                             message_id=status_message.message_id)
        cleanup_user_data(chat_id)

@bot.callback_query_handler(func=lambda call: call.data == 'cancel')
def handle_cancel(call):
    chat_id = call.message.chat.id
    
    bot.answer_callback_query(call.id, "Операция отменена.")
    bot.edit_message_text("Операция отменена. Отправьте новый .mcpack файл для начала.", 
                         chat_id=chat_id, 
                         message_id=call.message.message_id)
    
    cleanup_user_data(chat_id)

def recolor_images(extract_dir, target_paths, target_color):
    """Перекраска определенных PNG изображений в распакованной директории"""
    # Получение RGB компонентов целевого цвета
    r_target, g_target, b_target = target_color
    
    # Поиск и перекраска каждого целевого изображения
    for target_path in target_paths:
        full_path = os.path.join(extract_dir, target_path)
        
        if os.path.exists(full_path):
            try:
                # Открытие изображения
                img = Image.open(full_path)
                
                # Конвертация в RGBA если еще не в этом формате
                if img.mode != 'RGBA':
                    img = img.convert('RGBA')
                
                # Разделение изображения на каналы
                r, g, b, a = img.split()
                
                # Создание версии в оттенках серого для яркости
                gray = ImageOps.grayscale(img)
                
                # Создание нового цветного изображения
                colored = Image.merge('RGB', (
                    ImageOps.colorize(gray, (0, 0, 0), (r_target, r_target, r_target)),
                    ImageOps.colorize(gray, (0, 0, 0), (g_target, g_target, g_target)),
                    ImageOps.colorize(gray, (0, 0, 0), (b_target, b_target, b_target)),
                ))
                
                # Слияние с альфа-каналом
                colored.putalpha(a)
                
                # Применение высококачественных улучшений
                enhancer = ImageEnhance.Contrast(colored)
                colored = enhancer.enhance(1.3)  # Увеличение контраста
                
                enhancer = ImageEnhance.Brightness(colored)
                colored = enhancer.enhance(1.1)  # Небольшое увеличение яркости
                
                enhancer = ImageEnhance.Color(colored)
                colored = enhancer.enhance(1.5)  # Увеличение насыщенности цвета
                
                # Сохранение измененного изображения
                colored.save(full_path)
                
                logger.info(f"Перекрашено {target_path}")
                
            except Exception as e:
                logger.error(f"Ошибка перекраски {target_path}: {e}")
                # Продолжаем с другими изображениями
        else:
            logger.warning(f"Целевой путь не найден: {target_path}")

def create_mcpack(extract_dir, original_file_name, color_name):
    """Создание нового .mcpack файла из распакованной и модифицированной директории"""
    # Создание временного zip файла
    base_name = os.path.splitext(original_file_name)[0]
    zip_path = tempfile.mktemp(suffix='.zip')
    
    # Удаление расширения из zip_path для использования с make_archive
    zip_base = zip_path[:-4]
    
    # Создание zip файла
    shutil.make_archive(zip_base, 'zip', extract_dir)
    
    # Переименование в .mcpack
    mcpack_path = f"{zip_base}_{color_name}.mcpack"
    os.rename(f"{zip_base}.zip", mcpack_path)
    
    return mcpack_path

def schedule_cleanup(chat_id):
    """Планирование очистки данных пользователя после таймаута"""
    def check_and_cleanup():
        time.sleep(CLEANUP_TIMEOUT)
        with user_data_lock:
            if chat_id in user_data:
                current_time = time.time()
                if current_time - user_data[chat_id]['timestamp'] >= CLEANUP_TIMEOUT:
                    logger.info(f"Очистка данных для chat_id {chat_id} из-за таймаута")
                    try:
                        bot.send_message(chat_id, "Ваша сессия истекла из-за неактивности. "
                                                "Пожалуйста, отправьте новый .mcpack файл для начала.")
                    except Exception as e:
                        logger.error(f"Ошибка отправки сообщения о таймауте: {e}")
                    
                    cleanup_user_data(chat_id)
    
    # Запуск потока очистки
    cleanup_thread = threading.Thread(target=check_and_cleanup)
    cleanup_thread.daemon = True
    cleanup_thread.start()

def cleanup_temp_files(extract_dir, file_path):
    """Очистка временных файлов"""
    try:
        if extract_dir and os.path.exists(extract_dir):
            shutil.rmtree(extract_dir)
        
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
    except Exception as e:
        logger.error(f"Ошибка очистки временных файлов: {e}")

def cleanup_user_data(chat_id):
    """Очистка данных пользователя и временных файлов"""
    with user_data_lock:
        if chat_id in user_data:
            # Получение данных
            extract_dir = user_data[chat_id].get('extract_dir')
            original_file = user_data[chat_id].get('original_file')
            
            # Очистка временных файлов
            cleanup_temp_files(extract_dir, original_file)
            
            # Удаление данных пользователя
            del user_data[chat_id]

@bot.message_handler(commands=['cancel'])
def cancel_operation(message):
    """Отмена текущей операции и очистка"""
    cleanup_user_data(message.chat.id)
    bot.reply_to(message, "Операция отменена. Вы можете отправить новый .mcpack файл.")

# Настройка обработчика для очистки при завершении работы бота
import atexit

@atexit.register
def cleanup_all():
    """Очистка всех временных файлов и директорий при завершении работы бота"""
    with user_data_lock:
        for chat_id in list(user_data.keys()):
            cleanup_user_data(chat_id)

if __name__ == '__main__':
    logger.info("Запуск бота...")
    # Запуск бота
    bot.polling(none_stop=True)
