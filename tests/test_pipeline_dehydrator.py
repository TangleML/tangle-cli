from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from tangle_cli import utils
from tangle_cli.pipeline_dehydrator import (
    DehydrateChoice,
    Jinja2ExportResult,
    PipelineDehydrator,
    _build_subgraph_processing_queue,
    _extract_input_defaults,
)


def _leaf_spec(name: str, *, canonical_url: str | None = None) -> dict[str, Any]:
    annotations = {}
    if canonical_url:
        annotations["canonical_location"] = canonical_url
    return {
        "name": name,
        "metadata": {"annotations": annotations},
        "implementation": {"container": {"image": "example/image:latest"}},
    }


def _task(name: str, digest: str, *, canonical_url: str | None = None) -> dict[str, Any]:
    return {"componentRef": {"name": name, "digest": digest, "spec": _leaf_spec(name, canonical_url=canonical_url)}}


def _pipeline(tasks: dict[str, Any]) -> dict[str, Any]:
    return {"name": "Pipeline", "implementation": {"graph": {"tasks": tasks}}}


class FakeClient:
    def __init__(self, found: set[str]) -> None:
        self.found = found
        self.calls: list[str] = []

    def get_component_spec(self, digest: str) -> dict[str, Any]:
        self.calls.append(digest)
        if digest not in self.found:
            raise KeyError(digest)
        return {"name": "found"}


def test_pipeline_dehydrator_replaces_refs_by_explicit_choice(tmp_path: Path) -> None:
    data = _pipeline({"task": _task("Leaf Component", "digest-1", canonical_url="https://example.test/leaf.yaml")})

    digest_result = PipelineDehydrator({"": DehydrateChoice.DIGEST}, output_file=tmp_path / "out.yaml").dehydrate(data)
    assert digest_result["implementation"]["graph"]["tasks"]["task"]["componentRef"] == {"digest": "digest-1"}

    name_result = PipelineDehydrator({"": DehydrateChoice.NAME}, output_file=tmp_path / "out.yaml").dehydrate(data)
    assert name_result["implementation"]["graph"]["tasks"]["task"]["componentRef"] == {"name": "Leaf Component"}

    url_result = PipelineDehydrator({"": DehydrateChoice.URL}, output_file=tmp_path / "out.yaml").dehydrate(data)
    assert url_result["implementation"]["graph"]["tasks"]["task"]["componentRef"] == {
        "url": "https://example.test/leaf.yaml"
    }

    keep_result = PipelineDehydrator({"": DehydrateChoice.KEEP}, output_file=tmp_path / "out.yaml").dehydrate(data)
    assert "spec" in keep_result["implementation"]["graph"]["tasks"]["task"]["componentRef"]


def test_pipeline_dehydrator_construction_is_auth_env_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Auth-free dehydration construction must not require TANGLE_API_URL."""

    monkeypatch.setenv("TANGLE_API_TOKEN", "token")
    monkeypatch.delenv("TANGLE_API_URL", raising=False)

    dehydrator = PipelineDehydrator({"": DehydrateChoice.DIGEST})

    assert dehydrator.remembered_choices == {"": DehydrateChoice.DIGEST}


def test_pipeline_dehydrator_auto_with_url_does_not_create_client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Auto mode should not create a client when canonical URL is enough to decide."""

    def fail_api_client(_self):
        raise AssertionError("client should not be created")

    monkeypatch.setattr(PipelineDehydrator, "_api_client", fail_api_client)
    data = _pipeline({"canonical": _task("Canonical", "digest-url", canonical_url="https://example.test/canonical.yaml")})

    result = PipelineDehydrator({"": DehydrateChoice.AUTO}, output_file=tmp_path / "out.yaml").dehydrate(data)

    tasks = result["implementation"]["graph"]["tasks"]
    assert tasks["canonical"]["componentRef"] == {"url": "https://example.test/canonical.yaml"}


def test_pipeline_dehydrator_auto_lazily_creates_client_for_library_lookup(tmp_path: Path) -> None:
    """Auto mode creates a default client only when a library lookup is needed."""

    client = FakeClient({"digest-found"})

    class LazyDehydrator(PipelineDehydrator):
        def _api_client(self):
            return client

    data = _pipeline({"published": _task("Published", "digest-found")})

    result = LazyDehydrator({"": DehydrateChoice.AUTO}, output_file=tmp_path / "out.yaml").dehydrate(data)

    tasks = result["implementation"]["graph"]["tasks"]
    assert tasks["published"]["componentRef"] == {"digest": "digest-found"}
    assert client.calls == ["digest-found"]


def test_pipeline_dehydrator_auto_falls_back_to_file_when_client_creation_fails(
    tmp_path: Path,
) -> None:
    """Auto mode should stay local when no safe API client can be created."""

    class UnavailableClientDehydrator(PipelineDehydrator):
        def _api_client(self):
            raise SystemExit("missing api configuration")

    data = _pipeline({"local": _task("Local Only", "digest-missing")})

    result = UnavailableClientDehydrator(
        {"": DehydrateChoice.AUTO},
        output_file=tmp_path / "out.yaml",
    ).dehydrate(data)

    tasks = result["implementation"]["graph"]["tasks"]
    assert tasks["local"]["componentRef"] == {"url": "file://./components/local_only.yaml"}
    saved_component = yaml.safe_load((tmp_path / "components" / "local_only.yaml").read_text(encoding="utf-8"))
    assert saved_component["name"] == "Local Only"


def test_pipeline_dehydrator_auto_uses_url_digest_then_file(tmp_path: Path) -> None:
    data = _pipeline(
        {
            "canonical": _task("Canonical", "digest-url", canonical_url="https://example.test/canonical.yaml"),
            "published": _task("Published", "digest-found"),
            "local": _task("Local Only", "digest-missing"),
        }
    )
    client = FakeClient({"digest-found"})

    result = PipelineDehydrator(
        {"": DehydrateChoice.AUTO},
        output_file=tmp_path / "out.yaml",
        client=client,
    ).dehydrate(data)

    tasks = result["implementation"]["graph"]["tasks"]
    assert tasks["canonical"]["componentRef"] == {"url": "https://example.test/canonical.yaml"}
    assert tasks["published"]["componentRef"] == {"digest": "digest-found"}
    assert tasks["local"]["componentRef"] == {"url": "file://./components/local_only.yaml"}
    assert client.calls == ["digest-found", "digest-missing"]
    saved_component = yaml.safe_load((tmp_path / "components" / "local_only.yaml").read_text(encoding="utf-8"))
    assert saved_component["name"] == "Local Only"


def test_pipeline_dehydrator_auto_extracts_subgraphs_and_rewrites_relative_urls(tmp_path: Path) -> None:
    subgraph_spec = {
        "name": "Nested Subgraph",
        "implementation": {"graph": {"tasks": {"leaf": _task("Inner Leaf", "digest-inner")}}},
    }
    data = _pipeline({"nested": {"componentRef": {"name": "Nested Subgraph", "digest": "digest-sub", "spec": subgraph_spec}}})

    result = PipelineDehydrator({"": DehydrateChoice.AUTO}, output_file=tmp_path / "out.yaml").dehydrate(data)

    nested_ref = result["implementation"]["graph"]["tasks"]["nested"]["componentRef"]
    assert nested_ref == {"url": "file://./subgraphs/nested_subgraph_0.yaml"}

    subgraph_file = tmp_path / "subgraphs" / "nested_subgraph_0.yaml"
    subgraph = yaml.safe_load(subgraph_file.read_text(encoding="utf-8"))
    assert subgraph["implementation"]["graph"]["tasks"]["leaf"]["componentRef"] == {
        "url": "file://./../components/inner_leaf.yaml"
    }
    assert yaml.safe_load((tmp_path / "components" / "inner_leaf.yaml").read_text(encoding="utf-8"))["name"] == "Inner Leaf"


def test_pipeline_dehydrator_exports_jinja2_template_and_config(tmp_path: Path) -> None:
    subgraph_spec = {"name": "Reusable", "implementation": {"graph": {"tasks": {}}}}
    data = {
        "name": "Pipeline",
        "inputs": [{"name": "Model Name", "type": "string", "default": "tiny"}],
        "implementation": {
            "graph": {
                "tasks": {
                    "nested": {"componentRef": {"name": "Reusable", "digest": "digest-sub", "spec": subgraph_spec}}
                }
            }
        },
    }

    result = PipelineDehydrator(output_file=tmp_path / "config.yaml").export_to_jinja2(
        data,
        tmp_path / "config.yaml",
        tmp_path / "pipeline.yaml.j2",
    )

    assert isinstance(result, Jinja2ExportResult)
    assert result.subtemplates_count == 1
    assert result.top_level_params_count == 1
    config = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert config == {"template_file": "pipeline.yaml.j2", "model_name": "tiny"}
    template = (tmp_path / "pipeline.yaml.j2").read_text(encoding="utf-8")
    assert "{{ model_name }}" in template
    assert "{% include 'pipeline_subtemplate_0.yaml.j2' %}" in template
    assert result.subtemplate_paths[0].name == "pipeline_subtemplate_0.yaml.j2"


def test_pipeline_dehydrator_uses_uri_hooks_for_read_write_and_extracted_components() -> None:
    input_data = _pipeline({"leaf": _task("Remote Leaf", "digest-remote")})
    sources = {"mem://bucket/input.yaml": utils.dump_yaml(input_data)}
    writes: dict[str, str] = {}

    def reader(_hydrator, uri, _context):
        return sources[uri]

    def writer(_hydrator, uri, content, _context):
        writes[uri] = content

    result = PipelineDehydrator(
        {"": DehydrateChoice.FILE},
        uri_readers={"mem": reader},
        uri_writers={"mem": writer},
    ).dehydrate_file("mem://bucket/input.yaml", "mem://bucket/out/pipeline.yaml")

    assert result["implementation"]["graph"]["tasks"]["leaf"]["componentRef"] == {
        "url": "mem://bucket/out/components/remote_leaf.yaml"
    }
    assert yaml.safe_load(writes["mem://bucket/out/components/remote_leaf.yaml"])["name"] == "Remote Leaf"
    assert yaml.safe_load(writes["mem://bucket/out/pipeline.yaml"]) == result


def test_pipeline_dehydrator_helper_exports_are_importable() -> None:
    assert _extract_input_defaults({"inputs": {"User Name": {"default": "Ada"}}}) == {"user_name": "Ada"}
    assert _build_subgraph_processing_queue(_pipeline({}))[0] == (0, "Pipeline")
