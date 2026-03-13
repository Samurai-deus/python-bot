#!/usr/bin/env python3
"""
Тесты для экосистемы торговых ботов
"""
import sys
from datetime import datetime, UTC, timedelta

def test_decision_core():
    """Тест Decision Core"""
    print("=" * 50)
    print("ТЕСТ: Decision Core")
    print("=" * 50)
    
    try:
        from core.decision_core import get_decision_core, MarketRegime, RiskExposure, CognitiveState, Opportunity
        
        decision_core = get_decision_core()
        
        # Тест 1: Без данных
        decision = decision_core.should_i_trade()
        print(f"✅ Тест 1: Decision без данных - {decision.can_trade}")
        assert isinstance(decision.can_trade, bool), "can_trade должен быть bool"
        
        # Тест 2: С Market Regime
        regime = MarketRegime(
            trend_type="TREND",
            volatility_level="MEDIUM",
            risk_sentiment="RISK_ON",
            confidence=0.8
        )
        decision_core.update_market_regime(regime)
        decision = decision_core.should_i_trade()
        print(f"✅ Тест 2: Decision с Market Regime - {decision.can_trade}")
        
        # Тест 3: С Risk & Exposure (перегрузка)
        risk = RiskExposure(
            total_risk_pct=15.0,  # Превышен лимит
            max_correlation=0.5,
            total_leverage=3.0,
            active_positions=5,
            exposure_pct=40.0,
            is_overloaded=True
        )
        decision_core.update_risk_exposure(risk)
        decision = decision_core.should_i_trade()
        print(f"✅ Тест 3: Decision с перегрузкой - can_trade={decision.can_trade}, reason={decision.reason[:50]}")
        assert not decision.can_trade, "Должен блокировать при перегрузке"
        
        # Тест 4: С Cognitive Filter (пауза)
        cognitive = CognitiveState(
            overtrading_score=0.8,
            emotional_entries=5,
            fomo_patterns=3,
            recent_trades_count=15,
            should_pause=True
        )
        decision_core.update_cognitive_state(cognitive)
        decision = decision_core.should_i_trade()
        print(f"✅ Тест 4: Decision с паузой - can_trade={decision.can_trade}")
        assert not decision.can_trade, "Должен блокировать при паузе"
        
        # Тест 5: get_risk_status
        status = decision_core.get_risk_status()
        print(f"✅ Тест 5: get_risk_status - {status.get('can_trade')}")
        assert "can_trade" in status, "Должен содержать can_trade"
        
        print("✅ Все тесты Decision Core пройдены\n")
        return True
        
    except Exception as e:
        print(f"❌ Ошибка в тестах Decision Core: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_market_regime_brain():
    """Тест Market Regime Brain"""
    print("=" * 50)
    print("ТЕСТ: Market Regime Brain")
    print("=" * 50)
    
    try:
        from brains.market_regime_brain import get_market_regime_brain
        
        brain = get_market_regime_brain()
        
        # Создаем тестовые данные
        symbols = ["BTCUSDT", "ETHUSDT"]
        candles_map = {
            "BTCUSDT": {
                "15m": [[0, 50000, 51000, 49000, 50500, 1000]] * 20,
                "30m": [[0, 50000, 51000, 49000, 50500, 1000]] * 20,
                "4h": [[0, 50000, 51000, 49000, 50500, 1000]] * 20
            },
            "ETHUSDT": {
                "15m": [[0, 3000, 3100, 2900, 3050, 500]] * 20,
                "30m": [[0, 3000, 3100, 2900, 3050, 500]] * 20,
                "4h": [[0, 3000, 3100, 2900, 3050, 500]] * 20
            }
        }
        
        regime = brain.analyze(symbols, candles_map)
        print(f"✅ Market Regime: trend={regime.trend_type}, volatility={regime.volatility_level}, risk={regime.risk_sentiment}")
        assert regime.trend_type in ["TREND", "RANGE"], "trend_type должен быть TREND или RANGE"
        assert regime.volatility_level in ["HIGH", "MEDIUM", "LOW"], "volatility_level должен быть HIGH/MEDIUM/LOW"
        
        print("✅ Все тесты Market Regime Brain пройдены\n")
        return True
        
    except Exception as e:
        print(f"❌ Ошибка в тестах Market Regime Brain: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_risk_exposure_brain():
    """Тест Risk & Exposure Brain"""
    print("=" * 50)
    print("ТЕСТ: Risk & Exposure Brain")
    print("=" * 50)
    
    try:
        from brains.risk_exposure_brain import get_risk_exposure_brain
        
        brain = get_risk_exposure_brain()
        
        # Тест без открытых позиций
        symbols = ["BTCUSDT"]
        candles_map = {
            "BTCUSDT": {
                "15m": [[0, 50000, 51000, 49000, 50500, 1000]] * 20
            }
        }
        
        risk = brain.analyze(symbols, candles_map)
        print(f"✅ Risk & Exposure: risk={risk.total_risk_pct:.2f}%, positions={risk.active_positions}, overloaded={risk.is_overloaded}")
        assert risk.total_risk_pct >= 0, "total_risk_pct должен быть >= 0"
        assert risk.active_positions >= 0, "active_positions должен быть >= 0"
        
        print("✅ Все тесты Risk & Exposure Brain пройдены\n")
        return True
        
    except Exception as e:
        print(f"❌ Ошибка в тестах Risk & Exposure Brain: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_cognitive_filter():
    """Тест Cognitive Filter"""
    print("=" * 50)
    print("ТЕСТ: Cognitive Filter")
    print("=" * 50)
    
    try:
        from brains.cognitive_filter import get_cognitive_filter
        
        filter_bot = get_cognitive_filter()
        
        cognitive = filter_bot.analyze()
        print(f"✅ Cognitive Filter: overtrading={cognitive.overtrading_score:.2f}, pause={cognitive.should_pause}")
        assert 0.0 <= cognitive.overtrading_score <= 1.0, "overtrading_score должен быть 0.0-1.0"
        assert isinstance(cognitive.should_pause, bool), "should_pause должен быть bool"
        
        print("✅ Все тесты Cognitive Filter пройдены\n")
        return True
        
    except Exception as e:
        print(f"❌ Ошибка в тестах Cognitive Filter: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_gatekeeper():
    """Тест Gatekeeper"""
    print("=" * 50)
    print("ТЕСТ: Gatekeeper")
    print("=" * 50)
    
    try:
        from execution.gatekeeper import get_gatekeeper
        from core.decision_core import get_decision_core
        
        gatekeeper = get_gatekeeper()
        decision_core = get_decision_core()
        
        # Устанавливаем состояние, которое блокирует торговлю
        from core.decision_core import RiskExposure
        risk = RiskExposure(
            total_risk_pct=15.0,
            max_correlation=0.5,
            total_leverage=3.0,
            active_positions=5,
            exposure_pct=40.0,
            is_overloaded=True
        )
        decision_core.update_risk_exposure(risk)
        
        # Тест проверки сигнала
        signal_data = {
            "zone": {"entry": 50000, "stop": 49000, "target": 52000},
            "position_size": 100.0,
            "leverage": 5.0
        }
        
        result = gatekeeper.check_signal("BTCUSDT", signal_data)
        print(f"✅ Gatekeeper check_signal (перегрузка): {result}")
        assert not result, "Должен блокировать при перегрузке"
        
        # Тест статистики
        stats = gatekeeper.get_stats()
        print(f"✅ Gatekeeper stats: {stats}")
        assert "blocked" in stats, "Должен содержать blocked"
        assert "approved" in stats, "Должен содержать approved"
        
        print("✅ Все тесты Gatekeeper пройдены\n")
        return True
        
    except Exception as e:
        print(f"❌ Ошибка в тестах Gatekeeper: {e}")
        import traceback
        traceback.print_exc()
        return False


def run_all_tests():
    """Запускает все тесты"""
    print("\n" + "=" * 50)
    print("ЗАПУСК ТЕСТОВ ЭКОСИСТЕМЫ")
    print("=" * 50 + "\n")
    
    tests = [
        ("Decision Core", test_decision_core),
        ("Market Regime Brain", test_market_regime_brain),
        ("Risk & Exposure Brain", test_risk_exposure_brain),
        ("Cognitive Filter", test_cognitive_filter),
        ("Gatekeeper", test_gatekeeper),
    ]
    
    results = []
    for name, test_func in tests:
        try:
            result = test_func()
            results.append((name, result))
        except Exception as e:
            print(f"❌ Критическая ошибка в тесте {name}: {e}")
            results.append((name, False))
    
    # Итоги
    print("=" * 50)
    print("ИТОГИ ТЕСТИРОВАНИЯ")
    print("=" * 50)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for name, result in results:
        status = "✅ ПРОЙДЕН" if result else "❌ ПРОВАЛЕН"
        print(f"{name}: {status}")
    
    print(f"\nВсего: {passed}/{total} тестов пройдено")
    
    if passed == total:
        print("🎉 Все тесты пройдены успешно!")
        return 0
    else:
        print("⚠️ Некоторые тесты провалены")
        return 1


if __name__ == "__main__":
    sys.exit(run_all_tests())

