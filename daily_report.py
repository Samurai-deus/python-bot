"""
Модуль для генерации ежедневных отчетов
"""
from datetime import datetime, UTC, timedelta
from telegram_bot import send_message
from bot_statistics import get_trade_statistics, format_statistics_report
from trade_manager import get_open_trades
from capital import get_current_balance
from capital import get_initial_balance


def generate_daily_report():
    """
    Генерирует и отправляет ежедневный отчет о работе бота.
    """
    try:
        # Статистика за сегодня
        stats_today = get_trade_statistics(days=1)
        
        # Статистика за неделю
        stats_week = get_trade_statistics(days=7)
        
        timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        
        report = f"📊 **ЕЖЕДНЕВНЫЙ ОТЧЕТ**\n\n"
        report += f"⏰ {timestamp}\n\n"
        
        # Статистика за сегодня
        report += "📅 **ЗА СЕГОДНЯ:**\n"
        if stats_today:
            report += f"Сделок: {stats_today['total_trades']}\n"
            report += f"Win Rate: {stats_today['win_rate']:.1f}%\n"
            report += f"PnL: {stats_today['total_pnl']:+.2f} USDT\n\n"
        else:
            report += "Сделок: 0\n"
            report += "Win Rate: 0.0%\n"
            report += "PnL: +0.00 USDT\n\n"
        
        # Статистика за неделю
        report += "📅 **ЗА НЕДЕЛЮ:**\n"
        if stats_week:
            report += f"Сделок: {stats_week['total_trades']}\n"
            report += f"Win Rate: {stats_week['win_rate']:.1f}%\n"
            report += f"PnL: {stats_week['total_pnl']:+.2f} USDT ({stats_week['total_pnl_pct']:+.2f}%)\n\n"
        else:
            report += "Сделок: 0\n"
            report += "Win Rate: 0.0%\n"
            report += "PnL: +0.00 USDT (+0.00%)\n\n"
        
        # Текущий баланс
        balance = get_current_balance()
        total_pnl_pct = (((balance - get_initial_balance()) / get_initial_balance()) * 100 if get_initial_balance() else 0.0)
        report += f"💰 **Текущий баланс:** {balance:.2f} USDT ({total_pnl_pct:+.2f}%)\n\n"
        
        # Открытые сделки
        open_trades = get_open_trades()
        report += f"📊 Открытых сделок: {len(open_trades)}\n"
        
        if stats_week and stats_week.get('best_trade'):
            report += f"\n🏆 Лучшая сделка недели: {stats_week['best_trade']['symbol']} {stats_week['best_trade']['pnl']:+.2f} USDT"
        
        send_message(report)
        
    except Exception as e:
        print(f"Ошибка генерации ежедневного отчета: {e}")

