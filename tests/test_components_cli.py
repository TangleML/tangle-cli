import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from tangle_cli import cli, components_cli
from tangle_cli.component_publisher import deprecate_component, publish_component


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


class FakeClient:
    def __init__(self) -> None:
        self.published_calls: list[dict[str, Any]] = []
        self.deprecate_calls: list[dict[str, Any]] = []

    def published_components_create(self, **kwargs: Any) -> dict[str, Any]:
        self.published_calls.append(kwargs)
        return {"digest": "sha256:abc123", "name": kwargs.get("name")}

    def published_components_update(self, **kwargs: Any) -> dict[str, Any]:
        self.deprecate_calls.append(kwargs)
        return {"digest": kwargs.get("digest"), "deprecated": kwargs.get("deprecated")}


def test_publish_component_calls_generated_create_with_loaded_spec(tmp_path: Path):
    component_path = _write_component(tmp_path / "component.yaml", name="Original")
    client = FakeClient()

    result = publish_component(
        client,
        component_path,
        image="python:3.13",
        name="Published Name",
        description="Published description",
        annotations={"owner": "oss"},
    )

    assert result.status == "success"
    assert result.digest == "sha256:abc123"
    assert client.published_calls == [
        {
            "name": "Published Name",
            "text": client.published_calls[0]["text"],
        }
    ]
    payload = yaml.safe_load(client.published_calls[0]["text"])
    assert payload["name"] == "Published Name"
    assert payload["description"] == "Published description"
    assert payload["implementation"]["container"]["image"] == "python:3.13"
    assert payload["metadata"]["annotations"]["owner"] == "oss"
    assert "published_at" in payload["metadata"]["annotations"]


def test_publish_component_dry_run_does_not_call_api(tmp_path: Path):
    component_path = _write_component(tmp_path / "component.yaml", name="Dry Run")
    client = FakeClient()

    result = publish_component(client, component_path, dry_run=True)

    assert result.status == "dry_run"
    assert result.dry_run is True
    assert client.published_calls == []
    assert result.response["name"] == "Dry Run"


def test_deprecate_component_calls_generated_update():
    client = FakeClient()

    result = deprecate_component(client, "sha256:old", superseded_by="sha256:new")

    assert result.status == "success"
    assert client.deprecate_calls == [
        {"digest": "sha256:old", "deprecated": True, "superseded_by": "sha256:new"}
    ]


def test_components_publish_cli_wiring_and_config_precedence(monkeypatch, tmp_path: Path, capsys):
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
    calls: list[dict[str, Any]] = []

    def fake_client_from_options(**kwargs: Any) -> object:
        raise AssertionError("dry-run publish must not create an API client")

    def fake_publish_component(client: object, component_path: Path, **kwargs: Any) -> dict[str, Any]:
        calls.append({"client": client, "component_path": component_path, **kwargs})
        return {"status": "dry_run", "name": kwargs["name"], "dry_run": kwargs["dry_run"]}

    monkeypatch.setattr(components_cli, "_client_from_options", fake_client_from_options)
    monkeypatch.setattr(components_cli, "publish_component", fake_publish_component)

    app = cli.build_app()
    run_app(
        app,
        ["sdk", "components", "publish", "--config", str(config), "--name", "CLI Name"],
    )

    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "dry_run"
    assert result["name"] == "CLI Name"
    assert calls == [
        {
            "client": None,
            "component_path": component_path,
            "image": None,
            "name": "CLI Name",
            "description": None,
            "annotations": {"from_config": True},
            "dry_run": True,
            "git_remote_sha": None,
            "git_remote_branch": None,
            "git_remote_url": None,
            "git_root": None,
        }
    ]


def test_components_deprecate_cli_wiring_and_config(monkeypatch, tmp_path: Path, capsys):
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
        return {"status": "success", "digest": digest, "superseded_by": kwargs.get("superseded_by")}

    monkeypatch.setattr(components_cli, "_client_from_options", fake_client_from_options)
    monkeypatch.setattr(components_cli, "deprecate_component", fake_deprecate_component)

    app = cli.build_app()
    run_app(app, ["sdk", "components", "deprecate", "--config", str(config)])

    result = json.loads(capsys.readouterr().out)
    assert result == {
        "digest": "sha256:from-config",
        "status": "success",
        "superseded_by": "sha256:new",
    }
    assert client_calls == [
        {"base_url": "https://api.test", "token": None, "auth_header": None, "header": ["X-Test: yes"]}
    ]
    assert deprecate_calls == [
        {"client": fake_client, "digest": "sha256:from-config", "superseded_by": "sha256:new"}
    ]


def test_components_help_excludes_publish_all_and_shopify_slack_options(capsys):
    app = cli.build_app()

    run_app(app, ["sdk", "components", "--help"])
    output = capsys.readouterr().out
    assert "publish" in output
    assert "deprecate" in output
    assert "publish-all" not in output

    run_app(app, ["sdk", "components", "publish", "--help"])
    publish_help = capsys.readouterr().out
    assert "slack" not in publish_help.lower()
    assert "shopify" not in publish_help.lower()
    assert "publish-all" not in publish_help

    with pytest.raises(SystemExit) as exc_info:
        app(["sdk", "components", "publish-all"])
    assert exc_info.value.code != 0
