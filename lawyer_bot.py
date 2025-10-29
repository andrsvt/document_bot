#!/usr/bin/env python3
import logging
import sqlite3
import re
import os
import smtplib
import random
import string
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, ConversationHandler

# Настройки
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('/opt/bots/bot.log'),
        logging.StreamHandler()
    ]
)
DB_PATH = '/opt/bots/documents.db'

# Состояния для добавления клиента
EMAIL, FULL_NAME, DOCUMENT = range(3)

# Загружаем секреты
from secrets import BOT_TOKEN_LAWYER, LAWYERS, EMAIL_HOST, EMAIL_PORT, EMAIL_USER, EMAIL_PASSWORD

# Импорты для email
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Импорты для PDF штампов
from pdf_stamp import add_signature_to_pdf, generate_document_hash, update_document_hash_in_db

def check_lawyer_access(user_id):
    """Проверяет доступ адвоката"""
    return user_id in LAWYERS

def init_database():
    """Инициализирует базу данных если нужно"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # Включаем проверку внешних ключей
    conn.execute("PRAGMA foreign_keys = ON")
    
    # Создаем таблицу clients если не существует
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            full_name TEXT NOT NULL,
            client_code TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Создаем таблицу documents если не существует
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER,
            file_path TEXT NOT NULL,
            lawyer_signed BOOLEAN DEFAULT 0,
            client_signed BOOLEAN DEFAULT 0,
            document_hash TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (client_id) REFERENCES clients (id)
        )
    ''')
    
    # Создаем таблицу для кодов подписи
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS signature_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER,
            user_type TEXT,
            code TEXT,
            attempts INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            expires_at DATETIME,
            FOREIGN KEY (document_id) REFERENCES documents (id)
        )
    ''')
    
    conn.commit()
    conn.close()
    logging.info("База данных инициализирована")

def generate_code():
    """Генерирует 6-значный код"""
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

def send_email(to_email, code, client_name=None):
    """Отправляет код на email"""
    try:
        # Создаем сообщение
        message = MIMEMultipart()
        message['From'] = EMAIL_USER
        message['To'] = to_email
        
        if client_name:
            message['Subject'] = "Код для подписи документа"
            body = f"""Код для подписи документа клиентом {client_name}:

🔐 Ваш код: {code}

⏰ Код действителен 10 минут

📄 Документ будет подписан после ввода кода"""
        else:
            message['Subject'] = "Код для подписи документа"
            body = f"""🔐 Ваш код для подписи документа: {code}

⏰ Код действителен 10 минут

📄 Документ будет подписан после ввода кода"""
        
        # Добавляем текст с правильной кодировкой
        message.attach(MIMEText(body, 'plain', 'utf-8'))
        
        with smtplib.SMTP(EMAIL_HOST, EMAIL_PORT) as server:
            server.starttls()
            server.login(EMAIL_USER, EMAIL_PASSWORD)
            server.send_message(message)
        
        logging.info(f"Код отправлен на {to_email}")
        return True
    except Exception as e:
        logging.error(f"Ошибка отправки email: {e}")
        return False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    user_id = update.message.from_user.id
    
    if not check_lawyer_access(user_id):
        await update.message.reply_text("🚫 Доступ запрещен")
        return
    
    # Очищаем временные данные
    context.user_data.clear()
    
    keyboard = [
        [InlineKeyboardButton("👥 Добавить клиента", callback_data="add_client")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "Добро пожаловать! Выберите раздел:",
        reply_markup=reply_markup
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопок"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    if not check_lawyer_access(user_id):
        await query.edit_message_text("🚫 Доступ запрещен")
        return
    
    if query.data == "add_client":
        await query.edit_message_text("Введите email клиента:")
        return EMAIL
    else:
        await query.edit_message_text("Неизвестная команда")
        return ConversationHandler.END

async def email_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка email"""
    email = update.message.text.strip()
    
    # Простая проверка email
    if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email):
        await update.message.reply_text("❌ Email некорректный. Попробуйте еще раз:")
        return EMAIL
    
    context.user_data['email'] = email
    
    await update.message.reply_text("✅ Email принят. Введите ФИО клиента:")
    return FULL_NAME

async def full_name_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка ФИО"""
    full_name = update.message.text.strip()
    
    if len(full_name) < 2:
        await update.message.reply_text("❌ ФИО слишком короткое. Введите еще раз:")
        return FULL_NAME
    
    context.user_data['full_name'] = full_name
    
    await update.message.reply_text("📄 Загрузите соглашение (PDF) для подписи:")
    return DOCUMENT

async def document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка документа"""
    if not update.message.document:
        await update.message.reply_text("❌ Пожалуйста, загрузите PDF файл:")
        return DOCUMENT
    
    document = update.message.document
    
    if document.mime_type != 'application/pdf':
        await update.message.reply_text("❌ Файл должен быть в формате PDF. Загрузите снова:")
        return DOCUMENT
    
    # Проверяем размер файла (макс 20MB)
    if document.file_size > 20 * 1024 * 1024:
        await update.message.reply_text("❌ Файл слишком большой (макс 20MB). Загрузите другой файл:")
        return DOCUMENT
    
    # Сохраняем информацию о клиенте в базу
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        # Сначала проверяем нет ли уже такого email
        cursor.execute("SELECT id FROM clients WHERE email = ?", (context.user_data['email'],))
        existing_client = cursor.fetchone()
        
        if existing_client:
            client_id = existing_client[0]
            # Обновляем ФИО если клиент уже существует
            cursor.execute("UPDATE clients SET full_name = ? WHERE id = ?", 
                         (context.user_data['full_name'], client_id))
            logging.info(f"Обновлен существующий клиент: {client_id}")
        else:
            # Создаем нового клиента
            cursor.execute(
                "INSERT INTO clients (email, full_name) VALUES (?, ?)",
                (context.user_data['email'], context.user_data['full_name'])
            )
            client_id = cursor.lastrowid
            logging.info(f"Создан новый клиент: {client_id}")
        
        conn.commit()
        
        # Создаем папку для документов если не существует
        os.makedirs('/opt/bots/documents', exist_ok=True)
        
        # Сохраняем информацию о документе
        file = await document.get_file()
        file_name = f"{client_id}_{document.file_name}"
        file_path = f"/opt/bots/documents/{file_name}"
        
        await file.download_to_drive(file_path)
        logging.info(f"Документ сохранен: {file_path}")
        
        # Генерируем хеш документа
        document_hash = generate_document_hash(client_id, document.file_name)
        
        # Сохраняем документ в базу
        cursor.execute(
            "INSERT INTO documents (client_id, file_path, document_hash) VALUES (?, ?, ?)",
            (client_id, file_path, document_hash)
        )
        document_id = cursor.lastrowid
        conn.commit()
        
        logging.info(f"Документ добавлен в базу для клиента {client_id}")
        
        # Показываем кнопку подписи
        keyboard = [[InlineKeyboardButton("🖊 Подписать", callback_data=f"sign_{document_id}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"✅ Документ успешно сохранен!\n"
            f"👤 Клиент: {context.user_data['full_name']}\n"
            f"📧 Email: {context.user_data['email']}\n"
            f"🆔 ID клиента: {client_id}\n"
            f"🔐 Хеш документа: {document_hash[:16]}...",
            reply_markup=reply_markup
        )
        
    except sqlite3.Error as e:
        logging.error(f"Ошибка базы данных: {e}")
        await update.message.reply_text("❌ Ошибка при сохранении в базу данных. Попробуйте снова.")
        return ConversationHandler.END
    except Exception as e:
        logging.error(f"Общая ошибка: {e}")
        await update.message.reply_text("❌ Произошла ошибка. Попробуйте снова.")
        return ConversationHandler.END
    finally:
        conn.close()
    
    # Очищаем временные данные
    context.user_data.clear()
    
    return ConversationHandler.END

async def sign_document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик подписи документа"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    if not check_lawyer_access(user_id):
        await query.edit_message_text("🚫 Доступ запрещен")
        return
    
    # Получаем ID документа из callback_data (sign_123 → 123)
    document_id = int(query.data.replace('sign_', ''))
    
    # Получаем информацию о клиенте из базы
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            SELECT c.email, c.full_name, d.file_path 
            FROM clients c 
            JOIN documents d ON c.id = d.client_id 
            WHERE d.id = ?
        ''', (document_id,))
        document_data = cursor.fetchone()
        
        if not document_data:
            await query.edit_message_text("❌ Документ не найден")
            return
        
        client_email, client_name, file_path = document_data
        
        # Генерируем код
        code = generate_code()
        lawyer_info = LAWYERS[user_id]
        
        # Отправляем код на email адвоката
        if send_email(lawyer_info['email'], code, client_name):
            # Сохраняем код в базу данных
            expires_at = datetime.now().timestamp() + 600  # 10 минут
            
            cursor.execute('''
                INSERT INTO signature_codes 
                (document_id, user_type, code, expires_at) 
                VALUES (?, ?, ?, datetime(?, 'unixepoch'))
            ''', (document_id, 'lawyer', code, expires_at))
            conn.commit()
            
            # Сохраняем ID документа для проверки кода
            context.user_data['current_document_id'] = document_id
            context.user_data['current_user_type'] = 'lawyer'
            
            await query.edit_message_text(
                f"📧 Код отправлен на {lawyer_info['email']}\n\n"
                f"🔐 Введите 6-значный код здесь:\n"
                f"(действует 10 минут)"
            )
        else:
            await query.edit_message_text("❌ Ошибка отправки email. Попробуйте снова.")
            
    except Exception as e:
        logging.error(f"Ошибка при подготовке подписи: {e}")
        await query.edit_message_text("❌ Ошибка системы. Попробуйте снова.")
    finally:
        conn.close()

async def verify_code_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверка введенного кода"""
    user_id = update.message.from_user.id
    
    if not check_lawyer_access(user_id):
        await update.message.reply_text("🚫 Доступ запрещен")
        return
    
    # Проверяем ожидается ли код
    if 'current_document_id' not in context.user_data:
        await update.message.reply_text("Сначала начните процесс подписи через меню")
        return
    
    entered_code = update.message.text.strip().upper()
    document_id = context.user_data['current_document_id']
    user_type = context.user_data['current_user_type']
    
    # Проверяем код в базе данных
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            SELECT code, expires_at, attempts 
            FROM signature_codes 
            WHERE document_id = ? AND user_type = ?
            ORDER BY created_at DESC 
            LIMIT 1
        ''', (document_id, user_type))
        
        code_data = cursor.fetchone()
        
        if not code_data:
            await update.message.reply_text("❌ Код не найден. Начните процесс подписи заново.")
            context.user_data.clear()
            return
        
        expected_code, expires_at, attempts = code_data
        
        # Проверяем время действия
        if datetime.now() > datetime.strptime(expires_at, '%Y-%m-%d %H:%M:%S'):
            await update.message.reply_text("⏰ Время действия кода истекло. Начните заново.")
            context.user_data.clear()
            return
        
        # Проверяем код
        if entered_code == expected_code:
            # Код верный - подписываем документ
            cursor.execute(
                "UPDATE documents SET lawyer_signed = 1 WHERE id = ?",
                (document_id,)
            )
            
            # Получаем данные для штампа
            cursor.execute('''
                SELECT d.file_path, d.document_hash, c.full_name 
                FROM documents d 
                JOIN clients c ON d.client_id = c.id 
                WHERE d.id = ?
            ''', (document_id,))
            
            stamp_data = cursor.fetchone()
            
            lawyer_info = LAWYERS[user_id]
            
            if stamp_data:
                file_path, document_hash, client_name = stamp_data
                
                # Создаем данные для штампа
                signature_data = {
                    'document_hash': document_hash,
                    'lawyer_signed': True,
                    'lawyer_name': lawyer_info['full_name'],
                    'lawyer_sign_date': datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
                    'client_signed': False,
                    'client_name': client_name
                }
                
                # Добавляем штамп в PDF
                try:
                    signed_file_path = file_path.replace('.pdf', '_signed.pdf')
                    if add_signature_to_pdf(file_path, signature_data, signed_file_path):
                        # Обновляем путь к файлу в базе
                        cursor.execute(
                            "UPDATE documents SET file_path = ? WHERE id = ?",
                            (signed_file_path, document_id)
                        )
                        logging.info(f"Штамп адвоката добавлен в документ {document_id}")
                    else:
                        logging.error(f"Ошибка при добавлении штампа адвоката в документ {document_id}")
                        
                except Exception as e:
                    logging.error(f"Ошибка при добавлении штампа: {e}")
                    # Продолжаем работу даже если штамп не добавился
            
            conn.commit()
            
            # Получаем информацию о клиенте для сообщения
            cursor.execute('''
                SELECT c.full_name 
                FROM clients c 
                JOIN documents d ON c.id = d.client_id 
                WHERE d.id = ?
            ''', (document_id,))
            client_data = cursor.fetchone()
            client_name = client_data[0] if client_data else "клиента"
            
            await update.message.reply_text(
                f"✅ Документ успешно подписан!\n\n"
                f"👤 Документ для {client_name} готов к отправке клиенту.\n"
                f"📄 Штамп электронной подписи добавлен в документ.\n"
                f"Используйте /start для возврата в меню."
            )
            
            # Очищаем временные данные
            context.user_data.clear()
            
        else:
            # Неверный код - увеличиваем счетчик попыток
            cursor.execute(
                "UPDATE signature_codes SET attempts = attempts + 1 WHERE document_id = ? AND user_type = ?",
                (document_id, user_type)
            )
            conn.commit()
            
            await update.message.reply_text(
                f"❌ Неверный код. Попыток: {attempts + 1}/3\n"
                f"Введите код еще раз:"
            )
            
    except Exception as e:
        logging.error(f"Ошибка при проверке кода: {e}")
        await update.message.reply_text("❌ Ошибка системы. Попробуйте снова.")
    finally:
        conn.close()

def main():
    # Инициализируем базу данных при запуске
    init_database()
    
    application = Application.builder().token(BOT_TOKEN_LAWYER).build()
    
    # Обработчик разговора для добавления клиента
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(button_handler, pattern='^add_client$')],
        states={
            EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, email_handler)],
            FULL_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, full_name_handler)],
            DOCUMENT: [MessageHandler(filters.Document.PDF, document_handler)],
        },
        fallbacks=[CommandHandler('start', start)],
        allow_reentry=True
    )
    
    # Обработчик ввода кода подписи
    code_handler = MessageHandler(
        filters.TEXT & ~filters.COMMAND, 
        verify_code_handler
    )
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(sign_document_handler, pattern='^sign_'))
    application.add_handler(code_handler)
    
    print("Бот адвоката запущен...")
    application.run_polling()

if __name__ == "__main__":
    main()
