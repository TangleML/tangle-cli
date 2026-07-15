"""``TaskEnv`` — declare image + dependencies once, reuse across ``@task``s.

A :class:`TaskEnv` bundles the container ``image`` and an optional
``dependencies_from`` file so a pipeline author can declare the execution
environment once and reference it from many ``@task`` components via
``@task(env=...)``. It is the Python equivalent of a ``local_from_python``
YAML anchor.

``TaskEnv`` is **authoring-only**. ``@task(env=...)`` expands it at
decoration time into the existing ``CallableRef._task_image`` /
``CallableRef._task_dependencies_from`` metadata. The compiler, hydrator,
and downstream runner never see a ``TaskEnv`` object — no downstream
component learns the word ``env``.

Example::

    from pathlib import Path
    from tangle_cli.python_pipeline import TaskEnv, task

    TRAINING = TaskEnv(
        image="python:3.12",
        dependencies_from=Path(__file__).parent / "pyproject.toml",
    )

    @task(env=TRAINING)
    def train_model(...):
        # docstring carries: Metadata / Name: Train Model
        ...

The component name comes from the function's docstring ``Metadata: Name:``
block (auto-derived from the function name if absent); the pipeline block name
(task id) comes from the call-site variable name, or ``.named("Block Name")``
for an explicit label.
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TaskEnv:
    """Reusable execution environment for ``@task`` components.

    Bundles the container image and optional dependencies file so a
    pipeline author can declare them once and reference the env from many
    tasks. This is the Python equivalent of a ``local_from_python`` YAML
    anchor.

    Attributes:
        image: Container image for the component. Required — naming the
            image once is the main point of ``TaskEnv``.
        dependencies_from: Optional path to a ``pyproject.toml`` (or any
            file the hydrator understands) declaring pip
            dependencies. A relative path is resolved at the ``TaskEnv``
            *definition site* (the module where ``TaskEnv(...)`` is
            written), so a shared ``_envs.py`` resolves intuitively.
            Authors can pass an absolute ``Path`` to avoid frame-based
            ambiguity. When omitted, the existing hydrator/generator
            dependency discovery still applies.
    """

    image: str
    dependencies_from: str | Path | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.image, str) or not self.image:
            raise ValueError("TaskEnv.image must be a non-empty string")
        if self.dependencies_from is None:
            return

        p = Path(self.dependencies_from)
        if not p.is_absolute():
            # Resolve a relative ``dependencies_from`` at the TaskEnv
            # DEFINITION SITE. Walk frames: __post_init__ -> generated
            # dataclass __init__ -> the caller that wrote ``TaskEnv(...)``.
            frame = inspect.currentframe()
            caller = (
                frame.f_back.f_back
                if frame and frame.f_back and frame.f_back.f_back
                else None
            )
            filename = caller.f_globals.get("__file__") if caller else None
            caller_dir = Path(filename).resolve().parent if filename else Path.cwd()
            p = caller_dir / p
        # Frozen dataclass: bypass __setattr__ to store the resolved Path.
        object.__setattr__(self, "dependencies_from", p.resolve())
