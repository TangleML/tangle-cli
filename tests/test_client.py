from __future__ import annotations

import io
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
import requests

import tangle_cli.client as client_module
from tangle_cli.client import TangleApiClient
from tangle_cli.logger import CaptureLogger
from tangle_cli.models import ComponentInfo


def _response(payload: Any = None, status_code: int = 200) -> requests.Response:
    r = requests.Response()
    r.status_code = status_code
    if payload is None:
        r._content = b""
    else:
        r._content = json.dumps(payload).encode("utf-8")
        r.headers["Content-Type"] = "application/json"
    r.request = requests.Request("GET", "https://api.test").prepare()
    return r


class _FakeSession:
    def __init__(self, responses: list[requests.Response | Exception] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.responses = responses or []

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        self.calls.append({"method": method, "url": url, **kwargs})
        if self.responses:
            next_response = self.responses.pop(0)
            if isinstance(next_response, Exception):
                raise next_response
            return next_response
        return _response({})


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


def _tracked_stream_response(raw: Any, status_code: int = 200) -> requests.Response:
    """A streaming-style response reading from ``raw`` that records ``close()`` in ``_closed``."""

    r = requests.Response()
    r.status_code = status_code
    r.raw = raw
    r.headers["Content-Type"] = "text/event-stream"
    r.request = requests.Request("GET", "https://api.test").prepare()
    r._closed = False
    original_close = r.close

    def tracked_close() -> None:
        r._closed = True
        original_close()

    r.close = tracked_close  # type: ignore[method-assign]
    return r


def _stream_response(lines: list[bytes] | None = None, status_code: int = 200) -> requests.Response:
    body = b"\n".join(lines) if lines else b""
    return _tracked_stream_response(io.BytesIO(body), status_code)


def test_stream_execution_container_log_yields_lines_and_closes() -> None:
    stream = _stream_response([b"line-1", b"line-2", b"line-3"])
    session = _FakeSession([stream])
    client = TangleApiClient("https://api.test", session=session)

    lines = list(client.iter_execution_container_log_lines("exec-1"))

    assert lines == ["line-1", "line-2", "line-3"]
    assert stream._closed is True
    assert session.calls[0]["url"] == "https://api.test/api/executions/exec-1/stream_container_log"
    assert session.calls[0]["stream"] is True
    # The follow stream keeps the connect timeout but has no per-read timeout,
    # so a healthy stream that is idle (container quiet) is never killed.
    assert session.calls[0]["timeout"] == (client.timeout, None)


def test_stream_execution_container_log_closes_on_early_break() -> None:
    stream = _stream_response([b"a", b"b", b"c"])
    session = _FakeSession([stream])
    client = TangleApiClient("https://api.test", session=session)

    gen = client.iter_execution_container_log_lines("exec-1")
    assert next(iter(gen)) == "a"
    gen.close()  # type: ignore[union-attr]

    assert stream._closed is True


def test_stream_open_retries_transient_status_then_succeeds(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("tangle_cli.client.time.sleep", sleeps.append)
    bad = _stream_response(status_code=503)
    ok = _stream_response([b"recovered"])
    session = _FakeSession([bad, ok])
    logger = CaptureLogger()
    client = TangleApiClient("https://api.test", session=session, logger=logger)

    lines = list(client.iter_execution_container_log_lines("exec-1"))

    assert lines == ["recovered"]
    assert bad._closed is True
    assert sleeps == [1.0]
    assert len(session.calls) == 2
    # Every stream-open retry sleep is announced through the client logger.
    assert "transient HTTP 503 opening log stream; retrying in 1.0s (attempt 2/7)" in (
        logger.get_logs() or ""
    )


def test_stream_open_retries_transport_error_then_succeeds(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("tangle_cli.client.time.sleep", sleeps.append)
    ok = _stream_response([b"after-blip"])
    calls = {"n": 0}

    class FlakySession(_FakeSession):
        def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                raise requests.ConnectionError("transient transport blip")
            return ok

    client = TangleApiClient("https://api.test", session=FlakySession())

    lines = list(client.iter_execution_container_log_lines("exec-1"))

    assert lines == ["after-blip"]
    assert calls["n"] == 2
    assert sleeps == [1.0]


def test_stream_open_backoff_doubles_and_is_capped(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("tangle_cli.client.time.sleep", sleeps.append)
    attempts = TangleApiClient._MAX_STREAM_OPEN_ATTEMPTS
    session = _FakeSession([_stream_response(status_code=503) for _ in range(attempts)])
    logger = CaptureLogger()
    client = TangleApiClient("https://api.test", session=session, logger=logger)

    with pytest.raises(requests.HTTPError):
        client.stream_execution_container_log("exec-1")

    assert sleeps == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0]
    # The last retry announces the final attempt; the exhausted 7th attempt
    # raises without announcing an 8th.
    logs = logger.get_logs() or ""
    assert "(attempt 7/7)" in logs
    assert "8/7" not in logs


def test_stream_open_raises_non_retryable_status_immediately(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("tangle_cli.client.time.sleep", sleeps.append)
    bad = _stream_response(status_code=404)
    session = _FakeSession([bad])
    client = TangleApiClient("https://api.test", session=session)

    with pytest.raises(requests.HTTPError):
        client.stream_execution_container_log("exec-1")

    # The streamed response must be closed before the non-retryable error
    # propagates so the open connection is not leaked.
    assert bad._closed is True
    assert sleeps == []
    assert len(session.calls) == 1


def test_stream_open_exhausts_retries_and_raises_last_status(monkeypatch) -> None:
    monkeypatch.setattr("tangle_cli.client.time.sleep", lambda _seconds: None)
    attempts = TangleApiClient._MAX_STREAM_OPEN_ATTEMPTS
    session = _FakeSession([_stream_response(status_code=502) for _ in range(attempts)])
    client = TangleApiClient("https://api.test", session=session)

    with pytest.raises(requests.HTTPError) as exc_info:
        client.stream_execution_container_log("exec-1")

    assert exc_info.value.response.status_code == 502
    assert len(session.calls) == attempts


def test_stream_open_exhausts_retries_and_raises_last_transport_error(monkeypatch) -> None:
    monkeypatch.setattr("tangle_cli.client.time.sleep", lambda _seconds: None)

    class AlwaysFailingSession(_FakeSession):
        def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
            raise requests.ConnectionError("permanent transport failure")

    client = TangleApiClient("https://api.test", session=AlwaysFailingSession())

    with pytest.raises(requests.ConnectionError, match="permanent transport failure"):
        client.stream_execution_container_log("exec-1")


def test_stream_open_cross_origin_redirect_is_not_retried(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("tangle_cli.client.time.sleep", sleeps.append)
    redirect = _stream_response(status_code=307)
    redirect.url = "https://api.test/api/executions/exec-1/stream_container_log"
    redirect.headers["Location"] = "https://attacker.example/leak"
    session = _FakeSession([redirect])
    client = TangleApiClient("https://api.test", session=session)

    with pytest.raises(requests.HTTPError, match="cross-origin redirect") as exc_info:
        client.stream_execution_container_log("exec-1")

    # Same-origin redirect protection must propagate immediately, not be retried.
    assert sleeps == []
    assert len(session.calls) == 1
    # The rejected streamed response is attached to the guard error and no
    # iterator ever receives it, so it must be closed before the error
    # propagates to avoid leaking the open connection.
    assert exc_info.value.response is redirect
    assert redirect._closed is True


def test_stream_open_too_many_redirects_is_not_retried(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("tangle_cli.client.time.sleep", sleeps.append)

    def same_origin_redirect() -> requests.Response:
        r = _stream_response(status_code=307)
        r.url = "https://api.test/api/executions/exec-1/stream_container_log"
        r.headers["Location"] = "/api/executions/exec-1/stream_container_log"
        return r

    redirect_calls = TangleApiClient._MAX_REDIRECTS + 1
    responses = [same_origin_redirect() for _ in range(redirect_calls)]
    session = _FakeSession(list(responses))
    client = TangleApiClient("https://api.test", session=session)

    with pytest.raises(requests.TooManyRedirects) as exc_info:
        client.stream_execution_container_log("exec-1")

    # One stream-open attempt that exhausts redirects; no retry of the open.
    assert sleeps == []
    assert len(session.calls) == redirect_calls
    # Every streamed redirect response must be closed; the final one is
    # attached to the guard error and must not leak.
    assert all(r._closed is True for r in responses)
    assert exc_info.value.response is responses[-1]


def test_stream_open_verbose_does_not_read_streamed_body(monkeypatch) -> None:
    monkeypatch.setenv("TANGLE_VERBOSE", "1")
    stream = _stream_response([b"line-1", b"line-2"])
    text_reads: list[int] = []
    original_text = type(stream).text

    def tracked_text(self: requests.Response) -> str:
        text_reads.append(1)
        return original_text.fget(self)  # type: ignore[attr-defined]

    monkeypatch.setattr(type(stream), "text", property(tracked_text))
    logger = CaptureLogger()
    session = _FakeSession([stream])
    client = TangleApiClient("https://api.test", session=session, logger=logger)

    response = client.stream_execution_container_log("exec-1")

    # Verbose logging must not drain the streamed body before the caller can
    # iterate it; the log stream stays readable.
    assert text_reads == []
    assert response._closed is False
    assert list(response.iter_lines()) == [b"line-1", b"line-2"]
    logs = logger.get_logs() or ""
    assert "<streaming body omitted>" in logs


def test_rate_limit_retry_closes_streamed_response_before_sleep(monkeypatch) -> None:
    closed_at_sleep: list[bool] = []
    rate_limited = _stream_response(status_code=429)
    rate_limited.headers["Retry-After"] = "0"
    ok = _stream_response([b"recovered"])

    def tracking_sleep(_seconds: float) -> None:
        # Record whether the 429 stream is already closed when the rate-limit
        # sleep runs; it must not be held open during the sleep.
        closed_at_sleep.append(rate_limited._closed)

    monkeypatch.setattr("tangle_cli.client.time.sleep", tracking_sleep)
    closed_at_retry: list[bool] = []

    class TrackingSession(_FakeSession):
        def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
            # Record whether the prior 429 stream was already closed by the
            # time the successful retry is issued.
            if self.calls:
                closed_at_retry.append(rate_limited._closed)
            return super().request(method, url, **kwargs)

    session = TrackingSession([rate_limited, ok])
    client = TangleApiClient("https://api.test", session=session)

    response = client.stream_execution_container_log("exec-1")

    assert response is ok
    assert len(session.calls) == 2
    # The intermediate 429 streamed response must be closed before sleeping and
    # before the retry is issued.
    assert closed_at_sleep == [True]
    assert closed_at_retry == [True]
    assert rate_limited._closed is True
    assert list(response.iter_lines()) == [b"recovered"]


def test_auth_refresh_closes_streamed_response_before_retry() -> None:
    unauthorized = _stream_response(status_code=401)
    ok = _stream_response([b"authorized"])
    closed_at_refresh: list[bool] = []
    closed_at_retry: list[bool] = []

    class RefreshingClient(TangleApiClient):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.refreshes = 0

        def _refresh_auth(self) -> None:
            self.refreshes += 1
            # On the auth-refresh triggered by the 401, the streamed 401
            # response must already be closed (not held open during refresh).
            if self.refreshes == 2:
                closed_at_refresh.append(unauthorized._closed)
            self.headers["Authorization"] = f"Bearer refreshed-{self.refreshes}"

    class TrackingSession(_FakeSession):
        def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
            # Record whether the prior 401 stream was already closed by the
            # time the successful retry is issued.
            if self.calls:
                closed_at_retry.append(unauthorized._closed)
            return super().request(method, url, **kwargs)

    session = TrackingSession([unauthorized, ok])
    client = RefreshingClient("https://api.test", session=session)

    response = client._make_request("GET", "/api/users/me", stream=True)

    assert response is ok
    assert client.refreshes == 2
    assert len(session.calls) == 2
    # The intermediate 401 streamed response must be closed before the auth
    # refresh and before the retry is issued.
    assert closed_at_refresh == [True]
    assert closed_at_retry == [True]
    assert unauthorized._closed is True
    # The successful retry stream remains open and readable for the caller.
    assert response._closed is False
    assert list(response.iter_lines()) == [b"authorized"]


def test_stream_open_synthetic_http_error_from_make_request_is_not_retried(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("tangle_cli.client.time.sleep", sleeps.append)
    client = TangleApiClient("https://api.test", session=_FakeSession())
    calls = {"n": 0}

    def fake_make_request(*args: Any, **kwargs: Any) -> requests.Response:
        calls["n"] += 1
        raise requests.HTTPError("redirect guard tripped")

    monkeypatch.setattr(client, "_make_request", fake_make_request)

    with pytest.raises(requests.HTTPError, match="redirect guard tripped"):
        client.stream_execution_container_log("exec-1")

    assert sleeps == []
    assert calls["n"] == 1


class _ScriptedRaw:
    """A raw stream whose ``read`` replays scripted byte chunks/exceptions.

    Each ``read`` returns the next queued ``bytes`` chunk verbatim (ignoring the
    requested size, so a multi-byte char can be split across reads) or raises a
    queued exception, modelling a mid-stream transport failure.
    """

    def __init__(self, chunks: list[bytes | Exception]) -> None:
        self._chunks = list(chunks)

    def read(self, _size: int = -1) -> bytes:
        if not self._chunks:
            return b""
        item = self._chunks.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def close(self) -> None:
        self._chunks.clear()


def _scripted_stream_response(chunks: list[bytes | Exception]) -> requests.Response:
    return _tracked_stream_response(_ScriptedRaw(chunks))


def test_stream_decodes_multibyte_char_split_across_chunks() -> None:
    # "café" and "日本語" each contain multi-byte UTF-8 sequences; feeding the
    # stream one byte at a time splits those sequences across chunk reads.
    # Decoding whole lines as UTF-8 must reassemble them rather than yield
    # replacement characters or mojibake.
    payload = "café\n日本語\n".encode("utf-8")
    stream = _scripted_stream_response([payload[i : i + 1] for i in range(len(payload))])
    client = TangleApiClient("https://api.test", session=_FakeSession([stream]))

    lines = list(client.iter_execution_container_log_lines("exec-1"))

    assert lines == ["café", "日本語"]
    assert stream._closed is True


def test_stream_read_error_mid_iteration_propagates_and_closes() -> None:
    # Once the stream is open the retry budget is spent; a transport failure
    # during iteration must propagate (not be retried or swallowed) and the
    # streamed response must still be closed by the iterator's finally block.
    stream = _scripted_stream_response(
        [b"line-1\n", requests.exceptions.ChunkedEncodingError("connection broken mid-stream")]
    )
    client = TangleApiClient("https://api.test", session=_FakeSession([stream]))

    gen = iter(client.iter_execution_container_log_lines("exec-1"))
    assert next(gen) == "line-1"
    with pytest.raises(requests.exceptions.ChunkedEncodingError, match="connection broken mid-stream"):
        next(gen)

    assert stream._closed is True
