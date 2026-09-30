"""YAML -> Python decompile: the load-bearing public contracts.

Codegen is proven by round-trip verification (the generated source must
recompile to the same semantic digest). The file entry point adds original
input validation, DIGEST resolve-sidecar canonicalization, two resolution
gates (online and forced-offline), and three-artifact publication.
"""
from __future__ import annotations

import ast
import contextlib
import copy
import io
import os
import pathlib
import re
import shutil
from pathlib import Path
from typing import Any

import pytest

from tangle_cli.pipeline_compiler import compile_pipeline
from tangle_cli.pipeline_decompiler import (
    CanonicalComponents,
    DecompileError,
    _UnreachableLibrary,
    assert_pinning_preserved,
    decompile_pipeline,
    decompile_pipeline_file,
)
from tangle_cli.pipeline_hydrator import PipelineHydrator
from tangle_cli.utils import compute_spec_digest, compute_text_digest, dump_yaml, parse_yaml_string

DIGEST = "a" * 64


# ---------------------------------------------------------------------------
# Builders


def _doc(tasks: dict[str, Any] | None = None, *, graph: dict[str, Any] | None = None, **top: Any) -> dict[str, Any]:
    """A dehydrated document; ``tasks`` defaults to one named-component task."""
    body = {"tasks": tasks if tasks is not None else {"Only Task": {"componentRef": {"name": "comp"}}}}
    body.update(graph or {})
    return {"name": "Base Pipeline", "implementation": {"graph": body}, **top}


def _ref(**ref: Any) -> dict[str, Any]:
    return {"componentRef": ref}


def _out(task: str, output: str) -> dict[str, Any]:
    return {"taskOutput": {"taskId": task, "outputName": output}}


def _leaf(marker: str = "payload") -> dict[str, Any]:
    return {"name": "Leaf", "implementation": {"container": {"image": "example/image:latest", "command": [marker]}}}


def _graph_spec(name: str, tasks: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "implementation": {"graph": {"tasks": tasks}}}


def _write(path: Path, data: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_yaml(data), encoding="utf-8")
    return path


def _pipeline(tmp_path: Path, task: dict[str, Any], name: str = "source.yaml") -> Path:
    return _write(tmp_path / name, _graph_spec("Outer", {"a": task}))


def _local_leaf_source(tmp_path: Path, marker: str = "payload") -> Path:
    """A dehydrated pipeline whose one component is a local file."""
    _write(tmp_path / "leaf.yaml", _leaf(marker))
    return _pipeline(tmp_path, _ref(url="file://./leaf.yaml"))


class _Library:
    """An offline component library: ``published`` digest -> spec."""

    def __init__(self, published: dict[str, Any] | None = None, successors: dict[str, str] | None = None) -> None:
        self.published = published or {}
        self.successors = successors or {}

    def resolve_digest(self, digest: str) -> str:
        return self.successors.get(digest, digest)

    def get_component_spec(self, digest: str) -> dict[str, Any]:
        if digest not in self.published:
            raise KeyError(digest)
        return copy.deepcopy(self.published[digest])

    def find_existing_components(self, names: list[str] | None = None, **kwargs: Any) -> list[Any]:
        from tangle_cli.models import ComponentInfo

        return [
            ComponentInfo(name=name, digest=digest, version=(spec.get("metadata", {}).get("annotations", {}).get("version")))
            for name in names or ()
            for digest, spec in self.published.items()
            if spec.get("name") == name
        ]


def _published(spec: dict[str, Any]) -> tuple[str, _Library]:
    digest = compute_text_digest(dump_yaml(spec))
    return digest, _Library({digest: spec})


def _captured(fn: Any) -> tuple[str, str]:
    """Run ``fn``; return (combined stdout+stderr, DecompileError message or "")."""
    buffer, message = io.StringIO(), ""
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        try:
            fn()
        except DecompileError as exc:
            message = str(exc)
    return buffer.getvalue(), message


def _fragment(script: Path, task: str = "a") -> list[dict[str, Any]]:
    """The sidecar entries a task of a PUBLISHED (or staged) script resolves through."""
    sidecar = script.with_name(f"{script.stem}.leaves.yaml")
    manifest = parse_yaml_string(sidecar.read_text(encoding="utf-8"))
    prefix = f"resolve://./{sidecar.name}#"
    for line in script.read_text(encoding="utf-8").splitlines():
        if prefix in line:
            return manifest[line.split(prefix, 1)[1].split("'")[0].split('"')[0]]
    raise AssertionError("no sidecar reference in the script")


def _snapshot(directory: Path) -> dict[str, bytes]:
    return {p.relative_to(directory).as_posix(): p.read_bytes() for p in directory.rglob("*") if p.is_file()}


def _leaf_specs(document: dict[str, Any]) -> list[str]:
    found: list[str] = []
    for task in document["implementation"]["graph"]["tasks"].values():
        spec = task["componentRef"]["spec"]
        if "graph" in spec["implementation"]:
            found += _leaf_specs(spec)
        else:
            found.append(compute_spec_digest({k: v for k, v in spec.items() if not str(k).startswith("_")}))
    return found


# ---------------------------------------------------------------------------
# Codegen: every construct round-trips (verification proves the digest)

CONSTRUCTS = [
    pytest.param(_doc(), ["ref(name='comp')", ".named('Only Task')"], [], id="minimal"),
    pytest.param(
        _doc(description="what it does", metadata={"labels": {"team": "search"}, "annotations": {"editor.flow-direction": "LR"}}),
        ["description='what it does'", "labels={'team': 'search'}", "annotations={'editor.flow-direction': 'LR'}"],
        # flow_direction= is applied after annotations, so it would overwrite an authored value.
        ["flow_direction="],
        id="root-metadata",
    ),
    pytest.param(
        _doc({"T": _ref(url="file://./c.yaml", name="comp", digest=DIGEST)}),
        ["url=", "name=", "digest="],
        [],
        id="all-locators",
    ),
    pytest.param(
        _doc({"T": _ref(url="resolve://./gen.yaml#frag", digest=DIGEST)}), ["url=", "digest="], [], id="resolve-locator"
    ),
    pytest.param(
        _doc(
            {
                "Producer": _ref(name="p"),
                "Consumer": {
                    **_ref(name="c"),
                    "arguments": {
                        "plain": "literal",
                        "sentinel": "select {{input_1}}",
                        "from_input": {"graphInput": {"inputName": "In One"}},
                        "from_task": _out("Producer", "rows"),
                        "token": {"dynamicData": {"secret": {"name": "API_KEY"}}},
                    },
                },
            },
            inputs=[{"name": "In One", "type": "String"}],
        ),
        [
            "plain='literal'",
            "raw('select {{input_1}}')",
            "from_input=input_in_one",
            "from_task=task_producer.rows",
            "token=dynamic_secret('API_KEY')",
        ],
        [],
        id="argument-shapes",
    ),
    pytest.param(
        _doc(
            {
                "T": {
                    "annotations": {"service_account": "sa@example.com"},
                    **_ref(name="c"),
                    "isEnabled": "false",
                    "executionOptions": {"cachingStrategy": {"maxCacheStaleness": "P0D"}, "retryStrategy": {"maxRetries": 3}},
                }
            }
        ),
        ["with_annotations({'service_account': 'sa@example.com'})", "is_enabled='false'", "maxCacheStaleness"],
        [],
        id="task-metadata",
    ),
    pytest.param(
        _doc({"Gate": _ref(name="g"), "Gated": {**_ref(name="c"), "isEnabled": _out("Gate", "ok")}}),
        ["is_enabled=task_gate.ok"],
        [],
        id="is-enabled-edge",
    ),
    pytest.param(
        _doc(
            outputs=[{"name": "Result", "type": "String", "description": "the result"}],
            graph={"outputValues": {"Result": _out("Only Task", "out")}},
        ),
        ["graph_output('Result', task_only_task.out, 'String'"],
        [],
        id="graph-outputs",
    ),
    pytest.param(
        _doc(
            inputs=[
                {
                    "name": "Pipeline Creation Time",
                    "type": "String",
                    "description": "when the run started",
                    "default": "1970-01-01",
                    "optional": True,
                    "annotations": {"editor.position": '{"x":1,"y":2}'},
                },
                {"name": "Maybe", "type": "String", "optional": True},
                {"name": "Flag", "type": "Boolean", "default": "True"},
            ]
        ),
        ["graph_input('Pipeline Creation Time', 'String'", "description='when the run started'", "optional=True", "default='True'"],
        ["In["],
        id="inputs-all-fields",
    ),
    pytest.param(
        # A compact editor.position must pass through verbatim: re-rendering via
        # json.dumps would rewrite roughly half the corpus.
        _doc(
            {"T": {"annotations": {"editor.position": '{"x":10,"y":20}'}, **_ref(name="c")}},
            inputs=[{"name": "In", "type": "String", "annotations": {"editor.position": '{"x":10,"y":20}'}}],
        ),
        ['{"x":10,"y":20}'],
        ["with_position", "position=("],
        id="compact-position",
    ),
    pytest.param(
        _doc({"Load 2024 Data!": _ref(name="c")}, inputs=[{"name": "Thing", "type": "String"}]),
        [".named('Load 2024 Data!')", "input_thing ="],
        [],
        id="non-identifier-task-id",
    ),
    pytest.param(
        _doc({"Thing": _ref(name="c")}, inputs=[{"name": "Thing", "type": "String"}]),
        ["input_thing =", "task_thing ="],
        [],
        id="input-and-task-share-a-name",
    ),
    pytest.param(
        _doc({"T": {**_ref(name="c"), "arguments": {"from": "x", "class": "y", "odd-key": "z"}}}),
        ["**{'from': 'x'}", "**{'class': 'y'}", "**{'odd-key': 'z'}"],
        [],
        id="keyword-argument-keys",
    ),
    pytest.param(
        # A real component input named is_enabled must not become the condition.
        _doc({"T": {**_ref(name="c"), "arguments": {"is_enabled": "a component input"}, "isEnabled": "true"}}),
        [".bind(is_enabled='a component input')", "is_enabled='true'"],
        [],
        id="is-enabled-input-is-bound",
    ),
    pytest.param(
        _doc({"P": _ref(name="p"), "C": {**_ref(name="c"), "arguments": {"x": _out("P", "rows written"), "y": _out("P", "class")}}}),
        ["getattr(task_p, 'rows written')", "getattr(task_p, 'class')"],
        [],
        id="non-identifier-and-keyword-output-names",
    ),
]


@pytest.mark.parametrize(("document", "present", "absent"), CONSTRUCTS)
def test_every_construct_round_trips(document: dict[str, Any], present: list[str], absent: list[str]) -> None:
    source = decompile_pipeline(document).source
    for needle in present:
        assert needle in source, needle
    for needle in absent:
        assert needle not in source, needle


def test_inputs_keep_document_order_and_distinct_names_stay_distinct() -> None:
    """Inputs all go through graph_input in document order (list order is part
    of the digest), and names collapsing to one identifier stay distinct."""
    document = _doc(
        {"my task": _ref(name="a"), "My Task": _ref(name="b"), "MY-TASK": _ref(name="c")},
        inputs=[{"name": "b", "type": "String"}, {"name": "a", "type": "String"}],
    )
    source = decompile_pipeline(document).source
    assert source.index("graph_input('b'") < source.index("graph_input('a'")
    assigned = [n.targets[0].id for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Assign)]
    assert len([a for a in assigned if a.startswith("task_")]) == len({a for a in assigned if a.startswith("task_")}) == 3


@pytest.mark.parametrize(
    ("tasks", "order"),
    [
        # Consumer listed before its producer: reordered.
        ({"Consumer": {**_ref(name="c"), "arguments": {"x": _out("Producer", "o")}}, "Producer": _ref(name="p")}, ["task_producer", "task_consumer"]),
        # Already topological: document order kept, even for an independent task.
        ({"Zebra": _ref(name="z"), "Alpha": _ref(name="a")}, ["task_zebra", "task_alpha"]),
        ({"A": _ref(name="a"), "B": {**_ref(name="b"), "arguments": {"x": _out("A", "o")}}, "C": _ref(name="c")}, ["task_a", "task_b", "task_c"]),
    ],
    ids=["producer-first", "topological-kept", "independent-does-not-jump"],
)
def test_task_order_is_topological_with_document_order_ties(tasks: dict[str, Any], order: list[str]) -> None:
    source = decompile_pipeline(_doc(tasks)).source
    positions = [source.index(f"{name} =") for name in order]
    assert positions == sorted(positions)


@pytest.mark.parametrize(
    "hostile",
    [
        '"""\nimport os\nos.system("id")\n#',
        "'; import os; os.system('id'); '",
        "\\x27 + __import__('os').system('id') + \\x27",
        "line one\nline two",
        "#!/bin/sh\nrm -rf /",
    ],
)
def test_hostile_text_cannot_escape_into_code(hostile: str) -> None:
    """Every recovered value is an ast.Constant: the generated file is executed
    during verification, so escaping a literal would be code execution."""
    tree = ast.parse(
        decompile_pipeline(
            {"name": hostile, "description": hostile, "implementation": {"graph": {"tasks": {hostile: {**_ref(name="c"), "arguments": {"a": hostile}}}}}}
        ).source
    )
    assert hostile in {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)}
    assert hostile not in ast.get_docstring(tree)
    assert [type(n).__name__ for n in tree.body] == ["Expr", "ImportFrom", "FunctionDef"]


def test_a_pipeline_named_after_a_builtin_does_not_shadow_it() -> None:
    """``def getattr(...)`` at module level would break the emitter's own getattr use."""
    document = _doc(
        {"a": {"componentRef": {"digest": DIGEST}, "arguments": {}}},
        graph={"outputValues": {"r": _out("a", "odd-name")}},
        outputs=[{"name": "r", "type": "String"}],
    )
    document["name"] = "getattr"
    result = decompile_pipeline(document)
    assert result.verified
    assert "def getattr_(" in result.source


def test_output_is_deterministic_and_independent_of_key_order() -> None:
    forward = _doc(description="d", metadata={"labels": {"a": "1"}}, inputs=[{"name": "i", "type": "String"}])
    shuffled = dict(reversed(list(forward.items())))
    assert decompile_pipeline(forward).source == decompile_pipeline(forward).source == decompile_pipeline(shuffled).source


# ---------------------------------------------------------------------------
# In-memory refusals: each names what it cannot faithfully express

REFUSALS = [
    pytest.param(_doc({"A": {**_ref(name="a"), "arguments": {"x": _out("B", "o")}}, "B": {**_ref(name="b"), "arguments": {"x": _out("A", "o")}}}), "unsupported-task-graph", id="cycle"),
    pytest.param(_doc({"T": _ref(name="c", spec={"implementation": {"container": {}}})}), "unsupported-document", id="hydrated-spec"),
    # A leaked compile-time template is never swept into the raw() runtime-sentinel exemption.
    pytest.param(_doc({"T": {**_ref(name="c"), "arguments": {"a": "{% for x in y %}{{ x }}{% endfor %}"}}}), "unsupported-document", id="jinja-statement"),
    # The schema permits these; the backend silently drops them.
    pytest.param(_doc({"T": {**_ref(name="c"), "executionOptions": {"retryStrategy": {"maxRetries": 1, "backoff": "60s"}}}}), "unsupported-execution-options", id="retry-backoff"),
    pytest.param(_doc({"T": {**_ref(name="c"), "executionOptions": {"timeoutStrategy": {}}}}), "unsupported-execution-options", id="empty-unmodeled-group"),
    pytest.param(_doc({"P": _ref(name="p"), "C": {**_ref(name="c"), "arguments": {"x": _out("P", "_hidden")}}}), "unsupported-task-output-name", id="private-output"),
    pytest.param(_doc(inputs=[{"name": "i", "type": "String", "default": None}]), "unsupported-input-default", id="null-default"),
    pytest.param(_doc(inputs=[{"name": "i", "type": "String", "annotations": {"system/x": "y"}}]), "unsupported-io-annotations", id="system-annotation"),
    # N2 covers empty containers, never nulls.
    pytest.param(_doc(outputs=None), "unsupported-document", id="null-block"),
    pytest.param(
        _doc({"Only Task": _ref(name="comp"), "R": {**_ref(name="r"), "arguments": {"x": {"graphInput": {"inputName": "In", "type": "String"}}}}}, inputs=[{"name": "In", "type": "String"}]),
        "unsupported-edge-fields",
        id="edge-wrapper-extra-field",
    ),
]


@pytest.mark.parametrize(("document", "code"), REFUSALS)
def test_unrepresentable_documents_are_refused_by_code(document: dict[str, Any], code: str) -> None:
    with pytest.raises(DecompileError) as excinfo:
        decompile_pipeline(document)
    assert excinfo.value.code == code


def test_a_refusal_reports_structure_never_the_offending_value() -> None:
    """Diagnostics name a location from structured metadata, never the value:
    a planted value shaped like a location must not leak into the message."""
    planted = " at SYNTHETIC_SCHEMA_VALUE_136: "
    document = _doc({"Task With Spaces": {**_ref(digest=DIGEST), "arguments": {"token": {"malformed": planted}}}})
    streams, message = _captured(lambda: decompile_pipeline(document, verify=False))
    assert "SYNTHETIC_SCHEMA_VALUE_136" not in message + streams
    assert "tasks.Task With Spaces.arguments.token" in message


def test_verification_is_what_catches_an_unfaithful_emission(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop a field during codegen and the digest gate must fire."""
    import tangle_cli.pipeline_decompiler as decompiler

    original = decompiler._graph_input_statement
    monkeypatch.setattr(decompiler, "_graph_input_statement", lambda plan, spec: original(plan, {k: v for k, v in spec.items() if k != "description"}))
    with pytest.raises(DecompileError) as excinfo:
        decompile_pipeline(_doc(inputs=[{"name": "i", "type": "String", "description": "dropped"}]))
    assert excinfo.value.code == "verification-digest-mismatch"
    assert "description" in str(excinfo.value)


@pytest.mark.parametrize(
    ("optional", "reported"), [(None, True), (True, False)], ids=["implied-optional", "explicit-optional"]
)
def test_n1_is_reported_only_when_a_default_implies_optional(optional: bool | None, reported: bool) -> None:
    field = {"name": "i", "type": "String", "default": "x", **({"optional": optional} if optional else {})}
    result = decompile_pipeline(_doc(inputs=[field]))
    assert result.verified
    assert any("N1" in note for note in result.normalizations) is reported


def _task(d: dict[str, Any]) -> dict[str, Any]:
    return d["implementation"]["graph"]["tasks"]["Only Task"]


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda d: d.update(metadata={}), id="metadata"),
        pytest.param(lambda d: d.update(inputs=[]), id="inputs"),
        pytest.param(lambda d: d.update(outputs=[]), id="outputs"),
        pytest.param(lambda d: d["implementation"]["graph"].update(outputValues={}), id="outputValues"),
        pytest.param(lambda d: _task(d).update(arguments={}), id="arguments"),
        pytest.param(lambda d: _task(d).update(annotations={}), id="annotations"),
        pytest.param(lambda d: _task(d).update(executionOptions={}), id="executionOptions"),
    ],
)
def test_n2_treats_a_vacuous_block_as_absent(mutate: Any) -> None:
    document = _doc()
    mutate(document)
    result = decompile_pipeline(document)
    assert result.verified
    assert any("N2" in note for note in result.normalizations)


def test_n2_never_drops_a_block_that_has_content() -> None:
    document = _doc(
        {"Only Task": {"annotations": {"k": "v"}, **_ref(name="comp"), "arguments": {"a": "b"}, "executionOptions": {"cachingStrategy": {"maxCacheStaleness": "P0D"}}}},
        metadata={"labels": {"a": "1"}},
    )
    result = decompile_pipeline(document)
    assert result.normalizations == ()
    assert "labels={'a': '1'}" in result.source and "a='b'" in result.source


EXAMPLES = sorted((pathlib.Path(__file__).resolve().parents[1] / "examples" / "python_pipeline").glob("*.py"))


def test_the_examples_directory_is_not_empty() -> None:
    assert EXAMPLES


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda p: p.stem)
def test_compiling_a_decompiled_example_reproduces_the_original_bytes(example: Path, tmp_path: Path) -> None:
    """compile -> decompile -> compile is a byte fixpoint on compiler output.

    Uses the in-memory API: the file command re-pins every component, which
    rewrites locators by design. Both compiles share one directory because a
    ``@task`` compiles to a relative sidecar ref resolved against the output.
    """
    workdir = tmp_path / "out"
    workdir.mkdir()
    compile_pipeline(example, workdir / "p.yaml")
    original = (workdir / "p.yaml").read_text(encoding="utf-8")
    generated = tmp_path / "generated.py"
    generated.write_text(decompile_pipeline(parse_yaml_string(original)).source, encoding="utf-8")
    compile_pipeline(generated, workdir / "p2.yaml")
    assert (workdir / "p2.yaml").read_text(encoding="utf-8") == original


# ---------------------------------------------------------------------------
# File entry point: the ORIGINAL input is validated before anything resolves


def _unresolvable_sibling(tasks: dict[str, Any]) -> dict[str, Any]:
    return {**tasks, "unresolvable": _ref(url="file://./definitely-missing.yaml")}


def _nested_with(leaf_task: dict[str, Any]) -> dict[str, Any]:
    return _ref(spec=_graph_spec("Inner", {"leaf": leaf_task}))


class _Unreachable(_Library):
    def get_component_spec(self, digest: str) -> Any:
        raise ConnectionError("simulated outage")


INPUT_REFUSALS = [
    # Legacy inline text: hydration would drop it silently. Refused on key presence, at any depth.
    pytest.param(_graph_spec("O", {"a": _ref(spec=_leaf(), text="name: Legacy\n")}), None, "unsupported-document", id="legacy-text"),
    pytest.param(_graph_spec("O", {"a": _ref(spec=_leaf(), text="")}), None, "unsupported-document", id="legacy-text-empty"),
    pytest.param(_graph_spec("O", {"sub": _nested_with(_ref(spec=_leaf(), text="x"))}), None, "unsupported-document", id="legacy-text-nested"),
    # Compile-time templates: refused in both shapes, at any depth.
    pytest.param(_graph_spec("O", {"a": {**_ref(spec=_leaf()), "arguments": {"x": "{% if y %}"}}}), None, "unsupported-document", id="template-hydrated"),
    pytest.param(_graph_spec("O", {"sub": _nested_with({**_ref(spec=_leaf()), "arguments": {"q": "{% if x %}1{% endif %}"}})}), None, "unsupported-document", id="template-nested"),
    pytest.param(_graph_spec("O", {"a": {**_ref(name="c"), "arguments": {"x": "{% if y %}"}}}), None, "unsupported-document", id="template-dehydrated"),
    # Each of these ALSO references an unresolvable component: the contract
    # error must win, proving validation ran before any resolution.
    pytest.param({**_graph_spec("O", _unresolvable_sibling({"a": _ref(name="c")})), "bogusRootKey": 1}, None, "unsupported-document", id="unknown-key-before-resolution"),
    pytest.param({**_graph_spec("O", _unresolvable_sibling({"a": _ref(spec=_leaf())})), "inputs": "not-a-list"}, None, "unsupported-document", id="hydrated-bad-shape-before-resolution"),
    # Resolution failures are split by who can fix them.
    pytest.param(_graph_spec("O", {"a": _ref(url="file://./missing.yaml")}), None, "unresolved-component", id="missing-file"),
    pytest.param(_graph_spec("O", {"a": _ref(name="Missing")}), _Library(), "unresolved-component", id="unpublished-name"),
    # A locator-less top-level ref never reaches resolution: the schema requires one.
    pytest.param(_graph_spec("O", {"a": {"componentRef": {}}}), None, "unsupported-document", id="empty-ref"),
    pytest.param(_graph_spec("O", {"a": _ref(url="")}), None, "unsupported-document", id="falsy-url"),
    pytest.param(_graph_spec("O", {"a": _ref(digest=DIGEST)}), _Unreachable(), "component-resolution-failed", id="library-unreachable"),
]


@pytest.mark.parametrize(("document", "client", "code"), INPUT_REFUSALS)
def test_an_unsupported_or_unresolvable_input_publishes_nothing(tmp_path: Path, document: dict[str, Any], client: Any, code: str) -> None:
    source = _write(tmp_path / "in.yaml", document)
    with pytest.raises(DecompileError) as excinfo:
        decompile_pipeline_file(source, tmp_path / "out.py", client=client)
    assert excinfo.value.code == code
    assert sorted(p.name for p in tmp_path.iterdir()) == ["in.yaml"]


def test_a_silently_unresolved_nested_component_is_refused(tmp_path: Path) -> None:
    """The one resolution failure that raises nothing: the hydrator returns a
    locator-less NESTED ref untouched. Input validation never sees a
    referenced file, so only the post-resolution check can catch it."""
    _write(tmp_path / "outer.yaml", _graph_spec("Outer", {"inner": {"componentRef": {}}}))
    source = _pipeline(tmp_path, _ref(url="file://./outer.yaml"), name="in.yaml")
    with pytest.raises(DecompileError) as excinfo:
        decompile_pipeline_file(source, tmp_path / "out.py")
    assert excinfo.value.code == "unresolved-component"
    assert "inner" in excinfo.value.message
    assert not (tmp_path / "out.py").exists()


def test_an_unparseable_input_reports_position_not_content(tmp_path: Path) -> None:
    source = tmp_path / "bad.yaml"
    source.write_text("name: ok\n\tSYNTHETIC_TOKEN_136: [unclosed\n", encoding="utf-8")
    with pytest.raises(DecompileError) as excinfo:
        decompile_pipeline_file(source, tmp_path / "out.py")
    assert excinfo.value.code == "unreadable-input"
    assert "SYNTHETIC_TOKEN_136" not in str(excinfo.value)


@pytest.mark.parametrize("nested", [False, True], ids=["flat", "nested-runtime-sentinel"])
def test_equivalent_hydrated_and_dehydrated_inputs_decompile_identically(tmp_path: Path, nested: bool) -> None:
    """Both shapes are canonicalized before codegen. A ``{{input_1}}`` runtime
    sentinel is legitimate at any depth, so nesting must not break parity."""
    leaf_task = {**_ref(spec=_leaf()), "arguments": {"q": "SELECT {{input_1}}"}} if nested else _ref(spec=_leaf())
    inner = _graph_spec("Inner", {"leaf": leaf_task})
    hydrated = _pipeline(tmp_path / "h", _ref(spec=inner) if nested else leaf_task)
    dehydrated_dir = tmp_path / "d"
    _write(dehydrated_dir / "c.yaml", inner if nested else _leaf())
    dehydrated = _pipeline(dehydrated_dir, _ref(url="file://./c.yaml"))

    one = decompile_pipeline_file(hydrated, hydrated.parent / "out.py")
    two = decompile_pipeline_file(dehydrated, dehydrated.parent / "out.py")
    assert one.verified and two.verified
    assert one.source == two.source
    assert one.component_files == two.component_files


# ---------------------------------------------------------------------------
# DIGEST canonicalization: every task goes through the resolve sidecar

def _versioned(marker: str, version: str) -> dict[str, Any]:
    spec = _leaf(marker)
    spec["metadata"] = {"annotations": {"version": version}}
    return spec


def _sidecar_case(kind: str, tmp_path: Path) -> tuple[Path, Any, str | None]:
    """Return (source, client, expected verified primary digest or None)."""
    if kind == "unpublished-file":
        return _local_leaf_source(tmp_path), None, None
    if kind == "published-file":
        text = dump_yaml(_leaf())
        (tmp_path / "leaf.yaml").write_text(text, encoding="utf-8")
        digest = compute_text_digest(text)
        return _pipeline(tmp_path, _ref(url="file://./leaf.yaml")), _Library({digest: _leaf()}), digest
    if kind == "name-only":
        digest, library = _published(_leaf())
        return _pipeline(tmp_path, _ref(name="Leaf")), library, digest
    if kind == "name-highest-version":
        old, new = _versioned("old", "1.0.0"), _versioned("new", "2.0.0")
        library = _Library({compute_text_digest(dump_yaml(s)): s for s in (old, new)})
        return _pipeline(tmp_path, _ref(name="Leaf")), library, compute_text_digest(dump_yaml(new))
    if kind == "inline-spec-has-no-text-provenance":
        # A documented limit: the library indexes the source TEXT digest, which
        # an inline spec does not have, so it gets only its local copy.
        _, library = _published(_leaf())
        return _pipeline(tmp_path, _ref(spec=_leaf())), library, None
    if kind == "canonical-url-is-not-kept":
        spec = {**_leaf(), "metadata": {"annotations": {"canonical_location": "https://example.test/leaf.yaml"}}}
        _write(tmp_path / "leaf.yaml", spec)
        return _pipeline(tmp_path, _ref(url="file://./leaf.yaml")), None, None
    raise AssertionError(kind)


@pytest.mark.parametrize(
    "kind",
    ["unpublished-file", "published-file", "name-only", "name-highest-version", "inline-spec-has-no-text-provenance", "canonical-url-is-not-kept"],
)
def test_a_verified_leaf_is_referenced_by_digest_alone_by_default(tmp_path: Path, kind: str) -> None:
    """Published: ref(digest=...) and no local copy at all. Unpublished: the
    local copy is the only representation, so it is always written."""
    source, client, primary = _sidecar_case(kind, tmp_path)
    output = tmp_path / "out" / "gen.py"

    result = decompile_pipeline_file(source, output, client=client)

    script = output.read_text(encoding="utf-8")
    assert result.verified and result.digest_refs == (1 if primary else 0)
    if primary:
        assert f"ref(digest={primary!r})" in script and "resolve://" not in script
        assert not output.with_name("gen.leaves.yaml").exists() and not output.with_name("gen.leaves").exists()
        assert result.resolve_config is None and result.components_dir is None
    else:
        assert [set(e) for e in _fragment(output)] == [{"local"}]


@pytest.mark.parametrize(
    "kind",
    ["unpublished-file", "published-file", "name-only", "name-highest-version", "inline-spec-has-no-text-provenance", "canonical-url-is-not-kept"],
)
def test_local_fallbacks_keep_a_verified_primary_and_a_local_copy(tmp_path: Path, kind: str) -> None:
    source, client, primary = _sidecar_case(kind, tmp_path)
    output = tmp_path / "out" / "gen.py"

    result = decompile_pipeline_file(source, output, client=client, include_local_fallbacks=True)

    assert result.digest_refs == 0
    entries = _fragment(output)
    local = entries[-1]
    assert set(local) == {"local"} and local["local"].startswith("./gen.leaves/")
    assert (output.parent / local["local"]).is_file()
    assert entries[:-1] == ([{"digest": primary, "fallback_on_error": True}] if primary else [])
    for text in (output.read_text(encoding="utf-8"), output.with_name("gen.leaves.yaml").read_text(encoding="utf-8")):
        assert "https://" not in text and str(tmp_path) not in text, "no mutable or absolute locator"
    assert result.verified and result.resolve_config == output.with_name("gen.leaves.yaml")


def test_one_component_shares_a_copy_and_same_named_components_stay_distinct(tmp_path: Path) -> None:
    _write(tmp_path / "first.yaml", _leaf("first"))
    _write(tmp_path / "second.yaml", _leaf("second"))
    tasks = {"a": _ref(url="file://./first.yaml"), "b": _ref(url="file://./second.yaml"), "c": _ref(url="file://./first.yaml")}
    source = _write(tmp_path / "in.yaml", _graph_spec("O", tasks))

    result = decompile_pipeline_file(source, tmp_path / "out.py")

    assert len(result.component_files) == 2, "same content shares one copy; distinct content never collapses"
    bodies = sorted((tmp_path / "out.leaves" / f).read_text(encoding="utf-8") for f in result.component_files)
    assert "first" in bodies[0] and "second" in bodies[1]


# ---------------------------------------------------------------------------
# Three-artifact publication: script, <stem>.leaves.yaml, <stem>.leaves/


def _published_leaf_source(tmp_path: Path, *, nested: bool) -> tuple[Path, _Library]:
    """An inline leaf carrying a published digest the library verifies."""
    digest, library = _published(_leaf("published-leaf"))
    task = _ref(digest=digest, spec=_leaf("published-leaf"))
    return _pipeline(tmp_path, _nested_with(task) if nested else task), library


@pytest.mark.parametrize("nested", [False, True], ids=["root", "nested"])
def test_the_three_products_relocate_compile_and_rehydrate_offline(tmp_path: Path, nested: bool) -> None:
    """Move all three products, compile from the moved script, and hydrate
    through the moved sidecar with the component library unreachable."""
    source, library = _published_leaf_source(tmp_path, nested=nested)
    work = tmp_path / "work"

    result = decompile_pipeline_file(source, work / "gen.py", client=library, include_local_fallbacks=True)

    assert result.verified
    assert (result.subgraph_module is not None) is nested
    companion = {"gen_subgraphs.py"} if nested else set()
    assert set(_snapshot(work)) == {"gen.py", "gen.leaves.yaml", *companion, *(f"gen.leaves/{f}" for f in result.component_files)}
    moved = tmp_path / "moved"
    shutil.copytree(work, moved)
    shutil.rmtree(work)
    # The natural invocation: <stem>.yaml beside the script, where a @task
    # sidecar would be written as <stem>.components.yaml.
    compile_pipeline(moved / "gen.py", moved / "gen.yaml")
    hydrated = PipelineHydrator(client=_UnreachableLibrary()).hydrate_file(moved / "gen.yaml").data
    assert _leaf_specs(hydrated) == [compute_spec_digest(_leaf("published-leaf"))]


@pytest.mark.parametrize("nested", [False, True], ids=["root", "nested"])
def test_mixed_default_output_relocates_and_rehydrates_exactly(tmp_path: Path, nested: bool) -> None:
    """A published leaf (twice, deduplicated) by digest, an unpublished one
    local only: moved, compiled, and hydrated through the library, every spec
    is the input's -- and a stale copy of the published leaf is not kept."""
    published = _leaf("published-leaf")
    digest, library = _published(published)
    _write(tmp_path / "local.yaml", _leaf("local-only"))
    tasks = {"a": _ref(digest=digest, spec=published), "b": _ref(digest=digest, spec=published), "c": _ref(url="file://./local.yaml")}
    document = _graph_spec("Outer", tasks)
    source = _write(tmp_path / "in.yaml", _graph_spec("Outer", {"n": _ref(spec=_graph_spec("Inner", tasks))}) if nested else document)
    work = tmp_path / "work"
    decompile_pipeline_file(source, work / "gen.py", client=library, include_local_fallbacks=True)

    result = decompile_pipeline_file(source, work / "gen.py", client=library)

    generated = "".join(p.read_text(encoding="utf-8") for p in work.glob("*.py"))
    assert result.verified and result.digest_refs == 1 and generated.count(f"ref(digest={digest!r})") == 2
    assert [p.name for p in (work / "gen.leaves").iterdir()] == [f for f in result.component_files] and len(result.component_files) == 1
    assert "local-only" in (work / "gen.leaves" / result.component_files[0]).read_text(encoding="utf-8")
    assert len(parse_yaml_string((work / "gen.leaves.yaml").read_text(encoding="utf-8"))) == 1
    moved = tmp_path / "moved"
    shutil.move(str(work), moved)
    compile_pipeline(moved / "gen.py", moved / "gen.yaml")
    hydrated = PipelineHydrator(client=library).hydrate_file(moved / "gen.yaml").data
    assert sorted(_leaf_specs(hydrated)) == sorted(compute_spec_digest(_leaf(m)) for m in ("published-leaf", "published-leaf", "local-only"))


@pytest.mark.parametrize("existing", ["file", "absolute-symlink", "relative-symlink", "stale-products"])
def test_the_output_is_always_replaced_never_written_through_or_merged(tmp_path: Path, existing: str) -> None:
    source = _local_leaf_source(tmp_path)
    output = tmp_path / "gen.py"
    target = tmp_path / "target.py"
    target.write_text("# untouched\n", encoding="utf-8")
    if existing == "file":
        output.write_text("# stale\n", encoding="utf-8")
    elif existing.endswith("symlink"):
        output.symlink_to(target if existing == "absolute-symlink" else Path("target.py"))
    else:
        _write(tmp_path / "gen.leaves" / "stale.yaml", {"name": "stale"})
        _write(tmp_path / "gen.leaves.yaml", {"stale-fragment": []})
        # The compiler's (or a user's) artifacts under the OLD name are not ours.
        _write(tmp_path / "gen.components" / "theirs.yaml", {"name": "theirs"})
        _write(tmp_path / "gen.components.yaml", {"their-fragment": []})
    foreign = {k: v for k, v in _snapshot(tmp_path).items() if ".components" in k}

    decompile_pipeline_file(source, output)

    assert {k: v for k, v in _snapshot(tmp_path).items() if ".components" in k} == foreign

    assert not output.is_symlink() and "# stale" not in output.read_text(encoding="utf-8")
    assert target.read_text(encoding="utf-8") == "# untouched\n", "a symlink is replaced, never followed"
    assert not (tmp_path / "gen.leaves" / "stale.yaml").exists()
    assert "stale-fragment" not in parse_yaml_string((tmp_path / "gen.leaves.yaml").read_text(encoding="utf-8"))
    assert not list(tmp_path.glob("*.replaced-*")) and not list(tmp_path.glob(".decompile-*"))


@pytest.mark.parametrize("fault", ["refused-input", "gen_subgraphs.py", "gen_tasks", "gen.leaves.yaml", "gen.leaves", "gen.py"])
def test_any_failure_leaves_the_previous_generation_exactly_intact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str) -> None:
    """No partial destination state: a refusal, or a failed rename at any of the
    five boundaries, leaves the previous script with its own companion, tasks
    package, sidecar and bundle."""
    import tangle_cli.pipeline_decompiler as module

    python = _python_leaf(tmp_path)

    def nested(marker: str) -> Path:
        child = _graph_spec(f"Child {marker}", {"leaf": _ref(spec=_leaf(marker))})
        return _write(tmp_path / f"{marker}.yaml", _graph_spec("O", {"a": _ref(spec=child), "py": _ref(spec=python)}))

    output = tmp_path / "gen.py"
    decompile_pipeline_file(nested("previous"), output)
    assert {"gen_subgraphs.py", "gen_tasks/__init__.py", "gen.leaves.yaml"} <= set(_snapshot(tmp_path)), "all five products"
    source = nested("next")
    if fault == "refused-input":
        source = _write(tmp_path / "bad.yaml", _graph_spec("O", {"a": _ref(url="file://./missing.yaml")}))
        expected: type[BaseException] = DecompileError
    else:
        real_replace = os.replace
        reached: list[Path] = []

        def fail_at_boundary(src: Any, dst: Any) -> Any:
            # The PUBLICATION rename from staging into the destination -- not a
            # rename inside staging, nor the rollback restoring the aside copy.
            if Path(dst) == tmp_path / fault and Path(src).parent.name.startswith(".decompile-"):
                reached.append(Path(dst))
                raise PermissionError("denied")
            return real_replace(src, dst)

        monkeypatch.setattr(module.os, "replace", fail_at_boundary)
        expected = PermissionError
    before = _snapshot(tmp_path)

    with pytest.raises(expected):
        decompile_pipeline_file(source, output)
    monkeypatch.undo()

    assert fault == "refused-input" or reached == [tmp_path / fault], "the fault fired at the publication boundary"
    assert _snapshot(tmp_path) == before, fault
    assert not list(tmp_path.glob("*.replaced-*")) and not list(tmp_path.glob(".decompile-*"))


# ---------------------------------------------------------------------------
# Gate (a): the pinned document must reproduce the pipeline, online AND offline

SIDECAR_PIN = {"url": "resolve://./out.leaves.yaml#leaf"}


def _pinned_pair(script_dir: Path, pinned_ref: dict[str, Any], manifest: dict[str, Any] | None = None) -> CanonicalComponents:
    """A hydrated/pinned pair with a sidecar that is correct by construction."""
    _write(script_dir / "out.leaves" / "leaf.yaml", _leaf())
    _write(script_dir / "out.leaves.yaml", manifest if manifest is not None else {"leaf": [{"local": "./out.leaves/leaf.yaml"}]})
    return CanonicalComponents(
        hydrated=_graph_spec("Outer", {"a": _ref(name="Leaf", spec=_leaf())}),
        pinned=_graph_spec("Outer", {"a": {"componentRef": pinned_ref}}),
    )


@pytest.mark.parametrize(
    ("case", "code"),
    [
        ("generated-sidecar", None),
        # Each of these resolves correctly TODAY -- content comparison alone
        # cannot catch them -- but breaks once the script moves.
        ("absolute-sidecar", "pinning-incomplete"),
        ("traversal", "pinning-incomplete"),
        ("foreign-manifest", "pinning-incomplete"),
        ("bare-file", "pinning-incomplete"),
        ("entry-escapes-bundle", "pinning-incomplete"),
        ("missing-sidecar", "pinning-unresolvable"),
        ("undefined-fragment", "pinning-unresolvable"),
        ("content-differs", "pinning-content-mismatch"),
    ],
)
def test_gate_a_accepts_only_the_exact_portable_generated_sidecar(tmp_path: Path, case: str, code: str | None) -> None:
    script_dir = tmp_path / "s"
    manifest = {"leaf": [{"local": "./elsewhere/leaf.yaml"}]} if case == "entry-escapes-bundle" else None
    canonical = _pinned_pair(script_dir, SIDECAR_PIN, manifest)
    _pinned_pair(tmp_path, SIDECAR_PIN)  # a twin in the parent, so traversal resolves
    _write(script_dir / "elsewhere" / "leaf.yaml", _leaf())
    shutil.copy(script_dir / "out.leaves.yaml", script_dir / "other.yaml")
    pins = {
        "absolute-sidecar": {"url": f"resolve://{script_dir / 'out.leaves.yaml'}#leaf"},
        "traversal": {"url": "resolve://./../out.leaves.yaml#leaf"},
        "foreign-manifest": {"url": "resolve://./other.yaml#leaf"},
        "bare-file": {"url": "file://./out.leaves/leaf.yaml"},
        "undefined-fragment": {"url": "resolve://./out.leaves.yaml#never-generated"},
    }
    if case in pins:
        canonical.pinned["implementation"]["graph"]["tasks"]["a"]["componentRef"] = pins[case]
    if case == "missing-sidecar":
        (script_dir / "out.leaves.yaml").unlink()
    if case == "content-differs":
        _write(script_dir / "out.leaves" / "leaf.yaml", _leaf("different"))

    check = lambda: assert_pinning_preserved(canonical, script_dir=script_dir, sidecar="out.leaves.yaml")  # noqa: E731
    if code is None:
        check()
        return
    with pytest.raises(DecompileError) as excinfo:
        check()
    assert excinfo.value.code == code


def _sabotage_copies(monkeypatch: pytest.MonkeyPatch, marker: str, content: str | None) -> None:
    """Replace -- or drop -- only the local copies whose text contains ``marker``."""
    from tangle_cli.pipeline_dehydrator import PipelineDehydrator

    original = PipelineDehydrator._write_text

    def sabotaged(self, destination, text, *, kind="output"):  # type: ignore[no-untyped-def]
        if kind != "component" or marker not in text:
            return original(self, destination, text, kind=kind)
        return original(self, destination, content, kind=kind) if content is not None else None

    monkeypatch.setattr(PipelineDehydrator, "_write_text", sabotaged)


@pytest.mark.parametrize("nested", [False, True], ids=["root", "nested"])
@pytest.mark.parametrize(
    ("published", "damage", "code"),
    [
        # A healthy remote primary short-circuits online resolution, so only the
        # forced-offline pass can see these; they would ship broken otherwise.
        (True, "missing", "pinning-incomplete"),
        (True, "corrupted", "pinning-content-mismatch"),
        # With no primary, the local copy IS the online path.
        (False, "missing", "pinning-unresolvable"),
        (False, "corrupted", "pinning-content-mismatch"),
    ],
)
def test_a_bad_local_fallback_is_refused_and_nothing_is_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, nested: bool, published: bool, damage: str, code: str
) -> None:
    if published:
        source, library = _published_leaf_source(tmp_path, nested=nested)
    else:
        leaf_task = _ref(spec=_leaf("published-leaf"))
        source, library = _pipeline(tmp_path, _nested_with(leaf_task) if nested else leaf_task), None
    output = tmp_path / "gen.py"
    decompile_pipeline_file(source, output, client=library)
    before = _snapshot(tmp_path)

    _sabotage_copies(monkeypatch, "published-leaf", None if damage == "missing" else dump_yaml(_leaf("CORRUPTED")))
    with pytest.raises(DecompileError) as excinfo:
        decompile_pipeline_file(source, output, client=library)

    assert excinfo.value.code == code
    assert _snapshot(tmp_path) == before
    assert not list(tmp_path.glob(".decompile-*"))


def _scores(extra_optional_input: bool) -> dict[str, Any]:
    """A sanitized stand-in for a real CompositeScores regression.

    Its successor adds one optional input, whose ``if/isPresent`` block lands
    before the ``outputPath`` items and so shifts the whole command array.
    """
    def optional(name: str) -> dict[str, Any]:
        return {"if": {"cond": {"isPresent": name}, "then": [f"--{name}", {"inputValue": name}]}}

    inputs = [{"name": n, "type": "String"} for n in ("run_id", "table_a", "table_b")]
    inputs.append({"name": "out_table", "type": "String", "optional": True})
    command: list[Any] = ["python", "-m", "--run_id", {"inputValue": "run_id"}, "--table_a", {"inputValue": "table_a"}]
    command += ["--table_b", {"inputValue": "table_b"}, optional("out_table")]
    if extra_optional_input:
        inputs.append({"name": "new_flag", "type": "String", "optional": True})
        command.append(optional("new_flag"))
    command += ["--o1", {"outputPath": "o1"}, "--o2", {"outputPath": "o2"}]
    return {
        "name": "CompositeScores",
        "inputs": inputs,
        "outputs": [{"name": "o1"}, {"name": "o2"}],
        "implementation": {"container": {"image": "example/scores:1", "command": command}},
    }


@pytest.mark.parametrize("successor", ["different", "identical"])
@pytest.mark.parametrize("shape", ["hydrated", "dehydrated-digest"])
def test_seed_time_exactness_ignores_deprecation_successors(tmp_path: Path, shape: str, successor: str) -> None:
    """Decompile is a one-time seeding transform: it reproduces the EXACT
    component the input references. A deprecation successor -- even one that
    changes the command -- is neither seeded nor used to judge the output;
    later hydrate/submit/run still upgrade, which is the maintainer's call."""
    seeded = _scores(extra_optional_input=False)
    newer = _scores(extra_optional_input=successor == "different")
    digest, successor_digest = compute_text_digest(dump_yaml(seeded)), "5" * 64
    library = _Library({digest: seeded, successor_digest: newer}, successors={digest: successor_digest})
    leaf = _ref(digest=digest, name="CompositeScores", spec=seeded) if shape == "hydrated" else _ref(digest=digest)
    source = _pipeline(tmp_path, _nested_with(leaf))
    output = tmp_path / "out" / "gen.py"

    assert f"ref(digest={digest!r})" in decompile_pipeline_file(source, tmp_path / "default" / "gen.py", client=library).source + (
        tmp_path / "default" / "gen_subgraphs.py"
    ).read_text(encoding="utf-8"), "the seeded digest, not its successor"
    result = decompile_pipeline_file(source, output, client=library, include_local_fallbacks=True)

    assert result.verified
    manifest = parse_yaml_string(output.with_name("gen.leaves.yaml").read_text(encoding="utf-8"))
    leaf_entries = next(v for v in manifest.values() if v and "digest" in v[0])
    assert leaf_entries[0] == {"digest": digest, "fallback_on_error": True}, "the seeded digest, not its successor"
    local = parse_yaml_string((output.parent / leaf_entries[1]["local"]).read_text(encoding="utf-8"))
    assert compute_spec_digest(local) == compute_spec_digest(seeded), "the local copy is the exact seeded content"


# ---------------------------------------------------------------------------
# Privacy: no untrusted document or component value reaches any output channel


@pytest.mark.parametrize(
    "case",
    ["url-userinfo", "url-signed-query", "malformed-child-yaml", "successful-pin-names"],
)
def test_no_document_value_reaches_the_message_or_the_streams(tmp_path: Path, case: str) -> None:
    marker = "SYNTHETIC_SECRET_136"
    if case == "url-userinfo":
        source = _pipeline(tmp_path, _ref(url=f"file://user:{marker}@/nope.yaml"))
    elif case == "url-signed-query":
        source = _pipeline(tmp_path, _ref(url=f"https://example.invalid/c.yaml?sig={marker}"))
    elif case == "malformed-child-yaml":
        (tmp_path / "leaf.yaml").write_text(f"name: L\n\t{marker}: [x\n", encoding="utf-8")
        source = _pipeline(tmp_path, _ref(url="file://./leaf.yaml"))
    else:
        _write(tmp_path / "leaf.yaml", {**_leaf(), "name": marker})
        source = _pipeline(tmp_path, _ref(url="file://./leaf.yaml"))

    streams, message = _captured(lambda: decompile_pipeline_file(source, tmp_path / "o.py"))

    assert marker not in message and marker.lower() not in streams.lower()
    if case.startswith("url-"):
        assert "[redacted]" in message, "the locator is still reported, minus its credentials"
    if case == "successful-pin-names":
        assert message == "", "this run must succeed, so the pin path really ran"


def test_a_verbose_api_client_does_not_echo_the_fetched_spec(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The quiet logger must reach the API client the hydrator builds internally."""
    import json

    import requests

    import tangle_cli.client as client_module

    spec = {**_leaf(), "description": "SYNTHETIC_VERBOSE_VALUE_136"}
    digest = compute_text_digest(dump_yaml(spec))
    payload = {"spec": spec, "digest": digest}
    body = json.dumps(payload)

    class _Response:
        status_code = 200
        headers = {"Content-Type": "application/json"}
        text = body
        content = body.encode("utf-8")

        def json(self) -> dict[str, Any]:
            return payload

        def raise_for_status(self) -> None:
            pass

    monkeypatch.setenv("TANGLE_VERBOSE", "1")
    monkeypatch.setenv("TANGLE_API_URL", "https://review.invalid")
    monkeypatch.setattr(requests.Session, "request", lambda self, m, u, **k: _Response())
    monkeypatch.setattr(client_module.TangleApiClient, "resolve_digest", lambda self, d: d)
    output = tmp_path / "o.py"

    streams, message = _captured(lambda: decompile_pipeline_file(_pipeline(tmp_path, _ref(digest=digest)), output))

    assert message == "" and output.is_file(), "the fetch path must actually run"
    assert "SYNTHETIC_VERBOSE_VALUE_136" not in streams


def test_only_an_explicit_logger_is_forwarded_to_internal_api_clients() -> None:
    """Forwarding the DEFAULT logger would override the client's own verbosity
    gating and make ordinary runs start logging. No public seam exposes this,
    so it is asserted on the option the hydrator passes to the client."""
    quiet = object()
    assert "logger" not in PipelineHydrator()._client_options
    assert PipelineHydrator(logger=quiet)._client_options.get("logger") is quiet  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Every graph becomes Python: nested graphs are @pipeline functions in the
# companion <stem>_subgraphs.py, called through subpipeline(...)


@pytest.fixture(autouse=True)
def _forget_generated_companions() -> Any:
    """The compiler imports a companion by NAME, so evict any from a previous
    test -- otherwise its cached module would silently stand in for this one."""
    import sys

    def evict() -> None:
        for name in [m for m in sys.modules if m.endswith("_subgraphs")]:
            sys.modules.pop(name)

    evict()
    yield
    evict()


def _io_leaf(marker: str) -> dict[str, Any]:
    return {
        "name": "Leaf",
        "inputs": [{"name": "x", "type": "String"}],
        "outputs": [{"name": "y", "type": "String"}],
        "implementation": {"container": {"image": "i", "command": [marker, {"inputValue": "x"}, {"outputPath": "y"}]}},
    }


def _io_graph(name: str, tasks: dict[str, Any], *, output_from: str | None = None, inputs: tuple[str, ...] = ()) -> dict[str, Any]:
    spec = _graph_spec(name, tasks)
    if inputs:
        spec["inputs"] = [{"name": n, "type": "String"} for n in inputs]
    if output_from:
        spec["outputs"] = [{"name": "out", "type": "String"}]
        spec["implementation"]["graph"]["outputValues"] = {"out": _out(output_from, "y")}
    return spec


def _nested_document() -> dict[str, Any]:
    """Root -> Mid -> Inner (shared by two tasks, chained by an edge), plus two
    same-name 'Judge' graphs whose content differs."""
    inner = _io_graph("Inner", {"leaf": {**_ref(spec=_io_leaf("inner")), "arguments": {"x": {"graphInput": {"inputName": "seed"}}}}}, output_from="leaf", inputs=("seed",))
    judge_a = _io_graph("Judge", {"leaf": {**_ref(spec=_io_leaf("judge-a")), "arguments": {"x": "1"}}}, output_from="leaf")
    judge_b = _io_graph("Judge", {"leaf": {**_ref(spec=_io_leaf("judge-b")), "arguments": {"x": "1"}}}, output_from="leaf")
    mid = _graph_spec("Mid", {
        "i1": {**_ref(spec=inner), "arguments": {"seed": "s1"}, "annotations": {"k": "v"}},
        "i2": {**_ref(spec=inner), "arguments": {"seed": _out("i1", "out")}},
        "j": _ref(spec=judge_a),
    })
    return _graph_spec("Root", {"mid": _ref(spec=mid), "judge": _ref(spec=judge_b)})


def _functions(path: Path) -> list[str]:
    return [n.name for n in ast.parse(path.read_text(encoding="utf-8")).body if isinstance(n, ast.FunctionDef)]


def test_every_nested_graph_becomes_a_python_pipeline_and_round_trips(tmp_path: Path) -> None:
    document = _nested_document()
    source = _write(tmp_path / "in.yaml", document)
    work = tmp_path / "work"

    result = decompile_pipeline_file(source, work / "gen.py")

    assert result.verified and result.subgraph_module == work / "gen_subgraphs.py"
    assert _functions(work / "gen.py") == ["root"]
    children = _functions(work / "gen_subgraphs.py")
    assert children[:3] == ["inner", "judge", "mid"], "deepest first, so every call is to an earlier definition"
    assert len(children) == 4 and children[3].startswith("judge_"), "same name, different content: suffixed"
    assert (work / "gen_subgraphs.py").read_text(encoding="utf-8").count("subpipeline(inner)") == 2, "a shared graph is one function"
    bundle = [parse_yaml_string((work / "gen.leaves" / f).read_text(encoding="utf-8")) for f in result.component_files]
    assert all("container" in spec["implementation"] for spec in bundle), "no graph is left as YAML"

    again = tmp_path / "again"
    decompile_pipeline_file(source, again / "gen.py")
    for name in ("gen.py", "gen_subgraphs.py"):
        assert (again / name).read_text(encoding="utf-8") == (work / name).read_text(encoding="utf-8"), "deterministic"

    moved = tmp_path / "moved"
    shutil.copytree(work, moved)
    shutil.rmtree(work)
    compile_pipeline(moved / "gen.py", moved / "compiled.yaml")
    hydrated = PipelineHydrator(client=_UnreachableLibrary()).hydrate_file(moved / "compiled.yaml").data
    assert sorted(_leaf_specs(hydrated)) == sorted(_leaf_specs(document))


@pytest.mark.parametrize(("key", "value"), [("isEnabled", "true"), ("executionOptions", {"cachingStrategy": {"maxCacheStaleness": "P0D"}})])
def test_graph_task_metadata_that_subpipeline_cannot_express_is_refused(tmp_path: Path, key: str, value: Any) -> None:
    """Tangle does not apply these to graph-component tasks, and subpipeline(...) rejects them."""
    boundary = {**_ref(spec=_graph_spec("Child", {"leaf": _ref(spec=_leaf())})), key: value}
    source = _write(tmp_path / "in.yaml", _graph_spec("Root", {"sub": boundary}))
    with pytest.raises(DecompileError) as excinfo:
        decompile_pipeline_file(source, tmp_path / "out.py")
    assert excinfo.value.code == "unsupported-subgraph-task"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["in.yaml"]


def test_a_cycle_between_referenced_graphs_is_refused_by_name(tmp_path: Path) -> None:
    _write(tmp_path / "a.yaml", _graph_spec("A", {"t": _ref(url="file://./b.yaml")}))
    _write(tmp_path / "b.yaml", _graph_spec("B", {"t": _ref(url="file://./a.yaml")}))
    source = _write(tmp_path / "in.yaml", _graph_spec("Root", {"t": _ref(url="file://./a.yaml")}))
    with pytest.raises(DecompileError) as excinfo:
        decompile_pipeline_file(source, tmp_path / "out" / "gen.py")
    assert excinfo.value.code == "cyclic-component-reference"
    assert not (tmp_path / "out" / "gen.py").exists()


def test_a_real_shape_twelve_graph_pipeline_decompiles_to_python(tmp_path: Path) -> None:
    """The sanitized shape of a real export: 6 scrape graphs (staging and
    production) each holding a judge graph, where two same-name judges differ
    only by provenance -- 12 graph instances, 9 distinct graphs."""
    def judge(name: str, provenance: str) -> dict[str, Any]:
        spec = _graph_spec(name, {"score": _ref(spec=_leaf(f"score-{name}")), "rank": _ref(spec=_leaf("rank"))})
        return {**spec, "metadata": {"annotations": {"component_yaml_path": provenance}}}

    variants = [("Judge Evaluate v5.0", "judge-47d4.yaml"), ("Judge Evaluate v5.0", "judge-f899.yaml"), ("Judge Evaluate storefront_v2.0", "sf.yaml")]
    tasks = {
        f"{env} {index}": _ref(spec=_graph_spec(f"{env} scrape {index}", {"scrape": _ref(spec=_leaf(f"scrape-{env}-{index}")), "judge": _ref(spec=judge(*variant))}))
        for env in ("Staging", "Production")
        for index, variant in enumerate(variants)
    }
    document = _graph_spec("Daily Pulse - Real Time", tasks)
    work = tmp_path / "work"

    result = decompile_pipeline_file(_write(tmp_path / "in.yaml", document), work / "gen.py")

    assert result.verified
    children = _functions(work / "gen_subgraphs.py")
    assert len(children) == 9
    assert sum(name.startswith("judge_evaluate_v5_0") for name in children) == 2, "provenance-only difference: two functions"
    compile_pipeline(work / "gen.py", work / "compiled.yaml")
    hydrated = PipelineHydrator(client=_UnreachableLibrary()).hydrate_file(work / "compiled.yaml").data
    assert sorted(_leaf_specs(hydrated)) == sorted(_leaf_specs(document))



def test_consecutive_runs_in_one_process_never_see_a_stale_companion(tmp_path: Path) -> None:
    """The root imports its companion by name, so a companion cached by an
    earlier run -- lacking this run's child functions -- must not be reused."""
    import sys

    for index, child in enumerate(("Child", "Other")):
        directory = tmp_path / f"run{index}"
        document = _graph_spec("Root", {"sub": _ref(spec=_graph_spec(child, {"leaf": _ref(spec=_leaf(child))}))})
        assert decompile_pipeline_file(_write(directory / "in.yaml", document), directory / "gen.py").verified
        assert "gen_subgraphs" not in sys.modules, "nothing lingers into a later run"


# ---------------------------------------------------------------------------
# The generated module namespace: one allocator, and one-to-one companion names


def _child(name: str, marker: str) -> dict[str, Any]:
    return _ref(spec=_graph_spec(name, {"leaf": _ref(spec=_leaf(marker))}))


def _namespace_case(case: str) -> dict[str, Any]:
    if case == "task-local":
        return _graph_spec("Root", {"Load": _child("Task Load", "a")})
    if case == "input-local":
        return {**_graph_spec("Root", {"t": _child("Input Seed", "a")}), "inputs": [{"name": "seed", "type": "String"}]}
    if case == "helper-names":
        return _graph_spec("Root", {f"t{i}": _child(name, name) for i, name in enumerate(("ref", "subpipeline", "task", "pipeline", "In", "Graph Input"))})
    if case == "adversarial-suffix":
        # Filled in by the test: a graph literally NAMED like the suffix the
        # second 'Judge' is given.
        return _graph_spec("Root", {"a": _child("Judge", "a"), "c": _child("Judge", "b")})
    if case == "nested-child-calls-child":
        return _graph_spec("Root", {"m": _ref(spec=_graph_spec("Mid", {"Load": _child("Task Load", "a")}))})
    raise AssertionError(case)


@pytest.mark.parametrize("case", ["task-local", "input-local", "helper-names", "adversarial-suffix", "nested-child-calls-child"])
def test_child_function_names_never_collide_in_the_generated_module(tmp_path: Path, case: str) -> None:
    """No child may be shadowed by a local, shadow a helper, or silently replace
    another definition -- with verification on OR off, and stably."""
    document = _namespace_case(case)
    if case == "adversarial-suffix":
        probe = tmp_path / "probe"
        decompile_pipeline_file(_write(probe / "in.yaml", document), probe / "gen.py")
        (suffixed,) = [n for n in _functions(probe / "gen_subgraphs.py") if n != "judge"]
        tasks = document["implementation"]["graph"]["tasks"]
        document["implementation"]["graph"]["tasks"] = {"a": tasks["a"], "b": _child(suffixed, "literal"), "c": tasks["c"]}
    source = _write(tmp_path / "in.yaml", document)
    for label, verify in (("verified", True), ("unverified", False), ("repeat", True)):
        directory = tmp_path / label
        result = decompile_pipeline_file(source, directory / "gen.py", verify=verify)
        assert result.verified is verify
        names = _functions(directory / "gen_subgraphs.py")
        assert len(names) == len(set(names)), f"{label}: a definition would replace another"
        assert not {"ref", "subpipeline", "task", "pipeline", "In", "Out", "graph_input"} & set(names)
        assert not [n for n in names if n.startswith(("input_", "task_"))]
    assert (tmp_path / "repeat" / "gen_subgraphs.py").read_text(encoding="utf-8") == (tmp_path / "verified" / "gen_subgraphs.py").read_text(encoding="utf-8")
    compile_pipeline(tmp_path / "verified" / "gen.py", tmp_path / "verified" / "compiled.yaml")
    hydrated = PipelineHydrator(client=_UnreachableLibrary()).hydrate_file(tmp_path / "verified" / "compiled.yaml").data
    assert sorted(_leaf_specs(hydrated)) == sorted(_leaf_specs(document))


@pytest.mark.parametrize(("stem", "nested", "accepted"), [
    ("foo-bar", True, False),     # would alias foo_bar's companion
    ("1pipe", True, False),
    ("my.pipeline", True, False),
    ("résumé", True, False),      # non-ASCII module names may not import
    ("class", True, True),        # class_subgraphs is a valid module name
    ("foo-bar", False, True),     # no nested graphs, no companion: any name
])
def test_a_companion_name_must_be_a_one_to_one_module_name(tmp_path: Path, stem: str, nested: bool, accepted: bool) -> None:
    decompile_pipeline_file(_write(tmp_path / "a.yaml", _graph_spec("First", {"t": _child("First", "first")})), tmp_path / "foo_bar.py")
    document = _graph_spec("Second", {"t": _child("Second", "second")} if nested else {"t": _ref(spec=_leaf("second"))})
    source = _write(tmp_path / "b.yaml", document)
    before = _snapshot(tmp_path)

    if not accepted:
        with pytest.raises(DecompileError) as excinfo:
            decompile_pipeline_file(source, tmp_path / f"{stem}.py")
        assert excinfo.value.code == "unsupported-output-name"
        assert _snapshot(tmp_path) == before, "the neighbouring output and its companion are untouched"
        return
    assert decompile_pipeline_file(source, tmp_path / f"{stem}.py").verified
    compile_pipeline(tmp_path / "foo_bar.py", tmp_path / "first.yaml")  # the neighbour still compiles


def test_every_leaf_is_retargeted_once_even_when_slash_joined_paths_coincide(tmp_path: Path) -> None:
    """Task ids may contain ``/``: four distinct leaves whose ids all join to
    ``a/b/c`` must each be retargeted exactly once."""
    deepest = _graph_spec("Deepest", {"c": _ref(spec=_leaf("a>b>c"))})
    document = _graph_spec("Root", {
        "a/b/c": _ref(spec=_leaf("a/b/c")),
        "a": _ref(spec=_graph_spec("A", {"b/c": _ref(spec=_leaf("a>b/c")), "b": _ref(spec=deepest)})),
        "a/b": _ref(spec=_graph_spec("AB", {"c": _ref(spec=_leaf("a/b>c"))})),
    })
    source = _write(tmp_path / "in.yaml", document)

    result = decompile_pipeline_file(source, tmp_path / "gen.py")

    assert result.verified
    generated = (tmp_path / "gen.py").read_text(encoding="utf-8") + (tmp_path / "gen_subgraphs.py").read_text(encoding="utf-8")
    fragments = re.findall(r"resolve://\./gen\.leaves\.yaml#([^'\"]+)", generated)
    assert len(fragments) == len(set(fragments)) == 4, "every leaf once, none duplicated or omitted"
    assert ".components.yaml" not in generated
    manifest = parse_yaml_string((tmp_path / "gen.leaves.yaml").read_text(encoding="utf-8"))
    assert set(fragments) == set(manifest)


# ---------------------------------------------------------------------------
# Python components: a static source transform; Tangle compiles them later

_GREET = '''import pathlib
pathlib.Path(__file__).with_name("SENTINEL").write_text("imported")


def greet(name: str, shout: bool = False):
    """Greet someone.

    Metadata:
        Name: Greet
        Version: 1.0.0
    """
    print(name.upper() if shout else name)
'''


def _python_leaf(tmp_path: Path, source: str = _GREET, *, dependencies: tuple[str, ...] = ()) -> dict[str, Any]:
    """A leaf made by the real Python component generator (which imports the
    source -- this is the test authoring it, never the decompiler)."""
    from tangle_cli.component_generator import regenerate_yaml

    authoring = tmp_path / "authoring"
    authoring.mkdir(exist_ok=True)
    (authoring / "greet_task.py").write_text(source, encoding="utf-8")
    deps = authoring / "deps.toml"
    deps.write_text("[project]\ndependencies = [" + ", ".join(f'"{d}"' for d in dependencies) + "]\n", encoding="utf-8")
    assert regenerate_yaml(
        python_file=authoring / "greet_task.py", output_path=authoring / "greet.yaml", image="python:3.12", function_name="greet", dependencies_from=deps
    )
    leaf = parse_yaml_string((authoring / "greet.yaml").read_text(encoding="utf-8"))
    shutil.rmtree(authoring)
    return leaf


def _provenance_free(spec: dict[str, Any]) -> dict[str, Any]:
    """What Tangle must rebuild exactly: everything but generator provenance."""
    from tangle_cli.pipeline_decompiler import _GENERATOR_ANNOTATIONS

    provenance = _GENERATOR_ANNOTATIONS - {"python_original_code", "python_dependencies", "tangle_cli_generation_function_name", "tangle_cli_generation_mode"}
    copied = copy.deepcopy(spec)
    annotations = copied["metadata"]["annotations"]
    for key in provenance & set(annotations) - {"cloud_pipelines.net", "components new regenerate python-function-component"}:
        del annotations[key]
    return copied


def test_python_components_become_tasks_without_running_their_source(tmp_path: Path) -> None:
    """Root and a child share one recovered task (renamed away from the child
    function ``greet``); a plain leaf stays pinned YAML. Nothing recovered runs
    until Tangle compiles, after which the rebuilt specs match but for
    provenance."""
    leaf = _python_leaf(tmp_path, dependencies=("requests==2.31.0", "demo @ https://example.invalid/\U0001F600/demo.whl"))
    plain = _leaf("plain")
    child = _graph_spec("Greet", {"inner": {**_ref(spec=leaf), "arguments": {"name": "b"}}})
    document = _graph_spec("Root", {
        "a": {**_ref(spec=leaf), "arguments": {"name": "a", "shout": "true"}},
        "c": _ref(spec=child),
        "p": _ref(spec=plain),
    })
    work = tmp_path / "work"

    result = decompile_pipeline_file(_write(tmp_path / "in.yaml", document), work / "gen.py")

    assert result.verified and (result.python_tasks, result.python_candidates) == (1, 1)
    assert not list(tmp_path.rglob("SENTINEL")), "decompile never imports recovered source"
    package = work / "gen_tasks"
    assert result.tasks_package == package
    assert (package / "greet_task" / "greet_task.py").read_bytes() == leaf["metadata"]["annotations"]["python_original_code"].encode("utf-8")
    assert "requests==2.31.0" in (package / "greet_task" / "greet_task.deps.toml").read_text(encoding="utf-8")
    assert _functions(work / "gen_subgraphs.py") == ["greet"]
    root_source, companion_source = (work / "gen.py").read_text(encoding="utf-8"), (work / "gen_subgraphs.py").read_text(encoding="utf-8")
    (task_name,) = re.findall(r"from gen_tasks import (\w+)", root_source)
    assert task_name != "greet" and f"from gen_tasks import {task_name}" in companion_source
    manifest = parse_yaml_string((work / "gen.leaves.yaml").read_text(encoding="utf-8"))
    assert len(manifest) == 1 and root_source.count("resolve://./gen.leaves.yaml#") == 1, "only the plain leaf is pinned"
    assert len(list((work / "gen.leaves").iterdir())) == 1, "the converted leaf's local copy is pruned"

    moved = tmp_path / "moved"
    shutil.move(str(work), moved)
    compile_pipeline(moved / "gen.py", moved / "gen.yaml")
    assert (moved / "gen_tasks" / "greet_task" / "SENTINEL").is_file(), "Tangle's compile is what imports it"
    # Hydrate runs it too, so it needs explicit trust outside the cwd tree.
    hydrated = PipelineHydrator(client=_UnreachableLibrary(), trusted_python_sources=[moved]).hydrate_file(moved / "gen.yaml").data
    tasks = hydrated["implementation"]["graph"]["tasks"]
    for spec in (tasks["a"]["componentRef"]["spec"], tasks["c"]["componentRef"]["spec"]["implementation"]["graph"]["tasks"]["inner"]["componentRef"]["spec"]):
        assert _provenance_free(spec) == _provenance_free(leaf)
    assert tasks["a"]["arguments"] == {"name": "a", "shout": "true"}
    assert tasks["p"]["componentRef"]["spec"] == plain


def test_a_sidecar_like_argument_is_data_not_a_reference(tmp_path: Path) -> None:
    """Only generated ref(url=...) locators are checked after pruning; the same
    text as an ordinary argument value is user data."""
    leaf = _python_leaf(tmp_path)
    data = "resolve://./gen.leaves.yaml#business-data"
    source = _write(tmp_path / "in.yaml", _graph_spec("Root", {"a": {**_ref(spec=leaf), "arguments": {"name": data}}}))

    result = decompile_pipeline_file(source, tmp_path / "gen.py")

    assert result.verified and result.python_tasks == 1
    assert repr(data) in (tmp_path / "gen.py").read_text(encoding="utf-8")


@pytest.mark.parametrize("dependency", ["plain==1.0", "astral \U0001F600", 'quote " and back\\slash', "control \x01 and del \x7f", "é BMP"])
def test_dependencies_round_trip_through_toml(dependency: str) -> None:
    from tangle_cli.component_from_func import tomllib
    from tangle_cli.pipeline_decompiler import _dependencies_toml

    assert tomllib.loads(_dependencies_toml([dependency, "second"]))["project"]["dependencies"] == [dependency, "second"]


def test_no_python_tasks_keeps_every_leaf_pinned_and_removes_a_stale_package(tmp_path: Path) -> None:
    leaf = _python_leaf(tmp_path)
    source = _write(tmp_path / "in.yaml", _graph_spec("Root", {"a": _ref(spec=leaf)}))
    decompile_pipeline_file(source, tmp_path / "gen.py")
    assert (tmp_path / "gen_tasks").is_dir()

    result = decompile_pipeline_file(source, tmp_path / "gen.py", python_tasks=False)

    assert result.verified and (result.python_tasks, result.python_candidates, result.tasks_package) == (0, 1, None)
    assert not (tmp_path / "gen_tasks").exists() and "gen_tasks" not in (tmp_path / "gen.py").read_text(encoding="utf-8")
    assert len(parse_yaml_string((tmp_path / "gen.leaves.yaml").read_text(encoding="utf-8"))) == 1

    with pytest.raises(DecompileError) as excinfo:
        decompile_pipeline_file(source, tmp_path / "foo-bar.py")
    assert excinfo.value.code == "unsupported-output-name"
    assert decompile_pipeline_file(source, tmp_path / "foo-bar.py", python_tasks=False).verified
    assert not list(tmp_path.rglob("SENTINEL"))


def _unprovable(case: str, leaf: dict[str, Any]) -> dict[str, Any]:
    leaf = copy.deepcopy(leaf)
    annotations = leaf["metadata"]["annotations"]
    code = annotations["python_original_code"]
    if case == "decorated":
        annotations["python_original_code"] = code.replace("def greet(", "@staticmethod\ndef greet(")
    elif case == "bundle-mode":
        annotations["tangle_cli_generation_mode"] = "bundle"
    elif case == "custom-name":
        leaf["name"] = "Renamed"
    elif case == "custom-annotation":
        annotations["owner"] = "someone"
    elif case == "synthetic-input":
        leaf["inputs"].append({"name": "unwrapped_field", "type": "String"})
    elif case == "missing-marker":
        del annotations["components new regenerate python-function-component"]
    elif case == "resolve-root":
        annotations["tangle_cli_generation_resolve_root"] = "src"
    elif case == "unparseable":
        annotations["python_original_code"] = code + "\ndef broken(:\n"
    elif case == "rebound":
        annotations["python_original_code"] = code + "\ngreet = print\n"
    elif case == "conditional-import":
        annotations["python_original_code"] = code + '\nif __name__.startswith("gen_tasks."):\n    import math as greet\n'
    elif case == "try-def":
        annotations["python_original_code"] = code + "\ntry:\n    pass\nexcept ImportError:\n    def greet(name):\n        pass\n"
    elif case == "walrus":
        annotations["python_original_code"] = code + "\nif (greet := print):\n    pass\n"
    elif case == "class-bases":
        annotations["python_original_code"] = code + '\nclass Shadow((greet := str) if __name__.startswith("gen_tasks.") else object):\n    pass\n'
    elif case == "def-annotation":
        annotations["python_original_code"] = code + '\ndef helper(x: ((greet := str) if __name__.startswith("gen_tasks.") else str)):\n    pass\n'
    elif case == "match-guard":
        annotations["python_original_code"] = code + '\nmatch __name__.startswith("gen_tasks."):\n    case True if (greet := str):\n        pass\n'
    elif case == "except-type":
        annotations["python_original_code"] = code + '\ntry:\n    raise Exception\nexcept (greet := Exception):\n    pass\n'
    elif case == "star-import":
        annotations["python_original_code"] = code + '\nif __name__.startswith("gen_tasks."):\n    from math import *\n'
    elif case == "globals":
        annotations["python_original_code"] = code + '\nglobals()["greet"] = print\n'
    elif case == "container-env":
        leaf["implementation"]["container"]["env"] = {"IMPORTANT_SETTING": "required-value"}
    elif case == "input-annotations":
        leaf["inputs"][0]["annotations"] = {"editor.position": "1"}
    elif case == "async":
        annotations["python_original_code"] = code.replace("def greet(", "async def greet(")
    else:
        raise AssertionError(case)
    return leaf


@pytest.mark.parametrize("case", [
    "decorated", "bundle-mode", "custom-name", "custom-annotation", "synthetic-input",
    "missing-marker", "resolve-root", "unparseable", "rebound", "async",
    "conditional-import", "try-def", "walrus", "globals", "container-env", "input-annotations",
    "class-bases", "def-annotation", "match-guard", "except-type", "star-import",
])
def test_python_leaves_the_static_contract_cannot_prove_stay_pinned_yaml(tmp_path: Path, case: str) -> None:
    leaf = _unprovable(case, _python_leaf(tmp_path))
    source = _write(tmp_path / "in.yaml", _graph_spec("Root", {"a": _ref(spec=leaf)}))

    result = decompile_pipeline_file(source, tmp_path / "gen.py")

    assert result.verified and result.python_tasks == 0 and result.tasks_package is None
    assert result.python_candidates == (0 if case == "missing-marker" else 1)
    assert not (tmp_path / "gen_tasks").exists() and not list(tmp_path.rglob("SENTINEL"))


# ---------------------------------------------------------------------------
# Decorated @task sources: literal image_id/unwrap, re-exported as they are

_SCORE = '''import pathlib
from typing import Dict

from cloud_pipelines.components import OutputPath
from tangle_cli.python_pipeline import task

pathlib.Path(__file__).with_name("SENTINEL").write_text("imported")


@task(image_id="scoring", unwrap="settings")
def score(settings: Dict[str, str], settings_path: OutputPath("Text")) -> str:
    """Score a batch.

    Metadata:
        Name: Score
        Version: 2.0.0
    """
    open(settings_path, "w").write(str(settings))
    return "ok"
'''
_SCORE_CALL = "score.named('Score')(settings={'alpha': '1', 'beta': '2', 'gamma-x': '3'})"


def _authored_task(tmp_path: Path, source: str = _SCORE, *, files: dict[str, str] | None = None, module: str = "score_task") -> dict[str, Any]:
    """A task body exactly as python_pipeline authoring produces it: compiled
    and hydrated by Tangle itself, which runs the source (the test authoring
    it -- never the decompiler)."""
    import sys

    authoring = tmp_path / f"authoring-{module}"
    authoring.mkdir()
    (authoring / f"{module}.py").write_text(source, encoding="utf-8")
    for name, text in (files or {}).items():
        (authoring / name).write_text(text, encoding="utf-8")
    (authoring / "pipe.py").write_text(
        f"from tangle_cli.python_pipeline import pipeline\nfrom {module} import score\n\n"
        f"@pipeline(name='Authored')\ndef authored() -> None:\n    {_SCORE_CALL}\n",
        encoding="utf-8",
    )
    sys.modules.pop(module, None)
    try:
        compile_pipeline(authoring / "pipe.py", authoring / "pipe.yaml", image_overrides={"scoring": "python:3.12"})
        hydrated = PipelineHydrator(trusted_python_sources=[str(authoring)]).hydrate_file(authoring / "pipe.yaml").data
    finally:
        sys.modules.pop(module, None)
    shutil.rmtree(authoring)
    body = hydrated["implementation"]["graph"]["tasks"]["Score"]
    return {"componentRef": {"spec": body["componentRef"]["spec"]}, "arguments": body["arguments"]}


def test_a_real_shape_image_id_unwrap_task_converts_without_running_its_source(tmp_path: Path) -> None:
    """The shape of the real marked leaf: @task(image_id=..., unwrap=...) from
    a typing.Dict parameter, all inputs synthetic, outputs from an OutputPath
    (named like the unwrap parameter) and the return. Shared by the
    root and a child named like it; re-exported verbatim; image_id left for
    the maintainer's compile to resolve."""
    import sys

    task = _authored_task(tmp_path)
    document = _graph_spec("Root", {"Score": task, "c": _ref(spec=_graph_spec("Score", {"Score": copy.deepcopy(task)}))})
    source = _write(tmp_path / "in.yaml", document)
    work = tmp_path / "work"

    result = decompile_pipeline_file(source, work / "gen.py")

    assert result.verified and (result.python_tasks, result.image_id_tasks, result.unwrap_tasks) == (1, 1, 1)
    assert not list(tmp_path.rglob("SENTINEL"))
    module = work / "gen_tasks" / "score_task"
    assert (module / "score_task.py").read_text(encoding="utf-8") == _SCORE, "byte-verbatim, decorator included"
    init = (work / "gen_tasks" / "__init__.py").read_text(encoding="utf-8")
    assert "task(" not in init.split('"""')[2], "re-exported, never wrapped a second time"
    (name,) = re.findall(r"from gen_tasks import (\w+)", (work / "gen.py").read_text(encoding="utf-8"))
    assert _functions(work / "gen_subgraphs.py") == ["score"] and name != "score", "one namespace with the child function"
    assert "settings={'alpha': '1', 'beta': '2', 'gamma-x': '3'}" in (work / "gen.py").read_text(encoding="utf-8")
    assert not (work / "gen.leaves.yaml").exists(), "nothing left to pin"

    moved = tmp_path / "moved"
    shutil.move(str(work), moved)
    for image in ("python:3.12", "python:3.13-slim"):
        sys.modules.pop("gen_tasks", None)
        compile_pipeline(moved / "gen.py", moved / "gen.yaml", image_overrides={"scoring": image})
        hydrated = PipelineHydrator(trusted_python_sources=[str(moved)]).hydrate_file(moved / "gen.yaml").data
        tasks = hydrated["implementation"]["graph"]["tasks"]
        rebuilt = tasks["Score"]["componentRef"]["spec"]
        assert rebuilt["implementation"]["container"]["image"] == image, "compile resolves image_id in ITS environment"
        if image == "python:3.12":
            assert _provenance_free(rebuilt) == _provenance_free(task["componentRef"]["spec"])
            assert tasks["Score"]["arguments"] == task["arguments"]
    sys.modules.pop("gen_tasks", None)
    assert (moved / "gen_tasks" / "score_task" / "SENTINEL").is_file()

    exact = decompile_pipeline_file(source, tmp_path / "exact" / "gen.py", python_tasks=False)
    assert exact.verified and exact.python_tasks == 0 and exact.python_candidates == 1


def _decorator_variant(case: str) -> str:
    replace = {
        "aliased": ("import task\n", "import task as t\n", "@task(", "@t("),
        "alias-swap": ("import task\n", "import task as real_task, ref as task\n"),
        "attribute": ("from tangle_cli.python_pipeline import task\n", "import tangle_cli.python_pipeline as pp\n", "@task(", "@pp.task("),
        "two-decorators": ("@task(", "@staticmethod\n@task("),
        "star-kwargs": ('unwrap="settings")', 'unwrap="settings", **{})'),
        "dynamic-value": ('image_id="scoring"', "image_id=IMAGE"),
        "unsupported-kwarg": ('unwrap="settings")', 'unwrap="settings", annotations={"a": "b"})'),
        "both-images": ('image_id="scoring"', 'image_id="scoring", image="python:3.12"'),
        "wrong-image": ('image_id="scoring"', 'image="python:3.11"'),
        "bundle-mode": ('unwrap="settings")', 'unwrap="settings", mode="bundle")'),
        "absolute-deps": ('unwrap="settings")', 'unwrap="settings", dependencies_from="/etc/deps.toml")'),
        "parent-deps": ('unwrap="settings")', 'unwrap="settings", dependencies_from="../deps.toml")'),
        "py-directory-deps": ('unwrap="settings")', 'unwrap="settings", dependencies_from="__init__.py/deps.toml")'),
        "py-directory-deps-case": ('unwrap="settings")', 'unwrap="settings", dependencies_from="__INIT__.PY/deps.toml")'),
        "non-toml-deps": ('unwrap="settings")', 'unwrap="settings", dependencies_from="requirements.txt")'),
        "not-a-dict": ("settings: Dict[str, str]", "settings: list"),
        "dict-from-elsewhere": ("from typing import Dict\n", "from collections import OrderedDict as Dict\n"),
        "dict-rebound": ("\n\n@task(", "\nDict = list\n\n\n@task("),
        "rebound-task": ("\n\n@task(", "\ntask = task\n\n\n@task("),
    }[case]
    source = _SCORE
    for old, new in zip(replace[::2], replace[1::2]):
        assert old in source, case
        source = source.replace(old, new, 1)
    return source


@pytest.mark.parametrize("case", [
    "aliased", "alias-swap", "attribute", "two-decorators", "star-kwargs", "dynamic-value", "unsupported-kwarg", "both-images",
    "wrong-image", "bundle-mode", "absolute-deps", "parent-deps", "py-directory-deps", "py-directory-deps-case", "non-toml-deps", "not-a-dict", "rebound-task",
    "dict-from-elsewhere", "dict-rebound",
])
def test_a_decorator_the_static_contract_cannot_prove_stays_pinned_yaml(tmp_path: Path, case: str) -> None:
    """Only the recorded source changes: it is all the decompiler reads."""
    task = _authored_task(tmp_path)
    task["componentRef"]["spec"]["metadata"]["annotations"]["python_original_code"] = _decorator_variant(case)
    source = _write(tmp_path / "in.yaml", _graph_spec("Root", {"Score": task}))

    result = decompile_pipeline_file(source, tmp_path / "gen.py")

    assert result.verified and (result.python_tasks, result.python_candidates) == (0, 1)
    assert not (tmp_path / "gen_tasks").exists() and not list(tmp_path.rglob("SENTINEL"))


@pytest.mark.parametrize("case", ["missing-synthetic", "extra-synthetic", "bare-unwrap", "one-call-site-of-two"])
def test_unwrap_call_sites_that_would_compile_differently_stay_yaml(tmp_path: Path, case: str) -> None:
    task = _authored_task(tmp_path)
    other = copy.deepcopy(task)
    arguments = (other if case == "one-call-site-of-two" else task)["arguments"]
    if case in ("missing-synthetic", "one-call-site-of-two"):
        del arguments["settings__beta"]
    elif case == "extra-synthetic":
        arguments["settings__zeta"] = "9"
    else:
        arguments["settings"] = "{}"
    tasks = {"Score": task, **({"Other": other} if case == "one-call-site-of-two" else {})}

    source = _write(tmp_path / "in.yaml", _graph_spec("Root", tasks))
    result = decompile_pipeline_file(source, tmp_path / "gen.py")

    assert result.verified and result.python_tasks == 0 and not (tmp_path / "gen_tasks").exists()
    # Nothing converts, so no package name is needed either.
    assert decompile_pipeline_file(source, tmp_path / "foo-bar.py").verified


def test_decorated_sources_with_the_same_relative_dependencies_do_not_collide(tmp_path: Path) -> None:
    """Each module has its own directory, so two ``dependencies_from=
    "pyproject.toml"`` keep their own recorded lists."""
    import sys

    tasks = {}
    for marker, dependency in (("first", "alpha==1"), ("second", "beta==2")):
        variant = _SCORE.replace('unwrap="settings")', 'unwrap="settings", dependencies_from="pyproject.toml")').replace("Score a batch.", f"Score a {marker} batch.")
        task = _authored_task(tmp_path, variant, files={"pyproject.toml": f'[project]\ndependencies = ["{dependency}"]\n'}, module=f"score_{marker}")
        tasks[marker] = task

    result = decompile_pipeline_file(_write(tmp_path / "in.yaml", _graph_spec("Root", tasks)), tmp_path / "work" / "gen.py")

    assert result.verified and result.python_tasks == 2
    lists = sorted((p.parent.name, p.read_text(encoding="utf-8")) for p in (tmp_path / "work" / "gen_tasks").rglob("pyproject.toml"))
    assert [name for name, _ in lists] == ["score_first", "score_second"]
    assert "alpha==1" in lists[0][1] and "beta==2" in lists[1][1]
    sys.modules.pop("gen_tasks", None)
    compile_pipeline(tmp_path / "work" / "gen.py", tmp_path / "work" / "gen.yaml", image_overrides={"scoring": "python:3.12"})
    sys.modules.pop("gen_tasks", None)
    hydrated = PipelineHydrator(trusted_python_sources=[str(tmp_path / "work")]).hydrate_file(tmp_path / "work" / "gen.yaml").data
    for marker in ("first", "second"):
        rebuilt = hydrated["implementation"]["graph"]["tasks"][marker]["componentRef"]["spec"]
        assert _provenance_free(rebuilt) == _provenance_free(tasks[marker]["componentRef"]["spec"])


def test_a_decorated_source_without_dependencies_from_keeps_its_recorded_list(tmp_path: Path) -> None:
    """Its dependencies were discovered beside the source; ``<module>.toml`` is
    what discovery finds first, so a maintainer's own pyproject.toml further up
    cannot replace the recorded list."""
    import sys

    task = _authored_task(tmp_path, files={"pyproject.toml": '[project]\ndependencies = ["recorded==1"]\n'})
    assert "recorded==1" in task["componentRef"]["spec"]["metadata"]["annotations"]["python_dependencies"]
    work = tmp_path / "work"
    work.mkdir()
    (work / "pyproject.toml").write_text('[project]\ndependencies = ["decoy==9"]\n', encoding="utf-8")

    assert decompile_pipeline_file(_write(tmp_path / "in.yaml", _graph_spec("Root", {"Score": task})), work / "gen.py").python_tasks == 1

    sys.modules.pop("gen_tasks", None)
    compile_pipeline(work / "gen.py", work / "gen.yaml", image_overrides={"scoring": "python:3.12"})
    sys.modules.pop("gen_tasks", None)
    hydrated = PipelineHydrator(trusted_python_sources=[str(work)]).hydrate_file(work / "gen.yaml").data
    rebuilt = hydrated["implementation"]["graph"]["tasks"]["Score"]["componentRef"]["spec"]
    assert _provenance_free(rebuilt) == _provenance_free(task["componentRef"]["spec"])


def test_task_and_subpackage_names_never_shadow_each_other(tmp_path: Path) -> None:
    """Function ``foo`` in ``bar.py`` and ``bar`` in ``foo.py``: importing a
    subpackage binds its name on the package, so neither may also be a task
    exported under that name."""
    import sys

    tasks = {}
    for function, module in (("foo", "bar"), ("bar", "foo")):
        source = _SCORE.replace("def score(", f"def {function}(") + f"\n\nscore = {function}\n"
        tasks[module] = _authored_task(tmp_path, source, module=module)
    work = tmp_path / "work"

    result = decompile_pipeline_file(_write(tmp_path / "in.yaml", _graph_spec("Root", tasks)), work / "gen.py")

    assert result.verified and result.python_tasks == 2
    subpackages = {p.name for p in (work / "gen_tasks").iterdir() if p.is_dir()}
    exported = set(re.findall(r"from gen_tasks import (.+)", (work / "gen.py").read_text(encoding="utf-8"))[0].split(", "))
    assert len(subpackages) == 2 and not subpackages & exported
    sys.modules.pop("gen_tasks", None)
    compile_pipeline(work / "gen.py", work / "gen.yaml", image_overrides={"scoring": "python:3.12"})
    sys.modules.pop("gen_tasks", None)


# ---------------------------------------------------------------------------
# Decompile never runs local Python, not even to resolve its input


@pytest.mark.parametrize("trust", ["cwd", "trusted-source"])
def test_a_local_from_python_input_is_refused_without_running_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, trust: str) -> None:
    """A compiled @task pipeline resolves through local_from_python, which runs
    the source. Neither cwd trust nor an explicit trusted source makes
    decompile do that; the previous output is untouched."""
    import sys

    from tangle_cli.hydration_trust import register_trusted_python_source

    authoring = tmp_path / "authoring"
    authoring.mkdir()
    (authoring / "score_task.py").write_text(_SCORE, encoding="utf-8")
    (authoring / "pipe.py").write_text(
        f"from tangle_cli.python_pipeline import pipeline\nfrom score_task import score\n\n@pipeline(name='Authored')\ndef authored() -> None:\n    {_SCORE_CALL}\n",
        encoding="utf-8",
    )
    sys.modules.pop("score_task", None)
    compile_pipeline(authoring / "pipe.py", authoring / "pipe.yaml", image_overrides={"scoring": "python:3.12"})
    sys.modules.pop("score_task", None)
    (authoring / "SENTINEL").unlink()
    if trust == "cwd":
        monkeypatch.chdir(authoring)
    else:
        monkeypatch.setattr("tangle_cli.hydration_trust._TRUSTED_PYTHON_SOURCES", [])
        register_trusted_python_source(authoring)
    output = tmp_path / "out" / "gen.py"
    output.parent.mkdir()
    output.write_text("# previous\n", encoding="utf-8")

    with pytest.raises(DecompileError) as excinfo:
        decompile_pipeline_file(authoring / "pipe.yaml", output, client=_UnreachableLibrary())

    assert excinfo.value.code == "unsupported-local-python-source"
    assert "tangle sdk pipelines hydrate" in str(excinfo.value) and "score" not in str(excinfo.value)
    assert not (authoring / "SENTINEL").exists(), "the source never ran"
    assert output.read_text(encoding="utf-8") == "# previous\n"
    assert not list(output.parent.glob(".decompile-*"))


def test_hydrating_first_then_decompiling_recovers_the_task_statically(tmp_path: Path) -> None:
    """The documented path: the explicit, trust-gated hydrate runs the source;
    decompile then recovers the task from the hydrated YAML without running it."""
    task = _authored_task(tmp_path)  # compiled AND hydrated by Tangle
    source = _write(tmp_path / "hydrated.yaml", _graph_spec("Root", {"Score": task}))

    result = decompile_pipeline_file(source, tmp_path / "gen.py")

    assert result.verified and result.python_tasks == 1 and not list(tmp_path.rglob("SENTINEL"))


def test_a_local_from_python_entry_is_refused_even_behind_a_fallback(tmp_path: Path) -> None:
    """A fallback_on_error entry would swallow the refusal and fall through to
    a copy; the input still names local Python, so it is still refused."""
    (tmp_path / "score_task.py").write_text(_SCORE, encoding="utf-8")
    _write(tmp_path / "leaf.yaml", _leaf("copy"))
    _write(tmp_path / "c.yaml", {"f": [
        {"local_from_python": {"file": "score_task.py", "function": "score", "image": "python:3.12"}, "fallback_on_error": True},
        {"local": "./leaf.yaml"},
    ]})
    source = _write(tmp_path / "in.yaml", _graph_spec("Root", {"a": _ref(url="resolve://./c.yaml#f")}))

    with pytest.raises(DecompileError) as excinfo:
        decompile_pipeline_file(source, tmp_path / "out" / "gen.py", client=_UnreachableLibrary())

    assert excinfo.value.code == "unsupported-local-python-source"
    assert not list(tmp_path.rglob("SENTINEL"))


_EXECUTABLE_KINDS = ["local_from_docker", "local_from_container", "from_docker", "from_container", "from-docker", "from-container", "downstream_build"]


@pytest.mark.parametrize("fallback", [False, True], ids=["primary", "behind-fallback"])
@pytest.mark.parametrize("kind", _EXECUTABLE_KINDS)
def test_every_non_static_resolver_kind_is_refused_without_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, fallback: bool
) -> None:
    """As a downstream package would register them: a resolver that runs code.
    Decompile refuses each one -- even behind a fallback -- and never calls it."""
    import tangle_cli.pipeline_hydrator as hydrator_module

    ran: list[str] = []
    monkeypatch.setitem(hydrator_module.COMPONENT_RESOLVERS, kind, lambda *a: ran.append(kind) or ("d" * 64, _leaf("built")))
    _write(tmp_path / "leaf.yaml", _leaf("copy"))
    entries = [{kind: {"image": "example"}, **({"fallback_on_error": True} if fallback else {})}, *([{"local": "./leaf.yaml"}] if fallback else [])]
    _write(tmp_path / "c.yaml", {"f": entries})
    source = _write(tmp_path / "in.yaml", _graph_spec("Root", {"a": _ref(url="resolve://./c.yaml#f")}))
    output = tmp_path / "out" / "gen.py"
    output.parent.mkdir()
    output.write_text("# previous\n", encoding="utf-8")

    with pytest.raises(DecompileError) as excinfo:
        decompile_pipeline_file(source, output, client=_UnreachableLibrary())

    assert excinfo.value.code == "unsupported-executable-component-source"
    assert "tangle sdk pipelines hydrate" in str(excinfo.value) and "example" not in str(excinfo.value)
    assert not ran, "the resolver never ran"
    assert output.read_text(encoding="utf-8") == "# previous\n" and not list(output.parent.glob(".decompile-*"))


def test_a_downstream_override_of_a_static_kind_is_not_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Allowed kinds are pinned to the built-in readers, so re-registering one
    cannot swap code into decompile."""
    import tangle_cli.pipeline_hydrator as hydrator_module

    ran: list[str] = []
    for kind in ("file", "url", "local", "resolve"):
        monkeypatch.setitem(hydrator_module.COMPONENT_RESOLVERS, kind, lambda *a, k=kind: ran.append(k) or None)

    assert decompile_pipeline_file(_local_leaf_source(tmp_path), tmp_path / "gen.py").verified
    assert not ran


def _template_leaf(directory: Path) -> Path:
    """A template_file config whose Jinja reaches Python builtins."""
    sentinel = directory / "SENTINEL"
    (directory / "leaf.j2").write_text(
        "# {{ cycler.__init__.__globals__.__builtins__.open(sentinel, 'w').write('ran') }}\n"
        + dump_yaml(_leaf("templated")),
        encoding="utf-8",
    )
    return _write(directory / "leaf.yaml", {"template_file": "leaf.j2", "sentinel": str(sentinel)})


@pytest.mark.parametrize("route", ["file-ref", "sidecar-local", "behind-fallback"])
def test_a_template_component_is_refused_without_rendering(tmp_path: Path, route: str) -> None:
    _template_leaf(tmp_path)
    if route == "file-ref":
        ref = _ref(url="file://./leaf.yaml")
    else:
        _write(tmp_path / "plain.yaml", _leaf("plain"))
        entries = [{"local": "./leaf.yaml"}] if route == "sidecar-local" else [{"local": "./leaf.yaml", "fallback_on_error": True}, {"local": "./plain.yaml"}]
        _write(tmp_path / "c.yaml", {"f": entries})
        ref = _ref(url="resolve://./c.yaml#f")
    source = _write(tmp_path / "in.yaml", _graph_spec("Root", {"a": ref}))
    output = tmp_path / "out" / "gen.py"
    output.parent.mkdir()
    output.write_text("# previous\n", encoding="utf-8")

    with pytest.raises(DecompileError) as excinfo:
        decompile_pipeline_file(source, output, client=_UnreachableLibrary())

    assert excinfo.value.code == "unsupported-template-component-source"
    assert not (tmp_path / "SENTINEL").exists(), "the template never rendered"
    assert output.read_text(encoding="utf-8") == "# previous\n"


def test_hydrating_a_template_first_then_decompiling_succeeds(tmp_path: Path) -> None:
    """The explicit hydrate renders it; decompile then reads plain YAML."""
    _template_leaf(tmp_path)
    source = _write(tmp_path / "in.yaml", _graph_spec("Root", {"a": _ref(url="file://./leaf.yaml")}))
    hydrated = PipelineHydrator(client=_UnreachableLibrary()).hydrate_file(source).data
    (tmp_path / "SENTINEL").unlink()

    result = decompile_pipeline_file(_write(tmp_path / "hydrated.yaml", hydrated), tmp_path / "gen.py", client=_UnreachableLibrary())

    assert result.verified and not (tmp_path / "SENTINEL").exists()


@pytest.mark.parametrize("fallback", [False, True], ids=["primary", "behind-fallback"])
def test_a_downstream_uri_reader_is_refused_without_running(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fallback: bool) -> None:
    """URI schemes follow the same allowlist as resolver kinds: a reader only
    a downstream package registered is never called."""
    import tangle_cli.pipeline_hydrator as hydrator_module

    ran: list[str] = []
    monkeypatch.setitem(hydrator_module.URI_READERS, "downstream-build", lambda *a: ran.append("read") or dump_yaml(_leaf("built")))
    _write(tmp_path / "plain.yaml", _leaf("plain"))
    entry = {"url": "downstream-build://./leaf.yaml", **({"fallback_on_error": True} if fallback else {})}
    _write(tmp_path / "c.yaml", {"f": [entry, *([{"local": "./plain.yaml"}] if fallback else [])]})
    source = _write(tmp_path / "in.yaml", _graph_spec("Root", {"a": _ref(url="resolve://./c.yaml#f")}))

    with pytest.raises(DecompileError) as excinfo:
        decompile_pipeline_file(source, tmp_path / "out" / "gen.py", client=_UnreachableLibrary())

    assert excinfo.value.code == "unsupported-executable-component-source" and not ran


def test_a_downstream_override_of_the_https_reader_is_not_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """https stays the built-in reader (faked here, so nothing goes on the network)
    for a remote resolve config."""
    import tangle_cli.pipeline_hydrator as hydrator_module

    ran: list[str] = []
    leaf = _write(tmp_path / "leaf.yaml", _leaf("fetched"))
    manifest = dump_yaml({"f": [{"url": f"file://{leaf}"}]})
    monkeypatch.setattr(hydrator_module, "_read_http_uri", lambda *a: manifest)
    monkeypatch.setitem(hydrator_module.URI_READERS, "https", lambda *a: ran.append("override") or manifest)
    source = _write(tmp_path / "in.yaml", _graph_spec("Root", {"a": _ref(url="resolve://https://example.invalid/c.yaml#f")}))

    result = decompile_pipeline_file(source, tmp_path / "gen.py", client=_UnreachableLibrary())

    assert result.verified and not ran
    assert "fetched" in "".join(p.read_text(encoding="utf-8") for p in (tmp_path / "gen.leaves").rglob("*.yaml"))


# ---------------------------------------------------------------------------
# editor.position: .with_position() only when it reproduces the exact string


@pytest.mark.parametrize(("annotations", "expected"), [
    pytest.param({"editor.position": '{"x": 10, "y": 20}'}, ".named('T').with_position(10, 20)(", id="position-only"),
    pytest.param({"editor.position": '{"x": -5, "y": 2.5}'}, ".with_position(-5, 2.5)(", id="negative-and-float"),
    pytest.param(
        {"editor.position": '{"x": 1, "y": 2, "width": 300, "height": 80}'},
        ".with_position(1, 2, width=300, height=80)(",
        id="width-height",
    ),
    pytest.param({"editor.position": '{"x": 1e+20, "y": 2}'}, ".with_position(1e+20, 2)(", id="canonical-exponent"),
    pytest.param(
        {"owner": "me", "editor.position": '{"x": 1, "y": 2}', "z": "last"},
        ".with_annotations({'owner': 'me', 'z': 'last'}).with_position(1, 2)(",
        id="mixed-position-last",
    ),
])
def test_an_exactly_reproducible_position_becomes_with_position(annotations: dict[str, Any], expected: str) -> None:
    result = decompile_pipeline(_doc({"T": {"annotations": annotations, **_ref(name="c")}}))

    assert result.verified and expected in result.source and "editor.position" not in result.source


@pytest.mark.parametrize("value", [
    pytest.param('{"x":10,"y":20}', id="compact"),
    pytest.param('{"y": 2, "x": 1}', id="reordered"),
    pytest.param('{"x": 1, "y": 2, "z": 3}', id="extra-key"),
    pytest.param('{"x": 1}', id="missing-y"),
    pytest.param('{"x": NaN, "y": 2}', id="non-finite"),
    pytest.param('{"x": true, "y": 2}', id="boolean"),
    pytest.param('{"x": 1, "y": 2, "width": null}', id="null-width"),
    pytest.param('{"x": 1, "x": 3, "y": 2}', id="duplicate-key"),
    pytest.param('{"x": 1e3, "y": 2}', id="non-canonical-exponent"),
    pytest.param('{"x": ' + "1" * 400 + ', "y": 2}', id="int-overflows-float"),
    pytest.param('{"x": 1, "y": 2, "width": ' + "9" * 400 + "}", id="width-overflows-float"),
    pytest.param("[" * 5000 + "]" * 5000, id="too-deep"),
    pytest.param("not json", id="malformed"),
    pytest.param("[1, 2]", id="not-an-object"),
])
def test_a_position_with_position_would_rewrite_stays_verbatim(value: str) -> None:
    result = decompile_pipeline(_doc({"T": {"annotations": {"editor.position": value, "k": "v"}, **_ref(name="c")}}))

    assert result.verified and "with_position" not in result.source
    assert "'k': 'v'" in result.source
    assert any(isinstance(n, ast.Constant) and n.value == value for n in ast.walk(ast.parse(result.source))), "kept verbatim"


def test_a_subpipeline_parent_card_and_its_child_internals_position_separately(tmp_path: Path) -> None:
    child = _graph_spec("Child", {"leaf": {"annotations": {"editor.position": '{"x": 7, "y": 8}'}, **_ref(spec=_leaf("c"))}})
    document = _graph_spec("Root", {"n": {"annotations": {"editor.position": '{"x": 1, "y": 2}'}, **_ref(spec=child)}})

    result = decompile_pipeline_file(_write(tmp_path / "in.yaml", document), tmp_path / "gen.py")

    assert result.verified
    assert "subpipeline(child).named('n').with_position(1, 2)(" in (tmp_path / "gen.py").read_text(encoding="utf-8")
    assert ".named('leaf').with_position(7, 8)(" in (tmp_path / "gen_subgraphs.py").read_text(encoding="utf-8")


def test_a_recovered_python_task_takes_its_position_too(tmp_path: Path) -> None:
    task = {"annotations": {"editor.position": '{"x": 3, "y": 4}'}, **_ref(spec=_python_leaf(tmp_path)), "arguments": {"name": "a"}}

    result = decompile_pipeline_file(_write(tmp_path / "in.yaml", _graph_spec("Root", {"g": task})), tmp_path / "gen.py")

    assert result.verified and result.python_tasks == 1
    assert re.search(r"= \w+\.named\('g'\)\.with_position\(3, 4\)\(name='a'\)", (tmp_path / "gen.py").read_text(encoding="utf-8"))


def _io_doc(annotations: dict[str, Any]) -> dict[str, Any]:
    return _doc(
        {"T": _ref(name="c")},
        inputs=[{"name": "In", "type": "String", "annotations": dict(annotations)}],
        outputs=[{"name": "Out", "type": "String", "annotations": dict(annotations)}],
        graph={"outputValues": {"Out": {"graphInput": {"inputName": "In"}}}},
    )


@pytest.mark.parametrize(("annotations", "expected"), [
    pytest.param({"editor.position": '{"x": 10, "y": 20}'}, "position=(10, 20))", id="position-only"),
    pytest.param({"editor.position": '{"x": -5, "y": 2.5}'}, "position=(-5, 2.5))", id="negative-and-float"),
    pytest.param({"note": "n", "editor.position": '{"x": 1, "y": 2}'}, "annotations={'note': 'n'}, position=(1, 2))", id="mixed"),
])
def test_an_exact_x_y_graph_io_position_becomes_position_kwarg(annotations: dict[str, Any], expected: str) -> None:
    result = decompile_pipeline(_io_doc(annotations))

    assert result.verified and result.source.count(expected) == 2, "both graph_input and graph_output"
    assert "editor.position" not in result.source


@pytest.mark.parametrize("value", [
    pytest.param('{"x": 1, "y": 2, "width": 300, "height": 80}', id="width-height-unsupported-by-position"),
    pytest.param('{"x":10,"y":20}', id="compact"),
    pytest.param('{"y": 2, "x": 1}', id="reordered"),
    pytest.param('{"x": ' + "1" * 400 + ', "y": 2}', id="overflow"),
    pytest.param("not json", id="malformed"),
])
def test_a_graph_io_position_position_cannot_reproduce_stays_verbatim(value: str) -> None:
    result = decompile_pipeline(_io_doc({"editor.position": value, "k": "v"}))

    assert result.verified and "position=" not in result.source
    assert sum(isinstance(n, ast.Constant) and n.value == value for n in ast.walk(ast.parse(result.source))) == 2
