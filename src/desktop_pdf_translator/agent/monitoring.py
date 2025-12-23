"""
Monitoring and analytics for agent decisions.
"""

import json
import logging
from pathlib import Path
from typing import List, Dict, Any
from datetime import datetime

from .models import AgentDecision

logger = logging.getLogger(__name__)


class AgentMonitor:
    """
    Monitors agent decisions and logs for analysis.
    Provides analytics and insights into agent behavior.
    """

    def __init__(self, log_path: str = "data/agent_decisions.jsonl"):
        """
        Initialize monitor.

        Args:
            log_path: Path to decision log file (JSONL format)
        """
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info(f"Agent monitor initialized (log: {log_path})")

    def log_decision(self, decision: AgentDecision):
        """
        Log an agent decision to file.

        Args:
            decision: AgentDecision to log
        """
        try:
            with open(self.log_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(decision.to_dict()) + '\n')
            logger.debug(f"Logged decision for query: {decision.query[:50]}...")
        except Exception as e:
            logger.error(f"Failed to log decision: {e}")

    def get_decision_stats(self, last_n: int = 100) -> Dict[str, Any]:
        """
        Get statistics about agent decisions.

        Args:
            last_n: Number of recent decisions to analyze

        Returns:
            Dictionary with statistics
        """
        try:
            decisions = self._load_decisions(last_n=last_n)

            if not decisions:
                return {
                    'total_decisions': 0,
                    'message': 'No decisions logged yet'
                }

            # Calculate stats
            total_decisions = len(decisions)

            strategy_counts = {}
            query_type_counts = {}
            tool_usage = {}
            total_time = 0
            total_estimated_time = 0

            for dec in decisions:
                # Strategy distribution
                strategy = dec.get('strategy', 'unknown')
                strategy_counts[strategy] = strategy_counts.get(strategy, 0) + 1

                # Query type distribution
                query_type = dec.get('query_type', 'unknown')
                query_type_counts[query_type] = query_type_counts.get(query_type, 0) + 1

                # Tool usage
                for tool in dec.get('tools_executed', []):
                    tool_usage[tool] = tool_usage.get(tool, 0) + 1

                # Execution time
                total_time += dec.get('execution_time', 0)
                total_estimated_time += dec.get('estimated_time', 0)

            avg_time = total_time / total_decisions
            avg_estimated_time = total_estimated_time / total_decisions

            return {
                'total_decisions': total_decisions,
                'strategy_distribution': strategy_counts,
                'query_type_distribution': query_type_counts,
                'tool_usage': tool_usage,
                'avg_execution_time': avg_time,
                'avg_estimated_time': avg_estimated_time,
                'estimation_accuracy': abs(avg_time - avg_estimated_time) / avg_time if avg_time > 0 else 0,
                'most_common_strategy': max(strategy_counts, key=strategy_counts.get) if strategy_counts else None,
                'most_common_query_type': max(query_type_counts, key=query_type_counts.get) if query_type_counts else None
            }

        except Exception as e:
            logger.error(f"Failed to calculate stats: {e}")
            return {'error': str(e)}

    def get_recent_decisions(self, n: int = 10) -> List[Dict[str, Any]]:
        """
        Get recent agent decisions.

        Args:
            n: Number of recent decisions to retrieve

        Returns:
            List of decision dictionaries
        """
        decisions = self._load_decisions(last_n=n)
        return decisions

    def _load_decisions(self, last_n: int = 100) -> List[Dict]:
        """
        Load decisions from log file.

        Args:
            last_n: Number of recent decisions to load

        Returns:
            List of decision dictionaries
        """
        decisions = []
        try:
            if self.log_path.exists():
                with open(self.log_path, 'r', encoding='utf-8') as f:
                    # Read all lines
                    lines = f.readlines()
                    # Get last N lines
                    recent_lines = lines[-last_n:] if len(lines) > last_n else lines
                    # Parse each line
                    for line in recent_lines:
                        try:
                            decisions.append(json.loads(line))
                        except json.JSONDecodeError as e:
                            logger.warning(f"Failed to parse decision line: {e}")
        except Exception as e:
            logger.error(f"Failed to load decisions: {e}")

        return decisions

    def clear_log(self):
        """Clear decision log file."""
        try:
            if self.log_path.exists():
                self.log_path.unlink()
                logger.info("Decision log cleared")
        except Exception as e:
            logger.error(f"Failed to clear log: {e}")

    def export_stats_report(self, output_path: str = "data/agent_stats_report.json"):
        """
        Export comprehensive stats report.

        Args:
            output_path: Path to save report
        """
        try:
            stats = self.get_decision_stats(last_n=1000)  # Last 1000 decisions
            recent = self.get_recent_decisions(n=20)  # Last 20 decisions

            report = {
                'generated_at': datetime.now().isoformat(),
                'statistics': stats,
                'recent_decisions': recent,
                'summary': {
                    'total_decisions': stats.get('total_decisions', 0),
                    'avg_execution_time': f"{stats.get('avg_execution_time', 0):.2f}s",
                    'most_used_strategy': stats.get('most_common_strategy'),
                    'most_common_query_type': stats.get('most_common_query_type')
                }
            }

            output_file = Path(output_path)
            output_file.parent.mkdir(parents=True, exist_ok=True)

            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(report, f, indent=2, ensure_ascii=False)

            logger.info(f"Stats report exported to: {output_path}")

        except Exception as e:
            logger.error(f"Failed to export stats report: {e}")
