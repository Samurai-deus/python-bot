"""
Скрипт миграции данных из CSV в SQLite

Запуск:
    python migrate_to_sqlite.py
"""
import logging
from database import migrate_from_csv, get_db_connection

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main():
    """Основная функция миграции"""
    logger.info("🚀 Начало миграции данных из CSV в SQLite")
    
    # Проверяем, что база данных создана
    conn = get_db_connection()
    conn.close()
    logger.info("✅ База данных инициализирована")
    
    # Мигрируем данные
    logger.info("📥 Миграция данных из demo_trades.csv...")
    migrate_from_csv("demo_trades.csv")
    
    logger.info("✅ Миграция завершена!")
    logger.info("📊 Теперь все данные хранятся в SQLite (market_bot.db)")
    logger.info("📝 CSV файлы остаются для логов (signals_log.csv)")


if __name__ == "__main__":
    main()

