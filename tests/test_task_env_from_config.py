"""``TaskEnv.from_config`` — generic, config-backed environment loading.

The point of these tests is that the behaviour is GENERIC: it lives on the
ordinary :class:`TaskEnv` and every dataclass subclass inherits it, validated
against that subclass's own fields. Each test loads a fabricated pipeline
*module from disk* rather than calling ``from_config`` from this test file, so
"relative paths resolve against the calling module, never the working
directory" is actually exercised — the tests run with the working directory
pointed somewhere else entirely.
"""

from __future__ import annotations

import importlib.util
import sys
import traceback
import textwrap
import uuid
from pathlib import Path
from types import ModuleType

import pytest

from tangle_cli.python_pipeline import TaskEnv
from tangle_cli.python_pipeline.errors import CompileError

#: A downstream-style subclass with its own field and validation, declared
#: inside the fabricated pipeline module so the module stays self-contained.
SUBCLASS_SOURCE = """
from dataclasses import InitVar, dataclass, field
from typing import ClassVar

from tangle_cli.python_pipeline import TaskEnv


@dataclass(frozen=True)
class GpuEnv(TaskEnv):
    accelerator: str = ""

    def __post_init__(self):
        super().__post_init__()
        if not isinstance(self.accelerator, str) or not self.accelerator:
            raise ValueError("GpuEnv.accelerator must be a non-empty string")


@dataclass(frozen=True)
class LeakyEnv(TaskEnv):
    # Hostile-by-accident subclass: it interpolates the offending value into
    # its own validation message, the way a careless author would.
    secret: str = ""

    def __post_init__(self):
        super().__post_init__()
        if not self.secret.startswith("ok-"):
            raise ValueError(f"invalid secret {self.secret}")


@dataclass(frozen=True)
class NestedLeakyEnv(TaskEnv):
    # Leaks a SCALAR LEAF of a structured value: no scan of the top-level
    # stringified dict would have matched it.
    settings: dict = None

    def __post_init__(self):
        super().__post_init__()
        raise ValueError(f"invalid credential {next(iter(self.settings.values()))}")


@dataclass(frozen=True)
class TransformedLeakyEnv(TaskEnv):
    # Leaks a TRANSFORMED copy of a flat value: content scanning cannot undo
    # a .lower(), a slice, or a re-encode.
    secret: str = ""

    def __post_init__(self):
        super().__post_init__()
        raise ValueError(
            f"invalid credential {self.secret.lower()} / {self.secret[3:]} / "
            f"{self.secret.encode().hex()}"
        )


@dataclass(frozen=True)
class ShortLeakyEnv(TaskEnv):
    # A 1-2 character value: short enough that any length-thresholded
    # redaction scheme would have exempted it.
    pin: str = ""

    def __post_init__(self):
        super().__post_init__()
        raise ValueError(f"invalid pin {self.pin}")


@dataclass(frozen=True)
class InitVarEnv(TaskEnv):
    # ``region`` is an InitVar: a real generated __init__ parameter that
    # ``dataclasses.fields()`` does NOT report.
    region: InitVar[str] = "local"
    tier: ClassVar[str] = "class-level"
    derived: str = field(init=False, default="")

    def __post_init__(self, region):
        super().__post_init__()
        object.__setattr__(self, "derived", f"{self.image}@{region}")


@dataclass(frozen=True)
class InheritedInitVarEnv(InitVarEnv):
    # Inherits the InitVar from its parent and adds one of its own.
    zone: InitVar[str] = "a"

    def __post_init__(self, region, zone):
        super().__post_init__(region)
        object.__setattr__(self, "derived", f"{self.derived}/{zone}")


"""


def _write_pipeline_module(directory: Path, body: str) -> Path:
    """Write a throwaway pipeline module that calls ``from_config``."""

    directory.mkdir(parents=True, exist_ok=True)
    module_path = directory / "pipeline_module.py"
    module_path.write_text(textwrap.dedent(body), encoding="utf-8")
    return module_path


def _load_module(module_path: Path) -> ModuleType:
    """Import a module BY PATH, the way a pipeline script is loaded."""

    name = f"_task_env_from_config_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    return module


def _run_pipeline_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> ModuleType:
    """Load a pipeline module from a directory that is NOT the cwd."""

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(exist_ok=True)
    monkeypatch.chdir(elsewhere)
    return _load_module(_write_pipeline_module(tmp_path / "project", body))


def test_plain_task_env_loads_from_a_config_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "project").mkdir(parents=True, exist_ok=True)
    (tmp_path / "project" / "envs.yaml").write_text("image: python:3.12\n", encoding="utf-8")

    module = _run_pipeline_module(
        tmp_path,
        monkeypatch,
        """
        from tangle_cli.python_pipeline import TaskEnv

        ENV = TaskEnv.from_config("envs.yaml")
        """,
    )

    assert type(module.ENV) is TaskEnv
    assert module.ENV.image == "python:3.12"
    assert module.ENV.dependencies_from is None


def test_relative_path_resolves_against_the_calling_module_not_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A same-named config next to the cwd must NOT win."""

    (tmp_path / "project").mkdir(parents=True, exist_ok=True)
    (tmp_path / "project" / "envs.yaml").write_text("image: from-module\n", encoding="utf-8")
    (tmp_path / "elsewhere").mkdir(parents=True, exist_ok=True)
    (tmp_path / "elsewhere" / "envs.yaml").write_text("image: from-cwd\n", encoding="utf-8")

    module = _run_pipeline_module(
        tmp_path,
        monkeypatch,
        """
        from tangle_cli.python_pipeline import TaskEnv

        ENV = TaskEnv.from_config("envs.yaml")
        """,
    )

    assert module.ENV.image == "from-module"


def test_absolute_path_is_used_as_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "somewhere" / "envs.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("image: python:3.12\n", encoding="utf-8")

    module = _run_pipeline_module(
        tmp_path,
        monkeypatch,
        f"""
        from tangle_cli.python_pipeline import TaskEnv

        ENV = TaskEnv.from_config({str(config)!r})
        """,
    )

    assert module.ENV.image == "python:3.12"


def test_relative_dependencies_from_anchors_to_the_config_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not to the pipeline module, and not to this framework module."""

    config_dir = tmp_path / "project" / "tangle"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (config_dir / "envs.yaml").write_text(
        "image: python:3.12\ndependencies_from: pyproject.toml\n", encoding="utf-8"
    )

    module = _run_pipeline_module(
        tmp_path,
        monkeypatch,
        """
        from tangle_cli.python_pipeline import TaskEnv

        ENV = TaskEnv.from_config("tangle/envs.yaml")
        """,
    )

    assert module.ENV.dependencies_from == (config_dir / "pyproject.toml").resolve()


def test_select_picks_the_case_named_by_the_environment_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "project").mkdir(parents=True, exist_ok=True)
    (tmp_path / "project" / "envs.yaml").write_text(
        textwrap.dedent(
            """
            _select:
              env: DEPLOY_ENVIRONMENT
              cases:
                production:
                  image: registry.example/prod
                staging:
                  image: registry.example/staging
              default:
                image: registry.example/local
            """
        ),
        encoding="utf-8",
    )
    body = """
        from tangle_cli.python_pipeline import TaskEnv

        ENV = TaskEnv.from_config("envs.yaml")
        """

    monkeypatch.setenv("DEPLOY_ENVIRONMENT", "production")
    assert _run_pipeline_module(tmp_path, monkeypatch, body).ENV.image == "registry.example/prod"

    monkeypatch.setenv("DEPLOY_ENVIRONMENT", "staging")
    assert _run_pipeline_module(tmp_path, monkeypatch, body).ENV.image == "registry.example/staging"

    monkeypatch.delenv("DEPLOY_ENVIRONMENT", raising=False)
    assert _run_pipeline_module(tmp_path, monkeypatch, body).ENV.image == "registry.example/local"


def test_select_without_a_default_fails_closed_when_the_variable_is_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "project").mkdir(parents=True, exist_ok=True)
    (tmp_path / "project" / "envs.yaml").write_text(
        textwrap.dedent(
            """
            _select:
              env: DEPLOY_ENVIRONMENT
              cases:
                production:
                  image: registry.example/prod
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("DEPLOY_ENVIRONMENT", raising=False)

    with pytest.raises(CompileError) as excinfo:
        _run_pipeline_module(
            tmp_path,
            monkeypatch,
            """
            from tangle_cli.python_pipeline import TaskEnv

            ENV = TaskEnv.from_config("envs.yaml")
            """,
        )

    assert "DEPLOY_ENVIRONMENT" in str(excinfo.value)
    assert "TaskEnv.from_config" in str(excinfo.value)


def test_missing_file_reports_the_resolved_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(CompileError) as excinfo:
        _run_pipeline_module(
            tmp_path,
            monkeypatch,
            """
            from tangle_cli.python_pipeline import TaskEnv

            ENV = TaskEnv.from_config("envs.yaml")
            """,
        )

    message = str(excinfo.value)
    assert "does not exist" in message
    assert str(tmp_path / "project" / "envs.yaml") in message


def test_multi_config_document_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "project").mkdir(parents=True, exist_ok=True)
    (tmp_path / "project" / "envs.yaml").write_text(
        "- image: one\n- image: two\n", encoding="utf-8"
    )

    with pytest.raises(CompileError) as excinfo:
        _run_pipeline_module(
            tmp_path,
            monkeypatch,
            """
            from tangle_cli.python_pipeline import TaskEnv

            ENV = TaskEnv.from_config("envs.yaml")
            """,
        )

    assert "ONE environment object" in str(excinfo.value)


def test_unknown_field_lists_the_concrete_classes_allowed_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "project").mkdir(parents=True, exist_ok=True)
    (tmp_path / "project" / "envs.yaml").write_text(
        "image: python:3.12\nschedule: '@daily'\n", encoding="utf-8"
    )

    with pytest.raises(CompileError) as excinfo:
        _run_pipeline_module(
            tmp_path,
            monkeypatch,
            """
            from tangle_cli.python_pipeline import TaskEnv

            ENV = TaskEnv.from_config("envs.yaml")
            """,
        )

    message = str(excinfo.value)
    assert "'schedule'" in message
    assert "Allowed fields: image, dependencies_from" in message


def test_config_is_environment_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pipeline concerns have no field to land in and are rejected."""

    (tmp_path / "project").mkdir(parents=True, exist_ok=True)
    for pipeline_only_key in ("file_path", "versioning", "annotations", "subscription"):
        (tmp_path / "project" / "envs.yaml").write_text(
            f"image: python:3.12\n{pipeline_only_key}: whatever\n", encoding="utf-8"
        )
        with pytest.raises(CompileError) as excinfo:
            _run_pipeline_module(
                tmp_path,
                monkeypatch,
                """
                from tangle_cli.python_pipeline import TaskEnv

                ENV = TaskEnv.from_config("envs.yaml")
                """,
            )
        assert f"'{pipeline_only_key}'" in str(excinfo.value)


def test_unknown_key_diagnostics_are_sanitized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hostile key cannot inject control characters or a wall of text."""

    (tmp_path / "project").mkdir(parents=True, exist_ok=True)
    (tmp_path / "project" / "envs.json").write_text(
        '{"image": "python:3.12", "a\\u0007b": 1, "' + "z" * 300 + '": 2}',
        encoding="utf-8",
    )

    with pytest.raises(CompileError) as excinfo:
        _run_pipeline_module(
            tmp_path,
            monkeypatch,
            """
            from tangle_cli.python_pipeline import TaskEnv

            ENV = TaskEnv.from_config("envs.json")
            """,
        )

    message = str(excinfo.value)
    assert "\a" not in message
    assert "a?b" in message
    assert "z" * 300 not in message
    assert "..." in message


def test_subclass_gets_its_own_fields_and_returns_its_own_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "project").mkdir(parents=True, exist_ok=True)
    (tmp_path / "project" / "envs.yaml").write_text(
        "image: python:3.12\naccelerator: gpu\n", encoding="utf-8"
    )

    module = _run_pipeline_module(
        tmp_path, monkeypatch, SUBCLASS_SOURCE + '\nENV = GpuEnv.from_config("envs.yaml")\n'
    )

    assert type(module.ENV) is module.GpuEnv
    assert isinstance(module.ENV, TaskEnv)
    assert module.ENV.accelerator == "gpu"
    assert module.ENV.image == "python:3.12"


def test_subclass_field_validation_is_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The subclass's ``__post_init__`` still REJECTS the case; only its
    message is withheld, since from_config cannot know whether it quotes a
    config value."""

    (tmp_path / "project").mkdir(parents=True, exist_ok=True)
    (tmp_path / "project" / "envs.yaml").write_text("image: python:3.12\n", encoding="utf-8")

    with pytest.raises(CompileError) as excinfo:
        _run_pipeline_module(
            tmp_path, monkeypatch, SUBCLASS_SOURCE + '\nENV = GpuEnv.from_config("envs.yaml")\n'
        )

    message = str(excinfo.value)
    assert "not a valid GpuEnv" in message
    assert "construct GpuEnv(...) directly" in message
    assert "accelerator must be a non-empty string" not in message


def test_subclass_rejects_a_field_it_does_not_declare(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "project").mkdir(parents=True, exist_ok=True)
    (tmp_path / "project" / "envs.yaml").write_text(
        "image: python:3.12\naccelerator: gpu\ntarget: nope\n", encoding="utf-8"
    )

    with pytest.raises(CompileError) as excinfo:
        _run_pipeline_module(
            tmp_path, monkeypatch, SUBCLASS_SOURCE + '\nENV = GpuEnv.from_config("envs.yaml")\n'
        )

    message = str(excinfo.value)
    assert "'target'" in message
    assert "Allowed fields: image, dependencies_from, accelerator" in message


def test_relative_path_without_a_calling_file_is_a_clear_error() -> None:
    """No ``__file__`` (``exec``/REPL) must not silently mean the cwd."""

    namespace: dict[str, object] = {}
    with pytest.raises(CompileError) as excinfo:
        exec(  # noqa: S102 - deliberately a frame with no __file__
            "from tangle_cli.python_pipeline import TaskEnv\n"
            "ENV = TaskEnv.from_config('envs.yaml')\n",
            namespace,
        )

    assert "pass an absolute path" in str(excinfo.value)


def test_a_config_loaded_env_drives_an_ordinary_generated_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing dbt-specific: a plain ``@task`` consumes the loaded env."""

    (tmp_path / "project").mkdir(parents=True, exist_ok=True)
    (tmp_path / "project" / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (tmp_path / "project" / "envs.yaml").write_text(
        "image: python:3.12\ndependencies_from: pyproject.toml\n", encoding="utf-8"
    )

    module = _run_pipeline_module(
        tmp_path,
        monkeypatch,
        """
        from tangle_cli.python_pipeline import TaskEnv, task

        ENV = TaskEnv.from_config("envs.yaml")

        @task(env=ENV)
        def score(value: str) -> str:
            return value
        """,
    )

    assert module.score._task_image == "python:3.12"
    assert module.score._task_dependencies_from == (
        tmp_path / "project" / "pyproject.toml"
    ).resolve()


# ---------------------------------------------------------------------------
# Adversarial: a config file is untrusted input. No diagnostic may echo a
# config VALUE, and no hostile key or subclass message may inject control
# characters or a wall of text into a compile/CI log.


def _expect_compile_error(tmp_path, monkeypatch, config_text, call, *, name="envs.yaml"):
    """Return everything a caller could SEE: the message plus the rendered
    traceback chain. ``str(exc)`` alone is not the disclosure surface — a test
    that only checks it misses ``__cause__``/``__context__``, which
    ``traceback.format_exception`` prints verbatim and which reaches any CI log
    that lets the error escape or logs it with ``exc_info=True``.
    """

    (tmp_path / "project").mkdir(parents=True, exist_ok=True)
    (tmp_path / "project" / name).write_text(textwrap.dedent(config_text), encoding="utf-8")
    with pytest.raises(CompileError) as excinfo:
        _run_pipeline_module(tmp_path, monkeypatch, SUBCLASS_SOURCE + f"\nENV = {call}\n")
    rendered = "".join(traceback.format_exception(excinfo.value))
    return str(excinfo.value) + "\n" + rendered


def test_arbitrary_subclass_exception_text_is_never_quoted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plain ValueError is reported generically — its text is withheld."""

    message = _expect_compile_error(
        tmp_path,
        monkeypatch,
        "image: python:3.12\nsecret: TOP-SECRET-VALUE\n",
        'LeakyEnv.from_config("envs.yaml")',
    )

    assert "TOP-SECRET-VALUE" not in message
    assert "invalid secret" not in message
    # Still actionable without the text: class, field NAMES, and the remedy.
    assert "not a valid LeakyEnv" in message
    assert "fields present: image, secret" in message
    # Actionable without the text: the author is told how to see it themselves.
    assert "construct LeakyEnv(...) directly" in message


def test_nested_scalar_leaf_cannot_leak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reviewer repro A: the leaked value is a leaf of a structured field, so
    no scan of the top-level stringified value could ever have matched it."""

    message = _expect_compile_error(
        tmp_path,
        monkeypatch,
        """
        image: python:3.12
        settings:
          credential: TOP-SECRET-VALUE
        """,
        'NestedLeakyEnv.from_config("envs.yaml")',
    )

    assert "TOP-SECRET-VALUE" not in message
    assert "invalid credential" not in message
    assert "fields present: image, settings" in message


def test_transformed_value_cannot_leak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reviewer repro B: lower cased, sliced, and hex re-encoded copies of the
    value. Content scanning cannot undo any of these; withholding can."""

    message = _expect_compile_error(
        tmp_path,
        monkeypatch,
        "image: python:3.12\nsecret: TOP-SECRET-VALUE\n",
        'TransformedLeakyEnv.from_config("envs.yaml")',
    )

    for disclosure in (
        "TOP-SECRET-VALUE",
        "top-secret-value",
        "SECRET-VALUE",
        "TOP-SECRET-VALUE".encode().hex(),
    ):
        assert disclosure not in message


def test_short_value_cannot_leak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No length threshold exists, because nothing is scanned or quoted."""

    message = _expect_compile_error(
        tmp_path,
        monkeypatch,
        "image: python:3.12\npin: '7'\n",
        'ShortLeakyEnv.from_config("envs.yaml")',
    )

    assert "invalid pin" not in message
    assert "fields present: image, pin" in message


def test_missing_required_field_is_reported_from_the_signature(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The common structural mistake keeps a precise message, derived from the
    signature rather than from withheld TypeError text."""

    message = _expect_compile_error(
        tmp_path,
        monkeypatch,
        "dependencies_from: pyproject.toml\n",
        'TaskEnv.from_config("envs.yaml")',
    )

    assert "missing required field(s): image" in message


def test_hostile_select_sibling_key_is_capped_through_from_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``_select`` error forwarded by from_config must be sanitized too."""

    (tmp_path / "project").mkdir(parents=True, exist_ok=True)
    (tmp_path / "project" / "envs.json").write_text(
        '{"_select": {"env": "E", "cases": {"a": {"image": "i"}}}, '
        '"' + "s" * 300 + '": 1}',
        encoding="utf-8",
    )

    with pytest.raises(CompileError) as excinfo:
        _run_pipeline_module(
            tmp_path,
            monkeypatch,
            """
            from tangle_cli.python_pipeline import TaskEnv

            ENV = TaskEnv.from_config("envs.json")
            """,
        )

    message = str(excinfo.value)
    assert "only regular key" in message
    assert "s" * 300 not in message
    assert "..." in message


def test_hostile_select_unexpected_key_is_scrubbed_through_from_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "project").mkdir(parents=True, exist_ok=True)
    (tmp_path / "project" / "envs.json").write_text(
        '{"_select": {"env": "E", "cases": {"a": {"image": "i"}}, '
        '"we\\u0007ird": 1, "' + "u" * 300 + '": 2}}',
        encoding="utf-8",
    )

    with pytest.raises(CompileError) as excinfo:
        _run_pipeline_module(
            tmp_path,
            monkeypatch,
            """
            from tangle_cli.python_pipeline import TaskEnv

            ENV = TaskEnv.from_config("envs.json")
            """,
        )

    message = str(excinfo.value)
    assert "supports only 'env', 'cases', and 'default'" in message
    assert "\a" not in message
    assert "we?ird" in message
    assert "u" * 300 not in message


def test_non_string_select_key_is_rendered_safely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """YAML permits non-string keys; the renderer must not choke on them."""

    (tmp_path / "project").mkdir(parents=True, exist_ok=True)
    (tmp_path / "project" / "envs.yaml").write_text(
        textwrap.dedent(
            """
            _select:
              env: E
              cases:
                a:
                  image: i
            7: sibling
            """
        ),
        encoding="utf-8",
    )

    with pytest.raises(CompileError) as excinfo:
        _run_pipeline_module(
            tmp_path,
            monkeypatch,
            """
            from tangle_cli.python_pipeline import TaskEnv

            ENV = TaskEnv.from_config("envs.yaml")
            """,
        )

    assert "only regular key" in str(excinfo.value)
    assert "'7'" in str(excinfo.value)


def test_init_var_is_an_accepted_constructor_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``dataclasses.fields()`` omits InitVars; the signature does not."""

    (tmp_path / "project").mkdir(parents=True, exist_ok=True)
    (tmp_path / "project" / "envs.yaml").write_text(
        "image: python:3.12\nregion: prod\n", encoding="utf-8"
    )

    module = _run_pipeline_module(
        tmp_path, monkeypatch, SUBCLASS_SOURCE + '\nENV = InitVarEnv.from_config("envs.yaml")\n'
    )

    assert module.ENV.derived == "python:3.12@prod"


def test_inherited_init_vars_are_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "project").mkdir(parents=True, exist_ok=True)
    (tmp_path / "project" / "envs.yaml").write_text(
        "image: python:3.12\nregion: prod\nzone: b\n", encoding="utf-8"
    )

    module = _run_pipeline_module(
        tmp_path,
        monkeypatch,
        SUBCLASS_SOURCE + '\nENV = InheritedInitVarEnv.from_config("envs.yaml")\n',
    )

    assert module.ENV.derived == "python:3.12@prod/b"


def test_class_var_and_non_init_field_are_not_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Widening to InitVars must not also open ClassVars/``init=False``."""

    (tmp_path / "project").mkdir(parents=True, exist_ok=True)
    for rejected_key in ("tier", "derived"):
        (tmp_path / "project" / "envs.yaml").write_text(
            f"image: python:3.12\n{rejected_key}: nope\n", encoding="utf-8"
        )
        with pytest.raises(CompileError) as excinfo:
            _run_pipeline_module(
                tmp_path,
                monkeypatch,
                SUBCLASS_SOURCE + '\nENV = InitVarEnv.from_config("envs.yaml")\n',
            )
        message = str(excinfo.value)
        assert f"'{rejected_key}'" in message
        assert "Allowed fields: image, dependencies_from, region" in message


def test_constructor_exception_is_absent_from_the_traceback_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``raise ... from exc`` would publish the withheld text on __cause__, and
    a bare re-raise inside the handler would publish it on __context__; either
    way ``traceback.format_exception`` prints it. Neither may be set."""

    (tmp_path / "project").mkdir(parents=True, exist_ok=True)
    (tmp_path / "project" / "envs.yaml").write_text(
        "image: python:3.12\nsecret: TOP-SECRET-VALUE\n", encoding="utf-8"
    )

    with pytest.raises(CompileError) as excinfo:
        _run_pipeline_module(
            tmp_path,
            monkeypatch,
            SUBCLASS_SOURCE + '\nENV = TransformedLeakyEnv.from_config("envs.yaml")\n',
        )

    error = excinfo.value
    assert error.__cause__ is None
    assert error.__context__ is None
    rendered = "".join(traceback.format_exception(error))
    for disclosure in (
        "TOP-SECRET-VALUE",
        "top-secret-value",
        "SECRET-VALUE",
        "TOP-SECRET-VALUE".encode().hex(),
        "invalid credential",
    ):
        assert disclosure not in rendered


def test_direct_construction_still_raises_an_ordinary_value_error() -> None:
    """The withholding policy applies to from_config diagnostics only. Calling
    a constructor directly is unchanged, which is what the from_config message
    points the author at."""

    with pytest.raises(ValueError, match="TaskEnv.image must be a non-empty string"):
        TaskEnv(image="")
