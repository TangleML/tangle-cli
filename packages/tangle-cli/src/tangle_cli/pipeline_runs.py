"""Generic pipeline-run helpers for `tangle sdk pipeline-runs`.

This module ports the OSS-safe parts of tangle-deploy's runner/run details
commands while keeping downstream-specific behavior behind hooks.  The default
implementation uses only the public Tangle API and local files; Shopify/GCP,
Slack, scheduler, mutex, run-as annotation defaults, and alternate log backends
are intentionally extension points rather than OSS behavior.
"""

from __future__ import annotations

import copy
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml

from .logger import Logger, get_default_logger
from .pipeline_hydrator import HydrationError, PipelineHydrator
from .utils import dump_yaml

_TERMINAL_STATUSES = ("FAILED", "SYSTEM_ERROR", "CANCELLED", "CANCELED", "SKIPPED", "SUCCEEDED")
_ACTIVE_STATUSES = ("RUNNING", "CANCELLING", "CANCELING", "PENDING", "QUEUED")


class PipelineRunError(RuntimeError):
    """Raised when a pipeline-run operation cannot complete."""


class UnsupportedPipelineRunFeatureError(PipelineRunError):
    """Raised for TD extension points intentionally unsupported in OSS defaults."""


@dataclass
class PipelineRunHooks:
    """Overridable seams for downstream tangle-deploy behavior.

    Subclasses can override these methods to add Shopify auth wrappers, gs://
    loading, JOB_CONFIG time input, run-as annotations, mutex/schedule behavior,
    graceful shutdown, Slack notifications, Observe/GCP logs, or from-container
    runtime defaults without forking the generic pipeline-run manager.
    """

    logger: Logger = field(default_factory=get_default_logger)
    trusted_python_sources: list[str] = field(default_factory=list)
    allow_all_hydration: bool = False

    def read_pipeline_yaml(self, pipeline_path: str | Path) -> dict[str, Any]:
        path_text = str(pipeline_path)
        if path_text.startswith("gs://"):
            raise UnsupportedPipelineRunFeatureError(
                "gs:// pipeline loading is not supported by the OSS CLI default hooks"
            )
        path = Path(pipeline_path)
        with path.open(encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
        if not isinstance(data, dict):
            raise PipelineRunError("Pipeline YAML must contain a top-level mapping")
        return data

    def hydrate_pipeline(
        self,
        pipeline_path: str | Path,
        *,
        client: Any,
        resolution_overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        hydrator = PipelineHydrator(
            client=client,
            resolution_overrides=resolution_overrides,
            logger=self.logger,
            trusted_python_sources=self.trusted_python_sources,
            allow_all_hydration=self.allow_all_hydration,
        )
        try:
            return hydrator.hydrate_file(pipeline_path).data
        except HydrationError as exc:
            raise PipelineRunError(str(exc)) from exc

    def prepare_run_arguments(
        self,
        pipeline_spec: dict[str, Any],
        run_args: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """Hook for TD JOB_CONFIG time input / scheduled runtime behavior."""
        return run_args

    def extra_submit_annotations(
        self,
        *,
        pipeline_spec: dict[str, Any],
        pipeline_path: str | Path | None,
        run_as: str | None = None,
    ) -> dict[str, str]:
        """Hook for downstream source/run-as/git annotations."""
        if run_as:
            raise UnsupportedPipelineRunFeatureError(
                "--run-as is a downstream extension point and has no OSS default behavior"
            )
        return {}

    def before_submit(self, pipeline_spec: dict[str, Any]) -> None:
        """Hook for TD mutex/overlap checks."""

    def after_submit(self, response: Mapping[str, Any]) -> None:
        """Hook for downstream start notifications."""

    def after_wait(self, result: Mapping[str, Any]) -> None:
        """Hook for downstream success/failure notifications."""

    def fetch_logs(self, client: Any, execution_id: str) -> Any:
        """Hook for alternate TD log providers; OSS uses the Tangle API only."""
        return client.executions_container_log(execution_id)


@dataclass
class PipelineRunManager:
    client: Any
    hooks: PipelineRunHooks = field(default_factory=PipelineRunHooks)
    logger: Logger = field(default_factory=get_default_logger)

    @staticmethod
    def to_plain(value: Any) -> Any:
        if hasattr(value, "to_dict"):
            return value.to_dict()
        if hasattr(value, "model_dump"):
            return value.model_dump(by_alias=True)
        if isinstance(value, dict):
            return {key: PipelineRunManager.to_plain(val) for key, val in value.items()}
        if isinstance(value, list):
            return [PipelineRunManager.to_plain(item) for item in value]
        return value

    @staticmethod
    def extract_default_arguments(pipeline_spec: dict[str, Any]) -> dict[str, Any]:
        arguments: dict[str, Any] = {}
        inputs = pipeline_spec.get("inputs", [])
        if isinstance(inputs, list):
            for input_item in inputs:
                if isinstance(input_item, dict) and "name" in input_item and "default" in input_item:
                    arguments[input_item["name"]] = input_item["default"]
        return arguments

    @staticmethod
    def convert_yaml_to_payload(
        pipeline_spec: dict[str, Any],
        run_args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"root_task": {"componentRef": {"spec": pipeline_spec}}}
        arguments = PipelineRunManager.extract_default_arguments(pipeline_spec)
        if run_args:
            arguments.update(run_args)

        pipeline_inputs = pipeline_spec.get("inputs", [])
        valid_inputs = {inp.get("name") for inp in pipeline_inputs if isinstance(inp, dict) and inp.get("name")}
        if valid_inputs:
            arguments = {key: value for key, value in arguments.items() if key in valid_inputs}

        missing: list[str] = []
        for input_item in pipeline_inputs if isinstance(pipeline_inputs, list) else []:
            if not isinstance(input_item, dict):
                continue
            name = input_item.get("name")
            if name and "default" not in input_item and not input_item.get("optional", False) and name not in arguments:
                missing.append(name)
        if missing:
            raise PipelineRunError(
                f"Missing {len(missing)} required pipeline input(s): {', '.join(sorted(missing))}"
            )

        if arguments:
            payload["root_task"]["arguments"] = arguments
        return payload

    @staticmethod
    def sanitize_submit_payload(value: Any) -> Any:
        """Return a submit-safe payload with TD-compatible componentRef fixes.

        The hydrator uses explicit local-only annotations such as
        ``_source_dir`` while recursively resolving local files. Those
        provenance keys must not be submitted to the backend. User-supplied
        underscore-prefixed payload keys are otherwise valid and preserved.
        TD also normalizes ``componentRef.text`` into ``componentRef.spec``
        for component-library entries before submit; keep the same behavior
        here.
        """

        if isinstance(value, list):
            return [PipelineRunManager.sanitize_submit_payload(item) for item in value]
        if not isinstance(value, dict):
            return value

        local_only_keys = {"_source_dir", "_recursive_params"}
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if str(key) in local_only_keys:
                continue
            cleaned[key] = PipelineRunManager.sanitize_submit_payload(item)

        component_ref = cleaned.get("componentRef")
        if isinstance(component_ref, dict) and "text" in component_ref and not component_ref.get("spec"):
            text_content = component_ref.pop("text")
            if isinstance(text_content, str):
                try:
                    component_ref["spec"] = yaml.safe_load(text_content)
                except yaml.YAMLError as exc:
                    component_name = component_ref.get("name", "unknown")
                    raise PipelineRunError(
                        f"Failed to parse YAML in componentRef {component_name!r}: {exc}"
                    ) from exc
            else:
                component_ref["spec"] = text_content
            component_ref["spec"] = PipelineRunManager.sanitize_submit_payload(component_ref["spec"])

        return cleaned

    @staticmethod
    def is_terminal_status(status: str | None) -> bool:
        return bool(status and status.upper() in _TERMINAL_STATUSES)

    @staticmethod
    def status_from_run(run: Mapping[str, Any]) -> str | None:
        summary = run.get("execution_summary")
        if isinstance(summary, Mapping) and summary.get("has_ended") is True:
            stats = run.get("execution_status_stats")
            if isinstance(stats, Mapping):
                for status in ("FAILED", "SYSTEM_ERROR", "CANCELLED", "CANCELED"):
                    if int(stats.get(status, 0) or 0) > 0:
                        return status
                if int(stats.get("SUCCEEDED", 0) or 0) > 0:
                    return "SUCCEEDED"
            return "ENDED"
        stats = run.get("execution_status_stats")
        if isinstance(stats, Mapping):
            for status in _ACTIVE_STATUSES:
                if int(stats.get(status, 0) or 0) > 0:
                    return status
            for status in _TERMINAL_STATUSES:
                if int(stats.get(status, 0) or 0) > 0:
                    return status
        return None

    def load_pipeline_for_submit(
        self,
        pipeline_path: str | Path,
        *,
        hydrate: bool = True,
        resolution_overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if hydrate:
            return self.hooks.hydrate_pipeline(
                pipeline_path,
                client=self.client,
                resolution_overrides=resolution_overrides,
            )
        return self.hooks.read_pipeline_yaml(pipeline_path)

    def build_submit_body(
        self,
        pipeline_path: str | Path,
        *,
        run_args: dict[str, Any] | None = None,
        annotations: dict[str, str] | None = None,
        hydrate: bool = True,
        run_as: str | None = None,
        resolution_overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        pipeline_spec = self.load_pipeline_for_submit(
            pipeline_path,
            hydrate=hydrate,
            resolution_overrides=resolution_overrides,
        )
        run_args = self.hooks.prepare_run_arguments(pipeline_spec, run_args)
        payload = self.convert_yaml_to_payload(copy.deepcopy(pipeline_spec), run_args)
        payload = self.sanitize_submit_payload(payload)
        submit_annotations = self.hooks.extra_submit_annotations(
            pipeline_spec=pipeline_spec,
            pipeline_path=pipeline_path,
            run_as=run_as,
        )
        if annotations:
            submit_annotations.update({str(k): str(v) for k, v in annotations.items()})
        return {"root_task": payload["root_task"], "annotations": submit_annotations}

    def submit_pipeline(
        self,
        pipeline_path: str | Path,
        *,
        run_args: dict[str, Any] | None = None,
        annotations: dict[str, str] | None = None,
        hydrate: bool = True,
        run_as: str | None = None,
        resolution_overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = self.build_submit_body(
            pipeline_path,
            run_args=run_args,
            annotations=annotations,
            hydrate=hydrate,
            run_as=run_as,
            resolution_overrides=resolution_overrides,
        )
        pipeline_spec = body["root_task"]["componentRef"]["spec"]
        self.hooks.before_submit(pipeline_spec)
        response = self.to_plain(self.client.pipeline_runs_create(body=body))
        self.hooks.after_submit(response)
        return response

    def get_run(self, run_id: str, *, include_execution_stats: bool = True) -> dict[str, Any]:
        return self.to_plain(
            self.client.pipeline_runs_get(
                run_id,
                include_execution_stats=include_execution_stats,
            )
        )

    def get_run_details(
        self,
        run_id: str,
        *,
        include_annotations: bool = False,
        include_execution_state: bool = False,
    ) -> dict[str, Any]:
        return self.to_plain(
            self.client.get_run_details(
                run_id,
                include_annotations=include_annotations,
                include_execution_state=include_execution_state,
            )
        )

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        return self.to_plain(self.client.pipeline_runs_cancel(run_id)) or {"id": run_id, "cancelled": True}

    def graph_state(self, execution_id: str) -> dict[str, Any]:
        return self.to_plain(self.client.executions_graph_execution_state(execution_id))

    def logs(self, execution_id: str) -> dict[str, Any]:
        return self.to_plain(self.hooks.fetch_logs(self.client, execution_id))

    def search_runs(
        self,
        *,
        filter: str | None = None,
        filter_query: str | None = None,
        page_token: str | None = None,
        include_pipeline_names: bool | None = None,
        include_execution_stats: bool | None = True,
    ) -> dict[str, Any]:
        return self.to_plain(
            self.client.pipeline_runs_list(
                page_token=page_token,
                filter=filter,
                filter_query=filter_query,
                include_pipeline_names=include_pipeline_names,
                include_execution_stats=include_execution_stats,
            )
        )

    def annotations_list(self, run_id: str) -> dict[str, Any]:
        return self.to_plain(self.client.pipeline_runs_annotations(run_id))

    def annotations_set(self, run_id: str, key: str, value: Any) -> dict[str, Any]:
        self.client.pipeline_runs_put_annotations(run_id, key, value=value)
        return {"id": run_id, "key": key, "value": value}

    def annotations_delete(self, run_id: str, key: str) -> dict[str, Any]:
        self.client.pipeline_runs_delete_annotations(run_id, key)
        return {"id": run_id, "key": key, "deleted": True}

    def export_run(self, run_id: str, output: str | Path | None = None) -> dict[str, Any]:
        task_spec = self.client.get_run_pipeline_spec(run_id)
        if task_spec is None:
            raise PipelineRunError(f"No pipeline spec found for run {run_id}")
        raw = getattr(task_spec, "raw", None)
        if isinstance(raw, Mapping):
            spec = raw.get("componentRef", {}).get("spec")
        else:
            spec = None
        component_spec = getattr(task_spec, "component_spec", None)
        if not isinstance(spec, dict) and component_spec is not None:
            spec = getattr(component_spec, "data", None)
        if not isinstance(spec, dict):
            raise PipelineRunError(f"Pipeline spec for run {run_id} is not exportable")
        content = dump_yaml(spec)
        if output is None:
            return {"run_id": run_id, "pipeline": spec, "yaml": content}
        output_path = Path(output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")
        return {"run_id": run_id, "output": str(output_path)}

    def wait_for_completion(
        self,
        run_id: str,
        *,
        max_wait: float,
        poll_interval: float,
    ) -> dict[str, Any]:
        if max_wait < 0:
            raise PipelineRunError("--max-wait must be non-negative")
        if poll_interval <= 0:
            raise PipelineRunError("--poll-interval must be positive")
        deadline = time.monotonic() + max_wait
        last_run: dict[str, Any] = {}
        while True:
            last_run = self.get_run(run_id, include_execution_stats=True)
            status = self.status_from_run(last_run)
            if self.is_terminal_status(status) or status == "ENDED":
                result = {"run": last_run, "status": status or "ENDED", "timed_out": False}
                self.hooks.after_wait(result)
                return result
            if time.monotonic() >= deadline:
                return {"run": last_run, "status": status or "UNKNOWN", "timed_out": True}
            time.sleep(min(poll_interval, max(0.0, deadline - time.monotonic())))


def parse_key_value_entries(entries: list[str] | None) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for entry in entries or []:
        if "=" not in entry:
            raise PipelineRunError("Expected KEY=VALUE")
        key, value = entry.split("=", 1)
        if not key:
            raise PipelineRunError("Expected KEY=VALUE")
        parsed[key] = value
    return parsed


def parse_json_or_key_values(text: str | None, entries: list[str] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if text:
        loaded = json.loads(text)
        if not isinstance(loaded, dict):
            raise PipelineRunError("JSON value must be an object")
        result.update(loaded)
    result.update(parse_key_value_entries(entries))
    return result
