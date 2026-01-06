"""
DataValidator - Валидация данных для торговых решений

ЦЕЛЬ: Гарантировать, что все данные, используемые в торговых решениях, валидны.
"""
import math
import logging
from typing import Any, Optional, Dict, List
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Результат валидации"""
    valid: bool
    errors: List[str]
    warnings: List[str] = None
    
    def __post_init__(self):
        if self.warnings is None:
            self.warnings = []


class DataValidator:
    """Валидатор данных"""
    
    @staticmethod
    def validate_float(value: Any, name: str, min_value: Optional[float] = None, 
                     max_value: Optional[float] = None, allow_none: bool = False) -> ValidationResult:
        """
        Валидирует float значение.
        
        Args:
            value: Значение для валидации
            name: Имя поля (для сообщений об ошибках)
            min_value: Минимальное значение
            max_value: Максимальное значение
            allow_none: Разрешить None
        
        Returns:
            ValidationResult: Результат валидации
        """
        errors = []
        warnings = []
        
        # Проверка на None
        if value is None:
            if allow_none:
                return ValidationResult(valid=True, errors=[], warnings=[])
            else:
                errors.append(f"{name} is None")
                return ValidationResult(valid=False, errors=errors, warnings=warnings)
        
        # Проверка типа
        if not isinstance(value, (int, float)):
            errors.append(f"{name} is not a number (type: {type(value).__name__})")
            return ValidationResult(valid=False, errors=errors, warnings=warnings)
        
        # Проверка на NaN
        if math.isnan(value):
            errors.append(f"{name} is NaN")
            return ValidationResult(valid=False, errors=errors, warnings=warnings)
        
        # Проверка на Inf
        if math.isinf(value):
            errors.append(f"{name} is Inf")
            return ValidationResult(valid=False, errors=errors, warnings=warnings)
        
        # Проверка диапазона
        if min_value is not None and value < min_value:
            errors.append(f"{name} ({value}) is less than minimum ({min_value})")
        
        if max_value is not None and value > max_value:
            errors.append(f"{name} ({value}) is greater than maximum ({max_value})")
        
        return ValidationResult(valid=len(errors) == 0, errors=errors, warnings=warnings)
    
    @staticmethod
    def validate_confidence_score(confidence: Any) -> ValidationResult:
        """Валидирует confidence score (0.0 - 1.0)"""
        return DataValidator.validate_float(
            confidence,
            name="confidence_score",
            min_value=0.0,
            max_value=1.0,
            allow_none=False
        )
    
    @staticmethod
    def validate_entropy_score(entropy: Any) -> ValidationResult:
        """Валидирует entropy score (0.0 - 1.0)"""
        return DataValidator.validate_float(
            entropy,
            name="entropy_score",
            min_value=0.0,
            max_value=1.0,
            allow_none=False
        )
    
    @staticmethod
    def validate_portfolio_exposure(exposure: Any) -> ValidationResult:
        """Валидирует portfolio exposure (0.0 - 1.0)"""
        return DataValidator.validate_float(
            exposure,
            name="portfolio_exposure",
            min_value=0.0,
            max_value=1.0,
            allow_none=False
        )
    
    @staticmethod
    def validate_not_none(value: Any, name: str) -> ValidationResult:
        """Проверяет, что значение не None"""
        if value is None:
            return ValidationResult(
                valid=False,
                errors=[f"{name} is None"],
                warnings=[]
            )
        return ValidationResult(valid=True, errors=[], warnings=[])
    
    @staticmethod
    def validate_list(value: Any, name: str, min_length: Optional[int] = None,
                     allow_none: bool = False) -> ValidationResult:
        """Валидирует список"""
        errors = []
        
        if value is None:
            if allow_none:
                return ValidationResult(valid=True, errors=[], warnings=[])
            else:
                errors.append(f"{name} is None")
                return ValidationResult(valid=False, errors=errors, warnings=[])
        
        if not isinstance(value, list):
            errors.append(f"{name} is not a list (type: {type(value).__name__})")
            return ValidationResult(valid=False, errors=errors, warnings=[])
        
        if min_length is not None and len(value) < min_length:
            errors.append(f"{name} length ({len(value)}) is less than minimum ({min_length})")
        
        return ValidationResult(valid=len(errors) == 0, errors=errors, warnings=[])
    
    @staticmethod
    def validate_dict(value: Any, name: str, required_keys: Optional[List[str]] = None,
                     allow_none: bool = False) -> ValidationResult:
        """Валидирует словарь"""
        errors = []
        
        if value is None:
            if allow_none:
                return ValidationResult(valid=True, errors=[], warnings=[])
            else:
                errors.append(f"{name} is None")
                return ValidationResult(valid=False, errors=errors, warnings=[])
        
        if not isinstance(value, dict):
            errors.append(f"{name} is not a dict (type: {type(value).__name__})")
            return ValidationResult(valid=False, errors=errors, warnings=[])
        
        if required_keys:
            for key in required_keys:
                if key not in value:
                    errors.append(f"{name} missing required key: {key}")
        
        return ValidationResult(valid=len(errors) == 0, errors=errors, warnings=[])


# Глобальный экземпляр
_data_validator: Optional[DataValidator] = None


def get_data_validator() -> DataValidator:
    """Получить глобальный экземпляр DataValidator"""
    global _data_validator
    if _data_validator is None:
        _data_validator = DataValidator()
    return _data_validator

