"""Tests for the ported ``tangle_cli.schema_validation`` module.

Covers the packaged dehydrated-schema loader, JSON-Schema structural
validation, the no-template-delimiter output contract (including
``exempt_paths``), the dehydrated shape detector, and the semantic checks
layered on top of the schema (dangling ``taskOutput.taskId``, undeclared
``graphInput.inputName``, ``outputValues`` ↔ ``outputs`` correspondence,
and the top-level key / ``template_file`` guards).
"""
import copy

import pytest

from tangle_cli.schema_validation import (
    SchemaValidationError,
    assert_no_template_delimiters,
    is_dehydrated_pipeline,
    iter_template_delimiters,
    load_dehydrated_schema,
    validate_dehydrated_data,
    validate_dehydrated_pipeline,
)


def _valid_pipeline() -> dict:
    """A minimal schema-valid, semantically-consistent dehydrated pipeline."""
    return {
        "name": "Demo Pipeline",
        "inputs": [{"name": "in1", "type": "String"}],
        "outputs": [{"name": "out1", "type": "String"}],
        "implementation": {
            "graph": {
                "tasks": {
                    "extract": {
                        "componentRef": {"url": "file://./noop.yaml"},
                        "arguments": {
                            "wait_for": {"graphInput": {"inputName": "in1"}},
                        },
                    },
                    "load": {
                        "componentRef": {"name": "Load"},
                        "arguments": {
                            "rows": {
                                "taskOutput": {
                                    "taskId": "extract",
                                    "outputName": "rows",
                                }
                            },
                            "a_constant": "hello world",
                        },
                    },
                },
                "outputValues": {
                    "out1": {
                        "taskOutput": {"taskId": "load", "outputName": "result"}
                    },
                },
            }
        },
    }


# ---------------------------------------------------------------------------
# Schema loading + structural validation.


def test_load_dehydrated_schema_has_expected_identity():
    schema = load_dehydrated_schema()
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"] == (
        "https://tangleml.com/schemas/tangle/dehydrated_pipeline_schema.json"
    )
    # Loader is cached: same object every call.
    assert load_dehydrated_schema() is schema


def test_validate_dehydrated_data_accepts_valid():
    validate_dehydrated_data(_valid_pipeline())  # should not raise


def test_validate_dehydrated_data_rejects_inline_component_spec():
    data = _valid_pipeline()
    data["implementation"]["graph"]["tasks"]["extract"]["componentRef"] = {
        "spec": {"name": "Extract"}
    }
    with pytest.raises(SchemaValidationError):
        validate_dehydrated_data(data)


def test_validate_dehydrated_data_rejects_missing_name():
    data = _valid_pipeline()
    del data["name"]
    with pytest.raises(SchemaValidationError):
        validate_dehydrated_data(data)


def test_validate_dehydrated_data_rejects_empty_tasks():
    data = _valid_pipeline()
    data["implementation"]["graph"]["tasks"] = {}
    with pytest.raises(SchemaValidationError):
        validate_dehydrated_data(data)


# ---------------------------------------------------------------------------
# Template-delimiter output contract.


def test_iter_template_delimiters_finds_offenders():
    data = {"name": "x", "sql": "SELECT {{ value }}"}
    found = dict(iter_template_delimiters(data))
    assert "sql" in found
    assert found["sql"] == "{{"


def test_assert_no_template_delimiters_passes_clean():
    assert_no_template_delimiters(_valid_pipeline())  # should not raise


def test_assert_no_template_delimiters_flags_leaked_template():
    data = _valid_pipeline()
    data["implementation"]["graph"]["tasks"]["load"]["arguments"][
        "a_constant"
    ] = "{{ not_rendered }}"
    with pytest.raises(SchemaValidationError) as exc:
        assert_no_template_delimiters(data)
    assert "{{" in str(exc.value)


def test_assert_no_template_delimiters_honors_exempt_paths():
    data = _valid_pipeline()
    args = data["implementation"]["graph"]["tasks"]["load"]["arguments"]
    args["a_constant"] = "{{input_1}}"
    path = "implementation.graph.tasks.load.arguments.a_constant"
    # Sanity: the offender is at the path we expect.
    assert (path, "{{") in list(iter_template_delimiters(data))
    # Exempting that exact path is accepted; a wrong path is not.
    assert_no_template_delimiters(data, exempt_paths=[path])
    with pytest.raises(SchemaValidationError):
        assert_no_template_delimiters(data, exempt_paths=["some.other.path"])


# ---------------------------------------------------------------------------
# Shape detection.


def test_is_dehydrated_pipeline_true_for_valid():
    assert is_dehydrated_pipeline(_valid_pipeline()) is True


def test_is_dehydrated_pipeline_false_for_template_file():
    data = _valid_pipeline()
    data["template_file"] = "pipeline.yaml.j2"
    assert is_dehydrated_pipeline(data) is False


def test_is_dehydrated_pipeline_false_for_non_runnable_argument():
    data = _valid_pipeline()
    # A bare int is not a runnable argument value (string / graphInput /
    # taskOutput) -> not yet dehydrated.
    data["implementation"]["graph"]["tasks"]["load"]["arguments"]["rows"] = 42
    assert is_dehydrated_pipeline(data) is False


def test_is_dehydrated_pipeline_false_for_non_mapping():
    assert is_dehydrated_pipeline(["not", "a", "pipeline"]) is False


# ---------------------------------------------------------------------------
# Full validate_dehydrated_pipeline: guards + semantics.


def test_validate_dehydrated_pipeline_accepts_valid():
    validate_dehydrated_pipeline(_valid_pipeline())  # should not raise


def test_validate_dehydrated_pipeline_rejects_template_file():
    data = _valid_pipeline()
    data["template_file"] = "pipeline.yaml.j2"
    with pytest.raises(SchemaValidationError) as exc:
        validate_dehydrated_pipeline(data)
    assert "template_file" in str(exc.value)


def test_validate_dehydrated_pipeline_rejects_extra_top_level_key():
    data = _valid_pipeline()
    data["foo"] = "leaked config"
    with pytest.raises(SchemaValidationError) as exc:
        validate_dehydrated_pipeline(data)
    assert "foo" in str(exc.value)


def test_validate_dehydrated_pipeline_rejects_dangling_task_output():
    data = _valid_pipeline()
    data["implementation"]["graph"]["tasks"]["load"]["arguments"]["rows"] = {
        "taskOutput": {"taskId": "missing", "outputName": "rows"}
    }
    with pytest.raises(SchemaValidationError) as exc:
        validate_dehydrated_pipeline(data)
    assert "missing" in str(exc.value)


def test_validate_dehydrated_pipeline_rejects_undeclared_graph_input():
    data = _valid_pipeline()
    data["implementation"]["graph"]["tasks"]["extract"]["arguments"][
        "wait_for"
    ] = {"graphInput": {"inputName": "nope"}}
    with pytest.raises(SchemaValidationError) as exc:
        validate_dehydrated_pipeline(data)
    assert "nope" in str(exc.value)


def test_validate_dehydrated_pipeline_rejects_output_value_without_output():
    data = _valid_pipeline()
    data["implementation"]["graph"]["outputValues"]["ghost"] = {
        "taskOutput": {"taskId": "load", "outputName": "result"}
    }
    with pytest.raises(SchemaValidationError) as exc:
        validate_dehydrated_pipeline(data)
    assert "ghost" in str(exc.value)


def test_validate_dehydrated_pipeline_rejects_non_scalar_annotation():
    data = _valid_pipeline()
    data["metadata"] = {"annotations": {"owner": {"nested": "object"}}}
    with pytest.raises(SchemaValidationError):
        validate_dehydrated_pipeline(data)


def test_validate_dehydrated_pipeline_does_not_mutate_input():
    data = _valid_pipeline()
    snapshot = copy.deepcopy(data)
    validate_dehydrated_pipeline(data)
    assert data == snapshot
