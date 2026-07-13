from __future__ import annotations

import builtins
import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import requests

from tangle_cli import artifacts as artifacts_module
from tangle_cli import artifacts_cli, cli
from tangle_cli.logger import get_default_logger


def run_app(app: Any, args: list[str]) -> None:
    try:
        app(args)
    except SystemExit as exc:
        if exc.code not in (0, None):
            raise


def _stub_client(monkeypatch: Any) -> list[dict[str, Any]]:
    """Stub client construction so mode tests never touch the network."""

    client_calls: list[dict[str, Any]] = []

    def fake_client_from_options(**kwargs: Any) -> object:
        client_calls.append(kwargs)
        return object()

    monkeypatch.setattr(artifacts_cli, "LazyTangleApiClient", fake_client_from_options)
    return client_calls


def _run_expecting_error(app: Any, args: list[str]) -> int:
    try:
        app(args)
    except SystemExit as exc:
        assert exc.code not in (0, None)
        return exc.code  # type: ignore[return-value]
    raise AssertionError("expected the command to fail")


def test_sdk_artifacts_help_lists_get_only(capsys) -> None:
    app = cli.build_app()

    run_app(app, ["sdk", "artifacts", "--help"])

    output = capsys.readouterr().out
    assert "get" in output
    assert "download" not in output
    assert "upload" not in output
    assert "signed" not in output.lower()


def test_sdk_artifacts_get_cli_config_auth_and_env_isolation(monkeypatch, tmp_path, capsys) -> None:
    config = tmp_path / "artifacts.yaml"
    config.write_text(
        "run_id: run-config\n"
        "query: '{\"artifact_ids\": [\"artifact-config\"]}'\n"
        "base_url: https://config.example\n"
        "token: config-token\n"
        "auth_header: Bearer config-auth\n"
        "header:\n"
        "  - 'X-Config: yes'\n",
        encoding="utf-8",
    )
    fake_client = object()
    client_calls: list[dict[str, Any]] = []
    get_calls: list[dict[str, Any]] = []

    def fake_client_from_options(**kwargs: Any) -> object:
        client_calls.append(kwargs)
        return fake_client

    def fake_get_artifacts(self, run_id: str, query: dict[str, Any]) -> dict[str, object]:
        get_calls.append({"run_id": run_id, "query": query, "client": self._require_client()})
        return {"artifact-config": object()}

    monkeypatch.setattr(artifacts_cli, "LazyTangleApiClient", fake_client_from_options)
    monkeypatch.setattr(artifacts_module.ArtifactManager, "get_artifacts", fake_get_artifacts)
    monkeypatch.setattr(
        artifacts_module.ArtifactManager,
        "serialize_artifacts",
        staticmethod(lambda artifacts: [{"id": "artifact-config", "uri": "gs://bucket/config", "key": "artifact-config"}]),
    )

    app = cli.build_app()
    run_app(app, ["sdk", "artifacts", "get", "--config", str(config)])

    result = json.loads(capsys.readouterr().out)
    assert result == {
        "status": "success",
        "run_id": "run-config",
        "count": 1,
        "artifacts": [{"id": "artifact-config", "key": "artifact-config", "uri": "gs://bucket/config"}],
    }
    assert client_calls == [
        {
            "base_url": "https://config.example",
            "token": "config-token",
            "auth_header": "Bearer config-auth",
            "header": ["X-Config: yes"],
            "include_env_credentials": False,
            "command_name": "artifact commands",
            "logger": get_default_logger(),
        }
    ]
    assert get_calls == [
        {
            "run_id": "run-config",
            "query": {"artifact_ids": ["artifact-config"]},
            "client": fake_client,
        }
    ]


def test_sdk_artifacts_get_cli_base_url_keeps_env_credentials(monkeypatch, tmp_path, capsys) -> None:
    config = tmp_path / "artifacts.yaml"
    config.write_text(
        "run_id: run-config\n"
        "query:\n"
        "  artifact_ids: [artifact-config]\n"
        "base_url: https://config.example\n",
        encoding="utf-8",
    )
    client_calls: list[dict[str, Any]] = []

    def fake_client_from_options(**kwargs: Any) -> object:
        client_calls.append(kwargs)
        return object()

    monkeypatch.setattr(artifacts_cli, "LazyTangleApiClient", fake_client_from_options)
    monkeypatch.setattr(artifacts_module.ArtifactManager, "get_artifacts", lambda self, *args, **kwargs: {})
    monkeypatch.setattr(artifacts_module.ArtifactManager, "serialize_artifacts", staticmethod(lambda artifacts: []))

    app = cli.build_app()
    run_app(
        app,
        [
            "sdk",
            "artifacts",
            "get",
            "--config",
            str(config),
            "--base-url",
            "https://cli.example",
        ],
    )

    json.loads(capsys.readouterr().out)
    assert client_calls[-1]["base_url"] == "https://cli.example"
    assert client_calls[-1]["include_env_credentials"] is True


def test_sdk_artifacts_get_missing_native_api_uses_friendly_error(monkeypatch, tmp_path) -> None:
    config = tmp_path / "artifacts.yaml"
    config.write_text(
        "run_id: run-config\nquery: '{\"artifact_ids\": [\"artifact-config\"]}'\n",
        encoding="utf-8",
    )
    import tangle_cli

    for attr in ("artifacts", "client", "models"):
        if hasattr(tangle_cli, attr):
            monkeypatch.delattr(tangle_cli, attr)
    for name in list(sys.modules):
        if name in {"tangle_cli.artifacts", "tangle_cli.client", "tangle_cli.models"} or name.startswith("tangle_api"):
            monkeypatch.delitem(sys.modules, name, raising=False)

    original_import = builtins.__import__

    def guarded_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "tangle_api" or name.startswith("tangle_api."):
            raise ModuleNotFoundError("No module named 'tangle_api'", name="tangle_api")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    app = cli.build_app()

    try:
        app(["sdk", "artifacts", "get", "--config", str(config)])
    except SystemExit as exc:
        message = str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("expected missing native API to fail")

    assert "Generated Tangle API bindings are required for artifact commands" in message
    assert "Install the default tangle-cli package with tangle-api" in message
    assert "local src/tangle_api shadows site-packages" in message


def test_sdk_artifacts_get_cli_requires_query(tmp_path) -> None:
    config = tmp_path / "artifacts.yaml"
    config.write_text("run_id: run-config\n", encoding="utf-8")
    app = cli.build_app()

    try:
        app(["sdk", "artifacts", "get", "--config", str(config)])
    except SystemExit as exc:
        assert exc.code not in (0, None)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("expected missing query to fail")


def test_artifacts_cli_imports_without_native_api(monkeypatch) -> None:
    for name in list(sys.modules):
        if name == "tangle_cli.artifacts_cli" or name.startswith("tangle_api"):
            del sys.modules[name]

    original_import = builtins.__import__

    def guarded_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "tangle_api" or name.startswith("tangle_api."):
            raise AssertionError(f"unexpected native API import: {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    module = importlib.import_module("tangle_cli.artifacts_cli")

    assert module.app.name == ("artifacts",)


# ---------------------------------------------------------------------------
# --list mode
# ---------------------------------------------------------------------------


def test_sdk_artifacts_list_mode_calls_manager_and_reports_rows(monkeypatch, capsys) -> None:
    _stub_client(monkeypatch)
    list_calls: list[dict[str, Any]] = []

    def fake_list(self, run_id: str, *, include_children: bool) -> list[dict[str, str]]:
        list_calls.append({"run_id": run_id, "include_children": include_children})
        return [{"owner": "root", "output": "model", "artifact_id": "artifact-model"}]

    monkeypatch.setattr(artifacts_module.ArtifactManager, "list_result_artifacts", fake_list)

    app = cli.build_app()
    run_app(app, ["sdk", "artifacts", "get", "run-1", "--list", "--include-children"])

    result = json.loads(capsys.readouterr().out)
    assert result == {
        "status": "success",
        "run_id": "run-1",
        "count": 1,
        "artifacts": [{"owner": "root", "output": "model", "artifact_id": "artifact-model"}],
    }
    assert list_calls == [{"run_id": "run-1", "include_children": True}]


# ---------------------------------------------------------------------------
# --out-dir mode
# ---------------------------------------------------------------------------


def test_sdk_artifacts_download_mode_reports_resolved_paths(monkeypatch, tmp_path, capsys) -> None:
    _stub_client(monkeypatch)
    download_calls: list[dict[str, Any]] = []
    written = tmp_path / "root__model__artifact-mod.json"
    written.write_bytes(b"{}")

    def fake_download(
        self, run_id: str, *, out_dir: Any, only: Any, include_children: bool
    ) -> dict[str, Path]:
        download_calls.append(
            {
                "run_id": run_id,
                "out_dir": out_dir,
                "only": only,
                "include_children": include_children,
            }
        )
        return {"root::model": written}

    monkeypatch.setattr(artifacts_module.ArtifactManager, "download_result_artifacts", fake_download)

    app = cli.build_app()
    run_app(
        app,
        [
            "sdk",
            "artifacts",
            "get",
            "run-1",
            "--out-dir",
            str(tmp_path),
            "--only",
            "model",
            "--include-children",
        ],
    )

    result = json.loads(capsys.readouterr().out)
    assert result == {
        "status": "success",
        "run_id": "run-1",
        "count": 1,
        "out_dir": str(tmp_path.resolve()),
        "artifacts": {"root::model": str(written.resolve())},
    }
    assert download_calls == [
        {
            "run_id": "run-1",
            "out_dir": str(tmp_path),
            "only": ["model"],
            "include_children": True,
        }
    ]


def test_sdk_artifacts_download_end_to_end_writes_streamed_bytes(monkeypatch, tmp_path, capsys) -> None:
    # Full command path with only the client boundary faked: the real
    # ArtifactManager resolves the run, streams the direct /data response, and
    # the CLI reports the written path.
    class _StreamResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            pass

        def iter_content(self, chunk_size: int):
            yield b'{"model":'
            yield b" true}"

        def close(self) -> None:
            pass

    class _FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def pipeline_runs_get(self, run_id: str) -> Any:
            # No run record: the resolver treats the id as an execution id.
            raise requests.HTTPError(
                "404 Not Found", response=SimpleNamespace(status_code=404)
            )

        def executions_details(self, execution_id: str) -> Any:
            return {"output_artifacts": {"model": {"id": "artifact-model"}}}

        def request_raw(self, method: str, path: str, **kwargs: Any) -> Any:
            assert (method, path) == ("GET", "/api/artifacts/artifact-model/data")
            return _StreamResponse()

    monkeypatch.setattr(artifacts_cli, "LazyTangleApiClient", _FakeClient)

    app = cli.build_app()
    run_app(app, ["sdk", "artifacts", "get", "run-1", "--out-dir", str(tmp_path)])

    result = json.loads(capsys.readouterr().out)
    # Streamed downloads keep the bare filename; only inline JSON values gain .json.
    written = tmp_path / "root__model__artifact-mod"
    assert result == {
        "status": "success",
        "run_id": "run-1",
        "count": 1,
        "out_dir": str(tmp_path.resolve()),
        "artifacts": {"root::model": str(written.resolve())},
    }
    assert written.read_bytes() == b'{"model": true}'


# ---------------------------------------------------------------------------
# API error surfacing
# ---------------------------------------------------------------------------


def test_sdk_artifacts_http_error_reports_clean_json_error(monkeypatch, capsys) -> None:
    # An HTTPError from a metadata endpoint must surface as a one-line JSON
    # error with a nonzero exit, not a raw requests traceback.
    _stub_client(monkeypatch)
    response = SimpleNamespace(
        status_code=500,
        reason="Internal Server Error",
        url="https://api.test/api/executions/root-exec/details",
        text="backend exploded",
        request=SimpleNamespace(
            method="GET", url="https://api.test/api/executions/root-exec/details"
        ),
    )

    def fake_list(self, run_id: str, *, include_children: bool) -> list[dict[str, str]]:
        raise requests.HTTPError("500 Server Error", response=response)  # type: ignore[arg-type]

    monkeypatch.setattr(artifacts_module.ArtifactManager, "list_result_artifacts", fake_list)

    app = cli.build_app()
    payload = _error_output(app, ["sdk", "artifacts", "get", "run-1", "--list"], capsys)
    assert payload["status"] == "error"
    assert (
        "Tangle API request failed (500 Internal Server Error) for "
        "GET https://api.test/api/executions/root-exec/details: backend exploded"
    ) == payload["error"]


def test_sdk_artifacts_transport_error_reports_clean_json_error(monkeypatch, capsys) -> None:
    _stub_client(monkeypatch)

    def fake_list(self, run_id: str, *, include_children: bool) -> list[dict[str, str]]:
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr(artifacts_module.ArtifactManager, "list_result_artifacts", fake_list)

    app = cli.build_app()
    payload = _error_output(app, ["sdk", "artifacts", "get", "run-1", "--list"], capsys)
    assert payload["status"] == "error"
    assert payload["error"] == "Tangle API request failed: connection refused"


# ---------------------------------------------------------------------------
# Mode validation (fails before client construction / any network call)
# ---------------------------------------------------------------------------


def _error_output(app: Any, args: list[str], capsys: Any) -> dict[str, Any]:
    _run_expecting_error(app, args)
    return json.loads(capsys.readouterr().out)


def test_mode_validation_rejects_query_and_list_together(monkeypatch, capsys) -> None:
    client_calls = _stub_client(monkeypatch)
    app = cli.build_app()

    payload = _error_output(
        app,
        ["sdk", "artifacts", "get", "run-1", "--query", '{"artifact_ids": ["a"]}', "--list"],
        capsys,
    )
    assert payload["status"] == "error"
    assert "mutually exclusive" in payload["error"]
    # Validation short-circuits before any client is built.
    assert client_calls == []


def test_mode_validation_rejects_no_mode(monkeypatch, capsys) -> None:
    client_calls = _stub_client(monkeypatch)
    app = cli.build_app()

    payload = _error_output(app, ["sdk", "artifacts", "get", "run-1"], capsys)
    assert payload["status"] == "error"
    assert "--query is required unless --list or --out-dir is set" in payload["error"]
    assert client_calls == []


def test_mode_validation_rejects_only_without_out_dir(monkeypatch, capsys) -> None:
    client_calls = _stub_client(monkeypatch)
    app = cli.build_app()

    payload = _error_output(
        app,
        ["sdk", "artifacts", "get", "run-1", "--list", "--only", "model"],
        capsys,
    )
    assert payload["status"] == "error"
    assert "--only is only valid with --out-dir" in payload["error"]
    assert client_calls == []


def test_mode_validation_rejects_include_children_with_query(monkeypatch, capsys) -> None:
    client_calls = _stub_client(monkeypatch)
    app = cli.build_app()

    payload = _error_output(
        app,
        [
            "sdk",
            "artifacts",
            "get",
            "run-1",
            "--query",
            '{"artifact_ids": ["a"]}',
            "--include-children",
        ],
        capsys,
    )
    assert payload["status"] == "error"
    assert "--include-children is only valid with --list or --out-dir" in payload["error"]
    assert client_calls == []


def test_mode_validation_rejects_non_object_query(monkeypatch, capsys) -> None:
    client_calls = _stub_client(monkeypatch)
    app = cli.build_app()

    payload = _error_output(
        app,
        ["sdk", "artifacts", "get", "run-1", "--query", "[1, 2, 3]"],
        capsys,
    )
    assert payload["status"] == "error"
    assert "--query must be a JSON object" in payload["error"]
    assert client_calls == []


def test_empty_object_query_is_accepted_as_query_mode(monkeypatch, capsys) -> None:
    # ``--query '{}'`` is an explicit (empty) object query, not a missing
    # ``--query``: it selects query mode and succeeds with zero artifacts
    # instead of surfacing the misleading "--query is required" error.
    _stub_client(monkeypatch)
    app = cli.build_app()

    run_app(app, ["sdk", "artifacts", "get", "run-1", "--query", "{}"])

    payload = json.loads(capsys.readouterr().out)
    assert payload == {"status": "success", "run_id": "run-1", "count": 0, "artifacts": []}


def test_empty_object_query_conflicts_with_list_mode(monkeypatch, capsys) -> None:
    # An explicit empty object counts as a selected --query for the
    # mutual-exclusion check, same as any other object value.
    client_calls = _stub_client(monkeypatch)
    app = cli.build_app()

    payload = _error_output(
        app,
        ["sdk", "artifacts", "get", "run-1", "--query", "{}", "--list"],
        capsys,
    )
    assert payload["status"] == "error"
    assert "mutually exclusive" in payload["error"]
    assert client_calls == []


def test_empty_array_query_is_rejected_as_non_object(monkeypatch, capsys) -> None:
    client_calls = _stub_client(monkeypatch)
    app = cli.build_app()

    payload = _error_output(
        app,
        ["sdk", "artifacts", "get", "run-1", "--query", "[]"],
        capsys,
    )
    assert payload["status"] == "error"
    assert "--query must be a JSON object" in payload["error"]
    assert client_calls == []


@pytest.mark.parametrize(
    "query, fragment",
    [
        ('{"executions": [1]}', "--query 'executions' must be an object"),
        ('{"tasks": {"Train": "model"}}', "--query 'tasks' entry 'Train' must be a list"),
        ('{"artifact_ids": "artifact-1"}', "--query 'artifact_ids' must be a list"),
        ('{"artifact_ids": [null]}', "--query 'artifact_ids' entry 0 must be a string"),
    ],
)
def test_malformed_nested_query_reports_clean_error(
    monkeypatch, capsys, query: str, fragment: str
) -> None:
    # Malformed nested query values must exit with the JSON error contract,
    # not an AttributeError/TypeError traceback from inside the artifact walk.
    _stub_client(monkeypatch)
    app = cli.build_app()

    payload = _error_output(
        app,
        ["sdk", "artifacts", "get", "run-1", "--query", query],
        capsys,
    )
    assert payload["status"] == "error"
    assert fragment in payload["error"]


def test_null_task_entry_query_resolves_all_outputs_end_to_end(monkeypatch, capsys) -> None:
    # Regression: ``--query '{"tasks": {"train": null}}'`` against a run tree
    # where the task exists and has outputs used to escape validation and
    # crash with a raw ``TypeError`` from the artifact walk. A ``null`` filter
    # must behave like ``[]`` (all outputs) through the full command path.
    class _FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def get_run_details(self, run_id: str) -> Any:
            return {
                "execution": {
                    "task_spec": {
                        "graph_tasks": {
                            "train": {
                                "execution_output_artifacts": {"model": "artifact-model"}
                            }
                        }
                    }
                }
            }

        def artifacts_get(self, artifact_id: str) -> Any:
            assert artifact_id == "artifact-model"
            return {
                "id": artifact_id,
                "artifact_data": {"uri": "gs://bucket/model", "total_size": 3, "is_dir": False},
            }

    monkeypatch.setattr(artifacts_cli, "LazyTangleApiClient", _FakeClient)

    app = cli.build_app()
    run_app(app, ["sdk", "artifacts", "get", "run-1", "--query", '{"tasks": {"train": null}}'])

    result = json.loads(capsys.readouterr().out)
    assert result == {
        "status": "success",
        "run_id": "run-1",
        "count": 1,
        "artifacts": [
            {
                "id": "artifact-model",
                "uri": "gs://bucket/model",
                "key": "train/model",
                "total_size": 3,
                "is_dir": False,
            }
        ],
    }


# ---------------------------------------------------------------------------
# Config-file type converters
# ---------------------------------------------------------------------------


def test_config_list_flag_must_be_boolean(monkeypatch, tmp_path) -> None:
    _stub_client(monkeypatch)
    config = tmp_path / "artifacts.yaml"
    config.write_text("run_id: run-1\nlist: not-a-bool\n", encoding="utf-8")
    app = cli.build_app()

    code = _run_expecting_error(app, ["sdk", "artifacts", "get", "--config", str(config)])
    assert code not in (0, None)


def test_config_only_must_be_string_list(monkeypatch, tmp_path) -> None:
    _stub_client(monkeypatch)
    config = tmp_path / "artifacts.yaml"
    config.write_text(
        "run_id: run-1\nout_dir: /tmp/out\nonly: model\n", encoding="utf-8"
    )
    app = cli.build_app()

    code = _run_expecting_error(app, ["sdk", "artifacts", "get", "--config", str(config)])
    assert code not in (0, None)


def test_config_list_and_out_dir_from_file(monkeypatch, tmp_path, capsys) -> None:
    _stub_client(monkeypatch)
    config = tmp_path / "artifacts.yaml"
    config.write_text(
        "run_id: run-config\nlist: true\ninclude_children: true\n", encoding="utf-8"
    )
    list_calls: list[dict[str, Any]] = []

    def fake_list(self, run_id: str, *, include_children: bool) -> list[dict[str, str]]:
        list_calls.append({"run_id": run_id, "include_children": include_children})
        return []

    monkeypatch.setattr(artifacts_module.ArtifactManager, "list_result_artifacts", fake_list)

    app = cli.build_app()
    run_app(app, ["sdk", "artifacts", "get", "--config", str(config)])

    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "success"
    assert list_calls == [{"run_id": "run-config", "include_children": True}]
