from __future__ import annotations

import copy
import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from tangle_cli import cli, pipeline_runs_cli
from tangle_cli.pipeline_runner import PipelineRunner, PipelineRunnerHooks
from tangle_cli.pipeline_run_manager import (
    PipelineRunContext,
    PipelineRunHooks,
    PipelineRunManager,
    PipelineRunError,
    PipelineWaitOutcome,
    PipelineWaitPoll,
)


def run_app(app, args: list[str]) -> None:
    try:
        app(args)
    except SystemExit as exc:
        if exc.code not in (0, None):
            raise


def _write_pipeline(path: Path) -> Path:
    path.write_text(
        yaml.safe_dump(
            {
                "name": "Demo Pipeline",
                "inputs": [
                    {"name": "query", "type": "String", "default": "default"},
                    {"name": "required", "type": "String"},
                ],
                "implementation": {"graph": {"tasks": {}}},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


class FakeClient:
    def __init__(self) -> None:
        self.base_url = "https://tangle.example"
        self.created: list[Any] = []
        self.cancelled: list[str] = []
        self.annotation_sets: list[tuple[str, str, Any]] = []
        self.annotation_deletes: list[tuple[str, str]] = []
        self.get_calls: list[dict[str, Any]] = []
        self.list_calls: list[dict[str, Any]] = []

    def pipeline_runs_create(self, body: Any = None) -> dict[str, Any]:
        self.created.append(body)
        return {"id": "run-1", "root_execution_id": "exec-1"}

    def pipeline_runs_get(self, id: str, include_execution_stats: bool | None = None) -> dict[str, Any]:
        self.get_calls.append({"id": id, "include_execution_stats": include_execution_stats})
        return {
            "id": id,
            "root_execution_id": "exec-1",
            "execution_summary": {"has_ended": True},
            "execution_status_stats": {"SUCCEEDED": 1},
        }

    def get_run_details(self, run_id: str, **kwargs: Any) -> dict[str, Any]:
        return {"run": {"id": run_id}, "kwargs": kwargs}

    def pipeline_runs_cancel(self, id: str) -> None:
        self.cancelled.append(id)
        return None

    def executions_graph_execution_state(self, id: str) -> dict[str, Any]:
        return {"child_execution_status_stats": {id: {"RUNNING": 1}}}

    def executions_container_log(self, id: str) -> dict[str, Any]:
        return {"log_text": f"logs for {id}\n"}

    def pipeline_runs_list(self, **kwargs: Any) -> dict[str, Any]:
        self.list_calls.append(kwargs)
        return {"pipeline_runs": [{"id": "run-1"}], "next_page_token": None}

    def users_me(self) -> SimpleNamespace:
        return SimpleNamespace(id="alice@example.com")

    def pipeline_runs_annotations(self, id: str) -> dict[str, Any]:
        return {"owner": "alice", "id": id}

    def pipeline_runs_put_annotations(self, id: str, key: str, value: Any = None) -> None:
        self.annotation_sets.append((id, key, value))

    def pipeline_runs_delete_annotations(self, id: str, key: str) -> None:
        self.annotation_deletes.append((id, key))

    def get_run_pipeline_spec(self, run_id: str) -> Any:
        return SimpleNamespace(
            raw={"componentRef": {"spec": {"name": "Exported", "implementation": {"graph": {"tasks": {}}}}}}
        )


def test_pipeline_runs_help_exposes_run_commands_not_local_pipeline_commands(capsys):
    app = cli.build_app()

    run_app(app, ["sdk", "--help"])
    assert "pipeline-runs" in capsys.readouterr().out

    run_app(app, ["sdk", "pipeline-runs", "--help"])
    output = capsys.readouterr().out
    for command in (
        "submit",
        "details",
        "status",
        "graph-state",
        "cancel",
        "wait",
        "logs",
        "search",
        "annotations",
        "export",
    ):
        assert command in output
    assert "validate" not in output
    assert "diagram" not in output

    run_app(app, ["sdk", "pipeline-runs", "submit", "--help"])
    assert "--log-type" in capsys.readouterr().out


def test_pipeline_runs_submit_builds_create_payload(monkeypatch, tmp_path: Path, capsys):
    pipeline_path = _write_pipeline(tmp_path / "pipeline.yaml")
    fake_client = FakeClient()
    monkeypatch.setattr(pipeline_runs_cli, "LazyTangleApiClient", lambda **kwargs: fake_client)
    app = cli.build_app()

    run_app(
        app,
        [
            "sdk",
            "pipeline-runs",
            "submit",
            str(pipeline_path),
            "--no-hydrate",
            "--arg",
            "required=value",
            "--annotation",
            "team=oss",
        ],
    )

    result = json.loads(capsys.readouterr().out)
    assert result == {"id": "run-1", "root_execution_id": "exec-1"}
    assert fake_client.created[0]["annotations"] == {"team": "oss"}
    root_task = fake_client.created[0]["root_task"]
    assert root_task["componentRef"]["spec"]["name"] == "Demo Pipeline"
    assert root_task["arguments"] == {"query": "default", "required": "value"}


def test_pipeline_runs_submit_dry_run_prints_sanitized_payload(monkeypatch, tmp_path: Path, capsys):
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        yaml.safe_dump(
            {
                "name": "Demo Pipeline",
                "_source_dir": "/tmp/private",
                "implementation": {
                    "graph": {
                        "tasks": {
                            "task": {
                                "arguments": {"config": {"_meta": {"mode": "keep"}}},
                                "componentRef": {
                                    "name": "text-component",
                                    "text": "name: Text Component\n_source_dir: /tmp/private\nimplementation:\n  container:\n    image: busybox\n",
                                }
                            }
                        }
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    fake_client = FakeClient()
    monkeypatch.setattr(pipeline_runs_cli, "LazyTangleApiClient", lambda **kwargs: fake_client)
    app = cli.build_app()

    run_app(
        app,
        [
            "sdk",
            "pipeline-runs",
            "submit",
            str(pipeline_path),
            "--no-hydrate",
            "--dry-run",
        ],
    )

    payload = json.loads(capsys.readouterr().out)
    assert fake_client.created == []
    spec = payload["root_task"]["componentRef"]["spec"]
    assert "_source_dir" not in spec
    task = spec["implementation"]["graph"]["tasks"]["task"]
    assert task["arguments"]["config"] == {"_meta": {"mode": "keep"}}
    task_ref = task["componentRef"]
    assert "text" not in task_ref
    assert task_ref["spec"]["name"] == "Text Component"
    assert "_source_dir" not in task_ref["spec"]


def test_pipeline_runs_submit_with_hydrate_logs_progress(
    monkeypatch,
    tmp_path: Path,
    capsys,
):
    (tmp_path / "component.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "Local Component",
                "implementation": {"container": {"image": "busybox"}},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        yaml.safe_dump(
            {
                "name": "Demo Pipeline",
                "implementation": {
                    "graph": {
                        "tasks": {
                            "task": {"componentRef": {"url": "file://./component.yaml"}}
                        }
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    fake_client = FakeClient()
    monkeypatch.setattr(pipeline_runs_cli, "LazyTangleApiClient", lambda **kwargs: fake_client)
    app = cli.build_app()

    run_app(app, ["sdk", "pipeline-runs", "submit", str(pipeline_path)])

    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"id": "run-1", "root_execution_id": "exec-1"}
    assert fake_client.created[0]["root_task"]["componentRef"]["spec"]["name"] == "Demo Pipeline"
    assert "Loading component from file URL" in captured.err
    assert "✅ Loaded component" in captured.err


def test_pipeline_runs_hydrate_logs_progress_when_verbose_false(
    monkeypatch,
    tmp_path: Path,
    capsys,
):
    monkeypatch.setenv("TANGLE_VERBOSE", "0")
    (tmp_path / "component.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "Local Component",
                "implementation": {"container": {"image": "busybox"}},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        yaml.safe_dump(
            {
                "name": "Demo Pipeline",
                "implementation": {
                    "graph": {
                        "tasks": {
                            "task": {"componentRef": {"url": "file://./component.yaml"}}
                        }
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    fake_client = FakeClient()
    monkeypatch.setattr(pipeline_runs_cli, "LazyTangleApiClient", lambda **kwargs: fake_client)
    app = cli.build_app()

    run_app(app, ["sdk", "pipeline-runs", "submit", str(pipeline_path), "--dry-run"])

    captured = capsys.readouterr()
    assert json.loads(captured.out)["root_task"]["componentRef"]["spec"]["name"] == "Demo Pipeline"
    assert "Loading component from file URL" in captured.err
    assert "✅ Loaded component" in captured.err
    assert "[verbose]" not in captured.err


def test_pipeline_runs_submit_log_type_file_captures_progress(
    monkeypatch,
    tmp_path: Path,
    capsys,
):
    (tmp_path / "component.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "Local Component",
                "implementation": {"container": {"image": "busybox"}},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        yaml.safe_dump(
            {
                "name": "Demo Pipeline",
                "implementation": {
                    "graph": {
                        "tasks": {
                            "task": {"componentRef": {"url": "file://./component.yaml"}}
                        }
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    fake_client = FakeClient()
    monkeypatch.setattr(pipeline_runs_cli, "LazyTangleApiClient", lambda **kwargs: fake_client)
    app = cli.build_app()

    run_app(
        app,
        [
            "sdk",
            "pipeline-runs",
            "submit",
            str(pipeline_path),
            "--dry-run",
            "--log-type",
            "file",
        ],
    )

    captured = capsys.readouterr()
    assert json.loads(captured.out)["root_task"]["componentRef"]["spec"]["name"] == "Demo Pipeline"
    assert "Logs written to:" in captured.err
    log_path = Path(captured.err.split("Logs written to:", 1)[1].strip())
    try:
        log_text = log_path.read_text(encoding="utf-8")
    finally:
        log_path.unlink(missing_ok=True)
    assert "Loading component from file URL" in log_text
    assert "✅ Loaded component" in log_text


def test_pipeline_runs_submit_log_type_none_suppresses_progress(
    monkeypatch,
    tmp_path: Path,
    capsys,
):
    (tmp_path / "component.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "Local Component",
                "implementation": {"container": {"image": "busybox"}},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        yaml.safe_dump(
            {
                "name": "Demo Pipeline",
                "implementation": {
                    "graph": {
                        "tasks": {
                            "task": {"componentRef": {"url": "file://./component.yaml"}}
                        }
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    fake_client = FakeClient()
    monkeypatch.setattr(pipeline_runs_cli, "LazyTangleApiClient", lambda **kwargs: fake_client)
    app = cli.build_app()

    run_app(
        app,
        [
            "sdk",
            "pipeline-runs",
            "submit",
            str(pipeline_path),
            "--dry-run",
            "--log-type",
            "none",
        ],
    )

    captured = capsys.readouterr()
    assert json.loads(captured.out)["root_task"]["componentRef"]["spec"]["name"] == "Demo Pipeline"
    assert captured.err == ""


def test_pipeline_runs_config_base_url_suppresses_ambient_credentials(monkeypatch, tmp_path: Path):
    calls: list[dict[str, Any]] = []
    fake_client = FakeClient()

    def fake_client_from_options(**kwargs: Any) -> FakeClient:
        calls.append(kwargs)
        return fake_client

    config = tmp_path / "config.yaml"
    config.write_text("base_url: https://api.test\ntoken: explicit\n", encoding="utf-8")
    monkeypatch.setenv("TANGLE_API_TOKEN", "ambient")
    monkeypatch.setattr(pipeline_runs_cli, "LazyTangleApiClient", fake_client_from_options)
    app = cli.build_app()

    run_app(app, ["sdk", "pipeline-runs", "status", "run-1", "--config", str(config)])

    assert calls[0]["base_url"] == "https://api.test"
    assert calls[0]["token"] == "explicit"
    assert calls[0]["include_env_credentials"] is False


def test_pipeline_runs_commands_call_generated_operations(monkeypatch, tmp_path: Path, capsys):
    fake_client = FakeClient()
    monkeypatch.setattr(pipeline_runs_cli, "LazyTangleApiClient", lambda **kwargs: fake_client)
    app = cli.build_app()

    run_app(app, ["sdk", "pipeline-runs", "details", "run-1", "--include-annotations"])
    assert json.loads(capsys.readouterr().out)["kwargs"] == {
        "include_annotations": True,
        "include_execution_state": False,
    }

    run_app(app, ["sdk", "pipeline-runs", "status", "run-1"])
    assert json.loads(capsys.readouterr().out)["status"] == "SUCCEEDED"

    run_app(app, ["sdk", "pipeline-runs", "graph-state", "exec-1"])
    assert json.loads(capsys.readouterr().out)["child_execution_status_stats"] == {"exec-1": {"RUNNING": 1}}

    run_app(app, ["sdk", "pipeline-runs", "cancel", "run-1"])
    assert json.loads(capsys.readouterr().out) == {"cancelled": True, "id": "run-1"}
    assert fake_client.cancelled == ["run-1"]

    run_app(app, ["sdk", "pipeline-runs", "logs", "exec-1"])
    assert capsys.readouterr().out == "logs for exec-1\n"

    run_app(app, ["sdk", "pipeline-runs", "search", "demo", "--filter-query", "status:running"])
    assert json.loads(capsys.readouterr().out)["pipeline_runs"] == [{"id": "run-1"}]
    assert fake_client.list_calls[-1]["filter"] == "demo"
    assert fake_client.list_calls[-1]["filter_query"] == "status:running"

    run_app(app, ["sdk", "pipeline-runs", "annotations", "list", "run-1"])
    assert json.loads(capsys.readouterr().out)["owner"] == "alice"

    run_app(app, ["sdk", "pipeline-runs", "annotations", "set", "run-1", "owner", "bob"])
    assert fake_client.annotation_sets == [("run-1", "owner", "bob")]

    run_app(app, ["sdk", "pipeline-runs", "annotations", "set", "run-1", "flag"])
    assert fake_client.annotation_sets[-1] == ("run-1", "flag", None)

    run_app(app, ["sdk", "pipeline-runs", "annotations", "delete", "run-1", "owner"])
    assert fake_client.annotation_deletes == [("run-1", "owner")]

    output = tmp_path / "export.yaml"
    run_app(app, ["sdk", "pipeline-runs", "export", "run-1", "--output", str(output)])
    assert yaml.safe_load(output.read_text(encoding="utf-8"))["name"] == "Exported"


def test_pipeline_runs_rich_search_builds_filters_and_formats_pages() -> None:
    class SearchClient(FakeClient):
        def pipeline_runs_list(self, **kwargs: Any) -> dict[str, Any]:
            self.list_calls.append(kwargs)
            if kwargs.get("page_token") is None:
                return {
                    "pipeline_runs": [
                        {
                            "id": "run-abcdef123456",
                            "pipeline_name": "Orders Pipeline",
                            "created_by": "alice@example.com",
                            "created_at": "2026-06-13T12:34:56Z",
                        }
                    ],
                    "next_page_token": "page-2",
                }
            return {
                "pipeline_runs": [
                    {
                        "id": "run-fedcba654321",
                        "pipeline_name": "Orders Pipeline Retry",
                        "created_by": "alice@example.com",
                        "created_at": "2026-06-13T13:34:56Z",
                    }
                ],
                "next_page_token": None,
            }

    client = SearchClient()
    manager = PipelineRunManager(client=client)

    result = manager.search_pipeline_runs(
        name="Orders",
        created_by="me",
        annotations={"team": "search", "debug": None},
        start_date="2026-06-13T00:00:00Z",
        end_date="2026-06-14T00:00:00Z",
        limit=2,
    )

    assert result["count"] == 2
    assert result["runs"][0]["run_url"] == "https://tangle.example/runs/run-abcdef123456"
    assert result["pages"][0]["next_page_token"] == "page-2"
    assert "Pipeline Run Search Results" in result["cli_table"]
    filter_query = json.loads(client.list_calls[0]["filter_query"])
    assert filter_query == {
        "and": [
            {"value_contains": {"key": "system/pipeline_run.name", "value_substring": "Orders"}},
            {"value_equals": {"key": "system/pipeline_run.created_by", "value": "alice@example.com"}},
            {"value_contains": {"key": "team", "value_substring": "search"}},
            {"key_exists": {"key": "debug"}},
            {
                "time_range": {
                    "key": "system/pipeline_run.date.created_at",
                    "start_time": "2026-06-13T00:00:00Z",
                    "end_time": "2026-06-14T00:00:00Z",
                }
            },
        ]
    }
    assert client.list_calls[0]["include_pipeline_names"] is True
    assert client.list_calls[1]["page_token"] == "page-2"


def test_pipeline_runs_search_cli_table_output(monkeypatch, capsys) -> None:
    class SearchClient(FakeClient):
        def pipeline_runs_list(self, **kwargs: Any) -> dict[str, Any]:
            self.list_calls.append(kwargs)
            return {
                "pipeline_runs": [
                    {
                        "id": "run-1",
                        "pipeline_name": "Demo",
                        "created_by": "alice@example.com",
                        "created_at": "2026-06-13T12:34:56Z",
                    }
                ],
                "next_page_token": None,
            }

    fake_client = SearchClient()
    monkeypatch.setattr(pipeline_runs_cli, "LazyTangleApiClient", lambda **kwargs: fake_client)
    app = cli.build_app()

    run_app(
        app,
        [
            "sdk",
            "pipeline-runs",
            "search",
            "--name",
            "Demo",
            "--annotation",
            "team=search",
            "--output",
            "table",
        ],
    )

    output = capsys.readouterr().out
    assert "Pipeline Run Search Results" in output
    assert "https://tangle.example/runs/run-1" in output
    assert json.loads(fake_client.list_calls[0]["filter_query"])["and"][0] == {
        "value_contains": {"key": "system/pipeline_run.name", "value_substring": "Demo"}
    }


def test_pipeline_runs_graph_state_returns_plain_generated_response() -> None:
    class GraphStateClient:
        def executions_graph_execution_state(self, id: str) -> Any:
            assert id == "exec-1"
            return SimpleNamespace(
                status_totals={"SUCCEEDED": 1},
                child_execution_status_stats={"child-1": {"RUNNING": 2}},
            )

    manager = PipelineRunManager(client=GraphStateClient())

    assert manager.graph_state("exec-1") == {
        "status_totals": {"SUCCEEDED": 1},
        "child_execution_status_stats": {"child-1": {"RUNNING": 2}},
    }


def test_pipeline_runs_details_and_graph_state_helpers() -> None:
    manager = PipelineRunManager(client=FakeClient())

    details = manager.get_run_details("run-1", include_annotations=True, include_execution_state=True)
    assert details == {"run": {"id": "run-1"}, "kwargs": {
        "include_annotations": True,
        "include_execution_state": True,
    }}

    graph = manager.graph_state_output(["run-1"], timeout=1)
    assert graph == {
        "results": [
            {
                "run_id": "run-1",
                "root_execution_id": "exec-1",
                "status_totals": None,
                "failed_execution_ids": None,
                "per_execution": None,
                "error": None,
            }
        ]
    }


def _write_submit_local_from_python_pipeline(
    project_dir: Path,
    python_file: str,
    *,
    resolve_root: str | None = None,
) -> Path:
    gen_config = {
        "file": python_file,
        "output_folder": "./generated",
    }
    if resolve_root is not None:
        gen_config["resolve_root"] = resolve_root
    (project_dir / "components.resolve.yaml").write_text(
        yaml.safe_dump({"generated": {"local_from_python": gen_config}}, sort_keys=False),
        encoding="utf-8",
    )
    pipeline_path = project_dir / "pipeline.yaml"
    pipeline_path.write_text(
        yaml.safe_dump(
            {
                "name": "Submit Pipeline",
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
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return pipeline_path


def test_pipeline_runs_submit_refuses_untrusted_local_from_python(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from tangle_cli import pipeline_hydrator as hydrator_module

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside_python = outside_dir / "evil.py"
    outside_python.write_text("raise RuntimeError('must not execute')\n", encoding="utf-8")
    pipeline_path = _write_submit_local_from_python_pipeline(project_dir, str(outside_python))

    def fake_regenerate_yaml(**kwargs):
        raise AssertionError("untrusted local_from_python must be blocked before generation")

    monkeypatch.setattr(hydrator_module, "regenerate_yaml", fake_regenerate_yaml)
    manager = PipelineRunManager(client=FakeClient())

    with pytest.raises(PipelineRunError, match="Refusing to execute untrusted local_from_python source"):
        manager.submit_pipeline(pipeline_path)


def test_pipeline_runs_submit_ignores_untrusted_resolve_root_for_python_trust(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from tangle_cli import pipeline_hydrator as hydrator_module

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside_python = outside_dir / "evil.py"
    outside_python.write_text("raise RuntimeError('must not execute')\n", encoding="utf-8")
    pipeline_path = _write_submit_local_from_python_pipeline(
        project_dir,
        str(outside_python),
        resolve_root=str(outside_dir),
    )

    def fake_regenerate_yaml(**kwargs):
        raise AssertionError("untrusted resolve_root must not authorize execution")

    monkeypatch.setattr(hydrator_module, "regenerate_yaml", fake_regenerate_yaml)
    manager = PipelineRunManager(client=FakeClient())

    with pytest.raises(PipelineRunError, match="Refusing to execute untrusted local_from_python source"):
        manager.submit_pipeline(pipeline_path)


def test_pipeline_runs_submit_trusted_hydration_allows_untrusted_local_from_python(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    from tangle_cli import pipeline_hydrator as hydrator_module

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside_python = outside_dir / "component.py"
    outside_python.write_text("# trusted by explicit override\n", encoding="utf-8")
    pipeline_path = _write_submit_local_from_python_pipeline(project_dir, str(outside_python))
    regenerated: list[Path] = []

    def fake_regenerate_yaml(**kwargs):
        regenerated.append(kwargs["python_file"])
        kwargs["output_path"].write_text(
            "name: Submit Generated Component\nimplementation:\n  container:\n    image: busybox\n",
            encoding="utf-8",
        )
        return True

    fake_client = FakeClient()
    monkeypatch.setattr(hydrator_module, "regenerate_yaml", fake_regenerate_yaml)
    monkeypatch.setattr(pipeline_runs_cli, "LazyTangleApiClient", lambda **kwargs: fake_client)
    app = cli.build_app()

    run_app(app, ["sdk", "pipeline-runs", "submit", str(pipeline_path), "--trusted-hydration"])

    assert json.loads(capsys.readouterr().out)["id"] == "run-1"
    assert regenerated == [outside_python.resolve()]
    submitted_task = fake_client.created[0]["root_task"]["componentRef"]["spec"]["implementation"]["graph"]["tasks"]["generated"]
    assert submitted_task["componentRef"]["name"] == "Submit Generated Component"


def test_pipeline_runs_build_submit_body_from_prepared_spec_and_run_name_template() -> None:
    class Hooks(PipelineRunHooks):
        def prepare_pipeline_spec(self, pipeline_spec, *, pipeline_path, run_args, hydrate):
            prepared = dict(pipeline_spec)
            prepared.setdefault("metadata", {}).setdefault("annotations", {})["prepared"] = "yes"
            return prepared

        def prepare_run_arguments(self, pipeline_spec, run_args):
            merged = dict(run_args or {})
            merged["timestamp"] = "2026-06-13"
            return merged

    manager = PipelineRunManager(client=FakeClient(), hooks=Hooks())
    body = manager.build_submit_body_from_spec(
        {
            "name": "Original",
            "inputs": [{"name": "timestamp", "type": "String"}],
            "metadata": {"annotations": {"run-name-template": "run-${arguments.timestamp}"}},
            "implementation": {"graph": {"tasks": {}}},
        },
        run_args={},
        annotations={"team": "oss"},
        hydrate=False,
    )

    spec = body["root_task"]["componentRef"]["spec"]
    assert spec["name"] == "run-2026-06-13"
    assert spec["metadata"]["annotations"]["prepared"] == "yes"
    assert body["root_task"]["arguments"] == {"timestamp": "2026-06-13"}
    assert body["annotations"] == {"team": "oss"}


def test_pipeline_runs_submit_error_hook_gets_context() -> None:
    class FailingClient(FakeClient):
        def pipeline_runs_create(self, body: Any = None) -> dict[str, Any]:
            raise RuntimeError("boom")

    errors = []

    class Hooks(PipelineRunHooks):
        def on_submit_error(self, error, *, context):
            errors.append((str(error), context.run_name, context.pipeline_spec["name"]))

    manager = PipelineRunManager(client=FailingClient(), hooks=Hooks())

    with pytest.raises(RuntimeError, match="boom"):
        manager.submit_pipeline_spec(
            {"name": "Explodes", "implementation": {"graph": {"tasks": {}}}},
            hydrate=False,
        )

    assert errors == [("boom", "Explodes", "Explodes")]


def test_pipeline_runs_wait_uses_graph_state_and_poll_hooks() -> None:
    events = []

    class GraphClient(FakeClient):
        def pipeline_runs_get(self, id: str, include_execution_stats: bool | None = None) -> dict[str, Any]:
            return {
                "id": id,
                "root_execution_id": "exec-graph",
                "execution_status_stats": {"RUNNING": 1},
            }

        def executions_graph_execution_state(self, id: str) -> dict[str, Any]:
            return {"status_totals": {"SUCCEEDED": 2}}

    class Hooks(PipelineRunHooks):
        def before_wait(self, context):
            events.append(("before_wait", context.run_id))

        def after_poll(self, poll, context):
            events.append(("after_poll", poll.status_counts, poll.total, poll.terminal))

        def on_terminal(self, poll, context):
            events.append(("terminal", poll.status))

        def after_wait_context(self, result, context):
            events.append(("after_wait", result["status"]))

    manager = PipelineRunManager(client=GraphClient(), hooks=Hooks())

    result = manager.wait_for_completion(
        "run-graph",
        max_wait=None,
        poll_interval=1,
        use_graph_state=True,
    )

    assert result["status"] == "SUCCEEDED"
    assert events == [
        ("before_wait", "run-graph"),
        ("after_poll", {"SUCCEEDED": 2}, 2, True),
        ("terminal", "SUCCEEDED"),
        ("after_wait", "SUCCEEDED"),
    ]


def test_pipeline_runs_wait_graph_state_treats_canceled_spelling_as_terminal() -> None:
    class CanceledGraphClient(FakeClient):
        def pipeline_runs_get(self, id: str, include_execution_stats: bool | None = None) -> dict[str, Any]:
            return {
                "id": id,
                "root_execution_id": "exec-canceled",
                "execution_status_stats": {"RUNNING": 1},
            }

        def executions_graph_execution_state(self, id: str) -> dict[str, Any]:
            return {"status_totals": {"CANCELED": 1}}

    manager = PipelineRunManager(client=CanceledGraphClient())

    result = manager.wait_for_completion(
        "run-canceled",
        max_wait=0,
        poll_interval=1,
        use_graph_state=True,
    )

    assert result == {
        "run": {
            "id": "run-canceled",
            "root_execution_id": "exec-canceled",
            "execution_status_stats": {"RUNNING": 1},
        },
        "status": "CANCELED",
        "timed_out": False,
    }


def test_pipeline_runs_wait_graph_state_treats_invalid_as_terminal() -> None:
    class InvalidGraphClient(FakeClient):
        def pipeline_runs_get(self, id: str, include_execution_stats: bool | None = None) -> dict[str, Any]:
            return {
                "id": id,
                "root_execution_id": "exec-invalid",
                "execution_status_stats": {"RUNNING": 1},
            }

        def executions_graph_execution_state(self, id: str) -> dict[str, Any]:
            return {"status_totals": {"INVALID": 1}}

    manager = PipelineRunManager(client=InvalidGraphClient())

    result = manager.wait_for_completion(
        "run-invalid",
        max_wait=0,
        poll_interval=1,
        use_graph_state=True,
    )

    assert result == {
        "run": {
            "id": "run-invalid",
            "root_execution_id": "exec-invalid",
            "execution_status_stats": {"RUNNING": 1},
        },
        "status": "INVALID",
        "timed_out": False,
    }


def test_pipeline_runs_wait_outcome_records_success_failure_timeout_and_early_exit() -> None:
    manager = PipelineRunManager(client=FakeClient())
    scenarios = [
        (
            PipelineWaitPoll("run-1", {}, "SUCCEEDED", {"SUCCEEDED": 1}, 1, True),
            {},
            True,
            False,
            False,
        ),
        (
            PipelineWaitPoll("run-1", {}, "SKIPPED", {"SUCCEEDED": 9, "SKIPPED": 1}, 10, True),
            {},
            True,
            False,
            False,
        ),
        (
            PipelineWaitPoll("run-1", {}, "CANCELLED", {"SUCCEEDED": 8, "CANCELLED": 2}, 10, True),
            {},
            False,
            False,
            False,
        ),
        (
            PipelineWaitPoll("run-1", {}, "FAILED", {"SUCCEEDED": 7, "FAILED": 3}, 10, True),
            {},
            False,
            False,
            False,
        ),
        (
            PipelineWaitPoll("run-1", {}, "RUNNING", {"RUNNING": 1}, 1, False),
            {"max_wait": 0},
            None,
            True,
            False,
        ),
        (
            PipelineWaitPoll("run-1", {}, "FAILED", {"FAILED": 1}, 1, False),
            {"exit_on_first_failure": True},
            False,
            False,
            True,
        ),
    ]
    for poll, options, expected_success, expected_timeout, expected_early_exit in scenarios:
        context = PipelineRunContext(run_id="run-1")
        manager._poll_run_status = lambda *args, **kwargs: poll  # type: ignore[method-assign]

        manager.wait_for_completion(
            "run-1",
            max_wait=options.get("max_wait", 10),
            poll_interval=0,
            allow_zero_poll_interval=True,
            context=context,
            exit_on_first_failure=bool(options.get("exit_on_first_failure", False)),
        )

        assert context.wait_outcome is not None
        assert context.wait_outcome.success is expected_success
        assert context.wait_outcome.timed_out is expected_timeout
        assert context.wait_outcome.early_exit is expected_early_exit


def test_pipeline_runs_wait_outcome_zero_total_early_exit_is_failure_not_exited_early() -> None:
    class ZeroTaskExitHooks(PipelineRunHooks):
        def should_exit_early(
            self,
            poll: PipelineWaitPoll,
            context: PipelineRunContext,
        ) -> bool:
            del context
            return poll.total == 0

    manager = PipelineRunManager(client=FakeClient(), hooks=ZeroTaskExitHooks())
    context = PipelineRunContext(run_id="run-1")
    manager._poll_run_status = (  # type: ignore[method-assign]
        lambda *args, **kwargs: PipelineWaitPoll("run-1", {}, "UNKNOWN", {}, 0, False)
    )

    manager.wait_for_completion(
        "run-1",
        max_wait=10,
        poll_interval=0,
        allow_zero_poll_interval=True,
        context=context,
    )

    assert context.wait_outcome is not None
    assert context.wait_outcome.success is False
    assert context.wait_outcome.timed_out is False
    assert context.wait_outcome.early_exit is False


def test_pipeline_runs_wait_outcome_from_wait_result_derives_counts_from_status_counts() -> None:
    outcome = PipelineWaitOutcome.from_wait_result(
        {"status": "FAILED", "timed_out": False},
        {"status_counts": {"FAILED": 1, "SYSTEM_ERROR": 1}},
    )

    assert outcome.success is False
    assert outcome.failed_count == 1
    assert outcome.error_count == 1


def test_pipeline_runs_wait_outcome_ended_without_counts_is_unknown() -> None:
    assert PipelineWaitOutcome(status="ENDED").success is None

    outcome = PipelineWaitOutcome.from_wait_result({"status": "ENDED", "timed_out": False})

    assert outcome.success is None


def test_pipeline_runs_wait_outcome_ended_uses_reliable_counts() -> None:
    success = PipelineWaitOutcome.from_wait_result(
        {"status": "ENDED", "timed_out": False},
        {"status_counts": {"SUCCEEDED": 2, "SKIPPED": 1}},
    )
    failure = PipelineWaitOutcome.from_wait_result(
        {"status": "ENDED", "timed_out": False},
        {"status_counts": {"SUCCEEDED": 2, "FAILED": 1}},
    )

    assert success.success is True
    assert failure.success is False


def test_pipeline_runs_wait_outcome_records_ended_without_counts_as_unknown() -> None:
    class EndedWithoutStatsClient(FakeClient):
        def pipeline_runs_get(self, id: str, include_execution_stats: bool | None = None) -> dict[str, Any]:
            return {"id": id, "execution_summary": {"has_ended": True}}

    manager = PipelineRunManager(client=EndedWithoutStatsClient())
    context = PipelineRunContext(run_id="run-ended")

    result = manager.wait_for_completion(
        "run-ended",
        max_wait=10,
        poll_interval=0,
        allow_zero_poll_interval=True,
        context=context,
    )

    assert result["status"] == "ENDED"
    assert context.wait_outcome is not None
    assert context.wait_outcome.success is None


def test_pipeline_runs_graph_state_counts_supports_mapping_like_objects() -> None:
    assert PipelineRunManager.status_counts_from_graph_state(
        SimpleNamespace(status_totals={"SUCCEEDED": 1})
    ) == {"SUCCEEDED": 1}


def test_pipeline_runs_wait_can_poll_execution_root_via_hooks() -> None:
    events = []

    class ExecutionClient(FakeClient):
        def pipeline_runs_get(self, id: str, include_execution_stats: bool | None = None) -> dict[str, Any]:
            raise AssertionError("execution-rooted polling should not fetch a run")

        def executions_graph_execution_state(self, id: str) -> dict[str, Any]:
            assert id == "exec-root"
            return {"status_totals": {"SUCCEEDED": 2}}

    class Hooks(PipelineRunHooks):
        def poll_run_snapshot(self, manager, run_id, context):
            return {"id": run_id, "root_execution_id": context.root_execution_id}

        def after_poll(self, poll, context):
            events.append((poll.status_counts, poll.total, poll.terminal))

    manager = PipelineRunManager(client=ExecutionClient(), hooks=Hooks())
    context = PipelineRunContext(
        run_id="exec-root",
        root_execution_id="exec-root",
    )

    result = manager.wait_for_completion(
        "exec-root",
        max_wait=None,
        poll_interval=1,
        use_graph_state=True,
        context=context,
    )

    assert result["status"] == "SUCCEEDED"
    assert events == [({"SUCCEEDED": 2}, 2, True)]


def test_pipeline_runs_wait_poll_error_hook_can_retry() -> None:
    class FlakyGraphClient(FakeClient):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def executions_graph_execution_state(self, id: str) -> dict[str, Any]:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("transient")
            return {"status_totals": {"SUCCEEDED": 1}}

    class Hooks(PipelineRunHooks):
        def on_poll_error(self, error, context):
            assert str(error) == "transient"
            return 0

    manager = PipelineRunManager(client=FlakyGraphClient(), hooks=Hooks())

    result = manager.wait_for_completion(
        "run-graph",
        max_wait=None,
        poll_interval=1,
        use_graph_state=True,
    )

    assert result["status"] == "SUCCEEDED"


def test_pipeline_runs_wait_poll_error_hook_respects_max_wait() -> None:
    class FailingGraphClient(FakeClient):
        def executions_graph_execution_state(self, id: str) -> dict[str, Any]:
            raise RuntimeError("persistent poll failure")

    errors = []

    class Hooks(PipelineRunHooks):
        def on_poll_error(self, error, context):
            errors.append(str(error))
            return 0

    manager = PipelineRunManager(client=FailingGraphClient(), hooks=Hooks())

    with pytest.raises(PipelineRunError, match="Timed out waiting for run run-graph"):
        manager.wait_for_completion(
            "run-graph",
            max_wait=0,
            poll_interval=1,
            use_graph_state=True,
        )

    assert errors == []


def test_pipeline_runs_fail_fast_hook_runs_before_lifecycle_release(tmp_path: Path) -> None:
    pipeline_path = _write_pipeline(tmp_path / "pipeline.yaml")
    events = []

    class RecordingContext:
        def __enter__(self):
            events.append("enter")

        def __exit__(self, exc_type, exc, tb):
            events.append("exit")
            return False

    class Hooks(PipelineRunHooks):
        def around_run(self, context):
            return RecordingContext()

        def after_poll(self, poll, context):
            events.append("poll")
            raise PipelineRunError("fail fast")

        def on_fail_fast_before_release(self, context, error):
            events.append("failfast")

        def after_run_lifecycle(self, context, *, success, error=None):
            events.append("after_lifecycle")

    manager = PipelineRunManager(client=FakeClient(), hooks=Hooks())

    with pytest.raises(PipelineRunError, match="fail fast"):
        manager.run_pipeline(
            pipeline_path,
            run_args={"required": "value"},
            hydrate=False,
            wait=True,
            max_attempts=1,
            poll_interval=1,
        )

    assert events == ["enter", "poll", "failfast", "exit", "after_lifecycle"]


def test_pipeline_runs_early_exit_hook_runs_before_lifecycle_release(tmp_path: Path) -> None:
    pipeline_path = _write_pipeline(tmp_path / "pipeline.yaml")
    events = []

    class RecordingContext:
        def __enter__(self):
            events.append("enter")

        def __exit__(self, exc_type, exc, tb):
            events.append("exit")
            return False

    class RunningClient(FakeClient):
        def pipeline_runs_get(self, id: str, include_execution_stats: bool | None = None) -> dict[str, Any]:
            return {
                "id": id,
                "root_execution_id": "exec-1",
                "execution_status_stats": {"RUNNING": 1},
            }

    class Hooks(PipelineRunHooks):
        def around_run(self, context):
            return RecordingContext()

        def after_poll(self, poll, context):
            events.append("poll")

        def should_exit_early(self, poll, context):
            return True

        def on_early_exit_before_release(self, poll, context):
            events.append("early_cleanup")

        def after_run_lifecycle(self, context, *, success, error=None):
            events.append("after_lifecycle")

    manager = PipelineRunManager(client=RunningClient(), hooks=Hooks())

    result = manager.run_pipeline(
        pipeline_path,
        run_args={"required": "value"},
        hydrate=False,
        wait=True,
        poll_interval=1,
    )

    assert result["wait"]["early_exit"] is True
    assert events == ["enter", "poll", "early_cleanup", "exit", "after_lifecycle"]


def test_pipeline_runs_retry_cancel_previous_run_before_lifecycle_release(tmp_path: Path) -> None:
    pipeline_path = _write_pipeline(tmp_path / "pipeline.yaml")
    events = []

    class EventClient(FakeClient):
        def pipeline_runs_cancel(self, id: str) -> None:
            events.append(("cancel", id))
            return super().pipeline_runs_cancel(id)

    class RecordingContext:
        def __enter__(self):
            events.append(("enter", None))

        def __exit__(self, exc_type, exc, tb):
            events.append(("exit", None))
            return False

    class Hooks(PipelineRunHooks):
        def around_run(self, context):
            return RecordingContext()

        def after_poll(self, poll, context):
            events.append(("poll", context.attempt))
            if context.attempt == 1:
                raise PipelineRunError("retry me")

        def should_cancel_previous_run(self, context, error, *, next_attempt):
            events.append(("should_cancel", context.run_id))
            return True

        def before_retry(self, context, error, *, next_attempt):
            events.append(("before_retry", context.run_id))

        def after_run_lifecycle(self, context, *, success, error=None):
            events.append(("after_lifecycle", context.attempt))

    client = EventClient()
    manager = PipelineRunManager(client=client, hooks=Hooks())

    manager.run_pipeline(
        pipeline_path,
        run_args={"required": "value"},
        hydrate=False,
        wait=True,
        max_attempts=2,
        poll_interval=1,
    )

    assert events[:7] == [
        ("enter", None),
        ("poll", 1),
        ("should_cancel", "run-1"),
        ("cancel", "run-1"),
        ("before_retry", "run-1"),
        ("exit", None),
        ("after_lifecycle", 1),
    ]


def test_pipeline_runs_legacy_after_wait_only_fires_for_terminal_results(monkeypatch) -> None:
    legacy_results = []

    class RunningClient(FakeClient):
        def pipeline_runs_get(self, id: str, include_execution_stats: bool | None = None) -> dict[str, Any]:
            return {
                "id": id,
                "root_execution_id": "exec-1",
                "execution_status_stats": {"RUNNING": 1},
            }

    class TimeoutHooks(PipelineRunHooks):
        def after_wait(self, result):
            legacy_results.append(result)

    manager = PipelineRunManager(client=RunningClient(), hooks=TimeoutHooks())
    result = manager.wait_for_completion("run-1", max_wait=0, poll_interval=1)
    assert result["timed_out"] is True
    assert legacy_results == []

    class EarlyExitHooks(TimeoutHooks):
        def should_exit_early(self, poll, context):
            return True

    manager = PipelineRunManager(client=RunningClient(), hooks=EarlyExitHooks())
    result = manager.wait_for_completion("run-1", max_wait=1, poll_interval=1)
    assert result["early_exit"] is True
    assert legacy_results == []

    manager = PipelineRunManager(client=FakeClient(), hooks=TimeoutHooks())
    result = manager.wait_for_completion("run-1", max_wait=1, poll_interval=1)
    assert result["status"] == "SUCCEEDED"
    assert len(legacy_results) == 1


def test_pipeline_runs_run_pipeline_lifecycle_and_retry_hooks(tmp_path: Path) -> None:
    pipeline_path = _write_pipeline(tmp_path / "pipeline.yaml")
    events = []

    class Hooks(PipelineRunHooks):
        def around_run(self, context):
            events.append(("around", context.attempt))
            return nullcontext()

        def before_run_lifecycle(self, context):
            events.append(("before_lifecycle", context.attempt))

        def after_poll(self, poll, context):
            events.append(("poll", context.attempt, poll.status))
            if context.attempt == 1:
                raise PipelineRunError("fail first wait")

        def should_cancel_previous_run(self, context, error, *, next_attempt):
            events.append(("should_cancel", context.run_id, next_attempt))
            return True

        def before_retry(self, context, error, *, next_attempt):
            events.append(("before_retry", context.run_id, next_attempt, str(error)))

        def after_retry_submit(self, context):
            events.append(("after_retry_submit", context.run_id, context.attempt))

        def after_run_lifecycle(self, context, *, success, error=None):
            events.append(("after_lifecycle", context.attempt, success, str(error) if error else None))

    client = FakeClient()
    manager = PipelineRunManager(client=client, hooks=Hooks())

    result = manager.run_pipeline(
        pipeline_path,
        run_args={"required": "value"},
        hydrate=False,
        wait=True,
        max_attempts=2,
        poll_interval=1,
    )

    assert result["response"]["id"] == "run-1"
    assert result["wait"]["status"] == "SUCCEEDED"
    assert client.cancelled == ["run-1"]
    assert events == [
        ("before_lifecycle", 1),
        ("around", 1),
        ("poll", 1, "SUCCEEDED"),
        ("should_cancel", "run-1", 2),
        ("before_retry", "run-1", 2, "fail first wait"),
        ("after_lifecycle", 1, False, "fail first wait"),
        ("before_lifecycle", 2),
        ("around", 2),
        ("after_retry_submit", "run-1", 2),
        ("poll", 2, "SUCCEEDED"),
        ("after_lifecycle", 2, True, None),
    ]


def test_pipeline_runs_path_retry_rebuilds_submit_body_each_attempt(tmp_path: Path) -> None:
    pipeline_path = _write_pipeline(tmp_path / "pipeline.yaml")
    reads = []
    events = []

    class Hooks(PipelineRunHooks):
        def read_pipeline_yaml(self, pipeline_path):
            reads.append(len(reads) + 1)
            return {
                "name": f"Retry Source {len(reads)}",
                "inputs": [{"name": "required", "type": "String"}],
                "implementation": {"graph": {"tasks": {}}},
            }

        def after_poll(self, poll, context):
            events.append(("poll", context.attempt, context.pipeline_spec["name"]))
            if context.attempt == 1:
                raise PipelineRunError("retry after first build")

    client = FakeClient()
    manager = PipelineRunManager(client=client, hooks=Hooks())

    result = manager.run_pipeline(
        pipeline_path,
        run_args={"required": "value"},
        hydrate=False,
        wait=True,
        max_attempts=2,
        poll_interval=1,
    )

    assert result["wait"]["status"] == "SUCCEEDED"
    assert reads == [1, 2]
    assert [body["root_task"]["componentRef"]["spec"]["name"] for body in client.created] == [
        "Retry Source 1",
        "Retry Source 2",
    ]
    assert events == [
        ("poll", 1, "Retry Source 1"),
        ("poll", 2, "Retry Source 2"),
    ]


def test_pipeline_runs_run_pipeline_spec_uses_in_memory_spec_lifecycle() -> None:
    events = []
    spec = {
        "name": "Prepared Spec",
        "inputs": [{"name": "required", "type": "String"}],
        "implementation": {"graph": {"tasks": {}}},
    }

    class Hooks(PipelineRunHooks):
        def around_run(self, context):
            events.append(("around", context.run_name, context.pipeline_path))
            return nullcontext()

        def before_submit_context(self, context):
            events.append(("before_submit", context.run_name, context.pipeline_spec["name"]))

        def after_submit_context(self, context):
            events.append(("after_submit", context.run_id, context.root_execution_id))

    client = FakeClient()
    manager = PipelineRunManager(client=client, hooks=Hooks())

    result = manager.run_pipeline_spec(
        spec,
        run_args={"required": "value"},
        annotations={"team": "oss"},
        pipeline_path="already-prepared.yaml",
        wait=False,
    )

    assert result["response"]["id"] == "run-1"
    assert result["context"].run_id == "run-1"
    assert client.created[0]["root_task"]["componentRef"]["spec"]["name"] == "Prepared Spec"
    assert client.created[0]["annotations"] == {"team": "oss"}
    assert events == [
        ("around", "Prepared Spec", "already-prepared.yaml"),
        ("before_submit", "Prepared Spec", "Prepared Spec"),
        ("after_submit", "run-1", "exec-1"),
    ]


def test_pipeline_runs_run_prepared_body_retry_body_factory() -> None:
    events = []
    base_body = {
        "root_task": {
            "componentRef": {"spec": {"name": "Body", "implementation": {"graph": {"tasks": {}}}}},
            "arguments": {"attempt": 1},
        },
        "annotations": {},
    }

    class Hooks(PipelineRunHooks):
        def after_poll(self, poll, context):
            events.append(("poll", context.attempt, context.submit_body["root_task"]["arguments"]))
            if context.attempt == 1:
                raise PipelineRunError("retry first body")

        def before_retry(self, context, error, *, next_attempt):
            events.append(("before_retry", context.run_id, next_attempt, str(error)))

        def after_retry_submit(self, context):
            events.append(("after_retry_submit", context.attempt))

    def retry_body_factory(attempt, previous_context, error):
        assert attempt == 2
        assert previous_context.run_id == "run-1"
        assert str(error) == "retry first body"
        retry_body = copy.deepcopy(base_body)
        retry_body["root_task"]["arguments"] = {"attempt": attempt}
        return retry_body

    client = FakeClient()
    manager = PipelineRunManager(client=client, hooks=Hooks())

    result = manager.run_prepared_body(
        base_body,
        wait=True,
        max_attempts=2,
        poll_interval=1,
        retry_body_factory=retry_body_factory,
    )

    assert result["wait"]["status"] == "SUCCEEDED"
    assert [body["root_task"]["arguments"] for body in client.created] == [
        {"attempt": 1},
        {"attempt": 2},
    ]
    assert events == [
        ("poll", 1, {"attempt": 1}),
        ("before_retry", "run-1", 2, "retry first body"),
        ("after_retry_submit", 2),
        ("poll", 2, {"attempt": 2}),
    ]


def test_pipeline_runs_wait_exit_on_first_failure_exits_on_failed() -> None:
    class FailedGraphClient(FakeClient):
        def pipeline_runs_get(self, id: str, include_execution_stats: bool | None = None) -> dict[str, Any]:
            return {
                "id": id,
                "root_execution_id": "exec-1",
                "execution_status_stats": {"RUNNING": 1},
            }

        def executions_graph_execution_state(self, id: str) -> dict[str, Any]:
            return {"status_totals": {"RUNNING": 2, "FAILED": 1}}

    manager = PipelineRunManager(client=FailedGraphClient())

    result = manager.wait_for_completion(
        "run-1",
        max_wait=10,
        poll_interval=1,
        use_graph_state=True,
        exit_on_first_failure=True,
    )

    assert result["early_exit"] is True
    assert result["failed_count"] == 1
    assert result["error_count"] == 0
    assert result["status_counts"] == {"RUNNING": 2, "FAILED": 1}
    assert isinstance(result["elapsed_seconds"], float)


def test_pipeline_runs_wait_exit_on_first_failure_exits_on_system_error() -> None:
    class ErrorGraphClient(FakeClient):
        def pipeline_runs_get(self, id: str, include_execution_stats: bool | None = None) -> dict[str, Any]:
            return {
                "id": id,
                "root_execution_id": "exec-1",
                "execution_status_stats": {"RUNNING": 1},
            }

        def executions_graph_execution_state(self, id: str) -> dict[str, Any]:
            return {"status_totals": {"RUNNING": 2, "SYSTEM_ERROR": 1}}

    manager = PipelineRunManager(client=ErrorGraphClient())

    result = manager.wait_for_completion(
        "run-1",
        max_wait=10,
        poll_interval=1,
        use_graph_state=True,
        exit_on_first_failure=True,
    )

    assert result["early_exit"] is True
    assert result["failed_count"] == 0
    assert result["error_count"] == 1
    assert result["status_counts"] == {"RUNNING": 2, "SYSTEM_ERROR": 1}


def test_pipeline_runs_wait_exit_on_first_failure_disabled_does_not_exit(monkeypatch) -> None:
    class FailedGraphClient(FakeClient):
        def pipeline_runs_get(self, id: str, include_execution_stats: bool | None = None) -> dict[str, Any]:
            return {
                "id": id,
                "root_execution_id": "exec-1",
                "execution_status_stats": {"RUNNING": 1},
            }

        def executions_graph_execution_state(self, id: str) -> dict[str, Any]:
            return {"status_totals": {"RUNNING": 2, "FAILED": 1}}

    manager = PipelineRunManager(client=FailedGraphClient())
    sleeps: list[float] = []
    monkeypatch.setattr("tangle_cli.pipeline_run_manager.time.sleep", lambda value: sleeps.append(value))

    result = manager.wait_for_completion(
        "run-1",
        max_wait=0,
        poll_interval=1,
        use_graph_state=True,
        timeout_clock="wall",
    )

    assert result["timed_out"] is True
    assert "early_exit" not in result
    assert result["failed_count"] == 1
    assert sleeps == []


def test_pipeline_runs_wait_timeout_still_routes_via_timeout_hook() -> None:
    events = []

    class RunningClient(FakeClient):
        def pipeline_runs_get(self, id: str, include_execution_stats: bool | None = None) -> dict[str, Any]:
            return {
                "id": id,
                "root_execution_id": "exec-1",
                "execution_status_stats": {"RUNNING": 1},
            }

    class Hooks(PipelineRunHooks):
        def should_exit_early(self, poll, context):
            events.append("should_exit_early")
            return super().should_exit_early(poll, context)

        def on_timeout(self, poll, context):
            events.append("timeout")

        def on_early_exit_before_release(self, poll, context):
            events.append("early_exit")

    manager = PipelineRunManager(client=RunningClient(), hooks=Hooks())

    result = manager.wait_for_completion(
        "run-1",
        max_wait=0,
        poll_interval=1,
        exit_on_first_failure=True,
    )

    assert result["timed_out"] is True
    assert "early_exit" not in result
    assert events == ["should_exit_early", "timeout"]


def test_pipeline_runs_wait_allows_zero_poll_interval_when_opted_in() -> None:
    class RunningClient(FakeClient):
        def pipeline_runs_get(self, id: str, include_execution_stats: bool | None = None) -> dict[str, Any]:
            return {
                "id": id,
                "root_execution_id": "exec-1",
                "execution_status_stats": {"RUNNING": 1},
            }

    manager = PipelineRunManager(client=RunningClient())

    result = manager.wait_for_completion(
        "run-1",
        max_wait=0,
        poll_interval=0,
        allow_zero_poll_interval=True,
        timeout_clock="wall",
    )

    assert result["timed_out"] is True


def test_pipeline_run_status_uses_deterministic_precedence() -> None:
    run = {
        "execution_status_stats": {
            "QUEUED": 3,
            "PENDING": 2,
            "RUNNING": 1,
        }
    }
    assert PipelineRunManager.status_from_run(run) == "RUNNING"

    terminal_run = {
        "execution_status_stats": {
            "SUCCEEDED": 3,
            "SKIPPED": 2,
            "FAILED": 1,
        }
    }
    assert PipelineRunManager.status_from_run(terminal_run) == "FAILED"


def test_pipeline_runs_wait_is_bounded_and_testable(monkeypatch):
    fake_client = FakeClient()
    manager = PipelineRunManager(client=fake_client)
    sleeps: list[float] = []
    monkeypatch.setattr("tangle_cli.pipeline_run_manager.time.sleep", lambda value: sleeps.append(value))

    result = manager.wait_for_completion("run-1", max_wait=1, poll_interval=0.01)

    assert result["timed_out"] is False
    assert result["status"] == "SUCCEEDED"
    assert sleeps == []


def test_pipeline_runs_wait_rejects_unbounded_or_invalid_polling() -> None:
    manager = PipelineRunManager(client=FakeClient())

    with pytest.raises(PipelineRunError, match="--max-wait"):
        manager.wait_for_completion("run-1", max_wait=-1, poll_interval=1)
    with pytest.raises(PipelineRunError, match="--poll-interval"):
        manager.wait_for_completion("run-1", max_wait=1, poll_interval=0)


def test_pipeline_runs_run_as_is_extension_seam(tmp_path: Path):
    pipeline_path = _write_pipeline(tmp_path / "pipeline.yaml")
    manager = PipelineRunManager(client=FakeClient())

    with pytest.raises(PipelineRunError, match="--run-as"):
        manager.submit_pipeline(
            pipeline_path,
            run_args={"required": "value"},
            hydrate=False,
            run_as="service@example.com",
        )


def test_pipeline_runner_orchestrates_load_validate_submit_wait(tmp_path: Path) -> None:
    pipeline_path = _write_pipeline(tmp_path / "pipeline.yaml")
    client = FakeClient()
    calls: list[str] = []

    class Hooks(PipelineRunnerHooks):
        def validate_pipeline_for_run(self, pipeline_spec, **kwargs):  # type: ignore[no-untyped-def]
            calls.append(f"validate:{pipeline_spec['name']}:{kwargs['skip_validation']}")
            return []

        def before_submit_pipeline_spec(self, pipeline_spec, **kwargs):  # type: ignore[no-untyped-def]
            calls.append("before_submit")
            updated = copy.deepcopy(pipeline_spec)
            updated["metadata"] = {"annotations": {"run-name-template": "Run ${arguments.required}"}}
            return updated

    runner = PipelineRunner(client=client, hooks=Hooks())

    result = runner.run_pipeline(
        pipeline_path,
        run_args={"required": "value"},
        annotations={"team": "oss"},
        hydrate=False,
        wait=True,
        use_graph_state=False,
        max_wait=1,
        poll_interval=0.01,
    )

    assert result["success"] is True
    assert result["status"] == "SUCCEEDED"
    assert result["pipeline_name"] == "Run value"
    assert result["run_id"] == "run-1"
    assert calls == ["validate:Demo Pipeline:False", "before_submit"]
    assert client.created[0]["annotations"] == {"team": "oss"}
    assert client.created[0]["root_task"]["componentRef"]["spec"]["name"] == "Run value"


def test_pipeline_runner_maps_non_mapping_yaml_to_run_error(tmp_path: Path) -> None:
    pipeline_path = tmp_path / "bad.yaml"
    pipeline_path.write_text("[]\n", encoding="utf-8")
    runner = PipelineRunner(client=FakeClient())

    with pytest.raises(PipelineRunError, match="top-level mapping"):
        runner.run_pipeline(pipeline_path, hydrate=False)


def test_pipeline_runner_layout_is_hookable(tmp_path: Path) -> None:
    pipeline_path = _write_pipeline(tmp_path / "pipeline.yaml")
    client = FakeClient()

    class Hooks(PipelineRunnerHooks):
        def should_apply_layout(self, pipeline_spec, **kwargs):  # type: ignore[no-untyped-def]
            assert kwargs["force_layout"] is True
            assert kwargs["layout_algorithm"] == "dot"
            return True

        def apply_layout(self, pipeline_spec, **kwargs):  # type: ignore[no-untyped-def]
            updated = copy.deepcopy(pipeline_spec)
            updated["layout_hook_ran"] = True
            return updated

    runner = PipelineRunner(client=client, hooks=Hooks())

    runner.run_pipeline(
        pipeline_path,
        run_args={"required": "value"},
        hydrate=False,
        force_layout=True,
        layout_algorithm="dot",
    )

    assert client.created[0]["root_task"]["componentRef"]["spec"]["layout_hook_ran"] is True


def test_pipeline_runner_path_retry_prepares_each_attempt(tmp_path: Path) -> None:
    pipeline_path = _write_pipeline(tmp_path / "pipeline.yaml")

    class FlakyClient(FakeClient):
        def pipeline_runs_create(self, body: Any = None) -> dict[str, Any]:
            self.created.append(body)
            if len(self.created) == 1:
                raise PipelineRunError("transient submit failure")
            return {"id": "run-2", "root_execution_id": "exec-2"}

    class Hooks(PipelineRunnerHooks):
        def before_submit_pipeline_spec(self, pipeline_spec, **kwargs):  # type: ignore[no-untyped-def]
            updated = copy.deepcopy(pipeline_spec)
            updated["name"] = f"attempt-{len(client.created) + 1}"
            return updated

    client = FlakyClient()
    runner = PipelineRunner(client=client, hooks=Hooks())

    result = runner.run_pipeline(
        pipeline_path,
        run_args={"required": "value"},
        hydrate=False,
        wait=True,
        max_attempts=2,
        max_wait=1,
        poll_interval=0.01,
    )

    assert result["run_id"] == "run-2"
    assert [body["root_task"]["componentRef"]["spec"]["name"] for body in client.created] == [
        "attempt-1",
        "attempt-2",
    ]


def test_pipeline_runner_wait_failed_status_is_not_success(tmp_path: Path) -> None:
    pipeline_path = _write_pipeline(tmp_path / "pipeline.yaml")

    class FailedClient(FakeClient):
        def pipeline_runs_get(self, id: str, include_execution_stats: bool | None = None) -> dict[str, Any]:
            return {
                "id": id,
                "root_execution_id": "exec-1",
                "execution_status_stats": {"FAILED": 1},
            }

    runner = PipelineRunner(client=FailedClient())

    result = runner.run_pipeline(
        pipeline_path,
        run_args={"required": "value"},
        hydrate=False,
        wait=True,
        max_wait=1,
        poll_interval=0.01,
    )

    assert result["status"] == "FAILED"
    assert result["success"] is False


def test_pipeline_runner_cleanup_runs_after_prepare_failure(tmp_path: Path) -> None:
    pipeline_path = _write_pipeline(tmp_path / "pipeline.yaml")
    temp_effective_path = tmp_path / "hydrated.yaml"
    temp_effective_path.write_text("name: hydrated\n", encoding="utf-8")
    cleaned: list[tuple[Path | None, str | None]] = []

    class Hooks(PipelineRunnerHooks):
        def hydrate_pipeline_for_run(self, pipeline_path, **kwargs):  # type: ignore[no-untyped-def]
            return yaml.safe_load(Path(pipeline_path).read_text(encoding="utf-8")), temp_effective_path

        def validate_pipeline_for_run(self, pipeline_spec, **kwargs):  # type: ignore[no-untyped-def]
            return ["boom"]

        def cleanup_prepared_pipeline(self, preparation, *, error=None):  # type: ignore[no-untyped-def]
            path = Path(preparation.effective_path) if preparation.effective_path is not None else None
            cleaned.append((path, str(error) if error else None))
            if path is not None:
                path.unlink(missing_ok=True)

    runner = PipelineRunner(client=FakeClient(), hooks=Hooks())

    with pytest.raises(PipelineRunError, match="boom"):
        runner.run_pipeline(pipeline_path, hydrate=True)

    assert cleaned == [(temp_effective_path, "Pipeline validation failed:\n  - boom")]
    assert not temp_effective_path.exists()
