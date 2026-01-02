"""
Трейдинг-боты (исполнители) - генерируют сигналы
"""
# Реэкспорт функции build_signal из модуля signals.py
import sys
import os
from pathlib import Path

# Получаем путь к родительской директории
parent_dir = Path(__file__).parent.parent

# Импортируем build_signal из модуля signals.py напрямую
try:
    # Используем importlib для загрузки модуля signals.py
    import importlib.util
    signals_file = parent_dir / "signals.py"
    if signals_file.exists():
        spec = importlib.util.spec_from_file_location("signals_module", signals_file)
        signals_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(signals_module)
        build_signal = signals_module.build_signal
    else:
        raise ImportError("signals.py not found")
except Exception as e:
    # Fallback: попробуем импортировать через sys.path
    import sys
    if str(parent_dir) not in sys.path:
        sys.path.insert(0, str(parent_dir))
    # Импортируем как модуль (но это может вызвать конфликт)
    import importlib
    signals_module = importlib.import_module("signals")
    build_signal = signals_module.build_signal

__all__ = ['build_signal']

