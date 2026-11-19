"""Default task annotations: ``@pipeline(task_annotations=...)`` and
``subpipeline(child).with_task_annotations(...)``."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from tangle_cli.pipeline_compiler import compile_pipeline
from tangle_cli.python_pipeline import pipeline, subpipeline
from tangle_cli.python_pipeline.errors import CompileError

_HEADER = """\
from tangle_cli.python_pipeline import pipeline, ref, subpipeline

op = ref(name="op")
"""


def _compile(tmp_path: Path, body: str, *, config: str | None = None, overrides=None) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    (src / "pipeline.py").write_text(_HEADER + textwrap.dedent(body), encoding="utf-8")
    if config is not None:
        (src / "config.yaml").write_text(config, encoding="utf-8")
    out = tmp_path / "out" / "compiled.yaml"
    compile_pipeline(src / "pipeline.py", out, overrides, pipeline_name="Root")
    return out


def _tasks(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["implementation"]["graph"]["tasks"]


def _annotations(path: Path, task_id: str) -> dict | None:
    return _tasks(path)[task_id].get("annotations")


def _child(parent: Path, task_id: str) -> Path:
    url = _tasks(parent)[task_id]["componentRef"]["url"]
    return (parent.parent / url.removeprefix("file://")).resolve()


def test_pipeline_defaults_reach_its_own_tasks_and_a_task_wins_per_key(tmp_path):
    out = _compile(
        tmp_path,
        """
        @pipeline("Child")
        def child():
            op.named("Child Task")()

        @pipeline("Root", task_annotations={"team": "root", "tier": "default"})
        def root():
            op.named("Plain")()
            op.with_annotations({"tier": "task", "extra": "task"}).named("Annotated")()
            subpipeline(child).named("Run Child")()
        """,
    )

    assert _annotations(out, "Plain") == {"team": "root", "tier": "default"}
    assert _annotations(out, "Annotated") == {"team": "root", "tier": "task", "extra": "task"}
    assert _annotations(out, "Run Child") is None
    assert _annotations(_child(out, "Run Child"), "Child Task") is None


def test_a_defaults_function_reads_the_resolved_config_without_a_cfg_parameter(tmp_path):
    out = _compile(
        tmp_path,
        """
        @pipeline(
            "Root",
            config="config.yaml",
            task_annotations=lambda cfg: {"account": cfg.service_account},
        )
        def root():
            op.named("Task")()
        """,
        config="service_account: base@example.com\n",
        overrides={"service_account": "override@example.com"},
    )

    assert _annotations(out, "Task") == {"account": "override@example.com"}


def test_call_site_defaults_reach_the_whole_subtree_and_the_nearest_wins(tmp_path):
    out = _compile(
        tmp_path,
        """
        @pipeline("Grandchild")
        def grandchild():
            op.named("GC Plain")()
            op.with_annotations({"k": "task"}).named("GC Annotated")()

        @pipeline("Child", task_annotations={"k": "child-own", "own": "child-own"})
        def child():
            op.named("Child Task")()
            subpipeline(grandchild).with_task_annotations({"k": "child-call"}).named("Run GC")()

        @pipeline("Root")
        def root():
            subpipeline(child).with_task_annotations({"k": "root-call", "r": "root-call"}).named(
                "Run Child"
            )()
        """,
    )
    child = _child(out, "Run Child")
    grandchild = _child(child, "Run GC")

    assert _annotations(out, "Run Child") is None
    assert _annotations(child, "Child Task") == {"k": "root-call", "own": "child-own", "r": "root-call"}
    assert _annotations(child, "Run GC") is None
    assert _annotations(grandchild, "GC Plain") == {"k": "child-call", "r": "root-call"}
    assert _annotations(grandchild, "GC Annotated") == {"k": "task", "r": "root-call"}


def test_a_child_shares_a_sidecar_only_with_calls_pushing_the_same_defaults(tmp_path):
    out = _compile(
        tmp_path,
        """
        @pipeline("Child")
        def child():
            op.named("Child Task")()

        @pipeline("Root")
        def root():
            subpipeline(child).with_task_annotations({"env": "a"}).named("A")()
            subpipeline(child).with_task_annotations({"env": "b"}).named("B")()
            subpipeline(child).with_task_annotations({"env": "a"}).named("A Again")()
        """,
    )

    assert _child(out, "A") == _child(out, "A Again")
    assert _child(out, "A") != _child(out, "B")
    assert _annotations(_child(out, "A"), "Child Task") == {"env": "a"}
    assert _annotations(_child(out, "B"), "Child Task") == {"env": "b"}


def test_descendants_of_a_shared_child_split_by_the_defaults_pushed_above_them(tmp_path):
    out = _compile(
        tmp_path,
        """
        @pipeline("Grandchild")
        def grandchild():
            op.named("GC Task")()

        @pipeline("Child")
        def child():
            subpipeline(grandchild).named("Run GC")()

        @pipeline("Root")
        def root():
            subpipeline(child).with_task_annotations({"env": "a"}).named("A")()
            subpipeline(child).with_task_annotations({"env": "b"}).named("B")()
        """,
    )
    grandchild_a = _child(_child(out, "A"), "Run GC")
    grandchild_b = _child(_child(out, "B"), "Run GC")

    assert grandchild_a != grandchild_b
    assert _annotations(grandchild_a, "GC Task") == {"env": "a"}
    assert _annotations(grandchild_b, "GC Task") == {"env": "b"}


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ('{"team": "left"}', '{"team": "right"}'),
        ('lambda cfg: {"team": cfg.left}', 'lambda cfg: {"team": cfg.right}'),
    ],
    ids=["mapping", "function"],
)
def test_wrappers_differing_only_in_their_own_defaults_get_separate_sidecars(tmp_path, left, right):
    out = _compile(
        tmp_path,
        f"""
        def child():
            op.named("Child Task")()

        left = pipeline("Child", task_annotations={left})(child)
        right = pipeline("Child", task_annotations={right})(child)

        @pipeline("Root")
        def root():
            subpipeline(left).named("Left")()
            subpipeline(right).named("Right")()
        """,
        config="left: left\nright: right\n",
    )

    assert _child(out, "Left") != _child(out, "Right")
    assert _annotations(_child(out, "Left"), "Child Task") == {"team": "left"}
    assert _annotations(_child(out, "Right"), "Child Task") == {"team": "right"}


@pytest.mark.parametrize(
    "declared",
    ['{"team": "left"}', 'lambda cfg: {"team": cfg.left}'],
    ids=["mapping", "function"],
)
def test_wrappers_with_the_same_own_defaults_share_a_sidecar(tmp_path, declared):
    out = _compile(
        tmp_path,
        f"""
        def child():
            op.named("Child Task")()

        first = pipeline("Child", task_annotations={declared})(child)
        second = pipeline("Child", task_annotations={declared})(child)

        @pipeline("Root")
        def root():
            subpipeline(first).named("First")()
            subpipeline(second).named("Second")()
            subpipeline(first).named("First Again")()
        """,
        config="left: left\n",
    )

    assert _child(out, "First") == _child(out, "Second") == _child(out, "First Again")
    assert _annotations(_child(out, "First"), "Child Task") == {"team": "left"}


def test_a_child_defaults_function_imports_siblings_of_the_child(tmp_path):
    src = tmp_path / "src"
    child_dir = src / "child"
    child_dir.mkdir(parents=True)
    (child_dir / "child_annotation_helper.py").write_text(
        "def annotations_for(cfg):\n    return {'team': 'helper-' + cfg.team}\n", encoding="utf-8"
    )
    (child_dir / "config.yaml").write_text("team: child\n", encoding="utf-8")
    (child_dir / "child_pipeline.py").write_text(
        _HEADER
        + textwrap.dedent(
            """
            def defaults(cfg):
                from child_annotation_helper import annotations_for

                return annotations_for(cfg)

            @pipeline("Child", task_annotations=defaults)
            def child():
                op.named("Child Task")()
            """
        ),
        encoding="utf-8",
    )
    (src / "pipeline.py").write_text(
        _HEADER
        + textwrap.dedent(
            """
            from child.child_pipeline import child

            @pipeline("Root")
            def root():
                subpipeline(child).named("Run Child")()
            """
        ),
        encoding="utf-8",
    )
    nested = tmp_path / "nested" / "compiled.yaml"
    direct = tmp_path / "direct" / "compiled.yaml"

    compile_pipeline(src / "pipeline.py", nested, None, pipeline_name="Root")
    compile_pipeline(child_dir / "child_pipeline.py", direct, None, pipeline_name="Child")

    assert _annotations(_child(nested, "Run Child"), "Child Task") == {"team": "helper-child"}
    assert _annotations(direct, "Child Task") == {"team": "helper-child"}


@pytest.mark.parametrize("order", [("plain", "configured"), ("configured", "plain")])
def test_a_child_defaults_function_without_a_config_fails_whichever_sibling_comes_first(tmp_path, order):
    first, second = order
    with pytest.raises(
        CompileError,
        match=r"config file not found: .*Pipeline 'Child' has a task_annotations function",
    ):
        _compile(
            tmp_path,
            f"""
            def child():
                op.named("Child Task")()

            plain = pipeline("Child")(child)
            configured = pipeline("Child", task_annotations=lambda cfg: {{"team": cfg.team}})(child)

            @pipeline("Root")
            def root():
                subpipeline({first}).named("First")()
                subpipeline({second}).named("Second")()
            """,
        )


@pytest.mark.parametrize("declared", ['"davies"', 'lambda cfg: "davies"'])
def test_pipeline_defaults_that_are_not_a_mapping_fail_the_compile(tmp_path, declared):
    with pytest.raises(CompileError, match=r"'Root'.*must be a mapping or a function of cfg returning one, got str"):
        _compile(
            tmp_path,
            f"""
            @pipeline("Root", task_annotations={declared})
            def root():
                op.named("Task")()
            """,
            config="{}\n",
        )


@pytest.mark.parametrize(
    ("ann", "message"),
    [
        (lambda cfg: {"k": "v"}, "takes a mapping, got function"),
        ({"k": Path("gs://bucket")}, "value for 'k' must be a string or another scalar"),
    ],
)
def test_call_site_defaults_reject_what_cannot_key_a_sidecar(ann, message):
    @pipeline("Child")
    def child():
        pass

    with pytest.raises(CompileError, match=message):
        subpipeline(child).with_task_annotations(ann)
