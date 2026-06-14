from __future__ import annotations

import subprocess
import sys
import textwrap
from types import SimpleNamespace
from typing import Any

import pytest

from tangle_cli.artifacts import ArtifactManager, get_artifacts, serialize_artifacts
from tangle_cli.models import ArtifactInfo
from tangle_cli.pipeline_run_details import PipelineRunDetails, get_graph_state_output, get_run_details_output
from tangle_cli.pipeline_run_search import PipelineRunSearch, search_pipeline_runs
from tangle_cli.secrets import SecretValueError, SecretsManager, create_secret, list_secrets


class ArtifactClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def artifacts_get(self, artifact_id: str) -> dict[str, Any]:
        self.calls.append(f"artifact:{artifact_id}")
        return {"id": artifact_id, "artifact_data": {"uri": f"gs://bucket/{artifact_id}"}}

    def get_execution_details(self, execution_id: str) -> Any:
        self.calls.append(f"execution:{execution_id}")
        return SimpleNamespace(output_artifacts={"model": {"id": "artifact-model"}})

    def get_run_details(self, run_id: str) -> Any:
        self.calls.append(f"run:{run_id}")
        return SimpleNamespace(execution=None)


def test_artifact_manager_lazy_factory_and_public_serialization() -> None:
    client = ArtifactClient()
    calls: list[str] = []

    def factory() -> ArtifactClient:
        calls.append("created")
        return client

    manager = ArtifactManager(client_factory=factory)
    assert calls == []

    artifacts = manager.get_artifacts("run-1", {"artifact_ids": ["artifact-1"]})

    assert calls == ["created"]
    assert artifacts["artifact-1"].uri == "gs://bucket/artifact-1"
    assert serialize_artifacts(artifacts) == [
        {
            "id": "artifact-1",
            "uri": "gs://bucket/artifact-1",
            "key": "artifact-1",
            "total_size": 0,
            "is_dir": False,
        }
    ]


def test_artifact_function_wrapper_delegates_to_manager() -> None:
    client = ArtifactClient()

    artifacts = get_artifacts("run-1", {"executions": {"exec-1": ["model"]}}, client=client)

    assert artifacts["exec-1/model"].id == "artifact-model"
    assert client.calls == ["execution:exec-1", "artifact:artifact-model"]


class SecretClient:
    def __init__(self) -> None:
        self.created: list[tuple[str, str]] = []

    def secrets_list(self) -> SimpleNamespace:
        return SimpleNamespace(secrets=[SimpleNamespace(secret_name="API_TOKEN", description="token")])

    def secrets_create(
        self,
        secret_name: str,
        secret_value: str,
        description: str | None = None,
        expires_at: str | None = None,
    ) -> SimpleNamespace:
        del expires_at
        self.created.append((secret_name, secret_value))
        return SimpleNamespace(secret_name=secret_name, description=description)

    def secrets_update(
        self,
        secret_name: str,
        secret_value: str,
        description: str | None = None,
        expires_at: str | None = None,
    ) -> SimpleNamespace:
        del secret_value, expires_at
        return SimpleNamespace(secret_name=secret_name, description=description)

    def secrets_delete(self, secret_name: str) -> None:
        del secret_name


def test_secrets_manager_methods_and_function_wrappers(monkeypatch: pytest.MonkeyPatch) -> None:
    client = SecretClient()
    calls: list[str] = []

    def factory() -> SecretClient:
        calls.append("created")
        return client

    manager = SecretsManager(client_factory=factory)
    assert manager.list()["secrets"] == [{"secret_name": "API_TOKEN", "description": "token"}]
    assert calls == ["created"]

    monkeypatch.setenv("SECRET_VALUE", "super-secret")
    result = manager.create("NEW_SECRET", from_env="SECRET_VALUE", description="demo")

    assert result == {
        "status": "success",
        "action": "created",
        "secret": {"secret_name": "NEW_SECRET", "description": "demo"},
    }
    assert client.created == [("NEW_SECRET", "super-secret")]
    assert list_secrets(client)["count"] == 1
    assert create_secret(client, "WRAPPED", value="wrapped")["action"] == "created"

    with pytest.raises(SecretValueError):
        SecretsManager.resolve_secret_value("inline", "SECRET_VALUE")


class SearchClient:
    base_url = "https://tangle.example"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def users_me(self) -> SimpleNamespace:
        return SimpleNamespace(id="user-1")

    def pipeline_runs_list(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {
            "pipeline_runs": [
                {
                    "id": "run-1234567890",
                    "pipeline_name": "Daily Pulse",
                    "created_by": "user-1",
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ],
            "next_page_token": None,
        }


def test_pipeline_run_search_class_and_function_wrapper() -> None:
    client = SearchClient()
    manager = PipelineRunSearch(client=client)

    result = manager.search(name="pulse", created_by="me", limit=10)

    assert result["count"] == 1
    assert result["runs"][0]["run_url"] == "https://tangle.example/runs/run-1234567890"
    assert "system/pipeline_run.name" in client.calls[0]["filter_query"]
    assert "user-1" in client.calls[0]["filter_query"]
    assert manager.build_filter_query(name="pulse") == {
        "and": [{"value_contains": {"key": "system/pipeline_run.name", "value_substring": "pulse"}}]
    }

    wrapped = search_pipeline_runs(client=client, query={"and": []}, limit=1)
    assert wrapped["count"] == 1


def test_pipeline_run_search_lazy_factory() -> None:
    client = SearchClient()
    calls: list[str] = []

    def factory() -> SearchClient:
        calls.append("created")
        return client

    manager = PipelineRunSearch(client_factory=factory)
    assert calls == []
    assert manager.search(query={"and": []}, limit=1)["count"] == 1
    assert calls == ["created"]


class DetailsClient:
    def __init__(self) -> None:
        self.details_kwargs: dict[str, Any] | None = None

    def get_run_details(self, run_id: str, **kwargs: Any) -> SimpleNamespace:
        self.details_kwargs = kwargs
        return SimpleNamespace(
            run=SimpleNamespace(
                id=run_id,
                root_execution_id="exec-root",
                created_at="2026-01-01T00:00:00Z",
                created_by="user-1",
                annotations={"k": "v"},
            ),
            execution=None,
            annotations={"extra": "yes"},
            execution_state=None,
        )

    def pipeline_runs_get(self, run_id: str) -> SimpleNamespace:
        return SimpleNamespace(id=run_id, root_execution_id="exec-root")

    def executions_graph_execution_state(self, root_execution_id: str) -> SimpleNamespace:
        return SimpleNamespace(
            status_totals={"SUCCEEDED": 1},
            failed_execution_ids=[],
            per_execution={root_execution_id: {"SUCCEEDED": 1}},
        )


def test_pipeline_run_details_class_and_function_wrappers() -> None:
    client = DetailsClient()
    manager = PipelineRunDetails(client=client)

    details = manager.get_run_details_output("run-1", include_annotations=True, execution_id="exec-1")

    assert details["run"]["id"] == "run-1"
    assert details["annotations"] == {"extra": "yes"}
    assert client.details_kwargs == {
        "include_annotations": True,
        "include_execution_state": False,
        "execution_id": "exec-1",
    }
    assert get_run_details_output(client, "run-2")["run"]["id"] == "run-2"

    graph = manager.get_graph_state_output(["run-1"])
    assert graph["results"][0]["status_totals"] == {"SUCCEEDED": 1}
    assert get_graph_state_output(client, ["exec-root"])["results"][0]["root_execution_id"] == "exec-root"


def test_pipeline_run_details_lazy_factory() -> None:
    client = DetailsClient()
    calls: list[str] = []

    def factory() -> DetailsClient:
        calls.append("created")
        return client

    manager = PipelineRunDetails(client_factory=factory)
    assert calls == []
    assert manager.get_graph_state_output(["run-1"])["results"][0]["error"] is None
    assert calls == ["created"]


def test_resource_manager_modules_import_without_native_tangle_api() -> None:
    code = r'''
import builtins

original_import = builtins.__import__

def guarded_import(name, *args, **kwargs):
    if name == "tangle_api" or name.startswith("tangle_api."):
        raise ModuleNotFoundError("blocked native tangle_api import")
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import

from tangle_cli.artifacts import ArtifactComponentQuery, ArtifactInfo, ArtifactManager
from tangle_cli.secrets import SecretsManager
from tangle_cli.pipeline_run_search import PipelineRunSearch
from tangle_cli.pipeline_run_details import PipelineRunDetails
from tangle_cli.pipeline_runner import PipelineRunner, PipelineRunnerHooks

assert ArtifactComponentQuery is not None
assert ArtifactInfo(id="a", uri="u").uri == "u"
assert ArtifactManager is not None
assert SecretsManager is not None
assert PipelineRunSearch is not None
assert PipelineRunDetails is not None
assert PipelineRunner is not None
assert PipelineRunnerHooks is not None
'''
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_resource_manager_import_surface() -> None:
    from tangle_cli.artifacts import ArtifactManager as ImportedArtifactManager
    from tangle_cli.artifacts import serialize_artifacts as imported_serialize_artifacts
    from tangle_cli.pipeline_run_details import PipelineRunDetails as ImportedPipelineRunDetails
    from tangle_cli.pipeline_run_search import PipelineRunSearch as ImportedPipelineRunSearch
    from tangle_cli.pipeline_runner import PipelineRunner as ImportedPipelineRunner
    from tangle_cli.secrets import SecretsManager as ImportedSecretsManager

    assert ImportedArtifactManager is ArtifactManager
    assert imported_serialize_artifacts({"a": ArtifactInfo(id="a", uri="u", key="a")})[0]["id"] == "a"
    assert ImportedSecretsManager is SecretsManager
    assert ImportedPipelineRunSearch is PipelineRunSearch
    assert ImportedPipelineRunDetails is PipelineRunDetails
    assert ImportedPipelineRunner is not None
