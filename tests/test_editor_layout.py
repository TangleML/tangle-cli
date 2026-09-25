"""Typed sugar for the ``editor.*`` layout annotations.

``.with_position(x, y)`` and ``@pipeline(flow_direction=...)`` restate what
an author can already hand-write as annotations. The contract pinned here:

* pure sugar, proven by BYTE identity of the whole compiled bundle against
  the hand-written form — the property a YAML→Python decompiler needs;
* chainable, immutable, and last-write-wins on ``editor.position``
  regardless of spelling (the sugar routes through ``with_annotations``);
* validation refuses what the editor could not parse back, and never echoes
  the rejected value;
* layout is descriptive: componentRefs, sidecar bytes and the cache are
  untouched;
* the runner's auto-layout gate only counts a NON-ZERO position as layout.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest
import yaml

from tangle_cli.editor_layout import (
    FLOW_DIRECTION_ANNOTATION,
    FLOW_DIRECTIONS,
    POSITION_ANNOTATION,
    position_annotation_value,
    validate_flow_direction,
)
from tangle_cli.pipeline_compiler import compile_pipeline
from tangle_cli.pipeline_runner import PipelineRunnerHooks
from tangle_cli.pipelines import POSITION_ANNOTATION as PIPELINES_POSITION_ANNOTATION
from tangle_cli.python_pipeline import pipeline, ref
from tangle_cli.python_pipeline.errors import CompileError, InvalidEditorLayoutError


# ---------------------------------------------------------------------------
# Compile helpers. Each compile runs in its own directory: the loader caches
# modules by name, and output paths are embedded in the bundle.


_TASK_SOURCE = '''
from tangle_cli.python_pipeline import Out, pipeline, task


@task(image="python:3.12")
def greet(greeting: str = "hi"):
    """Write a greeting.

    Metadata:
        Name: Greet
    """
    print(greeting)


@pipeline(__DECORATOR_ARGS__)
def laid_out() -> Out[str]:
    run_greet = greet__TASK_SUFFIX__()
    return run_greet
'''


def _source(*, decorator_args: str = '"Laid Out"', task_suffix: str = "") -> str:
    return textwrap.dedent(
        _TASK_SOURCE.replace("__DECORATOR_ARGS__", decorator_args).replace(
            "__TASK_SUFFIX__", task_suffix
        )
    )


def _compile(
    tmp_path: Path, source: str, case: str, *, pipeline_name: str | None = None
) -> Path:
    """Compile ``source`` in its own directory; return the output path."""
    case_dir = tmp_path / case
    case_dir.mkdir(parents=True, exist_ok=True)
    script = case_dir / "pipeline.py"
    script.write_text(source, encoding="utf-8")
    out = case_dir / "compiled.yaml"
    compile_pipeline(script, out, pipeline_name=pipeline_name)
    return out


def _bundle(out: Path) -> dict[str, bytes]:
    """Every file the compile wrote, keyed by name relative to its directory."""
    return {
        p.name: p.read_bytes()
        for p in sorted(out.parent.rglob("*"))
        if p.is_file() and p.suffix in {".yaml", ".yml"}
    }


def _task_annotations(out: Path, task_id: str = "Run Greet") -> dict:
    data = yaml.safe_load(out.read_text(encoding="utf-8"))
    return data["implementation"]["graph"]["tasks"][task_id].get("annotations", {})


def _root_annotations(out: Path) -> dict:
    data = yaml.safe_load(out.read_text(encoding="utf-8"))
    return data.get("metadata", {}).get("annotations", {})


# ---------------------------------------------------------------------------
# Pure sugar: byte identity with the hand-written annotation.


def test_a_position_compiles_to_the_hand_written_annotation(tmp_path):
    """Byte identity across the bundle, not just an equal parsed value."""
    sugar = _compile(
        tmp_path, _source(task_suffix=".with_position(300, 120)"), "sugar"
    )
    manual = _compile(
        tmp_path,
        _source(
            task_suffix=(
                ".with_annotations({\"editor.position\": "
                "'{\"x\": 300, \"y\": 120}'})"
            )
        ),
        "manual",
    )

    assert _bundle(sugar) == _bundle(manual)
    assert _task_annotations(sugar) == {"editor.position": '{"x": 300, "y": 120}'}


def test_a_flow_direction_compiles_to_the_hand_written_annotation(tmp_path):
    """Same for the root keyword, including its place in key order."""
    sugar = _compile(
        tmp_path,
        _source(
            decorator_args=(
                '"Laid Out", annotations={"sdk": "x"}, '
                'flow_direction="left-to-right"'
            )
        ),
        "sugar",
    )
    manual = _compile(
        tmp_path,
        _source(
            decorator_args=(
                '"Laid Out", annotations={"sdk": "x", '
                '"editor.flow-direction": "left-to-right"}'
            )
        ),
        "manual",
    )

    assert _bundle(sugar) == _bundle(manual)
    assert list(_root_annotations(sugar)) == ["sdk", "editor.flow-direction"]


def test_the_position_value_matches_the_corpus_byte_for_byte():
    """The serialized form itself: a JSON object STRING, ``x`` then ``y``,
    ``json.dumps`` spacing — what ``pipelines layout`` and the corpus use."""
    assert position_annotation_value(300, 120) == '{"x": 300, "y": 120}'
    assert position_annotation_value(-10, 80) == '{"x": -10, "y": 80}'
    assert position_annotation_value(1.5, 2.5) == '{"x": 1.5, "y": 2.5}'
    assert (
        position_annotation_value(10, 20, width=250, height=100)
        == '{"x": 10, "y": 20, "width": 250, "height": 100}'
    )
    # Optional dimensions are OMITTED, not emitted as null: the editor reads
    # a missing width as "use the default", but a null would be a number-ish
    # field it has to reject.
    assert "width" not in position_annotation_value(10, 20)
    assert json.loads(position_annotation_value(10, 20)) == {"x": 10, "y": 20}


def test_the_shared_module_stays_importable_without_the_authoring_stack():
    """Importing ``python_pipeline`` pulls the codegen stack and its optional
    dependencies, which a minimal install does not have — and the layout
    command and submit-time gate both read these constants."""
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import tangle_cli.editor_layout as m; "
            "assert not [n for n in sys.modules "
            "if n.startswith('tangle_cli.python_pipeline')], "
            "sorted(n for n in sys.modules if n.startswith('tangle_cli')); "
            "print(m.position_annotation_value(1, 2))",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == '{"x": 1, "y": 2}'


def test_the_shared_helpers_default_to_the_stdlib_error():
    """``error_cls`` injection keeps the shared module independent of the
    authoring error hierarchy."""
    with pytest.raises(ValueError):
        position_annotation_value("x", 0)
    with pytest.raises(ValueError):
        validate_flow_direction("sideways")


def test_the_authoring_surfaces_inject_the_precise_error_type():
    with pytest.raises(InvalidEditorLayoutError):
        ref(url="file://./greet.yaml").with_position("x", 0)

    with pytest.raises(InvalidEditorLayoutError):

        @pipeline("P", flow_direction="sideways")
        def a_pipeline():
            return None


def test_the_key_names_have_one_definition():
    """``pipelines`` (CLI layout) and ``pipeline_runner`` (auto-layout gate)
    read the same constant the sugar writes, so the three cannot drift."""
    assert POSITION_ANNOTATION == "editor.position"
    assert FLOW_DIRECTION_ANNOTATION == "editor.flow-direction"
    assert PIPELINES_POSITION_ANNOTATION is POSITION_ANNOTATION


# ---------------------------------------------------------------------------
# Handle semantics: chaining, immutability, collisions.


def test_position_chains_with_the_other_combinators(tmp_path):
    """Order does not matter, and the task ID still comes from ``.named``."""
    out = _compile(
        tmp_path,
        _source(task_suffix='.named("Greet Step").with_position(40, 50)'),
        "chain",
    )
    reversed_order = _compile(
        tmp_path,
        _source(task_suffix='.with_position(40, 50).named("Greet Step")'),
        "chain_reversed",
    )

    assert _task_annotations(out, "Greet Step") == {
        "editor.position": '{"x": 40, "y": 50}'
    }
    assert _bundle(out) == _bundle(reversed_order)


def test_position_returns_a_new_handle_and_leaves_the_original_alone():
    """Immutable composition: a shared base ref can be positioned twice."""
    base = ref(url="file://./greet.yaml")
    left = base.with_position(0, 0)
    right = base.with_position(100, 200)

    assert base.annotations in (None, {})
    assert left is not base and right is not base
    assert left.annotations == {"editor.position": '{"x": 0, "y": 0}'}
    assert right.annotations == {"editor.position": '{"x": 100, "y": 200}'}


def test_the_last_write_to_the_position_key_wins_either_way():
    """Neither spelling shadows the other; they are simply ordered."""
    handle = ref(url="file://./greet.yaml")

    sugar_last = handle.with_annotations(
        {"editor.position": '{"x": 1, "y": 1}'}
    ).with_position(2, 2)
    manual_last = handle.with_position(2, 2).with_annotations(
        {"editor.position": '{"x": 1, "y": 1}'}
    )

    assert sugar_last.annotations["editor.position"] == '{"x": 2, "y": 2}'
    assert manual_last.annotations["editor.position"] == '{"x": 1, "y": 1}'


def test_a_position_leaves_unrelated_annotations_alone():
    handle = ref(url="file://./greet.yaml").with_annotations(
        {"cloud-pipelines.net/launchers/generic/resources.memory": "50Gi"}
    )

    positioned = handle.with_position(10, 20)

    assert positioned.annotations == {
        "cloud-pipelines.net/launchers/generic/resources.memory": "50Gi",
        "editor.position": '{"x": 10, "y": 20}',
    }


def test_the_typed_flow_direction_wins_over_the_same_key_in_annotations():
    """Applied last. Not an error: a shared annotations dict plus an explicit
    keyword means the explicit one."""

    @pipeline(
        "P",
        annotations={"editor.flow-direction": "top-to-bottom"},
        flow_direction="left-to-right",
    )
    def a_pipeline():
        return None

    assert a_pipeline.annotations == {"editor.flow-direction": "left-to-right"}


def test_flow_direction_is_optional_and_absent_by_default():
    """No keyword means no annotation; inventing a default would change the
    emitted document for every existing pipeline."""

    @pipeline("P")
    def a_pipeline():
        return None

    assert a_pipeline.annotations == {}


# ---------------------------------------------------------------------------
# Validation. A rejected value never appears in the diagnostic.


@pytest.mark.parametrize(
    "kwargs",
    [
        {"x": "300", "y": 120},
        {"x": 300, "y": "120"},
        {"x": None, "y": 0},
        {"x": True, "y": 0},
        {"x": 0, "y": False},
        {"x": [300], "y": 120},
        {"x": {"x": 1}, "y": 120},
        {"x": float("nan"), "y": 0},
        {"x": 0, "y": float("inf")},
        {"x": 0, "y": float("-inf")},
    ],
)
def test_an_unusable_coordinate_is_refused_without_echoing_it(kwargs):
    """Each of these is unparseable JSON or silently misread (``True`` -> 1)."""
    with pytest.raises(InvalidEditorLayoutError) as exc:
        position_annotation_value(**kwargs, error_cls=InvalidEditorLayoutError)

    message = str(exc.value)
    assert "'x'" in message or "'y'" in message
    # A type NAME is fine ("got NoneType"); the VALUE never appears.
    for value in kwargs.values():
        if isinstance(value, (str, list, dict)):
            assert str(value) not in message


def test_an_unusable_dimension_is_refused():
    with pytest.raises(InvalidEditorLayoutError) as exc:
        position_annotation_value(
            0, 0, width="wide", error_cls=InvalidEditorLayoutError
        )

    assert "'width'" in str(exc.value)
    assert "wide" not in str(exc.value)


def test_a_bad_coordinate_fails_before_anything_is_written(tmp_path):
    """Validation is at the handle call, not at emit: no partial bundle."""
    case_dir = tmp_path / "bad"
    case_dir.mkdir()
    script = case_dir / "pipeline.py"
    script.write_text(_source(task_suffix='.with_position("300", 120)'), "utf-8")

    with pytest.raises(InvalidEditorLayoutError):
        compile_pipeline(script, case_dir / "out" / "compiled.yaml")

    assert not (case_dir / "out").exists()


@pytest.mark.parametrize("direction", FLOW_DIRECTIONS)
def test_every_documented_flow_direction_is_accepted(direction):
    assert validate_flow_direction(direction) == direction


@pytest.mark.parametrize(
    "direction", ["LEFT-TO-RIGHT", "left_to_right", "diagonal", "", None, 1]
)
def test_an_unknown_flow_direction_is_refused_with_the_allowed_values(direction):
    with pytest.raises(InvalidEditorLayoutError) as exc:
        validate_flow_direction(direction, error_cls=InvalidEditorLayoutError)

    message = str(exc.value)
    assert "'left-to-right'" in message and "'top-to-bottom'" in message
    if isinstance(direction, str) and direction:
        assert direction not in message


def test_layout_errors_are_compile_errors():
    """Existing ``CompileError`` handlers keep working."""
    assert issubclass(InvalidEditorLayoutError, CompileError)


# ---------------------------------------------------------------------------
# Subpipelines.


def test_a_subpipeline_task_can_be_positioned(tmp_path):
    """The position lands on the PARENT task; the child sidecar's name and
    bytes are untouched.

    Both compiles reuse the SAME source and output paths: the sidecar name
    hashes compile identity, which includes the source path, so separate
    directories would differ for reasons unrelated to the position.
    """
    child_and_parent = '''
from tangle_cli.python_pipeline import Out, pipeline, subpipeline, task


@task(image="python:3.12")
def greet(greeting: str = "hi"):
    """Write a greeting.

    Metadata:
        Name: Greet
    """
    print(greeting)


@pipeline("Child")
def child() -> Out[str]:
    run_greet = greet()
    return run_greet


@pipeline("Parent")
def parent() -> Out[str]:
    run_child = subpipeline(child)__SUFFIX__()
    return run_child
'''

    script = tmp_path / "pipeline.py"
    out = tmp_path / "out" / "compiled.yaml"

    script.write_text(
        textwrap.dedent(child_and_parent).replace("__SUFFIX__", ""), encoding="utf-8"
    )
    compile_pipeline(script, out, pipeline_name="parent")
    plain_children = {
        name: data for name, data in _bundle(out).items() if name.startswith("child-")
    }
    assert plain_children, "expected a child sidecar to compare"

    script.write_text(
        textwrap.dedent(child_and_parent).replace(
            "__SUFFIX__", ".with_position(500, 60)"
        ),
        encoding="utf-8",
    )
    compile_pipeline(script, out, pipeline_name="parent")

    assert _task_annotations(out, "Run Child") == {
        "editor.position": '{"x": 500, "y": 60}'
    }
    positioned_children = {
        name: data for name, data in _bundle(out).items() if name.startswith("child-")
    }
    assert positioned_children == plain_children


def test_a_position_does_not_change_the_component_reference(tmp_path):
    """Only the task's own annotations differ between the two compiles."""
    positioned = _compile(
        tmp_path, _source(task_suffix=".with_position(300, 120)"), "with_pos"
    )
    plain = _compile(tmp_path, _source(), "without_pos")

    def _task(out: Path) -> dict:
        data = yaml.safe_load(out.read_text(encoding="utf-8"))
        return data["implementation"]["graph"]["tasks"]["Run Greet"]

    positioned_task, plain_task = _task(positioned), _task(plain)
    assert positioned_task["componentRef"] == plain_task["componentRef"]
    assert {k: v for k, v in positioned_task.items() if k != "annotations"} == {
        k: v for k, v in plain_task.items() if k != "annotations"
    }
    assert "annotations" not in plain_task


# ---------------------------------------------------------------------------
# Auto-layout interaction.


def test_an_explicit_position_suppresses_auto_layout(tmp_path):
    """The runner relayouts only a graph it considers unpositioned."""
    hooks = PipelineRunnerHooks()
    positioned = yaml.safe_load(
        _compile(
            tmp_path, _source(task_suffix=".with_position(300, 120)"), "auto_pos"
        ).read_text(encoding="utf-8")
    )
    plain = yaml.safe_load(
        _compile(tmp_path, _source(), "auto_plain").read_text(encoding="utf-8")
    )

    assert hooks.has_layout(positioned) is True
    assert hooks.has_layout(plain) is False

    common = {
        "pipeline_path": "p.yaml",
        "effective_path": None,
        "skip_layout": False,
        "layout_algorithm": None,
    }
    assert hooks.should_apply_layout(positioned, force_layout=False, **common) is False
    assert hooks.should_apply_layout(plain, force_layout=False, **common) is True
    # Explicit force still wins: the author asked for a relayout.
    assert hooks.should_apply_layout(positioned, force_layout=True, **common) is True


def test_a_position_at_the_origin_does_not_count_as_layout(tmp_path):
    """Pre-existing runner semantics, pinned because the sugar makes ``(0, 0)``
    easy to write: an all-origin graph is still auto-laid-out."""
    hooks = PipelineRunnerHooks()
    origin = yaml.safe_load(
        _compile(
            tmp_path, _source(task_suffix=".with_position(0, 0)"), "auto_origin"
        ).read_text(encoding="utf-8")
    )

    assert hooks.has_layout(origin) is False


def test_the_cli_layout_writes_what_the_sugar_writes(tmp_path):
    """A laid-out document must round-trip back through Python."""
    from tangle_cli.pipelines import layout_pipeline_spec

    spec = yaml.safe_load(
        _compile(tmp_path, _source(), "for_layout").read_text(encoding="utf-8")
    )
    layout_pipeline_spec(spec)

    written = spec["implementation"]["graph"]["tasks"]["Run Greet"]["annotations"][
        POSITION_ANNOTATION
    ]
    assert written == position_annotation_value(0, 0)
