"""Tests for breadth-first execution-tree fetching (``tangle_cli.execution_tree``)."""

from __future__ import annotations

import threading
from typing import Any
from unittest.mock import MagicMock

import pytest

from tangle_cli import execution_tree as et
from tangle_cli.client import TangleApiClient
from tangle_cli.models import GetExecutionInfoResponse

TREE: dict[str, dict[str, Any]] = {
    "root": {"id": "root", "child_task_execution_ids": {"A": "a", "Sub": "sub", "Empty": None}},
    "a": {"id": "a"},
    "sub": {"id": "sub", "child_task_execution_ids": {"X": "x", "Y": "y"}},
    "x": {"id": "x"},
    "y": {"id": "y"},
}
LEVELS = [
    ["/api/executions/root/details"],
    ["/api/executions/a/details", "/api/executions/sub/details"],
    ["/api/executions/x/details", "/api/executions/y/details"],
]


class Client:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.threads: set[int] = set()
        self.prefetched: list[list[str]] = []
        self.persisted: list[list[str]] = []
        self._lock = threading.Lock()

    def executions_details(self, execution_id: str) -> dict[str, Any]:
        with self._lock:
            self.calls.append(execution_id)
            self.threads.add(threading.get_ident())
        return dict(TREE[execution_id])

    def prefetch(self, paths: list[str]) -> None:
        self.prefetched.append(list(paths))

    def persist(self, paths: list[str]) -> None:
        self.persisted.append(list(paths))


def test_breadth_first_with_names_parents_and_hooks():
    client = Client()

    tree = et.fetch_execution_tree(client, "root", max_workers=1)

    assert list(tree) == ["root", "a", "sub", "x", "y"]
    assert [(n.name, n.parent) for n in tree.values()] == [
        (None, None), ("A", "root"), ("Sub", "root"), ("X", "sub"), ("Y", "sub"),
    ]
    assert client.prefetched == LEVELS
    assert client.persisted == LEVELS
    assert sorted(client.calls) == sorted(TREE)  # each fetched once; the empty child id is skipped


def test_parallel_and_inline_fetching(monkeypatch):
    parallel = Client()
    et.fetch_execution_tree(parallel, "root", max_workers=4)
    assert sorted(parallel.calls) == sorted(TREE)

    monkeypatch.setattr(et.sys, "platform", "emscripten")  # no threads (e.g. Pyodide): never start any
    inline = Client()
    et.fetch_execution_tree(inline, "root", max_workers=8)
    assert inline.threads == {threading.get_ident()}


def test_custom_fetch_and_errors_propagate():
    client = Client()
    seen: list[str] = []

    def fetch(execution_id: str) -> dict[str, Any]:
        seen.append(execution_id)
        if execution_id == "x":
            raise RuntimeError("boom")
        return dict(TREE[execution_id])

    with pytest.raises(RuntimeError, match="boom"):
        et.fetch_execution_tree(client, "root", fetch=fetch, max_workers=1)
    assert client.calls == [] and "x" in seen


def test_child_execution_ids_reads_models_dicts_and_raw():
    class AttrOnly:
        raw = None
        child_task_execution_ids = {"B": "b", "A": "a"}

    class RawFirst:
        raw = {"child_task_execution_ids": {"Z": "z"}}
        child_task_execution_ids = {"ignored": "i"}

    assert et.child_execution_ids(AttrOnly()) == {"B": "b", "A": "a"}
    assert et.child_execution_ids(RawFirst()) == {"Z": "z"}
    assert et.child_execution_ids({"child_task_execution_ids": None}) == {}
    assert et.child_execution_ids({}) == {}


def _model_client(**kwargs: Any) -> TangleApiClient:
    client = TangleApiClient("https://api.test", **kwargs)
    client.executions_details = MagicMock(  # type: ignore[method-assign]
        side_effect=lambda eid: GetExecutionInfoResponse.from_dict(dict(TREE[eid]))
    )
    return client


def test_client_hooks_are_no_ops_by_default():
    client = TangleApiClient("https://api.test")

    assert client.prefetch(["/api/x"]) is None
    assert client.persist(["/api/x"]) is None


def test_get_execution_details_fetches_breadth_first_and_calls_hooks():
    client = _model_client()
    client.prefetch = MagicMock()  # type: ignore[method-assign]
    client.persist = MagicMock()  # type: ignore[method-assign]

    details = client.get_execution_details("root")

    assert sorted(call.args[0] for call in client.executions_details.call_args_list) == sorted(TREE)
    assert [call.args[0] for call in client.prefetch.call_args_list] == LEVELS
    assert [call.args[0] for call in client.persist.call_args_list] == LEVELS
    assert list(details.child_executions) == ["A", "Sub"]
    assert list(details.child_executions["Sub"].child_executions) == ["X", "Y"]


def test_get_execution_details_respects_max_workers(monkeypatch):
    client = _model_client()
    client.execution_tree_max_workers = 1
    seen: list[int] = []
    monkeypatch.setattr(et, "parallel_map", lambda fn, items, workers: seen.append(workers) or [fn(i) for i in items])

    client.get_execution_details("root")

    assert seen and set(seen) == {1}


def test_enrich_without_prefetched_tree_still_fetches_children():
    """Subclasses calling ``_enrich_execution_tree(execution)`` keep the per-node fetch."""

    client = _model_client()
    root = GetExecutionInfoResponse.from_dict(dict(TREE["root"]))

    client._enrich_execution_tree(root)

    assert sorted(call.args[0] for call in client.executions_details.call_args_list) == ["a", "sub", "x", "y"]
    assert list(root.child_executions) == ["A", "Sub"]
