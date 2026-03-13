"""
Drift Metrics - вычисление метрик для Drift Detector.

Содержит функции для вычисления статистических метрик:
- mean, variance
- percentile (p90, p95)
- correlation
"""
from typing import List, Tuple
import statistics
import math


def calculate_mean(values: List[float]) -> float:
    """
    Вычисляет среднее значение.
    
    Args:
        values: Список значений
    
    Returns:
        float: Среднее значение
    """
    if not values:
        return 0.0
    return statistics.mean(values)


def calculate_variance(values: List[float]) -> float:
    """
    Вычисляет дисперсию.
    
    Args:
        values: Список значений
    
    Returns:
        float: Дисперсия
    """
    if not values or len(values) < 2:
        return 0.0
    return statistics.variance(values)


def calculate_percentile(values: List[float], percentile: float) -> float:
    """
    Вычисляет перцентиль.
    
    Args:
        values: Список значений
        percentile: Перцентиль (0.0 - 1.0)
    
    Returns:
        float: Значение перцентиля
    """
    if not values:
        return 0.0
    
    sorted_values = sorted(values)
    index = percentile * (len(sorted_values) - 1)
    
    if index.is_integer():
        return sorted_values[int(index)]
    else:
        lower = sorted_values[int(index)]
        upper = sorted_values[int(index) + 1]
        return lower + (upper - lower) * (index - int(index))


def calculate_correlation(x_values: List[float], y_values: List[float]) -> float:
    """
    Вычисляет корреляцию Пирсона между двумя списками значений.
    
    Args:
        x_values: Первый список значений
        y_values: Второй список значений
    
    Returns:
        float: Корреляция (-1.0 до 1.0)
    
    Примечание:
        Возвращает 0.0 если недостаточно данных или дисперсия равна 0.
    """
    if len(x_values) != len(y_values):
        return 0.0
    
    if len(x_values) < 2:
        return 0.0
    
    # Вычисляем средние
    x_mean = calculate_mean(x_values)
    y_mean = calculate_mean(y_values)
    
    # Вычисляем корреляцию
    numerator = 0.0
    x_variance = 0.0
    y_variance = 0.0
    
    for x, y in zip(x_values, y_values):
        x_diff = x - x_mean
        y_diff = y - y_mean
        numerator += x_diff * y_diff
        x_variance += x_diff * x_diff
        y_variance += y_diff * y_diff
    
    # Проверяем, что дисперсии не равны нулю
    if x_variance == 0.0 or y_variance == 0.0:
        return 0.0
    
    denominator = math.sqrt(x_variance * y_variance)
    
    if denominator == 0.0:
        return 0.0
    
    return numerator / denominator


def calculate_metrics(
    confidence_values: List[float],
    entropy_values: List[float]
) -> Tuple[float, float, float, float, float, float, float]:
    """
    Вычисляет все метрики для списка значений.
    
    Args:
        confidence_values: Список значений confidence
        entropy_values: Список значений entropy
    
    Returns:
        Tuple: (confidence_mean, confidence_variance, confidence_p90, confidence_p95,
                entropy_mean, entropy_variance, entropy_p90, entropy_p95, correlation)
    """
    confidence_mean = calculate_mean(confidence_values)
    confidence_variance = calculate_variance(confidence_values)
    confidence_p90 = calculate_percentile(confidence_values, 0.90)
    confidence_p95 = calculate_percentile(confidence_values, 0.95)
    
    entropy_mean = calculate_mean(entropy_values)
    entropy_variance = calculate_variance(entropy_values)
    entropy_p90 = calculate_percentile(entropy_values, 0.90)
    entropy_p95 = calculate_percentile(entropy_values, 0.95)
    
    correlation = calculate_correlation(confidence_values, entropy_values)
    
    return (
        confidence_mean, confidence_variance, confidence_p90, confidence_p95,
        entropy_mean, entropy_variance, entropy_p90, entropy_p95,
        correlation
    )


def calculate_drift_metrics(
    recent_confidence: List[float],
    recent_entropy: List[float],
    baseline_confidence: List[float],
    baseline_entropy: List[float]
) -> Tuple[float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float]:
    """
    Вычисляет все метрики для recent и baseline окон.
    
    Args:
        recent_confidence: Confidence в recent_window
        recent_entropy: Entropy в recent_window
        baseline_confidence: Confidence в baseline_window
        baseline_entropy: Entropy в baseline_window
    
    Returns:
        Tuple: Все метрики для DriftMetrics
    """
    # Recent метрики
    (
        conf_mean_recent, conf_var_recent, conf_p90_recent, conf_p95_recent,
        ent_mean_recent, ent_var_recent, ent_p90_recent, ent_p95_recent,
        corr_recent
    ) = calculate_metrics(recent_confidence, recent_entropy)
    
    # Baseline метрики
    (
        conf_mean_baseline, conf_var_baseline, conf_p90_baseline, conf_p95_baseline,
        ent_mean_baseline, ent_var_baseline, ent_p90_baseline, ent_p95_baseline,
        corr_baseline
    ) = calculate_metrics(baseline_confidence, baseline_entropy)
    
    return (
        conf_mean_recent, conf_mean_baseline,
        conf_var_recent, conf_var_baseline,
        conf_p90_recent, conf_p95_recent,
        conf_p90_baseline, conf_p95_baseline,
        ent_mean_recent, ent_mean_baseline,
        ent_var_recent, ent_var_baseline,
        ent_p90_recent, ent_p95_recent,
        ent_p90_baseline, ent_p95_baseline,
        corr_recent, corr_baseline
    )

