from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from tangle_api.generated.models import ComponentSpec

from tangle_cli.component_publisher import (
    ComponentPublishContext,
    ComponentPublisher,
    ProcessingOutcome,
    ProcessingResult,
    deprecate_component,
    deprecate_old_components,
    perform_version_check,
    publish_component_to_tangle,
)
from tangle_cli.logger import CaptureLogger, NullLogger


@dataclass
class User:
    id: str


@dataclass
class ExistingComponent:
    digest: str
    name: str = "demo"


class FakeClient:
    def __init__(self) -> None:
        self.user: User | None = User("alice@example.com")
        self.existing: list[ExistingComponent] = []
        self.component_versions: dict[str, str] = {}
        self.publish_response: dict[str, Any] = {"digest": "sha256:new"}
        self.users_me_calls = 0
        self.find_calls: list[dict[str, Any]] = []
        self.create_calls: list[dict[str, Any]] = []
        self.update_calls: list[dict[str, Any]] = []

    def users_me(self) -> User | None:
        self.users_me_calls += 1
        return self.user

    def find_existing_components(self, components: Any, **kwargs: Any) -> list[ExistingComponent]:
        self.find_calls.append({"components": list(components), **kwargs})
        return self.existing

    def get_component_spec(self, digest: str) -> ComponentSpec:
        version = self.component_versions[digest]
        return ComponentSpec.from_yaml(f"name: demo\nmetadata:\n  annotations:\n    version: '{version}'\n")

    def published_components_create(self, **kwargs: Any) -> dict[str, Any]:
        self.create_calls.append(kwargs)
        return self.publish_response

    def published_components_update(self, **kwargs: Any) -> dict[str, Any]:
        self.update_calls.append(kwargs)
        return {"digest": kwargs["digest"], "deprecated": kwargs.get("deprecated")}


def write_component(path: Path, *, name: str = "demo", version: str | None = "1.0") -> Path:
    annotations = {} if version is None else {"version": version}
    path.write_text(
        yaml.safe_dump(
            {
                "name": name,
                "metadata": {"annotations": annotations},
                "implementation": {"container": {"image": "python:3.12"}},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def test_publish_file_read_error() -> None:
    result = publish_component_to_tangle("/nonexistent/file.yaml", dry_run=True)

    assert result.outcome == ProcessingOutcome.ERROR
    assert result.reason is not None and "Failed to read file" in result.reason
    assert result.local_version is None
    assert result.latest_version is None


def test_publish_no_version_in_yaml_skips(tmp_path: Path) -> None:
    component_path = write_component(tmp_path / "component.yaml", version=None)

    result = publish_component_to_tangle(component_path, dry_run=True)

    assert result.outcome == ProcessingOutcome.SKIP
    assert result.reason is not None and "Component version is required" in result.reason


def test_publish_yaml_parsing_error(tmp_path: Path) -> None:
    component_path = tmp_path / "component.yaml"
    component_path.write_text("invalid: yaml: content:", encoding="utf-8")

    result = publish_component_to_tangle(component_path, dry_run=True)

    assert result.outcome == ProcessingOutcome.ERROR
    assert result.reason is not None


def test_client_factory_lazily_creates_downstream_client(tmp_path: Path) -> None:
    component_path = write_component(tmp_path / "component.yaml")
    client = FakeClient()
    calls = []

    def client_factory() -> FakeClient:
        calls.append("created")
        return client

    publisher = ComponentPublisher(client_factory=client_factory)

    assert calls == []
    result = publisher.publish_component(component_path)

    assert result.outcome == ProcessingOutcome.SUCCESS
    assert calls == ["created"]
    assert client.create_calls


def test_client_factory_is_not_called_for_dry_run(tmp_path: Path) -> None:
    component_path = write_component(tmp_path / "component.yaml")
    calls = []

    def client_factory() -> FakeClient:
        calls.append("created")
        return FakeClient()

    result = publish_component_to_tangle(component_path, dry_run=True, client_factory=client_factory)

    assert result.outcome == ProcessingOutcome.SUCCESS
    assert calls == []


def test_client_creation_failure(monkeypatch, tmp_path: Path) -> None:
    component_path = write_component(tmp_path / "component.yaml")

    def fake_get_client(self: ComponentPublisher) -> None:
        return None

    monkeypatch.setattr(ComponentPublisher, "_get_client", fake_get_client)
    result = ComponentPublisher(dry_run=False).publish_component(component_path)

    assert result.outcome == ProcessingOutcome.ERROR
    assert result.reason == "Failed to create TangleApiClient"


def test_dry_run_success_does_not_call_api(tmp_path: Path) -> None:
    component_path = write_component(tmp_path / "component.yaml", name="dry")
    client = FakeClient()

    result = publish_component_to_tangle(
        component_path,
        dry_run=True,
        client=client,
        git_remote_sha="abc123",
        git_remote_branch="main",
    )

    assert result.outcome == ProcessingOutcome.SUCCESS
    assert result.reason is not None and "Dry-run: would publish" in result.reason
    assert result.local_version == "1.0"
    assert client.create_calls == []
    assert result.spec.annotations["git_remote_sha"] == "abc123"
    assert result.spec.annotations["git_remote_branch"] == "main"


def test_version_check_filters_by_current_author() -> None:
    spec = ComponentSpec.from_yaml("name: demo\nmetadata:\n  annotations:\n    version: '1.0'\n")
    client = FakeClient()

    result = perform_version_check(spec=spec, dry_run=False, client=client)

    assert result.outcome == ProcessingOutcome.PROCEED
    assert client.find_calls == [
        {
            "components": ["demo", "[Official] demo"],
            "verbose": False,
            "published_by": "alice@example.com",
        }
    ]


def test_version_check_fails_closed_without_current_user() -> None:
    spec = ComponentSpec.from_yaml("name: demo\nmetadata:\n  annotations:\n    version: '1.0'\n")
    client = FakeClient()
    client.user = None

    result = perform_version_check(spec=spec, dry_run=False, client=client)

    assert result.outcome == ProcessingOutcome.ERROR
    assert result.reason == "Cannot determine current user for author filtering"
    assert client.find_calls == []


def test_version_check_skips_unchanged_owner_scoped_version() -> None:
    spec = ComponentSpec.from_yaml("name: demo\nmetadata:\n  annotations:\n    version: '1.0'\n")
    client = FakeClient()
    client.existing = [ExistingComponent("sha256:old")]
    client.component_versions = {"sha256:old": "1.0"}

    result = perform_version_check(spec=spec, dry_run=False, client=client)

    assert result.outcome == ProcessingOutcome.SKIP
    assert result.latest_version == "1.0"
    assert "unchanged" in (result.reason or "")


def test_version_check_progress_uses_logger_not_tangle_verbose(monkeypatch, capsys) -> None:
    spec = ComponentSpec.from_yaml("name: demo\nmetadata:\n  annotations:\n    version: '1.0'\n")
    client = FakeClient()
    client.existing = [ExistingComponent("sha256:old")]
    client.component_versions = {"sha256:old": "1.0"}

    monkeypatch.setenv("TANGLE_VERBOSE", "1")
    result = perform_version_check(
        spec=spec,
        dry_run=False,
        client=client,
        logger=NullLogger(),
    )

    assert result.outcome == ProcessingOutcome.SKIP
    assert capsys.readouterr().err == ""

    monkeypatch.setenv("TANGLE_VERBOSE", "0")
    capture = CaptureLogger()
    result = perform_version_check(
        spec=spec,
        dry_run=False,
        client=client,
        logger=capture,
    )

    assert result.outcome == ProcessingOutcome.SKIP
    logs = capture.get_logs() or ""
    assert "Local version: 1.0" in logs
    assert "Remote version: 1.0" in logs
    assert "Skipping: Version 1.0 unchanged" in logs


def test_successful_publish_deprecates_owner_scoped_old_versions(tmp_path: Path) -> None:
    component_path = write_component(tmp_path / "component.yaml", name="demo", version="1.1")
    client = FakeClient()
    client.existing = [ExistingComponent("sha256:old")]
    client.component_versions = {"sha256:old": "1.0"}
    client.publish_response = {"digest": "sha256:newer"}

    result = publish_component_to_tangle(
        component_path,
        client=client,
        image="python:3.13",
        name="Published Name",
        description="Published description",
        annotations={"owner": "oss"},
    )

    assert result.outcome == ProcessingOutcome.SUCCESS
    assert result.digest == "sha256:newer"
    assert client.create_calls and client.create_calls[0]["name"] == "Published Name"
    payload = yaml.safe_load(client.create_calls[0]["text"])
    assert payload["name"] == "Published Name"
    assert payload["description"] == "Published description"
    assert payload["implementation"]["container"]["image"] == "python:3.13"
    assert payload["metadata"]["annotations"]["owner"] == "oss"
    assert "published_at" in payload["metadata"]["annotations"]
    assert client.update_calls == [
        {"digest": "sha256:old", "deprecated": True, "superseded_by": "sha256:newer"},
    ]


def test_publish_error_when_no_digest_returned(tmp_path: Path) -> None:
    component_path = write_component(tmp_path / "component.yaml")
    client = FakeClient()
    client.publish_response = {"name": "demo"}

    result = publish_component_to_tangle(component_path, client=client)

    assert result.outcome == ProcessingOutcome.ERROR
    assert result.reason == "Component published but no digest returned"


def test_deprecate_old_components_skips_new_digest() -> None:
    client = FakeClient()

    count = deprecate_old_components(
        [ExistingComponent("sha256:old"), ExistingComponent("sha256:new")],
        "sha256:new",
        client=client,
    )

    assert count == 1
    assert client.update_calls == [
        {"digest": "sha256:old", "deprecated": True, "superseded_by": "sha256:new"}
    ]


def test_deprecate_component_calls_generated_update() -> None:
    client = FakeClient()

    result = deprecate_component(client, "sha256:old", superseded_by="sha256:new")

    assert result["success"] is True
    assert client.update_calls == [
        {"digest": "sha256:old", "deprecated": True, "superseded_by": "sha256:new"}
    ]


class RecordingHook:
    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []

    def before_batch(self, components_config: list[dict[str, Any]]) -> None:
        self.events.append(("before", len(components_config)))

    def after_component(self, component_path: str, result: ProcessingResult) -> None:
        self.events.append(("component", component_path, result.outcome.value))

    def after_batch(self, results: list[tuple[str, ProcessingResult]]) -> None:
        self.events.append(("after", len(results)))


class ContextHook:
    def __init__(self) -> None:
        self.contexts: list[ComponentPublishContext] = []

    def before_batch(self, components_config: list[dict[str, Any]], *, context: ComponentPublishContext) -> None:
        self.contexts.append(context)

    def after_component(
        self,
        component_path: str,
        result: ProcessingResult,
        *,
        context: ComponentPublishContext,
    ) -> None:
        self.contexts.append(context)

    def after_batch(
        self,
        results: list[tuple[str, ProcessingResult]],
        *,
        context: ComponentPublishContext,
    ) -> None:
        self.contexts.append(context)


class KwargsContextHook:
    def __init__(self) -> None:
        self.contexts: list[ComponentPublishContext] = []

    def after_batch(self, results: list[tuple[str, ProcessingResult]], **kwargs: Any) -> None:
        self.contexts.append(kwargs["context"])


def test_publish_components_passes_structured_context_to_context_aware_hooks(tmp_path: Path) -> None:
    component_path = write_component(tmp_path / "component.yaml", name="demo", version="1.0")
    hook = ContextHook()
    kwargs_hook = KwargsContextHook()
    publisher = ComponentPublisher(
        dry_run=True,
        hooks=[hook, kwargs_hook],
        git_remote_sha="sha",
        git_remote_branch="main",
        git_remote_url="https://github.com/Shopify/discovery",
        git_repo="Shopify/discovery",
        git_root=tmp_path,
        published_by="alice@example.com",
    )

    exit_code = publisher.publish_components([{"component_path": component_path, "name": "Demo"}])

    assert exit_code == 0
    before_context, component_context, after_context = hook.contexts
    assert before_context.git_remote_sha == "sha"
    assert before_context.git_remote_branch == "main"
    assert before_context.git_remote_url == "https://github.com/Shopify/discovery"
    assert before_context.git_repo == "Shopify/discovery"
    assert before_context.git_root == str(tmp_path)
    assert before_context.published_by == "alice@example.com"
    assert before_context.batch_config == [{"component_path": component_path, "name": "Demo"}]
    assert component_context.component_path == str(component_path)
    assert component_context.component_config == {"component_path": component_path, "name": "Demo"}
    assert component_context.result is publisher.results[0][1]
    assert component_context.results == tuple(publisher.results)
    assert after_context.results == tuple(publisher.results)
    assert kwargs_hook.contexts == [after_context]


def test_publish_components_batches_configs_and_runs_hooks(tmp_path: Path) -> None:
    first = write_component(tmp_path / "one.yaml", name="one", version="1.0")
    second = write_component(tmp_path / "two.yaml", name="two", version="2.0")
    client = FakeClient()
    hook = RecordingHook()
    publisher = ComponentPublisher(dry_run=True, client=client, hooks=[hook])

    exit_code = publisher.publish_components(
        [
            {"component_path": first, "image": "python:3.12"},
            {"component_path": second, "name": "Two"},
        ]
    )

    assert exit_code == 0
    assert len(publisher.results) == 2
    assert [result.outcome for _, result in publisher.results] == [
        ProcessingOutcome.SUCCESS,
        ProcessingOutcome.SUCCESS,
    ]
    assert hook.events == [
        ("before", 2),
        ("component", str(first), "success"),
        ("component", str(second), "success"),
        ("after", 2),
    ]


def test_publish_components_returns_nonzero_for_errors(tmp_path: Path) -> None:
    component_path = write_component(tmp_path / "component.yaml")
    publisher = ComponentPublisher(dry_run=True)

    exit_code = publisher.publish_components([
        {"component_path": component_path},
        {},
    ])

    assert exit_code == 1
    assert [result.outcome for _, result in publisher.results] == [
        ProcessingOutcome.SUCCESS,
        ProcessingOutcome.ERROR,
    ]
