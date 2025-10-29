#!/bin/bash
echo "Установка системы документооборота..."

# Создаем папки
mkdir -p /opt/bots/documents

# Копируем файлы
cp *.py /opt/bots/
chmod +x /opt/bots/*.py

# Настраиваем secrets.py если нужно
if [ ! -f "/opt/bots/secrets.py" ]; then
    echo "Создайте файл /opt/bots/secrets.py на основе secrets.example.py"
    echo "Заполните реальными токенами и настройками"
fi

# Копируем службы
cp *.service /etc/systemd/system/

# Обновляем systemd
systemctl daemon-reload

echo "Установка завершена!"
echo "Не забудьте:"
echo "1. Настроить secrets.py с вашими данными"
echo "2. Запустить ботов: systemctl start lawyer-bot client-bot"
echo "3. Включить автозапуск: systemctl enable lawyer-bot client-bot"
