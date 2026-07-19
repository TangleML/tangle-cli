import json
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import yaml

from tangle_cli import cli
from tangle_cli.pipeline_hydrator import PipelineHydrator
from tangle_cli.pipelines import (
    collect_pipeline_spec_errors,
    load_pipeline_schema,
    validate_component_inputs,
    validate_pipeline_schema,
)


def run_app(app, args: list[str]) -> None:
    try:
        app(args)
    except SystemExit as exc:
        if exc.code not in (0, None):
            raise


def _write_pipeline(path: Path, data: dict) -> Path:
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def _minimal_valid_pipeline() -> dict:
    return {
        "name": "Demo Pipeline",
        "implementation": {
            "graph": {
                "tasks": {
                    "extract": {
                        "componentRef": {
                            "spec": {
                                "name": "Extract",
                                "outputs": [{"name": "rows", "type": "String"}],
                            }
                        }
                    },
                    "load": {
                        "componentRef": {
                            "spec": {
                                "name": "Load",
                                "inputs": [{"name": "rows", "type": "String"}],
                            }
                        },
                        "arguments": {
                            "rows": {
                                "taskOutput": {"taskId": "extract", "outputName": "rows"}
                            }
                        },
                    },
                }
            }
        },
    }


def test_sdk_help_includes_pipelines(capsys):
    app = cli.build_app()

    run_app(app, ["sdk", "--help"])

    output = capsys.readouterr().out
    assert "artifacts" in output
    assert "components" in output
    assert "pipelines" in output
    assert "published-components" in output
    assert "secrets" in output


def test_sdk_pipelines_help_lists_local_commands(capsys):
    app = cli.build_app()

    run_app(app, ["sdk", "pipelines", "--help"])

    output = capsys.readouterr().out
    assert "validate" in output
    assert "hydrate" in output
    assert "diagram" in output
    assert "layout" in output
    assert "compile" in output
    assert "pipeline-runs" not in output


def test_pipeline_hydrator_generic_local_resolver_priority(tmp_path: Path):
    local_yaml = tmp_path / "local.yaml"
    local_yaml.write_text(yaml.safe_dump({"name": "Local"}), encoding="utf-8")
    ignored_yaml = tmp_path / "ignored.yaml"
    ignored_yaml.write_text(yaml.safe_dump({"name": "Ignored"}), encoding="utf-8")
    hydrator = PipelineHydrator(client=MagicMock())

    result = hydrator._try_resolve_entry(
        {"local": "./local.yaml", "local_from_docker": {"source": "./ignored.yaml"}},
        "demo.task",
        tmp_path,
    )

    assert result is not None
    assert result[1]["name"] == "Local"


def test_pipeline_hydrator_generic_preview_skips_local_materialization(tmp_path: Path):
    source = tmp_path / "component.py"
    source.write_text(
        'def component():\n    """Demo.\n\n    Metadata:\n        version: 1.0.0\n    """\n',
        encoding="utf-8",
    )
    hydrator = PipelineHydrator(client=MagicMock())
    hydrator._resolve_registered_component = MagicMock(return_value=("local", {"name": "Local"}))  # type: ignore[method-assign]

    result = hydrator._try_resolve_entry(
        {"local_from_python": {"file": "./component.py", "output_folder": "./generated"}},
        "demo.task",
        tmp_path,
    )

    assert result == ("local", {"name": "Local"})
    hydrator._resolve_registered_component.assert_called_once()

    hydrator._resolve_registered_component.reset_mock()
    hydrator._resolve_primary = MagicMock(  # type: ignore[method-assign]
        return_value=(
            "primary",
            {"name": "Published", "metadata": {"annotations": {"version": "2.0.0"}}},
        )
    )
    result = hydrator._try_resolve_entry(
        {"local_from_python": {"file": "./component.py", "output_folder": "./generated"}},
        "demo.task",
        tmp_path,
    )

    assert result == (
        "primary",
        {"name": "Published", "metadata": {"annotations": {"version": "2.0.0"}}},
    )
    hydrator._resolve_registered_component.assert_not_called()


def test_pipelines_validate_succeeds_for_minimal_valid_yaml(tmp_path: Path, capsys):
    pipeline_path = _write_pipeline(tmp_path / "pipeline.yaml", _minimal_valid_pipeline())
    app = cli.build_app()

    run_app(app, ["sdk", "pipelines", "validate", str(pipeline_path)])

    assert "Valid pipeline" in capsys.readouterr().out


def test_pipelines_validate_fails_for_invalid_yaml(tmp_path: Path):
    pipeline_path = _write_pipeline(
        tmp_path / "pipeline.yaml",
        {
            "name": "Broken Pipeline",
            "implementation": {
                "graph": {
                    "tasks": {
                        "load": {
                            "componentRef": {"name": "Load"},
                            "arguments": {
                                "rows": {
                                    "taskOutput": {
                                        "taskId": "missing",
                                        "outputName": "rows",
                                    }
                                }
                            },
                        }
                    }
                }
            },
        },
    )
    app = cli.build_app()

    with pytest.raises(SystemExit) as exc_info:
        app(["sdk", "pipelines", "validate", str(pipeline_path)])

    assert exc_info.value.code != 0
    assert "unknown task 'missing'" in str(exc_info.value)


def test_pipelines_validate_rejects_non_string_task_ids(tmp_path: Path):
    pipeline_path = _write_pipeline(
        tmp_path / "pipeline.yaml",
        {
            "name": "Broken Pipeline",
            "implementation": {
                "graph": {
                    "tasks": {
                        1: {"componentRef": {"name": "Leaf"}},
                    },
                },
            },
        },
    )
    app = cli.build_app()

    with pytest.raises(SystemExit) as exc_info:
        app(["sdk", "pipelines", "validate", str(pipeline_path)])

    assert exc_info.value.code != 0
    assert "task ids must be strings" in str(exc_info.value)


def test_pipelines_validate_rejects_bare_container_root(tmp_path: Path):
    pipeline_path = _write_pipeline(
        tmp_path / "pipeline.yaml",
        {
            "name": "Bare Container",
            "implementation": {"container": {"image": "busybox"}},
        },
    )
    app = cli.build_app()

    with pytest.raises(SystemExit) as exc_info:
        app(["sdk", "pipelines", "validate", str(pipeline_path)])

    assert exc_info.value.code != 0
    assert "implementation.graph must be an object" in str(exc_info.value)


def test_vendored_pipeline_schema_loads_and_accepts_minimal_fixture():
    schema = load_pipeline_schema()

    assert schema["title"] == "Tangle Pipeline Schema (generated from TangleML)"
    assert schema["x-tangle-source"]["path"] == "cloud_pipelines_backend/component_structures.py"
    assert validate_pipeline_schema(_minimal_valid_pipeline()) == []


def test_pipeline_schema_validation_rejects_wrong_root_input_type():
    pipeline = _minimal_valid_pipeline()
    pipeline["inputs"] = "not-a-list"

    errors = validate_pipeline_schema(pipeline)

    assert any("'inputs' must be array, got str" in error for error in errors)


def _component_spec(
    *,
    inputs: list[dict] | None = None,
    outputs: list[dict] | None = None,
    implementation: dict | None = None,
) -> dict:
    return {
        "name": "Component",
        "inputs": inputs or [],
        "outputs": outputs or [],
        "implementation": implementation or {"container": {"image": "busybox"}},
    }


def _single_task_pipeline(task: dict, *, inputs: list[dict] | None = None) -> dict:
    return {
        "name": "Pipeline",
        "inputs": inputs or [],
        "implementation": {"graph": {"tasks": {"task": task}}},
    }


def test_component_input_validation_rejects_missing_required_input():
    pipeline = _single_task_pipeline(
        {
            "componentRef": {
                "spec": _component_spec(inputs=[{"name": "query", "type": "String"}])
            }
        }
    )

    assert validate_component_inputs(pipeline) == [
        "Task 'task': required input 'query' has no value or connection"
    ]


def test_component_input_validation_allows_optional_and_default_inputs():
    pipeline = _single_task_pipeline(
        {
            "componentRef": {
                "spec": _component_spec(
                    inputs=[
                        {"name": "optional", "type": "String", "optional": True},
                        {"name": "with_default", "type": "String", "default": "value"},
                    ]
                )
            }
        }
    )

    assert validate_component_inputs(pipeline) == []


def test_component_input_validation_allows_null_default_without_argument():
    pipeline = _single_task_pipeline(
        {
            "componentRef": {
                "spec": _component_spec(
                    inputs=[{"name": "nullable", "type": "String", "default": None}]
                )
            }
        }
    )

    assert validate_component_inputs(pipeline) == []


def test_component_input_validation_allows_unknown_producer_outputs():
    pipeline = {
        "name": "Pipeline",
        "implementation": {
            "graph": {
                "tasks": {
                    "producer": {"componentRef": {"name": "RemoteProducer"}},
                    "consumer": {
                        "componentRef": {
                            "spec": _component_spec(inputs=[{"name": "rows", "type": "String"}])
                        },
                        "arguments": {
                            "rows": {"taskOutput": {"taskId": "producer", "outputName": "rows"}}
                        },
                    },
                }
            }
        },
    }

    assert validate_component_inputs(pipeline) == []


def test_component_input_validation_rejects_known_empty_producer_outputs():
    pipeline = {
        "name": "Pipeline",
        "implementation": {
            "graph": {
                "tasks": {
                    "producer": {"componentRef": {"spec": _component_spec(outputs=[])}},
                    "consumer": {
                        "componentRef": {
                            "spec": _component_spec(inputs=[{"name": "rows", "type": "String"}])
                        },
                        "arguments": {
                            "rows": {"taskOutput": {"taskId": "producer", "outputName": "rows"}}
                        },
                    },
                }
            }
        },
    }

    assert validate_component_inputs(pipeline) == [
        "Task 'consumer': input 'rows' references non-existent output 'rows' on task 'producer'"
    ]


def test_component_input_validation_rejects_bad_refs_on_supplied_optional_inputs():
    pipeline = {
        "name": "Pipeline",
        "inputs": [{"name": "existing", "type": "String"}],
        "implementation": {
            "graph": {
                "tasks": {
                    "producer": {
                        "componentRef": {
                            "spec": _component_spec(outputs=[{"name": "rows", "type": "String"}])
                        }
                    },
                    "consumer": {
                        "componentRef": {
                            "spec": _component_spec(
                                inputs=[
                                    {"name": "optional", "type": "String", "optional": True},
                                    {"name": "with_default", "type": "String", "default": "value"},
                                ]
                            )
                        },
                        "arguments": {
                            "optional": {"graphInput": {"inputName": "missing"}},
                            "with_default": {
                                "taskOutput": {"taskId": "producer", "outputName": "missing"}
                            },
                        },
                    },
                }
            }
        },
    }

    assert validate_component_inputs(pipeline) == [
        "Task 'consumer': input 'optional' references non-existent graph input 'missing'",
        "Task 'consumer': input 'with_default' references non-existent output 'missing' on task 'producer'",
    ]


def test_component_input_validation_rejects_bad_task_output_name():
    pipeline = {
        "name": "Pipeline",
        "implementation": {
            "graph": {
                "tasks": {
                    "producer": {
                        "componentRef": {
                            "spec": _component_spec(outputs=[{"name": "rows", "type": "String"}])
                        }
                    },
                    "consumer": {
                        "componentRef": {
                            "spec": _component_spec(inputs=[{"name": "rows", "type": "String"}])
                        },
                        "arguments": {
                            "rows": {"taskOutput": {"taskId": "producer", "outputName": "missing"}}
                        },
                    },
                }
            }
        },
    }

    assert validate_component_inputs(pipeline) == [
        "Task 'consumer': input 'rows' references non-existent output 'missing' on task 'producer'"
    ]


def test_component_input_validation_rejects_bad_graph_input_ref():
    pipeline = _single_task_pipeline(
        {
            "componentRef": {
                "spec": _component_spec(inputs=[{"name": "query", "type": "String"}])
            },
            "arguments": {"query": {"graphInput": {"inputName": "missing"}}},
        },
        inputs=[{"name": "existing", "type": "String"}],
    )

    assert validate_component_inputs(pipeline) == [
        "Task 'task': input 'query' references non-existent graph input 'missing'"
    ]


def test_collect_pipeline_errors_combines_shape_schema_and_input_wiring():
    pipeline = _single_task_pipeline(
        {
            "componentRef": {
                "spec": _component_spec(inputs=[{"name": "query", "type": "String"}])
            }
        }
    )
    pipeline["inputs"] = "not-a-list"

    errors = collect_pipeline_spec_errors(pipeline)

    assert any("'inputs' must be array, got str" in error for error in errors)
    assert "Task 'task': required input 'query' has no value or connection" in errors


def test_pipelines_diagram_outputs_small_dependency_graph(tmp_path: Path, capsys):
    pipeline_path = _write_pipeline(tmp_path / "pipeline.yaml", _minimal_valid_pipeline())
    app = cli.build_app()

    run_app(app, ["sdk", "pipelines", "diagram", str(pipeline_path)])

    output = capsys.readouterr().out
    assert "```mermaid" in output
    assert "flowchart LR" in output
    assert "extract --> load" in output
    assert "Extract" in output
    assert "Load" in output


def test_pipelines_hydrate_renders_template_and_resolves_local_file_refs(
    tmp_path: Path,
    capsys,
):
    components_dir = tmp_path / "components"
    components_dir.mkdir()
    _write_pipeline(
        components_dir / "echo.yaml",
        {
            "name": "Echo Component",
            "inputs": [{"name": "message", "type": "String"}],
            "outputs": [{"name": "result", "type": "String"}],
            "implementation": {"container": {"image": "python:3.12"}},
        },
    )
    (tmp_path / "pipeline.yaml.j2").write_text(
        "name: {{ pipeline_name }}\n"
        "inputs:\n"
        "  - name: message\n"
        "    type: String\n"
        "implementation:\n"
        "  graph:\n"
        "    tasks:\n"
        "      echo:\n"
        "        componentRef:\n"
        "          url: file://{{ component_file }}\n"
        "        arguments:\n"
        "          message:\n"
        "            graphInput:\n"
        "              inputName: message\n",
        encoding="utf-8",
    )
    config_path = _write_pipeline(
        tmp_path / "pipeline.config.yaml",
        {
            "template_file": "pipeline.yaml.j2",
            "pipeline_name": "Config Name",
            "component_file": "components/echo.yaml",
        },
    )
    app = cli.build_app()

    run_app(
        app,
        [
            "sdk",
            "pipelines",
            "hydrate",
            str(config_path),
            "--var",
            "pipeline_name=Hydrated Pipeline",
        ],
    )

    hydrated = yaml.safe_load(capsys.readouterr().out)
    assert hydrated["name"] == "Hydrated Pipeline"
    task = hydrated["implementation"]["graph"]["tasks"]["echo"]
    assert set(task["componentRef"]) == {"name", "digest", "spec"}
    assert task["componentRef"]["name"] == "Echo Component"
    assert task["componentRef"]["spec"]["implementation"]["container"]["image"] == "python:3.12"


def test_pipelines_hydrate_log_type_none_suppresses_progress(tmp_path: Path, capsys):
    component_path = _write_pipeline(
        tmp_path / "component.yaml",
        {
            "name": "Local Component",
            "implementation": {"container": {"image": "python:3.12"}},
        },
    )
    pipeline_path = _write_pipeline(
        tmp_path / "pipeline.yaml",
        {
            "name": "Pipeline",
            "implementation": {
                "graph": {
                    "tasks": {
                        "local": {
                            "componentRef": {"url": f"file://{component_path.name}"}
                        }
                    }
                }
            },
        },
    )
    app = cli.build_app()

    run_app(
        app,
        ["sdk", "pipelines", "hydrate", str(pipeline_path), "--log-type", "none"],
    )

    captured = capsys.readouterr()
    hydrated = yaml.safe_load(captured.out)
    assert hydrated["implementation"]["graph"]["tasks"]["local"]["componentRef"]["spec"]["name"] == "Local Component"
    assert captured.err == ""


def test_pipelines_hydrate_writes_output_when_requested(tmp_path: Path, capsys):
    component_path = _write_pipeline(
        tmp_path / "component.yaml",
        {
            "name": "Local Component",
            "implementation": {"container": {"image": "python:3.12"}},
        },
    )
    pipeline_path = _write_pipeline(
        tmp_path / "pipeline.yaml",
        {
            "name": "Pipeline",
            "implementation": {
                "graph": {
                    "tasks": {
                        "local": {
                            "componentRef": {"url": f"file://{component_path.name}"}
                        }
                    }
                }
            },
        },
    )
    output_path = tmp_path / "hydrated.yaml"
    app = cli.build_app()

    run_app(
        app,
        ["sdk", "pipelines", "hydrate", str(pipeline_path), "--output", str(output_path)],
    )

    assert "1 component(s) resolved" in capsys.readouterr().out
    hydrated = yaml.safe_load(output_path.read_text(encoding="utf-8"))
    local_ref = hydrated["implementation"]["graph"]["tasks"]["local"]["componentRef"]
    assert local_ref["spec"]["name"] == "Local Component"


def test_pipelines_hydrate_local_file_refs_do_not_import_native_api(tmp_path: Path):
    component_path = _write_pipeline(
        tmp_path / "component.yaml",
        {
            "name": "Local Only Component",
            "implementation": {"container": {"image": "python:3.12"}},
        },
    )
    pipeline_path = _write_pipeline(
        tmp_path / "pipeline.yaml",
        {
            "name": "Pipeline",
            "implementation": {
                "graph": {
                    "tasks": {
                        "local": {
                            "componentRef": {"url": f"file://{component_path.name}"}
                        }
                    }
                }
            },
        },
    )
    script = textwrap.dedent(
        f"""
        import builtins
        import sys
        from pathlib import Path

        for name in list(sys.modules):
            if name == "tangle_api" or name.startswith("tangle_api."):
                del sys.modules[name]

        original_import = builtins.__import__

        def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "tangle_api" or name.startswith("tangle_api."):
                raise AssertionError(f"unexpected native API import: {{name}}")
            return original_import(name, globals, locals, fromlist, level)

        builtins.__import__ = guarded_import

        from tangle_cli.pipelines import hydrate_pipeline_file

        result = hydrate_pipeline_file(Path({str(pipeline_path)!r}))
        assert "Local Only Component" in result.content
        assert "tangle_api" not in sys.modules
        """
    )

    subprocess.run([sys.executable, "-c", script], check=True, text=True)


def test_pipelines_hydrate_nested_file_refs_use_loaded_component_source_dir(
    tmp_path: Path,
    capsys,
):
    subgraphs_dir = tmp_path / "subgraphs"
    components_dir = tmp_path / "components"
    subgraphs_dir.mkdir()
    components_dir.mkdir()
    _write_pipeline(
        components_dir / "grandchild.yaml",
        {
            "name": "Grandchild Component",
            "implementation": {"container": {"image": "python:3.12"}},
        },
    )
    _write_pipeline(
        subgraphs_dir / "child.yaml",
        {
            "name": "Child Subgraph",
            "implementation": {
                "graph": {
                    "tasks": {
                        "grandchild": {
                            "componentRef": {
                                "url": "file://./../components/grandchild.yaml"
                            }
                        }
                    }
                }
            },
        },
    )
    pipeline_path = _write_pipeline(
        tmp_path / "pipeline.yaml",
        {
            "name": "Pipeline",
            "implementation": {
                "graph": {
                    "tasks": {
                        "child": {"componentRef": {"url": "file://./subgraphs/child.yaml"}}
                    }
                }
            },
        },
    )
    app = cli.build_app()

    run_app(app, ["sdk", "pipelines", "hydrate", str(pipeline_path)])

    hydrated = yaml.safe_load(capsys.readouterr().out)
    child_spec = hydrated["implementation"]["graph"]["tasks"]["child"]["componentRef"]["spec"]
    grandchild_ref = child_spec["implementation"]["graph"]["tasks"]["grandchild"]["componentRef"]
    assert grandchild_ref["name"] == "Grandchild Component"
    assert grandchild_ref["spec"]["implementation"]["container"]["image"] == "python:3.12"
    assert "_source_dir" not in child_spec
    assert "_source_dir" not in grandchild_ref["spec"]


def test_pipelines_hydrate_cache_separates_same_relative_ref_by_source_dir(
    tmp_path: Path,
    capsys,
):
    left_dir = tmp_path / "left"
    right_dir = tmp_path / "right"
    left_dir.mkdir()
    right_dir.mkdir()
    _write_pipeline(
        left_dir / "leaf.yaml",
        {
            "name": "Left Leaf",
            "implementation": {"container": {"image": "left:latest"}},
        },
    )
    _write_pipeline(
        right_dir / "leaf.yaml",
        {
            "name": "Right Leaf",
            "implementation": {"container": {"image": "right:latest"}},
        },
    )
    _write_pipeline(
        left_dir / "subgraph.yaml",
        {
            "name": "Left Subgraph",
            "implementation": {
                "graph": {
                    "tasks": {"leaf": {"componentRef": {"url": "file://leaf.yaml"}}}
                }
            },
        },
    )
    _write_pipeline(
        right_dir / "subgraph.yaml",
        {
            "name": "Right Subgraph",
            "implementation": {
                "graph": {
                    "tasks": {"leaf": {"componentRef": {"url": "file://leaf.yaml"}}}
                }
            },
        },
    )
    pipeline_path = _write_pipeline(
        tmp_path / "pipeline.yaml",
        {
            "name": "Pipeline",
            "implementation": {
                "graph": {
                    "tasks": {
                        "left": {"componentRef": {"url": "file://left/subgraph.yaml"}},
                        "right": {"componentRef": {"url": "file://right/subgraph.yaml"}},
                    }
                }
            },
        },
    )
    app = cli.build_app()

    run_app(app, ["sdk", "pipelines", "hydrate", str(pipeline_path)])

    hydrated = yaml.safe_load(capsys.readouterr().out)
    tasks = hydrated["implementation"]["graph"]["tasks"]
    left_leaf = tasks["left"]["componentRef"]["spec"]["implementation"]["graph"]["tasks"]["leaf"]["componentRef"]
    right_leaf = tasks["right"]["componentRef"]["spec"]["implementation"]["graph"]["tasks"]["leaf"]["componentRef"]
    assert left_leaf["name"] == "Left Leaf"
    assert left_leaf["spec"]["implementation"]["container"]["image"] == "left:latest"
    assert right_leaf["name"] == "Right Leaf"
    assert right_leaf["spec"]["implementation"]["container"]["image"] == "right:latest"


def test_pipelines_hydrate_cache_separates_relative_resolve_refs_by_source_dir(
    tmp_path: Path,
    capsys,
):
    left_dir = tmp_path / "left"
    right_dir = tmp_path / "right"
    left_dir.mkdir()
    right_dir.mkdir()
    for side, image in (("left", "left:latest"), ("right", "right:latest")):
        side_dir = tmp_path / side
        _write_pipeline(
            side_dir / "leaf.yaml",
            {
                "name": f"{side.title()} Leaf",
                "implementation": {"container": {"image": image}},
            },
        )
        _write_pipeline(
            side_dir / "components.resolve.yaml",
            {"leaf": {"url": "file://leaf.yaml"}},
        )
        _write_pipeline(
            side_dir / "subgraph.yaml",
            {
                "name": f"{side.title()} Subgraph",
                "implementation": {
                    "graph": {
                        "tasks": {
                            "leaf": {
                                "componentRef": {
                                    "url": "resolve://./components.resolve.yaml#leaf"
                                }
                            }
                        }
                    }
                },
            },
        )
    pipeline_path = _write_pipeline(
        tmp_path / "pipeline.yaml",
        {
            "name": "Pipeline",
            "implementation": {
                "graph": {
                    "tasks": {
                        "left": {"componentRef": {"url": "file://left/subgraph.yaml"}},
                        "right": {"componentRef": {"url": "file://right/subgraph.yaml"}},
                    }
                }
            },
        },
    )
    app = cli.build_app()

    run_app(app, ["sdk", "pipelines", "hydrate", str(pipeline_path)])

    hydrated = yaml.safe_load(capsys.readouterr().out)
    tasks = hydrated["implementation"]["graph"]["tasks"]
    left_leaf = tasks["left"]["componentRef"]["spec"]["implementation"]["graph"]["tasks"]["leaf"]["componentRef"]
    right_leaf = tasks["right"]["componentRef"]["spec"]["implementation"]["graph"]["tasks"]["leaf"]["componentRef"]
    assert left_leaf["name"] == "Left Leaf"
    assert left_leaf["spec"]["implementation"]["container"]["image"] == "left:latest"
    assert right_leaf["name"] == "Right Leaf"
    assert right_leaf["spec"]["implementation"]["container"]["image"] == "right:latest"


def test_pipelines_hydrate_resolve_url_fragment_uses_config_relative_local_refs(
    tmp_path: Path,
    capsys,
):
    root = tmp_path / "project"
    pipeline_dir = root / "pipelines"
    component_dir = root / "components"
    pipeline_dir.mkdir(parents=True)
    component_dir.mkdir()
    _write_pipeline(
        component_dir / "truncate.yaml",
        {
            "name": "Truncate If Time",
            "metadata": {"annotations": {"version": "1.0"}},
            "implementation": {"container": {"image": "python:3.12"}},
        },
    )
    _write_pipeline(
        root / "components.resolve.yaml",
        {
            "_defaults": {"publisher": "unused@example.com"},
            "truncate-if-time": {"local": "components/truncate.yaml"},
        },
    )
    pipeline_path = _write_pipeline(
        pipeline_dir / "pipeline.yaml",
        {
            "name": "Pipeline",
            "implementation": {
                "graph": {
                    "tasks": {
                        "truncate": {
                            "componentRef": {
                                "url": "resolve://../components.resolve.yaml#truncate-if-time"
                            }
                        }
                    }
                }
            },
        },
    )
    app = cli.build_app()

    run_app(app, ["sdk", "pipelines", "hydrate", str(pipeline_path)])

    hydrated = yaml.safe_load(capsys.readouterr().out)
    ref = hydrated["implementation"]["graph"]["tasks"]["truncate"]["componentRef"]
    assert ref["name"] == "Truncate If Time"
    assert ref["spec"]["metadata"]["annotations"]["version"] == "1.0"


def test_pipelines_hydrate_http_url_refs(monkeypatch, tmp_path: Path, capsys):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return (
                b"name: Remote Component\n"
                b"implementation:\n"
                b"  container:\n"
                b"    image: python:3.12\n"
            )

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout: FakeResponse())
    pipeline_path = _write_pipeline(
        tmp_path / "pipeline.yaml",
        {
            "name": "Pipeline",
            "implementation": {
                "graph": {
                    "tasks": {
                        "remote": {
                            "componentRef": {"url": "https://example.test/component.yaml"}
                        }
                    }
                }
            },
        },
    )
    app = cli.build_app()

    run_app(app, ["sdk", "pipelines", "hydrate", str(pipeline_path)])

    hydrated = yaml.safe_load(capsys.readouterr().out)
    ref = hydrated["implementation"]["graph"]["tasks"]["remote"]["componentRef"]
    assert ref["name"] == "Remote Component"
    assert ref["spec"]["implementation"]["container"]["image"] == "python:3.12"


def _write_local_from_python_pipeline(
    project_dir: Path,
    python_file: str,
    *,
    mode: str | None = None,
    resolve_root: str | None = None,
) -> Path:
    gen_config = {
        "file": python_file,
        "output_folder": "./generated",
    }
    if mode is not None:
        gen_config["mode"] = mode
    if resolve_root is not None:
        gen_config["resolve_root"] = resolve_root
    _write_pipeline(
        project_dir / "components.resolve.yaml",
        {"generated": {"local_from_python": gen_config}},
    )
    return _write_pipeline(
        project_dir / "pipeline.yaml",
        {
            "name": "Pipeline",
            "implementation": {
                "graph": {
                    "tasks": {
                        "generated": {
                            "componentRef": {"url": "resolve://./components.resolve.yaml#generated"}
                        }
                    }
                }
            },
        },
    )


def test_pipelines_hydrate_local_from_python_trusts_project_paths(
    monkeypatch,
    tmp_path: Path,
    capsys,
):
    from tangle_cli import pipeline_hydrator as hydrator_module

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    python_file = project_dir / "component.py"
    python_file.write_text("# trusted project component\n", encoding="utf-8")
    pipeline_path = _write_local_from_python_pipeline(project_dir, "./component.py")
    regenerated: list[Path] = []

    def fake_regenerate_yaml(**kwargs):
        regenerated.append(kwargs["python_file"])
        kwargs["output_path"].write_text(
            "name: Generated Component\nimplementation:\n  container:\n    image: busybox\n",
            encoding="utf-8",
        )
        return True

    monkeypatch.setattr(hydrator_module, "regenerate_yaml", fake_regenerate_yaml)
    app = cli.build_app()

    run_app(app, ["sdk", "pipelines", "hydrate", str(pipeline_path)])

    hydrated = yaml.safe_load(capsys.readouterr().out)
    ref = hydrated["implementation"]["graph"]["tasks"]["generated"]["componentRef"]
    assert ref["name"] == "Generated Component"
    assert regenerated == [python_file.resolve()]


def test_pipelines_hydrate_local_from_python_forwards_bundle_mode_and_resolve_root(
    monkeypatch,
    tmp_path: Path,
):
    from tangle_cli import pipeline_hydrator as hydrator_module
    from tangle_cli.pipelines import hydrate_pipeline_file

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    src_dir = project_dir / "src"
    src_dir.mkdir()
    python_file = src_dir / "component.py"
    python_file.write_text("# trusted project component\n", encoding="utf-8")
    pipeline_path = _write_local_from_python_pipeline(
        project_dir,
        "./src/component.py",
        mode="bundle",
        resolve_root="./src",
    )
    calls: list[dict[str, object]] = []

    def fake_regenerate_yaml(**kwargs):
        calls.append(kwargs)
        kwargs["output_path"].write_text(
            "name: Generated Component\nimplementation:\n  container:\n    image: busybox\n",
            encoding="utf-8",
        )
        return True

    monkeypatch.setattr(hydrator_module, "regenerate_yaml", fake_regenerate_yaml)

    hydrate_pipeline_file(pipeline_path)

    assert calls[0]["python_file"] == python_file.resolve()
    assert calls[0]["mode"] == "bundle"
    assert calls[0]["resolve_root"] == src_dir.resolve()


def test_pipelines_hydrate_local_from_python_refuses_untrusted_absolute_path(
    monkeypatch,
    tmp_path: Path,
):
    from tangle_cli import pipeline_hydrator as hydrator_module
    from tangle_cli.pipelines import PipelineValidationError, hydrate_pipeline_file

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside_python = outside_dir / "evil.py"
    outside_python.write_text("raise RuntimeError('must not execute')\n", encoding="utf-8")
    pipeline_path = _write_local_from_python_pipeline(project_dir, str(outside_python))
    regenerated: list[Path] = []

    def fake_regenerate_yaml(**kwargs):
        regenerated.append(kwargs["python_file"])
        raise AssertionError("untrusted local_from_python must be blocked before generation")

    monkeypatch.setattr(hydrator_module, "regenerate_yaml", fake_regenerate_yaml)

    with pytest.raises(PipelineValidationError, match="Refusing to execute untrusted local_from_python source"):
        hydrate_pipeline_file(pipeline_path)
    assert regenerated == []


def test_pipelines_hydrate_local_from_python_ignores_untrusted_resolve_root(
    monkeypatch,
    tmp_path: Path,
):
    from tangle_cli import pipeline_hydrator as hydrator_module
    from tangle_cli.pipelines import PipelineValidationError, hydrate_pipeline_file

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside_python = outside_dir / "evil.py"
    outside_python.write_text("raise RuntimeError('must not execute')\n", encoding="utf-8")
    pipeline_path = _write_local_from_python_pipeline(
        project_dir,
        str(outside_python),
        resolve_root=str(outside_dir),
    )

    def fake_regenerate_yaml(**kwargs):
        raise AssertionError("untrusted resolve_root must not authorize execution")

    monkeypatch.setattr(hydrator_module, "regenerate_yaml", fake_regenerate_yaml)

    with pytest.raises(PipelineValidationError, match="Refusing to execute untrusted local_from_python source"):
        hydrate_pipeline_file(pipeline_path)


def test_pipelines_hydrate_local_from_python_blocks_symlink_escape(
    monkeypatch,
    tmp_path: Path,
):
    from tangle_cli import pipeline_hydrator as hydrator_module
    from tangle_cli.pipelines import PipelineValidationError, hydrate_pipeline_file

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside_python = outside_dir / "evil.py"
    outside_python.write_text("raise RuntimeError('must not execute')\n", encoding="utf-8")
    symlink = project_dir / "linked.py"
    symlink.symlink_to(outside_python)
    pipeline_path = _write_local_from_python_pipeline(project_dir, "./linked.py")

    def fake_regenerate_yaml(**kwargs):
        raise AssertionError("symlink escape must be blocked before generation")

    monkeypatch.setattr(hydrator_module, "regenerate_yaml", fake_regenerate_yaml)

    with pytest.raises(PipelineValidationError, match="Refusing to execute untrusted local_from_python source"):
        hydrate_pipeline_file(pipeline_path)


def test_trusted_python_source_globs_are_path_segment_aware(tmp_path: Path):
    from tangle_cli.hydration_trust import is_trusted_python_source

    project_dir = tmp_path / "project"
    components_dir = project_dir / "components"
    nested_dir = components_dir / "nested"
    nested_dir.mkdir(parents=True)
    direct_source = components_dir / "component.py"
    nested_source = nested_dir / "component.py"
    direct_source.write_text("# trusted\n", encoding="utf-8")
    nested_source.write_text("# not matched by single-segment glob\n", encoding="utf-8")

    single_segment_pattern = str(components_dir / "*.py")
    recursive_pattern = str(components_dir / "**" / "*.py")

    assert is_trusted_python_source(direct_source, trusted_sources=[single_segment_pattern]) is True
    assert is_trusted_python_source(nested_source, trusted_sources=[single_segment_pattern]) is False
    assert is_trusted_python_source(direct_source, trusted_sources=[recursive_pattern]) is True
    assert is_trusted_python_source(nested_source, trusted_sources=[recursive_pattern]) is True


def test_pipelines_hydrate_trusted_hydration_allows_untrusted_python_source(
    monkeypatch,
    tmp_path: Path,
    capsys,
):
    from tangle_cli import pipeline_hydrator as hydrator_module

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside_python = outside_dir / "component.py"
    outside_python.write_text("# trusted by explicit override\n", encoding="utf-8")
    pipeline_path = _write_local_from_python_pipeline(project_dir, str(outside_python))
    regenerated: list[Path] = []

    def fake_regenerate_yaml(**kwargs):
        regenerated.append(kwargs["python_file"])
        kwargs["output_path"].write_text(
            "name: Explicitly Trusted Component\nimplementation:\n  container:\n    image: busybox\n",
            encoding="utf-8",
        )
        return True

    monkeypatch.setattr(hydrator_module, "regenerate_yaml", fake_regenerate_yaml)
    app = cli.build_app()

    run_app(app, ["sdk", "pipelines", "hydrate", str(pipeline_path), "--trusted-hydration"])

    hydrated = yaml.safe_load(capsys.readouterr().out)
    ref = hydrated["implementation"]["graph"]["tasks"]["generated"]["componentRef"]
    assert ref["name"] == "Explicitly Trusted Component"
    assert regenerated == [outside_python.resolve()]


def test_pipelines_hydrate_name_refs_use_api_without_env_credentials_for_config_base_url(
    monkeypatch,
    tmp_path: Path,
    capsys,
):
    from tangle_cli import client as client_module

    created_clients = []

    class FakeClient:
        def __init__(self, **kwargs):
            created_clients.append(kwargs)

        def find_existing_components(self, components, **kwargs):
            assert components == ["Remote Name", "[Official] Remote Name"]
            return [SimpleNamespace(digest="sha256:remote", version="2.0")]

        def get_component_spec(self, digest):
            assert digest == "sha256:remote"
            return SimpleNamespace(
                data={
                    "name": "Remote Name",
                    "metadata": {"annotations": {"version": "2.0"}},
                    "implementation": {"container": {"image": "python:3.12"}},
                }
            )

    monkeypatch.setenv("TANGLE_API_TOKEN", "ambient-token")
    monkeypatch.setattr(client_module, "TangleApiClient", FakeClient)
    config_path = _write_pipeline(
        tmp_path / "hydrate-config.yaml",
        {"base_url": "https://api.test"},
    )
    pipeline_path = _write_pipeline(
        tmp_path / "pipeline.yaml",
        {
            "name": "Pipeline",
            "implementation": {
                "graph": {
                    "tasks": {
                        "remote": {"componentRef": {"name": "Remote Name"}}
                    }
                }
            },
        },
    )
    app = cli.build_app()

    run_app(app, ["sdk", "pipelines", "hydrate", str(pipeline_path), "--config", str(config_path)])

    hydrated = yaml.safe_load(capsys.readouterr().out)
    ref = hydrated["implementation"]["graph"]["tasks"]["remote"]["componentRef"]
    assert ref["digest"] == "sha256:remote"
    assert ref["spec"]["name"] == "Remote Name"
    assert created_clients[0]["base_url"] == "https://api.test"
    assert created_clients[0]["token"] is None
    assert created_clients[0]["include_env_credentials"] is False


def test_pipeline_hydrator_resolve_config_name_uses_filters(tmp_path: Path):
    from tangle_cli.pipeline_hydrator import PipelineHydrator

    specs = {
        "sha256:old": {
            "name": "Thing",
            "metadata": {"annotations": {"version": "1.0", "team": "x"}},
            "implementation": {"container": {"image": "old"}},
        },
        "sha256:wrong-team": {
            "name": "Thing",
            "metadata": {"annotations": {"version": "3.0", "team": "y"}},
            "implementation": {"container": {"image": "wrong-team"}},
        },
        "sha256:match-low": {
            "name": "Thing",
            "metadata": {"annotations": {"version": "2.1", "team": "x"}},
            "implementation": {"container": {"image": "match-low"}},
        },
        "sha256:match-high": {
            "name": "Thing",
            "metadata": {"annotations": {"version": "2.4", "team": "x"}},
            "implementation": {"container": {"image": "match-high"}},
        },
    }
    calls = []

    class FakeClient:
        def find_existing_components(self, components, **kwargs):
            calls.append({"components": components, **kwargs})
            return [
                SimpleNamespace(digest="sha256:old", version="1.0"),
                SimpleNamespace(digest="sha256:wrong-team", version="3.0"),
                SimpleNamespace(digest="sha256:match-low", version="2.1"),
                SimpleNamespace(digest="sha256:match-high", version="2.4"),
            ]

        def get_component_spec(self, digest):
            return SimpleNamespace(data=specs[digest])

    hydrator = PipelineHydrator(client=FakeClient())

    digest, spec = hydrator._resolve_from_config(
        {
            "name": "Thing",
            "publisher": "alice@example.com",
            "version": ">=2",
            "annotations": {"team": "x"},
        },
        "Pipeline.task",
        tmp_path,
    )

    assert digest == "sha256:match-high"
    assert spec["implementation"]["container"]["image"] == "match-high"
    assert calls == [
        {
            "components": ["Thing", "[Official] Thing"],
            "verbose": False,
            "published_by": "alice@example.com",
        }
    ]


def test_pipeline_hydrator_unsupported_resolver_lists_available_resolvers(tmp_path: Path):
    from tangle_cli.pipeline_hydrator import PipelineHydrator, UnsupportedHydrationFeatureError

    hydrator = PipelineHydrator()

    with pytest.raises(UnsupportedHydrationFeatureError) as exc_info:
        hydrator._resolve_from_config(
            {"local_from_docker": {"source": "component.yaml"}},
            "Pipeline.task",
            tmp_path,
        )

    message = str(exc_info.value)
    assert "local_from_docker" in message
    assert "Available resolvers" in message
    assert "file" in message
    assert "local_from_python" in message


def test_pipeline_hydrator_resolver_registry_can_add_downstream_kind(tmp_path: Path):
    from tangle_cli.pipeline_hydrator import PipelineHydrator

    calls = []

    def fake_docker_resolver(hydrator, value, path, base_dir):
        calls.append({"value": value, "path": path, "base_dir": base_dir})
        return (
            "sha256:docker",
            {"name": "Docker Component", "implementation": {"container": {"image": "x"}}},
        )

    hydrator = PipelineHydrator(
        component_resolvers={"local_from_docker": fake_docker_resolver}
    )

    result = hydrator._resolve_from_config(
        {"local_from_docker": {"source": "component.yaml"}},
        "Pipeline.task",
        tmp_path,
    )

    assert result == (
        "sha256:docker",
        {"name": "Docker Component", "implementation": {"container": {"image": "x"}}},
    )
    assert calls == [
        {
            "value": {"source": "component.yaml"},
            "path": "Pipeline.task",
            "base_dir": tmp_path,
        }
    ]


def test_pipeline_hydrator_resolver_registry_passes_structured_context(tmp_path: Path):
    from tangle_cli.pipeline_hydrator import PipelineHydrator, ResolverContext

    calls: list[ResolverContext] = []

    def fake_resolver(hydrator, value, path, base_dir, context):
        calls.append(context)
        return (
            "sha256:custom",
            {"name": "Custom Component", "implementation": {"container": {"image": "x"}}},
        )

    hydrator = PipelineHydrator(
        component_resolvers={"custom": fake_resolver},
        trusted_python_sources=[str(tmp_path)],
        allow_all_hydration=True,
        error_policy="raise",
    )

    result = hydrator._resolve_from_config(
        {"custom": {"source": "component.yaml", "output_folder": "generated"}},
        "Pipeline.task",
        tmp_path,
    )

    assert result == (
        "sha256:custom",
        {"name": "Custom Component", "implementation": {"container": {"image": "x"}}},
    )
    assert calls
    context = calls[0]
    assert context.kind == "custom"
    assert context.path == "Pipeline.task"
    assert context.base_dir == tmp_path
    assert context.source_path == tmp_path / "component.yaml"
    assert context.output_folder == tmp_path / "generated"
    assert context.base_dirs[0] == tmp_path
    assert context.trusted_python_sources == (str(tmp_path),)
    assert context.allow_all_hydration is True
    assert context.error_policy == "raise"


def test_pipeline_hydrator_resolver_registry_keeps_legacy_signature(tmp_path: Path):
    from tangle_cli.pipeline_hydrator import PipelineHydrator

    calls = []

    def legacy_resolver(hydrator, value, path, base_dir):
        calls.append({"value": value, "path": path, "base_dir": base_dir})
        return (
            "sha256:legacy",
            {"name": "Legacy Component", "implementation": {"container": {"image": "x"}}},
        )

    hydrator = PipelineHydrator(component_resolvers={"legacy": legacy_resolver})

    result = hydrator._resolve_from_config(
        {"legacy": "component.yaml"},
        "Pipeline.task",
        tmp_path,
    )

    assert result == (
        "sha256:legacy",
        {"name": "Legacy Component", "implementation": {"container": {"image": "x"}}},
    )
    assert calls == [{"value": "component.yaml", "path": "Pipeline.task", "base_dir": tmp_path}]


def test_pipeline_hydrator_uri_hooks_cover_top_level_and_resolve_config():
    from tangle_cli.pipeline_hydrator import PipelineHydrator, ResolverContext

    output: dict[str, str] = {}
    contexts: list[ResolverContext] = []
    sources = {
        "mem://pipeline": yaml.safe_dump({
            "name": "Pipeline",
            "implementation": {
                "graph": {
                    "tasks": {
                        "task": {"componentRef": {"url": "resolve://mem://resolve#thing"}}
                    }
                }
            },
        }),
        "mem://resolve": yaml.safe_dump({"thing": {"url": "mem://component"}}),
        "mem://component": yaml.safe_dump({
            "name": "Memory Component",
            "implementation": {"container": {"image": "python:3.12"}},
        }),
    }

    def reader(hydrator, uri, context):
        contexts.append(context)
        return sources[uri]

    def writer(hydrator, uri, content, context):
        contexts.append(context)
        output[uri] = content

    hydrator = PipelineHydrator(uri_readers={"mem": reader}, uri_writers={"mem": writer})

    result = hydrator.hydrate_file("mem://pipeline", "mem://hydrated")

    assert "mem://hydrated" in output
    hydrated = yaml.safe_load(output["mem://hydrated"])
    ref = hydrated["implementation"]["graph"]["tasks"]["task"]["componentRef"]
    assert ref["digest"] == result.data["implementation"]["graph"]["tasks"]["task"]["componentRef"]["digest"]
    assert ref["spec"]["name"] == "Memory Component"
    assert {context.kind for context in contexts} == {"mem"}
    assert {context.path for context in contexts} >= {"pipeline", "Pipeline.task", "output"}


def test_pipeline_hydrator_normalizes_model_specs_from_clients_and_resolvers():
    from tangle_cli.pipeline_hydrator import PipelineHydrator

    class ModelSpec:
        def __init__(self, data):
            self.data = data

        def to_mutable_spec_dict(self):
            return self.data

    source_spec = {
        "name": "Model Component",
        "metadata": {"annotations": {"version": "1.0"}},
    }

    class FakeClient:
        def get_component_spec(self, digest):
            assert digest == "sha256:model"
            return ModelSpec(source_spec)

    hydrator = PipelineHydrator(client=FakeClient(), upgrade_deprecated=False)

    digest, fetched = hydrator.fetch_component("sha256:model")
    fetched["metadata"]["annotations"]["version"] = "mutated"

    assert digest == "sha256:model"
    assert source_spec["metadata"]["annotations"]["version"] == "1.0"

    hydrator.register_component_resolver(
        "custom",
        lambda *_args: ("sha256:custom", SimpleNamespace(data=source_spec)),
    )
    resolved_digest, resolved_spec = hydrator._resolve_registered_component(
        "custom",
        "unused",
        "Pipeline.task",
        None,
    )
    resolved_spec["metadata"]["annotations"]["version"] = "resolver-mutated"

    assert resolved_digest == "sha256:custom"
    assert source_spec["metadata"]["annotations"]["version"] == "1.0"


def test_pipeline_hydrator_uri_reader_soft_failures_respect_error_policy():
    from tangle_cli.logger import CaptureLogger
    from tangle_cli.pipeline_hydrator import HydrationError, PipelineHydrator

    sources = {
        "mem://missing": FileNotFoundError("missing"),
        "mem://invalid": "name: [",
        "mem://empty": "",
        "mem://list": "- not\n- a mapping\n",
        "mem://template": yaml.safe_dump({"template_file": "component.yaml.j2"}),
    }

    def reader(_hydrator, uri, _context):
        value = sources[uri]
        if isinstance(value, Exception):
            raise value
        return value

    logger = CaptureLogger()
    hydrator = PipelineHydrator(uri_readers={"mem": reader}, logger=logger)

    for uri in sources:
        assert hydrator._fetch_component_from_uri(uri, "Pipeline.task") is None

    logs = logger.get_logs() or ""
    assert "Component not found at URI mem://missing" in logs
    assert "Failed to parse component YAML from mem://invalid" in logs
    assert "Failed to parse YAML from mem://empty" in logs
    assert "expected a mapping" in logs
    assert "non-local URI is a template_file config" in logs

    strict = PipelineHydrator(uri_readers={"mem": reader}, error_policy="raise")
    with pytest.raises(HydrationError, match="expected a mapping"):
        strict._fetch_component_from_uri("mem://list", "Pipeline.task")

    with pytest.raises(HydrationError, match="Pipeline not found at URI mem://missing"):
        strict.hydrate_file("mem://missing")


def test_pipeline_hydrator_url_dispatch_soft_fails_unexpected_errors(tmp_path: Path):
    from tangle_cli.logger import CaptureLogger
    from tangle_cli.pipeline_hydrator import HydrationError, PipelineHydrator

    component_path = tmp_path / "component.yaml"
    component_path.write_text(
        yaml.safe_dump({"name": "Local", "implementation": {"container": {"image": "x"}}}),
        encoding="utf-8",
    )

    def failing_resolver(*_args):
        raise RuntimeError("boom")

    logger = CaptureLogger()
    hydrator = PipelineHydrator(
        component_resolvers={"boom": failing_resolver},
        logger=logger,
    )

    digest, spec = hydrator._fetch_component_by_url("component.yaml", "Pipeline.task", tmp_path)
    assert digest
    assert spec["name"] == "Local"

    assert hydrator._fetch_component_by_url("boom://component", "Pipeline.task", tmp_path) is None
    assert "Failed to fetch component from URL boom://component: boom" in (logger.get_logs() or "")

    strict = PipelineHydrator(
        component_resolvers={"boom": failing_resolver},
        error_policy="raise",
    )
    with pytest.raises(HydrationError, match="Failed to fetch component from URL"):
        strict._fetch_component_by_url("boom://component", "Pipeline.task", tmp_path)


def test_pipeline_hydrator_postprocess_loaded_local_spec_hook(tmp_path: Path):
    from tangle_cli import utils
    from tangle_cli.logger import CaptureLogger
    from tangle_cli.pipeline_hydrator import PipelineHydrator

    (tmp_path / "component.yaml.j2").write_text(
        textwrap.dedent(
            """
            name: "{{ name }}"
            implementation:
              container:
                image: python:3.12
            """
        ),
        encoding="utf-8",
    )
    _write_pipeline(
        tmp_path / "component-config.yaml",
        {"template_file": "component.yaml.j2", "name": "Rendered"},
    )

    calls = []

    class HookedHydrator(PipelineHydrator):
        def postprocess_loaded_local_spec(
            self,
            spec,
            *,
            file_path,
            yaml_text,
            rendered_from_template,
        ):
            calls.append(
                {
                    "file_path": file_path,
                    "yaml_text": yaml_text,
                    "rendered_from_template": rendered_from_template,
                    "source_dir": spec.get("_source_dir"),
                }
            )
            updated = dict(spec)
            updated["name"] = "Postprocessed"
            updated["metadata"] = {"annotations": {"postprocessed": "true"}}
            return updated

    logger = CaptureLogger()
    hydrator = HookedHydrator(logger=logger)

    digest, spec = hydrator._fetch_component_from_file_url(
        "file://./component-config.yaml",
        "Pipeline.task",
        tmp_path,
    )

    assert spec["name"] == "Postprocessed"
    assert spec["metadata"]["annotations"]["postprocessed"] == "true"
    assert len(calls) == 1
    assert calls[0]["file_path"] == tmp_path / "component-config.yaml"
    assert calls[0]["rendered_from_template"] is True
    assert calls[0]["source_dir"] == str(tmp_path)
    assert "name: \"Rendered\"" in calls[0]["yaml_text"]
    assert digest == utils.compute_text_digest(calls[0]["yaml_text"])
    assert "Loaded component: Postprocessed" in (logger.get_logs() or "")


def test_pipeline_hydrator_recursive_context_flows_to_child_templates(tmp_path: Path):
    from tangle_cli.pipeline_hydrator import PipelineHydrator

    (tmp_path / "pipeline.yaml.j2").write_text(
        textwrap.dedent(
            """
            name: Pipeline
            implementation:
              graph:
                tasks:
                  child:
                    componentRef:
                      url: file://./child-config.yaml
            """
        ),
        encoding="utf-8",
    )
    (tmp_path / "child.yaml.j2").write_text(
        textwrap.dedent(
            """
            name: "Child {{ shared }} {{ parent_only }} {{ child_only }}"
            implementation:
              container:
                image: python:3.12
            """
        ),
        encoding="utf-8",
    )
    _write_pipeline(
        tmp_path / "pipeline-config.yaml",
        {
            "template_file": "pipeline.yaml.j2",
            "shared": "parent",
            "parent_only": "yes",
        },
    )
    _write_pipeline(
        tmp_path / "child-config.yaml",
        {
            "template_file": "child.yaml.j2",
            "shared": "child",
            "child_only": "also",
        },
    )

    parent_first = PipelineHydrator(recursive_context="parent-priority").hydrate_file(
        tmp_path / "pipeline-config.yaml"
    )
    child_first = PipelineHydrator(recursive_context="child-priority").hydrate_file(
        tmp_path / "pipeline-config.yaml"
    )

    parent_spec = parent_first.data["implementation"]["graph"]["tasks"]["child"]["componentRef"]["spec"]
    child_spec = child_first.data["implementation"]["graph"]["tasks"]["child"]["componentRef"]["spec"]
    assert parent_spec["name"] == "Child parent yes also"
    assert child_spec["name"] == "Child child yes also"


def test_pipeline_hydrator_recursive_context_does_not_leak_between_hydrates(tmp_path: Path):
    from tangle_cli.pipeline_hydrator import PipelineHydrator

    (tmp_path / "first-pipeline.yaml.j2").write_text(
        textwrap.dedent(
            """
            name: First
            implementation:
              graph:
                tasks:
                  child:
                    componentRef:
                      url: file://./first-child-config.yaml
            """
        ),
        encoding="utf-8",
    )
    (tmp_path / "first-child.yaml.j2").write_text(
        textwrap.dedent(
            """
            name: "First {{ parent_only }}"
            implementation:
              container:
                image: python:3.12
            """
        ),
        encoding="utf-8",
    )
    _write_pipeline(
        tmp_path / "first-config.yaml",
        {"template_file": "first-pipeline.yaml.j2", "parent_only": "leaked"},
    )
    _write_pipeline(
        tmp_path / "first-child-config.yaml",
        {"template_file": "first-child.yaml.j2"},
    )

    (tmp_path / "plain-child.yaml.j2").write_text(
        textwrap.dedent(
            """
            name: "Plain {{ parent_only|default('missing') }} {{ child_only }}"
            implementation:
              container:
                image: python:3.12
            """
        ),
        encoding="utf-8",
    )
    _write_pipeline(
        tmp_path / "plain.yaml",
        {
            "name": "Plain",
            "implementation": {
                "graph": {
                    "tasks": {
                        "child": {"componentRef": {"url": "file://./plain-child-config.yaml"}}
                    }
                }
            },
        },
    )
    _write_pipeline(
        tmp_path / "plain-child-config.yaml",
        {"template_file": "plain-child.yaml.j2", "child_only": "second"},
    )

    hydrator = PipelineHydrator(recursive_context="parent-priority")
    hydrator.hydrate_file(tmp_path / "first-config.yaml")
    second = hydrator.hydrate_file(tmp_path / "plain.yaml")

    child_spec = second.data["implementation"]["graph"]["tasks"]["child"]["componentRef"]["spec"]
    assert child_spec["name"] == "Plain missing second"
    assert hydrator._global_params == {}


def test_pipelines_layout_preserves_tasks_and_updates_coordinates(tmp_path: Path, capsys):
    pipeline_path = _write_pipeline(tmp_path / "pipeline.yaml", _minimal_valid_pipeline())
    output_path = tmp_path / "layout.yaml"
    app = cli.build_app()

    run_app(
        app,
        [
            "sdk",
            "pipelines",
            "layout",
            str(pipeline_path),
            "--output",
            str(output_path),
        ],
    )

    assert "Positioned 2 task" in capsys.readouterr().out
    original = yaml.safe_load(pipeline_path.read_text(encoding="utf-8"))
    updated = yaml.safe_load(output_path.read_text(encoding="utf-8"))
    original_tasks = original["implementation"]["graph"]["tasks"]
    updated_tasks = updated["implementation"]["graph"]["tasks"]
    assert list(updated_tasks) == list(original_tasks)
    assert updated_tasks["extract"]["componentRef"] == original_tasks["extract"]["componentRef"]
    assert updated_tasks["load"]["componentRef"] == original_tasks["load"]["componentRef"]

    extract_position = json.loads(updated_tasks["extract"]["annotations"]["editor.position"])
    load_position = json.loads(updated_tasks["load"]["annotations"]["editor.position"])
    assert extract_position == {"x": 0, "y": 0}
    assert load_position["x"] > extract_position["x"]
