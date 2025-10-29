#!/usr/bin/env python3

import os
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from PyPDF2 import PdfReader, PdfWriter
from datetime import datetime
import hashlib

def generate_document_hash(client_id, document_name):
    """Генерирует уникальный хеш документа"""
    unique_string = f"{client_id}_{document_name}_{datetime.now().timestamp()}"
    return hashlib.md5(unique_string.encode()).hexdigest()

def create_signature_stamp(signature_data, output_path):
    """Создает PDF с правильным штампом подписи"""
    c = canvas.Canvas(output_path, pagesize=A4)
    width, height = A4

    try:
        # Регистрируем шрифты
        pdfmetrics.registerFont(TTFont('DejaVuSans', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'))
        pdfmetrics.registerFont(TTFont('DejaVuSans-Bold', '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'))
        
        # Параметры штампа
        stamp_width = 303  # 8.58 см
        stamp_x = 54       # 1.91 см от левого края
        stamp_y = 50       # от нижнего края
        
        # Рассчитываем высоту штампа
        line_height = 7    # высота строки
        num_lines = 3      # базовые строки
        
        if signature_data.get('lawyer_signed'):
            num_lines += 3  # адвокат: заголовок + 2 строки
            
        if signature_data.get('client_signed'):
            num_lines += 3  # клиент: заголовок + 2 строки
            
        stamp_height = num_lines * line_height + 20  # общая высота
        
        # Рамка штампа
        c.setStrokeColorRGB(0.56, 0.66, 0.86)  # #8FA8DB в RGB
        c.setLineWidth(1.5)
        c.rect(stamp_x, stamp_y, stamp_width, stamp_height)
        
        # Текст штампа
        c.setFillColorRGB(0.56, 0.66, 0.86)  # #8FA8DB в RGB
        text_x = stamp_x + 8  # 0.2 см от левого края рамки
        current_y = stamp_y + stamp_height - 10  # отступ сверху
        
        # Основной текст
        c.setFont("DejaVuSans-Bold", 7)
        c.drawString(text_x, current_y, "Документ подписан простой электронной подписью")
        current_y -= line_height
        
        c.setFont("DejaVuSans", 7)
        c.drawString(text_x, current_y, f"ID документа: {signature_data['document_hash']}")
        current_y -= line_height
        
        # Подпись адвоката
        if signature_data.get('lawyer_signed'):
            c.setFont("DejaVuSans-Bold", 7)
            c.drawString(text_x, current_y, "Адвокат")
            current_y -= line_height
            
            c.setFont("DejaVuSans", 7)
            c.drawString(text_x, current_y, f"Подписант: {signature_data.get('lawyer_name', '')}")
            current_y -= line_height
            
            c.drawString(text_x, current_y, f"Дата и время подписи: {signature_data.get('lawyer_sign_date', '')} MSK")
            current_y -= line_height
        
        # Подпись клиента
        if signature_data.get('client_signed'):
            c.setFont("DejaVuSans-Bold", 7)
            c.drawString(text_x, current_y, "Клиент")
            current_y -= line_height
            
            c.setFont("DejaVuSans", 7)
            c.drawString(text_x, current_y, f"Подписант: {signature_data.get('client_name', '')}")
            current_y -= line_height
            
            c.drawString(text_x, current_y, f"Дата и время подписи: {signature_data.get('client_sign_date', '')} MSK")
            
    except Exception as e:
        print(f"Ошибка создания штампа: {e}")
        # Английский fallback
        c.setFont("Helvetica", 7)
        c.drawString(50, 50, f"Document ID: {signature_data['document_hash']}")

    c.save()

def add_signature_to_pdf(original_pdf_path, signature_data, output_pdf_path):
    """Добавляет штамп подписи в существующий PDF"""
    try:
        # Создаем временный файл со штампом
        stamp_path = "/tmp/signature_stamp.pdf"
        create_signature_stamp(signature_data, stamp_path)
        
        # Открываем оригинальный PDF и штамп
        original_pdf = PdfReader(original_pdf_path)
        stamp_pdf = PdfReader(stamp_path)
        
        # Создаем writer для нового PDF
        writer = PdfWriter()
        
        # Для каждой страницы добавляем штамп
        for page_num in range(len(original_pdf.pages)):
            page = original_pdf.pages[page_num]
            
            # Если это последняя страница - добавляем штамп
            if page_num == len(original_pdf.pages) - 1:
                stamp_page = stamp_pdf.pages[0]
                page.merge_page(stamp_page)
            
            writer.add_page(page)
        
        # Сохраняем результат
        with open(output_pdf_path, 'wb') as output_file:
            writer.write(output_file)
        
        # Удаляем временный файл
        os.remove(stamp_path)
        return True
        
    except Exception as e:
        print(f"Ошибка при добавлении штампа: {e}")
        return False

def update_document_hash_in_db(document_id, document_hash):
    """Обновляет хеш документа в базе данных"""
    import sqlite3
    conn = sqlite3.connect('/opt/bots/documents.db')
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE documents SET document_hash = ? WHERE id = ?",
            (document_hash, document_id)
        )
        conn.commit()
    except Exception as e:
        print(f"Ошибка при обновлении хеша: {e}")
    finally:
        conn.close()
