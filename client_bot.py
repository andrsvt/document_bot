#!/usr/bin/env python3
import logging
import sqlite3
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

# Состояния для клиента
EMAIL_VERIFICATION = 1

# Загружаем секреты
from secrets import BOT_TOKEN_CLIENT, EMAIL_HOST, EMAIL_PORT, EMAIL_USER, EMAIL_PASSWORD, LAWYERS

# Импорты для email
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Импорты для PDF штампов
from pdf_stamp import add_signature_to_pdf

def generate_code():
    """Генерирует 6-значный код"""
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

def send_email(to_email, code, user_name=None):
    """Отправляет код на email клиента"""
    try:
        # Создаем сообщение
        message = MIMEMultipart()
        message['From'] = EMAIL_USER
        message['To'] = to_email
        
        if user_name:
            message['Subject'] = "Код для подписи документа"
            body = f"""Уважаемый(ая) {user_name}!

🔐 Ваш код для подписи документа: {code}

⏰ Код действителен 10 минут

📄 После подписи документ будет иметь юридическую силу

С уважением,
Система электронного документооборота"""
        else:
            message['Subject'] = "Код для подписи документа"
            body = f"""🔐 Ваш код для подписи документа: {code}

⏰ Код действителен 10 минут

📄 После подписи документ будет иметь юридическую силу"""
        
        # Добавляем текст с правильной кодировкой
        message.attach(MIMEText(body, 'plain', 'utf-8'))
        
        with smtplib.SMTP(EMAIL_HOST, EMAIL_PORT) as server:
            server.starttls()
            server.login(EMAIL_USER, EMAIL_PASSWORD)
            server.send_message(message)
        
        logging.info(f"Код отправлен клиенту {to_email}")
        return True
    except Exception as e:
        logging.error(f"Ошибка отправки email клиенту: {e}")
        return False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start для клиента"""
    # Очищаем временные данные
    context.user_data.clear()
    
    await update.message.reply_text(
        "👋 Добро пожаловать в систему электронной подписи!\n\n"
        "📧 Для начала работы введите ваш email:"
    )
    
    return EMAIL_VERIFICATION

async def email_verification_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка ввода email клиентом"""
    email = update.message.text.strip().lower()
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        # Ищем клиента по email
        cursor.execute("SELECT id, full_name FROM clients WHERE email = ?", (email,))
        client_data = cursor.fetchone()
        
        if not client_data:
            keyboard = [
                [InlineKeyboardButton("📞 Чат с поддержкой", url="https://t.me/your_support")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                f"❌ Email {email} не найден в системе.\n\n"
                "Возможные причины:\n"
                "• Email введен с ошибкой\n"
                "• Адвокат еще не добавил вас в систему\n"
                "• Обратитесь к вашему адвокату",
                reply_markup=reply_markup
            )
            return ConversationHandler.END
        
        client_id, client_name = client_data
        
        # Сохраняем данные клиента
        context.user_data['client_id'] = client_id
        context.user_data['client_name'] = client_name
        context.user_data['client_email'] = email
        
        # Проверяем есть ли документы для подписи
        cursor.execute('''
            SELECT COUNT(*) FROM documents 
            WHERE client_id = ? AND lawyer_signed = 1 AND client_signed = 0
        ''', (client_id,))
        doc_count = cursor.fetchone()[0]
        
        if doc_count > 0:
            keyboard = [
                [InlineKeyboardButton("📄 Открыть документ", callback_data=f"view_doc_{client_id}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                f"✅ Добро пожаловать, {client_name}!\n"
                f"🔍 Найдено документов для подписи: {doc_count}",
                reply_markup=reply_markup
            )
        else:
            await update.message.reply_text(
                f"✅ Добро пожаловать, {client_name}!\n"
                f"📭 На данный момент нет документов для подписи.\n"
                f"Ожидайте уведомления от вашего адвоката."
            )
        
        return ConversationHandler.END
            
    except Exception as e:
        logging.error(f"Ошибка при поиске клиента: {e}")
        await update.message.reply_text("❌ Ошибка системы. Попробуйте позже.")
        return ConversationHandler.END
    finally:
        conn.close()

async def view_document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показ документа клиенту"""
    query = update.callback_query
    await query.answer()
    
    # Получаем ID клиента из callback_data
    client_id = int(query.data.replace('view_doc_', ''))
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        # Получаем последний документ для клиента
        cursor.execute('''
            SELECT d.id, d.file_path, c.full_name, c.email, d.document_hash
            FROM documents d 
            JOIN clients c ON d.client_id = c.id 
            WHERE d.client_id = ? AND d.lawyer_signed = 1 AND d.client_signed = 0
            ORDER BY d.created_at DESC 
            LIMIT 1
        ''', (client_id,))
        
        doc_data = cursor.fetchone()
        
        if not doc_data:
            await query.edit_message_text("❌ Документ не найден или уже подписан")
            return
        
        doc_id, file_path, client_name, client_email, document_hash = doc_data
        
        # Сохраняем данные для подписи
        context.user_data['current_doc_id'] = doc_id
        context.user_data['client_email'] = client_email
        context.user_data['client_name'] = client_name
        context.user_data['document_hash'] = document_hash
        
        # Отправляем информацию о документе
        await query.edit_message_text(
            f"📋 Документ: Соглашение\n"
            f"👤 Для: {client_name}\n"
            f"📧 Email: {client_email}\n"
            f"🔐 ID документа: {document_hash}\n"
            f"✅ Подписан адвокатом\n"
            f"⏳ Ожидает вашей подписи\n\n"
            f"Отправляем документ..."
        )
        
        # Отправляем сам документ
        with open(file_path, 'rb') as doc_file:
            await context.bot.send_document(
                chat_id=query.message.chat_id,
                document=doc_file,
                filename=f"document_{doc_id}.pdf",
                caption="📄 Ваш документ для подписи"
            )
        
        # Показываем кнопку подписи
        keyboard = [
            [InlineKeyboardButton("🖊 Подписать документ", callback_data=f"client_sign_{doc_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="📄 Документ готов к подписи",
            reply_markup=reply_markup
        )
        
    except FileNotFoundError:
        await query.edit_message_text("❌ Файл документа не найден на сервере")
    except Exception as e:
        logging.error(f"Ошибка при показе документа: {e}")
        await query.edit_message_text("❌ Ошибка при загрузке документа")
    finally:
        conn.close()

async def client_sign_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик подписи документа клиентом"""
    query = update.callback_query
    await query.answer()
    
    # Получаем ID документа
    doc_id = int(query.data.replace('client_sign_', ''))
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        # Получаем данные клиента
        cursor.execute('''
            SELECT c.email, c.full_name 
            FROM clients c 
            JOIN documents d ON c.id = d.client_id 
            WHERE d.id = ?
        ''', (doc_id,))
        
        client_data = cursor.fetchone()
        
        if not client_data:
            await query.edit_message_text("❌ Данные клиента не найдены")
            return
        
        client_email, client_name = client_data
        
        # Генерируем код
        code = generate_code()
        
        # Отправляем код на email клиента
        if send_email(client_email, code, client_name):
            # Сохраняем код в базу данных
            expires_at = datetime.now().timestamp() + 600  # 10 минут
            
            cursor.execute('''
                INSERT INTO signature_codes 
                (document_id, user_type, code, expires_at) 
                VALUES (?, ?, ?, datetime(?, 'unixepoch'))
            ''', (doc_id, 'client', code, expires_at))
            conn.commit()
            
            # Сохраняем данные для проверки кода
            context.user_data['current_doc_id'] = doc_id
            context.user_data['current_user_type'] = 'client'
            context.user_data['client_name'] = client_name
            
            await query.edit_message_text(
                f"📧 Код отправлен на {client_email}\n\n"
                f"🔐 Введите 6-значный код здесь:\n"
                f"(действует 10 минут)\n\n"
                f"👤 Получатель: {client_name}"
            )
        else:
            keyboard = [
                [InlineKeyboardButton("🔄 Попробовать снова", callback_data=f"client_sign_{doc_id}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                "❌ Ошибка отправки email. Попробуйте снова.",
                reply_markup=reply_markup
            )
            
    except Exception as e:
        logging.error(f"Ошибка при подготовке подписи клиента: {e}")
        await query.edit_message_text("❌ Ошибка системы. Попробуйте снова.")
    finally:
        conn.close()

async def verify_client_code_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверка введенного кода клиентом"""
    # Проверяем ожидается ли код
    if 'current_doc_id' not in context.user_data:
        await update.message.reply_text("Сначала начните процесс подписи через меню")
        return
    
    entered_code = update.message.text.strip().upper()
    doc_id = context.user_data['current_doc_id']
    user_type = context.user_data['current_user_type']
    client_name = context.user_data.get('client_name', 'клиент')
    
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
        ''', (doc_id, user_type))
        
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
            # Код верный - подписываем документ клиентом
            cursor.execute(
                "UPDATE documents SET client_signed = 1 WHERE id = ?",
                (doc_id,)
            )
            
            # Получаем данные для штампа
            cursor.execute('''
                SELECT d.file_path, d.document_hash 
                FROM documents d 
                WHERE d.id = ?
            ''', (doc_id,))
            
            stamp_data = cursor.fetchone()
            
            if stamp_data:
                file_path, document_hash = stamp_data
                
                # Берем первого адвоката из списка LAWYERS
                lawyer_name = list(LAWYERS.values())[0]['full_name'] if LAWYERS else "Адвокат"
                lawyer_sign_date = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
                
                lawyer_data = cursor.fetchone()
                lawyer_name = lawyer_data[0] if lawyer_data else "Адвокат"
                lawyer_sign_date = lawyer_data[1] if lawyer_data else datetime.now().strftime("%d.%m.%Y %H:%M:%S")
                
                # Создаем данные для штампа
                signature_data = {
                    'document_hash': document_hash,
                    'lawyer_signed': True,
                    'lawyer_name': lawyer_name,
                    'lawyer_sign_date': lawyer_sign_date,
                    'client_signed': True,
                    'client_name': client_name,
                    'client_sign_date': datetime.now().strftime("%d.%m.%Y %H:%M:%S")
                }
                
                # Добавляем штамп в PDF
                try:
                    final_file_path = file_path.replace('.pdf', '_final.pdf')
                    if add_signature_to_pdf(file_path, signature_data, final_file_path):
                        # Обновляем путь к файлу в базе
                        cursor.execute(
                            "UPDATE documents SET file_path = ? WHERE id = ?",
                            (final_file_path, doc_id)
                        )
                        logging.info(f"Штамп клиента добавлен в документ {doc_id}")
                    else:
                        logging.error(f"Ошибка при добавлении штампа клиента в документ {doc_id}")
                        
                except Exception as e:
                    logging.error(f"Ошибка при добавлении штампа: {e}")
                    # Продолжаем работу даже если штамп не добавился
            
            conn.commit()
            
            # Получаем путь к файлу для отправки
            cursor.execute("SELECT file_path FROM documents WHERE id = ?", (doc_id,))
            file_path = cursor.fetchone()[0]
            
            await update.message.reply_text(
                f"✅ Документ успешно подписан!\n\n"
                f"👤 {client_name}, ваша подпись добавлена в документ.\n"
                f"📄 Отправляем подписанный документ..."
            )
            
            # Отправляем подписанный документ
            with open(file_path, 'rb') as doc_file:
                await update.message.reply_document(
                    document=doc_file,
                    filename=f"подписанный_документ_{doc_id}.pdf",
                    caption="📄 Документ подписан вами и адвокатом"
                )
            
            await update.message.reply_text(
                "🎉 Процесс подписания завершен!\n"
                "Для проверки других документов используйте /start"
            )
            
            # Очищаем временные данные
            context.user_data.clear()
            
        else:
            # Неверный код - увеличиваем счетчик попыток
            cursor.execute(
                "UPDATE signature_codes SET attempts = attempts + 1 WHERE document_id = ? AND user_type = ?",
                (doc_id, user_type)
            )
            conn.commit()
            
            remaining_attempts = 3 - (attempts + 1)
            
            if remaining_attempts > 0:
                await update.message.reply_text(
                    f"❌ Неверный код. Попыток: {attempts + 1}/3\n"
                    f"Осталось попыток: {remaining_attempts}\n"
                    f"Введите код еще раз:"
                )
            else:
                keyboard = [
                    [InlineKeyboardButton("🔄 Запросить новый код", callback_data=f"client_sign_{doc_id}")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await update.message.reply_text(
                    "🚫 Превышено количество попыток.\n"
                    "Запросите новый код для подписи.",
                    reply_markup=reply_markup
                )
                context.user_data.clear()
            
    except Exception as e:
        logging.error(f"Ошибка при проверке кода клиента: {e}")
        await update.message.reply_text("❌ Ошибка системы. Попробуйте снова.")
    finally:
        conn.close()

def main():
    application = Application.builder().token(BOT_TOKEN_CLIENT).build()
    
    # Обработчик разговора для проверки email
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            EMAIL_VERIFICATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, email_verification_handler)],
        },
        fallbacks=[]
    )
    
    # Обработчики кнопок
    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(view_document_handler, pattern='^view_doc_'))
    application.add_handler(CallbackQueryHandler(client_sign_handler, pattern='^client_sign_'))
    
    # Обработчик ввода кода клиентом
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, 
        verify_client_code_handler
    ))
    
    print("Бот клиента запущен...")
    application.run_polling()

if __name__ == "__main__":
    main()
