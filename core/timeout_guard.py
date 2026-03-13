"""
TimeoutGuard - Защита от таймаутов

ЦЕЛЬ: Гарантировать, что все операции завершаются в пределах таймаута.
"""
import asyncio
import logging
from typing import TypeVar, Coroutine, Any, Optional
from functools import wraps

logger = logging.getLogger(__name__)

T = TypeVar('T')


class TimeoutError(Exception):
    """Исключение при превышении таймаута"""
    pass


async def timeout_guard(
    coro: Coroutine[Any, Any, T],
    timeout_seconds: float,
    operation_name: str = "operation"
) -> T:
    """
    Выполняет корутину с таймаутом.
    
    Args:
        coro: Корутина для выполнения
        timeout_seconds: Таймаут в секундах
        operation_name: Имя операции (для логирования)
    
    Returns:
        Результат выполнения корутины
    
    Raises:
        TimeoutError: Если операция превысила таймаут
    """
    try:
        result = await asyncio.wait_for(coro, timeout=timeout_seconds)
        return result
    except asyncio.TimeoutError:
        logger.error(
            f"Operation {operation_name} exceeded timeout ({timeout_seconds}s)"
        )
        raise TimeoutError(f"Operation {operation_name} exceeded timeout ({timeout_seconds}s)")


def with_timeout(timeout_seconds: float, operation_name: Optional[str] = None):
    """
    Декоратор для добавления таймаута к async функции.
    
    Args:
        timeout_seconds: Таймаут в секундах
        operation_name: Имя операции (если None, используется имя функции)
    
    Example:
        @with_timeout(5.0, "check_module_health")
        async def check_module_health():
            ...
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            op_name = operation_name or func.__name__
            return await timeout_guard(
                func(*args, **kwargs),
                timeout_seconds=timeout_seconds,
                operation_name=op_name
            )
        return wrapper
    return decorator

