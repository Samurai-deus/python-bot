"""
Модуль для расчета статистики работы бота.

Включает базовую статистику по сделкам и финансовые метрики:
    - Sharpe Ratio
    - Max Drawdown
    - Profit Factor
    - Recovery Factor
    - Expectancy
"""
import logging
import math
from typing import List, Optional, Tuple
from datetime import datetime, UTC, timedelta
from capital import get_current_balance
from config import INITIAL_BALANCE
from database import get_trades_statistics
from core.market_state import MarketState, normalize_state

logger = logging.getLogger(__name__)


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
                logger.debug("Чтение CSV с заголовками. Найдено строк: %d", len(rows))
                
                # Логируем первую строку для отладки
                if rows:
                    first_row_keys = list(rows[0].keys())
                    logger.debug("Ключи в первой строке: %s", first_row_keys)
                
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
                            logger.debug("Сигнал #%d: symbol=%s, state_15m=%s (raw: %s), risk=%s, timestamp=%s", len(signals)+1, symbol, state_15m, state_15m_raw, risk, timestamp[:20])

                        signals.append({
                            'timestamp': timestamp or 'N/A',
                            'symbol': symbol or 'N/A',
                            'state_15m': state_15m or 'N/A',
                            'risk': risk or 'N/A'
                        })
                    except Exception as e:
                        logger.warning("Ошибка парсинга строки сигнала: %s", e)
                        continue
            else:
                # Нет заголовков или они не распознаны, читаем как обычный reader
                f.seek(0)
                reader = csv.reader(f)
                rows = list(reader)
                
                logger.debug("Чтение CSV без заголовков. Найдено строк: %d", len(rows))
                
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
                        logger.debug("Пропущена строка с недостаточным количеством колонок: %d", len(row))
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
                            logger.debug("Сигнал #%d: symbol=%s, state_15m=%s (raw: %s), risk=%s, timestamp=%s", len(signals)+1, symbol, state_15m, state_15m_raw, risk, timestamp[:20])

                        signals.append({
                            'timestamp': timestamp or 'N/A',
                            'symbol': symbol or 'N/A',
                            'state_15m': state_15m or 'N/A',
                            'risk': risk or 'N/A'
                        })
                    except (IndexError, ValueError) as e:
                        logger.warning("Ошибка парсинга строки по индексам: %s, строка: %s", e, row[:5])
                        continue
    except Exception as e:
        logger.error("Ошибка чтения сигналов из CSV: %s", e, exc_info=True)
        import traceback
        traceback.print_exc()
    
    result = list(reversed(signals))  # От новых к старым
    logger.debug("Возвращено сигналов: %d", len(result))
    return result


def format_statistics_report(stats):
    """
    Форматирует статистику в читаемый текст.
    """
    if not stats:
        return "📊 Статистика недоступна"
    
    # Эмодзи для PnL
    pnl_emoji = "🟢" if stats['total_pnl'] >= 0 else "🔴"
    has_trades = stats.get('total_trades', 0) > 0
    win_rate = stats['win_rate']
    win_rate_emoji = "🟢" if has_trades and win_rate >= 50 else "🟡" if has_trades and win_rate >= 30 else "🔴"
    win_rate_str = f"`{win_rate:.1f}%`" if has_trades else "`N/A`"

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
    report += f"• {win_rate_emoji} Win Rate: {win_rate_str}\n\n"
    
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
    return report


# ---------------------------------------------------------------------------
# Финансовые метрики (Фаза 3)
# ---------------------------------------------------------------------------

def calculate_sharpe_ratio(
    equity_curve: List[float],
    risk_free: float = 0.0,
    periods_per_year: int = 252,
) -> Optional[float]:
    """
    Sharpe Ratio на основе дневных доходностей equity curve.

    Args:
        equity_curve: Список балансов во времени (минимум 2 точки).
        risk_free:    Годовая безрисковая ставка (дробь, например 0.05 = 5%).
        periods_per_year: Число периодов в году (252 для дневных данных).

    Returns:
        Sharpe Ratio или None при недостаточном количестве данных.
    """
    if len(equity_curve) < 2:
        return None

    # Дневные доходности
    returns = [
        (equity_curve[i] - equity_curve[i - 1]) / equity_curve[i - 1]
        for i in range(1, len(equity_curve))
        if equity_curve[i - 1] != 0
    ]
    if not returns:
        return None

    n = len(returns)
    mean_r = sum(returns) / n
    risk_free_per_period = risk_free / periods_per_year

    variance = sum((r - mean_r) ** 2 for r in returns) / n
    std_r = math.sqrt(variance)

    if std_r == 0:
        return None

    sharpe = (mean_r - risk_free_per_period) / std_r * math.sqrt(periods_per_year)
    return round(sharpe, 4)


def calculate_max_drawdown(equity_curve: List[float]) -> Tuple[float, float]:
    """
    Максимальная просадка по equity curve.

    Args:
        equity_curve: Список балансов во времени.

    Returns:
        Tuple (абсолютная просадка в USDT, процентная просадка 0–1).
        (0.0, 0.0) при недостаточных данных.
    """
    if len(equity_curve) < 2:
        return 0.0, 0.0

    peak = equity_curve[0]
    max_dd_abs = 0.0
    max_dd_pct = 0.0

    for value in equity_curve[1:]:
        if value > peak:
            peak = value
        drawdown_abs = peak - value
        drawdown_pct = drawdown_abs / peak if peak > 0 else 0.0
        if drawdown_pct > max_dd_pct:
            max_dd_abs = drawdown_abs
            max_dd_pct = drawdown_pct

    return round(max_dd_abs, 4), round(max_dd_pct, 6)


def calculate_profit_factor(trades: List[dict]) -> Optional[float]:
    """
    Profit Factor = валовая прибыль / валовый убыток.

    Args:
        trades: Список сделок. Каждая сделка должна содержать ключ 'pnl' (float).

    Returns:
        Profit Factor или None при отсутствии убыточных сделок.
    """
    gross_profit = sum(t['pnl'] for t in trades if t.get('pnl', 0) > 0)
    gross_loss = abs(sum(t['pnl'] for t in trades if t.get('pnl', 0) < 0))

    if gross_loss == 0:
        return None  # Нет убыточных сделок — метрика не применима

    return round(gross_profit / gross_loss, 4)


def calculate_recovery_factor(
    trades: List[dict],
    equity_curve: List[float],
) -> Optional[float]:
    """
    Recovery Factor = чистая прибыль / Max Drawdown (абс.).

    Args:
        trades:       Список закрытых сделок с ключом 'pnl'.
        equity_curve: Список балансов.

    Returns:
        Recovery Factor или None при нулевой просадке.
    """
    total_net_profit = sum(t.get('pnl', 0) for t in trades)
    max_dd_abs, _ = calculate_max_drawdown(equity_curve)

    if max_dd_abs == 0:
        return None

    return round(total_net_profit / max_dd_abs, 4)


def calculate_expectancy(trades: List[dict]) -> Optional[float]:
    """
    Математическое ожидание за сделку.

    Expectancy = (Win Rate × Avg Win) − (Loss Rate × Avg Loss)

    Args:
        trades: Список закрытых сделок с ключом 'pnl'.

    Returns:
        Expectancy в USDT или None при отсутствии сделок.
    """
    if not trades:
        return None

    wins = [t['pnl'] for t in trades if t.get('pnl', 0) > 0]
    losses = [t['pnl'] for t in trades if t.get('pnl', 0) < 0]
    total = len(trades)

    win_rate = len(wins) / total
    loss_rate = len(losses) / total

    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0.0

    expectancy = (win_rate * avg_win) - (loss_rate * avg_loss)
    return round(expectancy, 4)


def get_full_statistics(days: int = 30) -> dict:
    """
    Расширенная статистика: базовые поля + все финансовые метрики.

    Args:
        days: Период анализа в днях.

    Returns:
        dict с полями базовой статистики плюс:
            sharpe_ratio, max_drawdown_abs, max_drawdown_pct,
            profit_factor, recovery_factor, expectancy.
        None при отсутствии данных.
    """
    from database import get_closed_trades, get_equity_curve_points

    base = get_trade_statistics(days)
    if base is None:
        return None

    # Загружаем закрытые сделки и equity curve из БД
    try:
        closed_trades = get_closed_trades(days) or []
    except Exception:
        logger.error("Failed to fetch closed_trades for extended stats (days=%d)", days, exc_info=True)
        closed_trades = []

    try:
        equity_points = get_equity_curve_points(days) or []
        equity_curve = [p['balance'] for p in equity_points]
    except Exception:
        logger.error("Failed to fetch equity_curve for extended stats (days=%d)", days, exc_info=True)
        equity_curve = []

    # Вычисляем метрики
    sharpe = calculate_sharpe_ratio(equity_curve) if len(equity_curve) >= 2 else None
    max_dd_abs, max_dd_pct = calculate_max_drawdown(equity_curve) if equity_curve else (0.0, 0.0)
    pf = calculate_profit_factor(closed_trades) if closed_trades else None
    rf = calculate_recovery_factor(closed_trades, equity_curve) if closed_trades and equity_curve else None
    exp = calculate_expectancy(closed_trades) if closed_trades else None

    return {
        **base,
        'sharpe_ratio': sharpe,
        'max_drawdown_abs': max_dd_abs,
        'max_drawdown_pct': max_dd_pct,
        'profit_factor': pf,
        'recovery_factor': rf,
        'expectancy': exp,
        'period_days': days,
    }
