# КОНТРАКТНЫЕ ТЕСТЫ ДЛЯ MARKET_STATE

## Назначение

Эти тесты защищают архитектурное правило:
**"runtime-логика работает ТОЛЬКО с MarketState enum"**

## Запуск тестов

```bash
# Все тесты
pytest tests/test_market_state_contract.py -v

# Конкретный класс тестов
pytest tests/test_market_state_contract.py::TestNormalizeState -v

# Конкретный тест
pytest tests/test_market_state_contract.py::TestArchitecturalRule::test_strings_are_not_market_state -v
```

## Защищаемые правила

### 1. Нормализация работает правильно
- `normalize_state()` преобразует валидные строки в enum
- Невалидные значения возвращают None
- `normalize_states_dict()` нормализует весь словарь

### 2. Runtime-функции работают только с enum
- `risk_level()` принимает только enum или None
- `calculate_score()` принимает только enum или None
- `build_signal()` принимает только enum или None

### 3. Строки запрещены в runtime
- Строки НЕ равны enum (даже если значение совпадает)
- После нормализации не должно быть строк
- Инварианты должны ловить нарушения

### 4. Защита от регрессий
- Тесты падают, если кто-то добавит `or state == "D"`
- Контракт `normalize_states_dict()` должен соблюдаться
- IO ↔ Runtime граница должна работать правильно

## Если тесты падают

Если тесты падают - значит кто-то нарушил архитектурное правило:

1. **Проверьте, нет ли сравнений со строками в runtime-логике**
   - Ищите: `or state == "D"`, `state == "A"` и т.д.
   - Должно быть: `state == MarketState.D`

2. **Проверьте, что normalize_states_dict() вызывается перед runtime-функциями**

3. **Проверьте, что инварианты работают правильно**

