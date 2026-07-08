"""High-level OSS pipeline-run orchestration.

This module owns the generic path-based run flow that downstream CLIs can share:
load/hydrate a pipeline, perform generic pre-submit preparation, optionally
layout/validate, then submit/wait/retry through :mod:`tangle_cli.pipeline_run_manager`.
Downstream-specific behavior (provider auth, cloud-object I/O, hosted logs,
notifications, mutexes, schedulers, service-account annotations, and legacy
result shapes) is exposed as hooks rather than imported here.
"""

from __future__ import annotations

import copy
import inspect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .pipeline_run_manager import (
    PipelineRunContext,
    PipelineRunError,
    PipelineRunHooks,
    PipelineRunManager,
    PipelineSubmitPayload,
    PipelineWaitOutcome,
)


@dataclass(frozen=True)
class PipelinePreparationResult:
    """Prepared pipeline state before submit/wait orchestration."""

    pipeline_spec: dict[str, Any]
    pipeline_name: str
    effective_path: str | Path | None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineRunnerHooks(PipelineRunHooks):
    """Extension seams for high-level pipeline-run orchestration.

    ``PipelineRunHooks`` already covers submit/wait/retry lifecycle behavior.
    This subclass adds path/spec preparation seams so downstreams can keep their
    platform-specific behavior outside the generic OSS runner.
    """

    def initial_pipeline_name(self, pipeline_path: str | Path) -> str:
        """Return the fallback display/run name before the spec is loaded."""

        return Path(str(pipeline_path)).stem

    def load_pipeline(self, pipeline_path: str | Path) -> dict[str, Any]:
        """Load an unhydrated pipeline spec.

        The default delegates to ``read_pipeline_yaml`` from ``PipelineRunHooks``.
        Downstreams can override this for alternate URI schemes such as gs://.
        """

        return self.read_pipeline_yaml(pipeline_path)

    def hydrate_pipeline_for_run(
        self,
        pipeline_path: str | Path,
        *,
        client: Any | None = None,
        resolution_overrides: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], str | Path | None]:
        """Hydrate a pipeline path for a run.

        Returns the hydrated spec and an optional effective path.  The effective
        path is the location layout/validation should use when hydration writes
        to a temporary file.  OSS hydration is in-memory by default.
        """

        hydrate_kwargs: dict[str, Any] = {"resolution_overrides": resolution_overrides}
        try:
            parameters = inspect.signature(self.hydrate_pipeline).parameters
        except (TypeError, ValueError):
            parameters = {}
        if client is not None and (
            "client" in parameters
            or any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values())
        ):
            hydrate_kwargs["client"] = client

        return (
            self.hydrate_pipeline(
                pipeline_path,
                **hydrate_kwargs,
            ),
            None,
        )

    def prepare_loaded_pipeline_spec(
        self,
        pipeline_spec: dict[str, Any],
        *,
        pipeline_path: str | Path,
        effective_path: str | Path | None,
        hydrate: bool,
        run_args: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Transform a loaded/hydrated spec before validation/layout.

        Use this for downstream template post-processing that is not specific to
        submit payload construction.
        """

        return pipeline_spec

    def validate_pipeline_for_run(
        self,
        pipeline_spec: dict[str, Any],
        *,
        pipeline_path: str | Path,
        effective_path: str | Path | None,
        skip_validation: bool,
    ) -> list[str]:
        """Return validation errors for a prepared pipeline spec.

        The OSS default intentionally does not enforce the local authoring
        validator here: submit-time API validation remains the source of truth,
        while downstreams can plug in stricter schema/input validators.
        """

        del pipeline_spec, pipeline_path, effective_path, skip_validation
        return []

    def has_layout(self, pipeline_spec: Mapping[str, Any]) -> bool:
        """Return True when a pipeline graph already has non-zero coordinates."""

        tasks = (
            pipeline_spec.get("implementation", {})
            .get("graph", {})
            .get("tasks", {})
        )
        if not tasks:
            return True

        for task in tasks.values() if isinstance(tasks, Mapping) else []:
            if not isinstance(task, Mapping):
                continue
            annotations = task.get("annotations", {})
            position = annotations.get("editor.position") if isinstance(annotations, Mapping) else None
            if isinstance(position, str):
                try:
                    import json

                    parsed = json.loads(position)
                except (TypeError, ValueError):
                    parsed = None
                if isinstance(parsed, Mapping) and (parsed.get("x", 0) != 0 or parsed.get("y", 0) != 0):
                    return True
            component_ref = task.get("componentRef", {})
            nested_spec = component_ref.get("spec") if isinstance(component_ref, Mapping) else None
            if isinstance(nested_spec, Mapping) and not self.has_layout(nested_spec):
                return False

        return False

    def should_apply_layout(
        self,
        pipeline_spec: dict[str, Any],
        *,
        pipeline_path: str | Path,
        effective_path: str | Path | None,
        skip_layout: bool,
        force_layout: bool,
        layout_algorithm: str | None,
    ) -> bool:
        """Return True when the runner should layout before submit."""

        del pipeline_path, effective_path, layout_algorithm
        return not skip_layout and (force_layout or not self.has_layout(pipeline_spec))

    def apply_layout(
        self,
        pipeline_spec: dict[str, Any],
        *,
        pipeline_path: str | Path,
        effective_path: str | Path | None,
        force_layout: bool,
        layout_algorithm: str | None,
    ) -> dict[str, Any]:
        """Apply the OSS deterministic layout to an in-memory pipeline spec."""

        del pipeline_path, effective_path, force_layout, layout_algorithm
        from .pipelines import layout_pipeline_spec

        laid_out = copy.deepcopy(pipeline_spec)
        layout_pipeline_spec(laid_out, recursive=True)
        return laid_out

    def before_submit_pipeline_spec(
        self,
        pipeline_spec: dict[str, Any],
        *,
        pipeline_path: str | Path,
        effective_path: str | Path | None,
        run_args: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Final pre-submit transform after validation/layout."""

        del pipeline_path, effective_path, run_args
        return pipeline_spec

    def metadata_for_run(
        self,
        *,
        pipeline_name: str,
        pipeline_path: str | Path,
        effective_path: str | Path | None,
        wait: bool,
        open_browser: bool,
        include_next_steps: bool,
        retry: int,
        max_wait: float | None,
        poll_interval: float,
        extra_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build metadata passed to submit/wait/retry lifecycle hooks."""

        metadata: dict[str, Any] = {
            "pipeline_name": pipeline_name,
            "pipeline_path": str(pipeline_path),
            "wait": wait,
            "open_browser": open_browser,
            "include_next_steps": include_next_steps,
            "retry": retry,
            "max_attempts": retry + 1 if wait else 1,
            "poll_interval": poll_interval,
            "max_wait_time": max_wait,
        }
        if effective_path is not None:
            metadata["effective_path"] = str(effective_path)
        if extra_metadata:
            metadata.update(extra_metadata)
        return metadata

    def cleanup_prepared_pipeline(
        self,
        preparation: PipelinePreparationResult,
        *,
        error: Exception | None = None,
    ) -> None:
        """Clean up resources associated with a prepared pipeline.

        Downstreams that hydrate into temporary files can override this to
        remove ``preparation.effective_path`` on success, validation failure,
        submit failure, wait failure, or retry failure.
        """

        del preparation, error

    def format_run_result(
        self,
        result: dict[str, Any],
        *,
        preparation: PipelinePreparationResult,
    ) -> dict[str, Any]:
        """Return the normalized OSS orchestration result.

        Downstreams can override this to preserve legacy CLI/MCP return shapes.
        """

        context = result.get("context")
        response = result.get("response") if isinstance(result.get("response"), Mapping) else {}
        wait_result = result.get("wait") if isinstance(result.get("wait"), Mapping) else None
        run_id = getattr(context, "run_id", None) if isinstance(context, PipelineRunContext) else response.get("id")
        root_execution_id = (
            getattr(context, "root_execution_id", None)
            if isinstance(context, PipelineRunContext)
            else response.get("root_execution_id")
        )
        status = "submitted"
        success: bool | None = True
        if wait_result is not None:
            status = str(wait_result.get("status") or "unknown")
            outcome = (
                context.wait_outcome
                if isinstance(context, PipelineRunContext) and context.wait_outcome is not None
                else PipelineWaitOutcome.from_wait_result(wait_result)
            )
            success = outcome.success
        result_pipeline_name = (
            str(context.run_name)
            if isinstance(context, PipelineRunContext) and context.run_name
            else preparation.pipeline_name
        )
        return {
            **result,
            "success": success,
            "status": status,
            "pipeline_name": result_pipeline_name,
            "run_id": run_id,
            "root_execution_id": root_execution_id,
            "preparation": preparation,
        }


@dataclass
class PipelineRunner(PipelineRunnerHooks, PipelineRunManager):
    """Generic high-level pipeline runner orchestration."""

    hooks: PipelineRunnerHooks = field(default_factory=PipelineRunnerHooks)

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.hooks is not self:
            setattr(self.hooks, "client", self.client)

    @staticmethod
    def _ensure_mapping(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise PipelineRunError("pipeline spec must be a mapping")
        return value

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

    def _high_level_hooks(self) -> PipelineRunnerHooks:
        """Return the object that owns high-level path/spec hooks.

        Subclasses override methods on ``self``. For direct OSS composition,
        preserve the existing ``PipelineRunner(client, hooks=...)`` API.
        """

        if type(self) is PipelineRunner and self.hooks is not self:
            return self.hooks
        return self

    def prepare_pipeline_for_run(
        self,
        pipeline_path: str | Path,
        *,
        run_args: dict[str, Any] | None = None,
        hydrate: bool = True,
        resolution_overrides: dict[str, Any] | None = None,
        skip_validation: bool = False,
        skip_layout: bool = True,
        force_layout: bool = False,
        layout_algorithm: str | None = None,
    ) -> PipelinePreparationResult:
        """Load/hydrate/validate/layout a pipeline before submission."""

        hooks = self._high_level_hooks()
        pipeline_name = hooks.initial_pipeline_name(pipeline_path)
        effective_path: str | Path | None = pipeline_path
        pipeline_spec: Any = {}
        preparation: PipelinePreparationResult | None = None
        try:
            if hydrate:
                hydrate_pipeline_for_run = hooks.hydrate_pipeline_for_run
                hydrate_kwargs: dict[str, Any] = {"resolution_overrides": resolution_overrides}
                if self._accepts_client_keyword(hydrate_pipeline_for_run):
                    hydrate_kwargs["client"] = self._get_client()
                pipeline_spec, hydrated_effective_path = hydrate_pipeline_for_run(
                    pipeline_path,
                    **hydrate_kwargs,
                )
                if hydrated_effective_path is not None:
                    effective_path = hydrated_effective_path
            else:
                pipeline_spec = hooks.load_pipeline(pipeline_path)

            pipeline_spec = self._ensure_mapping(pipeline_spec)
            spec_name = pipeline_spec.get("name")
            if isinstance(spec_name, str) and spec_name:
                pipeline_name = spec_name

            pipeline_spec = hooks.prepare_loaded_pipeline_spec(
                pipeline_spec,
                pipeline_path=pipeline_path,
                effective_path=effective_path,
                hydrate=hydrate,
                run_args=run_args,
            )
            pipeline_spec = self._ensure_mapping(pipeline_spec)
            spec_name = pipeline_spec.get("name")
            if isinstance(spec_name, str) and spec_name:
                pipeline_name = spec_name

            if hooks.should_apply_layout(
                pipeline_spec,
                pipeline_path=pipeline_path,
                effective_path=effective_path,
                skip_layout=skip_layout,
                force_layout=force_layout,
                layout_algorithm=layout_algorithm,
            ):
                pipeline_spec = hooks.apply_layout(
                    pipeline_spec,
                    pipeline_path=pipeline_path,
                    effective_path=effective_path,
                    force_layout=force_layout,
                    layout_algorithm=layout_algorithm,
                )
                pipeline_spec = self._ensure_mapping(pipeline_spec)
                spec_name = pipeline_spec.get("name")
                if isinstance(spec_name, str) and spec_name:
                    pipeline_name = spec_name

            validation_errors = hooks.validate_pipeline_for_run(
                pipeline_spec,
                pipeline_path=pipeline_path,
                effective_path=effective_path,
                skip_validation=skip_validation,
            )
            if validation_errors and not skip_validation:
                raise PipelineRunError("Pipeline validation failed:\n  - " + "\n  - ".join(validation_errors))

            pipeline_spec = hooks.before_submit_pipeline_spec(
                pipeline_spec,
                pipeline_path=pipeline_path,
                effective_path=effective_path,
                run_args=run_args,
            )
            pipeline_spec = self._ensure_mapping(pipeline_spec)
            spec_name = pipeline_spec.get("name")
            if isinstance(spec_name, str) and spec_name:
                pipeline_name = spec_name

            preparation = PipelinePreparationResult(
                pipeline_spec=pipeline_spec,
                pipeline_name=pipeline_name,
                effective_path=effective_path,
            )
            return preparation
        except Exception as exc:
            cleanup_spec = pipeline_spec if isinstance(pipeline_spec, dict) else {}
            hooks.cleanup_prepared_pipeline(
                preparation
                or PipelinePreparationResult(
                    pipeline_spec=cleanup_spec,
                    pipeline_name=pipeline_name,
                    effective_path=effective_path,
                ),
                error=exc,
            )
            raise

    def submit_pipeline_spec_result(
        self,
        pipeline_name: str,
        pipeline_spec: dict[str, Any],
        *,
        run_args: dict[str, Any] | None = None,
        annotations: dict[str, str] | None = None,
        run_as: str | None = None,
        pipeline_path: str | Path | None = None,
    ) -> dict[str, Any]:
        """Submit an already prepared spec and return a normalized summary."""

        submit_payload = self.prepare_submit_payload_from_spec(
            copy.deepcopy(pipeline_spec),
            run_args=run_args,
            annotations=annotations,
            pipeline_path=pipeline_path,
            run_as=run_as,
            hydrate=False,
        )
        response = self.submit_prepared_payload(submit_payload, pipeline_path=pipeline_path)
        run_id = str(response.get("id")) if response.get("id") is not None else None
        root_execution_id = (
            str(response.get("root_execution_id")) if response.get("root_execution_id") is not None else None
        )
        return {
            "success": True,
            "status": "submitted",
            "pipeline_name": submit_payload.run_name or pipeline_name,
            "run_id": run_id,
            "root_execution_id": root_execution_id,
            "response": response,
        }

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
        retry: int = 0,
        max_attempts: int | None = None,
        allow_zero_poll_interval: bool = False,
        timeout_clock: str = "monotonic",
        exit_on_first_failure: bool = False,
        skip_validation: bool = False,
        skip_layout: bool = True,
        force_layout: bool = False,
        layout_algorithm: str | None = None,
        open_browser: bool = False,
        include_next_steps: bool = False,
        metadata: dict[str, Any] | None = None,
        submit_recovery_attempts: int = 2,
    ) -> dict[str, Any]:
        """Run a pipeline path through generic preparation + lifecycle hooks.

        Path-based runs prepare inside the retry body factory so every retry
        re-runs load/hydrate/validation/layout/pre-submit hooks.
        """

        attempts = max_attempts if max_attempts is not None else (retry + 1 if wait else 1)
        hooks = self._high_level_hooks()
        preparations: dict[int, PipelinePreparationResult] = {}
        submit_payloads: dict[int, PipelineSubmitPayload] = {}

        def prepare_attempt(attempt: int) -> PipelinePreparationResult:
            preparation = self.prepare_pipeline_for_run(
                pipeline_path,
                run_args=run_args,
                hydrate=hydrate,
                resolution_overrides=resolution_overrides,
                skip_validation=skip_validation,
                skip_layout=skip_layout,
                force_layout=force_layout,
                layout_algorithm=layout_algorithm,
            )
            preparations[attempt] = preparation
            return preparation

        def body_factory(
            attempt: int,
            _previous_context: PipelineRunContext | None,
            _error: Exception | None,
        ) -> dict[str, Any]:
            preparation = prepare_attempt(attempt)
            submit_payload = self.prepare_submit_payload_from_spec(
                copy.deepcopy(preparation.pipeline_spec),
                run_args=run_args,
                annotations=annotations,
                pipeline_path=pipeline_path,
                run_as=run_as,
                hydrate=False,
            )
            submit_payloads[attempt] = submit_payload
            return submit_payload.to_body()

        def metadata_factory(
            attempt: int,
            previous_context: PipelineRunContext | None,
            _error: Exception | None,
        ) -> dict[str, Any]:
            preparation = preparations.get(attempt)
            submit_payload = submit_payloads.get(attempt)
            if (
                preparation is None
                and previous_context is not None
                and previous_context.run_id is None
                and previous_context.submit_body is not None
            ):
                # ``PipelineRunManager`` reuses the previous submit body after
                # submit-time exceptions. Mirror the previous preparation
                # bookkeeping so metadata/result formatting still point at the
                # logical pipeline being retried without re-running dynamic
                # body preparation hooks.
                preparation = preparations.get(previous_context.attempt)
                if preparation is not None:
                    preparations[attempt] = preparation
                submit_payload = submit_payloads.get(previous_context.attempt)
                if submit_payload is not None:
                    submit_payloads[attempt] = submit_payload
            if preparation is None:
                raise PipelineRunError("Pipeline retry metadata requested before preparation")
            return hooks.metadata_for_run(
                pipeline_name=(submit_payload.run_name if submit_payload else None) or preparation.pipeline_name,
                pipeline_path=pipeline_path,
                effective_path=preparation.effective_path,
                wait=wait,
                open_browser=open_browser,
                include_next_steps=include_next_steps,
                retry=retry,
                max_wait=max_wait,
                poll_interval=poll_interval,
                extra_metadata=metadata,
            )

        error: Exception | None = None
        try:
            result = self._run_body_factory(
                body_factory,
                pipeline_path=pipeline_path,
                wait=wait,
                max_wait=max_wait,
                poll_interval=poll_interval,
                use_graph_state=use_graph_state,
                max_attempts=attempts,
                allow_zero_poll_interval=allow_zero_poll_interval,
                timeout_clock=timeout_clock,
                exit_on_first_failure=exit_on_first_failure,
                metadata_factory=metadata_factory,
                submit_recovery_attempts=submit_recovery_attempts,
            )
            context = result.get("context")
            attempt = context.attempt if isinstance(context, PipelineRunContext) else max(preparations)
            return hooks.format_run_result(result, preparation=preparations[attempt])
        except Exception as exc:
            error = exc
            raise
        finally:
            cleaned_preparation_ids: set[int] = set()
            for preparation in preparations.values():
                preparation_id = id(preparation)
                if preparation_id in cleaned_preparation_ids:
                    continue
                cleaned_preparation_ids.add(preparation_id)
                hooks.cleanup_prepared_pipeline(preparation, error=error)
