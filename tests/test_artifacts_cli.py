from __future__ import annotations

import builtins
import importlib
import json
import sys
from typing import Any

from tangle_cli import artifacts as artifacts_module
from tangle_cli import artifacts_cli, cli


def run_app(app: Any, args: list[str]) -> None:
    try:
        app(args)
    except SystemExit as exc:
        if exc.code not in (0, None):
            raise


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

    assert "Native generated Tangle API bindings are required for artifact commands" in message
    assert "Install tangle-cli[native]" in message


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
