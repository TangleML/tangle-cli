"""Handwritten extensions mixed into generated Tangle API models."""

from __future__ import annotations

from typing import cast


class GetGraphExecutionStateResponseExtensions:
    """Convenience properties for graph execution state responses."""

    @property
    def per_execution(self) -> dict[str, dict[str, int]]:
        return cast(
            dict[str, dict[str, int]],
            getattr(self, "child_execution_status_stats", None) or {},
        )

    @property
    def status_totals(self) -> dict[str, int]:
        totals: dict[str, int] = {}
        for status_counts in self.per_execution.values():
            for status, count in status_counts.items():
                totals[status] = totals.get(status, 0) + count
        return totals

    @property
    def failed_execution_ids(self) -> list[str]:
        return [
            execution_id
            for execution_id, status_counts in self.per_execution.items()
            if status_counts.get("FAILED", 0) > 0
            or status_counts.get("SYSTEM_ERROR", 0) > 0
        ]


MODEL_EXTENSIONS = {
    "GetGraphExecutionStateResponse": "GetGraphExecutionStateResponseExtensions",
}
