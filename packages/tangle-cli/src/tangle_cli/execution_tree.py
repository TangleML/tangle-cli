"""Breadth-first, batched fetching of a run's execution tree.

:func:`fetch_execution_tree` fetches ``/api/executions/{id}/details`` for an
execution and everything under it, one batch per tree level:

* on CPython each batch is fetched with a small thread pool; without threads
  (e.g. Pyodide / ``sys.platform == "emscripten"``) or with ``max_workers=1``
  it runs inline;
* the client's optional ``prefetch(paths)`` / ``persist(paths)`` hooks (see
  :meth:`tangle_cli.client.TangleApiClient.prefetch`) are called with each
  batch's relative API paths: ``prefetch`` before the batch is read, ``persist``
  after it (an execution's details never change).

:meth:`tangle_cli.client.TangleApiClient.get_execution_details` uses it, and
downstream tools (e.g. run timelines) can reuse it directly.

Importing this module starts no threads.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

DEFAULT_MAX_WORKERS = 8


def parallel_map(fn: Callable[[Any], Any], items: list[Any], max_workers: int) -> list[Any]:
    """Map *fn* over *items* preserving order; inline without threads or for 1 worker.

    Exceptions raised by *fn* propagate to the caller.
    """

    if max_workers <= 1 or len(items) <= 1 or sys.platform == "emscripten":
        return [fn(item) for item in items]
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=min(max_workers, len(items))) as pool:
        return list(pool.map(fn, items))


def call_client_hook(client: Any, hook: str, paths: list[str]) -> None:
    """Call the client's optional ``prefetch``/``persist`` hook with relative API paths."""

    function = getattr(client, hook, None)
    if callable(function) and paths:
        function(paths)


def details_path(execution_id: str) -> str:
    """Relative API path of one execution's details (the path ``executions_details`` requests)."""

    return f"/api/executions/{execution_id}/details"


def child_execution_ids(details: Any) -> dict[str, str]:
    """``{task name: child execution id}`` of one details response, in task order.

    Accepts a dict or a response model; reads the model's ``raw`` payload first,
    like ``TangleApiClient._enrich_execution_tree``. Empty child ids are skipped.
    """

    if isinstance(details, dict):
        children = details.get("child_task_execution_ids")
    else:
        raw = getattr(details, "raw", None)
        children = raw.get("child_task_execution_ids") if isinstance(raw, dict) else None
        if children is None:
            children = getattr(details, "child_task_execution_ids", None)
    if not isinstance(children, dict):
        return {}
    return {str(name): str(child) for name, child in children.items() if child}


@dataclass
class TreeNode:
    """One fetched execution and its place in the tree."""

    execution_id: str
    name: str | None  # task name in the parent graph; None for the root
    parent: str | None
    details: Any  # the ``executions_details`` response (model or dict)


def fetch_execution_tree(
    client: Any,
    root_execution_id: str,
    *,
    fetch: Callable[[str], Any] | None = None,
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> dict[str, TreeNode]:
    """Fetch every execution under (and including) *root_execution_id*, breadth-first.

    Args:
        client: Supplies ``executions_details`` and the optional
            ``prefetch``/``persist`` hooks.
        root_execution_id: The tree (or subtree) root.
        fetch: Override for fetching one execution's details (defaults to
            ``client.executions_details``), e.g. to count or wrap calls.
        max_workers: Parallel requests per level (inline without threads).

    Returns:
        ``{execution_id: TreeNode}`` in breadth-first order, children in task
        order. Each execution is fetched once. Exceptions from a fetch propagate.
    """

    fetch_one = fetch or client.executions_details
    nodes: dict[str, TreeNode] = {}
    frontier: list[tuple[str, str | None, str | None]] = [(root_execution_id, None, None)]
    while frontier:
        frontier = [entry for entry in frontier if entry[0] not in nodes]
        ids = [execution_id for execution_id, _, _ in frontier]
        if not ids:
            break
        call_client_hook(client, "prefetch", [details_path(execution_id) for execution_id in ids])
        responses = parallel_map(fetch_one, ids, max_workers)
        next_frontier: list[tuple[str, str | None, str | None]] = []
        for (execution_id, name, parent), details in zip(frontier, responses):
            nodes[execution_id] = TreeNode(execution_id=execution_id, name=name, parent=parent, details=details)
            next_frontier.extend(
                (child, child_name, execution_id) for child_name, child in child_execution_ids(details).items()
            )
        call_client_hook(client, "persist", [details_path(execution_id) for execution_id in ids])
        frontier = next_frontier
    return nodes


__all__ = [
    "DEFAULT_MAX_WORKERS",
    "TreeNode",
    "call_client_hook",
    "child_execution_ids",
    "details_path",
    "fetch_execution_tree",
    "parallel_map",
]
