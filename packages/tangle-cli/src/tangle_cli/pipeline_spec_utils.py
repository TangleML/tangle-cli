"""Shared helpers for traversing Tangle pipeline spec dictionaries."""

from __future__ import annotations

from typing import Any, Mapping


def _extract_task_output_refs(value: Any) -> set[str]:
    """Return task ids referenced by nested taskOutput argument values."""

    refs: set[str] = set()
    if isinstance(value, Mapping):
        task_output = value.get("taskOutput")
        if isinstance(task_output, Mapping) and isinstance(task_output.get("taskId"), str):
            refs.add(task_output["taskId"])
        for nested in value.values():
            refs.update(_extract_task_output_refs(nested))
    elif isinstance(value, list):
        for item in value:
            refs.update(_extract_task_output_refs(item))
    return refs
