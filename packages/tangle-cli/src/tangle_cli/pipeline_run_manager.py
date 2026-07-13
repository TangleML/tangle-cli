"""Generic pipeline-run helpers for `tangle sdk pipeline-runs`.

This module ports the OSS-safe parts of tangle-deploy's runner/run details
commands while keeping downstream-specific behavior behind hooks.  The default
implementation uses only the public Tangle API and local files; cloud storage,
notifications, scheduler, mutex, run-as annotation defaults, and alternate log
backends are intentionally extension points rather than OSS behavior.
"""

from __future__ import annotations

import copy
import inspect
import json
import re
import time
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml

from .handler import TangleCliHandler
from .logger import Logger, get_default_logger
from .pipeline_dehydrator import DehydrateChoice, PipelineDehydrator
from .pipeline_hydrator import HydrationError, PipelineHydrator
from .pipeline_run_details import PipelineRunDetails
from .pipelines import collect_pipeline_spec_errors
from .pipeline_run_search import PipelineRunSearch
from .utils import dump_yaml

_TERMINAL_STATUSES = ("FAILED", "SYSTEM_ERROR", "CANCELLED", "CANCELED", "SKIPPED", "SUCCEEDED", "INVALID")
_ACTIVE_STATUSES = ("RUNNING", "CANCELLING", "CANCELING", "PENDING", "QUEUED")
_FAILURE_EARLY_EXIT_STATUSES = ("FAILED", "SYSTEM_ERROR")
_EXECUTION_STATE_TIMINGS_METADATA_KEY = "execution_state_timings"
_EXECUTION_STATE_TIMING_MONOTONIC_METADATA_KEY = "_execution_state_timing_monotonic"
_SUBMISSION_ID_ANNOTATION_KEY = "tangle-cli/submission-id"
_SUBMIT_RECOVERY_BACKOFF_SECONDS = (0.5, 1.0, 2.0, 4.0, 8.0, 16.0)
_DEFAULT_SUBMIT_RECOVERY_ATTEMPTS = 2


class PipelineRunError(RuntimeError):
    """Raised when a pipeline-run operation cannot complete."""


class UnsupportedPipelineRunFeatureError(PipelineRunError):
    """Raised for TD extension points intentionally unsupported in OSS defaults."""


class AmbiguousPipelineRunRecoveryError(PipelineRunError):
    """Raised when submit recovery finds multiple runs for one submission id."""


@dataclass
class PipelineSubmitPayload:
    """Prepared submit payload state before calling ``pipeline_runs_create``.

    This keeps the generic submit-body pipeline explicit: downstream hooks can
    adjust the spec, runtime arguments, run name, and annotations while callers
    still have one canonical body shape to submit.
    """

    prepared_spec: dict[str, Any]
    pipeline_spec: dict[str, Any]
    run_args: dict[str, Any] | None
    root_task: dict[str, Any]
    annotations: dict[str, str]
    run_name: str | None = None

    def to_body(self) -> dict[str, Any]:
        return {"root_task": self.root_task, "annotations": self.annotations}

    def sync_from_body(self, body: Mapping[str, Any]) -> None:
        """Refresh derived payload fields after in-place body normalization."""

        root_task = body.get("root_task")
        if isinstance(root_task, dict):
            self.root_task = root_task
        annotations = body.get("annotations")
        if isinstance(annotations, dict):
            self.annotations = {str(key): str(value) for key, value in annotations.items()}
        component_ref = self.root_task.get("componentRef") if isinstance(self.root_task, Mapping) else None
        submit_spec = component_ref.get("spec") if isinstance(component_ref, Mapping) else None
        if isinstance(submit_spec, dict):
            self.pipeline_spec = submit_spec
            run_name = submit_spec.get("name")
            self.run_name = run_name if isinstance(run_name, str) and run_name else None


@dataclass(frozen=True)
class PipelineWaitOutcome:
    """Normalized wait result attached to a run context.

    This is the generic OSS result boundary for wait lifecycle decisions.
    Downstreams can format legacy result dictionaries or notifications from
    this typed outcome without inventing their own metadata flags for success,
    timeout, failure counts, or fail-fast early exit.
    """

    status: str | None = None
    timed_out: bool = False
    early_exit: bool = False
    failed_count: int = 0
    error_count: int = 0
    elapsed_seconds: float = 0.0
    success_override: bool | None = None

    @property
    def success(self) -> bool | None:
        """Return generic success for completed waits, or None for timeout/unknown."""

        if self.success_override is not None:
            return self.success_override
        if self.timed_out:
            return None
        if self.early_exit or self.failed_count > 0 or self.error_count > 0:
            return False
        status = str(self.status or "").upper()
        if status == "SUCCEEDED":
            return True
        if status in _TERMINAL_STATUSES:
            return False
        return None

    @staticmethod
    def _count_statuses(status_counts: Mapping[str, Any], *statuses: str) -> int:
        total = 0
        for status in statuses:
            try:
                total += int(status_counts.get(status, 0) or 0)
            except (TypeError, ValueError):
                continue
        return total

    @classmethod
    def _success_override_from_counts(
        cls,
        status_counts: Mapping[str, Any],
        *,
        terminal: bool,
        total: int,
    ) -> bool | None:
        if not terminal or total <= 0:
            return None
        unsuccessful = cls._count_statuses(
            status_counts,
            "FAILED",
            "SYSTEM_ERROR",
            "CANCELLED",
            "CANCELED",
            "INVALID",
        )
        if unsuccessful > 0:
            return False
        terminal_count = cls._count_statuses(status_counts, *_TERMINAL_STATUSES)
        if terminal_count == total:
            return True
        return None

    @classmethod
    def from_poll_result(
        cls,
        poll: "PipelineWaitPoll",
        result: Mapping[str, Any],
    ) -> "PipelineWaitOutcome":
        """Build an outcome from a wait poll and public wait result."""

        timed_out = bool(result.get("timed_out"))
        early_exit = bool(result.get("early_exit"))
        success_override = cls._success_override_from_counts(
            poll.status_counts,
            terminal=poll.terminal and not timed_out,
            total=poll.total,
        )
        if early_exit and poll.total == 0:
            early_exit = False
            success_override = False
        return cls(
            status=str(result.get("status")) if result.get("status") is not None else poll.status,
            timed_out=timed_out,
            early_exit=early_exit,
            failed_count=int(poll.status_counts.get("FAILED", 0) or 0),
            error_count=int(poll.status_counts.get("SYSTEM_ERROR", 0) or 0),
            elapsed_seconds=poll.elapsed_seconds,
            success_override=success_override,
        )

    @classmethod
    def from_wait_result(
        cls,
        result: Mapping[str, Any],
        metadata: Mapping[str, Any] | None = None,
    ) -> "PipelineWaitOutcome":
        """Build an outcome from a public wait result and optional metadata."""

        source = metadata or result
        status = str(result.get("status")) if result.get("status") is not None else None
        timed_out = bool(result.get("timed_out") or source.get("timed_out"))
        early_exit = bool(result.get("early_exit") or source.get("early_exit"))
        status_counts = source.get("status_counts")
        status_counts = status_counts if isinstance(status_counts, Mapping) else {}
        total = 0
        for count in status_counts.values():
            try:
                total += int(count or 0)
            except (TypeError, ValueError):
                continue
        terminal = bool(status and (status.upper() == "ENDED" or status.upper() in _TERMINAL_STATUSES))
        success_override = cls._success_override_from_counts(
            status_counts,
            terminal=terminal and not timed_out,
            total=total,
        )
        if early_exit and total == 0:
            early_exit = False
            success_override = False
        failed_count = int(
            source.get(
                "failed_count",
                result.get("failed_count", cls._count_statuses(status_counts, "FAILED")),
            )
            or 0
        )
        error_count = int(
            source.get(
                "error_count",
                result.get("error_count", cls._count_statuses(status_counts, "SYSTEM_ERROR")),
            )
            or 0
        )
        return cls(
            status=status,
            timed_out=timed_out,
            early_exit=early_exit,
            failed_count=failed_count,
            error_count=error_count,
            elapsed_seconds=float(source.get("elapsed_seconds", 0.0) or 0.0),
            success_override=success_override,
        )


@dataclass
class PipelineRunContext:
    """First-class context for a pipeline run lifecycle.

    Downstreams can use this for mutex ownership, graceful-shutdown state,
    notifications, retries, and scheduled timeout bookkeeping without scraping
    transient manager attributes.

    Fields:
        run_id: Submitted pipeline run id, when an attempt reaches submit.
        run_name: Display/pipeline name derived from the submitted spec.
        root_execution_id: Root execution id returned by the submit API.
        pipeline_path: Source path or URI used for the run, when path-backed.
        start_time: Wall-clock attempt start time for downstream reporting.
        attempt: 1-based attempt number for submit/wait/retry lifecycle hooks.
        submit_body: Submit body for this attempt after normalization.
        pipeline_spec: Pipeline spec extracted from ``submit_body``.
        response: Submit API response for this attempt, when available.
        wait_outcome: Generic wait result for this attempt, when wait ran.
        previous_context: Previous attempt context, including attempts that
            failed during submit before a ``run_id`` existed. This is not just
            the previous successfully submitted run context.
        previous_error: Error from the previous attempt that caused this retry.
        carry_resource_to_retry: Generic resource/mutex handoff flag. Hooks set
            this directly when a resource should remain held for the replacement
            attempt. The current attempt's lifecycle context can then skip
            release, and the next attempt can inspect ``previous_context`` to
            reuse the carried resource.
        metadata: Extra hook-specific state carried through the lifecycle.
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
    wait_outcome: PipelineWaitOutcome | None = None
    previous_context: "PipelineRunContext | None" = None
    previous_error: Exception | None = None
    carry_resource_to_retry: bool = False
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
    execution_state_timings: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class PipelineRunHooks:
    """Overridable seams for downstream tangle-deploy behavior.

    Subclasses can override these methods to add provider-specific auth wrappers,
    cloud-object loading, JOB_CONFIG time input, run-as annotations,
    mutex/schedule behavior, graceful shutdown, notifications, hosted logs, or
    from-container runtime defaults without forking the generic pipeline-run manager.
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
        resolution_overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        client = getattr(self, "client", None)
        if client is None and hasattr(self, "_get_client"):
            client = self._get_client()
        if client is None:
            raise PipelineRunError("Failed to create TangleApiClient")
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

    def validate_pipeline_for_run(
        self,
        pipeline_spec: dict[str, Any],
        *,
        pipeline_path: str | Path | None,
        effective_path: str | Path | None,
        skip_validation: bool,
    ) -> list[str]:
        """Return submit-time validation errors for a prepared pipeline spec.

        The OSS default enforces the same local authoring validator used by
        ``tangle pipeline validate``. Downstreams can override or extend this
        hook with stricter schema/input validators.
        """

        del pipeline_path, effective_path
        if skip_validation:
            return []
        return collect_pipeline_spec_errors(pipeline_spec)

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
        """Return True to stop waiting before terminal/timeout.

        The generic fail-fast policy is opt-in via ``exit_on_first_failure``.
        Downstreams can set that flag when they want the wait loop to return as
        soon as a task fails, before the full graph reaches a terminal state.
        """

        if not context.metadata.get("exit_on_first_failure"):
            return False
        return any(int(poll.status_counts.get(status, 0) or 0) > 0 for status in _FAILURE_EARLY_EXIT_STATUSES)

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

    def wait_outcome(
        self,
        poll: PipelineWaitPoll,
        result: Mapping[str, Any],
        context: PipelineRunContext,
    ) -> PipelineWaitOutcome:
        """Return the typed wait outcome to attach to the run context."""

        del context
        return PipelineWaitOutcome.from_poll_result(poll, result)

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
class PipelineRunManager(TangleCliHandler):
    client: Any
    hooks: PipelineRunHooks = field(default_factory=PipelineRunHooks)
    logger: Logger = field(default_factory=get_default_logger)
    base_url: str | None = None

    def __post_init__(self) -> None:
        TangleCliHandler.__init__(
            self,
            client=self.client,
            logger=self.logger,
            base_url=self.base_url,
        )
        if self.hooks is not self:
            setattr(self.hooks, "client", self.client)

    def _http_error_type(self) -> type[Exception]:
        return PipelineRunError

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
    def normalize_submit_body_in_place(body: dict[str, Any]) -> dict[str, Any]:
        """Normalize a submit body in place and return it.

        This is the mutable counterpart to :meth:`sanitize_submit_payload` for
        callers that already have a body object.  It keeps component-ref text
        normalization and submit-only field stripping in the OSS submit layer,
        instead of requiring downstream runners to patch bodies before submit.
        """

        sanitized = PipelineRunManager.sanitize_submit_payload(body)
        if not isinstance(sanitized, dict):
            raise PipelineRunError("submit body must be a mapping")
        body.clear()
        body.update(sanitized)
        return body

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
    def execution_status_counts_from_graph_state(graph_state: Mapping[str, Any] | Any) -> dict[str, dict[str, int]]:
        """Return per-execution status counts from a graph-state response."""

        child_stats = (
            graph_state.get("child_execution_status_stats")
            if isinstance(graph_state, Mapping)
            else getattr(graph_state, "child_execution_status_stats", None)
        )
        child_counts = PipelineRunManager._counts_mapping(child_stats)
        if child_counts is None:
            return {}
        result: dict[str, dict[str, int]] = {}
        for execution_id, stats in child_counts.items():
            counts = PipelineRunManager._counts_mapping(stats)
            if counts is None:
                continue
            status_counts: dict[str, int] = {}
            for status, count in counts.items():
                try:
                    status_counts[str(status).upper()] = int(count or 0)
                except (TypeError, ValueError):
                    continue
            result[str(execution_id)] = status_counts
        return result

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

    @staticmethod
    def _accepts_client_keyword(method: Any) -> bool:
        try:
            parameters = inspect.signature(method).parameters
        except (TypeError, ValueError):
            return False
        return "client" in parameters or any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )

    @staticmethod
    def _raise_pipeline_validation_error(validation_errors: list[str]) -> None:
        if validation_errors:
            raise PipelineRunError("Pipeline validation failed:\n  - " + "\n  - ".join(validation_errors))

    def load_pipeline_for_submit(
        self,
        pipeline_path: str | Path,
        *,
        hydrate: bool = True,
        resolution_overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if hydrate:
            hydrate_pipeline = self.hooks.hydrate_pipeline
            hydrate_kwargs: dict[str, Any] = {"resolution_overrides": resolution_overrides}
            if self._accepts_client_keyword(hydrate_pipeline):
                hydrate_kwargs["client"] = self._get_client()
            return hydrate_pipeline(pipeline_path, **hydrate_kwargs)
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

    @staticmethod
    def _resolve_fallback_pipeline_name(
        pipeline_spec: dict[str, Any],
        fallback_name: str | None,
    ) -> dict[str, Any]:
        """Fill a missing pipeline ``name`` from a caller-resolved fallback.

        A file-based submit whose spec omits a usable ``name`` still validates
        and runs under a fallback (historically the source-file stem), matching
        the long-standing downstream behavior. A declared non-blank ``name``
        always wins; a missing, non-string, or whitespace-only ``name`` is
        treated as genuinely nameless and takes the fallback, so other
        structural errors still surface at validation. A no-op when the spec is
        not a mapping or no fallback name is available.
        """
        if not isinstance(pipeline_spec, dict):
            return pipeline_spec
        name = pipeline_spec.get("name")
        if isinstance(name, str) and name.strip():
            return pipeline_spec
        if not (isinstance(fallback_name, str) and fallback_name):
            return pipeline_spec
        return {**pipeline_spec, "name": fallback_name}

    def prepare_submit_payload_from_spec(
        self,
        pipeline_spec: dict[str, Any],
        *,
        run_args: dict[str, Any] | None = None,
        annotations: dict[str, str] | None = None,
        pipeline_path: str | Path | None = None,
        run_as: str | None = None,
        hydrate: bool = True,
        skip_validation: bool = False,
    ) -> PipelineSubmitPayload:
        """Prepare the generic submit payload from a pipeline spec.

        The order here is the submit-body contract shared by OSS and TD:
        prepare the spec, prepare runtime arguments, expand run-name templates,
        validate the prepared authoring spec, convert/sanitize the payload, then
        merge downstream/default annotations before caller-supplied annotations
        override them.
        """

        prepared_spec = self.prepare_pipeline_spec_for_submit(
            pipeline_spec,
            pipeline_path=pipeline_path,
            run_args=run_args,
            hydrate=hydrate,
        )
        prepared_run_args = self.hooks.prepare_run_arguments(prepared_spec, run_args)
        prepared_spec = self.apply_run_name_template(prepared_spec, prepared_run_args)
        prepared_spec = self._resolve_fallback_pipeline_name(
            prepared_spec,
            Path(str(pipeline_path)).stem if pipeline_path is not None else None,
        )
        if not skip_validation:
            validation_errors = self.hooks.validate_pipeline_for_run(
                prepared_spec,
                pipeline_path=pipeline_path,
                effective_path=None,
                skip_validation=False,
            )
            self._raise_pipeline_validation_error(validation_errors)
        payload = self.convert_yaml_to_payload(copy.deepcopy(prepared_spec), prepared_run_args)
        payload = self.sanitize_submit_payload(payload)
        root_task = payload["root_task"]
        component_ref = root_task.get("componentRef") if isinstance(root_task, Mapping) else None
        submit_spec = (
            component_ref.get("spec")
            if isinstance(component_ref, Mapping) and isinstance(component_ref.get("spec"), dict)
            else prepared_spec
        )
        submit_annotations = self.hooks.extra_submit_annotations(
            pipeline_spec=prepared_spec,
            pipeline_path=pipeline_path,
            run_as=run_as,
        )
        if annotations:
            submit_annotations.update({str(k): str(v) for k, v in annotations.items()})
        run_name = submit_spec.get("name")
        return PipelineSubmitPayload(
            prepared_spec=prepared_spec,
            pipeline_spec=submit_spec,
            run_args=prepared_run_args,
            root_task=root_task,
            annotations=submit_annotations,
            run_name=run_name if isinstance(run_name, str) and run_name else None,
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
        skip_validation: bool = False,
    ) -> dict[str, Any]:
        """Build a submit body from an already-prepared pipeline spec."""

        return self.prepare_submit_payload_from_spec(
            pipeline_spec,
            run_args=run_args,
            annotations=annotations,
            pipeline_path=pipeline_path,
            run_as=run_as,
            hydrate=hydrate,
            skip_validation=skip_validation,
        ).to_body()

    def prepare_submit_payload(
        self,
        pipeline_path: str | Path,
        *,
        run_args: dict[str, Any] | None = None,
        annotations: dict[str, str] | None = None,
        hydrate: bool = True,
        run_as: str | None = None,
        resolution_overrides: dict[str, Any] | None = None,
        skip_validation: bool = False,
    ) -> PipelineSubmitPayload:
        pipeline_spec = self.load_pipeline_for_submit(
            pipeline_path,
            hydrate=hydrate,
            resolution_overrides=resolution_overrides,
        )
        return self.prepare_submit_payload_from_spec(
            pipeline_spec,
            run_args=run_args,
            annotations=annotations,
            pipeline_path=pipeline_path,
            run_as=run_as,
            hydrate=hydrate,
            skip_validation=skip_validation,
        )

    def build_submit_body(
        self,
        pipeline_path: str | Path,
        *,
        run_args: dict[str, Any] | None = None,
        annotations: dict[str, str] | None = None,
        hydrate: bool = True,
        run_as: str | None = None,
        resolution_overrides: dict[str, Any] | None = None,
        skip_validation: bool = False,
    ) -> dict[str, Any]:
        return self.prepare_submit_payload(
            pipeline_path,
            run_args=run_args,
            annotations=annotations,
            hydrate=hydrate,
            run_as=run_as,
            resolution_overrides=resolution_overrides,
            skip_validation=skip_validation,
        ).to_body()

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
            run_name=run_name if isinstance(run_name, str) and run_name else None,
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
        notify_submit_error: bool = True,
    ) -> dict[str, Any]:
        self.normalize_submit_body_in_place(body)
        pipeline_spec = body["root_task"]["componentRef"]["spec"]
        submit_context = context or PipelineRunContext(
            pipeline_path=pipeline_path,
            start_time=time.time(),
            attempt=attempt,
        )
        spec_name = pipeline_spec.get("name") if isinstance(pipeline_spec, dict) else None
        submit_context.run_name = spec_name if isinstance(spec_name, str) and spec_name else None
        submit_context.pipeline_path = pipeline_path
        submit_context.attempt = attempt
        submit_context.submit_body = body
        submit_context.pipeline_spec = pipeline_spec if isinstance(pipeline_spec, dict) else None
        self.hooks.before_submit_context(submit_context)
        client = self._require_client()
        try:
            with self._surface_http_errors():
                response = self.to_plain(client.pipeline_runs_create(body=body))
        except Exception as exc:
            if notify_submit_error:
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

    def submit_prepared_payload(
        self,
        payload: PipelineSubmitPayload,
        *,
        pipeline_path: str | Path | None = None,
        attempt: int = 1,
        context: PipelineRunContext | None = None,
    ) -> dict[str, Any]:
        body = payload.to_body()
        response = self.submit_prepared_body(
            body,
            pipeline_path=pipeline_path,
            attempt=attempt,
            context=context,
        )
        payload.sync_from_body(body)
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
        skip_validation: bool = False,
    ) -> dict[str, Any]:
        payload = self.prepare_submit_payload_from_spec(
            pipeline_spec,
            run_args=run_args,
            annotations=annotations,
            pipeline_path=pipeline_path,
            run_as=run_as,
            hydrate=hydrate,
            skip_validation=skip_validation,
        )
        return self.submit_prepared_payload(payload, pipeline_path=pipeline_path, attempt=attempt)

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
        skip_validation: bool = False,
    ) -> dict[str, Any]:
        payload = self.prepare_submit_payload(
            pipeline_path,
            run_args=run_args,
            annotations=annotations,
            hydrate=hydrate,
            run_as=run_as,
            resolution_overrides=resolution_overrides,
            skip_validation=skip_validation,
        )
        return self.submit_prepared_payload(payload, pipeline_path=pipeline_path, attempt=attempt)

    def get_run(self, run_id: str, *, include_execution_stats: bool = True) -> dict[str, Any]:
        with self._surface_http_errors():
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
        with self._surface_http_errors():
            return PipelineRunDetails(client=self.client).get_run_details_output(
                run_id,
                include_implementations=include_implementations,
                include_annotations=include_annotations,
                include_execution_state=include_execution_state,
                execution_id=execution_id,
            )

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        with self._surface_http_errors():
            cancelled = self.to_plain(self.client.pipeline_runs_cancel(run_id))
        return cancelled or {"id": run_id, "cancelled": True}

    def graph_state(self, execution_id: str) -> Mapping[str, Any] | Any:
        with self._surface_http_errors():
            graph_state = self.client.executions_graph_execution_state(execution_id)
        return self.to_plain(graph_state)

    def graph_state_output(self, run_ids: list[str], *, timeout: float = 30.0) -> dict[str, Any]:
        # Per-run failures are reported in each result's "error" field rather
        # than raised, so no HTTP-error surfacing is needed at this boundary.
        return PipelineRunDetails(client=self.client).get_graph_state_output(run_ids, timeout=timeout)

    def logs(self, execution_id: str) -> dict[str, Any]:
        with self._surface_http_errors():
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
        with self._surface_http_errors():
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
        with self._surface_http_errors():
            return PipelineRunSearch(client=self.client, logger=self.logger).search(
                name=name,
                created_by=created_by,
                annotations=annotations,
                start_date=start_date,
                end_date=end_date,
                local_time=local_time,
                query=query,
                limit=limit,
                page_token=page_token,
            )

    def export_run(
        self,
        run_id: str,
        output: str | Path | None = None,
        *,
        dehydrate: bool = False,
    ) -> dict[str, Any]:
        with self._surface_http_errors():
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
        if not isinstance(spec, dict) or not spec:
            raise PipelineRunError(f"Pipeline spec for run {run_id} is not exportable")
        if dehydrate and output is None:
            raise PipelineRunError("--dehydrate requires --output")
        if dehydrate:
            with self._surface_http_errors():
                spec = PipelineDehydrator(
                    remembered_choices={"": DehydrateChoice.AUTO},
                    output_file=output,
                    client=self.client,
                    logger=self.logger,
                ).dehydrate(spec)
        content = dump_yaml(spec)
        if output is None:
            return {"run_id": run_id, "pipeline": spec, "yaml": content, "dehydrated": dehydrate}
        output_path = Path(output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")

        result = {"run_id": run_id, "output": str(output_path), "dehydrated": dehydrate}
        arguments = self.to_plain(getattr(task_spec, "arguments", None) or {})
        if not arguments and isinstance(raw, Mapping):
            arguments = self.to_plain(raw.get("arguments") or {})
        if isinstance(arguments, Mapping) and (arguments or dehydrate):
            config_path = output_path.parent / f"{output_path.stem}.config.yaml"
            config_data: dict[str, Any] = {"pipeline_path": output_path.name}
            if dehydrate:
                config_data["hydrate"] = True
            if arguments:
                config_data["args"] = dict(arguments)
            config_path.write_text(dump_yaml(config_data), encoding="utf-8")
            result["config_path"] = str(config_path)
        return result

    def _update_execution_state_timings(
        self,
        context: PipelineRunContext,
        graph_state: Mapping[str, Any] | Any,
    ) -> dict[str, dict[str, Any]]:
        """Track how long each execution has stayed in its observed state."""

        execution_status_counts = self.execution_status_counts_from_graph_state(graph_state)
        if not execution_status_counts:
            context.metadata[_EXECUTION_STATE_TIMINGS_METADATA_KEY] = {}
            context.metadata[_EXECUTION_STATE_TIMING_MONOTONIC_METADATA_KEY] = {}
            return {}

        existing_value = context.metadata.get(_EXECUTION_STATE_TIMINGS_METADATA_KEY)
        existing = existing_value if isinstance(existing_value, Mapping) else {}
        monotonic_value = context.metadata.get(_EXECUTION_STATE_TIMING_MONOTONIC_METADATA_KEY)
        monotonic_state_entered = monotonic_value if isinstance(monotonic_value, Mapping) else {}
        now_wall = time.time()
        now_monotonic = time.monotonic()
        timings: dict[str, dict[str, Any]] = {}
        next_monotonic_state_entered: dict[str, float] = {}

        for execution_id, status_counts in execution_status_counts.items():
            state = self.status_from_counts(status_counts) or "UNKNOWN"
            existing_record = existing.get(execution_id)
            previous = existing_record if isinstance(existing_record, Mapping) else {}
            previous_state = previous.get("state")
            if previous_state == state:
                try:
                    state_entered_at = float(previous.get("state_entered_at", now_wall))
                except (TypeError, ValueError):
                    state_entered_at = now_wall
                try:
                    state_entered_monotonic = float(monotonic_state_entered.get(execution_id, now_monotonic))
                except (TypeError, ValueError):
                    state_entered_monotonic = now_monotonic
            else:
                state_entered_at = now_wall
                state_entered_monotonic = now_monotonic

            timings[execution_id] = {
                "state": state,
                "state_entered_at": state_entered_at,
                "elapsed_seconds": max(0.0, now_monotonic - state_entered_monotonic),
                "last_observed_at": now_wall,
            }
            next_monotonic_state_entered[execution_id] = state_entered_monotonic

        context.metadata[_EXECUTION_STATE_TIMINGS_METADATA_KEY] = timings
        context.metadata[_EXECUTION_STATE_TIMING_MONOTONIC_METADATA_KEY] = next_monotonic_state_entered
        return copy.deepcopy(timings)

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
        execution_state_timings: dict[str, dict[str, Any]] = {}
        status_counts = self.status_counts_from_run(run)
        if use_graph_state:
            root_execution_id = self.hooks.graph_state_execution_id(run, wait_context)
            if root_execution_id:
                graph_state = self.graph_state(str(root_execution_id))
                graph_counts = self.status_counts_from_graph_state(graph_state)
                if graph_counts:
                    status_counts = graph_counts
                execution_state_timings = self._update_execution_state_timings(wait_context, graph_state)
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
            execution_state_timings=execution_state_timings,
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
        exit_on_first_failure: bool = False,
    ) -> dict[str, Any]:
        wait_context = context or PipelineRunContext(run_id=run_id, start_time=time.time())
        if exit_on_first_failure:
            wait_context.metadata["exit_on_first_failure"] = True
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
                wait_context.metadata["wait_result"] = self._wait_metadata(poll)
                self.hooks.on_terminal(poll, wait_context)
                result = self._wait_result(poll, timed_out=False)
                self._record_wait_outcome(wait_context, poll, result)
                self.hooks.after_wait_context(result, wait_context)
                return result
            if self.hooks.should_exit_early(poll, wait_context):
                wait_context.metadata["wait_result"] = self._wait_metadata(poll, early_exit=True)
                self.hooks.on_early_exit_before_release(poll, wait_context)
                result = self._wait_result(poll, timed_out=False, early_exit=True)
                self._record_wait_outcome(wait_context, poll, result)
                self.hooks.after_wait_context(result, wait_context)
                return result
            if deadline is not None and deadline_now() >= deadline:
                wait_context.metadata["wait_result"] = self._wait_metadata(poll, timed_out=True)
                self.hooks.on_timeout(poll, wait_context)
                result = self._wait_result(poll, timed_out=True)
                self._record_wait_outcome(wait_context, poll, result)
                self.hooks.after_wait_context(result, wait_context)
                return result
            if deadline is None:
                sleep_for = poll_interval
            else:
                sleep_for = min(poll_interval, max(0.0, deadline - deadline_now()))
            time.sleep(sleep_for)
        if last_poll is None:  # pragma: no cover - defensive, loop always polls first
            raise PipelineRunError(f"No status returned for run {run_id}")

    @staticmethod
    def _wait_metadata(
        poll: PipelineWaitPoll,
        *,
        timed_out: bool = False,
        early_exit: bool = False,
    ) -> dict[str, Any]:
        failed_count = int(poll.status_counts.get("FAILED", 0) or 0)
        error_count = int(poll.status_counts.get("SYSTEM_ERROR", 0) or 0)
        metadata: dict[str, Any] = {
            "status_counts": dict(poll.status_counts),
            "failed_count": failed_count,
            "error_count": error_count,
            "elapsed_seconds": poll.elapsed_seconds,
        }
        if timed_out:
            metadata["timed_out"] = True
        if early_exit:
            metadata["early_exit"] = True
        return metadata

    def _record_wait_outcome(
        self,
        context: PipelineRunContext,
        poll: PipelineWaitPoll,
        result: Mapping[str, Any],
    ) -> None:
        context.wait_outcome = self.hooks.wait_outcome(poll, result, context)

    @staticmethod
    def _wait_result(
        poll: PipelineWaitPoll,
        *,
        timed_out: bool,
        early_exit: bool = False,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "run": poll.run,
            "status": poll.status,
            "timed_out": timed_out,
        }
        if early_exit or timed_out:
            result.update(PipelineRunManager._wait_metadata(poll, timed_out=timed_out, early_exit=early_exit))
        if early_exit:
            result["early_exit"] = True
        return result

    @staticmethod
    def _ensure_submission_id_annotation(body: dict[str, Any]) -> str:
        annotations = body.setdefault("annotations", {})
        if not isinstance(annotations, dict):
            annotations = {}
            body["annotations"] = annotations
        submission_id = annotations.get(_SUBMISSION_ID_ANNOTATION_KEY)
        if submission_id:
            annotations[_SUBMISSION_ID_ANNOTATION_KEY] = str(submission_id)
            return str(submission_id)
        submission_id = uuid.uuid4().hex
        annotations[_SUBMISSION_ID_ANNOTATION_KEY] = submission_id
        return submission_id

    @staticmethod
    def _submission_id_from_body(body: Mapping[str, Any]) -> str | None:
        annotations = body.get("annotations")
        if not isinstance(annotations, Mapping):
            return None
        submission_id = annotations.get(_SUBMISSION_ID_ANNOTATION_KEY)
        return str(submission_id) if submission_id else None

    @staticmethod
    def _submit_recovery_backoff_seconds(submit_recovery_attempts: int) -> tuple[float, ...]:
        attempt_count = max(0, min(int(submit_recovery_attempts), len(_SUBMIT_RECOVERY_BACKOFF_SECONDS)))
        return _SUBMIT_RECOVERY_BACKOFF_SECONDS[:attempt_count]

    def _submitted_runs_for_submission_id(self, submission_id: str) -> list[dict[str, Any]]:
        query = {
            "and": [
                PipelineRunSearch.build_value_equals(
                    key=_SUBMISSION_ID_ANNOTATION_KEY,
                    value=submission_id,
                )
            ]
        }
        response = self._require_client().pipeline_runs_list(
            filter_query=json.dumps(query, separators=(",", ":")),
            include_pipeline_names=True,
        )
        plain = self.to_plain(response)
        if not isinstance(plain, Mapping):
            return []
        runs = plain.get("pipeline_runs")
        if not isinstance(runs, list):
            return []
        return [dict(run) for run in runs if isinstance(run, Mapping)]

    def _recover_submitted_run_after_submit_error(
        self,
        *,
        submission_id: str | None,
        submit_recovery_attempts: int = _DEFAULT_SUBMIT_RECOVERY_ATTEMPTS,
    ) -> dict[str, Any] | None:
        if not submission_id:
            return None
        backoff_seconds = self._submit_recovery_backoff_seconds(submit_recovery_attempts)
        total_lookup_attempts = len(backoff_seconds)
        if total_lookup_attempts == 0:
            self.logger.warn(
                "Submit recovery lookup disabled "
                f"({_SUBMISSION_ID_ANNOTATION_KEY}={submission_id}, "
                f"submit_recovery_attempts={submit_recovery_attempts}); "
                "resubmitting the same frozen body with preserved inputs."
            )
            return None
        for lookup_attempt, delay_seconds in enumerate(backoff_seconds, start=1):
            self.logger.info(
                "Waiting "
                f"{delay_seconds:g}s before checking whether failed submit already created a pipeline run "
                f"({_SUBMISSION_ID_ANNOTATION_KEY}={submission_id}, "
                f"lookup_attempt={lookup_attempt}/{total_lookup_attempts})"
            )
            time.sleep(delay_seconds)
            self.logger.info(
                "Checking whether failed submit already created a pipeline run "
                f"({_SUBMISSION_ID_ANNOTATION_KEY}={submission_id}, "
                f"lookup_attempt={lookup_attempt}/{total_lookup_attempts})"
            )
            try:
                matches = self._submitted_runs_for_submission_id(submission_id)
            except Exception as exc:
                self.logger.warn(
                    "Submit recovery lookup failed "
                    f"({_SUBMISSION_ID_ANNOTATION_KEY}={submission_id}): {exc}. "
                    "Falling back to resubmitting the same frozen body."
                )
                return None
            self.logger.info(
                "Submit recovery lookup matched "
                f"{len(matches)} run(s) for {_SUBMISSION_ID_ANNOTATION_KEY}={submission_id}"
            )
            if len(matches) == 1:
                run = matches[0]
                run_id = run.get("id")
                root_execution_id = run.get("root_execution_id")
                self.logger.info(
                    "Recovered existing pipeline run "
                    f"run_id={run_id}, root_execution_id={root_execution_id}, "
                    f"{_SUBMISSION_ID_ANNOTATION_KEY}={submission_id}; adopting instead of resubmitting."
                )
                return run
            if len(matches) > 1:
                run_ids = [str(run.get("id")) for run in matches if run.get("id") is not None]
                self.logger.warn(
                    "Submit recovery lookup was ambiguous "
                    f"({_SUBMISSION_ID_ANNOTATION_KEY}={submission_id}, matched_run_ids={run_ids}). "
                    "Refusing to submit a duplicate."
                )
                raise AmbiguousPipelineRunRecoveryError(
                    "Found multiple pipeline runs for failed submit recovery "
                    f"{_SUBMISSION_ID_ANNOTATION_KEY}={submission_id}: {', '.join(run_ids) or matches!r}. "
                    "Refusing to submit a duplicate."
                )
        self.logger.warn(
            "No existing pipeline run found after submit failure "
            f"({_SUBMISSION_ID_ANNOTATION_KEY}={submission_id}); "
            "resubmitting the same frozen body with preserved inputs."
        )
        return None

    def _adopt_submitted_run(
        self,
        *,
        response: Mapping[str, Any],
        body: dict[str, Any],
        pipeline_path: str | Path | None,
        attempt: int,
        context: PipelineRunContext,
    ) -> dict[str, Any]:
        response_dict = dict(response)
        submitted_context = self.response_run_context(
            response_dict,
            submit_body=body,
            pipeline_path=pipeline_path,
            attempt=attempt,
        )
        context.run_id = submitted_context.run_id
        context.run_name = submitted_context.run_name
        context.root_execution_id = submitted_context.root_execution_id
        context.submit_body = submitted_context.submit_body
        context.pipeline_spec = submitted_context.pipeline_spec
        context.response = response_dict
        context.metadata["recovered_after_submit_error"] = True
        self.hooks.after_submit_context(context)
        return response_dict

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
        exit_on_first_failure: bool = False,
        metadata: dict[str, Any] | None = None,
        submit_recovery_attempts: int = _DEFAULT_SUBMIT_RECOVERY_ATTEMPTS,
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
                previous_context=previous_context,
                previous_error=last_error,
                metadata=dict(metadata or {}),
            )
            lifecycle_started = False
            success = False
            error: Exception | None = None
            retry_requested = False
            reused_after_submit_failure = (
                previous_context is not None
                and previous_context.run_id is None
                and previous_context.submit_body is not None
            )
            if reused_after_submit_failure:
                # The previous attempt failed while submitting, before the API
                # returned a run id. Retry the exact same submit body instead
                # of rebuilding it: body construction can intentionally inject
                # dynamic inputs (for example a scheduler creation timestamp),
                # and changing those inputs on an ambiguous submit timeout can
                # defeat cache reuse or double-run the logical pipeline.
                body = copy.deepcopy(previous_context.submit_body)
                self.logger.info(
                    "Retrying submit after submit exception with the same frozen body "
                    f"({_SUBMISSION_ID_ANNOTATION_KEY}={self._submission_id_from_body(body)}); "
                    "dynamic inputs are preserved."
                )
            else:
                if previous_context is not None:
                    self.logger.info(
                        "Retrying after pipeline failure; rebuilding submit body so dynamic run arguments "
                        "can follow hook policy (for example update-vs-fixed time input)."
                    )
                body = body_factory(attempt, previous_context, last_error)
            self.normalize_submit_body_in_place(body)
            submission_id = self._ensure_submission_id_annotation(body)
            context.metadata["submission_id"] = submission_id
            if metadata_factory is not None:
                context.metadata.update(metadata_factory(attempt, previous_context, last_error))
            pipeline_spec = body.get("root_task", {}).get("componentRef", {}).get("spec")
            context.submit_body = body
            context.pipeline_spec = pipeline_spec if isinstance(pipeline_spec, dict) else None
            if context.pipeline_spec is not None:
                spec_name = context.pipeline_spec.get("name")
                if isinstance(spec_name, str) and spec_name:
                    context.run_name = spec_name
            self.hooks.before_run_lifecycle(context)
            lifecycle_started = True
            attempts.append(context)
            # ``previous_context`` tracks the previous attempt, not only the
            # previous successfully submitted run.  Resource-carry hooks need to
            # hand off mutexes/leases even when an attempt fails during submit
            # before a run id is available.
            previous_context = context
            try:
                with self.hooks.around_run(context):
                    try:
                        recovered_response = None
                        if reused_after_submit_failure:
                            recovered_response = self._recover_submitted_run_after_submit_error(
                                submission_id=self._submission_id_from_body(body),
                                submit_recovery_attempts=submit_recovery_attempts,
                            )
                        if recovered_response is not None:
                            response = self._adopt_submitted_run(
                                response=recovered_response,
                                body=body,
                                pipeline_path=pipeline_path,
                                attempt=attempt,
                                context=context,
                            )
                            if attempt > 1:
                                self.hooks.after_retry_submit(context)
                        else:
                            try:
                                response = self.submit_prepared_body(
                                    body,
                                    pipeline_path=pipeline_path,
                                    attempt=attempt,
                                    context=context,
                                    notify_submit_error=False,
                                )
                            except Exception as submit_exc:
                                if context.run_id is not None:
                                    raise
                                submission_id_for_recovery = self._submission_id_from_body(body)
                                self.logger.warn(
                                    "Submit failed before a run id was returned "
                                    f"({_SUBMISSION_ID_ANNOTATION_KEY}={submission_id_for_recovery}): "
                                    f"{submit_exc}. Checking whether the run was actually created."
                                )
                                recovered_response = self._recover_submitted_run_after_submit_error(
                                    submission_id=submission_id_for_recovery,
                                    submit_recovery_attempts=submit_recovery_attempts,
                                )
                                if recovered_response is None:
                                    self.hooks.on_submit_error(submit_exc, context=context)
                                    raise
                                response = self._adopt_submitted_run(
                                    response=recovered_response,
                                    body=body,
                                    pipeline_path=pipeline_path,
                                    attempt=attempt,
                                    context=context,
                                )
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
                                exit_on_first_failure=exit_on_first_failure,
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
                        if isinstance(exc, AmbiguousPipelineRunRecoveryError):
                            self.hooks.on_fail_fast_before_release(context, exc)
                            raise
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
        exit_on_first_failure: bool = False,
        metadata: dict[str, Any] | None = None,
        submit_recovery_attempts: int = _DEFAULT_SUBMIT_RECOVERY_ATTEMPTS,
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
            exit_on_first_failure=exit_on_first_failure,
            metadata=metadata,
            submit_recovery_attempts=submit_recovery_attempts,
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
        exit_on_first_failure: bool = False,
        metadata: dict[str, Any] | None = None,
        submit_recovery_attempts: int = _DEFAULT_SUBMIT_RECOVERY_ATTEMPTS,
        skip_validation: bool = False,
    ) -> dict[str, Any]:
        """Submit/wait/retry an already hydrated/validated in-memory spec."""

        def body_factory(
            _attempt: int,
            _previous_context: PipelineRunContext | None,
            _error: Exception | None,
        ) -> dict[str, Any]:
            return self.prepare_submit_payload_from_spec(
                copy.deepcopy(pipeline_spec),
                run_args=run_args,
                annotations=annotations,
                pipeline_path=pipeline_path,
                run_as=run_as,
                hydrate=hydrate,
                skip_validation=skip_validation,
            ).to_body()

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
            exit_on_first_failure=exit_on_first_failure,
            metadata=metadata,
            submit_recovery_attempts=submit_recovery_attempts,
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
        exit_on_first_failure: bool = False,
        metadata: dict[str, Any] | None = None,
        submit_recovery_attempts: int = _DEFAULT_SUBMIT_RECOVERY_ATTEMPTS,
        skip_validation: bool = False,
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
            return self.prepare_submit_payload(
                pipeline_path,
                run_args=run_args,
                annotations=annotations,
                hydrate=hydrate,
                run_as=run_as,
                resolution_overrides=resolution_overrides,
                skip_validation=skip_validation,
            ).to_body()

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
            exit_on_first_failure=exit_on_first_failure,
            metadata=metadata,
            submit_recovery_attempts=submit_recovery_attempts,
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


def parse_json_or_key_values(
    text: str | Mapping[str, Any] | None,
    entries: list[str] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if text:
        loaded = dict(text) if isinstance(text, Mapping) else json.loads(text)
        if not isinstance(loaded, dict):
            raise PipelineRunError("JSON value must be an object")
        result.update(loaded)
    result.update(parse_key_value_entries(entries))
    return result
