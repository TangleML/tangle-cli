"""``TaskEnv`` — declare image + dependencies once, reuse across ``@task``s.

A :class:`TaskEnv` bundles the container ``image`` and an optional
``dependencies_from`` file so a pipeline author can declare the execution
environment once and reference it from many ``@task`` components via
``@task(env=...)``. It is the Python equivalent of a ``local_from_python``
YAML anchor.

``TaskEnv`` is **authoring-only**. ``@task(env=...)`` expands it at decoration
time into the existing ``CallableRef._task_image`` /
``CallableRef._task_dependencies_from`` metadata, so the compiler, hydrator,
and runner never see a ``TaskEnv`` object.

Example::

    from pathlib import Path
    from tangle_cli.python_pipeline import TaskEnv, task

    TRAINING = TaskEnv(
        image="python:3.12",
        dependencies_from=Path(__file__).parent / "pyproject.toml",
    )

    @task(env=TRAINING)
    def train_model(...):
        ...

An environment can also live in a config file, loaded with
:meth:`TaskEnv.from_config`, which any ``TaskEnv`` dataclass subclass
inherits unchanged::

    ENV = TaskEnv.from_config("tangle/envs.yaml")  # relative to THIS file
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

_TaskEnvT = TypeVar("_TaskEnvT", bound="TaskEnv")


@dataclass(frozen=True)
class TaskEnv:
    """Reusable execution environment for ``@task`` components.

    Attributes:
        image: Container image for the component. Required.
        dependencies_from: Optional path to a ``pyproject.toml`` (or any file
            the hydrator understands) declaring pip dependencies. A relative
            path is resolved at the ``TaskEnv`` *definition site*, so a shared
            ``_envs.py`` resolves intuitively; pass an absolute ``Path`` to
            avoid that. When omitted, the hydrator's existing dependency
            discovery still applies.
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
            # Frames: __post_init__ -> generated __init__ -> the definition site.
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

    @classmethod
    def from_config(cls: type[_TaskEnvT], path: str | Path) -> _TaskEnvT:
        """Build the env from a config file, returning an instance of ``cls``.

        Every ``TaskEnv`` dataclass subclass inherits this and is validated
        against its own fields; nothing here is specific to one kind of
        environment.

        A relative ``path`` resolves against the **calling file's** directory,
        never the working directory, so a pipeline script selects the same
        config wherever the CLI is run from. Call this directly on the class —
        a subclass that wraps or delegates to it is not supported.

        The file is read with the loader ``--config`` uses, so its ``_select``
        directive behaves identically here, and it must resolve to exactly one
        object. The object's keys must be named parameters of ``cls``'s
        ``__init__``, which keeps an environment config environment-only:
        pipeline concerns such as versioning or schedules have no field to land
        in. A relative ``dependencies_from`` inside the object is anchored to
        the config file's directory rather than to this module.

        Raises:
            CompileError: for every failure — a missing file, an unresolvable
                relative path, a load or ``_select`` error, a multi-object
                document, an unknown or missing field, or a value the subclass
                rejects.

        Note:
            A config file is untrusted input, so no diagnostic raised here
            echoes a config *value* — not in the message, and not through
            ``__cause__``/``__context__``, which a rendered traceback would
            print into the same CI log. Construct ``cls(...)`` directly to see
            a constructor's own validation message.
        """

        # Lazy import: the authoring surface should not pay for the YAML/CLI
        # config stack unless a pipeline actually loads an env from a file.
        from tangle_cli.args_container import (
            SELECT_KEY,
            ArgsContainer,
            ConfigFileError,
            _render_config_key,
        )
        from tangle_cli.python_pipeline.errors import CompileError

        label = f"{cls.__name__}.from_config"
        config_path = Path(path)
        if not config_path.is_absolute():
            frame = inspect.currentframe()
            caller = frame.f_back if frame is not None else None
            filename = caller.f_globals.get("__file__") if caller is not None else None
            if not isinstance(filename, str) or not filename:
                raise CompileError(
                    f"{label}({str(path)!r}): a relative path resolves against the "
                    "calling file, which could not be determined here — pass an "
                    "absolute path."
                )
            config_path = (Path(filename).resolve().parent / config_path).resolve()
        if not config_path.exists():
            raise CompileError(
                f"{label}: {config_path} does not exist. Declare the environment "
                f"there (optionally a {SELECT_KEY} over an environment variable, "
                f"with a default case for local runs), or construct "
                f"{cls.__name__}(...) directly."
            )

        try:
            documents = ArgsContainer._load_config_file(config_path)
        except ConfigFileError as exc:
            raise CompileError(f"{label} could not resolve {config_path}: {exc}") from exc
        if len(documents) != 1 or not isinstance(documents[0], dict):
            raise CompileError(
                f"{label}: {config_path} must resolve to ONE environment object "
                f"(one {SELECT_KEY} case), got a multi-config document."
            )

        case = dict(documents[0])
        # The signature, not dataclasses.fields(): it is what cls(**case)
        # actually accepts — InitVar pseudo-fields in, ClassVar and
        # field(init=False) out. *args/**kwargs are excluded so a typo stays
        # fail-closed.
        parameters = {
            name: parameter
            for name, parameter in inspect.signature(cls).parameters.items()
            if parameter.kind
            in (parameter.POSITIONAL_OR_KEYWORD, parameter.KEYWORD_ONLY)
        }
        unknown = sorted(_render_config_key(key) for key in set(case) - set(parameters))
        if unknown:
            raise CompileError(
                f"{label}: {config_path} case has unknown field(s) "
                f"{', '.join(unknown)}. Allowed fields: {', '.join(parameters)}."
            )
        missing = sorted(
            name
            for name, parameter in parameters.items()
            if parameter.default is parameter.empty and name not in case
        )
        if missing:
            raise CompileError(
                f"{label}: {config_path} case is missing required field(s): "
                f"{', '.join(missing)}."
            )

        dependencies = case.get("dependencies_from")
        if isinstance(dependencies, (str, Path)) and not Path(dependencies).is_absolute():
            case["dependencies_from"] = (config_path.parent / Path(dependencies)).resolve()

        try:
            return cls(**case)
        except (TypeError, ValueError):
            # Re-raised below, OUTSIDE this handler: the constructor's message
            # may quote a config value, and `from exc` would publish it on
            # __cause__ while a raise in-handler would publish it on
            # __context__ — both get printed by traceback.format_exception.
            pass
        raise CompileError(
            f"{label}: {config_path} case is not a valid {cls.__name__} "
            f"(fields present: {', '.join(sorted(case))}). Its validation "
            f"message is withheld because it can contain config values; "
            f"construct {cls.__name__}(...) directly to see it."
        )
