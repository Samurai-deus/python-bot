#!/bin/bash
# Скрипт для установки market-bot как systemd service

echo "🚀 Установка Market Bot как systemd service..."

# Проверяем, что мы root
if [ "$EUID" -ne 0 ]; then 
    echo "❌ Запустите скрипт с sudo: sudo bash setup_service.sh"
    exit 1
fi

# Путь к проекту
PROJECT_DIR="/root/market_bot"
SERVICE_FILE="$PROJECT_DIR/market-bot.service"
SYSTEMD_PATH="/etc/systemd/system/market-bot.service"

# Проверяем существование файла service
if [ ! -f "$SERVICE_FILE" ]; then
    echo "❌ Файл $SERVICE_FILE не найден!"
    exit 1
fi

# Копируем service файл
echo "📋 Копирование service файла..."
cp "$SERVICE_FILE" "$SYSTEMD_PATH"

# Перезагружаем systemd
echo "🔄 Перезагрузка systemd daemon..."
systemctl daemon-reload

# Включаем автозапуск
echo "✅ Включение автозапуска..."
systemctl enable market-bot

echo ""
echo "✅ Установка завершена!"
echo ""
echo "📝 Команды для управления:"
echo "   Запустить:    sudo systemctl start market-bot"
echo "   Остановить:   sudo systemctl stop market-bot"
echo "   Статус:       sudo systemctl status market-bot"
echo "   Логи:         sudo journalctl -u market-bot -f"
echo "   Перезапуск:   sudo systemctl restart market-bot"
echo ""
echo "🚀 Запустить бота сейчас? (y/n)"
read -r answer
if [ "$answer" = "y" ] || [ "$answer" = "Y" ]; then
    systemctl start market-bot
    echo "✅ Бот запущен!"
    sleep 2
    systemctl status market-bot
fi

