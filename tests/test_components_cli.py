import json
from pathlib import Path
from typing import Any

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
        self.publish_configs: list[dict[str, Any]] = []
        self.results: list[tuple[str, ProcessingResult]] = []
        FakePublisher.instances.append(self)

    def publish_components(self, component_configs: list[dict[str, Any]]) -> int:
        self.publish_configs = component_configs
        for config in component_configs:
            result = ProcessingResult(
                outcome=ProcessingOutcome.SUCCESS,
                local_version="1.0",
                latest_version=None,
                reason=f"Dry-run: would publish {config.get('name') or 'component'}",
            )
            self.results.append((str(config["component_path"]), result))
        return 0


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
    }
    assert FakePublisher.instances[0].publish_configs == [
        {
            "component_path": component_path,
            "image": None,
            "name": "CLI Name",
            "description": None,
            "annotations": {"from_config": True},
        }
    ]


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
    assert FakePublisher.instances[0].publish_configs == [
        {
            "component_path": first,
            "image": "python:3.12",
            "name": "One Config",
            "description": None,
            "annotations": None,
        },
        {
            "component_path": second,
            "image": "python:3.12",
            "name": "Two Config",
            "description": None,
            "annotations": None,
        },
    ]


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
        {"base_url": "https://api.test", "token": None, "auth_header": None, "header": ["X-Test: yes"]}
    ]
    assert deprecate_calls == [
        {"client": fake_client, "digest": "sha256:from-config", "superseded_by": "sha256:new"}
    ]


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
    assert "slack" not in publish_help.lower()
    assert "shopify" not in publish_help.lower()
    assert "publish-all" not in publish_help

    for old_path in (["sdk", "components", "publish"], ["sdk", "components", "deprecate"], ["sdk", "components", "publish-all"]):
        with pytest.raises(SystemExit) as exc_info:
            app(old_path)
        assert exc_info.value.code != 0
