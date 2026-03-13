#!/bin/bash
# Полная настройка Ubuntu 24.04 для Market Bot
# Запуск: sudo bash setup_ubuntu_server.sh

set -e  # Остановить при ошибке

echo "🚀 Настройка Ubuntu 24.04 для Market Bot"
echo "========================================"

# Проверка прав root
if [ "$EUID" -ne 0 ]; then 
    echo "❌ Запустите скрипт с sudo: sudo bash setup_ubuntu_server.sh"
    exit 1
fi

# Цвета для вывода
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 1. Обновление системы
echo -e "\n${YELLOW}[1/10] Обновление системы...${NC}"
apt update
apt upgrade -y
apt autoremove -y

# 2. Установка базовых утилит
echo -e "\n${YELLOW}[2/10] Установка базовых утилит...${NC}"
apt install -y \
    curl \
    wget \
    git \
    build-essential \
    software-properties-common \
    apt-transport-https \
    ca-certificates \
    gnupg \
    lsb-release \
    htop \
    nano \
    ufw \
    fail2ban \
    logrotate

# 3. Установка Python 3.12 и зависимостей
echo -e "\n${YELLOW}[3/10] Установка Python и зависимостей...${NC}"
apt install -y python3.12 python3.12-venv python3.12-dev python3-pip

# Установка системных библиотек
apt install -y \
    libssl-dev \
    libffi-dev \
    libbz2-dev \
    libreadline-dev \
    libsqlite3-dev \
    libncurses5-dev \
    libncursesw5-dev \
    xz-utils \
    tk-dev \
    libxml2-dev \
    libxmlsec1-dev

# Обновление pip
python3 -m pip install --upgrade pip

# 4. Настройка часового пояса
echo -e "\n${YELLOW}[4/10] Настройка часового пояса...${NC}"
timedatectl set-timezone UTC
echo -e "${GREEN}✓ Часовой пояс установлен в UTC${NC}"

# 5. Настройка swap (если нужно)
echo -e "\n${YELLOW}[5/10] Проверка swap...${NC}"
if [ -f /swapfile ]; then
    echo "Swap файл уже существует"
else
    echo "Создание swap файла 2GB..."
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
    echo -e "${GREEN}✓ Swap файл создан${NC}"
fi

# 6. Настройка лимитов системы
echo -e "\n${YELLOW}[6/10] Настройка системных лимитов...${NC}"
cat > /etc/security/limits.d/market-bot.conf << EOF
root soft nofile 65536
root hard nofile 65536
root soft nproc 32768
root hard nproc 32768
EOF
echo -e "${GREEN}✓ Лимиты системы настроены${NC}"

# 7. Настройка sysctl
echo -e "\n${YELLOW}[7/10] Настройка сетевых параметров...${NC}"
cat > /etc/sysctl.d/99-market-bot.conf << EOF
# Увеличение лимитов для сетевых соединений
net.core.somaxconn = 1024
net.ipv4.tcp_max_syn_backlog = 2048
net.ipv4.tcp_fin_timeout = 30
net.ipv4.tcp_keepalive_time = 300
net.ipv4.tcp_keepalive_probes = 5
net.ipv4.tcp_keepalive_intvl = 15

# Оптимизация памяти
vm.swappiness = 10
vm.dirty_ratio = 60
vm.dirty_background_ratio = 2
EOF
sysctl -p /etc/sysctl.d/99-market-bot.conf
echo -e "${GREEN}✓ Сетевые параметры настроены${NC}"

# 8. Настройка logrotate
echo -e "\n${YELLOW}[8/10] Настройка ротации логов...${NC}"
cat > /etc/logrotate.d/market-bot << EOF
/root/market_bot/*.log {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
    create 0644 root root
}
EOF
echo -e "${GREEN}✓ Ротация логов настроена${NC}"

# 9. Настройка Firewall
echo -e "\n${YELLOW}[9/10] Настройка Firewall (UFW)...${NC}"
ufw --force enable
ufw allow 22/tcp
echo -e "${GREEN}✓ Firewall настроен${NC}"

# 10. Настройка Fail2Ban
echo -e "\n${YELLOW}[10/10] Настройка Fail2Ban...${NC}"
cat > /etc/fail2ban/jail.local << EOF
[DEFAULT]
bantime = 3600
findtime = 600
maxretry = 5

[sshd]
enabled = true
port = 22
logpath = /var/log/auth.log
EOF
systemctl enable fail2ban
systemctl restart fail2ban
echo -e "${GREEN}✓ Fail2Ban настроен${NC}"

# 11. Создание скрипта мониторинга
echo -e "\n${YELLOW}[11/11] Создание скрипта мониторинга...${NC}"
PROJECT_DIR="/root/market_bot"
mkdir -p $PROJECT_DIR

cat > $PROJECT_DIR/check_bot.sh << 'EOF'
#!/bin/bash
if ! systemctl is-active --quiet market-bot; then
    echo "Bot is not running! Restarting..."
    systemctl restart market-bot
    echo "Bot restarted at $(date)" >> /root/market_bot/bot_restarts.log
fi
EOF
chmod +x $PROJECT_DIR/check_bot.sh
echo -e "${GREEN}✓ Скрипт мониторинга создан${NC}"

# 12. Создание скрипта резервного копирования
cat > $PROJECT_DIR/backup.sh << 'EOF'
#!/bin/bash
BACKUP_DIR="/root/backups"
DATE=$(date +%Y%m%d_%H%M%S)
PROJECT_DIR="/root/market_bot"

mkdir -p $BACKUP_DIR

# Создать архив проекта
tar -czf $BACKUP_DIR/market_bot_$DATE.tar.gz \
    -C /root market_bot \
    --exclude='venv' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='*.log'

# Удалить старые бэкапы (старше 7 дней)
find $BACKUP_DIR -name "market_bot_*.tar.gz" -mtime +7 -delete

echo "Backup completed: market_bot_$DATE.tar.gz"
EOF
chmod +x $PROJECT_DIR/backup.sh
echo -e "${GREEN}✓ Скрипт резервного копирования создан${NC}"

# Итоговое сообщение
echo -e "\n${GREEN}========================================${NC}"
echo -e "${GREEN}✅ Настройка сервера завершена!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "📝 Следующие шаги:"
echo "1. Загрузите проект в /root/market_bot"
echo "2. Создайте виртуальное окружение:"
echo "   cd /root/market_bot"
echo "   python3.12 -m venv venv"
echo "   source venv/bin/activate"
echo "   pip install -r requirements.txt"
echo ""
echo "3. Настройте переменные окружения (если нужно)"
echo "4. Запустите установку service:"
echo "   sudo bash setup_service.sh"
echo ""
echo "5. Добавьте мониторинг в cron:"
echo "   crontab -e"
echo "   Добавьте: */5 * * * * /root/market_bot/check_bot.sh"
echo ""
echo "6. Добавьте резервное копирование в cron:"
echo "   Добавьте: 0 3 * * * /root/market_bot/backup.sh"
echo ""
echo "📊 Проверка системы:"
echo "   htop          - мониторинг ресурсов"
echo "   systemctl status market-bot - статус бота"
echo "   journalctl -u market-bot -f - логи бота"
echo ""

