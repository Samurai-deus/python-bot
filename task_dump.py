"""
Task Dump Module - Observability для asyncio tasks

ЦЕЛЬ: Dump всех asyncio tasks при CRITICAL состояниях для диагностики.

ФОРМАТ:
- task_id, task_name
- coroutine stacktrace
- state (running, pending, done)
- timestamps (created, last_yield)
- locks/semaphores (если доступно)
"""
import asyncio
import traceback
import logging
import json
from typing import Dict, Any, List, Optional
from datetime import datetime, UTC

logger = logging.getLogger(__name__)


def dump_all_tasks(incident_id: str) -> Dict[str, Any]:
    """
    Dump всех asyncio tasks
    
    Args:
        incident_id: ID инцидента для корреляции
    
    Returns:
        Структурированный dump всех tasks
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return {
            "incident_id": incident_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "error": "no_running_event_loop",
            "tasks": []
        }
    
    tasks = asyncio.all_tasks(loop)
    task_dumps = []
    
    for task in tasks:
        try:
            task_dump = _dump_task(task, incident_id)
            task_dumps.append(task_dump)
        except Exception as e:
            logger.error(f"TASK_DUMP_ERROR task={task.get_name()} error={type(e).__name__}: {e}")
            task_dumps.append({
                "task_id": id(task),
                "task_name": task.get_name(),
                "error": f"dump_failed: {type(e).__name__}: {e}"
            })
    
    return {
        "incident_id": incident_id,
        "timestamp": datetime.now(UTC).isoformat(),
        "total_tasks": len(tasks),
        "tasks": task_dumps
    }


def _dump_task(task: asyncio.Task, incident_id: str) -> Dict[str, Any]:
    """Dump одного task"""
    task_name = task.get_name()
    task_id = id(task)
    
    # Состояние task
    if task.done():
        state = "done"
        exception = None
        if task.exception():
            try:
                exception = {
                    "type": type(task.exception()).__name__,
                    "message": str(task.exception()),
                    "traceback": traceback.format_exception(
                        type(task.exception()),
                        task.exception(),
                        task.exception().__traceback__
                    )
                }
            except Exception:
                exception = {"error": "failed_to_extract_exception"}
    elif task.cancelled():
        state = "cancelled"
        exception = None
    else:
        state = "running"
        exception = None
    
    # Stacktrace coroutine
    coro_stack = None
    try:
        if hasattr(task, '_coro'):
            coro = task._coro
            if hasattr(coro, 'cr_frame'):
                frame = coro.cr_frame
                if frame:
                    coro_stack = traceback.format_stack(frame)
        elif hasattr(task, 'get_stack'):
            coro_stack = task.get_stack()
    except Exception as e:
        coro_stack = f"failed_to_extract_stack: {type(e).__name__}: {e}"
    
    # Timestamps (если доступно)
    created_at = None
    last_yield = None
    try:
        if hasattr(task, '_created'):
            created_at = datetime.fromtimestamp(task._created, tz=UTC).isoformat()
    except Exception:
        pass
    
    return {
        "task_id": task_id,
        "task_name": task_name,
        "state": state,
        "exception": exception,
        "coroutine_stack": coro_stack,
        "created_at": created_at,
        "last_yield": last_yield,
        "incident_id": incident_id
    }


def log_task_dump(incident_id: str, context: str = "CRITICAL") -> None:
    """
    Логирование task dump
    
    Args:
        incident_id: ID инцидента
        context: Контекст dump (CRITICAL, SAFE_MODE, etc.)
    """
    dump = dump_all_tasks(incident_id)
    
    # Логируем как structured JSON
    logger.critical(
        f"TASK_DUMP_START incident_id={incident_id} context={context} "
        f"total_tasks={dump['total_tasks']}"
    )
    
    # Логируем каждый task отдельно (для читаемости)
    for task in dump["tasks"]:
        logger.critical(
            f"TASK_DUMP_TASK "
            f"incident_id={incident_id} "
            f"task_id={task['task_id']} "
            f"task_name={task['task_name']} "
            f"state={task['state']} "
            f"has_exception={task['exception'] is not None} "
            f"has_stack={task['coroutine_stack'] is not None}"
        )
    
    # Полный dump в JSON формате (для парсинга)
    dump_json = json.dumps(dump, indent=2, default=str)
    logger.critical(f"TASK_DUMP_FULL incident_id={incident_id} dump={dump_json}")


def get_stalled_tasks(threshold_seconds: float = 60.0) -> List[Dict[str, Any]]:
    """
    Найти stalled tasks (не yield'ят долго)
    
    Args:
        threshold_seconds: Порог для определения stall
    
    Returns:
        Список stalled tasks
    """
    # Это упрощённая версия - в реальности нужен более сложный механизм
    # для отслеживания last_yield каждого task
    tasks = asyncio.all_tasks()
    stalled = []
    
    for task in tasks:
        if task.done() or task.cancelled():
            continue
        
        # Проверяем, не заблокирован ли task
        # В реальности это требует более глубокого анализа
        stalled.append({
            "task_id": id(task),
            "task_name": task.get_name(),
            "reason": "potential_stall"
        })
    
    return stalled

