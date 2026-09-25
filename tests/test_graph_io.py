"""``graph_input()`` / ``graph_output()`` — graph I/O declared from the body.

The signature/return-annotation route covers Python-shaped graphs only. These
functions exist for the shapes it cannot express, and the contract pinned here
is what a YAML→Python port needs:

* non-identifier names and exact Tangle type strings survive to the YAML;
* declaration order is signature parameters first, then body order;
* ``default`` implies ``optional: true`` unless ``optional=False``;
* a name may be declared once — including against ``In[...]`` parameters and
  the return annotation;
* constants are not graph outputs;
* diagnostics name the field, never the value.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from tangle_cli.pipeline_compiler import compile_pipeline
from tangle_cli.python_pipeline import graph_input, graph_output
from tangle_cli.python_pipeline.errors import (
    CompileError,
    InvalidEditorLayoutError,
    InvalidGraphIoError,
)

_HEADER = '''
from dataclasses import dataclass

from tangle_cli.python_pipeline import (
    In,
    Out,
    Outputs,
    graph_input,
    graph_output,
    pipeline,
    subpipeline,
    task,
)


@task(image="python:3.12")
def greet(greeting: str = "hi", limit: str = "0"):
    """Write a greeting.

    Metadata:
        Name: Greet
    """
    print(greeting, limit)

'''


def _compile(tmp_path: Path, body: str, case: str, *, pipeline_name=None) -> Path:
    """Compile ``_HEADER + body`` in its own directory; return the output."""
    case_dir = tmp_path / case
    case_dir.mkdir(parents=True, exist_ok=True)
    script = case_dir / "pipeline.py"
    script.write_text(_HEADER + textwrap.dedent(body), encoding="utf-8")
    out = case_dir / "compiled.yaml"
    compile_pipeline(script, out, pipeline_name=pipeline_name)
    return out


def _doc(out: Path) -> dict:
    return yaml.safe_load(out.read_text(encoding="utf-8"))


def _expect(tmp_path, body, case, error=InvalidGraphIoError, **kwargs):
    with pytest.raises(error) as exc:
        _compile(tmp_path, body, case, **kwargs)
    return str(exc.value)


# ---------------------------------------------------------------------------
# What the signature cannot express.


def test_a_non_identifier_name_and_exact_type_reach_the_document(tmp_path):
    """The reason this API exists: ``"Pipeline Creation Time"`` is not a legal
    parameter name, and ``Json`` is not a Python type."""
    out = _compile(
        tmp_path,
        '''
        @pipeline("P")
        def p() -> Out[str]:
            created = graph_input(
                "Pipeline Creation Time",
                "String",
                default="Use as a cache busting mechanism",
            )
            params = graph_input("template_params_override", "Json", optional=True)
            run_greet = greet(greeting=created, limit=params)
            return run_greet
        ''',
        "shapes",
    )

    assert _doc(out)["inputs"] == [
        {
            "name": "Pipeline Creation Time",
            "type": "String",
            "default": "Use as a cache busting mechanism",
            "optional": True,
        },
        {"name": "template_params_override", "type": "Json", "optional": True},
    ]


def test_signature_inputs_come_first_then_declaration_order(tmp_path):
    out = _compile(
        tmp_path,
        '''
        @pipeline("P")
        def p(from_signature: In[str] = "x") -> Out[str]:
            second = graph_input("second", "String")
            third = graph_input("third", "String")
            run_greet = greet(greeting=second, limit=third)
            return run_greet
        ''',
        "order",
    )

    assert [i["name"] for i in _doc(out)["inputs"]] == [
        "from_signature",
        "second",
        "third",
    ]


def test_a_conditional_input_declares_nothing_when_false(tmp_path):
    """``when=False`` keeps a conditional declaration a single assignment."""
    out = _compile(
        tmp_path,
        '''
        @pipeline("P")
        def p() -> Out[str]:
            absent = graph_input("absent", "String", when=False)
            assert absent is None
            run_greet = greet()
            graph_output("skipped", run_greet, "String", when=False)
            return run_greet
        ''',
        "when",
    )

    doc = _doc(out)
    assert "inputs" not in doc
    assert [o["name"] for o in doc["outputs"]] == ["wait_for_output"]


# ---------------------------------------------------------------------------
# optional / default.


def test_a_default_implies_optional_and_matches_the_signature_route(tmp_path):
    """A defaulted ``In[T]`` parameter emits ``default`` + ``optional: true``;
    the explicit route emits the same entry for the same shape."""
    declared = _compile(
        tmp_path,
        '''
        @pipeline("P")
        def p() -> Out[str]:
            value = graph_input("value", "String", default="x")
            run_greet = greet(greeting=value)
            return run_greet
        ''',
        "declared",
    )
    from_signature = _compile(
        tmp_path,
        '''
        @pipeline("P")
        def p(value: In[str] = "x") -> Out[str]:
            run_greet = greet(greeting=value)
            return run_greet
        ''',
        "signature",
    )

    assert _doc(declared)["inputs"] == _doc(from_signature)["inputs"]


def test_an_explicit_optional_false_survives_a_default(tmp_path):
    """The corpus contains defaulted-but-required inputs, so the implication
    has to be overridable."""
    out = _compile(
        tmp_path,
        '''
        @pipeline("P")
        def p() -> Out[str]:
            value = graph_input("value", "String", default="x", optional=False)
            run_greet = greet(greeting=value)
            return run_greet
        ''',
        "required_default",
    )

    assert _doc(out)["inputs"] == [
        {"name": "value", "type": "String", "default": "x", "optional": False}
    ]


def test_optional_alone_emits_no_default(tmp_path):
    out = _compile(
        tmp_path,
        '''
        @pipeline("P")
        def p() -> Out[str]:
            value = graph_input("value", "String", optional=True)
            run_greet = greet(greeting=value)
            return run_greet
        ''',
        "optional_only",
    )

    assert _doc(out)["inputs"] == [
        {"name": "value", "type": "String", "optional": True}
    ]


def test_a_scalar_default_is_rendered_as_the_schema_string(tmp_path):
    """``InputSpec.default`` is a string in the pipeline schema, so the value
    is rendered here rather than failing later against a schema path. Full
    rules and refusals live in ``test_signature_defaults.py``."""
    doc = _doc(_compile(
        tmp_path,
        '''
        @pipeline("P")
        def p() -> Out[str]:
            value = graph_input("value", "Integer", default=41)
            run_greet = greet(greeting=value)
            return run_greet
        ''',
        "scalar_default",
    ))

    assert doc["inputs"][0]["default"] == "41"


def test_an_unrenderable_default_is_refused_without_echoing_it(tmp_path):
    message = _expect(
        tmp_path,
        '''
        @pipeline("P")
        def p() -> Out[str]:
            value = graph_input("value", "Integer", default=4.1)
            run_greet = greet(greeting=value)
            return run_greet
        ''',
        "bad_default",
    )

    assert "default" in message and "float" in message
    assert "4.1" not in message


# ---------------------------------------------------------------------------
# Uniqueness.


def test_a_duplicate_input_name_is_refused(tmp_path):
    message = _expect(
        tmp_path,
        '''
        @pipeline("P")
        def p() -> Out[str]:
            first = graph_input("value", "String")
            second = graph_input("value", "String")
            run_greet = greet(greeting=first, limit=second)
            return run_greet
        ''',
        "dup_input",
    )

    assert "'value'" in message


def test_an_input_colliding_with_a_signature_parameter_is_refused(tmp_path):
    """Signature inputs are appended before the body runs, so the collision is
    visible at declaration time."""
    message = _expect(
        tmp_path,
        '''
        @pipeline("P")
        def p(value: In[str] = "x") -> Out[str]:
            shadow = graph_input("value", "String")
            run_greet = greet(greeting=shadow)
            return run_greet
        ''',
        "dup_signature",
    )

    assert "'value'" in message


def test_a_duplicate_output_name_is_refused(tmp_path):
    message = _expect(
        tmp_path,
        '''
        @pipeline("P")
        def p() -> Out[str]:
            run_greet = greet()
            graph_output("result", run_greet.a, "String")
            graph_output("result", run_greet.b, "String")
            return run_greet
        ''',
        "dup_output",
    )

    assert "'result'" in message


def test_an_output_colliding_with_the_return_annotation_is_refused(tmp_path):
    """The return output is added after the body, so this is caught there."""
    message = _expect(
        tmp_path,
        '''
        @pipeline("P")
        def p() -> Out[str]:
            run_greet = greet()
            graph_output("wait_for_output", run_greet.rows, "String")
            return run_greet
        ''',
        "dup_return",
    )

    assert "'wait_for_output'" in message


def test_an_output_colliding_with_an_outputs_field_is_refused(tmp_path):
    message = _expect(
        tmp_path,
        '''
        @dataclass(frozen=True)
        class Result(Outputs):
            rows: Out[str]


        @pipeline("P")
        def p() -> Result:
            run_greet = greet()
            graph_output("rows", run_greet.rows, "String")
            return Result(rows=run_greet.rows)
        ''',
        "dup_outputs_field",
    )

    assert "'rows'" in message


# ---------------------------------------------------------------------------
# Outputs and wiring.


def test_outputs_wire_from_a_task_output_or_a_graph_input(tmp_path):
    out = _compile(
        tmp_path,
        '''
        @pipeline("P")
        def p() -> Out[str]:
            created = graph_input("created", "String")
            run_greet = greet(greeting=created)
            graph_output("scrape_date", run_greet.scrape_date, "String")
            graph_output("passthrough", created, "String")
            return run_greet
        ''',
        "wiring",
    )

    values = _doc(out)["implementation"]["graph"]["outputValues"]
    assert values["scrape_date"] == {
        "taskOutput": {"taskId": "Run Greet", "outputName": "scrape_date"}
    }
    assert values["passthrough"] == {"graphInput": {"inputName": "created"}}


def test_the_returned_output_is_kept_alongside_declared_outputs(tmp_path):
    """Regression: emit PREFERS the multi-output map, so a declared output
    must not displace the value the pipeline returns."""
    out = _compile(
        tmp_path,
        '''
        @pipeline("P")
        def p() -> Out[str]:
            run_greet = greet()
            graph_output("scrape_date", run_greet.scrape_date, "String")
            return run_greet
        ''',
        "return_kept",
    )

    doc = _doc(out)
    assert [o["name"] for o in doc["outputs"]] == ["scrape_date", "wait_for_output"]
    assert list(doc["implementation"]["graph"]["outputValues"]) == [
        "scrape_date",
        "wait_for_output",
    ]


def test_a_constant_graph_output_is_refused_without_echoing_it(tmp_path):
    message = _expect(
        tmp_path,
        '''
        @pipeline("P")
        def p() -> Out[str]:
            run_greet = greet()
            graph_output("literal", "s3://bucket/secret-path", "String")
            return run_greet
        ''',
        "constant_output",
    )

    assert "str" in message
    assert "s3://bucket/secret-path" not in message


# ---------------------------------------------------------------------------
# Layout sugar reuse.


def test_position_and_annotations_are_written_to_the_entry(tmp_path):
    out = _compile(
        tmp_path,
        '''
        @pipeline("P")
        def p() -> Out[str]:
            created = graph_input(
                "created",
                "String",
                annotations={"sdk": "x"},
                position=(-540, 860),
            )
            run_greet = greet(greeting=created)
            graph_output("done", run_greet, "String", position=(120, 40))
            return run_greet
        ''',
        "layout",
    )

    doc = _doc(out)
    assert doc["inputs"][0]["annotations"] == {
        "sdk": "x",
        "editor.position": '{"x": -540, "y": 860}',
    }
    assert doc["outputs"][0]["annotations"] == {
        "editor.position": '{"x": 120, "y": 40}'
    }


def test_a_bad_position_raises_the_layout_error(tmp_path):
    _expect(
        tmp_path,
        '''
        @pipeline("P")
        def p() -> Out[str]:
            created = graph_input("created", "String", position=(float("nan"), 0))
            run_greet = greet(greeting=created)
            return run_greet
        ''',
        "bad_position",
        error=InvalidEditorLayoutError,
    )


def test_a_rejected_annotation_value_is_not_echoed(tmp_path):
    """Annotations go through the same caller policy as
    ``pipeline_annotations``, so a config-sourced value is never echoed."""
    message = _expect(
        tmp_path,
        '''
        @pipeline("P")
        def p() -> Out[str]:
            created = graph_input(
                "created", "String", annotations={"token": ["super-secret"]}
            )
            run_greet = greet(greeting=created)
            return run_greet
        ''',
        "bad_annotation",
    )

    assert "'token'" in message and "list" in message
    assert "super-secret" not in message


# ---------------------------------------------------------------------------
# Subpipelines.


def test_a_child_declared_input_is_bound_from_the_parent_call_site(tmp_path):
    """A child's declared inputs are ordinary graph inputs, so the parent
    passes them by name exactly as it would an ``In[T]`` parameter."""
    out = _compile(
        tmp_path,
        '''
        @pipeline("Child")
        def child() -> Out[str]:
            created = graph_input("Pipeline Creation Time", "String")
            run_greet = greet(greeting=created)
            return run_greet


        @pipeline("Parent")
        def parent() -> Out[str]:
            parent_value = graph_input("parent_value", "String")
            run_child = subpipeline(child)(
                **{"Pipeline Creation Time": parent_value}
            )
            return run_child
        ''',
        "subpipeline",
        pipeline_name="parent",
    )

    parent_task = _doc(out)["implementation"]["graph"]["tasks"]["Run Child"]
    assert parent_task["arguments"] == {
        "Pipeline Creation Time": {"graphInput": {"inputName": "parent_value"}}
    }
    child_path = next(
        p for p in out.parent.rglob("child-*.yaml") if ".components" not in p.name
    )
    child_doc = yaml.safe_load(child_path.read_text(encoding="utf-8"))
    assert child_doc["inputs"] == [
        {"name": "Pipeline Creation Time", "type": "String"}
    ]


# ---------------------------------------------------------------------------
# Misuse outside a trace.


def test_declaring_outside_a_pipeline_body_is_refused():
    """There is no active graph to declare on, and a silent no-op would lose
    the declaration."""
    with pytest.raises(InvalidGraphIoError) as exc:
        graph_input("value", "String")
    assert "@pipeline" in str(exc.value)

    with pytest.raises(InvalidGraphIoError):
        graph_output("value", None, "String")


def test_graph_io_errors_are_compile_errors():
    assert issubclass(InvalidGraphIoError, CompileError)
