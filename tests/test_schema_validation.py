"""Tests for the ported ``tangle_cli.schema_validation`` module.

Covers the packaged dehydrated-schema loader, JSON-Schema structural
validation, the no-template-delimiter output contract (including
``exempt_paths``), the dehydrated shape detector, and the semantic checks
layered on top of the schema (dangling ``taskOutput.taskId``, undeclared
``graphInput.inputName``, ``outputValues`` ↔ ``outputs`` correspondence,
and the top-level key / ``template_file`` guards).
"""
import copy

import jsonschema
import pytest
from jsonschema import Draft202012Validator
from jsonschema.validators import validator_for

from tangle_cli import schema_validation
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


@pytest.mark.parametrize(
    "condition",
    [
        "false",
        {"graphInput": {"inputName": "in1"}},
        {"taskOutput": {"taskId": "extract", "outputName": "enabled"}},
    ],
)
def test_validate_dehydrated_data_accepts_supported_is_enabled(condition):
    data = _valid_pipeline()
    data["implementation"]["graph"]["tasks"]["load"]["isEnabled"] = condition
    validate_dehydrated_data(data)


@pytest.mark.parametrize(
    "condition",
    [
        False,
        {"dynamicData": {"secret": {"name": "TOKEN"}}},
        {"graphInput": "in1"},
        {"taskOutput": {"taskId": "extract"}},
    ],
)
def test_validate_dehydrated_data_rejects_unsupported_is_enabled(condition):
    data = _valid_pipeline()
    data["implementation"]["graph"]["tasks"]["load"]["isEnabled"] = condition
    with pytest.raises(SchemaValidationError):
        validate_dehydrated_data(data)


@pytest.mark.parametrize(
    "execution_options",
    [
        {"cachingStrategy": {"maxCacheStaleness": "P0D"}},
        {"cachingStrategy": {"maxCacheStaleness": None}},
        {"retryStrategy": {"maxRetries": 0}},
        {
            "cachingStrategy": {"maxCacheStaleness": "P7D"},
            "retryStrategy": {"maxRetries": 3},
        },
    ],
)
def test_validate_dehydrated_data_accepts_supported_execution_options(
    execution_options,
):
    data = _valid_pipeline()
    data["implementation"]["graph"]["tasks"]["load"][
        "executionOptions"
    ] = execution_options
    validate_dehydrated_data(data)


@pytest.mark.parametrize(
    "execution_options",
    [
        # ``maxRetries`` is required by the backend's RetryStrategySpec, so a
        # partial retry strategy must not pass the dehydrated schema either.
        {"retryStrategy": {}},
        {"retryStrategy": {"backoff": "30s"}},
        {"retryStrategy": {"maxRetries": -1}},
        {"retryStrategy": {"maxRetries": "3"}},
        {"cachingStrategy": {"maxCacheStaleness": 0}},
        {"cachingStrategy": "P0D"},
    ],
)
def test_validate_dehydrated_data_rejects_unsupported_execution_options(
    execution_options,
):
    data = _valid_pipeline()
    data["implementation"]["graph"]["tasks"]["load"][
        "executionOptions"
    ] = execution_options
    with pytest.raises(SchemaValidationError):
        validate_dehydrated_data(data)


# ---------------------------------------------------------------------------
# Compiled-validator caching: check_schema runs once, every payload
# still fully validated with unchanged error semantics.


@pytest.fixture
def check_schema_calls(monkeypatch):
    """Cold validator cache; record every check_schema call."""
    calls: list[object] = []
    original = Draft202012Validator.check_schema

    def counting(cls, s, *args, **kwargs):
        calls.append(s)
        return original(s, *args, **kwargs)

    monkeypatch.setattr(Draft202012Validator, "check_schema", classmethod(counting))
    schema_validation._dehydrated_validator.cache_clear()
    yield calls
    schema_validation._dehydrated_validator.cache_clear()


def _uncached_error(data) -> str | None:
    """Reference: the pre-cache path (build a validator every call)."""
    schema = load_dehydrated_schema()
    cls = validator_for(schema)
    errors = sorted(cls(schema).iter_errors(data), key=lambda e: list(e.absolute_path))
    if not errors:
        return None
    best = jsonschema.exceptions.best_match(errors)
    loc = ".".join(str(p) for p in best.absolute_path) or "root"
    return f"dehydrated pipeline failed schema validation at {loc}: {best.message}"


def _invalid_payloads() -> list[dict]:
    missing_name = _valid_pipeline()
    del missing_name["name"]
    no_tasks = _valid_pipeline()
    no_tasks["implementation"]["graph"]["tasks"] = {}
    inline_spec = _valid_pipeline()
    inline_spec["implementation"]["graph"]["tasks"]["extract"]["componentRef"] = {
        "spec": {"name": "Extract"}
    }
    bad_retry = _valid_pipeline()
    bad_retry["implementation"]["graph"]["tasks"]["load"]["executionOptions"] = {
        "retryStrategy": {"maxRetries": -1}
    }
    return [missing_name, no_tasks, inline_spec, bad_retry]


def test_check_schema_runs_once_across_many_validations(check_schema_calls):
    for _ in range(25):
        validate_dehydrated_data(_valid_pipeline())
        for payload in _invalid_payloads():
            with pytest.raises(SchemaValidationError):
                validate_dehydrated_data(payload)
    assert len(check_schema_calls) == 1


def test_cached_validator_keeps_error_messages(check_schema_calls):
    # Interleave valid/invalid payloads on a warm cache; each result must
    # match the uncached reference exactly (message + location).
    for _ in range(3):
        validate_dehydrated_data(_valid_pipeline())
        for payload in _invalid_payloads():
            expected = _uncached_error(payload)
            assert expected is not None
            with pytest.raises(SchemaValidationError) as excinfo:
                validate_dehydrated_data(payload)
            assert str(excinfo.value) == expected
            assert excinfo.value.location == expected.split(" at ", 1)[1].split(":", 1)[0]
            validate_dehydrated_data(_valid_pipeline())
    assert len(check_schema_calls) == 1


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


def test_validate_dehydrated_pipeline_checks_is_enabled_references():
    data = _valid_pipeline()
    data["implementation"]["graph"]["tasks"]["load"]["isEnabled"] = {
        "taskOutput": {"taskId": "missing", "outputName": "enabled"}
    }
    with pytest.raises(SchemaValidationError) as exc:
        validate_dehydrated_pipeline(data)
    assert "tasks.load.isEnabled" in str(exc.value)
    assert "missing" in str(exc.value)


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
