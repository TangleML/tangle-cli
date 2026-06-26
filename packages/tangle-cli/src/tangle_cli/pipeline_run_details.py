"""Pipeline-run details and graph-state serialization helpers.

These helpers are native-free and keep provider-specific log enrichment out of
OSS.  Downstreams can call them with their authenticated API client and layer
Observe/GCP/Slack output through ``PipelineRunHooks.fetch_logs`` or wrappers.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any

from .handler import TangleCliHandler


class PipelineRunDetails(TangleCliHandler):
    """Resource manager for pipeline run details and graph-state output.

    Downstream packages can subclass this class or inject a lazy
    ``client_factory`` to supply authenticated clients without OSS importing
    provider-specific auth or SDK code.
    """

    def __init__(
        self,
        client: Any = None,
        *,
        client_factory: Any | None = None,
        logger: Any | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(client=client, client_factory=client_factory, logger=logger, **kwargs)

    @staticmethod
    def to_plain(value: Any) -> Any:
        """Convert generated/native objects into JSON-serializable values."""

        return _to_plain(value)

    def serialize_execution(self, execution: Any) -> dict[str, Any]:
        """Serialize an execution details object into a concise dict."""

        out: dict[str, Any] = {"id": _value(execution, "id", "")}
        task_spec = _value(execution, "task_spec")
        component_spec = _value(task_spec, "component_spec")
        if component_spec:
            out["component"] = _value(component_spec, "name", "unknown") or "unknown"
            try:
                from .component_inspector import ComponentInspector

                transparent, reason = ComponentInspector.transparency_check(component_spec)
                out["transparent"] = transparent
                out["transparency_reason"] = reason
            except Exception:
                pass
            description = _value(component_spec, "description")
            if description:
                out["description"] = description
            implementation = _value(component_spec, "implementation")
            if implementation:
                out["implementation"] = self.to_plain(implementation)
        arguments = _value(task_spec, "arguments")
        if arguments:
            out["arguments"] = self.to_plain(arguments)
        raw = _value(execution, "raw", {}) or {}
        for key in ("state", "created_at", "finished_at"):
            raw_value = _value(raw, key)
            if raw_value:
                out[key] = raw_value
        input_artifacts = _value(execution, "input_artifacts")
        if input_artifacts:
            out["input_artifacts"] = self.to_plain(input_artifacts)
        output_artifacts = _value(execution, "output_artifacts")
        if output_artifacts:
            out["output_artifacts"] = self.to_plain(output_artifacts)
        return out

    def serialize_run_details(self, details: Any) -> dict[str, Any]:
        """Convert ``RunDetails`` into a JSON-serializable dict."""

        if isinstance(details, dict):
            return self.to_plain(details)
        out: dict[str, Any] = {}
        run = details.run
        out["run"] = {
            "id": _value(run, "id"),
            "root_execution_id": _value(run, "root_execution_id"),
            "created_at": _value(run, "created_at"),
            "created_by": _value(run, "created_by"),
        }
        annotations = _value(run, "annotations")
        if annotations:
            out["run"]["annotations"] = self.to_plain(annotations)
        if details.execution:
            out["execution"] = self.serialize_execution(details.execution)
        if details.annotations:
            out["annotations"] = self.to_plain(details.annotations)
        if details.execution_state:
            out["execution_state"] = {
                "totals": self.to_plain(_value(details.execution_state, "status_totals")),
                "per_execution": self.to_plain(_value(details.execution_state, "child_execution_status_stats")),
            }
        return out

    def get_run_details_output(
        self,
        run_id: str,
        *,
        include_implementations: bool = False,
        include_annotations: bool = False,
        include_execution_state: bool = False,
        execution_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch run details and return serialized output."""

        kwargs: dict[str, Any] = {
            "include_annotations": include_annotations,
            "include_execution_state": include_execution_state,
        }
        if include_implementations:
            kwargs["include_implementations"] = include_implementations
        if execution_id is not None:
            kwargs["execution_id"] = execution_id
        details = self._require_client().get_run_details(run_id, **kwargs)
        return self.serialize_run_details(details)

    def fetch_graph_state_one(self, run_id: str) -> dict[str, Any]:
        """Fetch graph state for a pipeline run id or root execution id."""

        try:
            run = self._require_client().pipeline_runs_get(run_id)
        except Exception as exc:
            response = getattr(exc, "response", None)
            if getattr(response, "status_code", None) != 404:
                raise
            run = None
        root_execution_id = _value(run, "root_execution_id") if run else None
        root_execution_id = root_execution_id or run_id
        state = self._require_client().executions_graph_execution_state(root_execution_id)
        return {
            "run_id": run_id,
            "root_execution_id": root_execution_id,
            "status_totals": self.to_plain(_value(state, "status_totals")),
            "failed_execution_ids": self.to_plain(_value(state, "failed_execution_ids")),
            "per_execution": self.to_plain(_value(state, "per_execution")),
            "error": None,
        }

    def get_graph_state_output(
        self,
        run_ids: list[str],
        *,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Fetch lightweight graph state for one or more run/execution IDs."""

        results: list[dict[str, Any]] = []
        for run_id in run_ids:
            executor = ThreadPoolExecutor(max_workers=1)
            try:
                future = executor.submit(self.fetch_graph_state_one, run_id)
                try:
                    results.append(future.result(timeout=timeout))
                except FutureTimeoutError:
                    results.append(_error_result(run_id, f"timeout after {timeout}s"))
                except Exception as exc:
                    results.append(_error_result(run_id, str(exc)))
            finally:
                executor.shutdown(wait=False)
        return {"results": results}


def _value(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _to_plain(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "model_dump"):
        return value.model_dump(by_alias=True)
    if isinstance(value, dict):
        return {key: _to_plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_plain(item) for item in value]
    return value


def _error_result(run_id: str, message: str) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "root_execution_id": None,
        "status_totals": None,
        "failed_execution_ids": None,
        "per_execution": None,
        "error": message,
    }


__all__ = ["PipelineRunDetails"]
