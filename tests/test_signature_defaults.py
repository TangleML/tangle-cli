"""Graph-input defaults are rendered as the string the schema wants, or refused.

``InputSpec.default`` is ``string | null``, and all 592 defaults across our
pipeline corpus are strings. A bare ``In[int] = 5`` — the shape ``In``'s own
docstring shows — emitted ``default: 5``, a schema-invalid document nothing
rejected locally. Authors worked around it by declaring numbers as
``In[str] = "60"``.

Rendering is deliberately narrow: six types in, everything else refused by
name, because ``str()`` on an enum or a dataclass produces text no one meant
to ship. ``graph_input(default=...)`` accepts the same values through the same
serializer.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from tangle_cli.component_from_func import _serialize_default
from tangle_cli.input_defaults import serialize_input_default
from tangle_cli.pipeline_compiler import compile_pipeline
from tangle_cli.python_pipeline.errors import CompileError, InvalidInputDefaultError

_PREAMBLE = '''
from tangle_cli.python_pipeline import In, Out, graph_input, pipeline, task


@task(image="python:3.12")
def greet(greeting: str = "hi"):
    """Write a greeting.

    Metadata:
        Name: Greet
    """
    print(greeting)
'''

_SIGNATURE = _PREAMBLE + '''

@pipeline("Defaults")
def defaults(value: In[__TYPE__] = __DEFAULT__) -> Out[str]:
    run_greet = greet(greeting=value)
    return run_greet
'''

_BODY = _PREAMBLE + '''

@pipeline("Defaults")
def defaults() -> Out[str]:
    value = graph_input("value", __TYPE__, default=__DEFAULT__)
    run_greet = greet(greeting=value)
    return run_greet
'''


def _write(tmp_path: Path, template: str, type_repr: str, default_repr: str, case: str) -> Path:
    case_dir = tmp_path / case
    case_dir.mkdir(parents=True, exist_ok=True)
    script = case_dir / "pipeline.py"
    script.write_text(
        textwrap.dedent(template)
        .replace("__TYPE__", type_repr)
        .replace("__DEFAULT__", default_repr),
        encoding="utf-8",
    )
    return script


def _compile(tmp_path: Path, template: str, type_repr: str, default_repr: str, case: str) -> dict:
    script = _write(tmp_path, template, type_repr, default_repr, case)
    out = script.parent / "compiled.yaml"
    compile_pipeline(script, out)
    return yaml.safe_load(out.read_text(encoding="utf-8"))


def _compile_error(
    tmp_path: Path, template: str, type_repr: str, default_repr: str, case: str
) -> str:
    script = _write(tmp_path, template, type_repr, default_repr, case)
    with pytest.raises(CompileError) as excinfo:
        compile_pipeline(script, script.parent / "compiled.yaml")
    return str(excinfo.value)


# ============================================================================
# Accepted values, through the signature and the body
# ============================================================================


@pytest.mark.parametrize(
    "py_type, tangle_type, default_repr, expected",
    [
        ("str", '"String"', '"x"', "x"),
        ("str", '"String"', '"41"', "41"),
        ("int", '"Integer"', "41", "41"),
        ("int", '"Integer"', "0", "0"),
        ("int", '"Integer"', "-1", "-1"),
        ("float", '"Float"', "1.5", "1.5"),
        ("bool", '"Boolean"', "True", "True"),
        ("bool", '"Boolean"', "False", "False"),
    ],
)
@pytest.mark.parametrize("route", ["signature", "body"])
def test_a_scalar_default_is_emitted_as_a_string(
    tmp_path, py_type, tangle_type, default_repr, expected, route
):
    template, type_repr = (
        (_SIGNATURE, py_type) if route == "signature" else (_BODY, tangle_type)
    )
    doc = _compile(
        tmp_path, template, type_repr, default_repr, f"{route}_{py_type}_{expected}"
    )

    entry = doc["inputs"][0]
    assert entry["default"] == expected
    assert isinstance(entry["default"], str)
    assert entry["optional"] is True


def test_the_yaml_text_quotes_a_numeric_default(tmp_path):
    """Round-tripping must not turn the string back into a number, which is
    what makes this a real fix rather than an in-memory detail."""
    script = _write(tmp_path, _SIGNATURE, "int", "41", "quoting")
    out = script.parent / "compiled.yaml"
    compile_pipeline(script, out)

    assert "default: '41'" in out.read_text(encoding="utf-8")


def test_a_string_default_is_unchanged(tmp_path):
    """The overwhelmingly common case stays byte-identical to before."""
    doc = _compile(tmp_path, _SIGNATURE, "str", '"already a string"', "unchanged")

    assert doc["inputs"][0]["default"] == "already a string"


def test_a_none_default_is_left_alone(tmp_path):
    """``default: null`` is schema-valid; rewriting it would churn existing
    documents for no gain."""
    doc = _compile(tmp_path, _SIGNATURE, "str", "None", "none_default")

    entry = doc["inputs"][0]
    assert entry["default"] is None
    assert entry["optional"] is True


def test_a_whole_number_for_a_float_input_renders_as_written(tmp_path):
    """Integer-looking Float defaults are idiomatic and appear in the corpus
    ('0', '72'), so they render as-is rather than as '1.0'."""
    doc = _compile(tmp_path, _SIGNATURE, "float", "1", "int_for_float")

    assert doc["inputs"][0]["default"] == "1"


def test_container_defaults_serialize_as_sorted_json():
    """Sorted keys keep the document reproducible across runs."""
    field = "input 'x'"
    assert serialize_input_default({"b": 1, "a": 2}, field=field) == '{"a": 2, "b": 1}'
    assert serialize_input_default(["b", "a"], field=field) == '["b", "a"]'


# ============================================================================
# Refused values
# ============================================================================


@pytest.mark.parametrize(
    "py_type, tangle_type, default_repr, case",
    [
        ("int", '"Integer"', "1.5", "float_for_int"),
        ("str", '"String"', "5", "int_for_str"),
        ("int", '"Integer"', "True", "bool_for_int"),
        ("bool", '"Boolean"', "1", "int_for_bool"),
    ],
)
@pytest.mark.parametrize("route", ["signature", "body"])
def test_a_default_contradicting_the_declared_type_is_refused(
    tmp_path, py_type, tangle_type, default_repr, case, route
):
    template, type_repr = (
        (_SIGNATURE, py_type) if route == "signature" else (_BODY, tangle_type)
    )
    message = _compile_error(tmp_path, template, type_repr, default_repr, f"{route}_{case}")

    assert "is declared" in message
    assert "its default is" in message


def test_a_prerendered_string_default_is_accepted_for_any_tangle_type(tmp_path):
    """Every default in the corpus is written as text, so a ported
    ``graph_input("n", "Integer", default="-1")`` must keep emitting the bytes
    it did before this rule existed."""
    doc = _compile(tmp_path, _BODY, '"Integer"', '"-1"', "prerendered")

    assert doc["inputs"][0]["default"] == "-1"


def test_a_string_default_still_contradicts_a_python_annotation(tmp_path):
    """``In[int]`` is a Python type, not a wire type: a string default is a
    mislabelled signature, and naming it points at the right fix."""
    message = _compile_error(tmp_path, _SIGNATURE, "int", '"41"', "str_for_int_sig")

    assert "is declared int" in message


def test_an_unsupported_type_is_refused_by_name(tmp_path):
    """An enum's ``str()`` is 'Colour.RED' — text no one meant to ship."""
    script = _write(tmp_path, _SIGNATURE, "str", "None", "unsupported")
    script.write_text(
        script.read_text(encoding="utf-8").replace(
            "def defaults(value: In[str] = None)",
            "def defaults(value: In[str] = __import__('pathlib').Path('/tmp/x'))",
        ),
        encoding="utf-8",
    )
    with pytest.raises(InvalidInputDefaultError) as excinfo:
        compile_pipeline(script, script.parent / "compiled.yaml")

    message = str(excinfo.value)
    assert "PosixPath" in message
    assert "/tmp/x" not in message


def test_an_unsupported_type_is_refused_where_no_type_rule_applies(tmp_path):
    """The Json route is not type-checked, so only the supported-types gate
    stands between a Path and ``str(value)`` text in the document."""
    message = _compile_error(
        tmp_path, _BODY, '"Json"', "__import__('pathlib').Path('/tmp/x')", "json_path"
    )

    assert "must be one of" in message
    assert "PosixPath" in message
    assert "/tmp/x" not in message


@pytest.mark.parametrize("default_repr", ["float('nan')", "float('inf')"])
def test_a_non_finite_float_is_refused(tmp_path, default_repr):
    """JSON has no spelling for them, so nothing downstream could read one."""
    message = _compile_error(
        tmp_path, _SIGNATURE, "float", default_repr, f"nonfinite_{len(default_repr)}"
    )

    assert "finite" in message


def test_the_body_route_refuses_an_explicit_none(tmp_path):
    """Omitting the argument is the way to declare no default; passing None
    is a mistake worth naming."""
    message = _compile_error(tmp_path, _BODY, '"String"', "None", "body_none")

    assert "Omit the argument" in message


def test_a_json_typed_input_is_not_type_checked(tmp_path):
    """Json values are structural, so the scalar rule does not apply."""
    doc = _compile(tmp_path, _BODY, '"Json"', '{"b": 1, "a": 2}', "json_type")

    assert doc["inputs"][0]["default"] == '{"a": 2, "b": 1}'


# ============================================================================
# Diagnostics and cross-path agreement
# ============================================================================


def test_diagnostics_name_the_input_without_echoing_the_value(tmp_path):
    message = _compile_error(tmp_path, _SIGNATURE, "int", "1.5000001", "no_echo")

    assert "'value'" in message
    assert "1.5000001" not in message


def test_the_pipeline_and_component_paths_spell_a_boolean_the_same_way(tmp_path):
    """One document must not spell the same value two ways: a Boolean default
    reads ``True`` whether it is a graph input or a component input."""
    doc = _compile(tmp_path, _SIGNATURE, "bool", "True", "agreement")

    assert doc["inputs"][0]["default"] == _serialize_default(True, "Boolean")


def test_a_serialized_boolean_is_parsable_by_the_generated_container():
    """The container's deserializer lowercases first, so Python spelling is
    safe; this pins the pairing rather than assuming it."""
    field = "input 'x'"
    assert serialize_input_default(True, field=field).lower() in ("true", "1", "yes")
    assert serialize_input_default(False, field=field).lower() in ("false", "0", "no")
