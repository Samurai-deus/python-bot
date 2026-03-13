"""
Модуль для расчета статистики работы бота
"""
from datetime import datetime, UTC, timedelta
from capital import get_current_balance
from config import INITIAL_BALANCE
from database import get_trades_statistics
from core.market_state import MarketState, normalize_state


def get_trade_statistics(days=1):
    """
    Рассчитывает статистику по сделкам за последние N дней из SQLite.
    
    Args:
        days: Количество дней для анализа
    
    Returns:
        dict: Статистика по сделкам
    """
    stats = get_trades_statistics(days)
    
    if not stats:
        return None
    
    # Добавляем total_pnl_pct и другие поля для совместимости
    current_balance = get_current_balance()
    if INITIAL_BALANCE > 0:
        stats['total_pnl_pct'] = ((current_balance - INITIAL_BALANCE) / INITIAL_BALANCE) * 100
    else:
        stats['total_pnl_pct'] = 0.0
    
    # Добавляем поля для совместимости со старым форматом
    stats['current_balance'] = current_balance
    stats['initial_balance'] = INITIAL_BALANCE
    stats['wins'] = stats.get('winning_trades', 0)
    stats['losses'] = stats.get('losing_trades', 0)
    stats['period_days'] = days
    
    return stats


def get_signals_statistics(limit=20):
    """
    Получает последние N сигналов из CSV (логи остаются в CSV).
    
    Args:
        limit: Количество последних сигналов
    
    Returns:
        list: Список последних сигналов с гарантированными полями
    """
    import csv
    import os
    import logging
    
    logger = logging.getLogger(__name__)
    
    if not os.path.exists("signals_log.csv"):
        logger.debug("Файл signals_log.csv не найден")
        return []
    
    signals = []
    try:
        with open("signals_log.csv", "r", encoding="utf-8") as f:
            # Сначала пробуем прочитать как DictReader (если есть заголовки)
            f.seek(0)
            reader = csv.DictReader(f)
            rows = list(reader)
            
            # Проверяем, есть ли валидные заголовки
            has_headers = rows and len(rows) > 0 and any(rows[0].keys())
            
            if has_headers:
                # Есть заголовки, используем DictReader
                logger.debug(f"Чтение CSV с заголовками. Найдено строк: {len(rows)}")
                
                # Логируем первую строку для отладки
                if rows:
                    first_row_keys = list(rows[0].keys())
                    logger.debug(f"Ключи в первой строке: {first_row_keys}")
                
                for row in rows[-limit:]:
                    try:
                        # Нормализуем ключи (убираем пробелы, приводим к нижнему регистру для сравнения)
                        row_normalized = {k.strip().lower(): v for k, v in row.items()}
                        
                        # Пробуем разные варианты ключей
                        timestamp = (
                            row.get('timestamp') or 
                            row_normalized.get('timestamp') or 
                            ''
                        ).strip()
                        
                        symbol = (
                            row.get('symbol') or 
                            row_normalized.get('symbol') or 
                            ''
                        ).strip()
                        
                        state_15m_raw = (
                            row.get('state_15m') or 
                            row_normalized.get('state_15m') or 
                            row.get('state_15m') or  # Пробуем оригинальный ключ
                            ''
                        ).strip()
                        
                        # Нормализуем состояние: валидируем и преобразуем в строку
                        # normalize_state() вернёт MarketState enum или None для невалидных значений
                        state_15m_normalized = normalize_state(state_15m_raw)
                        state_15m = state_15m_normalized.value if state_15m_normalized else (state_15m_raw if state_15m_raw else 'N/A')
                        
                        risk = (
                            row.get('risk') or 
                            row_normalized.get('risk') or 
                            ''
                        ).strip()
                        
                        # Логируем для отладки (только первые несколько)
                        if len(signals) < 2:
                            logger.debug(f"Сигнал #{len(signals)+1}: symbol={symbol}, state_15m={state_15m} (raw: {state_15m_raw}), risk={risk}, timestamp={timestamp[:20]}")
                        
                        signals.append({
                            'timestamp': timestamp or 'N/A',
                            'symbol': symbol or 'N/A',
                            'state_15m': state_15m or 'N/A',
                            'risk': risk or 'N/A'
                        })
                    except Exception as e:
                        logger.warning(f"Ошибка парсинга строки сигнала: {e}")
                        continue
            else:
                # Нет заголовков или они не распознаны, читаем как обычный reader
                f.seek(0)
                reader = csv.reader(f)
                rows = list(reader)
                
                logger.debug(f"Чтение CSV без заголовков. Найдено строк: {len(rows)}")
                
                # Пропускаем первую строку, если она похожа на заголовки
                if rows and len(rows) > 0:
                    first_row = rows[0]
                    if first_row and len(first_row) > 0 and first_row[0].lower() in ['timestamp', 'time']:
                        rows = rows[1:]  # Пропускаем заголовок
                        logger.debug("Пропущена строка заголовков")
                
                # Парсим данные вручную по индексам
                # Структура CSV: timestamp, symbol, state_1h, state_30m, state_15m, state_5m, risk, entry, exit, r
                for row in rows[-limit:]:
                    if len(row) < 7:  # Минимум 7 колонок нужно (до risk включительно)
                        logger.debug(f"Пропущена строка с недостаточным количеством колонок: {len(row)}")
                        continue
                    try:
                        timestamp = (row[0] if len(row) > 0 else '').strip()
                        symbol = (row[1] if len(row) > 1 else '').strip()
                        state_15m_raw = (row[4] if len(row) > 4 else '').strip()  # state_15m это 5-я колонка (индекс 4)
                        risk = (row[6] if len(row) > 6 else '').strip()  # risk это 7-я колонка (индекс 6)
                        
                        # Нормализуем состояние: валидируем и преобразуем в строку
                        # normalize_state() вернёт MarketState enum или None для невалидных значений
                        state_15m_normalized = normalize_state(state_15m_raw)
                        state_15m = state_15m_normalized.value if state_15m_normalized else (state_15m_raw if state_15m_raw else 'N/A')
                        
                        # Логируем для отладки (только первые несколько)
                        if len(signals) < 2:
                            logger.debug(f"Сигнал #{len(signals)+1}: symbol={symbol}, state_15m={state_15m} (raw: {state_15m_raw}), risk={risk}, timestamp={timestamp[:20]}")
                        
                        signals.append({
                            'timestamp': timestamp or 'N/A',
                            'symbol': symbol or 'N/A',
                            'state_15m': state_15m or 'N/A',
                            'risk': risk or 'N/A'
                        })
                    except (IndexError, ValueError) as e:
                        logger.warning(f"Ошибка парсинга строки по индексам: {e}, строка: {row[:5]}")
                        continue
    except Exception as e:
        logger.error(f"Ошибка чтения сигналов из CSV: {e}", exc_info=True)
        import traceback
        traceback.print_exc()
    
    result = list(reversed(signals))  # От новых к старым
    logger.debug(f"Возвращено сигналов: {len(result)}")
    return result


def format_statistics_report(stats):
    """
    Форматирует статистику в читаемый текст.
    """
    if not stats:
        return "📊 Статистика недоступна"
    
    # Эмодзи для PnL
    pnl_emoji = "🟢" if stats['total_pnl'] >= 0 else "🔴"
    win_rate_emoji = "🟢" if stats['win_rate'] >= 50 else "🟡" if stats['win_rate'] >= 30 else "🔴"
    
    report = f"💰 **БАЛАНС:**\n"
    report += f"• Начальный: `{stats['initial_balance']:.2f}` USDT\n"
    report += f"• Текущий: `{stats['current_balance']:.2f}` USDT\n"
    report += f"• {pnl_emoji} P&L: `{stats['total_pnl']:+.2f}` USDT (`{stats['total_pnl_pct']:+.2f}%`)\n\n"
    
    report += f"📈 **СДЕЛКИ:**\n"
    report += f"• Всего: `{stats['total_trades']}`\n"
    report += f"• Открыто: `{stats['open_trades']}`\n"
    report += f"• Закрыто: `{stats['total_trades'] - stats['open_trades']}`\n"
    report += f"• Побед: `{stats.get('wins', stats.get('winning_trades', 0))}` ✅\n"
    report += f"• Поражений: `{stats.get('losses', stats.get('losing_trades', 0))}` ❌\n"
    report += f"• {win_rate_emoji} Win Rate: `{stats['win_rate']:.1f}%`\n\n"
    
    if stats.get('best_trade'):
        report += f"🏆 **Лучшая сделка:**\n"
        report += f"• `{stats['best_trade']['symbol']}` {stats['best_trade'].get('side', '')}\n"
        report += f"• P&L: `{stats['best_trade']['pnl']:+.2f}` USDT\n\n"
    
    if stats.get('worst_trade'):
        report += f"📉 **Худшая сделка:**\n"
        report += f"• `{stats['worst_trade']['symbol']}` {stats['worst_trade'].get('side', '')}\n"
        report += f"• P&L: `{stats['worst_trade']['pnl']:+.2f}` USDT\n\n"
    
    # Топ-3 символа по PnL
    if stats.get('symbol_stats'):
        sorted_symbols = sorted(
            stats['symbol_stats'].items(),
            key=lambda x: x[1]['pnl'],
            reverse=True
        )[:3]
        
        report += "📊 **Топ-3 символа по P&L:**\n"
        for symbol, data in sorted_symbols:
            win_rate_symbol = (data['wins'] / data['trades'] * 100) if data['trades'] > 0 else 0
            pnl_sign = "+" if data['pnl'] >= 0 else ""
            report += f"• `{symbol}`: `{pnl_sign}{data['pnl']:.2f}` USDT ({data['trades']} сделок, WR: {win_rate_symbol:.1f}%)\n"
    
    return report

