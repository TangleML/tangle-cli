from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from tangle_cli import cli, pipeline_runs_cli
from tangle_cli.pipeline_runs import PipelineRunManager, PipelineRunError


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
    task_ref = spec["implementation"]["graph"]["tasks"]["task"]["componentRef"]
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

    run_app(app, ["sdk", "pipeline-runs", "annotations", "delete", "run-1", "owner"])
    assert fake_client.annotation_deletes == [("run-1", "owner")]

    output = tmp_path / "export.yaml"
    run_app(app, ["sdk", "pipeline-runs", "export", "run-1", "--output", str(output)])
    assert yaml.safe_load(output.read_text(encoding="utf-8"))["name"] == "Exported"


def test_pipeline_runs_wait_is_bounded_and_testable(monkeypatch):
    fake_client = FakeClient()
    manager = PipelineRunManager(client=fake_client)
    sleeps: list[float] = []
    monkeypatch.setattr("tangle_cli.pipeline_runs.time.sleep", lambda value: sleeps.append(value))

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
