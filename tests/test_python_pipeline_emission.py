"""Tests for native ``.with_emission(...)`` on task-reference handles.

Covers the shared seam (annotation key + event-name grammar), the immutable
combinator on every handle type that supports ``.with_annotations``, and the
compiled output the backend actually reads.
"""
from pathlib import Path

import pytest
import yaml

from tangle_cli.pipeline_compiler import compile_pipeline
from tangle_cli.python_pipeline import (
    READINESS_EVENT_ANNOTATION,
    In,
    Out,
    ReadinessEventNameError,
    pipeline,
    ref,
    subpipeline,
    task,
    validate_event_name,
)
from tangle_cli.python_pipeline.errors import CompileError
from tangle_cli.schema_validation import validate_dehydrated_data

FIXTURES = Path(__file__).parent / "fixtures" / "python_pipeline"


@pipeline("Emission Test Child")
def _child(seed: In[str]) -> Out[str]:  # pragma: no cover - traced, not called
    return seed


def _callable_handle():
    return ref(name="exporter")


def _subpipeline_handle():
    return subpipeline(_child)


HANDLES = pytest.mark.parametrize(
    "make_handle", [_callable_handle, _subpipeline_handle], ids=["callable", "subpipeline"]
)


# ----------------------------------------------------------------------
# The shared seam.


def test_annotation_key_is_the_wire_contract():
    assert READINESS_EVENT_ANNOTATION == "tangleml.com/emission/readiness/event"


def test_validate_event_name_is_a_pass_through():
    assert validate_event_name("orders-ready") == "orders-ready"


def test_readiness_event_name_error_is_both_compile_error_and_value_error():
    assert issubclass(ReadinessEventNameError, CompileError)
    assert issubclass(ReadinessEventNameError, ValueError)


# ----------------------------------------------------------------------
# Direct chaining, immutability, composition.


@HANDLES
def test_with_emission_sets_the_readiness_annotation(make_handle):
    handle = make_handle().with_emission("orders-ready")

    assert handle.annotations == {READINESS_EVENT_ANNOTATION: "orders-ready"}


@HANDLES
def test_with_emission_does_not_mutate_the_original_handle(make_handle):
    original = make_handle()
    derived = original.with_emission("orders-ready")

    assert original.annotations is None
    assert derived is not original
    assert type(derived) is type(original)


@HANDLES
@pytest.mark.parametrize("order", ["annotations-first", "emission-first"])
def test_with_emission_composes_with_with_annotations(make_handle, order):
    base = make_handle()
    if order == "annotations-first":
        handle = base.with_annotations({"team": "orders"}).with_emission("orders-ready")
    else:
        handle = base.with_emission("orders-ready").with_annotations({"team": "orders"})

    assert handle.annotations == {
        "team": "orders",
        READINESS_EVENT_ANNOTATION: "orders-ready",
    }


@HANDLES
def test_last_with_emission_wins_and_other_metadata_survives(make_handle):
    handle = (
        make_handle()
        .with_annotations({"team": "orders"})
        .with_emission("orders-ready")
        .named("Export Orders")
        .with_emission("orders-ready-v2")
    )

    assert handle.annotations == {
        "team": "orders",
        READINESS_EVENT_ANNOTATION: "orders-ready-v2",
    }
    assert handle.task_id_hint == "Export Orders"


def test_with_emission_preserves_task_ref_introspection_attributes():
    @task(image="python:3.12")
    def exporter(greeting: str = "hi"):
        """Write a greeting.

        Metadata:
            Name: Exporter
        """
        print(greeting)

    derived = exporter.with_emission("orders-ready")

    assert derived.__name__ == exporter.__name__
    assert derived.__signature__ == exporter.__signature__
    assert derived._task_function_name == exporter._task_function_name


def test_with_emission_on_subpipeline_keeps_child_and_config_overrides():
    handle = subpipeline(_child).override_config(foo="bar").with_emission("child-ready")

    assert handle.child is _child
    assert handle.config_overrides == {"foo": "bar"}


# ----------------------------------------------------------------------
# Invalid values.


@pytest.mark.parametrize(
    "event",
    [
        "",
        "Orders-Ready",
        "orders_ready",
        "orders ready",
        " orders-ready",
        "orders-ready ",
        "orders-ready\n",
        "-orders-ready",
        "orders-ready-",
        "orders.ready",
        "café-ready",
        "a" * 256,
    ],
)
@HANDLES
def test_with_emission_rejects_illegal_event_names(make_handle, event):
    with pytest.raises(ReadinessEventNameError):
        make_handle().with_emission(event)


@pytest.mark.parametrize("event", [None, 1, b"orders-ready", ["orders-ready"]])
@HANDLES
def test_with_emission_rejects_non_string_event_names(make_handle, event):
    with pytest.raises(ReadinessEventNameError) as exc:
        make_handle().with_emission(event)

    assert "plain str" in str(exc.value)


@HANDLES
def test_with_emission_rejects_str_subclasses(make_handle):
    class Event(str):
        pass

    with pytest.raises(ReadinessEventNameError):
        make_handle().with_emission(Event("orders-ready"))


@HANDLES
def test_rejected_non_str_repr_is_never_executed(make_handle):
    """The exact-type gate precedes any inspection of the rejected value."""

    class HostileRepr:
        def __repr__(self):
            raise RuntimeError("boom")

    with pytest.raises(ReadinessEventNameError) as exc:
        make_handle().with_emission(HostileRepr())

    assert "HostileRepr" in str(exc.value)


@HANDLES
def test_rejected_str_subclass_repr_is_never_executed(make_handle):
    class HostileEvent(str):
        def __repr__(self):
            raise RuntimeError("boom")

    with pytest.raises(ReadinessEventNameError) as exc:
        make_handle().with_emission(HostileEvent("orders-ready"))

    assert "plain str" in str(exc.value)


def test_error_message_names_the_grammar_and_elides_huge_values():
    with pytest.raises(ReadinessEventNameError) as exc:
        validate_event_name("X" * 5000)

    message = str(exc.value)
    assert "lowercase" in message and "255" in message
    assert "XXXXXXXXXX" in message
    assert len(message) < 500


@pytest.mark.parametrize("event", ["a", "fx1", "orders-eu-ready", "a" * 255])
def test_legal_event_names_are_accepted(event):
    assert ref(name="exporter").with_emission(event).annotations == {
        READINESS_EVENT_ANNOTATION: event
    }


# ----------------------------------------------------------------------
# Compiled output.


def test_emission_annotations_reach_the_compiled_pipeline(tmp_path):
    output = tmp_path / "compiled.yaml"
    compile_pipeline(
        FIXTURES / "emission_pipeline.py", output, pipeline_name="Emission Parent"
    )
    body = yaml.safe_load(output.read_text())

    tasks = body["implementation"]["graph"]["tasks"]
    assert tasks["Export Orders"]["annotations"] == {
        "team": "orders",
        READINESS_EVENT_ANNOTATION: "orders-ready",
    }
    assert tasks["Run Child"]["annotations"] == {
        READINESS_EVENT_ANNOTATION: "child-ready"
    }
    validate_dehydrated_data(body)
