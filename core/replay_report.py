"""
Replay Report - генерация отчётов по результатам replay.

Агрегирует результаты replay и генерирует отчёты для анализа.
"""
from typing import List, Dict
from datetime import datetime
from core.replay_models import ReplayResult, ReplayReport, DecisionType


class ReplayReporter:
    """
    Генератор отчётов по результатам replay.
    
    Агрегирует результаты и создаёт отчёты для анализа расхождений.
    """
    
    def __init__(self):
        """Инициализация ReplayReporter"""
        pass
    
    def generate_report(self, results: List[ReplayResult]) -> ReplayReport:
        """
        Генерирует агрегированный отчёт по результатам replay.
        
        Args:
            results: Список результатов replay
        
        Returns:
            ReplayReport: Агрегированный отчёт
        """
        total = len(results)
        changed = sum(1 for r in results if r.is_changed())
        unchanged = total - changed
        
        # Breakdown по причинам изменений
        meta_decision_changes = 0
        portfolio_changes = 0
        position_sizing_changes = 0
        risk_changes = 0
        size_changes = 0
        
        # Breakdown по типам решений
        decision_type_changes: Dict[str, int] = {}
        
        changed_results = []
        
        for result in results:
            if result.is_changed():
                changed_results.append(result)
                
                # Анализируем причины изменений
                if result.replayed_decision.decision_source == "MetaDecisionBrain":
                    meta_decision_changes += 1
                
                if result.replayed_decision.decision_source == "PortfolioBrain":
                    portfolio_changes += 1
                
                if result.replayed_decision.decision_source == "PositionSizer":
                    position_sizing_changes += 1
                
                if result.diff.block_level_changed:
                    risk_changes += 1
                
                if result.diff.position_size_changed:
                    size_changes += 1
                
                # Тип изменения решения
                change_key = f"{result.original_decision.decision_type.value}→{result.replayed_decision.decision_type.value}"
                decision_type_changes[change_key] = decision_type_changes.get(change_key, 0) + 1
        
        return ReplayReport(
            total_snapshots=total,
            changed_decisions=changed,
            unchanged_decisions=unchanged,
            change_rate=changed / total if total > 0 else 0.0,
            meta_decision_changes=meta_decision_changes,
            portfolio_changes=portfolio_changes,
            position_sizing_changes=position_sizing_changes,
            risk_changes=risk_changes,
            size_changes=size_changes,
            decision_type_changes=decision_type_changes,
            changed_results=changed_results
        )
    
    def format_report(self, report: ReplayReport) -> str:
        """
        Форматирует отчёт в читаемый текст.
        
        Args:
            report: ReplayReport для форматирования
        
        Returns:
            str: Отформатированный отчёт
        """
        lines = []
        lines.append("=" * 60)
        lines.append("REPLAY REPORT")
        lines.append("=" * 60)
        lines.append("")
        
        # Общая статистика
        lines.append(f"Total snapshots: {report.total_snapshots}")
        lines.append(f"Changed decisions: {report.changed_decisions}")
        lines.append(f"Unchanged decisions: {report.unchanged_decisions}")
        lines.append(f"Change rate: {report.change_rate * 100:.1f}%")
        lines.append("")
        
        # Breakdown по причинам
        lines.append("Breakdown by source:")
        lines.append(f"  MetaDecisionBrain: {report.meta_decision_changes}")
        lines.append(f"  PortfolioBrain: {report.portfolio_changes}")
        lines.append(f"  PositionSizer: {report.position_sizing_changes}")
        lines.append(f"  Risk changes: {report.risk_changes}")
        lines.append(f"  Size changes: {report.size_changes}")
        lines.append("")
        
        # Breakdown по типам решений
        if report.decision_type_changes:
            lines.append("Decision type changes:")
            for change_type, count in sorted(report.decision_type_changes.items(), key=lambda x: x[1], reverse=True):
                lines.append(f"  {change_type}: {count}")
            lines.append("")
        
        # Детали изменений
        if report.changed_results:
            lines.append("Changed decisions (first 10):")
            for i, result in enumerate(report.changed_results[:10], 1):
                lines.append(f"  {i}. Snapshot #{result.snapshot_id} ({result.symbol}):")
                lines.append(f"     {result.diff.diff_summary}")
            if len(report.changed_results) > 10:
                lines.append(f"  ... and {len(report.changed_results) - 10} more")
        
        lines.append("")
        lines.append("=" * 60)
        
        return "\n".join(lines)
    
    def export_to_dict(self, report: ReplayReport) -> Dict:
        """
        Экспортирует отчёт в словарь (для JSON/CSV).
        
        Args:
            report: ReplayReport для экспорта
        
        Returns:
            Dict: Отчёт в виде словаря
        """
        return {
            "total_snapshots": report.total_snapshots,
            "changed_decisions": report.changed_decisions,
            "unchanged_decisions": report.unchanged_decisions,
            "change_rate": report.change_rate,
            "meta_decision_changes": report.meta_decision_changes,
            "portfolio_changes": report.portfolio_changes,
            "position_sizing_changes": report.position_sizing_changes,
            "risk_changes": report.risk_changes,
            "size_changes": report.size_changes,
            "decision_type_changes": report.decision_type_changes,
            "changed_results_count": len(report.changed_results)
        }

