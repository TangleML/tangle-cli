"""Tests for ``dynamicData`` argument wrappers in Python-authored pipelines.

The dehydrated-pipeline schema is deliberately strict: a ``dynamicData``
argument must be a non-empty ``secret.name`` reference and nothing else.
(The runnable ``pipeline_schema.json`` stays intentionally permissive — it
mirrors the real Tangle runtime, which resolves arbitrary dynamic data such
as run IDs and loop indices — so strictness is enforced here, at compile /
dehydrated-validation time.)
"""
import copy
from pathlib import Path

import pytest
import yaml

from tangle_cli.pipeline_compiler import compile_pipeline
from tangle_cli.python_pipeline.dynamic_data import dynamic_secret
from tangle_cli.python_pipeline.emit import emit_pipeline
from tangle_cli.python_pipeline.graph import GraphBuilder, TaskNode
from tangle_cli.schema_validation import SchemaValidationError, validate_dehydrated_pipeline

FIXTURES = Path(__file__).parent / "fixtures" / "python_pipeline"


def _body_with_dynamic_secret() -> dict:
    builder = GraphBuilder(name="Demo")
    builder.add_task(
        TaskNode(
            task_id="Call Model",
            ref_url="file://./noop.yaml",
            arguments={"openai_api_key": dynamic_secret("OPENAI_API_KEY")},
        )
    )
    return emit_pipeline(builder)[0]


def test_dynamic_secret_emits_dynamic_data_argument():
    body = _body_with_dynamic_secret()

    emitted = body["implementation"]["graph"]["tasks"]["Call Model"]["arguments"]["openai_api_key"]
    assert emitted == {"dynamicData": {"secret": {"name": "OPENAI_API_KEY"}}}
    validate_dehydrated_pipeline(body)


def test_dynamic_secret_fixture_compiles_and_validates(tmp_path):
    """Keep a real Python pipeline example alongside the dynamic-data tests."""
    output = tmp_path / "dynamic_secret.yaml"
    compile_pipeline(FIXTURES / "dynamic_secret_pipeline.py", output)
    body = yaml.safe_load(output.read_text())

    argument = body["implementation"]["graph"]["tasks"]["Call Model"]["arguments"]
    assert argument["openai_api_key"] == {
        "dynamicData": {"secret": {"name": "OPENAI_API_KEY"}}
    }
    validate_dehydrated_pipeline(body)


@pytest.mark.parametrize(
    "dynamic_data",
    [
        {},
        {"secret": {}},
        {"secret": {"name": ""}},
        {"arbitrary": [1]},
        {"secret": {"name": "OPENAI_API_KEY", "extra": "value"}},
    ],
)
def test_dynamic_data_rejects_invalid_secret_references(dynamic_data: dict):
    body = _body_with_dynamic_secret()
    argument = body["implementation"]["graph"]["tasks"]["Call Model"]["arguments"]["openai_api_key"]
    argument["dynamicData"] = copy.deepcopy(dynamic_data)

    with pytest.raises(SchemaValidationError):
        validate_dehydrated_pipeline(body)


def test_dynamic_secret_requires_non_empty_name():
    with pytest.raises(ValueError, match="non-empty secret name"):
        dynamic_secret("")
