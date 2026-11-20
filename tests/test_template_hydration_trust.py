"""A ``template_file`` is pipeline input: rendering it must not reach Python internals."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from tangle_cli.pipeline_hydrator import (
    PipelineHydrator,
    UntrustedHydrationSourceError,
    render_template,
)

PIPELINE_TEMPLATE = textwrap.dedent(
    """
    name: "{{ pipeline_name }}"
    implementation:
      graph:
        tasks: {}
    """
)


def _write_config(path: Path, template_file: str, **params: str) -> Path:
    path.write_text(yaml.safe_dump({"template_file": template_file, **params}), encoding="utf-8")
    return path


def test_render_template_blocks_python_internals(tmp_path: Path):
    template = tmp_path / "pipeline.yaml.j2"
    template.write_text('name: "{{ pipeline_name.__class__ }}"\n', encoding="utf-8")

    with pytest.raises(UntrustedHydrationSourceError, match="template sandbox"):
        render_template(template, {"pipeline_name": "P"})


def test_hydrate_file_template_cannot_reach_python_internals(tmp_path: Path):
    (tmp_path / "pipeline.yaml.j2").write_text(
        'name: "{{ pipeline_name.__class__.__mro__ }}"\nimplementation:\n  graph:\n    tasks: {}\n',
        encoding="utf-8",
    )
    config = _write_config(tmp_path / "pipeline.yaml", "pipeline.yaml.j2", pipeline_name="P")

    with pytest.raises(UntrustedHydrationSourceError, match="template sandbox"):
        PipelineHydrator().hydrate_file(config)


def test_component_template_cannot_reach_python_internals(tmp_path: Path):
    (tmp_path / "component.yaml.j2").write_text(
        'name: "{{ component_name.__class__ }}"\nimplementation:\n  container:\n    image: python:3.12\n',
        encoding="utf-8",
    )
    _write_config(tmp_path / "component.yaml", "component.yaml.j2", component_name="C")
    pipeline = tmp_path / "pipeline.yaml"
    pipeline.write_text(
        yaml.safe_dump({
            "name": "P",
            "implementation": {"graph": {"tasks": {
                "task": {"componentRef": {"url": "file://./component.yaml"}},
            }}},
        }),
        encoding="utf-8",
    )

    with pytest.raises(UntrustedHydrationSourceError, match="template sandbox"):
        PipelineHydrator().hydrate_file(pipeline)


@pytest.mark.parametrize("trust", ["all", "source"])
def test_trusted_hydration_does_not_lift_the_template_sandbox(tmp_path: Path, trust: str):
    (tmp_path / "pipeline.yaml.j2").write_text(
        'name: "{{ pipeline_name.__class__ }}"\nimplementation:\n  graph:\n    tasks: {}\n',
        encoding="utf-8",
    )
    config = _write_config(tmp_path / "pipeline.yaml", "pipeline.yaml.j2", pipeline_name="P")

    with pytest.raises(UntrustedHydrationSourceError, match="template sandbox"):
        PipelineHydrator(
            allow_all_hydration=trust == "all",
            trusted_python_sources=[str(tmp_path)] if trust == "source" else [],
        ).hydrate_file(config)


@pytest.mark.parametrize("directive", ["{{ include_raw(%s) }}", "{%% include %s %%}", "{%% import %s as m %%}"])
@pytest.mark.parametrize("absolute", [False, True], ids=["parent-traversal", "absolute-path"])
def test_includes_read_only_inside_the_template_directory(tmp_path: Path, directive: str, absolute: bool):
    from jinja2.exceptions import TemplateNotFound

    templates = tmp_path / "templates"
    templates.mkdir()
    (tmp_path / "outside.txt").write_text("outside the template directory\n", encoding="utf-8")
    template = templates / "pipeline.yaml.j2"
    include_path = str(tmp_path / "outside.txt") if absolute else "../outside.txt"
    template.write_text(directive % repr(include_path), encoding="utf-8")

    with pytest.raises(TemplateNotFound):
        render_template(template, {})


def test_symlinked_shared_macros_still_render(tmp_path: Path):
    shared = tmp_path / "shared"
    pipeline_dir = tmp_path / "pipelines" / "daily"
    shared.mkdir()
    pipeline_dir.mkdir(parents=True)
    (shared / "macros.j2").write_text(
        "{% macro describe(team) %}Owned by {{ team | upper }}{% endmacro %}\n",
        encoding="utf-8",
    )
    (pipeline_dir / "macros.j2").symlink_to(Path("..") / ".." / "shared" / "macros.j2")
    (pipeline_dir / "pipeline.yaml.j2").write_text(
        textwrap.dedent(
            """\
            {%- import 'macros.j2' as m %}
            name: "{{ pipeline_name }}"
            description: "{{ m.describe(team) }}"
            implementation:
              graph:
                tasks: {}
            """
        ),
        encoding="utf-8",
    )
    config = _write_config(pipeline_dir / "pipeline.yaml", "pipeline.yaml.j2", pipeline_name="Daily", team="ml")

    hydrated = PipelineHydrator().hydrate_file(config)

    assert hydrated.data["name"] == "Daily"
    assert hydrated.data["description"] == "Owned by ML"


def test_shared_template_in_a_parent_directory_still_renders(tmp_path: Path, monkeypatch):
    configs = tmp_path / "configs" / "prod"
    configs.mkdir(parents=True)
    (tmp_path / "pipeline.yaml.j2").write_text(PIPELINE_TEMPLATE, encoding="utf-8")
    config = _write_config(configs / "pipeline.yaml", "../../pipeline.yaml.j2", pipeline_name="Prod")
    monkeypatch.chdir(configs)

    hydrated = PipelineHydrator().hydrate_file(config)

    assert hydrated.data["name"] == "Prod"


def test_ordinary_template_features_render_in_the_sandbox(tmp_path: Path):
    templates = tmp_path / "templates"
    (templates / "sql").mkdir(parents=True)
    (templates / "sql" / "query.sql").write_text("SELECT * FROM {{ table }}\n", encoding="utf-8")
    (templates / "labels.yaml.j2").write_text(
        "labels: {{ labels | join(',') }} {{ '%s-%d' | format(env, 3) }} {{ labels[0].replace('-', '_') }}\n",
        encoding="utf-8",
    )
    template = templates / "pipeline.yaml.j2"
    template.write_text(
        textwrap.dedent(
            """\
            {%- set suffix = env | upper %}
            name: "{{ pipeline_name }} {{ suffix }}"
            query: {{ include_raw('sql/query.sql') | tojson }}
            implementation:
              graph:
                tasks:
            {%- for name, digest in tasks.items() | sort %}
                  {{ name }}:
                    componentRef:
                      digest: {{ digest }}
            {%- endfor %}
            {% include 'labels.yaml.j2' %}
            """
        ),
        encoding="utf-8",
    )

    rendered = yaml.safe_load(
        render_template(
            template,
            {
                "pipeline_name": "Daily",
                "env": "prod",
                "tasks": {"train": "sha-train", "eval": "sha-eval"},
                "labels": ["team-a", "nightly"],
            },
        )
    )

    assert rendered["name"] == "Daily PROD"
    assert rendered["query"] == "SELECT * FROM {{ table }}\n"
    assert rendered["implementation"]["graph"]["tasks"] == {
        "eval": {"componentRef": {"digest": "sha-eval"}},
        "train": {"componentRef": {"digest": "sha-train"}},
    }
    assert rendered["labels"] == "team-a,nightly prod-3 team_a"
