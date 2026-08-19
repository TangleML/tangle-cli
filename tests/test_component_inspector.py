"""Tests for static-client-backed component inspection helpers."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import pytest

from tangle_cli import component_inspector
from tangle_cli.component_inspector import ComponentInspector
from tangle_cli.models import ComponentInfo, ComponentSpec


def _staggered(text: str, depth: int) -> str:
    encoded = "".join(f"%{ord(char):02X}" for char in text)
    for _ in range(depth - 1):
        encoded = "%" + "".join(f"%{ord(char):02X}" for char in encoded[1:])
    return encoded


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

    @pytest.mark.parametrize("remote_url", [
        # empty authority hides the userinfo in the path
        "https:///user:s3cr3tTOKEN@Org/repo.git",
        "https:/user:s3cr3tTOKEN@Org/repo.git",
        # percent-encoded userinfo, invisible to a literal-separator screen
        "https:///user%3As3cr3tTOKEN%40Org/repo.git",
        "https://user%3As3cr3tTOKEN%40evil.example/Org/repo.git",
        # deeply nested encoding of the separators
        "https:///user%2525253As3cr3tTOKEN%252525252540Org/repo.git",
        # staggered cross-replacement encoding past the screening pass cap
        f"https:///user{_staggered(':', 6)}s3cr3tTOKEN{_staggered('@', 6)}Org/repo.git",
        # query delimiter percent-encoded into the path
        "https://github.com/Org/repo%3Faccess_token%3Ds3cr3tTOKEN",
        "https://github.com/Org/repo%23access_token%3Ds3cr3tTOKEN",
        # credential-bearing query parameter, hyphenated spelling
        "https://github.com/Org/repo.git?x-api-key=s3cr3tTOKEN",
        # the same parameter with its name percent-encoded
        "https://github.com/Org/repo.git?x%252Dapi%252Dkey=s3cr3tTOKEN",
    ])
    def test_git_source_reason_handles_malformed_credential_urls(self, remote_url):
        spec = ComponentSpec.from_dict({
            "spec": {
                "name": "demo",
                "implementation": {"container": {"image": "registry.example.com/private/demo:latest"}},
                "metadata": {
                    "annotations": {
                        "git_remote_url": remote_url,
                        "component_yaml_path": "comp.yaml",
                    },
                },
            },
        })

        transparent, reason = ComponentInspector.transparency_check(spec)

        assert transparent is True
        assert "s3cr3tTOKEN" not in reason

    @pytest.mark.parametrize("remote_url,expected_yaml_link", [
        (
            "https://user:s3cr3tTOKEN@github.com/Org/repo.git",
            "https://github.com/Org/repo/blob/main/comp.yaml",
        ),
        (
            "https://github.com/Org/repo.git?x-api-key=s3cr3tTOKEN",
            "https://github.com/Org/repo/blob/main/comp.yaml",
        ),
        (
            "https:///user:s3cr3tTOKEN@Org/repo.git",
            "[redacted-invalid-git-url]/blob/main/comp.yaml",
        ),
        (
            "https:///user%3As3cr3tTOKEN%40Org/repo.git",
            "[redacted-invalid-git-url]/blob/main/comp.yaml",
        ),
        (
            "https:///user%2525253As3cr3tTOKEN%252525252540Org/repo.git",
            "[redacted-invalid-git-url]/blob/main/comp.yaml",
        ),
        (
            f"https:///user{_staggered(':', 6)}s3cr3tTOKEN{_staggered('@', 6)}Org/repo.git",
            "[redacted-invalid-git-url]/blob/main/comp.yaml",
        ),
        (
            "https://github.com/Org/repo%3Faccess_token%3Ds3cr3tTOKEN.git",
            "[redacted-invalid-git-url]/blob/main/comp.yaml",
        ),
    ])
    def test_browse_links_never_carry_credentials(self, remote_url, expected_yaml_link):
        spec = ComponentSpec.from_dict({
            "spec": {
                "name": "demo",
                "implementation": {"container": {"image": "python:3.12"}},
                "metadata": {
                    "annotations": {
                        "git_remote_url": remote_url,
                        "git_remote_branch": "main",
                        "component_yaml_path": "comp.yaml",
                    },
                },
            },
        })

        source = component_inspector._resolve_git_source(spec)

        assert source is not None
        assert source["component_yaml"] == expected_yaml_link
        assert "s3cr3tTOKEN" not in source["component_yaml"]


def _git_annotation_spec(remote_url: str) -> ComponentSpec:
    return ComponentSpec.from_dict({
        "spec": {
            "name": "demo",
            "implementation": {"container": {"image": "python:3.12"}},
            "metadata": {
                "annotations": {
                    "git_remote_url": remote_url,
                    "git_remote_branch": "main",
                    "component_yaml_path": "comp.yaml",
                },
            },
        },
    })


class TestGitAnnotationSanitizerCost:
    """Bound the cost of sanitizing a server-supplied ``git_remote_url``.

    The annotation is remote input, so sanitizing it must stay linear in its
    length.  A quadratic scan here is a hang, not a slowdown: a benign 32 KB
    ``?ref=`` value once took eleven seconds and 64 KB took forty-seven.
    """

    # Bounds are ~100x the observed cost, which still leaves a quadratic
    # regression (tens of seconds upward) far outside the limit.
    ABSOLUTE_BUDGET_SECONDS = 5.0

    @staticmethod
    def _fastest_resolve(remote_url: str) -> float:
        spec = _git_annotation_spec(remote_url)
        timings = []
        for _ in range(3):
            start = time.perf_counter()
            assert component_inspector._resolve_git_source(spec) is not None
            timings.append(time.perf_counter() - start)
        return min(timings)

    @pytest.mark.parametrize("kilobytes", [32, 64, 128])
    def test_benign_word_run_annotation_is_bounded(self, kilobytes):
        """The reported payload: a long value with no credential marker at all."""
        remote_url = "https://github.com/Org/repo?ref=" + "a" * (kilobytes * 1024)
        assert self._fastest_resolve(remote_url) < self.ABSOLUTE_BUDGET_SECONDS

    @pytest.mark.parametrize("unit", [
        "a",      # one unbroken word run
        "_a",     # separator-delimited runs, one letter each
        "a:",     # colon-dense, a delimiter every other character
        "k=v;",   # assignment-dense
        "a ",     # whitespace between every run and the next
        "a@",     # userinfo-shaped
        "a.b-",   # dots and dashes inside the run
        "a%3a",   # percent-encoded colon, screened on a decoded copy
    ])
    def test_hostile_annotation_shapes_are_bounded(self, unit):
        remote_url = "https://github.com/Org/repo?ref=" + unit * (131_072 // len(unit))
        assert self._fastest_resolve(remote_url) < self.ABSOLUTE_BUDGET_SECONDS

    def test_annotation_cost_scales_subquadratically(self):
        """Linear work quadruples over a 4x size step; quadratic work grows 16x."""
        prefix = "https://github.com/Org/repo?ref="
        small = self._fastest_resolve(prefix + "a" * 32_768)
        large = self._fastest_resolve(prefix + "a" * 131_072)
        assert large < self.ABSOLUTE_BUDGET_SECONDS
        if small > 1e-4:
            assert large / small < 8.0

    def test_credentials_are_still_redacted_in_a_huge_annotation(self):
        """Cost bounds must not come at the price of missing the credential."""
        padding = "a" * 65_536
        spec = _git_annotation_spec(
            f"https://github.com/Org/repo?ref={padding}"
            f"&access_token=s3cr3tTOKEN#oauth_token=s3cr3tTOKEN"
        )

        source = component_inspector._resolve_git_source(spec)

        assert source is not None
        assert "s3cr3tTOKEN" not in source["component_yaml"]

    def test_nested_credential_in_a_huge_benign_value_is_caught(self):
        """The padded run must not push the nested assignment out of view."""
        spec = _git_annotation_spec(
            "https://github.com/Org/repo?ref=" + "a" * 65_536 + "?access_token=s3cr3tTOKEN"
        )

        source = component_inspector._resolve_git_source(spec)

        assert source is not None
        assert "s3cr3tTOKEN" not in source["component_yaml"]


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
