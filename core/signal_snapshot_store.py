"""
SignalSnapshotStore - абстракция для persistence layer.

Обеспечивает единую точку входа для всех операций сохранения/загрузки snapshots
с гарантией fault injection в самом начале.
"""
import os
from typing import Optional, Dict
from core.signal_snapshot import SignalSnapshot

# ========== FAULT INJECTION (для тестирования устойчивости) ==========

FAULT_INJECT_STORAGE_FAILURE = os.environ.get("FAULT_INJECT_STORAGE_FAILURE", "false").lower() == "true"


def _check_fault_injection(operation: str):
    """
    Проверяет fault injection в самом начале операции.
    
    Это entry point для всех storage operations - проверка происходит
    ДО любых await, timeout, или mutation операций.
    
    Args:
        operation: Название операции (для логирования)
    
    Raises:
        IOError: Если FAULT_INJECT_STORAGE_FAILURE=true
    """
    if FAULT_INJECT_STORAGE_FAILURE:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(
            f"FAULT_INJECTION: storage_failure - "
            f"Controlled storage failure for {operation}. "
            f"This exception is expected when FAULT_INJECT_STORAGE_FAILURE=true. "
            f"No data mutation occurred."
        )
        raise IOError(
            "FAULT_INJECTION: storage_failure - "
            "Controlled storage failure for runtime resilience testing. "
            "This exception is expected when FAULT_INJECT_STORAGE_FAILURE=true. "
            "No data mutation occurred."
        )


class SignalSnapshotStore:
    """
    Абстракция для persistence layer SignalSnapshot.
    
    Обеспечивает единую точку входа для всех операций сохранения/загрузки
    с гарантией fault injection в самом начале.
    """
    
    @staticmethod
    def save(snapshot: SignalSnapshot) -> None:
        """
        Сохраняет SignalSnapshot.
        
        Это entry point для сохранения - fault injection проверяется
        ДО любых операций записи/чтения.
        
        Args:
            snapshot: SignalSnapshot для сохранения
        
        Raises:
            IOError: Если FAULT_INJECT_STORAGE_FAILURE=true
        """
        # ========== FAULT INJECTION CHECK - VERY ENTRY POINT ==========
        _check_fault_injection("SignalSnapshotStore.save")
        
        # Делегируем в journal.py
        from journal import log_signal_snapshot
        log_signal_snapshot(snapshot)
    
    @staticmethod
    def load_latest(symbol: Optional[str] = None) -> Optional[SignalSnapshot]:
        """
        Загружает последний SignalSnapshot.
        
        Это entry point для загрузки - fault injection проверяется
        ДО любых операций чтения.
        
        Args:
            symbol: Символ для фильтрации (опционально)
        
        Returns:
            SignalSnapshot или None если нет snapshots
        
        Raises:
            IOError: Если FAULT_INJECT_STORAGE_FAILURE=true
        """
        # ========== FAULT INJECTION CHECK - VERY ENTRY POINT ==========
        _check_fault_injection("SignalSnapshotStore.load_latest")
        
        # Для SignalSnapshot загрузка из CSV не реализована в текущей архитектуре
        # Это placeholder для будущей реализации
        return None


class SystemStateSnapshotStore:
    """
    Абстракция для persistence layer SystemState snapshots.
    
    Обеспечивает единую точку входа для всех операций сохранения/загрузки
    с гарантией fault injection в самом начале.
    """
    
    @staticmethod
    def save(snapshot_data: Dict) -> int:
        """
        Сохраняет SystemState snapshot.
        
        Это entry point для сохранения - fault injection проверяется
        ДО любых операций записи/чтения.
        
        Args:
            snapshot_data: Данные снимка (из SystemState.create_snapshot())
        
        Returns:
            int: ID сохранённого снимка
        
        Raises:
            IOError: Если FAULT_INJECT_STORAGE_FAILURE=true
        """
        # ========== FAULT INJECTION CHECK - VERY ENTRY POINT ==========
        _check_fault_injection("SystemStateSnapshotStore.save")
        
        # Делегируем в database.py
        from database import save_system_state_snapshot
        return save_system_state_snapshot(snapshot_data)
    
    @staticmethod
    def load_latest() -> Optional[Dict]:
        """
        Загружает последний SystemState snapshot.
        
        Это entry point для загрузки - fault injection проверяется
        ДО любых операций чтения.
        
        Returns:
            dict: Последний снимок или None если нет снимков
        
        Raises:
            IOError: Если FAULT_INJECT_STORAGE_FAILURE=true
        """
        # ========== FAULT INJECTION CHECK - VERY ENTRY POINT ==========
        _check_fault_injection("SystemStateSnapshotStore.load_latest")
        
        # Делегируем в database.py
        from database import get_latest_system_state_snapshot
        return get_latest_system_state_snapshot()
