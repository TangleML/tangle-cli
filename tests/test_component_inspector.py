"""Tests for generic OpenAPI-backed component inspection helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from tangle_cli import component_inspector
from tangle_cli.component_inspector import (
    get_standard_library,
    inspect_by_digest,
    inspect_by_name,
    search_components,
    transparency_check,
)
from tangle_cli.models import ComponentSpec


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

    def call(self, operation_name: str, **params: Any) -> Any:
        if operation_name == "components.get":
            return self.component if params.get("digest") == "abc123" else None
        if operation_name == "published-components.list":
            if params.get("digest") == "abc123" or params.get("name_substring") == "demo":
                return {
                    "published_components": [
                        {
                            "name": "demo",
                            "digest": "abc123",
                            "version": "1.2.3",
                            "published_by": "user@example.com",
                            "description": "Demo component",
                        }
                    ]
                }
            if params.get("digest") == "old":
                return {
                    "published_components": [
                        {
                            "name": "demo",
                            "digest": "old",
                            "version": "1.0.0",
                            "deprecated": True,
                            "superseded_by": "abc123",
                        }
                    ]
                }
            return {"published_components": []}
        raise AssertionError(f"unexpected operation: {operation_name}")


class TestTransparencyCheck:
    def test_standard_public_base_image_is_transparent(self):
        spec = ComponentSpec.from_dict({
            "spec": {
                "name": "demo",
                "implementation": {"container": {"image": "python:3.12-slim"}},
            },
        })

        transparent, reason = transparency_check(spec)

        assert transparent is True
        assert "standard public base image" in reason

    def test_unknown_container_is_opaque(self):
        spec = ComponentSpec.from_dict({
            "spec": {
                "name": "demo",
                "implementation": {"container": {"image": "registry.example.com/private/demo:latest"}},
            },
        })

        transparent, reason = transparency_check(spec)

        assert transparent is False
        assert "no inline source" in reason


class TestComponentLibrary:
    def test_standard_library_does_not_fetch_cross_origin_component_urls(self):
        class LibraryClient:
            base_url = "https://tangle.example.com"

            def __init__(self):
                self.paths: list[str] = []

            def call(self, operation_name: str, **params: Any) -> Any:
                raise AssertionError(f"unexpected operation: {operation_name}")

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

        library = get_standard_library(client)

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

            def call(self, operation_name: str, **params: Any) -> Any:
                raise AssertionError(f"unexpected operation: {operation_name}")

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

        library = get_standard_library(client)

        assert client.paths == ["/component_library.yaml", "/components/demo.yaml"]
        assert library["folders"][0]["components"][0]["spec"]["name"] == "demo"

    def test_component_library_cache_is_scoped_per_client(self):
        class LibraryFallbackClient:
            base_url = "https://tangle.example.com"

            def __init__(self, component_name: str | None):
                self.component_name = component_name
                self.paths: list[str] = []

            def call(self, operation_name: str, **params: Any) -> Any:
                if operation_name in {"components.get", "published-components.list"}:
                    return {"published_components": []}
                raise AssertionError(f"unexpected operation: {operation_name}")

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

        first_result = inspect_by_name(first_client, "private-a")
        second_result = inspect_by_name(second_client, "private-a")

        assert first_result["status"] == "success"
        assert second_result["status"] == "not_found"
        assert first_client.paths == ["/component_library.yaml"]
        assert second_client.paths == ["/component_library.yaml"]


class TestInspectComponents:
    def test_inspect_by_digest_merges_spec_and_publication_metadata(self):
        result = inspect_by_digest(FakeClient(), "abc123")

        assert result["status"] == "success"
        assert result["name"] == "demo"
        assert result["digest"] == "abc123"
        assert result["version"] == "1.2.3"
        assert result["transparent"] is True
        assert "implementation" not in result["spec"]

    def test_inspect_by_digest_can_follow_deprecated_chain(self):
        result = inspect_by_digest(FakeClient(), "old", follow_deprecated=True)

        assert result["status"] == "success"
        assert result["digest"] == "abc123"

    def test_inspect_by_name_returns_matching_versions(self):
        result = inspect_by_name(FakeClient(), "demo")

        assert result["status"] == "success"
        assert result["name"] == "demo"
        assert result["version_count"] == 1
        assert result["versions"][0]["digest"] == "abc123"

    def test_search_components_returns_summary_rows(self):
        result = search_components(FakeClient(), name="demo")

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
            def call(self, operation_name: str, **params: Any) -> Any:
                if operation_name == "published-components.list":
                    return {"published_components": [{"name": "demo", "digest": "abc123", "description": None}]}
                return super().call(operation_name, **params)

        result = search_components(NullDescriptionClient(), name="demo")

        assert result["components"][0]["description"] == ""
