"""
Runtime Safety Observer (RSO) v1.0

Внешний read-only наблюдатель для торговой системы.
Только чтение данных, без контроля и модификаций.

ABSOLUTE RULES:
- READ-ONLY: No writes, no control, no admin actions
- NO VERDICTS: Do not compute PASS/FAIL
- EXTERNAL PROCESS: Must run as external process
- STRICT SCHEMAS: Output must follow JSON/MD schemas exactly
"""

import json
import sys
import os
from datetime import datetime, UTC
from pathlib import Path
from typing import Dict, List, Optional, Any
import logging

# Настройка логирования для RSO (только для отладки, не для вывода)
logging.basicConfig(
    level=logging.WARNING,
    format='%(asctime)s [RSO] %(levelname)s: %(message)s'
)
logger = logging.getLogger(__name__)


class RSOReader:
    """
    Read-only читатель данных торговой системы.
    
    Все методы только читают данные, никогда не модифицируют.
    """
    
    def __init__(self, project_root: Optional[str] = None):
        """
        Инициализация RSO.
        
        Args:
            project_root: Корневая директория проекта (по умолчанию текущая)
        """
        if project_root is None:
            project_root = os.path.dirname(os.path.abspath(__file__))
        self.project_root = Path(project_root)
        self._sys_path_added = False
    
    def _ensure_sys_path(self):
        """Добавляет project_root в sys.path для импорта модулей (только чтение)"""
        if not self._sys_path_added:
            sys.path.insert(0, str(self.project_root))
            self._sys_path_added = True
    
    def read_fsm_state(self) -> Optional[Dict[str, Any]]:
        """
        Читает текущее состояние FSM (State Machine).
        
        Returns:
            Dict с информацией о состоянии или None если недоступно
        """
        try:
            self._ensure_sys_path()
            from system_state_machine import get_state_machine
            
            state_machine = get_state_machine()
            if state_machine is None:
                return None
            
            # Используем get_state_info() для чтения состояния
            state_info = state_machine.get_state_info()
            
            # Читаем переходы (только чтение)
            transitions = []
            if hasattr(state_machine, '_transitions'):
                for trans in state_machine._transitions:
                    transitions.append({
                        "from_state": trans.from_state.value,
                        "to_state": trans.to_state.value,
                        "reason": trans.reason,
                        "timestamp": trans.timestamp.isoformat(),
                        "incident_id": trans.incident_id,
                        "owner": trans.owner,
                        "metadata": trans.metadata
                    })
            
            return {
                "current_state": state_info.get("state"),
                "duration_in_state": state_info.get("duration_in_state"),
                "consecutive_errors": state_info.get("consecutive_errors"),
                "recovery_cycles": state_info.get("recovery_cycles"),
                "safe_mode_entered_at": state_info.get("safe_mode_entered_at"),
                "last_heartbeat": state_info.get("last_heartbeat"),
                "transitions_count": state_info.get("transitions_count"),
                "last_transition": state_info.get("last_transition"),
                "all_transitions": transitions[-50:] if transitions else []  # Последние 50 переходов
            }
        except Exception as e:
            logger.warning(f"Failed to read FSM state: {e}")
            return None
    
    def read_system_state(self) -> Optional[Dict[str, Any]]:
        """
        Читает состояние системы (SystemState).
        
        Returns:
            Dict с информацией о состоянии системы или None если недоступно
        """
        try:
            self._ensure_sys_path()
            from system_state import get_system_state
            
            system_state = get_system_state()
            if system_state is None:
                return None
            
            # Используем to_dict() для чтения состояния
            state_dict = system_state.to_dict()
            
            # Добавляем дополнительную информацию (только чтение)
            return {
                "timestamp": datetime.now(UTC).isoformat(),
                "system_health": {
                    "is_running": system_state.system_health.is_running,
                    "safe_mode": system_state.system_health.safe_mode,
                    "trading_paused": system_state.system_health.trading_paused,
                    "last_heartbeat": system_state.system_health.last_heartbeat.isoformat() if system_state.system_health.last_heartbeat else None,
                    "consecutive_errors": system_state.system_health.consecutive_errors
                },
                "performance_metrics": {
                    "total_cycles": system_state.performance_metrics.total_cycles,
                    "successful_cycles": system_state.performance_metrics.successful_cycles,
                    "errors": system_state.performance_metrics.errors,
                    "last_error": system_state.performance_metrics.last_error
                },
                "trading_decision": {
                    "can_trade": system_state.can_trade,
                    "last_decision_time": system_state.last_decision_time.isoformat() if system_state.last_decision_time else None
                },
                "market_state": state_dict.get("market_regime"),
                "risk_state": state_dict.get("risk_state"),
                "cognitive_state": state_dict.get("cognitive_state"),
                "opportunities_count": state_dict.get("opportunities"),
                "open_positions_count": state_dict.get("open_positions"),
                "recent_signals_count": state_dict.get("recent_signals")
            }
        except Exception as e:
            logger.warning(f"Failed to read system state: {e}")
            return None
    
    def read_structured_logs(self, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Читает структурированные логи из файла runner.log.
        
        Args:
            limit: Максимальное количество записей для чтения
        
        Returns:
            List словарей с записями логов
        """
        logs = []
        log_file = self.project_root / "runner.log"
        
        if not log_file.exists():
            return logs
        
        try:
            with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
                
            # Читаем последние limit строк
            for line in lines[-limit:]:
                line = line.strip()
                if not line:
                    continue
                
                # Парсим структурированные логи (формат: timestamp [LEVEL] message)
                log_entry = {
                    "raw": line,
                    "timestamp": None,
                    "level": None,
                    "message": None
                }
                
                # Пытаемся извлечь timestamp и level
                parts = line.split(' ', 3)
                if len(parts) >= 3:
                    try:
                        # Пробуем распарсить timestamp
                        timestamp_str = parts[0] + ' ' + parts[1]
                        log_entry["timestamp"] = timestamp_str
                        log_entry["message"] = parts[3] if len(parts) > 3 else line
                        
                        # Ищем уровень логирования
                        if '[CRITICAL]' in line or 'CRITICAL' in line:
                            log_entry["level"] = "CRITICAL"
                        elif '[ERROR]' in line or 'ERROR' in line:
                            log_entry["level"] = "ERROR"
                        elif '[WARNING]' in line or 'WARNING' in line:
                            log_entry["level"] = "WARNING"
                        elif '[INFO]' in line or 'INFO' in line:
                            log_entry["level"] = "INFO"
                        elif '[DEBUG]' in line or 'DEBUG' in line:
                            log_entry["level"] = "DEBUG"
                        else:
                            log_entry["level"] = "UNKNOWN"
                    except Exception:
                        log_entry["message"] = line
                
                logs.append(log_entry)
        
        except Exception as e:
            logger.warning(f"Failed to read logs: {e}")
        
        return logs
    
    def read_fsm_transitions(self, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Читает переходы FSM (State Machine transitions).
        
        Args:
            limit: Максимальное количество переходов для чтения
        
        Returns:
            List словарей с переходами состояний
        """
        try:
            self._ensure_sys_path()
            from system_state_machine import get_state_machine
            
            state_machine = get_state_machine()
            if state_machine is None:
                return []
            
            transitions = []
            if hasattr(state_machine, '_transitions'):
                for trans in state_machine._transitions[-limit:]:
                    transitions.append({
                        "from_state": trans.from_state.value,
                        "to_state": trans.to_state.value,
                        "reason": trans.reason,
                        "timestamp": trans.timestamp.isoformat(),
                        "incident_id": trans.incident_id,
                        "owner": trans.owner,
                        "metadata": trans.metadata
                    })
            
            return transitions
        except Exception as e:
            logger.warning(f"Failed to read FSM transitions: {e}")
            return []


class RSOOutput:
    """
    Генератор вывода RSO в форматах JSON и Markdown.
    
    Строго следует предоставленным схемам.
    """
    
    @staticmethod
    def generate_json_report(
        fsm_state: Optional[Dict],
        system_state: Optional[Dict],
        logs: List[Dict],
        transitions: List[Dict]
    ) -> Dict[str, Any]:
        """
        Генерирует JSON отчет согласно схеме.
        
        Args:
            fsm_state: Состояние FSM
            system_state: Состояние системы
            logs: Логи
            transitions: Переходы FSM
        
        Returns:
            Dict с JSON структурой отчета
        """
        return {
            "rso_version": "1.0",
            "timestamp": datetime.now(UTC).isoformat(),
            "observation_type": "read_only",
            "fsm_state": fsm_state,
            "system_state": system_state,
            "recent_logs": logs[-50:] if logs else [],  # Последние 50 логов
            "fsm_transitions": transitions[-50:] if transitions else [],  # Последние 50 переходов
            "metadata": {
                "observer_mode": "external",
                "read_only": True,
                "no_verdict": True
            }
        }
    
    @staticmethod
    def generate_markdown_report(
        fsm_state: Optional[Dict],
        system_state: Optional[Dict],
        logs: List[Dict],
        transitions: List[Dict]
    ) -> str:
        """
        Генерирует Markdown отчет согласно схеме.
        
        Args:
            fsm_state: Состояние FSM
            system_state: Состояние системы
            logs: Логи
            transitions: Переходы FSM
        
        Returns:
            Строка с Markdown отчетом
        """
        md = []
        md.append("# Runtime Safety Observer (RSO) v1.0 Report")
        md.append("")
        md.append(f"**Observation Time:** {datetime.now(UTC).isoformat()}")
        md.append(f"**Observer Mode:** External (Read-Only)")
        md.append("")
        
        # FSM State
        md.append("## FSM State")
        if fsm_state:
            md.append(f"- **Current State:** `{fsm_state.get('current_state', 'UNKNOWN')}`")
            duration = fsm_state.get('duration_in_state')
            if duration is not None:
                md.append(f"- **Duration in State:** `{duration:.1f}s`")
            md.append(f"- **Consecutive Errors:** `{fsm_state.get('consecutive_errors', 0)}`")
            md.append(f"- **Recovery Cycles:** `{fsm_state.get('recovery_cycles', 0)}`")
            safe_mode_at = fsm_state.get('safe_mode_entered_at')
            if safe_mode_at:
                md.append(f"- **Safe Mode Entered At:** `{safe_mode_at}`")
            last_transition = fsm_state.get('last_transition')
            if last_transition:
                md.append(f"- **Last Transition:** `{last_transition.get('from')}` → `{last_transition.get('to')}`")
                md.append(f"  - Reason: `{last_transition.get('reason', 'N/A')}`")
                md.append(f"  - Incident ID: `{last_transition.get('incident_id', 'N/A')}`")
        else:
            md.append("- **Status:** FSM state unavailable")
        md.append("")
        
        # System State
        md.append("## System State")
        if system_state:
            health = system_state.get('system_health', {})
            md.append("### System Health")
            md.append(f"- **Is Running:** `{health.get('is_running', False)}`")
            md.append(f"- **Safe Mode:** `{health.get('safe_mode', False)}`")
            md.append(f"- **Trading Paused:** `{health.get('trading_paused', False)}`")
            md.append(f"- **Consecutive Errors:** `{health.get('consecutive_errors', 0)}`")
            
            perf = system_state.get('performance_metrics', {})
            md.append("### Performance Metrics")
            md.append(f"- **Total Cycles:** `{perf.get('total_cycles', 0)}`")
            md.append(f"- **Successful Cycles:** `{perf.get('successful_cycles', 0)}`")
            md.append(f"- **Errors:** `{perf.get('errors', 0)}`")
            
            trading = system_state.get('trading_decision', {})
            md.append("### Trading Decision")
            md.append(f"- **Can Trade:** `{trading.get('can_trade', False)}`")
        else:
            md.append("- **Status:** System state unavailable")
        md.append("")
        
        # FSM Transitions
        md.append("## FSM Transitions")
        if transitions:
            md.append(f"**Total Transitions:** {len(transitions)}")
            md.append("")
            md.append("| From | To | Reason | Timestamp | Incident ID | Owner |")
            md.append("|------|----|----|-----------|-------------|-------|")
            for trans in transitions[-20:]:  # Последние 20 переходов
                from_state = trans.get('from_state', 'N/A')
                to_state = trans.get('to_state', 'N/A')
                reason = trans.get('reason', 'N/A')[:50]  # Ограничиваем длину
                timestamp = trans.get('timestamp', 'N/A')[:19]  # Без микросекунд
                incident_id = trans.get('incident_id', 'N/A')[:12]  # Первые 12 символов
                owner = trans.get('owner', 'N/A')
                md.append(f"| `{from_state}` | `{to_state}` | `{reason}` | `{timestamp}` | `{incident_id}` | `{owner}` |")
        else:
            md.append("- **Status:** No transitions available")
        md.append("")
        
        # Recent Logs
        md.append("## Recent Logs")
        if logs:
            md.append(f"**Total Log Entries:** {len(logs)}")
            md.append("")
            md.append("| Timestamp | Level | Message |")
            md.append("|-----------|-------|---------|")
            for log in logs[-20:]:  # Последние 20 логов
                timestamp = log.get('timestamp', 'N/A')[:19] if log.get('timestamp') else 'N/A'
                level = log.get('level', 'UNKNOWN')
                message = log.get('message', log.get('raw', 'N/A'))[:100]  # Ограничиваем длину
                md.append(f"| `{timestamp}` | `{level}` | `{message}` |")
        else:
            md.append("- **Status:** No logs available")
        md.append("")
        
        md.append("---")
        md.append("")
        md.append("**Note:** This is a read-only observation. No verdicts are computed.")
        md.append("**Observer:** External process, no control actions performed.")
        
        return "\n".join(md)


def main():
    """
    Главная функция RSO.
    
    Читает данные и выводит отчеты в JSON и Markdown форматах.
    """
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Runtime Safety Observer (RSO) v1.0 - External Read-Only Observer"
    )
    parser.add_argument(
        '--project-root',
        type=str,
        default=None,
        help='Root directory of the trading system project'
    )
    parser.add_argument(
        '--output-dir',
        type=str,
        default=None,
        help='Output directory for reports (default: project root)'
    )
    parser.add_argument(
        '--json-only',
        action='store_true',
        help='Output only JSON report'
    )
    parser.add_argument(
        '--md-only',
        action='store_true',
        help='Output only Markdown report'
    )
    
    args = parser.parse_args()
    
    # Определяем директории
    project_root = args.project_root or os.path.dirname(os.path.abspath(__file__))
    output_dir = Path(args.output_dir) if args.output_dir else Path(project_root)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Создаем читатель
    reader = RSOReader(project_root)
    
    # Читаем данные (только чтение)
    fsm_state = reader.read_fsm_state()
    system_state = reader.read_system_state()
    logs = reader.read_structured_logs(limit=100)
    transitions = reader.read_fsm_transitions(limit=50)
    
    # Генерируем отчеты
    json_report = RSOOutput.generate_json_report(fsm_state, system_state, logs, transitions)
    md_report = RSOOutput.generate_markdown_report(fsm_state, system_state, logs, transitions)
    
    # Выводим отчеты
    timestamp_str = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    
    if not args.md_only:
        json_file = output_dir / f"rso_report_{timestamp_str}.json"
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(json_report, f, indent=2, ensure_ascii=False)
        print(f"JSON report written to: {json_file}")
    
    if not args.json_only:
        md_file = output_dir / f"rso_report_{timestamp_str}.md"
        with open(md_file, 'w', encoding='utf-8') as f:
            f.write(md_report)
        print(f"Markdown report written to: {md_file}")
    
    # Выводим краткую информацию в stdout
    print("\n=== RSO Observation Summary ===")
    print(f"FSM State: {fsm_state.get('current_state') if fsm_state else 'UNAVAILABLE'}")
    print(f"System State: {'AVAILABLE' if system_state else 'UNAVAILABLE'}")
    print(f"Logs Read: {len(logs)}")
    print(f"Transitions Read: {len(transitions)}")
    print("\nNote: This is a read-only observation. No verdicts computed.")


if __name__ == "__main__":
    main()

