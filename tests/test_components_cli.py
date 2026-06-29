import builtins
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import ANY

import pytest
import yaml

from tangle_cli import cli, published_components_cli
from tangle_cli.component_publisher import ProcessingOutcome, ProcessingResult


def run_app(app, args: list[str]) -> None:
    try:
        app(args)
    except SystemExit as exc:
        if exc.code not in (0, None):
            raise


def _write_component(path: Path, *, name: str = "demo", version: str = "1.0") -> Path:
    path.write_text(
        yaml.safe_dump(
            {
                "name": name,
                "metadata": {"annotations": {"version": version}},
                "implementation": {"container": {"image": "python:3.12"}},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


class FakePublisher:
    instances: list["FakePublisher"] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.publish_calls: list[dict[str, Any]] = []
        FakePublisher.instances.append(self)

    def publish_component(self, component_path: Path, **kwargs: Any) -> ProcessingResult:
        self.publish_calls.append({"component_path": component_path, **kwargs})
        return ProcessingResult(
            outcome=ProcessingOutcome.SUCCESS,
            local_version="1.0",
            latest_version=None,
            reason=f"Dry-run: would publish {kwargs.get('name') or 'component'}",
        )


def test_published_components_publish_cli_wiring_and_config_precedence(monkeypatch, tmp_path: Path, capsys):
    component_path = _write_component(tmp_path / "component.yaml", name="Config Name")
    config = tmp_path / "publish.yaml"
    config.write_text(
        f"component_path: {component_path}\n"
        "dry_run: true\n"
        "name: Config Name\n"
        "annotations:\n"
        "  from_config: yes\n",
        encoding="utf-8",
    )

    def fake_client_from_options(**kwargs: Any) -> object:
        raise AssertionError("dry-run publish must not create an API client")

    FakePublisher.instances = []
    monkeypatch.setattr(published_components_cli, "_client_from_options", fake_client_from_options)
    monkeypatch.setattr(published_components_cli, "ComponentPublisher", FakePublisher)

    app = cli.build_app()
    run_app(
        app,
        ["sdk", "published-components", "publish", "--config", str(config), "--name", "CLI Name"],
    )

    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "success"
    assert result["components_count"] == 1
    assert FakePublisher.instances[0].kwargs == {
        "dry_run": True,
        "git_remote_sha": None,
        "git_remote_branch": None,
        "git_remote_url": None,
        "git_root": None,
        "published_by": None,
        "client": None,
        "logger": ANY,
    }
    assert FakePublisher.instances[0].publish_calls == [
        {
            "component_path": component_path,
            "image": None,
            "name": "CLI Name",
            "description": None,
            "annotations": {"from_config": True},
        }
    ]


def test_published_components_publish_config_base_url_suppresses_env_credentials(monkeypatch, tmp_path: Path, capsys):
    component_path = _write_component(tmp_path / "component.yaml", name="Config Name")
    config = tmp_path / "publish.yaml"
    config.write_text(
        f"component_path: {component_path}\n"
        "base_url: https://config.example\n"
        "token: config-token\n"
        "auth_header: Bearer config-auth\n"
        "header:\n"
        "  - 'X-Config: yes'\n",
        encoding="utf-8",
    )
    fake_client = object()
    client_calls: list[dict[str, Any]] = []
    FakePublisher.instances = []

    def fake_client_from_options(**kwargs: Any) -> object:
        client_calls.append(kwargs)
        return fake_client

    monkeypatch.setattr(published_components_cli, "_client_from_options", fake_client_from_options)
    monkeypatch.setattr(published_components_cli, "ComponentPublisher", FakePublisher)

    app = cli.build_app()
    run_app(app, ["sdk", "published-components", "publish", "--config", str(config)])

    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "success"
    assert client_calls == [
        {
            "base_url": "https://config.example",
            "token": "config-token",
            "auth_header": "Bearer config-auth",
            "header": ["X-Config: yes"],
            "include_env_credentials": False,
            "command_name": "published-component commands",
        }
    ]
    assert FakePublisher.instances[0].kwargs["client"] is fake_client


def test_published_components_publish_cli_base_url_keeps_env_credentials(monkeypatch, tmp_path: Path, capsys):
    component_path = _write_component(tmp_path / "component.yaml", name="Config Name")
    config = tmp_path / "publish.yaml"
    config.write_text(
        f"component_path: {component_path}\n"
        "base_url: https://config.example\n",
        encoding="utf-8",
    )
    client_calls: list[dict[str, Any]] = []
    FakePublisher.instances = []

    def fake_client_from_options(**kwargs: Any) -> object:
        client_calls.append(kwargs)
        return object()

    monkeypatch.setattr(published_components_cli, "_client_from_options", fake_client_from_options)
    monkeypatch.setattr(published_components_cli, "ComponentPublisher", FakePublisher)

    app = cli.build_app()
    run_app(
        app,
        [
            "sdk",
            "published-components",
            "publish",
            "--config",
            str(config),
            "--base-url",
            "https://cli.example",
        ],
    )

    json.loads(capsys.readouterr().out)
    assert client_calls[-1]["base_url"] == "https://cli.example"
    assert client_calls[-1]["include_env_credentials"] is True


def test_published_components_publish_config_array_is_batch_interface(monkeypatch, tmp_path: Path, capsys):
    first = _write_component(tmp_path / "one.yaml", name="One")
    second = _write_component(tmp_path / "two.yaml", name="Two")
    config = tmp_path / "publish-many.yaml"
    config.write_text(
        "_defaults:\n"
        "  dry_run: true\n"
        "  image: python:3.12\n"
        "configs:\n"
        f"  - component_path: {first}\n"
        "    name: One Config\n"
        f"  - component_path: {second}\n"
        "    name: Two Config\n",
        encoding="utf-8",
    )

    FakePublisher.instances = []
    monkeypatch.setattr(published_components_cli, "ComponentPublisher", FakePublisher)

    app = cli.build_app()
    run_app(app, ["sdk", "published-components", "publish", "--config", str(config)])

    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "success"
    assert result["components_count"] == 2
    assert [publisher.publish_calls for publisher in FakePublisher.instances] == [
        [
            {
                "component_path": first,
                "image": "python:3.12",
                "name": "One Config",
                "description": None,
                "annotations": None,
            }
        ],
        [
            {
                "component_path": second,
                "image": "python:3.12",
                "name": "Two Config",
                "description": None,
                "annotations": None,
            }
        ],
    ]


def test_published_components_publish_config_array_uses_per_entry_controls(monkeypatch, tmp_path: Path, capsys):
    first = _write_component(tmp_path / "one.yaml", name="One")
    second = _write_component(tmp_path / "two.yaml", name="Two")
    config = tmp_path / "publish-controls.yaml"
    config.write_text(
        "configs:\n"
        f"  - component_path: {first}\n"
        "    base_url: https://first.example\n"
        "    token: first-token\n"
        "    published_by: first@example.com\n"
        "    git_remote_sha: first-sha\n"
        f"  - component_path: {second}\n"
        "    dry_run: true\n"
        "    base_url: https://second.example\n"
        "    token: second-token\n"
        "    published_by: second@example.com\n"
        "    git_remote_sha: second-sha\n",
        encoding="utf-8",
    )
    fake_client = object()
    client_calls: list[dict[str, Any]] = []
    FakePublisher.instances = []

    def fake_client_from_options(**kwargs: Any) -> object:
        client_calls.append(kwargs)
        return fake_client

    monkeypatch.setattr(published_components_cli, "_client_from_options", fake_client_from_options)
    monkeypatch.setattr(published_components_cli, "ComponentPublisher", FakePublisher)

    app = cli.build_app()
    run_app(app, ["sdk", "published-components", "publish", "--config", str(config)])

    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "success"
    assert result["components_count"] == 2
    assert client_calls == [
        {
            "base_url": "https://first.example",
            "token": "first-token",
            "auth_header": None,
            "header": None,
            "include_env_credentials": False,
            "command_name": "published-component commands",
        }
    ]
    assert [publisher.kwargs for publisher in FakePublisher.instances] == [
        {
            "dry_run": False,
            "git_remote_sha": "first-sha",
            "git_remote_branch": None,
            "git_remote_url": None,
            "git_root": None,
            "published_by": "first@example.com",
            "client": fake_client,
            "logger": ANY,
        },
        {
            "dry_run": True,
            "git_remote_sha": "second-sha",
            "git_remote_branch": None,
            "git_remote_url": None,
            "git_root": None,
            "published_by": "second@example.com",
            "client": None,
            "logger": ANY,
        },
    ]
    assert FakePublisher.instances[0].publish_calls[0]["component_path"] == first
    assert FakePublisher.instances[1].publish_calls[0]["component_path"] == second


def test_published_components_deprecate_cli_wiring_and_config(monkeypatch, tmp_path: Path, capsys):
    config = tmp_path / "deprecate.yaml"
    config.write_text(
        "digest: sha256:from-config\n"
        "superseded_by: sha256:new\n"
        "base_url: https://api.test\n"
        "header:\n"
        "  - 'X-Test: yes'\n",
        encoding="utf-8",
    )
    fake_client = object()
    client_calls: list[dict[str, Any]] = []
    deprecate_calls: list[dict[str, Any]] = []

    def fake_client_from_options(**kwargs: Any) -> object:
        client_calls.append(kwargs)
        return fake_client

    def fake_deprecate_component(client: object, digest: str, **kwargs: Any) -> dict[str, Any]:
        deprecate_calls.append({"client": client, "digest": digest, **kwargs})
        return {"success": True, "digest": digest, "superseded_by": kwargs.get("superseded_by")}

    monkeypatch.setattr(published_components_cli, "_client_from_options", fake_client_from_options)
    monkeypatch.setattr(published_components_cli, "deprecate_component", fake_deprecate_component)

    app = cli.build_app()
    run_app(app, ["sdk", "published-components", "deprecate", "--config", str(config)])

    result = json.loads(capsys.readouterr().out)
    assert result == {
        "digest": "sha256:from-config",
        "success": True,
        "superseded_by": "sha256:new",
    }
    assert client_calls == [
        {
            "base_url": "https://api.test",
            "token": None,
            "auth_header": None,
            "header": ["X-Test: yes"],
            "include_env_credentials": False,
            "command_name": "published-component commands",
        }
    ]
    assert deprecate_calls == [
        {
            "client": fake_client,
            "digest": "sha256:from-config",
            "superseded_by": "sha256:new",
            "logger": ANY,
        }
    ]


def test_published_components_missing_native_api_uses_friendly_error(monkeypatch):
    import tangle_cli

    for attr in ("component_inspector", "client", "models"):
        if hasattr(tangle_cli, attr):
            monkeypatch.delattr(tangle_cli, attr)
    for name in list(sys.modules):
        if name in {
            "tangle_cli.component_inspector",
            "tangle_cli.client",
            "tangle_cli.models",
        } or name.startswith("tangle_api"):
            monkeypatch.delitem(sys.modules, name, raising=False)

    original_import = builtins.__import__

    def guarded_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "tangle_api" or name.startswith("tangle_api."):
            raise ModuleNotFoundError("No module named 'tangle_api'", name="tangle_api")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    app = cli.build_app()

    for command in (
        ["sdk", "published-components", "search", "demo"],
        ["sdk", "published-components", "inspect", "demo"],
        ["sdk", "published-components", "library"],
    ):
        with pytest.raises(SystemExit) as exc_info:
            app(command)
        message = str(exc_info.value)
        assert "Native generated Tangle API bindings are required for published-component commands" in message
        assert "Install tangle-cli[native]" in message


def test_published_components_publish_log_type_none_suppresses_progress(tmp_path: Path, capsys):
    component_path = _write_component(tmp_path / "component.yaml", name="Quiet")
    app = cli.build_app()

    run_app(
        app,
        [
            "sdk",
            "published-components",
            "publish",
            str(component_path),
            "--dry-run",
            "--log-type",
            "none",
        ],
    )

    captured = capsys.readouterr()
    assert json.loads(captured.out)["status"] == "success"
    assert captured.err == ""


def test_components_and_published_components_help_reflect_api_split(capsys):
    app = cli.build_app()

    run_app(app, ["sdk", "components", "--help"])
    output = capsys.readouterr().out
    assert "generate" in output
    assert "bump-version" in output
    assert "publish" not in output
    assert "deprecate" not in output
    assert "publish-all" not in output
    assert "base-url" not in output
    assert "auth-header" not in output
    assert "slack" not in output.lower()
    assert "shopify" not in output.lower()

    run_app(app, ["sdk", "published-components", "--help"])
    published_output = capsys.readouterr().out
    assert "search" in published_output
    assert "inspect" in published_output
    assert "library" in published_output
    assert "publish" in published_output
    assert "deprecate" in published_output
    assert "publish-all" not in published_output
    assert "slack" not in published_output.lower()
    assert "shopify" not in published_output.lower()

    run_app(app, ["sdk", "published-components", "publish", "--help"])
    publish_help = capsys.readouterr().out
    assert "base-url" in publish_help
    assert "auth-header" in publish_help
    assert "published-by" in publish_help
    assert "--log-type" in publish_help
    assert "slack" not in publish_help.lower()
    assert "shopify" not in publish_help.lower()
    assert "publish-all" not in publish_help

    for old_path in (["sdk", "components", "publish"], ["sdk", "components", "deprecate"], ["sdk", "components", "publish-all"]):
        with pytest.raises(SystemExit) as exc_info:
            app(old_path)
        assert exc_info.value.code != 0
