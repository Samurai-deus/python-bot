# 🚀 Полная настройка Ubuntu 24.04 для Market Bot

## 📋 Содержание
1. [Базовая настройка системы](#1-базовая-настройка-системы)
2. [Установка Python и зависимостей](#2-установка-python-и-зависимостей)
3. [Настройка проекта](#3-настройка-проекта)
4. [Настройка Systemd Service](#4-настройка-systemd-service)
5. [Оптимизация производительности](#5-оптимизация-производительности)
6. [Мониторинг и логирование](#6-мониторинг-и-логирование)
7. [Безопасность](#7-безопасность)
8. [Резервное копирование](#8-резервное-копирование)

---

## 1. Базовая настройка системы

### 1.1 Обновление системы
```bash
sudo apt update
sudo apt upgrade -y
sudo apt autoremove -y
```

### 1.2 Установка базовых утилит
```bash
sudo apt install -y \
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
```

### 1.3 Настройка часового пояса
```bash
sudo timedatectl set-timezone UTC
timedatectl status
```

### 1.4 Настройка swap (если нужно)
```bash
# Проверить текущий swap
free -h

# Если swap нет или мало, создать:
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile

# Сделать постоянным
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

---

## 2. Установка Python и зависимостей

### 2.1 Установка Python 3.12
```bash
# Ubuntu 24.04 уже имеет Python 3.12, но проверим:
python3 --version

# Если нужно установить Python 3.12:
sudo apt install -y python3.12 python3.12-venv python3.12-dev python3-pip

# Установить pip
sudo apt install -y python3-pip

# Обновить pip
python3 -m pip install --upgrade pip
```

### 2.2 Установка системных библиотек
```bash
sudo apt install -y \
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
```

---

## 3. Настройка проекта

### 3.1 Создание директории проекта
```bash
sudo mkdir -p /root/market_bot
cd /root/market_bot
```

### 3.2 Загрузка проекта
```bash
# Если проект в Git:
# git clone <repository_url> /root/market_bot

# Или скопируйте файлы через SCP/WinSCP
```

### 3.3 Создание виртуального окружения
```bash
cd /root/market_bot
python3.12 -m venv venv
source venv/bin/activate
```

### 3.4 Установка Python пакетов
```bash
# Обновить pip
pip install --upgrade pip setuptools wheel

# Установить зависимости
pip install \
    python-telegram-bot \
    requests \
    pandas \
    numpy \
    psutil
```

### 3.5 Настройка переменных окружения
```bash
# Создать .env файл (если используется)
nano /root/market_bot/.env

# Добавить:
# TELEGRAM_TOKEN=your_token_here
# CHAT_ID=your_chat_id_here
```

### 3.6 Проверка прав доступа
```bash
# Убедиться, что файлы доступны
chmod +x /root/market_bot/runner.py
chmod +x /root/market_bot/setup_service.sh
```

---

## 4. Настройка Systemd Service

### 4.1 Установка service
```bash
cd /root/market_bot
sudo bash setup_service.sh
```

### 4.2 Проверка service файла
```bash
sudo nano /etc/systemd/system/market-bot.service
```

Убедитесь, что пути корректны:
```ini
[Unit]
Description=Market Trading Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/market_bot
Environment="PATH=/root/market_bot/venv/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart=/root/market_bot/venv/bin/python /root/market_bot/runner.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

### 4.3 Запуск и проверка
```bash
# Перезагрузить systemd
sudo systemctl daemon-reload

# Включить автозапуск
sudo systemctl enable market-bot

# Запустить
sudo systemctl start market-bot

# Проверить статус
sudo systemctl status market-bot

# Посмотреть логи
sudo journalctl -u market-bot -f
```

---

## 5. Оптимизация производительности

### 5.1 Настройка лимитов системы
```bash
# Создать файл лимитов
sudo nano /etc/security/limits.d/market-bot.conf
```

Добавить:
```
root soft nofile 65536
root hard nofile 65536
root soft nproc 32768
root hard nproc 32768
```

### 5.2 Настройка sysctl для сетевых соединений
```bash
sudo nano /etc/sysctl.d/99-market-bot.conf
```

Добавить:
```conf
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
```

Применить:
```bash
sudo sysctl -p /etc/sysctl.d/99-market-bot.conf
```

### 5.3 Настройка автоматической очистки логов
```bash
sudo nano /etc/logrotate.d/market-bot
```

Добавить:
```
/root/market_bot/*.log {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
    create 0644 root root
}
```

---

## 6. Мониторинг и логирование

### 6.1 Установка мониторинга (опционально)
```bash
# Установить htop для мониторинга ресурсов
sudo apt install -y htop iotop nethogs

# Установить netdata (опционально, для веб-мониторинга)
bash <(curl -Ss https://my-netdata.io/kickstart.sh)
```

### 6.2 Настройка логирования systemd
```bash
# Ограничить размер логов journald
sudo nano /etc/systemd/journald.conf
```

Изменить:
```ini
SystemMaxUse=500M
SystemKeepFree=1G
SystemMaxFileSize=100M
MaxRetentionSec=7day
```

Перезапустить:
```bash
sudo systemctl restart systemd-journald
```

### 6.3 Создание скрипта мониторинга
```bash
nano /root/market_bot/check_bot.sh
```

Добавить:
```bash
#!/bin/bash
if ! systemctl is-active --quiet market-bot; then
    echo "Bot is not running! Restarting..."
    systemctl restart market-bot
    echo "Bot restarted at $(date)" >> /root/market_bot/bot_restarts.log
fi
```

Сделать исполняемым:
```bash
chmod +x /root/market_bot/check_bot.sh
```

Добавить в cron (проверка каждые 5 минут):
```bash
crontab -e
```

Добавить:
```
*/5 * * * * /root/market_bot/check_bot.sh
```

---

## 7. Безопасность

### 7.1 Настройка Firewall (UFW)
```bash
# Разрешить SSH
sudo ufw allow 22/tcp

# Разрешить другие необходимые порты (если нужно)
# sudo ufw allow 80/tcp
# sudo ufw allow 443/tcp

# Включить firewall
sudo ufw enable
sudo ufw status
```

### 7.2 Настройка Fail2Ban
```bash
# Fail2Ban уже установлен, создадим конфиг для SSH
sudo nano /etc/fail2ban/jail.local
```

Добавить:
```ini
[DEFAULT]
bantime = 3600
findtime = 600
maxretry = 5

[sshd]
enabled = true
port = 22
logpath = /var/log/auth.log
```

Запустить:
```bash
sudo systemctl enable fail2ban
sudo systemctl start fail2ban
sudo fail2ban-client status
```

### 7.3 Отключение root логина по SSH (рекомендуется)
```bash
# Создать нового пользователя
sudo adduser trader
sudo usermod -aG sudo trader

# Настроить SSH ключи
sudo mkdir -p /home/trader/.ssh
sudo nano /home/trader/.ssh/authorized_keys
# Вставить ваш публичный SSH ключ

# Настроить SSH
sudo nano /etc/ssh/sshd_config
```

Изменить:
```
PermitRootLogin no
PasswordAuthentication no
PubkeyAuthentication yes
```

Перезапустить SSH:
```bash
sudo systemctl restart sshd
```

### 7.4 Настройка автоматических обновлений безопасности
```bash
sudo apt install -y unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades
```

---

## 8. Резервное копирование

### 8.1 Создание скрипта резервного копирования
```bash
nano /root/market_bot/backup.sh
```

Добавить:
```bash
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
    --exclude='*.pyc'

# Удалить старые бэкапы (старше 7 дней)
find $BACKUP_DIR -name "market_bot_*.tar.gz" -mtime +7 -delete

echo "Backup completed: market_bot_$DATE.tar.gz"
```

Сделать исполняемым:
```bash
chmod +x /root/market_bot/backup.sh
```

### 8.2 Настройка автоматического бэкапа
```bash
crontab -e
```

Добавить (бэкап каждый день в 3:00):
```
0 3 * * * /root/market_bot/backup.sh >> /root/market_bot/backup.log 2>&1
```

---

## 9. Быстрая проверка после установки

```bash
# Проверить статус бота
sudo systemctl status market-bot

# Проверить логи
sudo journalctl -u market-bot -n 50

# Проверить использование ресурсов
htop

# Проверить сетевые соединения
netstat -tulpn | grep python

# Проверить дисковое пространство
df -h

# Проверить память
free -h
```

---

## 10. Полезные команды

### Управление ботом
```bash
# Статус
sudo systemctl status market-bot

# Запуск
sudo systemctl start market-bot

# Остановка
sudo systemctl stop market-bot

# Перезапуск
sudo systemctl restart market-bot

# Логи в реальном времени
sudo journalctl -u market-bot -f

# Логи за последний час
sudo journalctl -u market-bot --since "1 hour ago"

# Логи за сегодня
sudo journalctl -u market-bot --since today
```

### Мониторинг
```bash
# Использование CPU и памяти
top
htop

# Использование диска
df -h
du -sh /root/market_bot/*

# Сетевые соединения
ss -tulpn
netstat -tulpn

# Процессы Python
ps aux | grep python
```

---

## ✅ Чеклист после установки

- [ ] Система обновлена
- [ ] Python 3.12 установлен
- [ ] Виртуальное окружение создано
- [ ] Все зависимости установлены
- [ ] Systemd service настроен и запущен
- [ ] Бот работает (проверено через `systemctl status`)
- [ ] Логи доступны и читаемы
- [ ] Firewall настроен
- [ ] Fail2Ban настроен
- [ ] Резервное копирование настроено
- [ ] Мониторинг настроен
- [ ] Автоматическая проверка бота в cron настроена

---

## 🆘 Решение проблем

### Бот не запускается
```bash
# Проверить логи
sudo journalctl -u market-bot -n 100

# Проверить права доступа
ls -la /root/market_bot/

# Проверить виртуальное окружение
/root/market_bot/venv/bin/python --version
```

### Проблемы с зависимостями
```bash
# Переустановить зависимости
cd /root/market_bot
source venv/bin/activate
pip install --upgrade --force-reinstall -r requirements.txt
```

### Проблемы с памятью
```bash
# Проверить использование памяти
free -h
ps aux --sort=-%mem | head

# Увеличить swap если нужно
```

### Проблемы с сетью
```bash
# Проверить соединение с API
curl -I https://api.bybit.com
curl -I https://api.telegram.org

# Проверить DNS
nslookup api.bybit.com
```

---

**Готово! Ваш сервер настроен для стабильной работы бота 24/7.** 🚀

