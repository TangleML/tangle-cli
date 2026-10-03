"""``@Layout()`` compile-time graph auto-layout.

``Layout`` is immutable authoring metadata on a graph definition. The compiler
resolves which graph occurrences it covers (``recursive`` propagation, nearest
declaration wins) and, when a ``layout_transform`` is installed, lays each
covered artifact out before validation/writing. Without a transform, or
without ``@Layout()``, compiled bytes and filenames are unchanged.
"""

from __future__ import annotations

import copy
import functools
import json
import pickle
import sys
import textwrap
import types
from pathlib import Path
from typing import Any

import pytest
import yaml

import tangle_cli.python_pipeline as pp
from tangle_cli import pipeline_compiler
from tangle_cli.pipeline_compiler import PipelineCompiler, compile_pipeline
from tangle_cli.pipelines import PipelineValidationError, compile_pipeline_file
from tangle_cli.python_pipeline import (
    GraphLayoutContext,
    InvalidLayoutError,
    Layout,
    TaskInterface,
    pipeline,
    subpipeline,
    task,
)
from tangle_cli.python_pipeline.errors import CompileError
from tangle_cli.python_pipeline.pipeline import PipelineFn

SECRET = "s3cr3t-value-never-rendered"
POSITION = "editor.position"


class _Hostile:
    """Every rendering, comparison or attribute hook raises ``SECRET``."""

    def _boom(self, *args: object, **kwargs: object) -> Any:  # pragma: no cover - must never run
        raise AssertionError(SECRET)

    __repr__ = __str__ = __format__ = __eq__ = __bool__ = __len__ = __getattr__ = __call__ = _boom
    __hash__ = None  # type: ignore[assignment]


class _HostileStr(str):
    def __repr__(self) -> str:  # pragma: no cover - must never run
        raise AssertionError(SECRET)


def _fn():
    return None


@task(image="python:3.12")
def _some_task(x: str = "1"):
    print(x)


class _Klass:
    def method(self):
        return None

    def __call__(self):
        return None


class _TrappedPipelineFn(PipelineFn):
    def __getattribute__(self, name: str) -> object:
        if name == "layout":
            raise RuntimeError(SECRET)
        return super().__getattribute__(name)


class _TrappedDict(dict):
    def _boom(self, *args: object) -> Any:  # pragma: no cover - must never run
        raise RuntimeError(SECRET)

    __contains__ = get = __getitem__ = __setitem__ = _boom


# ---------------------------------------------------------------------------
# The decorator


def test_layout_value_contract_and_exports():
    for name in ("Layout", "GraphLayoutContext", "GraphLayoutTransform", "TaskInterface", "InvalidLayoutError"):
        assert name in pp.__all__
        assert getattr(pipeline_compiler, name, getattr(pp, name)) is getattr(pp, name)
    assert issubclass(InvalidLayoutError, CompileError)

    assert (Layout().algorithm, Layout().recursive) == (None, True)
    assert Layout("banded") == Layout(algorithm="banded") != Layout("banded", recursive=False)
    assert repr(Layout()) == "Layout()"
    assert repr(Layout("banded", recursive=False)) == "Layout(algorithm='banded', recursive=False)"
    # Unsupported names are the transform's concern.
    assert Layout("not-a-real-engine").algorithm == "not-a-real-engine"

    layout = Layout("banded", recursive=False)
    for name in ("algorithm", "recursive", "_algorithm", "extra"):
        with pytest.raises(AttributeError):
            setattr(layout, name, "other")
    with pytest.raises(AttributeError):
        del layout._algorithm
    assert hash(layout) == hash(Layout("banded", recursive=False))
    assert pickle.loads(pickle.dumps(layout)) == copy.deepcopy(layout) == layout


def test_layout_decorates_both_orders_and_snapshots_per_wrapper():
    # Outer order: the PipelineFn itself is returned, carries the layout, and
    # keeps its other @pipeline metadata.
    outer_target = pipeline("Outer", flow_direction="left-to-right", annotations={"a": "b"}, propagate_config=True)(
        _fn
    )
    banded = Layout("banded")
    assert banded(outer_target) is outer_target and outer_target.layout is banded
    assert outer_target.annotations == {"a": "b", "editor.flow-direction": "left-to-right"}
    assert outer_target.propagate_config is True

    # Inner order: the plain function is returned; @pipeline snapshots it.
    def inner():
        return None

    default = Layout()
    assert default(inner) is inner
    first, second = pipeline("First")(inner), pipeline("Second")(inner)
    assert first.layout is second.layout is default

    # An outer decoration only affects the wrapper it decorates, not the
    # function, and leaves PipelineFn equality alone.
    def shared():
        return None

    plain, styled = pipeline("Same")(shared), pipeline("Same")(shared)
    Layout("banded")(styled)
    assert plain.layout is None and "__tangle_layout__" not in shared.__dict__
    assert plain == styled

    # A forged marker is ignored; a dict-subclass ``__dict__`` never dispatches.
    def forged():
        return None

    forged.__dict__["__tangle_layout__"] = "banded"
    assert pipeline("Forged")(forged).layout is None

    def trapped():
        return None

    trapped.__dict__ = _TrappedDict()
    assert pipeline("Plain Trapped")(trapped).layout is None
    assert banded(trapped) is trapped
    assert pipeline("Inner Trapped")(trapped).layout is banded


_BAD_CONSTRUCTIONS = {
    # bare ``@Layout`` (the target arrives positionally) and bad positionals
    "bare_function": (lambda: Layout(_fn), "@Layout()"),
    "bare_pipeline": (lambda: Layout(pipeline("Bare")(_fn)), "@Layout()"),
    "bare_hostile": (lambda: Layout(_Hostile()), "@Layout()"),
    "positional_none": (lambda: Layout(None), "@Layout()"),
    "two_positionals": (lambda: Layout("a", "b"), "@Layout()"),
    "positional_str_subclass": (lambda: Layout(_HostileStr("banded")), "@Layout()"),
    "positional_and_keyword": (lambda: Layout("a", algorithm="b"), "@Layout()"),
    # algorithm: exact non-empty str or None
    "empty": (lambda: Layout(algorithm=""), "non-empty str"),
    "int": (lambda: Layout(algorithm=1), "non-empty str"),
    "bool": (lambda: Layout(algorithm=True), "non-empty str"),
    "bytes": (lambda: Layout(algorithm=b"banded"), "non-empty str"),
    "str_subclass": (lambda: Layout(algorithm=_HostileStr("banded")), "non-empty str"),
    "hostile": (lambda: Layout(algorithm=_Hostile()), "non-empty str"),
    "layout": (lambda: Layout(algorithm=Layout()), "non-empty str"),
    # recursive: exact bool
    "recursive_none": (lambda: Layout(recursive=None), "True or False"),
    "recursive_int": (lambda: Layout(recursive=1), "True or False"),
    "recursive_str": (lambda: Layout(recursive="false"), "True or False"),
    "recursive_hostile": (lambda: Layout(recursive=_Hostile()), "True or False"),
}


def test_layout_refuses_invalid_construction_safely():
    for case, (build, fragment) in _BAD_CONSTRUCTIONS.items():
        with pytest.raises(InvalidLayoutError) as excinfo:
            build()
        message = str(excinfo.value)
        assert fragment in message, case
        assert SECRET not in message, case
    with pytest.raises(TypeError):
        Layout(algo="banded")  # type: ignore[call-arg]


def test_layout_refuses_wrong_targets_and_duplicates_safely():
    child = pipeline("Child Target")(_fn)
    wrong_targets = [
        _some_task,
        subpipeline(child),
        subpipeline(child).named("Handle"),
        _Klass,
        _Klass().method,
        _Klass(),
        functools.partial(_fn),
        print,
        None,
        _Hostile(),
        _TrappedPipelineFn(fn=_fn, name="Trapped"),
    ]
    for target in wrong_targets:
        with pytest.raises(InvalidLayoutError) as excinfo:
            Layout()(target)
        assert "graph definition" in str(excinfo.value)
        assert SECRET not in str(excinfo.value)
    assert child.layout is None

    def fn():
        return None

    Layout()(fn)
    pfn = Layout()(pipeline("Dup")(_fn))
    mixed = pipeline("Mixed")(fn)  # inner marker snapshotted
    trapped = _fn_with_trapped_dict()
    Layout()(trapped)
    for target in (fn, pfn, mixed, trapped):
        with pytest.raises(InvalidLayoutError, match="at most once"):
            Layout("banded")(target)
    assert fn.__dict__["__tangle_layout__"] == pfn.layout == mixed.layout == Layout()


def _fn_with_trapped_dict():
    def fn():
        return None

    fn.__dict__ = _TrappedDict()
    return fn


# ---------------------------------------------------------------------------
# Compile-time layout: helpers

# Deterministic test transform: every task/input gets a position whose ``y``
# encodes the algorithm, so each artifact's effective policy is observable in
# its written YAML.
_ALGO_Y = {None: 0, "sugiyama": 1000, "banded": 2000, "a": 3000, "b": 4000}


class RecordingTransform:
    def __init__(self) -> None:
        self.calls: list[GraphLayoutContext] = []

    def __call__(self, graph: dict[str, Any], context: GraphLayoutContext) -> dict[str, Any]:
        self.calls.append(context)
        y = _ALGO_Y[context.algorithm]
        for index, task_spec in enumerate(graph["implementation"]["graph"]["tasks"].values()):
            task_spec.setdefault("annotations", {})[POSITION] = json.dumps({"x": index * 100, "y": y})
        for index, spec in enumerate(graph.get("inputs") or []):
            spec.setdefault("annotations", {})[POSITION] = json.dumps({"x": -100, "y": y + index})
        return graph

    def summary(self) -> list[tuple[str, tuple[str, ...], str | None]]:
        return [(c.pipeline_name, c.path, c.algorithm) for c in self.calls]


# Placeholders become ``Layout(...)`` or the line-count-preserving ``_same``
# no-op, so decorated and plain sources differ only in decorators.
_BUNDLE = '''
from tangle_cli.python_pipeline import Layout, In, Out, pipeline, subpipeline, task
import layout_probe

layout_probe.events.append("root-module")


def _same(fn):
    return fn


@task(image="python:3.12")
def leaf(value: str = "x"):
    print(value)


{JUDGE}
@pipeline("Judge")
def judge(seed: In[str]) -> Out[str]:
    layout_probe.events.append("trace-judge")
    return leaf.named("Leaf")(value=seed)


@pipeline("Mid")
{MID}
def mid(seed: In[str]) -> Out[str]:
    layout_probe.events.append("trace-mid")
    first = subpipeline(judge).named("First")(seed=seed)
    return subpipeline(judge).named("Second")(seed=first)


{ROOT}
@pipeline("Root")
def root(seed: In[str]) -> Out[str]:
    layout_probe.events.append("trace-root")
    middle = subpipeline(mid).named("Middle")(seed=seed)
    return subpipeline(judge).named("Direct")(seed=middle)


@Layout(algorithm="unselected")
@pipeline("Unselected")
def unselected(seed: In[str]) -> Out[str]:
    return subpipeline(judge).named("Never")(seed=seed)
'''

_PATHS: list[tuple[str, ...]] = [(), ("Middle",), ("Middle", "First"), ("Middle", "Second"), ("Direct",)]


@pytest.fixture
def probe(monkeypatch):
    module = types.ModuleType("layout_probe")
    module.events = []  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "layout_probe", module)
    return module


def _write(path: Path, source: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    return path


def _compile_bundle(tmp_path: Path, name: str, transform: Any = None, select: str = "root", **decorators: str):
    values = {"JUDGE": "@_same", "MID": "@_same", "ROOT": "@_same", **decorators}
    src = _write(tmp_path / "proj" / "bundle.py", _BUNDLE.format(**values))
    out = tmp_path / name / "compiled.yaml"
    return compile_pipeline(src, out, pipeline_name=select, layout_transform=transform), out


def _bundle_bytes(out: Path) -> dict[str, bytes]:
    return {p.relative_to(out.parent).as_posix(): p.read_bytes() for p in sorted(out.parent.rglob("*")) if p.is_file()}


def _load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _occurrence(out: Path, *path: str) -> tuple[Path, dict[str, Any]]:
    """Follow the written ``file://`` refs from the root to occurrence ``path``."""
    current = out.resolve()
    body = _load(current)
    for task_id in path:
        url = body["implementation"]["graph"]["tasks"][task_id]["componentRef"]["url"]
        current = (current.parent / url.removeprefix("file://")).resolve()
        body = _load(current)
    return current, body


def _ys(out: Path, *path: str) -> set[int | None]:
    """Distinct laid-out ``y`` values of the tasks in the graph at ``path``."""
    _file, body = _occurrence(out, *path)
    tasks = body["implementation"]["graph"]["tasks"].values()
    values = [(t.get("annotations") or {}).get(POSITION) for t in tasks]
    return {None if v is None else json.loads(v)["y"] for v in values}


# ---------------------------------------------------------------------------
# Coverage policy over one bundle: Root -> Middle(Mid) -> First/Second(Judge),
# Root -> Direct(Judge). Each row: decorators, expected y per occurrence path
# (None = not laid out), expected transform calls (post-order, once per
# artifact variant), sidecar count, and the paths whose sidecar must keep its
# legacy (no-layout) filename.

_SUG, _BAN = '@Layout("sugiyama")', '@Layout("banded")'
_POLICY_CASES = {
    "default_recursive_covers_subtree": (
        {"ROOT": _SUG},
        [1000, 1000, 1000, 1000, 1000],
        [("Judge", ("Middle", "First"), "sugiyama"), ("Mid", ("Middle",), "sugiyama"), ("Root", (), "sugiyama")],
        2,
        [],
    ),
    "recursive_false_covers_only_itself": (
        {"ROOT": '@Layout("sugiyama", recursive=False)'},
        [1000, None, None, None, None],
        [("Root", (), "sugiyama")],
        2,
        _PATHS[1:],
    ),
    "explicit_child_under_non_recursive_parent": (
        {"ROOT": "@Layout(recursive=False)", "JUDGE": _BAN},
        [0, None, 2000, 2000, 2000],
        [("Judge", ("Middle", "First"), "banded"), ("Root", (), None)],
        2,
        [("Middle",)],
    ),
    "recursive_override_splits_shared_child": (
        {"ROOT": _SUG, "MID": _BAN},
        [1000, 2000, 2000, 2000, 1000],
        [
            ("Judge", ("Middle", "First"), "banded"),
            ("Mid", ("Middle",), "banded"),
            ("Judge", ("Direct",), "sugiyama"),
            ("Root", (), "sugiyama"),
        ],
        3,
        [],
    ),
    "reset_to_default_is_inherited_below": (
        {"ROOT": _SUG, "MID": "@Layout()"},
        [1000, 0, 0, 0, 1000],
        [
            ("Judge", ("Middle", "First"), None),
            ("Mid", ("Middle",), None),
            ("Judge", ("Direct",), "sugiyama"),
            ("Root", (), "sugiyama"),
        ],
        3,
        [],
    ),
    "non_recursive_override_stops_all_inheritance": (
        {"ROOT": _SUG, "MID": '@Layout("banded", recursive=False)'},
        [1000, 2000, None, None, 1000],
        [("Mid", ("Middle",), "banded"), ("Judge", ("Direct",), "sugiyama"), ("Root", (), "sugiyama")],
        3,
        [("Middle", "First"), ("Middle", "Second")],
    ),
    "undecorated_root_with_decorated_descendant": (
        {"JUDGE": _BAN},
        [None, None, 2000, 2000, 2000],
        [("Judge", ("Middle", "First"), "banded")],
        2,
        [("Middle",)],
    ),
}


@pytest.mark.parametrize("case", list(_POLICY_CASES))
def test_compile_layout_coverage_policy(tmp_path, probe, case):
    decorators, expected_ys, expected_calls, sidecars, legacy_paths = _POLICY_CASES[case]
    _plain, plain_out = _compile_bundle(tmp_path, "plain")
    del probe.events[:]
    transform = RecordingTransform()
    result, out = _compile_bundle(tmp_path, "out", transform, **decorators)

    assert transform.summary() == expected_calls
    assert len(result.subgraph_paths) == sidecars
    for path, expected in zip(_PATHS, expected_ys):
        assert _ys(out, *path) == {expected}, path
    # Root and subgraph graph inputs are positioned with their graph's tasks.
    for path, expected in zip(_PATHS, expected_ys):
        inputs = _occurrence(out, *path)[1]["inputs"]
        position = (inputs[0].get("annotations") or {}).get(POSITION)
        assert (position is None) == (expected is None), path
    # Repeated calls under one policy share one artifact; uncovered children
    # keep their legacy filenames.
    assert _occurrence(out, "Middle", "First")[0] == _occurrence(out, "Middle", "Second")[0]
    for path in legacy_paths:
        assert _occurrence(out, *path)[0].name == _occurrence(plain_out, *path)[0].name, path
    # One module execution; each definition is traced once per artifact
    # variant (Judge has one or two), never once per occurrence.
    assert [e for e in probe.events if e != "trace-judge"] == ["root-module", "trace-root", "trace-mid"]
    judge_files = {_occurrence(out, *path)[0] for path in _PATHS[2:]}
    assert probe.events.count("trace-judge") == len(judge_files)


def test_compile_layout_leaves_output_identical_without_transform_or_layout(tmp_path, probe):
    _plain, plain_out = _compile_bundle(tmp_path, "plain")
    plain_bytes = _bundle_bytes(plain_out)

    decorators = {"ROOT": _SUG, "MID": '@Layout("banded", recursive=False)', "JUDGE": "@Layout()"}
    no_transform, out = _compile_bundle(tmp_path, "no-transform", None, **decorators)
    assert _bundle_bytes(out) == plain_bytes
    warned = [w for w in no_transform.warnings if "@Layout()" in w]
    assert len(warned) == 3 and all(any(repr(n) in w for w in warned) for n in ("Root", "Mid", "Judge"))

    unused = RecordingTransform()
    _result, out = _compile_bundle(tmp_path, "undecorated", unused)
    assert unused.calls == [] and _bundle_bytes(out) == plain_bytes

    unselected = RecordingTransform()
    _compile_bundle(tmp_path, "unselected", unselected, select="mid")
    assert unselected.calls == []

    # Laid-out output is deterministic across compiles.
    _r1, out1 = _compile_bundle(tmp_path, "first", RecordingTransform(), ROOT=_SUG, MID=_BAN)
    _r2, out2 = _compile_bundle(tmp_path, "second", RecordingTransform(), ROOT=_SUG, MID=_BAN)
    assert _bundle_bytes(out1) == _bundle_bytes(out2) != plain_bytes


_DIAMOND = '''
from tangle_cli.python_pipeline import Layout, In, Out, pipeline, subpipeline, task

@task(image="python:3.12")
def leaf(value: str = "x"):
    print(value)

@pipeline("Bottom")
def bottom(seed: In[str]) -> Out[str]:
    return leaf.named("Leaf")(value=seed)

@Layout("{left}")
@pipeline("Left")
def left(seed: In[str]) -> Out[str]:
    return subpipeline(bottom).named("Shared")(seed=seed)

@Layout("{right}")
@pipeline("Right")
def right(seed: In[str]) -> Out[str]:
    return subpipeline(bottom).named("Shared")(seed=seed)

@pipeline("Top")
def top(seed: In[str]) -> Out[str]:
    a = subpipeline(left).named("A")(seed=seed)
    return subpipeline(right).named("B")(seed=a)
'''


@pytest.mark.parametrize(("left", "right", "bottom_calls", "sidecars"), [("a", "a", 1, 3), ("a", "b", 2, 4)])
def test_compile_layout_diamond_dedups_same_policy_and_splits_different(tmp_path, left, right, bottom_calls, sidecars):
    src = _write(tmp_path / "proj" / "diamond.py", _DIAMOND.format(left=left, right=right))
    transform = RecordingTransform()
    out = tmp_path / "out" / "compiled.yaml"
    result = compile_pipeline(src, out, pipeline_name="top", layout_transform=transform)

    assert [c.pipeline_name for c in transform.calls].count("Bottom") == bottom_calls
    assert len(result.subgraph_paths) == sidecars
    same_file = _occurrence(out, "A", "Shared")[0] == _occurrence(out, "B", "Shared")[0]
    assert same_file == (left == right)
    assert _ys(out, "A", "Shared") == {_ALGO_Y[left]}
    assert _ys(out, "B", "Shared") == {_ALGO_Y[right]}
    assert _ys(out) == {None}


def test_compile_layout_config_variants_and_edge_wrappers(tmp_path):
    project = tmp_path / "proj"
    _write(project / "child_config.yaml", "message: from-file\n")
    src = _write(
        project / "variants.py",
        '''
        from tangle_cli.python_pipeline import Layout, In, Out, pipeline, subpipeline, task

        @task(image="python:3.12")
        def emit(message: str = "x"):
            print(message)

        @Layout("banded")
        @pipeline("Variant Child", config="child_config.yaml")
        def child(seed: In[str], cfg) -> Out[str]:
            return emit.named("Emit")(message=cfg.message, wait_for=seed)

        def _body(seed: In[str]) -> Out[str]:
            return emit.named("Emit")(message="plain", wait_for=seed)

        # Two wrappers of one function share a compile key; only one is styled.
        plain_child = pipeline("Shared Child")(_body)
        styled_child = Layout("banded")(pipeline("Shared Child")(_body))

        @pipeline("Variant Parent")
        def parent(seed: In[str]) -> Out[str]:
            one = subpipeline(child).named("One").override_config(message="one")(seed=seed)
            two = subpipeline(child).named("Two").override_config(message="two")(seed=one)
            again = subpipeline(child).named("Again").override_config(message="one")(seed=two)
            plain = subpipeline(plain_child).named("Plain")(seed=again)
            return subpipeline(styled_child).named("Styled")(seed=plain)
        ''',
    )
    plain_out = tmp_path / "plain" / "compiled.yaml"
    compile_pipeline(src, plain_out, pipeline_name="parent")
    transform = RecordingTransform()
    out = tmp_path / "out" / "compiled.yaml"
    result = compile_pipeline(src, out, pipeline_name="parent", layout_transform=transform)

    assert transform.summary() == [
        ("Variant Child", ("One",), "banded"),
        ("Variant Child", ("Two",), "banded"),
        ("Shared Child", ("Styled",), "banded"),
    ]
    # Config variants stay distinct and the repeat dedups; the two wrappers
    # split into an uncovered (legacy-named) and a laid-out artifact.
    assert len(result.subgraph_paths) == 4
    assert _occurrence(out, "One")[0] == _occurrence(out, "Again")[0] != _occurrence(out, "Two")[0]
    for path in [("One",), ("Two",), ("Again",), ("Styled",)]:
        assert _ys(out, *path) == {2000}, path
    assert _ys(out, "Plain") == {None}
    assert _occurrence(out, "Plain")[0].name == _occurrence(plain_out, "Plain")[0].name


def test_compile_layout_cycle_detection_ignores_layout_variants(tmp_path):
    """``Loop`` reached under ``Step``'s inherited layout is a different artifact
    variant than the root ``Loop``, but still the same definition: the cycle
    is reported at once, before any transform runs or anything is written."""
    src = _write(
        tmp_path / "proj" / "cycle.py",
        '''
        from tangle_cli.python_pipeline import Layout, In, Out, pipeline, subpipeline

        @pipeline("Loop")
        def loop(seed: In[str]) -> Out[str]:
            return subpipeline(step).named("Step")(seed=seed)

        @Layout("b")
        @pipeline("Step")
        def step(seed: In[str]) -> Out[str]:
            return subpipeline(loop).named("Again")(seed=seed)
        ''',
    )
    transform = RecordingTransform()
    out = tmp_path / "out" / "compiled.yaml"
    with pytest.raises(CompileError) as excinfo:
        compile_pipeline(src, out, pipeline_name="loop", layout_transform=transform)
    message = str(excinfo.value)
    assert "nested pipeline cycle detected" in message and "max depth" not in message
    assert message.count("Loop (") == 2 and message.count("Step (") == 1
    assert transform.calls == [] and not out.parent.exists()


_IMPORTED_CHILD = '''
from tangle_cli.python_pipeline import Layout, In, Out, pipeline, task
import layout_probe

layout_probe.events.append("child-module")


@task(image="python:3.12")
def leaf(value: str = "x"):
    print(value)


@pipeline("Imported Child")
@Layout(algorithm="banded")
def imported_child(seed: In[str]) -> Out[str]:
    return leaf.named("Leaf")(value=seed)
'''

_IMPORTING_ROOT = '''
from tangle_cli.python_pipeline import In, Out, pipeline, subpipeline
from {module} import imported_child


@pipeline("Importing Root")
def importing_root(seed: In[str]) -> Out[str]:
    return subpipeline(imported_child).named("Imported")(seed=seed)
'''


def test_compile_layout_imported_and_pre_imported_children(tmp_path, probe, monkeypatch):
    # A sibling module imported by the root during compile.
    _write(tmp_path / "proj" / "sibling_child.py", _IMPORTED_CHILD)
    src = _write(tmp_path / "proj" / "root.py", _IMPORTING_ROOT.format(module="sibling_child"))
    transform = RecordingTransform()
    out = tmp_path / "sibling" / "compiled.yaml"
    compile_pipeline(src, out, layout_transform=transform)
    assert transform.summary() == [("Imported Child", ("Imported",), "banded")]
    assert _ys(out, "Imported") == {2000}

    # A library module imported (and cached) BEFORE compile keeps its layout,
    # across repeated compiles, without being re-executed.
    module_name = "layout_cached_child_lib"
    _write(tmp_path / "libs" / f"{module_name}.py", _IMPORTED_CHILD)
    monkeypatch.syspath_prepend(str(tmp_path / "libs"))
    monkeypatch.delitem(sys.modules, module_name, raising=False)
    cached = __import__(module_name)
    monkeypatch.setitem(sys.modules, module_name, cached)
    src = _write(tmp_path / "cached-proj" / "root.py", _IMPORTING_ROOT.format(module=module_name))
    executions = probe.events.count("child-module")
    for attempt in range(2):
        sys.modules[module_name] = cached  # the compiler purges it after each compile
        transform = RecordingTransform()
        out = tmp_path / f"cached{attempt}" / "compiled.yaml"
        compile_pipeline(src, out, layout_transform=transform)
        assert transform.summary() == [("Imported Child", ("Imported",), "banded")]
        assert _ys(out, "Imported") == {2000}
    assert probe.events.count("child-module") == executions


def test_compile_layout_context_and_task_interfaces(tmp_path):
    project = tmp_path / "proj"
    for name, spec in {
        # Opaque ref components: their YAML is never read by the compiler, so
        # declared-but-unused ports (``unused``, ``log``, ``hidden``) are absent.
        "producer": "inputs: [{name: unused}, {name: seed}, {name: mode}]\noutputs: [{name: data}, {name: log}]",
        "consumer": "inputs: [{name: mode}, {name: hidden}]",
        "noop": "inputs: [{name: wait_for}]",
    }.items():
        _write(project / f"{name}.yaml", f"name: {name}\n{spec}\nimplementation:\n  container:\n    image: alpine\n")
    src = _write(
        project / "interfaces.py",
        '''
        from typing import NamedTuple
        from tangle_cli.python_pipeline import Layout, In, Out, pipeline, ref, subpipeline, task

        class Pair(NamedTuple):
            left: str
            right: str

        @task(image="python:3.12")
        def split(text: str, sep: str = ",") -> Pair:
            return Pair(*text.split(sep, 1))

        @pipeline("Inner")
        def inner(seed: In[str], extra: In[str] = "e") -> Out[str]:
            return split.named("Split")(text=seed)

        @Layout("banded")
        @pipeline("Outer")
        def outer(seed: In[str]) -> Out[str]:
            produced = ref(url="file://./producer.yaml").named("Producer")(seed=seed, mode="fast")
            child = subpipeline(inner).named("Child")(seed=produced.data)
            ref(url="file://./consumer.yaml").named("Consumer")(mode="x", is_enabled=produced.flag)
            ref(url="file://./noop.yaml").named("Noop")(wait_for=produced.data)
            return child
        ''',
    )
    transform = RecordingTransform()
    out = project / "compiled.yaml"  # colocated so the author refs resolve
    compile_pipeline(src, out, pipeline_name="outer", layout_transform=transform)

    inner_ctx, outer_ctx = transform.calls
    assert (inner_ctx.pipeline_name, inner_ctx.path, outer_ctx.path) == ("Inner", ("Child",), ())
    assert inner_ctx.layout is outer_ctx.layout == Layout("banded")
    assert inner_ctx.artifact_dir == (project / "compiled.subgraphs").resolve()
    assert outer_ctx.artifact_dir == project.resolve()
    assert dict(inner_ctx.task_interfaces) == {
        # Exact signature ports, unioned with the observed bare-return output.
        "Split": TaskInterface(inputs=("text", "sep"), outputs=("left", "right", "wait_for_output")),
    }
    assert list(outer_ctx.task_interfaces.items()) == [
        # Opaque: every supplied argument (edge AND literal) + consumed outputs,
        # including one consumed only by another task's isEnabled condition.
        ("Producer", TaskInterface(inputs=("seed", "mode"), outputs=("data", "flag"), approximate=True)),
        ("Child", TaskInterface(inputs=("seed", "extra"), outputs=("wait_for_output",))),
        ("Consumer", TaskInterface(inputs=("mode",), outputs=(), approximate=True)),
        ("Noop", TaskInterface(inputs=("wait_for",), outputs=(), approximate=True)),
    ]
    with pytest.raises(TypeError):
        outer_ctx.task_interfaces["Noop"] = TaskInterface((), ())  # type: ignore[index]
    # Extra observed INPUT names cannot be authored for known interfaces (the
    # compiler validates them), so the defensive input union is checked directly.
    known = TaskInterface(inputs=("a", "b"), outputs=("x",))
    assert pipeline_compiler._union_interface(known, ["b", "c"], []) == TaskInterface(("a", "b", "c"), ("x",))
    # Opaque tasks are positioned too; no interface metadata is written.
    assert _ys(out) == {2000}
    assert "approximate" not in out.read_text(encoding="utf-8")


def test_compile_layout_transform_is_forwarded_by_every_entry_point(tmp_path, probe):
    outs = []
    for name, run in {
        "function": compile_pipeline,
        "handler": PipelineCompiler().compile_file,
        "pipelines_api": compile_pipeline_file,
    }.items():
        values = {"JUDGE": _BAN, "MID": "@_same", "ROOT": "@_same"}
        src = _write(tmp_path / "proj" / "bundle.py", _BUNDLE.format(**values))
        transform = RecordingTransform()
        out = tmp_path / name / "compiled.yaml"
        run(src, out, pipeline_name="root", layout_transform=transform)
        assert len(transform.calls) == 1, name
        outs.append(_bundle_bytes(out))
    assert outs[0] == outs[1] == outs[2]


# ---------------------------------------------------------------------------
# Transform contract guard

_GUARDED = '''
from tangle_cli.python_pipeline import Layout, In, Out, pipeline, task

@task(image="python:3.12")
def leaf(value: str = "x"):
    print(value)

@Layout()
@pipeline("Single")
def single(seed: In[str]) -> Out[str]:
    first = leaf.named("First").with_position(5, 5, width=10, height=10)(value=seed)
    second = leaf.named("Second").with_annotations({"keep": "me"})(value=seed, wait_for=first)
    return leaf.named("Third")(value=seed, wait_for=second)
'''


def _tasks(graph: dict[str, Any]) -> dict[str, Any]:
    return graph["implementation"]["graph"]["tasks"]


def _set_positions(graph, context):
    for index, task_spec in enumerate(_tasks(graph).values()):
        task_spec.setdefault("annotations", {})[POSITION] = json.dumps({"x": index, "y": 7})
    return graph


def _remove_position_only_block(graph, context):
    del _tasks(graph)["First"]["annotations"]
    return graph


def _raise(exc: Exception):
    def transform(graph, context):
        raise exc

    return transform


class _Refused(CompileError):
    pass


def _mutate(edit):
    def transform(graph, context):
        edit(graph)
        return graph

    return transform


_GUARD_CASES = {
    # allowed: create, replace (dropping manual width/height) and remove positions
    "set_positions": (_set_positions, None),
    "remove_position_only_block": (_remove_position_only_block, None),
    # refused: anything beyond editor.position, bad values and bad returns
    "rename": (_mutate(lambda g: g.update(name="Renamed")), "beyond"),
    "argument": (_mutate(lambda g: _tasks(g)["Second"]["arguments"].update(value="changed")), "beyond"),
    "other_annotation": (_mutate(lambda g: _tasks(g)["Third"].setdefault("annotations", {}).update(o="x")), "beyond"),
    "drop_other_annotations": (_mutate(lambda g: _tasks(g)["Second"].pop("annotations")), "beyond"),
    "drop_task": (_mutate(lambda g: _tasks(g).pop("Third")), "beyond"),
    "non_string_position": (
        _mutate(lambda g: _tasks(g)["Third"].setdefault("annotations", {}).update({POSITION: {"x": 1}})),
        "non-string",
    ),
    "return_none": (lambda graph, context: None, "must return a dict"),
    "return_mapping_proxy": (lambda graph, context: types.MappingProxyType(graph), "must return a dict"),
    # transform failures: CompileError propagates as-is, others are wrapped
    "raises_value_error": (_raise(ValueError("unsupported algorithm")), "ValueError: unsupported algorithm"),
    "raises_compile_error": (_raise(_Refused("flow direction not supported")), "flow direction not supported"),
}


def test_compile_layout_transform_guard(tmp_path):
    src = _write(tmp_path / "proj" / "single.py", _GUARDED)
    for case, (transform, error) in _GUARD_CASES.items():
        out = tmp_path / case / "compiled.yaml"
        if error is None:
            compile_pipeline(src, out, layout_transform=transform)
            continue
        with pytest.raises(CompileError) as excinfo:
            compile_pipeline(src, out, layout_transform=transform)
        assert error in str(excinfo.value), case
        assert "'Single'" in str(excinfo.value) or case == "raises_compile_error", case
        assert not out.parent.exists(), case
    assert isinstance(excinfo.value, _Refused)

    tasks = _tasks(_load(tmp_path / "set_positions" / "compiled.yaml"))
    assert [json.loads(t["annotations"][POSITION]) for t in tasks.values()] == [
        {"x": 0, "y": 7},
        {"x": 1, "y": 7},
        {"x": 2, "y": 7},
    ]
    assert tasks["Second"]["annotations"]["keep"] == "me"
    removed = _tasks(_load(tmp_path / "remove_position_only_block" / "compiled.yaml"))
    assert "annotations" not in removed["First"] and removed["Second"]["annotations"] == {"keep": "me"}

    with pytest.raises(PipelineValidationError):
        compile_pipeline_file(src, tmp_path / "api" / "compiled.yaml", layout_transform=_raise(ValueError("x")))


def test_compile_layout_retained_transform_references_cannot_reach_output(tmp_path, probe):
    """A transform that keeps the bodies it was given/returned cannot change
    the guarded, written output later (from the parent's call or afterwards)."""
    retained: list[dict[str, Any]] = []

    def sneaky(graph, context):
        for earlier in retained:
            for task_spec in _tasks(earlier).values():
                task_spec.setdefault("arguments", {})["value"] = "CHANGED_AFTER_GUARD"
        result = RecordingTransform()(graph, context)
        retained.append(result)
        return result

    _result, out = _compile_bundle(tmp_path, "out", sneaky, ROOT=_SUG)
    assert len(retained) == 3
    retained[-1]["name"] = "CHANGED_AFTER_COMPILE"
    for text in (b.decode("utf-8") for b in _bundle_bytes(out).values()):
        assert "CHANGED_AFTER" not in text


def test_compile_layout_misuse_in_module_fails_before_writing(tmp_path):
    src = _write(
        tmp_path / "dup.py",
        '''
        from tangle_cli.python_pipeline import Layout, Out, pipeline

        @Layout()
        @pipeline("Dup Root")
        @Layout()
        def dup() -> Out[str]:
            return None
        ''',
    )
    out = tmp_path / "out" / "compiled.yaml"
    with pytest.raises(CompileError, match="at most once"):
        compile_pipeline(src, out, layout_transform=RecordingTransform())
    assert not out.parent.exists()
