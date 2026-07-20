"""Tests for static-client-backed component inspection helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from tangle_cli import component_inspector
from tangle_cli.component_inspector import ComponentInspector
from tangle_cli.models import ComponentInfo, ComponentSpec


@dataclass
class FakeResponse:
    text: str

    def raise_for_status(self) -> None:
        return None


@pytest.fixture(autouse=True)
def reset_component_library_cache():
    component_inspector._component_libraries_by_client.clear()
    yield
    component_inspector._component_libraries_by_client.clear()


class FakeClient:
    base_url = "https://tangle.example.com"

    def __init__(self):
        self.component = {
            "digest": "abc123",
            "spec": {
                "name": "demo",
                "description": "Demo component",
                "metadata": {"annotations": {"version": "1.2.3"}},
                "implementation": {"container": {"image": "python:3.12-slim"}},
            },
        }

    def get_component_spec(self, digest: str) -> ComponentSpec | None:
        if digest != "abc123":
            return None
        return ComponentSpec.from_dict(self.component)

    def list_published_component_infos(self, **params: Any) -> list[ComponentInfo]:
        if params.get("digest") == "abc123" or params.get("name_substring") == "demo":
            return [
                ComponentInfo(
                    name="demo",
                    digest="abc123",
                    version="1.2.3",
                    published_by="user@example.com",
                    description="Demo component",
                )
            ]
        if params.get("digest") == "old":
            return [
                ComponentInfo(
                    name="demo",
                    digest="old",
                    version="1.0.0",
                    deprecated=True,
                    superseded_by="abc123",
                )
            ]
        return []


class TestTransparencyCheck:
    def test_standard_public_base_image_is_transparent(self):
        spec = ComponentSpec.from_dict({
            "spec": {
                "name": "demo",
                "implementation": {"container": {"image": "python:3.12-slim"}},
            },
        })

        transparent, reason = ComponentInspector.transparency_check(spec)

        assert transparent is True
        assert "standard public base image" in reason

    def test_unknown_container_is_opaque(self):
        spec = ComponentSpec.from_dict({
            "spec": {
                "name": "demo",
                "implementation": {"container": {"image": "registry.example.com/private/demo:latest"}},
            },
        })

        transparent, reason = ComponentInspector.transparency_check(spec)

        assert transparent is False
        assert "no inline source" in reason

    def test_git_source_reason_does_not_leak_credentials(self):
        spec = ComponentSpec.from_dict({
            "spec": {
                "name": "demo",
                "implementation": {"container": {"image": "registry.example.com/private/demo:latest"}},
                "metadata": {
                    "annotations": {
                        "git_remote_url": "https://user:s3cr3tTOKEN@github.com/Org/repo.git",
                        "component_yaml_path": "comp.yaml",
                    },
                },
            },
        })

        transparent, reason = ComponentInspector.transparency_check(spec)

        assert transparent is True
        assert "s3cr3tTOKEN" not in reason
        assert "@github.com" not in reason
        assert "https://github.com/Org/repo" in reason


class TestComponentLibrary:
    def test_standard_library_does_not_fetch_cross_origin_component_urls(self):
        class LibraryClient:
            base_url = "https://tangle.example.com"

            def __init__(self):
                self.paths: list[str] = []

            def request_path(self, path: str):
                self.paths.append(path)
                if path == "/component_library.yaml":
                    return FakeResponse(
                        "folders:\n"
                        "  - name: demo\n"
                        "    components:\n"
                        "      - url: http://127.0.0.1/internal.yaml\n"
                    )
                raise AssertionError(f"unexpected fetch: {path}")

        client = LibraryClient()

        library = ComponentInspector(client=client).get_standard_library()

        assert client.paths == ["/component_library.yaml"]
        assert library["folders"][0]["components"][0] == {
            "url": "http://127.0.0.1/internal.yaml",
            "spec": None,
        }

    def test_standard_library_fetches_relative_component_urls_through_client(self):
        class LibraryClient:
            base_url = "https://tangle.example.com"

            def __init__(self):
                self.paths: list[str] = []

            def request_path(self, path: str):
                self.paths.append(path)
                if path == "/component_library.yaml":
                    return FakeResponse(
                        "folders:\n"
                        "  - name: demo\n"
                        "    components:\n"
                        "      - url: components/demo.yaml\n"
                    )
                if path == "/components/demo.yaml":
                    return FakeResponse("name: demo\ndescription: Demo from library\n")
                raise AssertionError(f"unexpected fetch: {path}")

        client = LibraryClient()

        library = ComponentInspector(client=client).get_standard_library()

        assert client.paths == ["/component_library.yaml", "/components/demo.yaml"]
        assert library["folders"][0]["components"][0]["spec"]["name"] == "demo"

    def test_component_library_cache_is_scoped_per_client(self):
        class LibraryFallbackClient:
            base_url = "https://tangle.example.com"

            def __init__(self, component_name: str | None):
                self.component_name = component_name
                self.paths: list[str] = []

            def get_component_spec(self, digest: str) -> ComponentSpec | None:
                return None

            def list_published_component_infos(self, **params: Any) -> list[ComponentInfo]:
                return []

            def request_path(self, path: str):
                self.paths.append(path)
                if path != "/component_library.yaml":
                    raise AssertionError(f"unexpected fetch: {path}")
                if self.component_name is None:
                    return FakeResponse("folders: []\n")
                return FakeResponse(
                    "folders:\n"
                    "  - name: demo\n"
                    "    components:\n"
                    "      - spec:\n"
                    f"          name: {self.component_name}\n"
                    "          description: Demo from library\n"
                )

        first_client = LibraryFallbackClient("private-a")
        second_client = LibraryFallbackClient(None)

        first_result = ComponentInspector(client=first_client).inspect_by_name("private-a")
        second_result = ComponentInspector(client=second_client).inspect_by_name("private-a")

        assert first_result["status"] == "success"
        assert second_result["status"] == "not_found"
        assert first_client.paths == ["/component_library.yaml"]
        assert second_client.paths == ["/component_library.yaml"]


class TestInspectComponents:
    def test_inspect_by_digest_merges_spec_and_publication_metadata(self):
        result = ComponentInspector(client=FakeClient()).inspect_by_digest("abc123")

        assert result["status"] == "success"
        assert result["name"] == "demo"
        assert result["digest"] == "abc123"
        assert result["version"] == "1.2.3"
        assert result["transparent"] is True
        assert "implementation" not in result["spec"]

    def test_inspect_by_digest_can_follow_deprecated_chain(self):
        result = ComponentInspector(client=FakeClient()).inspect_by_digest("old", follow_deprecated=True)

        assert result["status"] == "success"
        assert result["digest"] == "abc123"

    def test_inspect_by_digest_backfills_missing_published_version_from_spec(self):
        class MissingPublishedVersionClient(FakeClient):
            def list_published_component_infos(self, **params: Any) -> list[ComponentInfo]:
                if params.get("digest") == "abc123":
                    return [ComponentInfo(name="demo", digest="abc123")]
                return super().list_published_component_infos(**params)

        result = ComponentInspector(client=MissingPublishedVersionClient()).inspect_by_digest("abc123")

        assert result["status"] == "success"
        assert result["version"] == "1.2.3"

    def test_inspect_by_name_backfills_missing_published_version_from_spec(self):
        class MissingPublishedVersionClient(FakeClient):
            def list_published_component_infos(self, **params: Any) -> list[ComponentInfo]:
                if params.get("name_substring") == "demo":
                    return [ComponentInfo(name="demo", digest="abc123")]
                return super().list_published_component_infos(**params)

        result = ComponentInspector(client=MissingPublishedVersionClient()).inspect_by_name("demo")

        assert result["status"] == "success"
        assert result["versions"][0]["version"] == "1.2.3"

    def test_inspect_by_name_returns_matching_versions(self):
        result = ComponentInspector(client=FakeClient()).inspect_by_name("demo")

        assert result["status"] == "success"
        assert result["name"] == "demo"
        assert result["version_count"] == 1
        assert result["versions"][0]["digest"] == "abc123"

    def test_search_components_returns_summary_rows(self):
        result = ComponentInspector(client=FakeClient()).search_components(name="demo")

        assert result == {
            "status": "success",
            "query": "demo",
            "count": 1,
            "components": [{
                "name": "demo",
                "digest": "abc123",
                "version": "1.2.3",
                "deprecated": False,
                "description": "Demo component",
            }],
        }

    def test_search_components_handles_null_description(self):
        class NullDescriptionClient(FakeClient):
            def list_published_component_infos(self, **params: Any) -> list[ComponentInfo]:
                return [ComponentInfo(name="demo", digest="abc123", description=None)]

        result = ComponentInspector(client=NullDescriptionClient()).search_components(name="demo")

        assert result["components"][0]["description"] == ""
