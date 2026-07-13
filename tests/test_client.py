from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import tangle_cli.client as client_module
from tangle_cli.client import TangleApiClient
from tangle_cli.models import ComponentInfo


def test_find_existing_components_matches_exact_names_case_insensitively() -> None:
    client = TangleApiClient("https://api.test")
    client.list_published_component_infos = MagicMock(
        return_value=[
            ComponentInfo(name="Scrape V2", digest="matching-digest"),
            ComponentInfo(name="Other", digest="other-digest"),
        ]
    )

    results = client.find_existing_components(names=["scrape v2"])

    assert [component.digest for component in results] == ["matching-digest"]


def test_get_run_pipeline_spec_fetches_raw_root_execution_without_enrichment() -> None:
    client = TangleApiClient("https://api.test")
    task_spec = MagicMock(name="task_spec")
    execution = SimpleNamespace(task_spec=task_spec)
    client.pipeline_runs_get = MagicMock(
        return_value={"id": "run-1", "root_execution_id": "root-exec-1"}
    )
    client.executions_details = MagicMock(return_value=execution)
    client.get_run_details = MagicMock(
        side_effect=AssertionError("get_run_pipeline_spec must not enrich via get_run_details")
    )
    client._enrich_execution_tree = MagicMock()

    assert client.get_run_pipeline_spec("run-1") is task_spec
    client.executions_details.assert_called_once_with("root-exec-1")
    client.get_run_details.assert_not_called()
    client._enrich_execution_tree.assert_not_called()


def test_get_run_pipeline_spec_reads_generated_run_response_directly(monkeypatch) -> None:
    def fail_from_dict(*args, **kwargs):
        raise AssertionError("get_run_pipeline_spec should not round-trip through PipelineRun.from_dict")

    monkeypatch.setattr(client_module.PipelineRun, "from_dict", fail_from_dict)
    client = TangleApiClient("https://api.test")
    task_spec = MagicMock(name="task_spec")
    client.pipeline_runs_get = MagicMock(return_value=SimpleNamespace(root_execution_id="root-exec-1"))
    client.executions_details = MagicMock(return_value=SimpleNamespace(task_spec=task_spec))

    assert client.get_run_pipeline_spec("run-1") is task_spec
    client.executions_details.assert_called_once_with("root-exec-1")


def test_request_raw_is_public_alias_for_make_request() -> None:
    session = MagicMock()
    response = MagicMock(status_code=200)
    session.request.return_value = response
    client = TangleApiClient("https://api.test", session=session)

    result = client.request_raw("GET", "/api/test", stream=True)

    assert result is response
    call = session.request.call_args
    assert call.args[0] == "GET"
    assert call.args[1] == "https://api.test/api/test"
    assert call.kwargs["stream"] is True


class _CaptureLogger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def info(self, message: str) -> None:
        self.messages.append(message)


class _StreamBodyGuardResponse:
    """Response whose body accessors raise, proving verbose logging never buffers.

    A ``stream=True`` request (artifact downloads) must not have its body read
    into diagnostics; touching ``.text``/``.content`` would defeat streaming and
    could write artifact bytes verbatim into logs.
    """

    def __init__(self) -> None:
        self.status_code = 200
        self.headers = {"Content-Type": "application/octet-stream"}
        self.url = "https://api.test/api/artifacts/a/data"

    @property
    def text(self) -> str:
        raise AssertionError("streamed request must not buffer response.text")

    @property
    def content(self) -> bytes:
        raise AssertionError("streamed request must not buffer response.content")


def test_verbose_streaming_request_logs_placeholder_without_buffering_body() -> None:
    session = MagicMock()
    session.request.return_value = _StreamBodyGuardResponse()
    logger = _CaptureLogger()
    client = TangleApiClient(
        "https://api.test", session=session, verbose=True, logger=logger
    )

    response = client.request_raw("GET", "/api/artifacts/a/data", stream=True)

    assert isinstance(response, _StreamBodyGuardResponse)
    assert any("<streaming body omitted>" in message for message in logger.messages)
