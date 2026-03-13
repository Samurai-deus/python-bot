"""
Модуль для обработки Telegram команд
"""
import csv
import os
from datetime import datetime, UTC
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters, CallbackQueryHandler
from telegram_bot import send_message
from bot_statistics import get_trade_statistics, format_statistics_report, get_signals_statistics
from trade_manager import get_open_trades
from capital import get_current_balance
from config import INITIAL_BALANCE
from core.decision_core import get_decision_core
from execution.gatekeeper import get_gatekeeper
from brains.market_regime_brain import get_market_regime_brain
from brains.risk_exposure_brain import get_risk_exposure_brain
from brains.cognitive_filter import get_cognitive_filter


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    help_text = """🧠 **ЭКОСИСТЕМА ТОРГОВЫХ БОТОВ**

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🎯 **DECISION CORE**
Основные команды принятия решений:

`/should_i_trade` - Можно ли торговать?
`/risk_status` - Статус риска и экспозиции
`/invest [сумма]` - Анализ инвестирования

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🧠 **АНАЛИТИКА "МОЗГОВ"**
Информация от модулей экосистемы:

`/market_regime` - Режим рынка
`/risk_exposure` - Риск и экспозиция
`/cognitive` - Когнитивный фильтр
`/opportunities` - Возможности рынка

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 **СТАТИСТИКА**
Торговая статистика и отчеты:

`/status` - Текущий статус бота
`/stats [дни]` - Статистика сделок
`/trades` - Открытые сделки
`/signals [кол-во]` - Последние сигналы
`/gatekeeper` - Статистика Gatekeeper

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

💡 Используйте кнопки ниже для быстрого доступа
"""
    
    # Создаем интерактивные кнопки
    keyboard = [
        [
            InlineKeyboardButton("🎯 Можно торговать?", callback_data="should_trade"),
            InlineKeyboardButton("📊 Статус риска", callback_data="risk_status")
        ],
        [
            InlineKeyboardButton("📈 Режим рынка", callback_data="market_regime"),
            InlineKeyboardButton("⚠️ Риск/Экспозиция", callback_data="risk_exposure")
        ],
        [
            InlineKeyboardButton("💼 Открытые сделки", callback_data="trades"),
            InlineKeyboardButton("📊 Статистика", callback_data="stats")
        ],
        [
            InlineKeyboardButton("🚪 Gatekeeper", callback_data="gatekeeper"),
            InlineKeyboardButton("💡 Помощь", callback_data="help")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(help_text, reply_markup=reply_markup, parse_mode="Markdown")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help"""
    await cmd_start(update, context)


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на кнопки"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    # Создаем фейковый update для команд (команды ожидают update с message или callback_query)
    class FakeUpdate:
        def __init__(self, callback_query):
            self.callback_query = callback_query
            self.message = None
    
    fake_update = FakeUpdate(query)
    
    try:
        if data == "should_trade":
            await cmd_should_i_trade(fake_update, context)
        elif data == "risk_status":
            await cmd_risk_status(fake_update, context)
        elif data == "market_regime":
            await cmd_market_regime(fake_update, context)
        elif data == "risk_exposure":
            await cmd_risk_exposure(fake_update, context)
        elif data == "trades":
            await cmd_trades(fake_update, context)
        elif data == "stats":
            await cmd_stats(fake_update, context)
        elif data == "gatekeeper":
            await cmd_gatekeeper(fake_update, context)
        elif data == "help":
            await cmd_start(fake_update, context)
    except Exception as e:
        await query.message.reply_text(f"❌ Ошибка: {e}")


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /stats"""
    # Определяем, откуда пришел запрос
    if hasattr(update, 'message') and update.message:
        reply_func = update.message.reply_text
    else:
        reply_func = update.callback_query.message.reply_text
    
    days = 1
    if context.args and len(context.args) > 0:
        try:
            days = int(context.args[0])
            days = max(1, min(30, days))  # От 1 до 30 дней
        except ValueError:
            days = 1
    
    stats = get_trade_statistics(days=days)
    if stats:
        report = format_statistics_report(stats)
        # Улучшаем форматирование
        header = f"📊 **СТАТИСТИКА ЗА {days} ДНЕЙ**\n\n"
        header += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        footer = f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n⏰ {datetime.now(UTC).strftime('%H:%M:%S UTC')}"
        await reply_func(header + report + footer, parse_mode="Markdown")
    else:
        await reply_func("📊 **Статистика недоступна**\n\nНет данных о сделках за указанный период.", parse_mode="Markdown")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /status - текущий статус бота"""
    try:
        # Определяем, откуда пришел запрос (команда или кнопка)
        if hasattr(update, 'message') and update.message:
            reply_func = update.message.reply_text
        else:
            reply_func = update.callback_query.message.reply_text
        
        # Текущий баланс
        balance = get_current_balance()
        pnl = balance - INITIAL_BALANCE
        pnl_pct = (pnl / INITIAL_BALANCE) * 100
        
        # Определяем статус
        status_emoji = "🟢" if pnl >= 0 else "🔴"
        status_text = f"{status_emoji} **СТАТУС БОТА**\n\n"
        status_text += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        
        # Баланс
        pnl_sign = "+" if pnl >= 0 else ""
        status_text += f"💰 **Баланс:** `{balance:.2f}` USDT\n"
        status_text += f"📈 **P&L:** `{pnl_sign}{pnl:.2f}` USDT (`{pnl_sign}{pnl_pct:.2f}%`)\n\n"
        
        # System State статус (trading, safe_mode, adaptive)
        from system_state import get_system_state
        import time
        from runner import get_adaptive_system_state, get_analysis_metrics
        
        system_state = get_system_state()
        if system_state is None:
            status_text += "⚠️ **System State:** Недоступно\n\n"
        else:
            # Trading status
            trading_status = "ACTIVE" if not system_state.system_health.trading_paused else "PAUSED"
            trading_emoji = "🟢" if trading_status == "ACTIVE" else "⏸"
            status_text += f"{trading_emoji} **Trading:** `{trading_status}`\n"
            
            # Safe mode
            safe_mode_status = "ACTIVE" if system_state.system_health.safe_mode else "INACTIVE"
            safe_mode_emoji = "🔴" if system_state.system_health.safe_mode else "🟢"
            status_text += f"{safe_mode_emoji} **Safe Mode:** `{safe_mode_status}`\n"
            
            # Adaptive interval
            adaptive_system = get_adaptive_system_state()
            adaptive_interval = adaptive_system.get("adaptive_interval", 300)
            volatility_state = adaptive_system.get("volatility_state", "MEDIUM")
            status_text += f"📊 **Interval:** `{adaptive_interval:.0f}s` (volatility: `{volatility_state}`)\n"
            
            # Uptime
            metrics = get_analysis_metrics()
            if metrics.get("start_time"):
                uptime = time.monotonic() - metrics["start_time"]
                uptime_hours = uptime / 3600
                if uptime_hours < 1:
                    uptime_str = f"{uptime / 60:.0f} мин"
                else:
                    uptime_str = f"{uptime_hours:.1f} ч"
                status_text += f"⏱ **Uptime:** `{uptime_str}`\n"
            
            status_text += "\n"
        
        # Decision Core статус (читает из SystemState)
        decision_core = get_decision_core()
        if system_state is None:
            decision_emoji = "⚠️"
            status_text += f"{decision_emoji} **Decision Core:** Состояние недоступно\n"
            status_text += f"⚠️ **Риск:** `UNKNOWN`\n\n"
        else:
            decision = decision_core.should_i_trade(system_state=system_state)
            decision_emoji = "✅" if decision.can_trade else "❌"
            status_text += f"{decision_emoji} **Decision Core:** {'Можно торговать' if decision.can_trade else 'Торговля заблокирована'}\n"
            status_text += f"⚠️ **Риск:** `{decision.risk_level}`\n\n"
        # Открытые сделки
        open_trades = get_open_trades()
        status_text += f"📊 **Открытых сделок:** `{len(open_trades)}`\n"
        
        if open_trades:
            status_text += "\n**Открытые позиции:**\n"
            for i, trade in enumerate(open_trades[:5], 1):
                symbol = trade.get('symbol', '')
                side = trade.get('side', '')
                entry = float(trade.get('entry', 0))
                side_emoji = "🟢" if side == "LONG" else "🔴"
                status_text += f"{i}. {side_emoji} `{symbol}` {side} @ `{entry:.4f}`\n"
            if len(open_trades) > 5:
                status_text += f"\n... и еще `{len(open_trades) - 5}` позиций\n"
        
        # Последний heartbeat
        if os.path.exists("last_heartbeat.txt"):
            try:
                with open("last_heartbeat.txt", "r", encoding='utf-8') as f:
                    last_heartbeat = float(f.read().strip())
                    time_since = (datetime.now(UTC).timestamp() - last_heartbeat) / 3600
                    if time_since < 1:
                        heartbeat_status = f"💓 Активен ({int(time_since * 60)} мин назад)"
                    else:
                        heartbeat_status = f"💓 Активен ({time_since:.1f} ч назад)"
                    status_text += f"\n{heartbeat_status}"
            except Exception:
                pass
        
        status_text += "\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        status_text += f"\n⏰ {datetime.now(UTC).strftime('%H:%M:%S UTC')}"
        
        await reply_func(status_text, parse_mode="Markdown")
    except Exception as e:
        reply_func = update.message.reply_text if hasattr(update, 'message') else update.callback_query.message.reply_text
        await reply_func(f"❌ Ошибка получения статуса: {e}")


async def cmd_trades(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /trades - открытые сделки"""
    # Определяем, откуда пришел запрос
    if hasattr(update, 'message') and update.message:
        reply_func = update.message.reply_text
    else:
        reply_func = update.callback_query.message.reply_text
    
    open_trades = get_open_trades()
    
    if not open_trades:
        await reply_func("📊 **Нет открытых сделок**\n\nВсе позиции закрыты или еще не открыты.")
        return
    
    report = f"💼 **ОТКРЫТЫЕ СДЕЛКИ** (`{len(open_trades)}`)\n\n"
    report += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    for i, trade in enumerate(open_trades, 1):
        symbol = trade.get('symbol', '')
        side = trade.get('side', '')
        entry = float(trade.get('entry', 0))
        stop = float(trade.get('stop', 0))
        target = float(trade.get('target', 0))
        position_size = trade.get('position_size', 0)
        leverage = trade.get('leverage', 1.0)
        
        side_emoji = "🟢" if side == "LONG" else "🔴"
        
        # Рассчитываем R:R
        if side == "LONG":
            risk = entry - stop
            reward = target - entry
        else:
            risk = stop - entry
            reward = entry - target
        
        rr_ratio = abs(reward / risk) if risk != 0 else 0
        
        report += f"{i}. {side_emoji} **{symbol}** `{side}`\n"
        report += f"   💰 Вход: `{entry:.4f}`\n"
        report += f"   🛑 Стоп: `{stop:.4f}`\n"
        report += f"   🎯 Цель: `{target:.4f}`\n"
        report += f"   📊 R:R: `{rr_ratio:.2f}`\n"
        
        if position_size:
            report += f"   💼 Размер: `{position_size:.2f}` USDT\n"
        if leverage:
            report += f"   ⚡ Плечо: `{leverage:.1f}x`\n"
        
        report += "\n"
    
    report += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    report += f"\n⏰ {datetime.now(UTC).strftime('%H:%M:%S UTC')}"
    
    await reply_func(report, parse_mode="Markdown")


async def cmd_signals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /signals - последние сигналы"""
    # Определяем, откуда пришел запрос
    if hasattr(update, 'message') and update.message:
        reply_func = update.message.reply_text
    else:
        reply_func = update.callback_query.message.reply_text
    
    limit = 10
    if context.args and len(context.args) > 0:
        try:
            limit = int(context.args[0])
            limit = max(1, min(50, limit))  # От 1 до 50
        except ValueError:
            limit = 10
    
    signals = get_signals_statistics(limit=limit)
    
    if not signals:
        await reply_func("📊 **Нет данных о сигналах**\n\nСигналы еще не генерировались.")
        return
    
    report = f"📊 **ПОСЛЕДНИЕ {len(signals)} СИГНАЛОВ**\n\n"
    report += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    for i, signal in enumerate(signals, 1):
        # Безопасное извлечение данных с fallback значениями
        timestamp = signal.get('timestamp', '').strip() if signal.get('timestamp') else 'N/A'
        symbol = signal.get('symbol', '').strip() if signal.get('symbol') else 'N/A'
        state = signal.get('state_15m', '').strip() if signal.get('state_15m') else 'N/A'
        risk = signal.get('risk', '').strip() if signal.get('risk') else 'N/A'
        
        # Логирование для отладки (только первые 2 сигнала)
        if i <= 2:
            import logging
            logger = logging.getLogger(__name__)
            logger.debug(f"Сигнал #{i} перед форматированием: symbol={symbol}, state={state}, risk={risk}, timestamp={timestamp}")
        
        # Защита от пустых значений
        if not state or state == '':
            state = 'N/A'
        if not risk or risk == '':
            risk = 'N/A'
        if not symbol or symbol == '':
            symbol = 'N/A'
        
        # Эмодзи для риска
        if risk.upper() == "HIGH":
            risk_emoji = "🔴"
        elif risk.upper() == "MEDIUM":
            risk_emoji = "🟡"
        elif risk.upper() == "LOW":
            risk_emoji = "🟢"
        else:
            risk_emoji = "⚪"  # Неизвестный риск
        
        # Форматируем время
        try:
            # Пробуем разные форматы времени
            time_str = timestamp[:16]  # По умолчанию первые 16 символов
            if timestamp and timestamp != 'N/A':
                try:
                    # Пробуем ISO формат
                    if 'Z' in timestamp:
                        timestamp_clean = timestamp.replace('Z', '+00:00')
                    else:
                        timestamp_clean = timestamp
                    dt = datetime.fromisoformat(timestamp_clean)
                    time_str = dt.strftime("%H:%M %d.%m")
                except (ValueError, AttributeError):
                    # Если не получилось, используем первые символы
                    time_str = timestamp[:16] if len(timestamp) > 16 else timestamp
        except Exception:
            time_str = 'N/A'
        
        report += f"{i}. **{symbol}**\n"
        report += f"   📊 Состояние: `{state}`\n"
        report += f"   {risk_emoji} Риск: `{risk}`\n"
        report += f"   ⏰ {time_str}\n\n"
    
    report += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    report += f"\n⏰ {datetime.now(UTC).strftime('%H:%M:%S UTC')}"
    
    await reply_func(report, parse_mode="Markdown")


async def cmd_should_i_trade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /should_i_trade - главный вопрос Decision Core"""
    try:
        # Определяем, откуда пришел запрос
        if hasattr(update, 'message') and update.message:
            reply_func = update.message.reply_text
        else:
            reply_func = update.callback_query.message.reply_text
        
        decision_core = get_decision_core()
        symbol = context.args[0] if context.args and hasattr(context, 'args') else None
        
        decision = decision_core.should_i_trade(symbol=symbol)
        
        emoji = "✅" if decision.can_trade else "❌"
        status = "МОЖНО ТОРГОВАТЬ" if decision.can_trade else "НЕЛЬЗЯ ТОРГОВАТЬ"
        
        msg = f"{emoji} **{status}**\n\n"
        msg += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        
        if symbol:
            msg += f"📊 **Символ:** `{symbol}`\n\n"
        
        msg += f"📋 **Причина:**\n`{decision.reason}`\n\n"
        msg += f"⚠️ **Уровень риска:** `{decision.risk_level}`\n"
        
        if decision.max_position_size:
            msg += f"💰 **Макс. размер позиции:** `{decision.max_position_size:.2f}` USDT\n"
        
        if decision.max_leverage:
            msg += f"📈 **Макс. плечо:** `{decision.max_leverage:.1f}x`\n"
        
        if decision.recommendations:
            msg += f"\n💡 **Рекомендации:**\n"
            for rec in decision.recommendations:
                msg += f"• {rec}\n"
        
        msg += "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        msg += f"\n⏰ {datetime.now(UTC).strftime('%H:%M:%S UTC')}"
        
        await reply_func(msg, parse_mode="Markdown")
    except Exception as e:
        reply_func = update.message.reply_text if hasattr(update, 'message') else update.callback_query.message.reply_text
        await reply_func(f"❌ Ошибка: {e}")


async def cmd_risk_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /risk_status - статус риска"""
    try:
        # Определяем, откуда пришел запрос
        if hasattr(update, 'message') and update.message:
            reply_func = update.message.reply_text
        else:
            reply_func = update.callback_query.message.reply_text
        
        decision_core = get_decision_core()
        
        # Если риск не определен, загружаем данные и анализируем
        from system_state import get_system_state
        system_state = get_system_state()
        risk_exposure = system_state.risk_state if system_state else None
        
        if not risk_exposure:
            try:
                from config import SYMBOLS, TIMEFRAMES
                from data_loader import get_candles_parallel
                from brains.risk_exposure_brain import get_risk_exposure_brain
                
                await reply_func("📥 Загрузка данных для анализа риска и экспозиции...")
                
                # Загружаем данные
                all_candles = get_candles_parallel(SYMBOLS, TIMEFRAMES, limit=120, max_workers=20)
                
                # Анализируем
                risk_exposure_brain = get_risk_exposure_brain()
                risk_exposure = risk_exposure_brain.analyze(SYMBOLS, all_candles, system_state)
            except Exception as e:
                await reply_func(f"❌ **Ошибка при загрузке данных**\n\n{type(e).__name__}: {e}")
                return
        
        from system_state import get_system_state
        system_state = get_system_state()
        
        # Безопасная проверка на None
        if system_state is None:
            await reply_func("⚠️ **Состояние системы недоступно**\n\nПопробуйте позже.")
            return
        
        status = decision_core.get_risk_status(system_state=system_state)
        
        can_trade_emoji = "✅" if status['can_trade'] else "❌"
        risk_level = status.get('risk_level', 'UNKNOWN')
        
        # Определяем цвет риска
        if risk_level == "HIGH":
            risk_emoji = "🔴"
        elif risk_level == "MEDIUM":
            risk_emoji = "🟡"
        else:
            risk_emoji = "🟢"
        
        msg = "📊 **СТАТУС РИСКА И ЭКСПОЗИЦИИ**\n\n"
        msg += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        
        msg += f"{can_trade_emoji} **Можно торговать:** {'Да' if status['can_trade'] else 'Нет'}\n"
        msg += f"{risk_emoji} **Уровень риска:** `{risk_level}`\n\n"
        
        msg += "📈 **Риск:**\n"
        msg += f"• Суммарный риск: `{status['total_risk_pct']:.2f}%`\n"
        msg += f"• Активных позиций: `{status['active_positions']}`\n"
        
        if 'exposure_pct' in status:
            msg += f"• Экспозиция: `{status['exposure_pct']:.2f}%`\n"
        
        if 'total_leverage' in status:
            msg += f"• Суммарное плечо: `{status['total_leverage']:.2f}x`\n"
        
        if status['warnings']:
            msg += f"\n⚠️ **Предупреждения:**\n"
            for warning in status['warnings']:
                msg += f"• {warning}\n"
        
        msg += "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        msg += f"\n⏰ {datetime.now(UTC).strftime('%H:%M:%S UTC')}"
        
        await reply_func(msg, parse_mode="Markdown")
    except Exception as e:
        reply_func = update.message.reply_text if hasattr(update, 'message') else update.callback_query.message.reply_text
        await reply_func(f"❌ Ошибка: {e}")


async def cmd_invest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /invest - анализ инвестирования"""
    try:
        # Определяем, откуда пришел запрос
        if hasattr(update, 'message') and update.message:
            reply_func = update.message.reply_text
        else:
            reply_func = update.callback_query.message.reply_text
        
        amount = None
        if context.args and len(context.args) > 0:
            try:
                amount = float(context.args[0])
            except ValueError:
                await reply_func("❌ **Неверный формат суммы**\n\nИспользуйте: `/invest 1000`", parse_mode="Markdown")
                return
        
        from system_state import get_system_state
        decision_core = get_decision_core()
        system_state = get_system_state()
        
        # Безопасная проверка на None
        if system_state is None:
            await reply_func("⚠️ **Состояние системы недоступно**\n\nПопробуйте позже.")
            return
        
        decision = decision_core.should_i_trade(system_state=system_state)
        
        msg = "💰 **АНАЛИЗ ИНВЕСТИРОВАНИЯ**\n\n"
        msg += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        
        if not decision.can_trade:
            msg += "❌ **НЕ РЕКОМЕНДУЕТСЯ** инвестировать сейчас\n\n"
            msg += f"📋 **Причина:**\n`{decision.reason}`\n"
        else:
            msg += "✅ **МОЖНО** инвестировать\n\n"
            
            if amount:
                from system_state import get_system_state
                system_state = get_system_state()
                risk_pct = system_state.risk_state.total_risk_pct if (system_state and system_state.risk_state) else 0.0
                max_risk = 10.0 - risk_pct
                recommended_risk = min(max_risk, 2.0)  # 2% на сделку
                recommended_amount = amount * (recommended_risk / 100)
                
                msg += f"💵 **Сумма:** `{amount:.2f}` USDT\n"
                msg += f"📊 **Рекомендуемый риск:** `{recommended_risk:.1f}%` (`{recommended_amount:.2f}` USDT)\n"
                msg += f"⚠️ **Текущий риск портфеля:** `{risk_pct:.2f}%`\n"
        
        if decision.recommendations:
            msg += f"\n💡 **Рекомендации:**\n"
            for rec in decision.recommendations:
                msg += f"• {rec}\n"
        
        msg += "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        msg += f"\n⏰ {datetime.now(UTC).strftime('%H:%M:%S UTC')}"
        
        await reply_func(msg, parse_mode="Markdown")
    except Exception as e:
        reply_func = update.message.reply_text if hasattr(update, 'message') else update.callback_query.message.reply_text
        await reply_func(f"❌ Ошибка: {e}")


async def cmd_market_regime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /market_regime - режим рынка"""
    try:
        # Определяем, откуда пришел запрос
        if hasattr(update, 'message') and update.message:
            reply_func = update.message.reply_text
        else:
            reply_func = update.callback_query.message.reply_text
        
        from system_state import get_system_state
        decision_core = get_decision_core()
        system_state = get_system_state()
        
        # Безопасная проверка на None
        if system_state is None:
            await reply_func("⚠️ **Состояние системы недоступно**\n\nПопробуйте позже.")
            return
        
        regime = system_state.market_regime
        
        # Если режим не определен, загружаем данные и анализируем
        if not regime:
            try:
                from config import SYMBOLS, TIMEFRAMES
                from data_loader import get_candles_parallel
                from brains.market_regime_brain import get_market_regime_brain
                
                await reply_func("📥 Загрузка данных для анализа режима рынка...")
                
                # Загружаем данные
                all_candles = get_candles_parallel(SYMBOLS, TIMEFRAMES, limit=120, max_workers=20)
                
                # Анализируем
                market_regime_brain = get_market_regime_brain()
                regime = market_regime_brain.analyze(SYMBOLS, all_candles, system_state)
            except Exception as e:
                await reply_func(f"❌ **Ошибка при загрузке данных**\n\n{type(e).__name__}: {e}")
                return
        
        if not regime:
            await reply_func("📊 **Режим рынка не определен**\n\nНе удалось загрузить или проанализировать данные.")
            return
        
        msg = "📊 **РЕЖИМ РЫНКА**\n\n"
        msg += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        
        # Trend type
        trend_emoji = "📈" if regime.trend_type == "TREND" else "📊"
        msg += f"{trend_emoji} **Тип рынка:** `{regime.trend_type}`\n"
        
        # Volatility
        vol_emoji = "🔴" if regime.volatility_level == "HIGH" else "🟡" if regime.volatility_level == "MEDIUM" else "🟢"
        msg += f"{vol_emoji} **Волатильность:** `{regime.volatility_level}`\n"
        
        # Risk sentiment
        risk_emoji = "🟢" if regime.risk_sentiment == "RISK_ON" else "🔴" if regime.risk_sentiment == "RISK_OFF" else "⚪"
        msg += f"{risk_emoji} **Настроение:** `{regime.risk_sentiment}`\n"
        
        if regime.macro_pressure:
            msg += f"🌍 **Макро-давление:** `{regime.macro_pressure}`\n"
        
        msg += f"\n📊 **Уверенность:** `{regime.confidence:.1%}`"
        
        msg += "\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        msg += f"\n⏰ {datetime.now(UTC).strftime('%H:%M:%S UTC')}"
        
        await reply_func(msg, parse_mode="Markdown")
    except Exception as e:
        reply_func = update.message.reply_text if hasattr(update, 'message') else update.callback_query.message.reply_text
        await reply_func(f"❌ Ошибка: {e}")


async def cmd_risk_exposure(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /risk_exposure - детальный риск и экспозиция"""
    try:
        # Определяем, откуда пришел запрос
        if hasattr(update, 'message') and update.message:
            reply_func = update.message.reply_text
        else:
            reply_func = update.callback_query.message.reply_text
        
        decision_core = get_decision_core()
        
        # Если риск не определен, загружаем данные и анализируем
        from system_state import get_system_state
        system_state = get_system_state()
        risk_exposure = system_state.risk_state if system_state else None
        
        if not risk_exposure:
            try:
                from config import SYMBOLS, TIMEFRAMES
                from data_loader import get_candles_parallel
                from brains.risk_exposure_brain import get_risk_exposure_brain
                
                await reply_func("📥 Загрузка данных для анализа риска и экспозиции...")
                
                # Загружаем данные
                all_candles = get_candles_parallel(SYMBOLS, TIMEFRAMES, limit=120, max_workers=20)
                
                # Анализируем
                risk_exposure_brain = get_risk_exposure_brain()
                risk_exposure = risk_exposure_brain.analyze(SYMBOLS, all_candles, system_state)
            except Exception as e:
                await reply_func(f"❌ **Ошибка при загрузке данных**\n\n{type(e).__name__}: {e}")
                return
        
        from system_state import get_system_state
        system_state = get_system_state()
        
        # Безопасная проверка на None
        if system_state is None:
            await reply_func("⚠️ **Состояние системы недоступно**\n\nПопробуйте позже.")
            return
        
        risk = system_state.risk_state
        
        if not risk:
            await reply_func("⚠️ **Риск и экспозиция не определены**\n\nНе удалось загрузить или проанализировать данные.")
            return
        
        msg = "⚠️ **РИСК И ЭКСПОЗИЦИЯ**\n\n"
        msg += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        
        overload_emoji = "🔴" if risk.is_overloaded else "🟢"
        msg += f"{overload_emoji} **Статус:** {'Перегрузка' if risk.is_overloaded else 'Норма'}\n\n"
        
        msg += "📈 **Риск:**\n"
        msg += f"• Суммарный риск: `{risk.total_risk_pct:.2f}%`\n"
        msg += f"• Макс. корреляция: `{risk.max_correlation:.2f}`\n\n"
        
        msg += "💼 **Экспозиция:**\n"
        msg += f"• Активных позиций: `{risk.active_positions}`\n"
        msg += f"• Экспозиция: `{risk.exposure_pct:.2f}%`\n"
        msg += f"• Суммарное плечо: `{risk.total_leverage:.2f}x`\n"
        
        msg += "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        msg += f"\n⏰ {datetime.now(UTC).strftime('%H:%M:%S UTC')}"
        
        await reply_func(msg, parse_mode="Markdown")
    except Exception as e:
        reply_func = update.message.reply_text if hasattr(update, 'message') else update.callback_query.message.reply_text
        await reply_func(f"❌ Ошибка: {e}")


async def cmd_cognitive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /cognitive - когнитивный фильтр"""
    try:
        # Определяем, откуда пришел запрос
        if hasattr(update, 'message') and update.message:
            reply_func = update.message.reply_text
        else:
            reply_func = update.callback_query.message.reply_text
        
        from system_state import get_system_state
        decision_core = get_decision_core()
        system_state = get_system_state()
        
        # Безопасная проверка на None
        if system_state is None:
            await reply_func("⚠️ **Состояние системы недоступно**\n\nПопробуйте позже.")
            return
        
        cognitive = system_state.cognitive_state
        
        if not cognitive:
            await reply_func("🧠 **Когнитивное состояние не определено**\n\nДанные еще не загружены.")
            return
        
        msg = "🧠 **КОГНИТИВНЫЙ ФИЛЬТР**\n\n"
        msg += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        
        pause_emoji = "⏸" if cognitive.should_pause else "✅"
        msg += f"{pause_emoji} **Статус:** {'Требуется пауза' if cognitive.should_pause else 'Норма'}\n\n"
        
        # Пере-торговля
        overtrading_emoji = "🔴" if cognitive.overtrading_score > 0.7 else "🟡" if cognitive.overtrading_score > 0.4 else "🟢"
        msg += f"{overtrading_emoji} **Пере-торговля:** `{cognitive.overtrading_score:.1%}`\n"
        msg += f"📊 **Сделок за 24ч:** `{cognitive.recent_trades_count}`\n"
        msg += f"😰 **Эмоциональных входов:** `{cognitive.emotional_entries}`\n"
        msg += f"🚨 **FOMO паттернов:** `{cognitive.fomo_patterns}`\n"
        
        msg += "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        msg += f"\n⏰ {datetime.now(UTC).strftime('%H:%M:%S UTC')}"
        
        await reply_func(msg, parse_mode="Markdown")
    except Exception as e:
        reply_func = update.message.reply_text if hasattr(update, 'message') else update.callback_query.message.reply_text
        await reply_func(f"❌ Ошибка: {e}")


async def cmd_opportunities(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /opportunities - возможности рынка"""
    try:
        # Определяем, откуда пришел запрос
        if hasattr(update, 'message') and update.message:
            reply_func = update.message.reply_text
        else:
            reply_func = update.callback_query.message.reply_text
        
        from system_state import get_system_state
        decision_core = get_decision_core()
        system_state = get_system_state()
        
        # Безопасная проверка на None
        if system_state is None:
            await reply_func("⚠️ **Состояние системы недоступно**\n\nПопробуйте позже.")
            return
        
        opportunities = system_state.opportunities
        
        if not opportunities:
            await reply_func("🔍 **Возможности не определены**\n\nДанные еще не загружены.")
            return
        
        msg = "🔍 **ВОЗМОЖНОСТИ РЫНКА**\n\n"
        msg += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        
        # Показываем топ-5 символов по готовности
        sorted_opps = sorted(
            opportunities.items(),
            key=lambda x: x[1].readiness_score,
            reverse=True
        )[:5]
        
        for symbol, opp in sorted_opps:
            readiness_emoji = "🟢" if opp.readiness_score > 0.7 else "🟡" if opp.readiness_score > 0.4 else "🔴"
            msg += f"{readiness_emoji} **{symbol}**\n"
            msg += f"   Готовность: `{opp.readiness_score:.1%}`\n"
            
            indicators = []
            if opp.volatility_squeeze:
                indicators.append("Сжатие волатильности")
            if opp.accumulation:
                indicators.append("Накопление")
            if opp.divergence:
                indicators.append("Расхождение")
            if opp.suspicious_silence:
                indicators.append("Тишина")
            
            if indicators:
                msg += f"   Признаки: {', '.join(indicators)}\n"
            msg += "\n"
        
        msg += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        msg += f"\n⏰ {datetime.now(UTC).strftime('%H:%M:%S UTC')}"
        
        await reply_func(msg, parse_mode="Markdown")
    except Exception as e:
        reply_func = update.message.reply_text if hasattr(update, 'message') else update.callback_query.message.reply_text
        await reply_func(f"❌ Ошибка: {e}")


async def cmd_gatekeeper(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /gatekeeper - статистика Gatekeeper"""
    try:
        # Определяем, откуда пришел запрос
        if hasattr(update, 'message') and update.message:
            reply_func = update.message.reply_text
        else:
            reply_func = update.callback_query.message.reply_text
        
        gatekeeper = get_gatekeeper()
        stats = gatekeeper.get_stats()
        
        msg = "🚪 **GATEKEEPER СТАТИСТИКА**\n\n"
        msg += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        
        if stats["total"] == 0:
            msg += "📊 Сигналы еще не обрабатывались\n"
        else:
            approved_pct = (stats["approved"] / stats["total"]) * 100 if stats["total"] > 0 else 0
            blocked_pct = (stats["blocked"] / stats["total"]) * 100 if stats["total"] > 0 else 0
            
            msg += f"✅ **Одобрено:** `{stats['approved']}` ({approved_pct:.1f}%)\n"
            msg += f"❌ **Заблокировано:** `{stats['blocked']}` ({blocked_pct:.1f}%)\n"
            msg += f"📊 **Всего:** `{stats['total']}`\n"
        
        msg += "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        msg += f"\n⏰ {datetime.now(UTC).strftime('%H:%M:%S UTC')}"
        
        await reply_func(msg, parse_mode="Markdown")
    except Exception as e:
        reply_func = update.message.reply_text if hasattr(update, 'message') else update.callback_query.message.reply_text
        await reply_func(f"❌ Ошибка: {e}")


async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /pause - приостановка торговли"""
    try:
        # Определяем, откуда пришел запрос
        if hasattr(update, 'message') and update.message:
            reply_func = update.message.reply_text
        else:
            reply_func = update.callback_query.message.reply_text
        
        from runner import pause_trading_manually
        
        success = pause_trading_manually()
        if success:
            await reply_func("⏸ **Trading paused manually**\n\nТорговля приостановлена. Используйте `/resume` для возобновления.", parse_mode="Markdown")
        else:
            await reply_func("⏸ **Trading is already paused**\n\nТорговля уже приостановлена.", parse_mode="Markdown")
    except Exception as e:
        reply_func = update.message.reply_text if hasattr(update, 'message') else update.callback_query.message.reply_text
        await reply_func(f"❌ Ошибка: {e}")


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /resume - возобновление торговли"""
    try:
        # Определяем, откуда пришел запрос
        if hasattr(update, 'message') and update.message:
            reply_func = update.message.reply_text
        else:
            reply_func = update.callback_query.message.reply_text
        
        from runner import resume_trading_manually
        
        success, message = resume_trading_manually()
        if success:
            await reply_func("✅ **Trading resumed manually**\n\nТорговля возобновлена.", parse_mode="Markdown")
        else:
            if "safe_mode" in message.lower():
                await reply_func("❌ **Нельзя возобновить:** Система в safe_mode. Сначала необходимо выйти из safe_mode.", parse_mode="Markdown")
            else:
                await reply_func(f"✅ **{message}**\n\nТорговля уже активна.", parse_mode="Markdown")
    except Exception as e:
        reply_func = update.message.reply_text if hasattr(update, 'message') else update.callback_query.message.reply_text
        await reply_func(f"❌ Ошибка: {e}")


def setup_commands(app):
    """
    Настраивает обработчики команд для Telegram бота.
    
    Args:
        app: Application instance от python-telegram-bot
    """
    # Основные команды
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    
    # Decision Core команды
    app.add_handler(CommandHandler("should_i_trade", cmd_should_i_trade))
    app.add_handler(CommandHandler("risk_status", cmd_risk_status))
    app.add_handler(CommandHandler("invest", cmd_invest))
    
    # Команды "мозгов"
    app.add_handler(CommandHandler("market_regime", cmd_market_regime))
    app.add_handler(CommandHandler("risk_exposure", cmd_risk_exposure))
    app.add_handler(CommandHandler("cognitive", cmd_cognitive))
    app.add_handler(CommandHandler("opportunities", cmd_opportunities))
    
    # Статистика
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("trades", cmd_trades))
    app.add_handler(CommandHandler("signals", cmd_signals))
    app.add_handler(CommandHandler("gatekeeper", cmd_gatekeeper))
    
    # Control plane команды
    app.add_handler(CommandHandler("pause", cmd_pause))
    app.add_handler(CommandHandler("resume", cmd_resume))
    
    # Обработчик кнопок
    app.add_handler(CallbackQueryHandler(button_callback))
    
    print("✅ Telegram команды настроены")

