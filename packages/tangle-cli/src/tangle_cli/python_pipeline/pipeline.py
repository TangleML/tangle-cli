"""``@pipeline`` decorator — metadata holder.

The decorator captures metadata (name, description, config path,
annotations, caller dir) on a :class:`PipelineFn` wrapper. It does NOT
call the user function — the tracer (``trace.trace_pipeline``) invokes it
inside a :class:`GraphBuilder` context.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import emit
from .graph import GraphBuilder


@dataclass
class PipelineFn:
    """Wrapper holding decorator metadata + a reference to the user fn.

    Attributes captured here are consumed by:
    - ``trace.trace_pipeline`` (passes ``fn``, ``config_path``,
      ``annotations`` through to GraphBuilder construction).
    - the compile driver (resolves config_path relative to caller_dir
      and threads it through cfg.load_cfg).
    """

    fn: Callable[..., Any]
    name: str
    description: str | None = None
    config_path: str | None = None  # path relative to caller_dir
    annotations: dict[str, Any] = field(default_factory=dict)
    task_annotations: dict[str, Any] = field(default_factory=dict)
    caller_dir: Path | None = None
    # Convention for the single Out[T] slot's name. Defaults to the PoC
    # canonical sentinel ``wait_for_output``; overridable via
    # ``@pipeline(output_name=...)``.
    output_name: str = "wait_for_output"
    # When True, this pipeline broadcasts its OWN resolved config.yaml deep
    # into its subtree by matching key name (lenient same-name overlay).
    # Off by default (Decision F isolation is preserved). Metadata only — no
    # config is read at decoration time; the compile driver acts on it.
    propagate_config: bool = False

    # ------------------------------------------------------------------
    # Calling the decorated PipelineFn directly is reserved for the
    # tracer; users normally invoke via the CLI compile driver. The
    # underlying function is still accessible as ``pipeline_fn.fn`` for
    # tests that want to call it raw.

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        # Guard against the inline-execution footgun (Decision H): calling
        # a @pipeline directly inside an active parent trace would record
        # the child's internal tasks into the PARENT's GraphBuilder,
        # destroying subgraph encapsulation. Block it with guidance toward
        # ``subpipeline(child)(...)``. The compiler's own root trace is
        # unaffected because ``trace_pipeline`` invokes ``self.fn(...)``
        # directly, never ``self(...)``. Imports are local to avoid an
        # import cycle with trace.py at module load.
        from .trace import current_builder

        if current_builder() is not None:
            from .errors import CompileError

            raise CompileError(
                f"Cannot call @pipeline {self.name!r} directly inside another "
                "@pipeline. Use subpipeline(child_pipeline)(...) so the child "
                "is compiled as a subgraph."
            )
        return self.fn(*args, **kwargs)

    # ------------------------------------------------------------------
    # Compile path: trace + emit. Returns the body dict ready for
    # ``dump_yaml``. The CLI driver calls this after loading cfg and
    # merging ``--override`` pairs.

    def compile_to_dict(
        self,
        cfg: Any | None = None,
        inputs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Trace the pipeline and emit the body dict.

        Args:
            cfg: The :class:`Cfg` object to bind to the pipeline's
                ``cfg`` parameter. If ``None``, an empty Cfg is used
                (useful for tests where no config is needed).
            inputs: Optional runtime values for ``In[T]`` parameters.
                Missing entries fall back to ``GraphInputPlaceholder``
                so emit can render them as ``graphInput:`` references.
        """
        # Local imports to avoid a circular dep with trace.py at module
        # load time (trace.py imports back into pipeline for typing).
        from . import emit, trace
        from .cfg import Cfg

        if cfg is None:
            cfg = Cfg({})
        builder = trace.trace_pipeline(self, cfg=cfg, inputs=inputs or {})
        # emit_pipeline returns (body, exempt_paths); the raw(...) exempt
        # paths are a compile-driver concern (the driver threads them into
        # the output guard). This trace+emit helper exposes only the body.
        body, _exempt_paths = emit.emit_pipeline(builder)
        return body

    # ------------------------------------------------------------------
    # Smoke-test path: emit a header-only dict without tracing. Used by
    # tests to verify canonical key order without needing the tracer.

    def compile_empty(self) -> dict[str, Any]:
        """Return the body dict assuming a 0-task graph."""
        builder = GraphBuilder(
            name=self.name,
            description=self.description,
            annotations=dict(self.annotations),
        )
        # emit_pipeline returns (body, exempt_paths); a 0-task graph has no
        # raw(...) arguments, so only the body is relevant here.
        body, _exempt_paths = emit.emit_pipeline(builder)
        return body


def pipeline(
    name: str,
    *,
    description: str | None = None,
    config: str | None = None,
    annotations: dict[str, Any] | None = None,
    task_annotations: dict[str, Any] | None = None,
    output_name: str = "wait_for_output",
    propagate_config: bool = False,
) -> Callable[[Callable[..., Any]], PipelineFn]:
    """Mark a function as a Tangle pipeline definition.

    The decorator captures metadata (name, description, config path,
    annotations) on a :class:`PipelineFn` wrapper. It does NOT call the
    user function — the trace driver invokes it inside a
    :class:`GraphBuilder` context.

    Args:
        name: The pipeline's ``name:`` value in the emitted YAML.
        description: Optional ``description:`` block (multi-line OK —
            block-literal style is applied by ``dump_yaml``).
        config: Path to ``config.yaml``, relative to the file holding
            the decorated function. Loaded by the CLI driver at compile
            time so ``--override key=value`` pairs can merge in.
        annotations: ``metadata.annotations`` block (e.g. ``version``,
            ``author``).
        task_annotations: Per-task default annotations applied to every
            task in the pipeline. Accepted for API completeness but not
            wired through in MVP — the PoC sets per-task annotations
            explicitly via ``.with_annotations``.
        output_name: Name for the single ``Out[T]`` output slot.
            Defaults to ``wait_for_output`` (matches the PoC).
        propagate_config: Broadcasts this pipeline's own config.yaml deep
            into its subtree by matching key name; off by default.
    """

    def decorator(fn: Callable[..., Any]) -> PipelineFn:
        # Resolve caller_dir from the decorated function's source file.
        try:
            source_file = Path(inspect.getfile(fn)).resolve()
            caller_dir = source_file.parent
        except (TypeError, OSError):
            # Tests inside generated modules may not have a real file.
            caller_dir = None

        return PipelineFn(
            fn=fn,
            name=name,
            description=description,
            config_path=config,
            annotations=dict(annotations or {}),
            task_annotations=dict(task_annotations or {}),
            caller_dir=caller_dir,
            output_name=output_name,
            propagate_config=propagate_config,
        )

    return decorator
