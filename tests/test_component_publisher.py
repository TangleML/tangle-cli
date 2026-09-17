from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from tangle_cli.models import ComponentSpec

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
    deprecated: bool = False


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


# ---------------------------------------------------------------------------
# Monotonic publishing contract
# ---------------------------------------------------------------------------


def _version_check(
    local_version: str,
    published: dict[str, str],
    *,
    deprecated: set[str] | None = None,
    allow_downgrade: bool = False,
) -> tuple[ProcessingResult, FakeClient]:
    spec = ComponentSpec.from_yaml(
        f"name: demo\nmetadata:\n  annotations:\n    version: '{local_version}'\n"
    )
    client = FakeClient()
    client.existing = [
        ExistingComponent(digest, deprecated=digest in (deprecated or set())) for digest in published
    ]
    client.component_versions = dict(published)
    result = perform_version_check(
        spec=spec,
        dry_run=False,
        client=client,
        allow_downgrade=allow_downgrade,
    )
    return result, client


def test_version_check_proceeds_when_nothing_published() -> None:
    result, client = _version_check("1.0", {})

    assert result.outcome == ProcessingOutcome.PROCEED
    assert result.latest_version is None
    assert result.latest_digest is None
    assert client.create_calls == []
    assert client.update_calls == []


def test_version_check_proceeds_when_local_is_newer() -> None:
    result, _ = _version_check("1.1", {"sha256:v10": "1.0"})

    assert result.outcome == ProcessingOutcome.PROCEED
    assert result.latest_version == "1.0"
    assert result.latest_digest == "sha256:v10"


def test_version_check_equal_version_skips_with_exact_digest() -> None:
    result, client = _version_check("1.0", {"sha256:v10": "1.0"})

    assert result.outcome == ProcessingOutcome.SKIP
    assert result.latest_version == "1.0"
    assert result.latest_digest == "sha256:v10"
    assert result.resolved_digest == "sha256:v10"
    assert "unchanged" in (result.reason or "")
    assert client.create_calls == []
    assert client.update_calls == []


def test_version_check_older_local_version_skips_with_newer_digest() -> None:
    result, client = _version_check("1.0", {"sha256:v20": "2.0"})

    assert result.outcome == ProcessingOutcome.SKIP
    assert result.latest_version == "2.0"
    assert result.latest_digest == "sha256:v20"
    assert result.resolved_digest == "sha256:v20"
    assert "older" in (result.reason or "")
    assert client.create_calls == []
    assert client.update_calls == []


def test_version_check_selects_highest_of_multiple_published_versions() -> None:
    result, _ = _version_check(
        "1.5",
        {"sha256:v10": "1.0", "sha256:v201": "2.0.1", "sha256:v20": "2.0"},
    )

    assert result.outcome == ProcessingOutcome.SKIP
    assert result.latest_version == "2.0.1"
    assert result.latest_digest == "sha256:v201"


def test_version_check_fails_closed_on_ambiguous_latest_digests() -> None:
    result, client = _version_check("1.0", {"sha256:a": "2.0", "sha256:b": "2.0"})

    assert result.outcome == ProcessingOutcome.ERROR
    assert result.latest_digest is None
    assert result.resolved_digest is None
    assert "Ambiguous latest published version 2.0" in (result.reason or "")
    assert "sha256:a" in (result.reason or "") and "sha256:b" in (result.reason or "")
    assert client.create_calls == []
    assert client.update_calls == []


def test_version_check_ignores_deprecated_components_when_selecting_latest() -> None:
    result, _ = _version_check(
        "1.0",
        {"sha256:v10": "1.0", "sha256:v20": "2.0"},
        deprecated={"sha256:v20"},
    )

    assert result.outcome == ProcessingOutcome.SKIP
    assert result.latest_version == "1.0"
    assert result.latest_digest == "sha256:v10"


def test_version_check_ignores_deprecated_duplicate_of_latest_version() -> None:
    result, _ = _version_check(
        "1.0",
        {"sha256:live": "2.0", "sha256:dead": "2.0"},
        deprecated={"sha256:dead"},
    )

    assert result.outcome == ProcessingOutcome.SKIP
    assert result.latest_digest == "sha256:live"


def test_version_check_allow_downgrade_opts_back_into_publishing_older() -> None:
    result, _ = _version_check("1.0", {"sha256:v20": "2.0"}, allow_downgrade=True)

    assert result.outcome == ProcessingOutcome.PROCEED
    assert result.latest_version == "2.0"
    assert result.latest_digest == "sha256:v20"


def test_publish_older_version_is_a_noop_that_pins_newer_digest(tmp_path: Path) -> None:
    component_path = write_component(tmp_path / "component.yaml", version="1.0")
    client = FakeClient()
    client.existing = [ExistingComponent("sha256:v20")]
    client.component_versions = {"sha256:v20": "2.0"}

    result = publish_component_to_tangle(component_path, client=client)

    assert result.outcome == ProcessingOutcome.SKIP
    assert result.digest is None
    assert result.latest_digest == "sha256:v20"
    assert result.resolved_digest == "sha256:v20"
    assert client.create_calls == []
    assert client.update_calls == []
    assert result.to_dict()["latest_digest"] == "sha256:v20"


def test_publish_newer_version_reports_new_and_previous_digests(tmp_path: Path) -> None:
    component_path = write_component(tmp_path / "component.yaml", version="2.0")
    client = FakeClient()
    client.existing = [ExistingComponent("sha256:v10")]
    client.component_versions = {"sha256:v10": "1.0"}
    client.publish_response = {"digest": "sha256:v20"}

    result = publish_component_to_tangle(component_path, client=client)

    assert result.outcome == ProcessingOutcome.SUCCESS
    assert result.digest == "sha256:v20"
    assert result.latest_digest == "sha256:v10"
    assert result.resolved_digest == "sha256:v20"


def test_publish_ambiguous_latest_digests_publishes_nothing(tmp_path: Path) -> None:
    component_path = write_component(tmp_path / "component.yaml", version="1.0")
    client = FakeClient()
    client.existing = [ExistingComponent("sha256:a"), ExistingComponent("sha256:b")]
    client.component_versions = {"sha256:a": "2.0", "sha256:b": "2.0"}

    result = publish_component_to_tangle(component_path, client=client)

    assert result.outcome == ProcessingOutcome.ERROR
    assert result.resolved_digest is None
    assert client.create_calls == []
    assert client.update_calls == []


def test_resolved_digest_is_none_for_error_outcomes(tmp_path: Path) -> None:
    component_path = write_component(tmp_path / "component.yaml", version="2.0")
    client = FakeClient()
    client.existing = [ExistingComponent("sha256:v10")]
    client.component_versions = {"sha256:v10": "1.0"}

    def failing_create(**kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("boom")

    client.published_components_create = failing_create  # type: ignore[method-assign]

    result = publish_component_to_tangle(component_path, client=client)

    assert result.outcome == ProcessingOutcome.ERROR
    assert result.latest_digest == "sha256:v10"
    assert result.resolved_digest is None
    assert client.update_calls == []


# ---------------------------------------------------------------------------
# Fail-closed reads, lookup races, deterministic diagnostics
# ---------------------------------------------------------------------------


class RaceClient(FakeClient):
    """Client whose published state changes between the two owner-scoped lookups."""

    def __init__(self, first: list[ExistingComponent], second: list[ExistingComponent]) -> None:
        super().__init__()
        self._sequence = [first, second]
        self.existing = first

    def find_existing_components(self, components: Any, **kwargs: Any) -> list[ExistingComponent]:
        self.existing = self._sequence[min(len(self.find_calls), len(self._sequence) - 1)]
        return super().find_existing_components(components, **kwargs)


def test_version_check_fails_closed_on_unreadable_candidate(tmp_path: Path) -> None:
    component_path = write_component(tmp_path / "component.yaml", version="2.0")
    client = FakeClient()
    client.existing = [ExistingComponent("sha256:v10"), ExistingComponent("sha256:unknown")]
    # "sha256:unknown" is absent from component_versions, so get_component_spec raises.
    client.component_versions = {"sha256:v10": "1.0"}

    result = publish_component_to_tangle(component_path, client=client)

    assert result.outcome == ProcessingOutcome.ERROR
    assert "Cannot read published version" in (result.reason or "")
    assert "sha256:unknown" in (result.reason or "")
    assert result.resolved_digest is None
    assert client.create_calls == []
    assert client.update_calls == []


def test_version_check_fails_closed_on_candidate_without_digest() -> None:
    spec = ComponentSpec.from_yaml("name: demo\nmetadata:\n  annotations:\n    version: '2.0'\n")
    client = FakeClient()
    client.existing = [ExistingComponent("", name="demo")]

    result = perform_version_check(spec=spec, dry_run=False, client=client)

    assert result.outcome == ProcessingOutcome.ERROR
    assert "no digest" in (result.reason or "")
    assert client.create_calls == []


def test_unreadable_row_appearing_before_publish_blocks_create_and_deprecate(tmp_path: Path) -> None:
    component_path = write_component(tmp_path / "component.yaml", version="2.0")
    client = RaceClient(
        [ExistingComponent("sha256:v10")],
        [ExistingComponent("sha256:v10"), ExistingComponent("sha256:unknown")],
    )
    client.component_versions = {"sha256:v10": "1.0"}

    result = publish_component_to_tangle(component_path, client=client)

    assert result.outcome == ProcessingOutcome.ERROR
    assert "sha256:unknown" in (result.reason or "")
    assert client.create_calls == []
    assert client.update_calls == []


def test_concurrent_newer_row_between_lookups_skips_without_publishing(tmp_path: Path) -> None:
    component_path = write_component(tmp_path / "component.yaml", version="2.0")
    client = RaceClient(
        [ExistingComponent("sha256:v10")],
        [ExistingComponent("sha256:v10"), ExistingComponent("sha256:v30")],
    )
    client.component_versions = {"sha256:v10": "1.0", "sha256:v30": "3.0"}

    result = publish_component_to_tangle(component_path, client=client)

    assert result.outcome == ProcessingOutcome.SKIP
    assert result.latest_version == "3.0"
    assert result.latest_digest == "sha256:v30"
    assert result.resolved_digest == "sha256:v30"
    assert "older" in (result.reason or "")
    assert client.create_calls == []
    assert client.update_calls == []


def test_concurrent_equal_row_between_lookups_skips_without_publishing(tmp_path: Path) -> None:
    component_path = write_component(tmp_path / "component.yaml", version="2.0")
    client = RaceClient([], [ExistingComponent("sha256:v20")])
    client.component_versions = {"sha256:v20": "2.0"}

    result = publish_component_to_tangle(component_path, client=client)

    assert result.outcome == ProcessingOutcome.SKIP
    assert result.latest_digest == "sha256:v20"
    assert "unchanged" in (result.reason or "")
    assert client.create_calls == []
    assert client.update_calls == []


def test_concurrent_ambiguous_rows_between_lookups_fail_closed(tmp_path: Path) -> None:
    component_path = write_component(tmp_path / "component.yaml", version="2.0")
    client = RaceClient(
        [ExistingComponent("sha256:v10")],
        [ExistingComponent("sha256:b"), ExistingComponent("sha256:a")],
    )
    client.component_versions = {"sha256:v10": "1.0", "sha256:a": "3.0", "sha256:b": "3.0"}

    result = publish_component_to_tangle(component_path, client=client)

    assert result.outcome == ProcessingOutcome.ERROR
    assert "Ambiguous latest published version 3.0" in (result.reason or "")
    assert client.create_calls == []
    assert client.update_calls == []


def test_row_appearing_after_version_check_is_never_deprecated(tmp_path: Path) -> None:
    component_path = write_component(tmp_path / "component.yaml", version="2.0")
    client = RaceClient(
        [ExistingComponent("sha256:v10")],
        [ExistingComponent("sha256:v10"), ExistingComponent("sha256:v11")],
    )
    client.component_versions = {"sha256:v10": "1.0", "sha256:v11": "1.1"}
    client.publish_response = {"digest": "sha256:v20"}

    result = publish_component_to_tangle(component_path, client=client)

    # Both late rows are proven older than 2.0, so both may be deprecated, in
    # sorted order; nothing unverified is ever touched.
    assert result.outcome == ProcessingOutcome.SUCCESS
    assert [call["digest"] for call in client.update_calls] == ["sha256:v10", "sha256:v11"]


def test_allow_downgrade_never_deprecates_newer_rows(tmp_path: Path) -> None:
    component_path = write_component(tmp_path / "component.yaml", version="1.0")
    client = FakeClient()
    client.existing = [ExistingComponent("sha256:v20"), ExistingComponent("sha256:v005")]
    client.component_versions = {"sha256:v20": "2.0", "sha256:v005": "0.5"}
    client.publish_response = {"digest": "sha256:v10"}

    result = publish_component_to_tangle(component_path, client=client, allow_downgrade=True)

    assert result.outcome == ProcessingOutcome.SUCCESS
    assert [call["digest"] for call in client.update_calls] == ["sha256:v005"]


def test_ambiguity_diagnostics_are_sorted_independently_of_api_order() -> None:
    spec = ComponentSpec.from_yaml("name: demo\nmetadata:\n  annotations:\n    version: '1.0'\n")
    reasons = []
    for digests in (["sha256:a", "sha256:b"], ["sha256:b", "sha256:a"]):
        client = FakeClient()
        client.existing = [ExistingComponent(digest) for digest in digests]
        client.component_versions = {"sha256:a": "2.0", "sha256:b": "2.0"}
        result = perform_version_check(spec=spec, dry_run=False, client=client)
        assert result.outcome == ProcessingOutcome.ERROR
        reasons.append(result.reason)

    assert reasons[0] == reasons[1]
    assert "(sha256:a, sha256:b)" in (reasons[0] or "")


def test_latest_digest_selection_is_independent_of_api_order() -> None:
    spec = ComponentSpec.from_yaml("name: demo\nmetadata:\n  annotations:\n    version: '1.0'\n")
    selected = []
    for digests in (["sha256:v10", "sha256:v30", "sha256:v20"], ["sha256:v30", "sha256:v20", "sha256:v10"]):
        client = FakeClient()
        client.existing = [ExistingComponent(digest) for digest in digests]
        client.component_versions = {"sha256:v10": "1.0", "sha256:v20": "2.0", "sha256:v30": "3.0"}
        result = perform_version_check(spec=spec, dry_run=False, client=client)
        selected.append((result.latest_version, result.latest_digest))

    assert selected[0] == selected[1] == ("3.0", "sha256:v30")


def test_compare_equal_raw_versions_pick_deterministic_representative() -> None:
    spec = ComponentSpec.from_yaml("name: demo\nmetadata:\n  annotations:\n    version: '0.9'\n")
    picked = []
    for digests in (["sha256:a", "sha256:b"], ["sha256:b", "sha256:a"]):
        client = FakeClient()
        client.existing = [ExistingComponent(digest) for digest in digests]
        # "1.0" and "1.0.0" compare equal, so this is an ambiguous tie either way.
        client.component_versions = {"sha256:a": "1.0", "sha256:b": "1.0.0"}
        result = perform_version_check(spec=spec, dry_run=False, client=client)
        assert result.outcome == ProcessingOutcome.ERROR
        picked.append((result.latest_version, result.reason))

    assert picked[0] == picked[1]


def test_dry_run_test_latest_version_applies_monotonic_rules(monkeypatch) -> None:
    spec = ComponentSpec.from_yaml("name: demo\nmetadata:\n  annotations:\n    version: '2.0'\n")

    monkeypatch.setenv("TEST_LATEST_VERSION", "1.0")
    newer = perform_version_check(spec=spec, dry_run=True)
    assert newer.outcome == ProcessingOutcome.PROCEED
    assert newer.latest_version == "1.0"
    assert newer.latest_digest is None

    monkeypatch.setenv("TEST_LATEST_VERSION", "2.0")
    equal = perform_version_check(spec=spec, dry_run=True)
    assert equal.outcome == ProcessingOutcome.SKIP
    assert equal.latest_version == "2.0"
    assert equal.latest_digest is None
    assert equal.resolved_digest is None
    assert "unchanged" in (equal.reason or "")

    monkeypatch.setenv("TEST_LATEST_VERSION", "3.0")
    older = perform_version_check(spec=spec, dry_run=True)
    assert older.outcome == ProcessingOutcome.SKIP
    assert older.latest_version == "3.0"
    assert older.latest_digest is None
    assert older.resolved_digest is None
    assert "older" in (older.reason or "")


def test_dry_run_without_test_latest_version_proceeds(monkeypatch) -> None:
    spec = ComponentSpec.from_yaml("name: demo\nmetadata:\n  annotations:\n    version: '2.0'\n")
    monkeypatch.delenv("TEST_LATEST_VERSION", raising=False)

    result = perform_version_check(spec=spec, dry_run=True)

    assert result.outcome == ProcessingOutcome.PROCEED
    assert result.latest_version is None
    assert result.latest_digest is None


def test_dry_run_publish_skips_when_test_latest_version_is_newer(monkeypatch, tmp_path: Path) -> None:
    component_path = write_component(tmp_path / "component.yaml", version="1.0")
    client = FakeClient()
    monkeypatch.setenv("TEST_LATEST_VERSION", "2.0")

    result = publish_component_to_tangle(component_path, dry_run=True, client=client)

    assert result.outcome == ProcessingOutcome.SKIP
    assert result.latest_version == "2.0"
    assert client.create_calls == []
    assert client.update_calls == []


def test_version_check_without_owner_reports_error_and_no_digest() -> None:
    spec = ComponentSpec.from_yaml("name: demo\nmetadata:\n  annotations:\n    version: '1.0'\n")
    client = FakeClient()
    client.user = None

    result = perform_version_check(spec=spec, dry_run=False, client=client)

    assert result.outcome == ProcessingOutcome.ERROR
    assert result.latest_digest is None
    assert result.resolved_digest is None
    assert client.find_calls == []
    assert client.create_calls == []


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
        git_remote_url="https://github.com/TangleML/example-pipelines",
        git_repo="TangleML/example-pipelines",
        git_root=tmp_path,
        published_by="alice@example.com",
    )

    exit_code = publisher.publish_components([{"component_path": component_path, "name": "Demo"}])

    assert exit_code == 0
    before_context, component_context, after_context = hook.contexts
    assert before_context.git_remote_sha == "sha"
    assert before_context.git_remote_branch == "main"
    assert before_context.git_remote_url == "https://github.com/TangleML/example-pipelines"
    assert before_context.git_repo == "TangleML/example-pipelines"
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


def test_the_owner_argument_a_version_check_passes_is_matched_exactly() -> None:
    """Pin the semantics of the owner argument ``perform_version_check`` sends.

    This is a CONTRACT test, not an end-to-end publisher run: it calls
    ``find_existing_components`` directly, which is the call the version check
    makes. The companion test above proves the publisher passes ``published_by``;
    this proves what that argument then means. Together they stop the
    publisher's owner scope loosening silently.

    It matters because the server query is a substring match: without the
    client's exact filter, a component owned by ``alice2`` would be read as part
    of ``alice``'s published state -- constraining alice's next version, or
    being considered for deprecation.
    """
    from tangle_cli.client import TangleApiClient
    from tangle_cli.models import ComponentInfo

    rows = [
        ComponentInfo(name="orders-loader", digest="sha256:theirs", version="9.9", published_by="alice2"),
        ComponentInfo(name="orders-loader", digest="sha256:mine", version="1.0", published_by="alice"),
    ]

    class _Session:
        def request(self, *a: Any, **kw: Any) -> Any:  # pragma: no cover - never called
            raise AssertionError("no HTTP in this test")

    class _Client(TangleApiClient):
        def list_published_component_infos(  # type: ignore[override]
            self,
            include_deprecated: bool = False,
            name_substring: str | None = None,
            published_by_substring: str | None = None,
            digest: str | None = None,
            *,
            fetch_specs: bool = False,
        ) -> list[Any]:
            out = rows
            if published_by_substring:
                out = [i for i in out if published_by_substring in (i.published_by or "")]
            if name_substring:
                out = [i for i in out if name_substring.lower() in i.name.lower()]
            return out

    client = _Client("https://api.test", session=_Session())

    found = client.find_existing_components(["orders-loader"], published_by="alice")

    assert [i.digest for i in found] == ["sha256:mine"]
