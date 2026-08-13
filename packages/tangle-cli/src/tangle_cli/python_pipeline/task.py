"""``@task`` decorator.

The decorator captures metadata about a Python function (source path,
function name, container image, dependencies, generation mode, resolve root, custom name, annotations)
and returns a :class:`CallableRef` with NO componentRef URL set.

At compile time the driver collects every ``@task`` ref that gets called
inside a ``@pipeline`` body, auto-emits a sibling
``<out>.components.yaml`` with one ``local_from_python:`` entry per
unique source file, and rewrites each task's componentRef URL to
``resolve://./<out_stem>.components.yaml#<fragment>``. Hydrate then uses
the hydrator's own ``local_from_python`` resolver to call
``regenerate_yaml`` at hydrate time -- no pre-codegen step on our side.

Crucially, the decorator does NOT call the user function.

Lazy import contract: this module does NOT import
``tangle_cli.component_generator`` at module load. The codegen
import lives inside ``CallableRef.materialize()`` (kept as a public
escape hatch for users who want to inspect the generated YAML offline)
so importing this package stays cheap.

Example::

    from tangle_cli.python_pipeline import task

    @task(image="python:3.12", dependencies_from="./pyproject.toml")
    def my_task(out: OutputPath("Text"), greeting: str = "hello"):
        '''Write a greeting.

        Metadata:
            Name: My Task
            Version: 1.0.0
        '''
        with open(out, "w") as f:
            f.write(greeting)

    # ``my_task`` is now a CallableRef -- NOT the function. Calling it
    # inside a @pipeline body records a TaskNode and registers the ref
    # for local_from_python auto-emission at compile time.
"""
from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any, Callable

# Install the ``cloud_pipelines`` shim eagerly at module load so
# upstream-generated components that do ``from cloud_pipelines import components``
# at top level can be imported by user @task wrappers without each
# pipeline having to call a private tangle_cli helper.
# ``component_from_func`` is NOT ``component_generator`` (the heavy
# codegen module) -- it's the lighter introspection module, and the shim
# helper itself is a no-op when ``cloud_pipelines`` is already in
# ``sys.modules``.
from tangle_cli.component_from_func import _ensure_cloud_pipelines_shim

from .ref import CallableRef
from .task_env import TaskEnv

_ensure_cloud_pipelines_shim()


def task(
    *,
    env: TaskEnv | None = None,
    image: str | None = None,
    image_id: str | None = None,
    dependencies_from: str | Path | None = None,
    mode: str | None = None,
    resolve_root: str | Path | None = None,
    annotations: dict[str, Any] | None = None,
    unwrap: str | list[str] | tuple[str, ...] | None = None,
) -> Callable[[Callable[..., Any]], CallableRef]:
    """Decorator: turn a Python function into a Tangle component ref.

    The decorated function is NEVER executed by the framework. Instead
    the decorator captures metadata onto a :class:`CallableRef`. The
    compile driver emits a sibling ``<out>.components.yaml`` with a
    ``local_from_python:`` entry for the function and rewrites the
    task's componentRef URL to point at it. Hydrate uses the hydrator's
    own resolver to regenerate the component YAML at hydrate time.

    Args:
        env: Optional :class:`TaskEnv` bundling a reusable container
            ``image`` + ``dependencies_from`` pair so several tasks can
            share one declared-once execution environment (the Python
            equivalent of a ``local_from_python`` YAML anchor). ``env`` is
            expanded at decoration time into the same ``_task_image`` /
            ``_task_dependencies_from`` metadata an explicit
            ``image=`` / ``dependencies_from=`` would produce — no
            ``TaskEnv`` object ever reaches the compiler, hydrator, or
            runner. Explicit ``image=`` / ``dependencies_from=`` override
            the env PER FIELD.
        image: Container image for the component (required for
            ``local_from_python`` resolution). The image string is
            written verbatim into the emitted
            ``components.yaml#local_from_python.image`` field. Overrides
            ``env.image`` when both are given.
        image_id: Logical image identifier resolved at compile time via
            registered defaults or ``tangle sdk pipelines compile --image
            ID=REF`` overrides. Ignored when explicit ``image=`` supplies
            an image; otherwise it also overrides ``env.image``.
        dependencies_from: Path to a ``pyproject.toml`` (or any file
            the hydrator understands) that declares pip
            dependencies. Resolved relative to the caller's source
            file when given as a string. Emitted into
            ``components.yaml#local_from_python.dependencies_from``.
            Overrides ``env.dependencies_from`` when both are given.
        mode: Optional local-from-python generation mode. ``None``
            preserves the hydrator default (currently ``inline``).
            Use ``"bundle"`` to ask hydrate-time codegen to embed
            first-party imports using the existing module bundler.
        resolve_root: Optional module resolution root for bundle mode.
            Relative strings are resolved relative to the task source
            file, then emitted into
            ``components.yaml#local_from_python.resolve_root``.
        annotations: Extra annotations to merge into the emitted
            component's ``metadata.annotations`` block.
        unwrap: Optional dict parameter name (or names) whose call-site
            ``dict`` value should be flattened into explicit component inputs.
            For example ``unwrap="run_data"`` turns
            ``run_data={"run_id_1": task.output}`` into a component input
            named ``run_data__run_id_1`` and reconstructs the original dict in
            the generated runtime wrapper.

    Returns:
        A decorator that, given the user's function, returns a
        :class:`CallableRef`. The returned ref:

        - Has ``url`` set to ``None`` -- the compile driver fills it in
          with ``resolve://./<out_stem>.components.yaml#<fragment>``
          after tracing.
        - Has ``_task_*`` metadata populated so the driver can build
          the local_from_python entry.
        - Behaves like any other ``ref()``-derived ref when called
          inside a ``@pipeline`` trace context.
    """

    # Validate ``env`` up front (decoration time) with a message that
    # names the public keyword so authors get an actionable error.
    if env is not None and not isinstance(env, TaskEnv):
        raise TypeError(
            "@task(env=...) expects a TaskEnv instance, got "
            f"{type(env).__name__!r}. Build one with "
            "TaskEnv(image=..., dependencies_from=...)."
        )

    # Per-field precedence: an explicit ``image=`` / ``dependencies_from=``
    # overrides the corresponding ``env`` field; otherwise the env value
    # (if any) is used. This mirrors YAML anchor semantics: start from the
    # declared-once defaults, override locally where needed.
    effective_image = (
        image
        if image is not None
        else (None if image_id is not None else (env.image if env else None))
    )
    effective_deps_raw = (
        dependencies_from
        if dependencies_from is not None
        else (env.dependencies_from if env else None)
    )

    # Normalise ``dependencies_from`` early so the driver doesn't have
    # to think about string-vs-Path forms. The path is resolved relative
    # to the user's source file (set inside ``decorator``). An
    # ``env.dependencies_from`` is ALREADY an absolute resolved Path (the
    # TaskEnv resolved it at its definition site), so it passes through
    # the normalisation below unchanged; an explicit relative string is
    # still resolved relative to the @task source file.
    raw_dependencies_from = effective_deps_raw
    raw_resolve_root = resolve_root
    if mode is not None and mode not in {"inline", "bundle"}:
        raise ValueError("@task(mode=...) must be 'inline', 'bundle', or None")

    if unwrap is None:
        unwrap_names: tuple[str, ...] = ()
    elif isinstance(unwrap, str):
        unwrap_names = (unwrap,)
    elif isinstance(unwrap, (list, tuple)) and all(isinstance(name, str) for name in unwrap):
        unwrap_names = tuple(unwrap)
    else:
        raise TypeError("@task(unwrap=...) expects a string, a list/tuple of strings, or None")
    if len(set(unwrap_names)) != len(unwrap_names):
        raise ValueError("@task(unwrap=...) contains duplicate parameter names")
    for name in unwrap_names:
        if not name.isidentifier():
            raise ValueError(
                "@task(unwrap=...) names must be valid Python parameter names; "
                f"got {name!r}"
            )

    def decorator(fn: Callable[..., Any]) -> CallableRef:
        """Capture a task function as a traceable ``CallableRef``.

        Args:
            fn: The user-authored Python function being decorated.

        Returns:
            A ``CallableRef`` carrying source, image, dependency, generation,
            annotation, and unwrap metadata. The unwrap names are stored on the
            ref so trace-time calls can flatten matching dict arguments and the
            compiler can persist ``local_from_python.unwrapped_inputs``.
        """
        # Capture the absolute path of the source file the user wrote
        # the function in. ``inspect.getfile`` raises TypeError for
        # builtins / dynamically-built functions; the @task path
        # requires a real on-disk file because the hydrator reads
        # the source via inspect.getfile too.
        try:
            source_path = Path(inspect.getfile(fn)).resolve()
        except (TypeError, OSError) as exc:
            raise RuntimeError(
                f"@task could not resolve the source file for {fn!r}: {exc}. "
                "@task only supports functions defined in real .py files; the "
                "codegen reads the source via inspect.getfile."
            ) from exc

        function_name = fn.__name__

        # Resolve local paths relative to the source file when the user
        # gave a relative value. Absolute paths pass through unchanged.
        deps_path: Path | None
        if raw_dependencies_from is None:
            deps_path = None
        else:
            deps_path = Path(raw_dependencies_from)
            if not deps_path.is_absolute():
                deps_path = (source_path.parent / deps_path).resolve()

        resolve_root_path: Path | None
        if raw_resolve_root is None:
            resolve_root_path = None
        else:
            resolve_root_path = Path(raw_resolve_root)
            if not resolve_root_path.is_absolute():
                resolve_root_path = (source_path.parent / resolve_root_path).resolve()

        # No URL set at decoration time -- the compile driver rewrites
        # componentRef.url for @task-derived refs to
        # ``resolve://./<out_stem>.components.yaml#<fragment>`` after
        # tracing. emit.py tolerates None for @task refs because the
        # driver fills it in before writing.
        ref_instance = CallableRef(
            url=None,
            _task_source_path=source_path,
            _task_function_name=function_name,
            _task_image=effective_image,
            _task_image_id=image_id,
            _task_dependencies_from=deps_path,
            _task_mode=mode,
            _task_resolve_root=resolve_root_path,
            _task_custom_annotations=dict(annotations) if annotations else None,
            _task_unwrap=unwrap_names,
        )

        # Expose function-like introspection so ``tangle_cli``'s
        # ``extract_interface`` can read signature/docstring directly
        # off the CallableRef. After the @task decorator runs, the
        # symbol the user wrote (``def my_task(...): ...``) is bound
        # to ``ref_instance`` in the module's namespace; when
        # ``regenerate_yaml`` does ``getattr(module, "my_task")`` it
        # gets the ref. Forwarding these dunders lets
        # ``inspect.signature`` / ``inspect.getdoc`` /
        # ``inspect.getsource`` (via ``__wrapped__``) treat the ref as
        # a stand-in for ``fn`` itself -- no separate function registry
        # needed.
        #
        # ``object.__setattr__`` bypasses the frozen-dataclass
        # ``__setattr__`` guard so we can add these attrs without
        # mutating any declared dataclass field.
        object.__setattr__(ref_instance, "__name__", function_name)
        object.__setattr__(ref_instance, "__qualname__", fn.__qualname__)
        object.__setattr__(ref_instance, "__module__", fn.__module__)
        object.__setattr__(ref_instance, "__doc__", fn.__doc__)
        object.__setattr__(ref_instance, "__wrapped__", fn)
        object.__setattr__(ref_instance, "__signature__", inspect.signature(fn))
        # Forward annotations too -- some introspection paths read
        # ``__annotations__`` directly rather than through signature.
        object.__setattr__(
            ref_instance, "__annotations__", dict(fn.__annotations__)
        )

        return ref_instance

    return decorator
