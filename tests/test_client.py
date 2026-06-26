from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import tangle_cli.client as client_module
from tangle_cli.client import TangleApiClient
from tangle_cli.models import ComponentInfo


def test_find_existing_components_matches_exact_names_case_insensitively() -> None:
    client = TangleApiClient("https://api.test")
    client.list_published_component_infos = MagicMock(
        return_value=[
            ComponentInfo(name="Scrape V2", digest="matching-digest"),
            ComponentInfo(name="Other", digest="other-digest"),
        ]
    )

    results = client.find_existing_components(names=["scrape v2"])

    assert [component.digest for component in results] == ["matching-digest"]


def test_get_run_pipeline_spec_fetches_raw_root_execution_without_enrichment() -> None:
    client = TangleApiClient("https://api.test")
    task_spec = MagicMock(name="task_spec")
    execution = SimpleNamespace(task_spec=task_spec)
    client.pipeline_runs_get = MagicMock(
        return_value={"id": "run-1", "root_execution_id": "root-exec-1"}
    )
    client.executions_details = MagicMock(return_value=execution)
    client.get_run_details = MagicMock(
        side_effect=AssertionError("get_run_pipeline_spec must not enrich via get_run_details")
    )
    client._enrich_execution_tree = MagicMock()

    assert client.get_run_pipeline_spec("run-1") is task_spec
    client.executions_details.assert_called_once_with("root-exec-1")
    client.get_run_details.assert_not_called()
    client._enrich_execution_tree.assert_not_called()


def test_get_run_pipeline_spec_reads_generated_run_response_directly(monkeypatch) -> None:
    def fail_from_dict(*args, **kwargs):
        raise AssertionError("get_run_pipeline_spec should not round-trip through PipelineRun.from_dict")

    monkeypatch.setattr(client_module.PipelineRun, "from_dict", fail_from_dict)
    client = TangleApiClient("https://api.test")
    task_spec = MagicMock(name="task_spec")
    client.pipeline_runs_get = MagicMock(return_value=SimpleNamespace(root_execution_id="root-exec-1"))
    client.executions_details = MagicMock(return_value=SimpleNamespace(task_spec=task_spec))

    assert client.get_run_pipeline_spec("run-1") is task_spec
    client.executions_details.assert_called_once_with("root-exec-1")
