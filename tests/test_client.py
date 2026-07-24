from __future__ import annotations

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


def test_get_retries_transient_5xx_then_succeeds(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("tangle_cli.client.time.sleep", sleeps.append)
    ok = _response({"ok": True})
    session = _FakeSession([_response(status_code=503), _response(status_code=500), ok])
    client = TangleApiClient("https://api.test", session=session)

    result = client._make_request("GET", "/api/test")

    assert result is ok
    assert len(session.calls) == 3
    assert sleeps == [1.0, 2.0]


def test_get_closes_intermediate_5xx_responses_before_retrying(monkeypatch) -> None:
    events: list[str] = []

    def tracking_response(status_code: int, marker: str) -> requests.Response:
        r = _response(status_code=status_code)
        r.close = lambda: events.append(f"close-{marker}")  # type: ignore[method-assign]
        return r

    monkeypatch.setattr("tangle_cli.client.time.sleep", lambda _delay: events.append("sleep"))
    session = _FakeSession(
        [tracking_response(503, "1"), tracking_response(500, "2"), tracking_response(200, "3")]
    )
    client = TangleApiClient("https://api.test", session=session)

    client._make_request("GET", "/api/test")

    # Intermediate 5xx are closed before each retry; the returned one is left open.
    assert events == ["close-1", "sleep", "close-2", "sleep"]


def test_get_retries_transport_error_then_succeeds(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("tangle_cli.client.time.sleep", sleeps.append)
    ok = _response({"ok": True})
    session = _FakeSession(
        [
            requests.ConnectionError("connection reset"),
            requests.Timeout("read timed out"),
            requests.exceptions.ChunkedEncodingError("incomplete chunked read"),
            requests.exceptions.ContentDecodingError("failed to decode gzip stream"),
            ok,
        ]
    )
    client = TangleApiClient("https://api.test", session=session)

    result = client._make_request("GET", "/api/test")

    assert result is ok
    assert len(session.calls) == 5
    assert sleeps == [1.0, 2.0, 4.0, 8.0]


def test_get_raises_after_exhausting_transport_retries(monkeypatch) -> None:
    monkeypatch.setattr("tangle_cli.client.time.sleep", lambda _delay: None)
    budget = TangleApiClient._MAX_GET_RETRIES
    final_attempt_error = requests.exceptions.ChunkedEncodingError("final permitted attempt")
    surplus = [requests.ConnectionError("never reached") for _ in range(3)]
    queued = [requests.ConnectionError("blip") for _ in range(budget)] + [final_attempt_error] + surplus
    assert len(queued) > budget + 1
    session = _FakeSession(queued)
    client = TangleApiClient("https://api.test", session=session)

    with pytest.raises(requests.exceptions.ChunkedEncodingError) as exc_info:
        client._make_request("GET", "/api/test")

    assert exc_info.value is final_attempt_error
    assert len(session.calls) == budget + 1
    assert len(session.responses) == len(surplus)


def test_get_returns_final_5xx_after_exhausting_status_retries(monkeypatch) -> None:
    closed: list[str] = []

    def tracking_5xx(marker: str) -> requests.Response:
        r = _response(status_code=503)
        r.close = lambda: closed.append(marker)  # type: ignore[method-assign]
        return r

    monkeypatch.setattr("tangle_cli.client.time.sleep", lambda _delay: None)
    budget = TangleApiClient._MAX_GET_RETRIES
    errors = [tracking_5xx(str(i)) for i in range(budget + 1)]
    trailing_ok = _response({"ok": True})
    session = _FakeSession([*errors, trailing_ok])
    client = TangleApiClient("https://api.test", session=session)

    result = client._make_request("GET", "/api/test")

    assert result is errors[budget]
    assert len(session.calls) == budget + 1
    assert closed == [str(i) for i in range(budget)]
    assert session.responses == [trailing_ok]


def test_post_is_not_retried_on_transient_5xx(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("tangle_cli.client.time.sleep", sleeps.append)
    server_error = _response(status_code=503)
    session = _FakeSession([server_error, _response({"ok": True})])
    client = TangleApiClient("https://api.test", session=session)

    result = client._make_request("POST", "/api/pipeline_runs/", json_data={"a": 1})

    assert result is server_error
    assert len(session.calls) == 1
    assert sleeps == []


def test_streamed_get_bypasses_transient_retry(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("tangle_cli.client.time.sleep", sleeps.append)
    server_error = _response(status_code=503)
    session = _FakeSession([server_error, _response({"ok": True})])
    client = TangleApiClient("https://api.test", session=session)

    result = client._make_request("GET", "/api/logs", stream=True)

    assert result is server_error
    assert len(session.calls) == 1
    assert sleeps == []


def test_transient_retry_decision_is_method_case_insensitive(monkeypatch) -> None:
    monkeypatch.setattr("tangle_cli.client.time.sleep", lambda _delay: None)

    def call(method: str) -> tuple[requests.Response, int]:
        ok = _response({"ok": True})
        session = _FakeSession([_response(status_code=503), ok])
        client = TangleApiClient("https://api.test", session=session)
        budget = client_module._RetryBudget(
            client._MAX_GET_RETRIES + 1,
            client_module.time.monotonic() + client._MAX_RETRY_ELAPSED_SECONDS,
        )
        result = client._request_with_transient_retries(
            method,
            "https://api.test/api/test",
            params=None,
            json_data=None,
            extra_headers=None,
            timeout=client.timeout,
            request_kwargs={},
            budget=budget,
        )
        return result, len(session.calls)

    get_result, get_calls = call("get")
    assert get_result.status_code == 200
    assert get_calls == 2

    post_result, post_calls = call("post")
    assert post_result.status_code == 503
    assert post_calls == 1


def test_get_retries_proxy_errors(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("tangle_cli.client.time.sleep", sleeps.append)
    ok = _response({"ok": True})
    session = _FakeSession(
        [
            requests.exceptions.ProxyError("proxy refused"),
            ok,
        ]
    )
    client = TangleApiClient("https://api.test", session=session)

    result = client._make_request("GET", "/api/test")

    assert result is ok
    assert len(session.calls) == 2
    assert sleeps == [1.0]


def test_get_does_not_retry_ssl_errors(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("tangle_cli.client.time.sleep", sleeps.append)
    error = requests.exceptions.SSLError("certificate verify failed")
    session = _FakeSession([error, _response({"ok": True})])
    client = TangleApiClient("https://api.test", session=session)

    with pytest.raises(requests.exceptions.SSLError) as exc_info:
        client._make_request("GET", "/api/test")

    assert exc_info.value is error
    assert len(session.calls) == 1
    assert sleeps == []


def test_get_transient_and_rate_limit_retry_layers_compose(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("tangle_cli.client.time.sleep", sleeps.append)
    ok = _response({"ok": True})
    session = _FakeSession(
        [
            _response(status_code=503),
            _response(status_code=429),
            _response(status_code=503),
            ok,
        ]
    )
    client = TangleApiClient("https://api.test", session=session)

    result = client._make_request("GET", "/api/test")

    assert result is ok
    assert len(session.calls) == 4
    # transient 1.0s, rate-limit 1.0s (no Retry-After), fresh transient 1.0s
    assert sleeps == [1.0, 1.0, 1.0]


def test_get_retry_sleeps_are_capped_and_announced_without_verbose(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("tangle_cli.client.time.sleep", sleeps.append)
    budget = TangleApiClient._MAX_GET_RETRIES
    session = _FakeSession([_response(status_code=503) for _ in range(budget + 1)])
    logger = CaptureLogger()
    client = TangleApiClient("https://api.test", session=session, logger=logger)

    result = client._make_request("GET", "/api/test")

    assert result.status_code == 503
    assert sleeps == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0]  # final sleep capped, not 32.0
    messages = (logger.get_logs() or "").splitlines()
    assert len(messages) == budget
    assert all(m.startswith("transient HTTP 503 on GET; retrying in ") for m in messages)
    assert messages[-1] == "transient HTTP 503 on GET; retrying in 30.0s (attempt 7/7)"


def test_get_retries_are_silent_on_default_non_verbose_client(monkeypatch, capsys) -> None:
    # A non-verbose client built without a logger stays silent; callers that
    # want retry announcements pass a logger (as the CLI command layer does).
    monkeypatch.delenv("TANGLE_VERBOSE", raising=False)
    monkeypatch.setattr("tangle_cli.client.time.sleep", lambda _delay: None)
    session = _FakeSession([_response(status_code=503), _response({"ok": True})])
    client = TangleApiClient("https://api.test", session=session)

    result = client._make_request("GET", "/api/test")

    assert result.status_code == 200
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_shared_budget_caps_total_requests_across_transient_and_rate_limit(monkeypatch) -> None:
    # Interleaved 503/429 responses must not let the rate-limit layer hand the
    # transient layer a fresh budget each round: the total physical request
    # count is bounded by the single shared budget, not their product.
    monkeypatch.setattr("tangle_cli.client.time.sleep", lambda _delay: None)
    budget = TangleApiClient._MAX_GET_RETRIES
    # Far more responses than the budget allows, alternating retryable states.
    session = _FakeSession([_response(status_code=503 if i % 2 == 0 else 429) for i in range(40)])
    client = TangleApiClient("https://api.test", session=session)

    result = client._make_request("GET", "/api/test")

    # Exactly the advertised budget of physical requests is spent, then the last
    # response surfaces for the caller's raise_for_status (no amplification).
    assert len(session.calls) == budget + 1
    assert result.status_code in {503, 429}


def test_auth_refresh_shares_transient_retry_budget(monkeypatch) -> None:
    # A 401 that triggers an auth refresh must continue on the same budget
    # rather than starting a fresh transient-retry round.
    monkeypatch.setattr("tangle_cli.client.time.sleep", lambda _delay: None)

    class RefreshingClient(TangleApiClient):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.refreshes = 0

        def _refresh_auth(self) -> None:
            self.refreshes += 1

    budget = TangleApiClient._MAX_GET_RETRIES
    # One 401 (consumes a request) followed by an unbroken run of 503s.
    session = _FakeSession(
        [_response(status_code=401)] + [_response(status_code=503) for _ in range(budget + 5)]
    )
    client = RefreshingClient("https://api.test", session=session)

    result = client._make_request("GET", "/api/test")

    assert result.status_code == 503
    # Refresh fired once for the 401 (plus the unconditional pre-request refresh).
    assert client.refreshes == 2
    # The 401 request plus the post-refresh retries share one budget: the total
    # never exceeds the shared cap (a fresh budget would allow budget+1 more).
    assert len(session.calls) == budget + 1


def test_shared_budget_caps_total_requests_across_transient_rate_limit_and_auth(monkeypatch) -> None:
    # The worst case the reviewer flagged: a 401 auth refresh, 429 rate limits,
    # and transient 503s all interleaved for one logical GET. A single shared
    # budget must bound the total physical request count instead of letting the
    # three layers multiply their per-layer limits together.
    monkeypatch.setattr("tangle_cli.client.time.sleep", lambda _delay: None)

    class RefreshingClient(TangleApiClient):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.refreshes = 0

        def _refresh_auth(self) -> None:
            self.refreshes += 1

    budget = TangleApiClient._MAX_GET_RETRIES
    # Lead with a 401 (drives one refresh), then alternate 429/503 far past the
    # budget so the cap, not the response list, is what stops the retries.
    responses = [_response(status_code=401)]
    responses += [_response(status_code=429 if i % 2 == 0 else 503) for i in range(40)]
    session = _FakeSession(responses)
    client = RefreshingClient("https://api.test", session=session)

    result = client._make_request("GET", "/api/test")

    # Exactly the advertised shared budget of physical requests is spent.
    assert len(session.calls) == budget + 1
    assert result.status_code in {429, 503}
    # Pre-request refresh plus exactly one refresh for the single 401; the 401's
    # retry continues on the shared budget rather than opening a fresh round.
    assert client.refreshes == 2


def test_shared_budget_deadline_halts_composed_retries_without_wallclock(monkeypatch) -> None:
    # The shared budget bounds retries by BOTH an attempt count and a
    # _MAX_RETRY_ELAPSED_SECONDS wall-time deadline. This pins the deadline
    # clause deterministically: a fake monotonic clock advances only when a
    # request is sent (never the real wall clock), so the elapsed-time cap, not
    # the attempt cap, is what stops the composed auth-refresh / rate-limit /
    # transient retry sequence. Without this every other budget test would still
    # pass on the attempt cap alone, so the deadline could be removed silently.
    clock = SimpleNamespace(now=1_000.0)
    step = 50.0
    window = TangleApiClient._MAX_RETRY_ELAPSED_SECONDS
    deadline = clock.now + window
    monkeypatch.setattr("tangle_cli.client.time.monotonic", lambda: clock.now)

    send_times: list[float] = []
    sleep_times: list[float] = []
    monkeypatch.setattr("tangle_cli.client.time.sleep", lambda _delay: sleep_times.append(clock.now))

    class _ClockAdvancingSession(_FakeSession):
        def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
            send_times.append(clock.now)
            response = super().request(method, url, **kwargs)
            # Time elapses only while a request is in flight; each send consumes
            # a fixed slice of the deadline window.
            clock.now += step
            return response

    class RefreshingClient(TangleApiClient):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.refreshes = 0

        def _refresh_auth(self) -> None:
            self.refreshes += 1

    # A 401 (auth-refresh path), a 429 (rate-limit path), then unbroken 503s
    # (transient path): all three layers draw on the one shared budget. Far more
    # responses are queued than either cap allows, so the cap that fires first is
    # what stops the sequence.
    responses = [_response(status_code=401), _response(status_code=429)]
    responses += [_response(status_code=503) for _ in range(20)]
    session = _ClockAdvancingSession(responses)
    client = RefreshingClient("https://api.test", session=session)

    result = client._make_request("GET", "/api/test")

    # The sequence genuinely retried across layers but stopped short of the
    # attempt cap with responses still queued: the deadline, not the attempt
    # count or the response list, is what ended it.
    attempt_cap = TangleApiClient._MAX_GET_RETRIES + 1
    assert 1 < len(session.calls) < attempt_cap
    assert session.responses, "unused responses prove the queue did not stop the retries"
    # Time actually crossed the deadline, yet no send or sleep happened at/after
    # it: can_retry gates every physical send and every sleep across the
    # transient and rate-limit layers.
    assert clock.now >= deadline
    assert all(t < deadline for t in send_times)
    assert all(t < deadline for t in sleep_times)
    # The composed auth path ran on the shared budget (pre-request refresh plus
    # one for the single 401), and the final 5xx surfaces for the caller.
    assert client.refreshes == 2
    assert result.status_code == 503


def test_get_5xx_exhaustion_raises_http_error_from_public_operation(monkeypatch) -> None:
    monkeypatch.setattr("tangle_cli.client.time.sleep", lambda _delay: None)
    budget = TangleApiClient._MAX_GET_RETRIES
    session = _FakeSession([_response(status_code=503) for _ in range(budget + 1)])
    client = TangleApiClient("https://api.test", session=session)

    with pytest.raises(requests.HTTPError) as exc_info:
        client.pipeline_runs_get("run-1")

    assert exc_info.value.response is not None
    assert exc_info.value.response.status_code == 503
    assert len(session.calls) == budget + 1
