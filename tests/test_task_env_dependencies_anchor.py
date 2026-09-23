"""A relative ``TaskEnv.dependencies_from`` anchors to the AUTHOR's file.

Regression coverage for a shipped bug: ``TaskEnv.__post_init__`` used to reach
the definition site with a fixed ``f_back.f_back``, which assumes exactly one
intermediate frame (the dataclass-generated ``__init__``). Every subclass that
overrides ``__post_init__`` and calls ``super()`` inserts another frame, so a
relative ``dependencies_from`` passed to such a subclass resolved against the
module that *defines the subclass* — for a downstream env that is the installed
package — instead of the pipeline file that wrote the path.

The tests therefore keep three directories apart and assert which one wins:

    <tmp>/envlib/    the module DEFINING the subclasses (the wrong answer)
    <tmp>/project/   the module CONSTRUCTING the envs   (the right answer)
    <tmp>/elsewhere/ the working directory              (also wrong)

Each one holds a ``deps.toml``, so a misresolution lands on a file that exists
and cannot hide behind a "file not found". The envs are always constructed from
a written-to-disk author module rather than from this test file, so the
expected anchor is a genuine third file and the frame stack is the real one.
"""

from __future__ import annotations

import importlib.util
import sys
import textwrap
import uuid
from pathlib import Path
from types import ModuleType

import pytest

from tangle_cli.python_pipeline import TaskEnv

#: The env-subclass library: lives in its own directory, exactly like an
#: installed package that ships a ``TaskEnv`` subclass. ``__MODULE__`` is
#: substituted with a unique module name per test.
ENV_LIBRARY_SOURCE = '''
"""A library of TaskEnv subclasses, defined AWAY from any pipeline file."""

from __future__ import annotations

from dataclasses import dataclass

from tangle_cli.python_pipeline import TaskEnv


@dataclass(frozen=True)
class DepthOneEnv(TaskEnv):
    """Overrides __post_init__ and calls super(): ONE extra frame."""

    accelerator: str = "cpu"

    def __post_init__(self):
        super().__post_init__()
        if not isinstance(self.accelerator, str) or not self.accelerator:
            raise ValueError("DepthOneEnv.accelerator must be a non-empty string")


@dataclass(frozen=True)
class DepthTwoEnv(DepthOneEnv):
    """Overrides __post_init__ again: TWO extra frames."""

    target: str = "dev"

    def __post_init__(self):
        super().__post_init__()
        if not isinstance(self.target, str) or not self.target:
            raise ValueError("DepthTwoEnv.target must be a non-empty string")


def _finish(env) -> None:
    """Run the base validation through a plain function.

    This frame holds the env under a name that is NOT ``self``, so it is not
    recognizable as construction machinery on its own — the generated
    ``__init__`` further out is what still marks the boundary.
    """

    TaskEnv.__post_init__(env)


@dataclass(frozen=True)
class IndirectEnv(TaskEnv):
    """Delegates to the base __post_init__ through a module-level function."""

    def __post_init__(self):
        _finish(self)
'''

#: Author-module prologue: import the library by its per-test module name.
_IMPORT_LIBRARY = "from __MODULE__ import DepthOneEnv, DepthTwoEnv, IndirectEnv"


def _load_module(module_path: Path) -> ModuleType:
    """Import a module BY PATH, the way a pipeline script is loaded."""

    name = f"_task_env_anchor_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    return module


def _run_author_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str
) -> tuple[ModuleType, Path]:
    """Lay out the three directories and run an author module in ``project/``.

    Returns the loaded module and the author directory — the only correct
    anchor for a relative ``dependencies_from``.
    """

    library_name = f"_task_env_library_{uuid.uuid4().hex}"
    library_dir = tmp_path / "envlib"
    project_dir = tmp_path / "project"
    cwd_dir = tmp_path / "elsewhere"
    for directory in (library_dir, project_dir, cwd_dir):
        directory.mkdir(parents=True, exist_ok=True)
    (library_dir / f"{library_name}.py").write_text(ENV_LIBRARY_SOURCE, encoding="utf-8")

    # Same file name in all three: only the DIRECTORY distinguishes them, and
    # all three exist so a wrong anchor still names a real file.
    (library_dir / "deps.toml").write_text("# subclass library\n", encoding="utf-8")
    (project_dir / "deps.toml").write_text("# author\n", encoding="utf-8")
    (cwd_dir / "deps.toml").write_text("# working directory\n", encoding="utf-8")

    monkeypatch.syspath_prepend(str(library_dir))
    monkeypatch.chdir(cwd_dir)

    source = textwrap.dedent(body).replace("__MODULE__", library_name)
    module_path = project_dir / "pipeline_module.py"
    module_path.write_text(source, encoding="utf-8")
    return _load_module(module_path), project_dir


def _assert_anchored_to_author(env: TaskEnv, tmp_path: Path, project_dir: Path) -> None:
    """The author's directory won — not the library's, not the cwd's."""

    assert env.dependencies_from == (project_dir / "deps.toml").resolve()
    assert env.dependencies_from != (tmp_path / "envlib" / "deps.toml").resolve()
    assert env.dependencies_from != (tmp_path / "elsewhere" / "deps.toml").resolve()


def test_base_task_env_anchors_to_the_constructing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Depth 0: the behaviour that already worked must keep working."""

    module, project_dir = _run_author_module(
        tmp_path,
        monkeypatch,
        """
        from tangle_cli.python_pipeline import TaskEnv

        ENV = TaskEnv(image="python:3.12", dependencies_from="deps.toml")
        """,
    )

    _assert_anchored_to_author(module.ENV, tmp_path, project_dir)


def test_subclass_one_level_deep_anchors_to_the_author_not_its_own_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Depth 1: the exact shipped bug — one extra ``__post_init__`` frame."""

    module, project_dir = _run_author_module(
        tmp_path,
        monkeypatch,
        f"""
        {_IMPORT_LIBRARY}

        ENV = DepthOneEnv(
            image="python:3.12", dependencies_from="deps.toml", accelerator="gpu"
        )
        """,
    )

    _assert_anchored_to_author(module.ENV, tmp_path, project_dir)


def test_subclass_two_levels_deep_anchors_to_the_author(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Depth 2: fails for the shipped code AND for any fixed frame count."""

    module, project_dir = _run_author_module(
        tmp_path,
        monkeypatch,
        f"""
        {_IMPORT_LIBRARY}

        ENV = DepthTwoEnv(
            image="python:3.12",
            dependencies_from="deps.toml",
            accelerator="gpu",
            target="prod",
        )
        """,
    )

    _assert_anchored_to_author(module.ENV, tmp_path, project_dir)


def test_subclass_delegating_through_a_library_function_anchors_to_the_author(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An intervening frame that does not bind ``self`` is still skipped."""

    module, project_dir = _run_author_module(
        tmp_path,
        monkeypatch,
        f"""
        {_IMPORT_LIBRARY}

        ENV = IndirectEnv(image="python:3.12", dependencies_from="deps.toml")
        """,
    )

    _assert_anchored_to_author(module.ENV, tmp_path, project_dir)


def test_construction_inside_a_function_or_method_anchors_to_the_author(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The author's own call depth is irrelevant, and a method's ``self`` — a
    DIFFERENT object — must not be mistaken for construction machinery."""

    module, project_dir = _run_author_module(
        tmp_path,
        monkeypatch,
        f"""
        {_IMPORT_LIBRARY}


        def build():
            return DepthTwoEnv(
                image="python:3.12", dependencies_from="deps.toml", target="prod"
            )


        def build_nested():
            return build()


        class Factory:
            def build(self):
                return DepthTwoEnv(
                    image="python:3.12", dependencies_from="deps.toml", target="prod"
                )


        FROM_FUNCTION = build()
        FROM_NESTED = build_nested()
        FROM_METHOD = Factory().build()
        """,
    )

    for env in (module.FROM_FUNCTION, module.FROM_NESTED, module.FROM_METHOD):
        _assert_anchored_to_author(env, tmp_path, project_dir)


def test_subclass_declared_in_the_author_file_anchors_to_that_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the author declares the subclass themselves, their file is both the
    defining and the constructing module — and construction inside a function
    of that file must not walk out past it."""

    module, project_dir = _run_author_module(
        tmp_path,
        monkeypatch,
        """
        from dataclasses import dataclass

        from tangle_cli.python_pipeline import TaskEnv


        @dataclass(frozen=True)
        class LocalEnv(TaskEnv):
            def __post_init__(self):
                super().__post_init__()


        def build():
            return LocalEnv(image="python:3.12", dependencies_from="deps.toml")


        ENV = build()
        """,
    )

    _assert_anchored_to_author(module.ENV, tmp_path, project_dir)


def test_absolute_dependencies_from_is_never_re_anchored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An absolute path is kept as written at every depth."""

    absolute = tmp_path / "anywhere" / "deps.toml"
    absolute.parent.mkdir(parents=True, exist_ok=True)
    absolute.write_text("# explicit\n", encoding="utf-8")

    module, _ = _run_author_module(
        tmp_path,
        monkeypatch,
        f"""
        {_IMPORT_LIBRARY}
        from pathlib import Path

        ABSOLUTE = Path({str(absolute)!r})

        ENV = DepthTwoEnv(
            image="python:3.12", dependencies_from=ABSOLUTE, target="prod"
        )
        """,
    )

    assert module.ENV.dependencies_from == absolute.resolve()


def test_from_config_still_anchors_to_the_config_file_for_a_deep_subclass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``from_config`` pre-resolves ``dependencies_from`` against the CONFIG
    file's directory, and that anchoring is unchanged: neither the author's
    directory nor the subclass's may win here."""

    config_dir = tmp_path / "project" / "conf"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "deps.toml").write_text("# config-relative\n", encoding="utf-8")
    (config_dir / "envs.yaml").write_text(
        "image: python:3.12\ndependencies_from: deps.toml\ntarget: prod\n",
        encoding="utf-8",
    )

    module, project_dir = _run_author_module(
        tmp_path,
        monkeypatch,
        f"""
        {_IMPORT_LIBRARY}

        ENV = DepthTwoEnv.from_config("conf/envs.yaml")
        """,
    )

    assert module.ENV.dependencies_from == (config_dir / "deps.toml").resolve()
    assert module.ENV.dependencies_from != (project_dir / "deps.toml").resolve()
    assert module.ENV.target == "prod"
