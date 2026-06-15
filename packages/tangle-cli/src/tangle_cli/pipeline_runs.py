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
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml

from .logger import Logger, get_default_logger
from .pipeline_hydrator import HydrationError, PipelineHydrator
from .pipeline_run_details import get_graph_state_output, get_run_details_output
from .pipeline_run_search import search_pipeline_runs
from .utils import dump_yaml

_TERMINAL_STATUSES = ("FAILED", "SYSTEM_ERROR", "CANCELLED", "CANCELED", "SKIPPED", "SUCCEEDED", "INVALID")
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

    def poll_run_snapshot(
        self,
        manager: "PipelineRunManager",
        run_id: str,
        context: PipelineRunContext,
    ) -> Mapping[str, Any] | None:
        """Optional hook to provide a run-like snapshot for wait polling.

        Downstreams whose wait API is rooted at an execution id can return a
        synthetic run snapshot here instead of forcing the generic manager to
        call ``pipeline_runs_get(run_id)``.
        """

        return None

    def graph_state_execution_id(
        self,
        run: Mapping[str, Any],
        context: PipelineRunContext,
    ) -> str | None:
        """Return the execution id to use for graph-state polling."""

        root_execution_id = run.get("root_execution_id") or context.root_execution_id
        return str(root_execution_id) if root_execution_id is not None else None

    def on_poll_error(self, error: Exception, context: PipelineRunContext) -> float | None:
        """Handle polling errors.

        Return a sleep interval to retry, or ``None`` to propagate the error.
        """

        return None

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
        if isinstance(value, Mapping):
            return {key: PipelineRunManager.to_plain(val) for key, val in value.items()}
        if hasattr(value, "to_dict"):
            return value.to_dict()
        if hasattr(value, "model_dump"):
            return value.model_dump(by_alias=True)
        if isinstance(value, list):
            return [PipelineRunManager.to_plain(item) for item in value]
        if hasattr(value, "__dict__"):
            return {
                key: PipelineRunManager.to_plain(val)
                for key, val in vars(value).items()
                if not key.startswith("_")
            }
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
    def _counts_mapping(value: Any) -> Mapping[str, Any] | None:
        if isinstance(value, Mapping):
            return value
        if value is not None and hasattr(value, "items"):
            return value
        return None

    @staticmethod
    def status_counts_from_graph_state(graph_state: Mapping[str, Any] | Any) -> dict[str, int]:
        for key in ("status_totals", "execution_status_stats"):
            stats = graph_state.get(key) if isinstance(graph_state, Mapping) else getattr(graph_state, key, None)
            counts = PipelineRunManager._counts_mapping(stats)
            if counts is not None:
                return {
                    str(status).upper(): int(count or 0)
                    for status, count in counts.items()
                }
        child_stats = (
            graph_state.get("child_execution_status_stats")
            if isinstance(graph_state, Mapping)
            else getattr(graph_state, "child_execution_status_stats", None)
        )
        totals: dict[str, int] = {}
        child_counts = PipelineRunManager._counts_mapping(child_stats)
        if child_counts is not None:
            for stats in child_counts.values():
                counts = PipelineRunManager._counts_mapping(stats)
                if counts is None:
                    continue
                for status, count in counts.items():
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
        context: PipelineRunContext | None = None,
    ) -> dict[str, Any]:
        pipeline_spec = body["root_task"]["componentRef"]["spec"]
        submit_context = context or PipelineRunContext(
            pipeline_path=pipeline_path,
            start_time=time.time(),
            attempt=attempt,
        )
        submit_context.run_name = (
            str(pipeline_spec.get("name")) if isinstance(pipeline_spec, dict) else None
        )
        submit_context.pipeline_path = pipeline_path
        submit_context.attempt = attempt
        submit_context.submit_body = body
        submit_context.pipeline_spec = pipeline_spec if isinstance(pipeline_spec, dict) else None
        self.hooks.before_submit_context(submit_context)
        try:
            response = self.to_plain(self.client.pipeline_runs_create(body=body))
        except Exception as exc:
            self.hooks.on_submit_error(exc, context=submit_context)
            raise
        if not isinstance(response, dict):
            response = {}
        submitted_context = self.response_run_context(
            response,
            submit_body=body,
            pipeline_path=pipeline_path,
            attempt=attempt,
        )
        submit_context.run_id = submitted_context.run_id
        submit_context.run_name = submitted_context.run_name
        submit_context.root_execution_id = submitted_context.root_execution_id
        submit_context.submit_body = submitted_context.submit_body
        submit_context.pipeline_spec = submitted_context.pipeline_spec
        submit_context.response = response
        self.hooks.after_submit_context(submit_context)
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
        include_implementations: bool = False,
        execution_id: str | None = None,
    ) -> dict[str, Any]:
        return get_run_details_output(
            self.client,
            run_id,
            include_implementations=include_implementations,
            include_annotations=include_annotations,
            include_execution_state=include_execution_state,
            execution_id=execution_id,
        )

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        return self.to_plain(self.client.pipeline_runs_cancel(run_id)) or {"id": run_id, "cancelled": True}

    def graph_state(self, execution_id: str) -> Mapping[str, Any] | Any:
        graph_state = self.client.executions_graph_execution_state(execution_id)
        return self.to_plain(graph_state)

    def graph_state_output(self, run_ids: list[str], *, timeout: float = 30.0) -> dict[str, Any]:
        return get_graph_state_output(self.client, run_ids, timeout=timeout)

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

    def search_pipeline_runs(
        self,
        *,
        name: str | None = None,
        created_by: str | None = None,
        annotations: dict[str, str | None] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        local_time: bool = False,
        query: dict[str, Any] | None = None,
        limit: int = 10,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        return search_pipeline_runs(
            client=self.client,
            name=name,
            created_by=created_by,
            annotations=annotations,
            start_date=start_date,
            end_date=end_date,
            local_time=local_time,
            query=query,
            limit=limit,
            page_token=page_token,
            logger=self.logger,
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
        context: PipelineRunContext | None = None,
    ) -> PipelineWaitPoll:
        wait_context = context or PipelineRunContext(run_id=run_id, start_time=time.time())
        run_snapshot = self.hooks.poll_run_snapshot(self, run_id, wait_context)
        run = self.to_plain(run_snapshot) if run_snapshot is not None else self.get_run(
            run_id, include_execution_stats=True
        )
        if not isinstance(run, dict):
            run = {}
        graph_state: dict[str, Any] | None = None
        status_counts = self.status_counts_from_run(run)
        if use_graph_state:
            root_execution_id = self.hooks.graph_state_execution_id(run, wait_context)
            if root_execution_id:
                graph_state = self.graph_state(str(root_execution_id))
                graph_counts = self.status_counts_from_graph_state(graph_state)
                if graph_counts:
                    status_counts = graph_counts
        status = self.status_from_counts(status_counts) or self.status_from_run(run) or "UNKNOWN"
        terminal = self.is_terminal_status(status) or status == "ENDED"
        total = sum(status_counts.values())
        if total and use_graph_state:
            terminal_count = sum(status_counts.get(state, 0) for state in _TERMINAL_STATUSES)
            terminal = terminal_count == total
        return PipelineWaitPoll(
            run_id=run_id,
            run=run,
            status=status,
            status_counts=status_counts,
            total=total,
            terminal=terminal,
            graph_state=graph_state if isinstance(graph_state, dict) else None,
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
        allow_zero_poll_interval: bool = False,
        timeout_clock: str = "monotonic",
    ) -> dict[str, Any]:
        wait_context = context or PipelineRunContext(run_id=run_id, start_time=time.time())
        if max_wait is not None and max_wait < 0:
            raise PipelineRunError("--max-wait must be non-negative")
        if poll_interval < 0 or (poll_interval == 0 and not allow_zero_poll_interval):
            raise PipelineRunError("--poll-interval must be positive")
        if timeout_clock not in {"monotonic", "wall"}:
            raise PipelineRunError("timeout_clock must be 'monotonic' or 'wall'")
        enforce_max_wait = max_wait is not None and self.hooks.should_enforce_max_wait(wait_context)
        poll_started_at = time.monotonic()
        deadline_now: Callable[[], float] = time.time if timeout_clock == "wall" else time.monotonic
        deadline_started_at = deadline_now()
        deadline = deadline_started_at + max_wait if enforce_max_wait else None
        self.hooks.before_wait(wait_context)
        last_poll: PipelineWaitPoll | None = None
        while True:
            try:
                poll = self._poll_run_status(
                    run_id,
                    use_graph_state=use_graph_state,
                    started_at=poll_started_at,
                    context=wait_context,
                )
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                if deadline is not None and deadline_now() >= deadline:
                    raise PipelineRunError(f"Timed out waiting for run {run_id}") from exc
                retry_interval = self.hooks.on_poll_error(exc, wait_context)
                if retry_interval is None:
                    raise
                if deadline is not None:
                    remaining = deadline - deadline_now()
                    if remaining <= 0:
                        raise PipelineRunError(f"Timed out waiting for run {run_id}") from exc
                    retry_interval = min(retry_interval, remaining)
                time.sleep(max(0.0, retry_interval))
                continue
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
            if deadline is not None and deadline_now() >= deadline:
                self.hooks.on_timeout(poll, wait_context)
                result = {"run": poll.run, "status": poll.status, "timed_out": True}
                self.hooks.after_wait_context(result, wait_context)
                return result
            if deadline is None:
                sleep_for = poll_interval
            else:
                sleep_for = min(poll_interval, max(0.0, deadline - deadline_now()))
            time.sleep(sleep_for)
        if last_poll is None:  # pragma: no cover - defensive, loop always polls first
            raise PipelineRunError(f"No status returned for run {run_id}")

    def _run_body_factory(
        self,
        body_factory: Callable[[int, PipelineRunContext | None, Exception | None], dict[str, Any]],
        *,
        pipeline_path: str | Path | None = None,
        wait: bool = False,
        max_wait: float | None = 600.0,
        poll_interval: float = 10.0,
        use_graph_state: bool = False,
        max_attempts: int = 1,
        allow_zero_poll_interval: bool = False,
        timeout_clock: str = "monotonic",
        metadata: dict[str, Any] | None = None,
        metadata_factory: Callable[
            [int, PipelineRunContext | None, Exception | None], dict[str, Any]
        ] | None = None,
    ) -> dict[str, Any]:
        """Drive submit/wait/retry for already prepared specs or submit bodies."""

        if max_attempts < 1:
            raise PipelineRunError("max_attempts must be at least 1")
        last_error: Exception | None = None
        previous_context: PipelineRunContext | None = None
        attempts: list[PipelineRunContext] = []
        for attempt in range(1, max_attempts + 1):
            context = PipelineRunContext(
                pipeline_path=pipeline_path,
                start_time=time.time(),
                attempt=attempt,
                metadata=dict(metadata or {}),
            )
            lifecycle_started = False
            success = False
            error: Exception | None = None
            retry_requested = False
            body = body_factory(attempt, previous_context, last_error)
            if metadata_factory is not None:
                context.metadata.update(metadata_factory(attempt, previous_context, last_error))
            pipeline_spec = body.get("root_task", {}).get("componentRef", {}).get("spec")
            context.submit_body = body
            context.pipeline_spec = pipeline_spec if isinstance(pipeline_spec, dict) else None
            if context.pipeline_spec is not None and context.pipeline_spec.get("name") is not None:
                context.run_name = str(context.pipeline_spec["name"])
            self.hooks.before_run_lifecycle(context)
            lifecycle_started = True
            attempts.append(context)
            try:
                with self.hooks.around_run(context):
                    try:
                        response = self.submit_prepared_body(
                            body,
                            pipeline_path=pipeline_path,
                            attempt=attempt,
                            context=context,
                        )
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
                                allow_zero_poll_interval=allow_zero_poll_interval,
                                timeout_clock=timeout_clock,
                            )
                            result = {"response": response, "wait": wait_result}
                        else:
                            result = {"response": response}
                        result["context"] = context
                        result["attempts"] = attempts
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

    def run_prepared_body(
        self,
        body: dict[str, Any],
        *,
        pipeline_path: str | Path | None = None,
        wait: bool = False,
        max_wait: float | None = 600.0,
        poll_interval: float = 10.0,
        use_graph_state: bool = False,
        max_attempts: int = 1,
        retry_body_factory: Callable[
            [int, PipelineRunContext | None, Exception | None], dict[str, Any]
        ] | None = None,
        allow_zero_poll_interval: bool = False,
        timeout_clock: str = "monotonic",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Submit/wait/retry an already prepared submit body.

        ``retry_body_factory`` lets downstreams refresh retry bodies while still
        keeping hydration/layout/validation outside the generic lifecycle.
        """

        def body_factory(
            attempt: int,
            previous_context: PipelineRunContext | None,
            error: Exception | None,
        ) -> dict[str, Any]:
            if attempt > 1 and retry_body_factory is not None:
                return retry_body_factory(attempt, previous_context, error)
            return copy.deepcopy(body)

        return self._run_body_factory(
            body_factory,
            pipeline_path=pipeline_path,
            wait=wait,
            max_wait=max_wait,
            poll_interval=poll_interval,
            use_graph_state=use_graph_state,
            max_attempts=max_attempts,
            allow_zero_poll_interval=allow_zero_poll_interval,
            timeout_clock=timeout_clock,
            metadata=metadata,
        )

    def run_pipeline_spec(
        self,
        pipeline_spec: dict[str, Any],
        *,
        run_args: dict[str, Any] | None = None,
        annotations: dict[str, str] | None = None,
        pipeline_path: str | Path | None = None,
        run_as: str | None = None,
        hydrate: bool = True,
        wait: bool = False,
        max_wait: float | None = 600.0,
        poll_interval: float = 10.0,
        use_graph_state: bool = False,
        max_attempts: int = 1,
        allow_zero_poll_interval: bool = False,
        timeout_clock: str = "monotonic",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Submit/wait/retry an already hydrated/validated in-memory spec."""

        def body_factory(
            _attempt: int,
            _previous_context: PipelineRunContext | None,
            _error: Exception | None,
        ) -> dict[str, Any]:
            return self.build_submit_body_from_spec(
                copy.deepcopy(pipeline_spec),
                run_args=run_args,
                annotations=annotations,
                pipeline_path=pipeline_path,
                run_as=run_as,
                hydrate=hydrate,
            )

        return self._run_body_factory(
            body_factory,
            pipeline_path=pipeline_path,
            wait=wait,
            max_wait=max_wait,
            poll_interval=poll_interval,
            use_graph_state=use_graph_state,
            max_attempts=max_attempts,
            allow_zero_poll_interval=allow_zero_poll_interval,
            timeout_clock=timeout_clock,
            metadata=metadata,
        )

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
        allow_zero_poll_interval: bool = False,
        timeout_clock: str = "monotonic",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Submit (and optionally wait for) a pipeline with lifecycle hooks.

        Unlike ``run_pipeline_spec``, path-based runs intentionally rebuild the
        submit body on every retry so read/hydrate/resolution hooks are
        re-invoked for each attempt.
        """

        def body_factory(
            _attempt: int,
            _previous_context: PipelineRunContext | None,
            _error: Exception | None,
        ) -> dict[str, Any]:
            return self.build_submit_body(
                pipeline_path,
                run_args=run_args,
                annotations=annotations,
                hydrate=hydrate,
                run_as=run_as,
                resolution_overrides=resolution_overrides,
            )

        return self._run_body_factory(
            body_factory,
            pipeline_path=pipeline_path,
            wait=wait,
            max_wait=max_wait,
            poll_interval=poll_interval,
            use_graph_state=use_graph_state,
            max_attempts=max_attempts,
            allow_zero_poll_interval=allow_zero_poll_interval,
            timeout_clock=timeout_clock,
            metadata=metadata,
        )


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
