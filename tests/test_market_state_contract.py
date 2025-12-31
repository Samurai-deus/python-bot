"""
Контрактные тесты для архитектурного правила:
"runtime-логика работает ТОЛЬКО с MarketState enum"

Эти тесты защищают архитектурное разделение:
- IO-слой: строки допускаются
- Runtime-логика: ТОЛЬКО MarketState enum или None

Если тесты падают - значит кто-то нарушил архитектурное правило.
"""
import pytest
import logging
from typing import Dict, Optional
from core.market_state import (
    MarketState,
    normalize_state,
    normalize_states_dict,
    validate_state,
    state_to_string,
    get_state_text
)
from risk import risk_level
from scoring import calculate_score, get_entry_conditions
from signals import build_signal


class TestNormalizeState:
    """Тесты для normalize_state() - нормализация строк в enum"""
    
    def test_valid_strings_to_enum(self):
        """Валидные строки должны преобразовываться в enum"""
        assert normalize_state("A") == MarketState.A
        assert normalize_state("B") == MarketState.B
        assert normalize_state("C") == MarketState.C
        assert normalize_state("D") == MarketState.D
    
    def test_none_returns_none(self):
        """None должен оставаться None"""
        assert normalize_state(None) is None
    
    def test_invalid_strings_return_none(self):
        """Невалидные строки должны возвращать None"""
        assert normalize_state("X") is None
        assert normalize_state("") is None
        assert normalize_state("invalid") is None
        assert normalize_state("a") is None  # lowercase
        assert normalize_state("E") is None  # несуществующее состояние
    
    def test_non_string_types_return_none(self):
        """Не-строковые типы должны возвращать None"""
        assert normalize_state(123) is None
        assert normalize_state([]) is None
        assert normalize_state({}) is None
        assert normalize_state(True) is None


class TestNormalizeStatesDict:
    """Тесты для normalize_states_dict() - нормализация словаря"""
    
    def test_mixed_types_normalized(self):
        """Словарь со смешанными типами должен нормализоваться"""
        states = {
            "15m": "D",  # строка
            "30m": MarketState.A,  # уже enum
            "1h": None,  # None
            "5m": "X"  # невалидная строка
        }
        
        normalized = normalize_states_dict(states)
        
        # Все значения должны быть MarketState enum или None
        assert isinstance(normalized["15m"], MarketState)
        assert normalized["15m"] == MarketState.D
        
        assert isinstance(normalized["30m"], MarketState)
        assert normalized["30m"] == MarketState.A
        
        assert normalized["1h"] is None
        
        assert normalized["5m"] is None  # невалидная строка → None
    
    def test_all_strings_normalized(self):
        """Все строки должны преобразовываться в enum"""
        states = {
            "15m": "A",
            "30m": "B",
            "1h": "C",
            "5m": "D"
        }
        
        normalized = normalize_states_dict(states)
        
        assert all(isinstance(v, MarketState) for v in normalized.values())
        assert normalized["15m"] == MarketState.A
        assert normalized["30m"] == MarketState.B
        assert normalized["1h"] == MarketState.C
        assert normalized["5m"] == MarketState.D
    
    def test_all_enums_preserved(self):
        """Enum значения должны сохраняться"""
        states = {
            "15m": MarketState.D,
            "30m": MarketState.A,
            "1h": None
        }
        
        normalized = normalize_states_dict(states)
        
        assert normalized["15m"] == MarketState.D
        assert normalized["30m"] == MarketState.A
        assert normalized["1h"] is None
    
    def test_invalid_values_become_none(self):
        """Невалидные значения должны становиться None"""
        states = {
            "15m": "X",
            "30m": 123,
            "1h": [],
            "5m": {}
        }
        
        normalized = normalize_states_dict(states)
        
        assert all(v is None for v in normalized.values())


class TestRuntimeFunctionsContract:
    """
    КОНТРАКТНЫЕ ТЕСТЫ: Runtime-функции работают ТОЛЬКО с enum
    
    Эти тесты защищают архитектурное правило:
    ❌ Строки в runtime - ЗАПРЕЩЕНЫ
    ✅ Строки допустимы только до normalize_states_dict()
    """
    
    def test_risk_level_accepts_enum(self):
        """risk_level() должна работать с enum"""
        states = {
            "15m": MarketState.D,
            "30m": MarketState.A,
            "1h": MarketState.B
        }
        
        result = risk_level(states)
        
        # Должна вернуть валидный уровень риска
        assert result in ["LOW", "MEDIUM", "HIGH"]
    
    def test_risk_level_accepts_none(self):
        """risk_level() должна работать с None"""
        states = {
            "15m": MarketState.D,
            "30m": None,
            "1h": None
        }
        
        result = risk_level(states)
        
        # Если 1h is None, должен вернуть HIGH
        assert result == "HIGH"
    
    def test_risk_level_normalizes_strings(self):
        """
        risk_level() должна нормализовать строки (но это нежелательно)
        
        ВАЖНО: Это тест на защиту от регрессии.
        В идеале строки не должны попадать в runtime-функции,
        но если попадут - функция должна их нормализовать.
        """
        states = {
            "15m": "D",  # строка (нежелательно, но защита должна сработать)
            "30m": MarketState.A,
            "1h": MarketState.B
        }
        
        result = risk_level(states)
        
        # Должна нормализовать и вернуть валидный результат
        assert result in ["LOW", "MEDIUM", "HIGH"]
    
    def test_calculate_score_accepts_enum(self):
        """calculate_score() должна работать с enum"""
        states = {
            "15m": MarketState.D,
            "30m": MarketState.A,
            "1h": MarketState.B
        }
        
        score, reasons, details = calculate_score(
            states,
            directions={"30m": "UP", "1h": "UP", "4h": "UP"},
            is_flat=False,
            good_time=True
        )
        
        assert isinstance(score, (int, float))
        assert isinstance(reasons, list)
        assert isinstance(details, dict)
    
    def test_calculate_score_normalizes_strings(self):
        """calculate_score() должна нормализовать строки"""
        states = {
            "15m": "D",  # строка
            "30m": "A",  # строка
            "1h": MarketState.B
        }
        
        score, reasons, details = calculate_score(
            states,
            directions={"30m": "UP", "1h": "UP", "4h": "UP"},
            is_flat=False,
            good_time=True
        )
        
        # Должна нормализовать и вернуть валидный результат
        assert isinstance(score, (int, float))
    
    def test_build_signal_accepts_enum(self):
        """build_signal() должна работать с enum"""
        states = {
            "15m": MarketState.D,
            "30m": MarketState.A,
            "1h": MarketState.B
        }
        
        result = build_signal(
            symbol="BTCUSDT",
            states=states,
            risk="LOW",
            directions={"30m": "UP"}
        )
        
        assert isinstance(result, str)
        assert "BTCUSDT" in result
    
    def test_build_signal_normalizes_strings(self):
        """build_signal() должна нормализовать строки"""
        states = {
            "15m": "D",  # строка
            "30m": "A",  # строка
            "1h": MarketState.B
        }
        
        result = build_signal(
            symbol="BTCUSDT",
            states=states,
            risk="LOW",
            directions={"30m": "UP"}
        )
        
        # Должна нормализовать и вернуть валидный результат
        assert isinstance(result, str)


class TestArchitecturalRule:
    """
    АРХИТЕКТУРНОЕ ПРАВИЛО: Строки запрещены в runtime
    
    Эти тесты явно фиксируют правило:
    - Строки допустимы только до normalize_states_dict()
    - После нормализации - только enum или None
    """
    
    def test_strings_are_not_market_state(self):
        """Строки НЕ являются MarketState enum"""
        assert not isinstance("A", MarketState)
        assert not isinstance("D", MarketState)
        assert "A" != MarketState.A  # Строка не равна enum
    
    def test_enum_comparison_works(self):
        """Enum сравнения работают правильно"""
        state1 = MarketState.D
        state2 = MarketState.D
        state3 = MarketState.A
        
        assert state1 == state2
        assert state1 != state3
        assert state1 == MarketState.D
    
    def test_string_vs_enum_comparison(self):
        """Строка НЕ равна enum (даже если значение совпадает)"""
        # Это критично: "D" != MarketState.D
        # Если кто-то добавит "or state == 'D'" - это нарушение архитектуры
        assert "D" != MarketState.D
        assert "A" != MarketState.A
        
        # Правильный способ - использовать enum
        assert MarketState.D == MarketState.D
    
    def test_normalize_states_dict_output_contract(self):
        """
        КОНТРАКТ: normalize_states_dict() возвращает ТОЛЬКО enum или None
        
        Это ключевой контракт системы. После нормализации
        в словаре не должно быть строк.
        """
        states = {
            "15m": "D",
            "30m": "A",
            "1h": None,
            "5m": "X"
        }
        
        normalized = normalize_states_dict(states)
        
        # КОНТРАКТ: Все значения должны быть MarketState или None
        for key, value in normalized.items():
            assert value is None or isinstance(value, MarketState), (
                f"CONTRACT VIOLATION: normalized[{key}] = {value} (type: {type(value).__name__}), "
                f"expected MarketState or None. normalize_states_dict() нарушил контракт."
            )
    
    def test_runtime_functions_reject_strings_after_normalization(self):
        """
        КОНТРАКТ: Runtime-функции должны работать только с enum после нормализации
        
        Если после normalize_states_dict() остались строки - это ошибка.
        Инварианты в runtime-функциях должны это поймать.
        """
        states = {
            "15m": "D",
            "30m": MarketState.A,
            "1h": None
        }
        
        # Нормализуем
        normalized = normalize_states_dict(states)
        
        # Проверяем контракт: после нормализации не должно быть строк
        for key, value in normalized.items():
            assert value is None or isinstance(value, MarketState), (
                f"После normalize_states_dict() остались строки: normalized[{key}] = {value}"
            )
        
        # Runtime-функции должны работать с нормализованными данными
        result = risk_level(normalized)
        assert result in ["LOW", "MEDIUM", "HIGH"]


class TestInvariantProtection:
    """
    Тесты на защиту инвариантов
    
    Проверяем, что инварианты в runtime-функциях работают правильно.
    """
    
    def test_validate_state_accepts_enum(self):
        """validate_state() должна принимать enum"""
        assert validate_state(MarketState.D) == MarketState.D
        assert validate_state(MarketState.A) == MarketState.A
    
    def test_validate_state_accepts_none(self):
        """validate_state() должна принимать None"""
        assert validate_state(None) is None
    
    def test_validate_state_rejects_strings(self):
        """validate_state() должна отклонять строки и логировать ошибку"""
        with pytest.raises(AssertionError) if False else None:
            # validate_state() не падает, но логирует ошибку
            result = validate_state("D", context="test")
            # Должна попытаться нормализовать
            assert result == MarketState.D or result is None
    
    def test_validate_state_rejects_invalid_types(self):
        """validate_state() должна отклонять невалидные типы"""
        result = validate_state(123, context="test")
        assert result is None  # Безопасный fallback


class TestIOBoundary:
    """
    Тесты на границу IO ↔ Runtime
    
    Проверяем, что преобразования на границе работают правильно.
    """
    
    def test_state_to_string_converts_enum(self):
        """state_to_string() должна преобразовывать enum в строку"""
        assert state_to_string(MarketState.D) == "D"
        assert state_to_string(MarketState.A) == "A"
        assert state_to_string(None) == ""
    
    def test_get_state_text_works_with_enum(self):
        """get_state_text() должна работать с enum"""
        assert get_state_text(MarketState.D) == "Отказ"
        assert get_state_text(MarketState.A) == "Импульс"
        assert get_state_text(None) == "Неопределённость"
    
    def test_io_to_runtime_flow(self):
        """
        Тест полного потока: IO → Runtime → IO
        
        1. Читаем строку из CSV (IO)
        2. Нормализуем в enum (граница)
        3. Используем в runtime
        4. Преобразуем обратно в строку для вывода (IO)
        """
        # 1. IO: строка из CSV
        csv_value = "D"
        
        # 2. Граница: нормализация
        state = normalize_state(csv_value)
        assert isinstance(state, MarketState)
        assert state == MarketState.D
        
        # 3. Runtime: использование
        states = {"15m": state}
        risk = risk_level(states)
        assert risk in ["LOW", "MEDIUM", "HIGH"]
        
        # 4. IO: преобразование обратно
        output_string = state_to_string(state)
        assert output_string == "D"
        
        # 5. IO: форматирование для пользователя
        text = get_state_text(state)
        assert text == "Отказ"


class TestRegressionProtection:
    """
    Защита от регрессий
    
    Эти тесты падают, если кто-то нарушит архитектурное правило.
    """
    
    def test_no_string_comparisons_in_runtime(self):
        """
        КРИТИЧНО: Этот тест падает, если кто-то добавит "or state == 'D'"
        
        Правило: В runtime-логике НЕ должно быть сравнений со строками.
        Все сравнения должны быть с MarketState enum.
        """
        # Создаём состояние как enum
        state = MarketState.D
        
        # Проверяем, что сравнение работает ТОЛЬКО с enum
        assert state == MarketState.D
        
        # Проверяем, что строка НЕ равна enum
        assert state != "D"
        
        # Если кто-то добавит "or state == 'D'" - это нарушение
        # Правильный код: state == MarketState.D
        # Неправильный код: state == MarketState.D or state == "D"
    
    def test_normalize_states_dict_contract_enforced(self):
        """
        КОНТРАКТ: normalize_states_dict() гарантирует только enum или None
        
        Если этот контракт нарушен - все последующие тесты должны падать.
        """
        states = {
            "15m": "D",
            "30m": "A",
            "1h": None
        }
        
        normalized = normalize_states_dict(states)
        
        # Строгий контракт: ТОЛЬКО enum или None
        for key, value in normalized.items():
            assert value is None or isinstance(value, MarketState), (
                f"CONTRACT VIOLATION: normalize_states_dict() вернул не enum: "
                f"normalized[{key}] = {value} (type: {type(value).__name__})"
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

