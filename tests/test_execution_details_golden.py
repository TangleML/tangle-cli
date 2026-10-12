"""Regression pin for run-details output (``PipelineRunDetails.get_run_details_output``).

The goldens in ``tests/fixtures/run_details`` were generated from the sequential
recursive traversal (one ``executions_details`` call per execution inside
``_enrich_execution_tree``), before ``get_execution_details`` fetched the tree with
``tangle_cli.execution_tree.fetch_execution_tree``. The output must stay
byte-identical however the tree is fetched.

Regenerate (only for an intentional output change), from the repo root:
    python tests/test_execution_details_golden.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from tangle_cli.client import TangleApiClient
from tangle_cli.models import GetExecutionInfoResponse
from tangle_cli.pipeline_run_details import PipelineRunDetails

FIXTURES = Path(__file__).parent / "fixtures" / "run_details"


def _leaf_ref(name: str, image: str) -> dict[str, Any]:
    return {
        "componentRef": {
            "name": name,
            "digest": f"digest-{name}",
            "spec": {
                "name": name,
                "description": f"{name} does work",
                "metadata": {"annotations": {"version": "1.0", "python_dependencies": "[big list]"}},
                "inputs": [{"name": "in", "type": "String"}],
                "outputs": [{"name": "out", "type": "String"}],
                "implementation": {"container": {"image": image, "command": ["run", name]}},
            },
            "text": f"raw yaml for {name}",
        },
        "arguments": {"in": "hello"},
    }


def _execution(
    exec_id: str,
    name: str,
    implementation: dict[str, Any],
    *,
    children: dict[str, str] | None = None,
    inputs: dict[str, str] | None = None,
    outputs: dict[str, str] | None = None,
    state: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": exec_id,
        "task_spec": {
            "componentRef": {
                "digest": f"digest-{exec_id}",
                "spec": {"name": name, "inputs": [], "outputs": [], "implementation": implementation},
                "text": f"raw yaml for {name}",
            },
            "arguments": {"arg": "value"},
        },
        "input_artifacts": {key: {"id": value} for key, value in (inputs or {}).items()},
        "output_artifacts": {key: {"id": value} for key, value in (outputs or {}).items()},
    }
    if children:
        result["child_task_execution_ids"] = children
    if state:
        result["state"] = state
    return result


_FAN_GRAPH = {
    "graph": {
        "tasks": {
            "Score A": _leaf_ref("Score", "gcr.io/score:v2"),
            "Score B": _leaf_ref("Score", "gcr.io/score:v2"),
        }
    }
}

#: root (graph) -> Prepare (leaf), Fan (graph) -> [Score A, Score B] (leaves), Report (leaf)
RESPONSES: dict[str, dict[str, Any]] = {
    "root-exec": _execution(
        "root-exec",
        "Golden Pipeline",
        {
            "graph": {
                "tasks": {
                    "Prepare": _leaf_ref("Prepare", "gcr.io/prep:v1"),
                    "Fan": {
                        "componentRef": {
                            "name": "Fan",
                            "digest": "digest-Fan",
                            "spec": {"name": "Fan", "implementation": _FAN_GRAPH},
                            "text": "raw yaml for Fan",
                        },
                        "arguments": {},
                    },
                    "Report": _leaf_ref("Report", "gcr.io/report:v1"),
                }
            }
        },
        children={"Prepare": "prep-exec", "Fan": "fan-exec", "Report": "report-exec"},
    ),
    "prep-exec": _execution(
        "prep-exec", "Prepare", {"container": {"image": "gcr.io/prep:v1"}}, outputs={"out": "art-prep"},
        state="SUCCEEDED",
    ),
    "fan-exec": _execution(
        "fan-exec", "Fan", _FAN_GRAPH, children={"Score A": "score-a-exec", "Score B": "score-b-exec"},
        inputs={"in": "art-prep"},
    ),
    "score-a-exec": _execution(
        "score-a-exec", "Score", {"container": {"image": "gcr.io/score:v2"}}, inputs={"in": "art-prep"},
        outputs={"out": "art-a"}, state="SUCCEEDED",
    ),
    "score-b-exec": _execution(
        "score-b-exec", "Score", {"container": {"image": "gcr.io/score:v2"}}, inputs={"in": "art-prep"},
        outputs={"out": "art-b"}, state="FAILED",
    ),
    "report-exec": _execution(
        "report-exec", "Report", {"container": {"image": "gcr.io/report:v1"}}, inputs={"a": "art-a", "b": "art-b"},
    ),
}

VARIANTS: dict[str, dict[str, Any]] = {
    "compact": {},
    "implementations": {"include_implementations": True},
    "state_and_annotations": {"include_execution_state": True, "include_annotations": True},
    "narrowed_to_subgraph": {"execution_id": "fan-exec"},
}


def build_client() -> TangleApiClient:
    client = TangleApiClient("https://api.test")
    client.pipeline_runs_get = MagicMock(  # type: ignore[method-assign]
        return_value={
            "id": "run-1",
            "root_execution_id": "root-exec",
            "created_at": "2026-06-11T00:00:00Z",
            "created_by": "tester@example.com",
            "annotations": {"team": "search"},
        }
    )

    def details(execution_id: str) -> GetExecutionInfoResponse:
        return GetExecutionInfoResponse.from_dict(json.loads(json.dumps(RESPONSES[execution_id])))

    client.executions_details = MagicMock(side_effect=details)  # type: ignore[method-assign]
    client.pipeline_runs_annotations = MagicMock(  # type: ignore[method-assign]
        return_value={"team": "search", "owner": "tester"}
    )
    stats = {"prep-exec": {"SUCCEEDED": 1}, "fan-exec": {"SUCCEEDED": 1, "FAILED": 1}}
    client.executions_graph_execution_state = MagicMock(  # type: ignore[method-assign]
        return_value={"child_execution_status_stats": stats}
    )
    return client


def render(variant: str) -> tuple[str, MagicMock]:
    client = build_client()
    output = PipelineRunDetails(client=client).get_run_details_output("run-1", **VARIANTS[variant])
    return json.dumps(output, indent=2, default=str) + "\n", client.executions_details  # type: ignore[return-value]


@pytest.mark.parametrize("variant", sorted(VARIANTS))
def test_run_details_output_is_byte_identical_to_golden(variant):
    rendered, executions_details = render(variant)

    assert rendered == (FIXTURES / f"{variant}.json").read_text()
    # Every execution in the (sub)tree is fetched exactly once.
    expected = 3 if variant == "narrowed_to_subgraph" else len(RESPONSES)
    assert executions_details.call_count == expected
    assert len({call.args[0] for call in executions_details.call_args_list}) == expected


if __name__ == "__main__":  # pragma: no cover - golden regeneration helper
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for name in VARIANTS:
        (FIXTURES / f"{name}.json").write_text(render(name)[0])
        print("wrote", FIXTURES / f"{name}.json")
