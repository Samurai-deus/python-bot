# DRIFT DETECTOR - АРХИТЕКТУРА

**Дата:** 2024-12-19  
**Задача:** Реализовать Drift Detector для выявления деградации поведения системы

---

## ✅ ВЫПОЛНЕНО

### 1. Создан Drift Detector

**Файлы:**
- `core/drift_models.py` - модели данных
- `core/drift_metrics.py` - вычисление метрик
- `core/drift_detector.py` - основная логика

**Класс:** `DriftDetector`

**Принцип:**
- Drift Detector НЕ торгует
- НЕ использует рынок или индикаторы
- Работает ТОЛЬКО на SignalSnapshot
- Не изменяет SystemState напрямую

---

## 📋 ЧЕМ DRIFT ОТЛИЧАЕТСЯ ОТ DRAWDOWN

### Drawdown:
- Снижение капитала/прибыли (финансовый показатель)
- Измеряется в процентах от пика
- Показывает финансовые потери
- Может быть временным (рыночные условия)

### Drift:
- Изменение поведения системы (когнитивный показатель)
- Измеряется через entropy и confidence
- Показывает деградацию мышления системы
- Может происходить БЕЗ drawdown (система работает, но по-другому)

### Примеры:
- **Drift БЕЗ drawdown**: Система изменила поведение, но всё ещё прибыльна
- **Drawdown БЕЗ drift**: Временные рыночные условия, система работает нормально
- **Drift + drawdown**: Критическая ситуация - система деградировала и теряет деньги

---

## 📋 ПОЧЕМУ ENTROPY И CONFIDENCE - ВЕДУЩИЕ ИНДИКАТОРЫ

### Confidence (Уверенность системы):
- Показывает, насколько система уверена в своих решениях
- **Низкая confidence** → система не уверена → возможна деградация
- **Высокая confidence при плохих результатах** → переобучение/overfitting
- **Стабильная confidence** → система работает предсказуемо

### Entropy (Когнитивная неопределённость):
- Показывает структурированность рынка
- **Высокая entropy** → рынок хаотичен → система может работать хуже
- **Низкая entropy** → рынок структурирован → система должна работать лучше
- **Резкое изменение entropy** → изменение структуры рынка

### Decoupling (Рассогласование):
- Когда confidence и entropy не согласованы
- **Высокая confidence + высокая entropy** → система переоценивает себя
- **Низкая confidence + низкая entropy** → система недооценивает возможности
- **Изменение корреляции** → изменение связи между уверенностью и структурой

---

## 📋 СТРУКТУРА

### DriftModels (drift_models.py)

1. **DriftSeverity** (enum) - LOW, MEDIUM, HIGH
2. **DriftType** (enum) - CONFIDENCE, ENTROPY, DECOUPLING, OVERALL
3. **DriftMetrics** (dataclass) - все метрики для анализа
4. **ConfidenceDrift** (dataclass) - drift в confidence
5. **EntropyDrift** (dataclass) - drift в entropy
6. **DecouplingDrift** (dataclass) - рассогласование
7. **DriftState** (dataclass) - общее состояние drift

### DriftMetrics (drift_metrics.py)

**Функции:**
- `calculate_mean()` - среднее значение
- `calculate_variance()` - дисперсия
- `calculate_percentile()` - перцентиль (p90, p95)
- `calculate_correlation()` - корреляция Пирсона
- `calculate_metrics()` - все метрики для списка значений
- `calculate_drift_metrics()` - метрики для recent и baseline окон

### DriftDetector (drift_detector.py)

**Методы:**
- `detect_drift()` - основной метод обнаружения drift
- `detect_confidence_drift()` - drift в confidence
- `detect_entropy_drift()` - drift в entropy
- `detect_decoupling_drift()` - рассогласование
- `compute_overall_drift()` - общий drift

**Внутренние методы:**
- `_split_windows()` - разделение на recent и baseline окна
- `_extract_values()` - извлечение confidence и entropy
- `_calculate_metrics()` - вычисление метрик

---

## 🔧 ПРОЦЕСС ОБНАРУЖЕНИЯ DRIFT

### Шаг 1: Получение snapshot'ов

```python
snapshots = snapshot_store.get_recent_snapshots(limit=1000)
```

### Шаг 2: Разделение на окна

```python
# Recent окно: последние 24 часа
# Baseline окно: предыдущие 7 дней
recent_snapshots, baseline_snapshots = _split_windows(snapshots, end_time)
```

### Шаг 3: Извлечение значений

```python
recent_confidence, recent_entropy = _extract_values(recent_snapshots)
baseline_confidence, baseline_entropy = _extract_values(baseline_snapshots)
```

### Шаг 4: Вычисление метрик

```python
metrics = _calculate_metrics(
    recent_confidence, recent_entropy,
    baseline_confidence, baseline_entropy
)
# Вычисляет: mean, variance, p90, p95, correlation
```

### Шаг 5: Обнаружение drift

```python
# Confidence drift
confidence_drift = detect_confidence_drift(metrics)

# Entropy drift
entropy_drift = detect_entropy_drift(metrics)

# Decoupling drift
decoupling_drift = detect_decoupling_drift(metrics)

# Overall drift
overall_drift, severity, reason = compute_overall_drift(
    confidence_drift, entropy_drift, decoupling_drift
)
```

### Шаг 6: Формирование DriftState

```python
drift_state = DriftState(
    confidence_drift=confidence_drift,
    entropy_drift=entropy_drift,
    decoupling_drift=decoupling_drift,
    overall_drift_detected=overall_drift,
    overall_severity=severity,
    overall_reason=reason,
    metrics=metrics
)
```

---

## 📊 МЕТРИКИ

### Confidence метрики:
- `confidence_mean_recent` - среднее в recent окне
- `confidence_mean_baseline` - среднее в baseline окне
- `confidence_variance_recent` - дисперсия в recent окне
- `confidence_variance_baseline` - дисперсия в baseline окне
- `confidence_p90_recent` - 90-й перцентиль в recent окне
- `confidence_p95_recent` - 95-й перцентиль в recent окне

### Entropy метрики:
- `entropy_mean_recent` - среднее в recent окне
- `entropy_mean_baseline` - среднее в baseline окне
- `entropy_variance_recent` - дисперсия в recent окне
- `entropy_variance_baseline` - дисперсия в baseline окне
- `entropy_p90_recent` - 90-й перцентиль в recent окне
- `entropy_p95_recent` - 95-й перцентиль в recent окне

### Correlation метрики:
- `correlation_recent` - корреляция confidence и entropy в recent окне
- `correlation_baseline` - корреляция confidence и entropy в baseline окне

---

## 📊 ПОРОГИ ОБНАРУЖЕНИЯ

### Confidence Drift:
- **LOW**: изменение среднего > 10%
- **MEDIUM**: изменение среднего > 15%
- **HIGH**: изменение среднего > 25%
- Дисперсия: изменение > 50% → MEDIUM или HIGH
- Перцентиль: сдвиг > 0.15 → MEDIUM или HIGH

### Entropy Drift:
- **LOW**: изменение среднего > 10%
- **MEDIUM**: изменение среднего > 15%
- **HIGH**: изменение среднего > 25%
- Дисперсия: изменение > 50% → MEDIUM или HIGH
- Перцентиль: сдвиг > 0.15 → MEDIUM или HIGH

### Decoupling Drift:
- **LOW**: изменение корреляции > 20%
- **MEDIUM**: изменение корреляции > 30%
- **HIGH**: изменение корреляции > 40%

---

## 📋 ПРИМЕРЫ ИСПОЛЬЗОВАНИЯ

### Пример 1: Обнаружение drift

```python
from core.drift_detector import DriftDetector
from core.signal_snapshot_store import SignalSnapshotStore

store = SignalSnapshotStore()
detector = DriftDetector(
    recent_window_hours=24,
    baseline_window_hours=168  # 7 дней
)

# Получаем snapshot'ы
snapshots = store.get_recent_snapshots(limit=1000)

# Обнаруживаем drift
drift_state = detector.detect_drift(snapshots)

if drift_state and drift_state.has_any_drift():
    print(f"Drift detected: {drift_state.overall_severity.value}")
    print(f"Reason: {drift_state.overall_reason}")
```

### Пример 2: Анализ конкретного типа drift

```python
if drift_state.confidence_drift.detected:
    print(f"Confidence drift: {drift_state.confidence_drift.severity.value}")
    print(f"Mean diff: {drift_state.confidence_drift.mean_diff_pct * 100:.1f}%")

if drift_state.entropy_drift.detected:
    print(f"Entropy drift: {drift_state.entropy_drift.severity.value}")
    print(f"Mean diff: {drift_state.entropy_drift.mean_diff_pct * 100:.1f}%")

if drift_state.decoupling_drift.detected:
    print(f"Decoupling drift: {drift_state.decoupling_drift.severity.value}")
    print(f"Correlation diff: {drift_state.decoupling_drift.correlation_diff:.3f}")
```

### Пример 3: Интеграция с MetaDecisionBrain

```python
from brains.meta_decision_brain import MetaDecisionBrain

# Обнаруживаем drift
drift_state = detector.detect_drift(snapshots)

# Передаём в MetaDecisionBrain
meta_brain = MetaDecisionBrain()
result = meta_brain.evaluate(
    confidence_score=current_confidence,
    entropy_score=current_entropy,
    portfolio_exposure=exposure,
    system_health=SystemHealthStatus.DEGRADED if drift_state.has_any_drift() else SystemHealthStatus.OK,
    drift_state=drift_state  # Передаём drift_state
)
```

---

## 🔄 ИНТЕГРАЦИЯ С META DECISION BRAIN

DriftState передаётся в MetaDecisionBrain как один из факторов:

```python
# В MetaDecisionBrain.evaluate()
if drift_state and drift_state.has_any_drift():
    # Учитываем drift при принятии решения
    if drift_state.get_max_severity() == DriftSeverity.HIGH:
        # Критический drift - более строгие проверки
        ...
    elif drift_state.get_max_severity() == DriftSeverity.MEDIUM:
        # Заметный drift - дополнительные проверки
        ...
```

**Важно:**
- Drift НЕ блокирует торговлю напрямую
- MetaDecisionBrain использует DriftState как один из факторов
- Drift может влиять на system_health (OK → DEGRADED)

---

## ✅ ПОДТВЕРЖДЕНИЕ ТРЕБОВАНИЙ

### 1. Архитектура
- ✅ Файлы созданы (drift_models.py, drift_metrics.py, drift_detector.py)
- ✅ Класс `DriftDetector` реализован
- ✅ Drift Detector НЕ торгует
- ✅ НЕ использует рынок или индикаторы
- ✅ Работает ТОЛЬКО на SignalSnapshot
- ✅ Не изменяет SystemState напрямую

### 2. Функциональность
- ✅ `detect_confidence_drift()` реализован
- ✅ `detect_entropy_drift()` реализован
- ✅ `detect_decoupling_drift()` реализован
- ✅ `compute_overall_drift()` реализован
- ✅ Использует скользящие окна (recent_window, baseline_window)

### 3. Метрики
- ✅ mean (среднее)
- ✅ variance (дисперсия)
- ✅ percentile (p90, p95)
- ✅ correlation(confidence, entropy)

### 4. DriftState
- ✅ Флаги по каждому типу drift
- ✅ severity (LOW/MEDIUM/HIGH)
- ✅ Текстовое объяснение

### 5. Код
- ✅ Использует dataclass
- ✅ Нет singleton
- ✅ Детерминированный
- ✅ Легко тестируемый

### 6. Интеграция
- ✅ DriftState передаётся в MetaDecisionBrain
- ✅ Drift НЕ блокирует торговлю напрямую

### 7. Документация
- ✅ Объяснена разница между Drift и Drawdown
- ✅ Объяснено, почему entropy/confidence - ведущие индикаторы

---

## 🎯 РЕЗУЛЬТАТ

### Достигнуто:
1. ✅ **DriftDetector создан** - обнаружение деградации поведения
2. ✅ **DriftModels созданы** - все необходимые dataclass'ы
3. ✅ **DriftMetrics созданы** - вычисление метрик
4. ✅ **Интеграция подготовлена** - DriftState передаётся в MetaDecisionBrain
5. ✅ **Документация** - объяснение Drift vs Drawdown, entropy/confidence

### Архитектура:
- ✅ Оффлайн-инструмент мониторинга
- ✅ Не влияет на торговую логику напрямую
- ✅ Детерминированный
- ✅ Готов к использованию

---

*Drift Detector готов к использованию.*

