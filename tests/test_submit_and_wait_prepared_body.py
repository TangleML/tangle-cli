from __future__ import annotations

import builtins
import copy
import json
from typing import Any

import pytest
from tangle_cli.logger import CaptureLogger
from tangle_cli.pipeline_run_manager import (
    PipelineRunError,
    PipelineRunManager,
    submit_and_wait_prepared_body,
)

RUN_ID = "run-1"
ROOT_EXECUTION_ID = "exec-1"

# The submit lifecycle injects a submission-id annotation into the (deep-copied)
# body before submit so post-failure recovery can find the created run. Strip it
# when comparing the submitted body to the caller's original.
_SUBMISSION_ANNOTATION_KEY = "tangle-cli/submission-id"


def _without_submission_annotation(body: dict[str, Any]) -> dict[str, Any]:
    stripped = copy.deepcopy(body)
    annotations = stripped.get("annotations")
    if isinstance(annotations, dict):
        annotations.pop(_SUBMISSION_ANNOTATION_KEY, None)
        if not annotations:
            stripped.pop("annotations", None)
    return stripped


def _prepared_body() -> dict[str, Any]:
    return {
        "root_task": {
            "componentRef": {
                "spec": {"name": "Prepared", "implementation": {"graph": {"tasks": {}}}}
            },
            "arguments": {"query": "value"},
        }
    }


class _SubmitClient:
    """Minimal API client: submit succeeds, status reports terminal SUCCEEDED."""

    def __init__(self) -> None:
        self.created: list[Any] = []
        self.get_calls: int = 0

    def pipeline_runs_create(self, body: Any = None) -> dict[str, Any]:
        self.created.append(copy.deepcopy(body))
        return {"id": RUN_ID, "root_execution_id": ROOT_EXECUTION_ID}

    def pipeline_runs_get(self, id: str, include_execution_stats: bool | None = None) -> dict[str, Any]:
        self.get_calls += 1
        return {
            "id": id,
            "root_execution_id": ROOT_EXECUTION_ID,
            "execution_summary": {"has_ended": True},
            "execution_status_stats": {"SUCCEEDED": 1},
        }


def _locator_body() -> dict[str, Any]:
    """A fully-formed submit body that references a component by name/digest.

    This shape has no inline ``componentRef.spec``; the helper must submit it
    verbatim instead of failing while trying to read a spec.
    """
    return {
        "root_task": {
            "componentRef": {"name": "my-pipeline", "digest": "sha256:abc123"},
            "arguments": {"query": "value"},
        }
    }


def test_submit_only_returns_run_metadata_without_wait() -> None:
    client = _SubmitClient()
    result = submit_and_wait_prepared_body(_prepared_body(), client=client, wait=False)

    assert result["run_id"] == RUN_ID
    assert result["root_execution_id"] == ROOT_EXECUTION_ID
    assert result["response"] == {"id": RUN_ID, "root_execution_id": ROOT_EXECUTION_ID}
    assert "wait" not in result
    assert client.get_calls == 0
    # No PipelineRunContext / attempts leak into the default output.
    assert set(result) == {"response", "run_id", "root_execution_id"}
    assert json.dumps(result)


def test_submit_and_wait_success_includes_serializable_wait_result() -> None:
    client = _SubmitClient()
    result = submit_and_wait_prepared_body(
        _prepared_body(), client=client, wait=True, poll_interval=0.01
    )

    assert result["run_id"] == RUN_ID
    assert result["wait"]["status"] == "SUCCEEDED"
    assert result["wait"]["timed_out"] is False
    assert client.get_calls >= 1
    assert json.dumps(result)


def test_timeout_metadata_preserved_and_serializable() -> None:
    class _NeverTerminalClient(_SubmitClient):
        def pipeline_runs_get(
            self, id: str, include_execution_stats: bool | None = None
        ) -> dict[str, Any]:
            self.get_calls += 1
            return {
                "id": id,
                "root_execution_id": ROOT_EXECUTION_ID,
                "execution_summary": {"has_ended": False},
                "execution_status_stats": {"RUNNING": 1},
            }

    result = submit_and_wait_prepared_body(
        _prepared_body(),
        client=_NeverTerminalClient(),
        wait=True,
        max_wait=0.0,
        poll_interval=0.01,
    )

    assert result["run_id"] == RUN_ID
    assert result["wait"]["timed_out"] is True
    assert "status_counts" in result["wait"]
    assert json.dumps(result)


def test_exit_on_first_failure_returns_serializable_early_exit() -> None:
    class _FailingGraphClient(_SubmitClient):
        # Run stays nonterminal at the top level; graph state reports a FAILED
        # child alongside a still-running one, so exit_on_first_failure trips.
        def pipeline_runs_get(
            self, id: str, include_execution_stats: bool | None = None
        ) -> dict[str, Any]:
            self.get_calls += 1
            return {
                "id": id,
                "root_execution_id": ROOT_EXECUTION_ID,
                "execution_status_stats": {"RUNNING": 1},
            }

        def executions_graph_execution_state(self, id: str) -> dict[str, Any]:
            return {"status_totals": {"RUNNING": 1, "FAILED": 1}}

    result = submit_and_wait_prepared_body(
        _prepared_body(),
        client=_FailingGraphClient(),
        wait=True,
        use_graph_state=True,
        exit_on_first_failure=True,
        poll_interval=0.01,
    )

    assert result["run_id"] == RUN_ID
    assert result["wait"]["early_exit"] is True
    assert result["wait"]["timed_out"] is False
    assert result["wait"]["failed_count"] == 1
    # Default output stays JSON-serializable and free of context/attempts leakage.
    assert "context" not in result
    assert "attempts" not in result
    assert json.dumps(result)


def test_invalid_poll_interval_raises_before_submit() -> None:
    client = _SubmitClient()
    with pytest.raises(PipelineRunError):
        submit_and_wait_prepared_body(
            _prepared_body(), client=client, wait=True, poll_interval=0
        )
    # The run must never be submitted when the wait params are invalid.
    assert client.created == []


def test_negative_max_wait_raises_before_submit() -> None:
    client = _SubmitClient()
    with pytest.raises(PipelineRunError):
        submit_and_wait_prepared_body(
            _prepared_body(), client=client, wait=True, max_wait=-1.0
        )
    assert client.created == []


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_max_wait_raises_before_submit(bad: float) -> None:
    # NaN/inf pass sign checks (NaN compares False, inf is "positive") and
    # would become a never-firing deadline; they must be rejected up front.
    client = _SubmitClient()
    with pytest.raises(PipelineRunError, match=r"max_wait \(--max-wait\) must be a finite number"):
        submit_and_wait_prepared_body(
            _prepared_body(), client=client, wait=True, max_wait=bad
        )
    assert client.created == []


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_poll_interval_raises_before_submit(bad: float) -> None:
    client = _SubmitClient()
    with pytest.raises(PipelineRunError, match=r"poll_interval \(--poll-interval\) must be a finite number"):
        submit_and_wait_prepared_body(
            _prepared_body(), client=client, wait=True, poll_interval=bad
        )
    assert client.created == []


def test_invalid_timeout_clock_raises_before_submit() -> None:
    client = _SubmitClient()
    with pytest.raises(PipelineRunError):
        submit_and_wait_prepared_body(
            _prepared_body(),
            client=client,
            wait=True,
            poll_interval=0.01,
            timeout_clock="bogus",
        )
    assert client.created == []


def test_wait_true_without_run_id_raises_instead_of_silent_no_wait() -> None:
    class _NoRunIdClient(_SubmitClient):
        def pipeline_runs_create(self, body: Any = None) -> dict[str, Any]:
            self.created.append(copy.deepcopy(body))
            return {"root_execution_id": ROOT_EXECUTION_ID}

    client = _NoRunIdClient()
    with pytest.raises(PipelineRunError, match="did not include a run id"):
        submit_and_wait_prepared_body(
            _prepared_body(), client=client, wait=True, poll_interval=0.01
        )
    # The run was submitted; only the wait was refused.
    assert len(client.created) == 1
    assert client.get_calls == 0

    # wait=False keeps the id-less response inspectable.
    result = submit_and_wait_prepared_body(_prepared_body(), client=client, wait=False)
    assert result["run_id"] is None
    assert "wait" not in result


@pytest.mark.parametrize(
    "body", [{"arguments": {}}, {"root_task": None}, {"root_task": "not-a-mapping"}]
)
def test_body_without_root_task_mapping_fails_before_submit(body: dict) -> None:
    client = _SubmitClient()
    with pytest.raises(PipelineRunError, match="root_task"):
        submit_and_wait_prepared_body(body, client=client, wait=False)
    assert client.created == []


@pytest.mark.parametrize(
    "root_task",
    [{"arguments": {}}, {"componentRef": None}, {"componentRef": ["not-a-mapping"]}],
)
def test_body_without_component_ref_mapping_fails_before_submit(root_task: dict) -> None:
    client = _SubmitClient()
    with pytest.raises(PipelineRunError, match="componentRef"):
        submit_and_wait_prepared_body(
            {"root_task": root_task}, client=client, wait=False
        )
    assert client.created == []


def test_caller_body_not_mutated() -> None:
    body = _prepared_body()
    original = copy.deepcopy(body)
    submit_and_wait_prepared_body(body, client=_SubmitClient(), wait=True, poll_interval=0.01)
    assert body == original


def test_manager_and_client_are_mutually_exclusive() -> None:
    manager = PipelineRunManager(client=_SubmitClient())
    with pytest.raises(PipelineRunError):
        submit_and_wait_prepared_body(
            _prepared_body(), manager=manager, client=_SubmitClient()
        )


def test_locator_body_without_inline_spec_submits_original_body() -> None:
    client = _SubmitClient()
    body = _locator_body()
    original = copy.deepcopy(body)

    result = submit_and_wait_prepared_body(body, client=client, wait=False)

    # The client receives the original locator body (modulo the submission-id
    # annotation the submit lifecycle injects for post-failure recovery).
    assert len(client.created) == 1
    assert _without_submission_annotation(client.created[0]) == original
    assert result["run_id"] == RUN_ID
    assert result["root_execution_id"] == ROOT_EXECUTION_ID
    assert body == original


def test_manager_submit_prepared_body_accepts_locator_body() -> None:
    """Regression for the shipped submit path (not just the new helper):
    ``PipelineRunManager.submit_prepared_body`` used to raise ``KeyError`` on a
    locator-style body with no inline ``componentRef.spec``; it must now submit
    the body verbatim with a spec-less run context."""

    client = _SubmitClient()
    manager = PipelineRunManager(client=client)
    body = _locator_body()
    original = copy.deepcopy(body)

    response = manager.submit_prepared_body(body)

    # submit_prepared_body normalizes/submits the caller's body directly (no
    # submission-id annotation injection, which lives in the run lifecycle).
    assert client.created == [original]
    assert response == {"id": RUN_ID, "root_execution_id": ROOT_EXECUTION_ID}
    assert body == original


def test_partial_native_package_raises_actionable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A partially-installed native package (missing ``tangle_api.generated``)
    should surface the actionable install hint, not a raw ModuleNotFoundError."""

    real_import = builtins.__import__

    def fake_import(
        name: str,
        globals: Any = None,
        locals: Any = None,
        fromlist: Any = (),
        level: int = 0,
    ) -> Any:
        # Intercept the lazy ``from .client import TangleApiClient`` and fail as
        # if a native submodule were missing rather than the top-level package.
        if level == 1 and name == "client" and fromlist and "TangleApiClient" in fromlist:
            raise ModuleNotFoundError(
                "No module named 'tangle_api.generated'", name="tangle_api.generated"
            )
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    # No manager/client -> the default manager path performs the native import.
    with pytest.raises(PipelineRunError, match="Native generated Tangle API bindings"):
        submit_and_wait_prepared_body(_prepared_body())


def test_explicit_manager_is_reused() -> None:
    client = _SubmitClient()
    manager = PipelineRunManager(client=client)
    result = submit_and_wait_prepared_body(
        _prepared_body(), manager=manager, wait=False
    )
    assert result["run_id"] == RUN_ID
    assert client.created  # the provided manager's client handled the submit


class _RecoveringClient(_SubmitClient):
    """Submit dies without a response although the run was actually created;
    the run is then discoverable via the submission-id list lookup."""

    def __init__(self) -> None:
        super().__init__()
        self.list_calls: list[dict[str, Any]] = []

    def pipeline_runs_create(self, body: Any = None) -> dict[str, Any]:
        self.created.append(copy.deepcopy(body))
        raise TimeoutError("submit connection dropped")

    def pipeline_runs_list(self, **kwargs: Any) -> dict[str, Any]:
        self.list_calls.append(kwargs)
        return {
            "pipeline_runs": [{"id": RUN_ID, "root_execution_id": ROOT_EXECUTION_ID}]
        }


def test_submit_failure_adopts_run_recovered_by_submission_id(monkeypatch) -> None:
    # Recovery finds the already-created run by the injected submission-id
    # annotation and adopts it instead of resubmitting a duplicate.
    monkeypatch.setattr("tangle_cli.pipeline_run_manager.time.sleep", lambda _delay: None)
    client = _RecoveringClient()

    result = submit_and_wait_prepared_body(_prepared_body(), client=client, wait=False)

    assert result["run_id"] == RUN_ID
    assert result["root_execution_id"] == ROOT_EXECUTION_ID
    # Exactly one submit was attempted; the run was adopted, not resubmitted.
    assert len(client.created) == 1
    submission_id = client.created[0]["annotations"][_SUBMISSION_ANNOTATION_KEY]
    assert len(client.list_calls) == 1
    assert submission_id in client.list_calls[0]["filter_query"]


def test_submit_recovery_attempts_zero_disables_lookup(monkeypatch) -> None:
    class _FailingClient(_SubmitClient):
        def __init__(self) -> None:
            super().__init__()
            self.list_calls: list[dict[str, Any]] = []

        def pipeline_runs_create(self, body: Any = None) -> dict[str, Any]:
            self.created.append(copy.deepcopy(body))
            raise TimeoutError("submit connection dropped")

        def pipeline_runs_list(self, **kwargs: Any) -> dict[str, Any]:
            self.list_calls.append(kwargs)
            return {"pipeline_runs": []}

    monkeypatch.setattr("tangle_cli.pipeline_run_manager.time.sleep", lambda _delay: None)
    client = _FailingClient()

    with pytest.raises(TimeoutError):
        submit_and_wait_prepared_body(
            _prepared_body(), client=client, wait=False, submit_recovery_attempts=0
        )

    assert client.list_calls == []


def test_logger_used_when_helper_builds_the_manager(monkeypatch) -> None:
    # The recovery path logs through the manager's logger, so it makes the
    # logger= wiring observable: with client=, the helper must hand the caller's
    # logger to the manager it builds.
    monkeypatch.setattr("tangle_cli.pipeline_run_manager.time.sleep", lambda _delay: None)
    logger = CaptureLogger()

    result = submit_and_wait_prepared_body(
        _prepared_body(), client=_RecoveringClient(), logger=logger, wait=False
    )

    assert result["run_id"] == RUN_ID
    assert "Recovered existing pipeline run" in (logger.get_logs() or "")


def test_logger_ignored_when_manager_is_supplied(monkeypatch) -> None:
    # Documented contract: a supplied manager keeps its own configured logger.
    monkeypatch.setattr("tangle_cli.pipeline_run_manager.time.sleep", lambda _delay: None)
    manager_logger = CaptureLogger()
    ignored_logger = CaptureLogger()
    manager = PipelineRunManager(client=_RecoveringClient(), logger=manager_logger)

    result = submit_and_wait_prepared_body(
        _prepared_body(), manager=manager, logger=ignored_logger, wait=False
    )

    assert result["run_id"] == RUN_ID
    assert "Recovered existing pipeline run" in (manager_logger.get_logs() or "")
    assert ignored_logger.get_logs() is None


def test_wait_with_max_wait_none_waits_without_deadline() -> None:
    client = _SubmitClient()

    result = submit_and_wait_prepared_body(
        _prepared_body(), client=client, wait=True, max_wait=None, poll_interval=0.01
    )

    assert result["wait"]["status"] == "SUCCEEDED"
    assert result["wait"]["timed_out"] is False
    assert client.get_calls >= 1
