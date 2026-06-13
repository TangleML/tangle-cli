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
import re
import time
from contextlib import AbstractContextManager, nullcontext
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
class PipelineRunContext:
    """First-class context for a pipeline run lifecycle.

    Downstreams can use this for mutex ownership, graceful-shutdown state,
    notifications, retries, and scheduled timeout bookkeeping without scraping
    transient manager attributes.
    """

    run_id: str | None = None
    run_name: str | None = None
    root_execution_id: str | None = None
    pipeline_path: str | Path | None = None
    start_time: float | None = None
    attempt: int = 1
    submit_body: dict[str, Any] | None = None
    pipeline_spec: dict[str, Any] | None = None
    response: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineWaitPoll:
    """One wait-loop observation passed to lifecycle hooks."""

    run_id: str
    run: dict[str, Any]
    status: str
    status_counts: dict[str, int]
    total: int
    terminal: bool
    graph_state: dict[str, Any] | None = None
    elapsed_seconds: float = 0.0


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

    def prepare_pipeline_spec(
        self,
        pipeline_spec: dict[str, Any],
        *,
        pipeline_path: str | Path | None,
        run_args: dict[str, Any] | None,
        hydrate: bool,
    ) -> dict[str, Any]:
        """Hook for downstream validation/hydration/layout/annotation transforms.

        The default returns the already-loaded spec unchanged. TD can override
        this to run schema validation, auto-layout, source annotations, or any
        pre-submit preparation before the generic payload conversion runs.
        """

        return pipeline_spec

    def prepare_run_arguments(
        self,
        pipeline_spec: dict[str, Any],
        run_args: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """Hook for TD JOB_CONFIG time input / scheduled runtime behavior."""
        return run_args

    def transform_run_name(
        self,
        run_name: str,
        *,
        pipeline_spec: dict[str, Any],
        run_args: dict[str, Any] | None,
    ) -> str:
        """Hook for downstream run-name policies after template expansion."""

        return run_name

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
        """Legacy hook retained for compatibility with existing downstreams."""

    def before_submit_context(self, context: PipelineRunContext) -> None:
        """Hook for TD mutex/overlap checks with full run context."""

        if context.pipeline_spec is not None:
            self.before_submit(context.pipeline_spec)

    def after_submit(self, response: Mapping[str, Any]) -> None:
        """Legacy hook retained for downstream start notifications."""

    def after_submit_context(self, context: PipelineRunContext) -> None:
        """Hook for downstream start notifications with full run context."""

        if context.response is not None:
            self.after_submit(context.response)

    def on_submit_error(
        self,
        error: Exception,
        *,
        context: PipelineRunContext,
    ) -> None:
        """Hook for downstream submit-error notifications/cleanup."""

    def around_run(self, context: PipelineRunContext) -> AbstractContextManager[Any]:
        """Context-manager seam for mutex/run lifecycle ownership."""

        return nullcontext()

    def before_run_lifecycle(self, context: PipelineRunContext) -> None:
        """Hook called before a run attempt enters the lifecycle context."""

    def after_run_lifecycle(
        self,
        context: PipelineRunContext,
        *,
        success: bool,
        error: Exception | None = None,
    ) -> None:
        """Hook called after the lifecycle context exits."""

    def on_fail_fast_before_release(
        self,
        context: PipelineRunContext,
        error: Exception,
    ) -> None:
        """Hook called before lifecycle release when fail-fast aborts a run."""

    def before_retry(
        self,
        context: PipelineRunContext,
        error: Exception,
        *,
        next_attempt: int,
    ) -> None:
        """Hook before retrying a failed submit/run attempt."""

    def after_retry_submit(self, context: PipelineRunContext) -> None:
        """Hook after a retry successfully submits a new run."""

    def should_cancel_previous_run(
        self,
        context: PipelineRunContext,
        error: Exception,
        *,
        next_attempt: int,
    ) -> bool:
        """Return True when retry should cancel the previous run first."""

        return False

    def before_wait(self, context: PipelineRunContext) -> None:
        """Hook called before polling a run."""

    def after_poll(self, poll: PipelineWaitPoll, context: PipelineRunContext) -> None:
        """Hook called after each run/graph-state poll."""

    def should_exit_early(self, poll: PipelineWaitPoll, context: PipelineRunContext) -> bool:
        """Return True to stop waiting before terminal/timeout."""

        return False

    def on_timeout(self, poll: PipelineWaitPoll, context: PipelineRunContext) -> None:
        """Hook called when wait reaches max_wait."""

    def on_terminal(self, poll: PipelineWaitPoll, context: PipelineRunContext) -> None:
        """Hook called when wait observes terminal state."""

    def on_early_exit_before_release(
        self,
        poll: PipelineWaitPoll,
        context: PipelineRunContext,
    ) -> None:
        """Hook called for fail-fast early exit before lifecycle release."""

    def after_wait(self, result: Mapping[str, Any]) -> None:
        """Legacy hook retained for terminal downstream notifications."""

    def after_wait_context(self, result: Mapping[str, Any], context: PipelineRunContext) -> None:
        """Hook called after wait returns with full run context.

        Preserve legacy behavior: ``after_wait(result)`` is called only for
        terminal observations, not timeouts or fail-fast/early-exit returns.
        Downstreams that need those outcomes should override ``on_timeout``,
        ``on_early_exit_before_release``, or this context-aware hook directly.
        """

        if not result.get("timed_out") and not result.get("early_exit"):
            status = result.get("status")
            status_text = str(status).upper() if status else None
            if status_text == "ENDED" or status_text in _TERMINAL_STATUSES:
                self.after_wait(result)

    def should_enforce_max_wait(self, context: PipelineRunContext) -> bool:
        """Return False for downstream-controlled scheduled timeout policies."""

        return True

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
    def status_counts_from_run(run: Mapping[str, Any]) -> dict[str, int]:
        stats = run.get("execution_status_stats")
        if not isinstance(stats, Mapping):
            return {}
        result: dict[str, int] = {}
        for key, value in stats.items():
            try:
                result[str(key).upper()] = int(value or 0)
            except (TypeError, ValueError):
                continue
        return result

    @staticmethod
    def status_counts_from_graph_state(graph_state: Mapping[str, Any]) -> dict[str, int]:
        for key in ("status_totals", "execution_status_stats"):
            stats = graph_state.get(key)
            if isinstance(stats, Mapping):
                return {
                    str(status).upper(): int(count or 0)
                    for status, count in stats.items()
                }
        child_stats = graph_state.get("child_execution_status_stats")
        totals: dict[str, int] = {}
        if isinstance(child_stats, Mapping):
            for stats in child_stats.values():
                if not isinstance(stats, Mapping):
                    continue
                for status, count in stats.items():
                    totals[str(status).upper()] = totals.get(str(status).upper(), 0) + int(count or 0)
        return totals

    @staticmethod
    def status_from_counts(status_counts: Mapping[str, int]) -> str | None:
        for status in _ACTIVE_STATUSES:
            if int(status_counts.get(status, 0) or 0) > 0:
                return status
        for status in _TERMINAL_STATUSES:
            if int(status_counts.get(status, 0) or 0) > 0:
                return status
        return None

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

    @staticmethod
    def expand_run_name_template(
        template: str,
        pipeline_spec: dict[str, Any],
        run_args: dict[str, Any] | None = None,
    ) -> str:
        """Expand ``${arguments.NAME}`` placeholders from defaults + run args."""

        arguments = PipelineRunManager.extract_default_arguments(pipeline_spec)
        if run_args:
            arguments.update(run_args)

        def replace_placeholder(match: re.Match[str]) -> str:
            value = arguments.get(match.group(1))
            return str(value) if value is not None else match.group(0)

        return re.sub(r"\$\{arguments\.([^}]+)\}", replace_placeholder, template)

    def apply_run_name_template(
        self,
        pipeline_spec: dict[str, Any],
        run_args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        annotations = pipeline_spec.get("metadata", {}).get("annotations", {})
        template = annotations.get("run-name-template") if isinstance(annotations, Mapping) else None
        if not template:
            return pipeline_spec
        transformed = copy.deepcopy(pipeline_spec)
        expanded = self.expand_run_name_template(str(template), transformed, run_args)
        transformed["name"] = self.hooks.transform_run_name(
            expanded,
            pipeline_spec=transformed,
            run_args=run_args,
        )
        return transformed

    def prepare_pipeline_spec_for_submit(
        self,
        pipeline_spec: dict[str, Any],
        *,
        pipeline_path: str | Path | None = None,
        run_args: dict[str, Any] | None = None,
        hydrate: bool = True,
    ) -> dict[str, Any]:
        return self.hooks.prepare_pipeline_spec(
            pipeline_spec,
            pipeline_path=pipeline_path,
            run_args=run_args,
            hydrate=hydrate,
        )

    def build_submit_body_from_spec(
        self,
        pipeline_spec: dict[str, Any],
        *,
        run_args: dict[str, Any] | None = None,
        annotations: dict[str, str] | None = None,
        pipeline_path: str | Path | None = None,
        run_as: str | None = None,
        hydrate: bool = True,
    ) -> dict[str, Any]:
        """Build a submit body from an already-prepared pipeline spec."""

        prepared_spec = self.prepare_pipeline_spec_for_submit(
            pipeline_spec,
            pipeline_path=pipeline_path,
            run_args=run_args,
            hydrate=hydrate,
        )
        run_args = self.hooks.prepare_run_arguments(prepared_spec, run_args)
        prepared_spec = self.apply_run_name_template(prepared_spec, run_args)
        payload = self.convert_yaml_to_payload(copy.deepcopy(prepared_spec), run_args)
        payload = self.sanitize_submit_payload(payload)
        submit_annotations = self.hooks.extra_submit_annotations(
            pipeline_spec=prepared_spec,
            pipeline_path=pipeline_path,
            run_as=run_as,
        )
        if annotations:
            submit_annotations.update({str(k): str(v) for k, v in annotations.items()})
        return {"root_task": payload["root_task"], "annotations": submit_annotations}

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
        return self.build_submit_body_from_spec(
            pipeline_spec,
            run_args=run_args,
            annotations=annotations,
            pipeline_path=pipeline_path,
            run_as=run_as,
            hydrate=hydrate,
        )

    @staticmethod
    def response_run_context(
        response: Mapping[str, Any],
        *,
        submit_body: dict[str, Any],
        pipeline_path: str | Path | None = None,
        attempt: int = 1,
    ) -> PipelineRunContext:
        pipeline_spec = submit_body.get("root_task", {}).get("componentRef", {}).get("spec")
        run_name = pipeline_spec.get("name") if isinstance(pipeline_spec, dict) else None
        return PipelineRunContext(
            run_id=str(response.get("id")) if response.get("id") is not None else None,
            run_name=str(run_name) if run_name is not None else None,
            root_execution_id=(
                str(response.get("root_execution_id"))
                if response.get("root_execution_id") is not None
                else None
            ),
            pipeline_path=pipeline_path,
            start_time=time.time(),
            attempt=attempt,
            submit_body=submit_body,
            pipeline_spec=pipeline_spec if isinstance(pipeline_spec, dict) else None,
            response=dict(response),
        )

    def submit_prepared_body(
        self,
        body: dict[str, Any],
        *,
        pipeline_path: str | Path | None = None,
        attempt: int = 1,
    ) -> dict[str, Any]:
        pipeline_spec = body["root_task"]["componentRef"]["spec"]
        context = PipelineRunContext(
            run_name=str(pipeline_spec.get("name")) if isinstance(pipeline_spec, dict) else None,
            pipeline_path=pipeline_path,
            start_time=time.time(),
            attempt=attempt,
            submit_body=body,
            pipeline_spec=pipeline_spec,
        )
        self.hooks.before_submit_context(context)
        try:
            response = self.to_plain(self.client.pipeline_runs_create(body=body))
        except Exception as exc:
            self.hooks.on_submit_error(exc, context=context)
            raise
        if not isinstance(response, dict):
            response = {}
        submitted_context = self.response_run_context(
            response,
            submit_body=body,
            pipeline_path=pipeline_path,
            attempt=attempt,
        )
        self.hooks.after_submit_context(submitted_context)
        return response

    def submit_pipeline_spec(
        self,
        pipeline_spec: dict[str, Any],
        *,
        run_args: dict[str, Any] | None = None,
        annotations: dict[str, str] | None = None,
        pipeline_path: str | Path | None = None,
        run_as: str | None = None,
        hydrate: bool = True,
        attempt: int = 1,
    ) -> dict[str, Any]:
        body = self.build_submit_body_from_spec(
            pipeline_spec,
            run_args=run_args,
            annotations=annotations,
            pipeline_path=pipeline_path,
            run_as=run_as,
            hydrate=hydrate,
        )
        return self.submit_prepared_body(body, pipeline_path=pipeline_path, attempt=attempt)

    def submit_pipeline(
        self,
        pipeline_path: str | Path,
        *,
        run_args: dict[str, Any] | None = None,
        annotations: dict[str, str] | None = None,
        hydrate: bool = True,
        run_as: str | None = None,
        resolution_overrides: dict[str, Any] | None = None,
        attempt: int = 1,
    ) -> dict[str, Any]:
        body = self.build_submit_body(
            pipeline_path,
            run_args=run_args,
            annotations=annotations,
            hydrate=hydrate,
            run_as=run_as,
            resolution_overrides=resolution_overrides,
        )
        return self.submit_prepared_body(body, pipeline_path=pipeline_path, attempt=attempt)

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

    def _poll_run_status(
        self,
        run_id: str,
        *,
        use_graph_state: bool,
        started_at: float,
    ) -> PipelineWaitPoll:
        run = self.get_run(run_id, include_execution_stats=True)
        graph_state: dict[str, Any] | None = None
        status_counts = self.status_counts_from_run(run)
        if use_graph_state:
            root_execution_id = run.get("root_execution_id")
            if root_execution_id:
                graph_state = self.graph_state(str(root_execution_id))
                graph_counts = self.status_counts_from_graph_state(graph_state)
                if graph_counts:
                    status_counts = graph_counts
        status = self.status_from_counts(status_counts) or self.status_from_run(run) or "UNKNOWN"
        terminal = self.is_terminal_status(status) or status == "ENDED"
        return PipelineWaitPoll(
            run_id=run_id,
            run=run,
            status=status,
            status_counts=status_counts,
            total=sum(status_counts.values()),
            terminal=terminal,
            graph_state=graph_state,
            elapsed_seconds=time.monotonic() - started_at,
        )

    def wait_for_completion(
        self,
        run_id: str,
        *,
        max_wait: float | None,
        poll_interval: float,
        use_graph_state: bool = False,
        context: PipelineRunContext | None = None,
    ) -> dict[str, Any]:
        wait_context = context or PipelineRunContext(run_id=run_id, start_time=time.time())
        if max_wait is not None and max_wait < 0:
            raise PipelineRunError("--max-wait must be non-negative")
        if poll_interval <= 0:
            raise PipelineRunError("--poll-interval must be positive")
        enforce_max_wait = max_wait is not None and self.hooks.should_enforce_max_wait(wait_context)
        started_at = time.monotonic()
        deadline = started_at + max_wait if enforce_max_wait else None
        self.hooks.before_wait(wait_context)
        last_poll: PipelineWaitPoll | None = None
        while True:
            poll = self._poll_run_status(run_id, use_graph_state=use_graph_state, started_at=started_at)
            last_poll = poll
            self.hooks.after_poll(poll, wait_context)
            if poll.terminal:
                self.hooks.on_terminal(poll, wait_context)
                result = {"run": poll.run, "status": poll.status, "timed_out": False}
                self.hooks.after_wait_context(result, wait_context)
                return result
            if self.hooks.should_exit_early(poll, wait_context):
                self.hooks.on_early_exit_before_release(poll, wait_context)
                result = {"run": poll.run, "status": poll.status, "timed_out": False, "early_exit": True}
                self.hooks.after_wait_context(result, wait_context)
                return result
            if deadline is not None and time.monotonic() >= deadline:
                self.hooks.on_timeout(poll, wait_context)
                result = {"run": poll.run, "status": poll.status, "timed_out": True}
                self.hooks.after_wait_context(result, wait_context)
                return result
            if deadline is None:
                sleep_for = poll_interval
            else:
                sleep_for = min(poll_interval, max(0.0, deadline - time.monotonic()))
            time.sleep(sleep_for)
        if last_poll is None:  # pragma: no cover - defensive, loop always polls first
            raise PipelineRunError(f"No status returned for run {run_id}")

    def run_pipeline(
        self,
        pipeline_path: str | Path,
        *,
        run_args: dict[str, Any] | None = None,
        annotations: dict[str, str] | None = None,
        hydrate: bool = True,
        run_as: str | None = None,
        resolution_overrides: dict[str, Any] | None = None,
        wait: bool = False,
        max_wait: float | None = 600.0,
        poll_interval: float = 10.0,
        use_graph_state: bool = False,
        max_attempts: int = 1,
    ) -> dict[str, Any]:
        """Submit (and optionally wait for) a pipeline with lifecycle hooks.

        This is intentionally opt-in so existing CLI submit/wait semantics stay
        unchanged. Downstreams can use it to centralize mutex acquisition,
        graceful-shutdown context, retry, and notification lifecycles around the
        generic OSS submit/wait behavior.
        """

        if max_attempts < 1:
            raise PipelineRunError("max_attempts must be at least 1")
        last_error: Exception | None = None
        previous_context: PipelineRunContext | None = None
        for attempt in range(1, max_attempts + 1):
            context = PipelineRunContext(
                pipeline_path=pipeline_path,
                start_time=time.time(),
                attempt=attempt,
            )
            lifecycle_started = False
            success = False
            error: Exception | None = None
            retry_requested = False
            body = self.build_submit_body(
                pipeline_path,
                run_args=run_args,
                annotations=annotations,
                hydrate=hydrate,
                run_as=run_as,
                resolution_overrides=resolution_overrides,
            )
            pipeline_spec = body.get("root_task", {}).get("componentRef", {}).get("spec")
            context.submit_body = body
            context.pipeline_spec = pipeline_spec if isinstance(pipeline_spec, dict) else None
            if context.pipeline_spec is not None and context.pipeline_spec.get("name") is not None:
                context.run_name = str(context.pipeline_spec["name"])
            self.hooks.before_run_lifecycle(context)
            lifecycle_started = True
            try:
                with self.hooks.around_run(context):
                    try:
                        response = self.submit_prepared_body(
                            body,
                            pipeline_path=pipeline_path,
                            attempt=attempt,
                        )
                        submitted_context = self.response_run_context(
                            response,
                            submit_body=body,
                            pipeline_path=pipeline_path,
                            attempt=attempt,
                        )
                        context.run_id = submitted_context.run_id
                        context.run_name = submitted_context.run_name
                        context.root_execution_id = submitted_context.root_execution_id
                        context.submit_body = submitted_context.submit_body
                        context.pipeline_spec = submitted_context.pipeline_spec
                        context.response = response
                        previous_context = context
                        if attempt > 1:
                            self.hooks.after_retry_submit(context)
                        result: dict[str, Any]
                        if wait and context.run_id:
                            wait_result = self.wait_for_completion(
                                context.run_id,
                                max_wait=max_wait,
                                poll_interval=poll_interval,
                                use_graph_state=use_graph_state,
                                context=context,
                            )
                            result = {"response": response, "wait": wait_result}
                        else:
                            result = {"response": response}
                        success = True
                        return result
                    except Exception as exc:
                        error = exc
                        last_error = exc
                        if (
                            context.run_id
                            and attempt < max_attempts
                            and self.hooks.should_cancel_previous_run(
                                context,
                                exc,
                                next_attempt=attempt + 1,
                            )
                        ):
                            self.cancel_run(context.run_id)
                        if attempt >= max_attempts:
                            self.hooks.on_fail_fast_before_release(context, exc)
                            raise
                        retry_context = context if context.run_id else previous_context or context
                        self.hooks.before_retry(retry_context, exc, next_attempt=attempt + 1)
                        retry_requested = True
            finally:
                if lifecycle_started:
                    self.hooks.after_run_lifecycle(context, success=success, error=error)
            if retry_requested:
                continue
        if last_error is not None:  # pragma: no cover - defensive
            raise last_error
        raise PipelineRunError("Pipeline run did not start")  # pragma: no cover


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
