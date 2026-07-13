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
            client._MAX_PHYSICAL_SENDS,
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


def _redirect(url: str, location: str, status_code: int = 307) -> requests.Response:
    r = _response(status_code=status_code)
    r.url = url
    r.headers["Location"] = location
    return r


def _redirect_chain(hops: int, final: requests.Response) -> list[requests.Response]:
    """A ``hops``-deep same-origin 307 chain ending in ``final``."""

    return [
        _redirect(f"https://api.test/api/hop{hop}", f"/api/hop{hop + 1}") for hop in range(hops)
    ] + [final]


class _ClockAdvancingSession(_FakeSession):
    """Session whose sends are the only thing that advances the fake clock.

    Deadline behaviour is then a pure function of the response sequence, with no
    dependence on how fast the test host runs.
    """

    def __init__(
        self,
        responses: list[requests.Response | Exception],
        clock: SimpleNamespace,
        step: float,
    ) -> None:
        super().__init__(responses)
        self.clock = clock
        self.step = step
        self.send_times: list[float] = []

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        self.send_times.append(self.clock.now)
        response = super().request(method, url, **kwargs)
        self.clock.now += self.step
        return response


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

    sleep_times: list[float] = []
    monkeypatch.setattr("tangle_cli.client.time.sleep", lambda _delay: sleep_times.append(clock.now))

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
    session = _ClockAdvancingSession(responses, clock, step)
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
    assert all(t < deadline for t in session.send_times)
    assert all(t < deadline for t in sleep_times)
    # The composed auth path ran on the shared budget (pre-request refresh plus
    # one for the single 401), and the final 5xx surfaces for the caller.
    assert client.refreshes == 2
    assert result.status_code == 503


def test_long_retry_after_is_not_slept_when_it_would_cross_the_deadline(monkeypatch) -> None:
    # A Retry-After longer than the time left on the shared deadline must end the
    # sequence immediately. Sleeping it out would only be followed by a send the
    # budget is then obliged to refuse, so the wait is pure dead time.
    clock = SimpleNamespace(now=1_000.0)
    deadline = clock.now + TangleApiClient._MAX_RETRY_ELAPSED_SECONDS
    monkeypatch.setattr("tangle_cli.client.time.monotonic", lambda: clock.now)
    sleeps: list[float] = []
    monkeypatch.setattr("tangle_cli.client.time.sleep", sleeps.append)

    rate_limited = _response(status_code=429)
    rate_limited.headers["Retry-After"] = "60"
    # One send burns most of the window, leaving less than the 60s Retry-After.
    session = _ClockAdvancingSession(
        [rate_limited] + [_response({"ok": True}) for _ in range(3)], clock, step=80.0
    )
    client = TangleApiClient("https://api.test", session=session)

    result = client._make_request("GET", "/api/test")

    assert result is rate_limited
    assert len(session.calls) == 1
    assert sleeps == []
    # Attempts and the deadline itself both still had room: only the fact that the
    # wait would have crossed the deadline stopped the retry.
    assert clock.now < deadline
    assert deadline - clock.now < 60.0


def test_transient_backoff_that_would_cross_the_deadline_stops_retrying(monkeypatch) -> None:
    # Same rule on the transient-5xx layer: once the doubling backoff no longer
    # fits inside the remaining deadline, the final 5xx surfaces instead of the
    # client sleeping into a send it cannot make.
    clock = SimpleNamespace(now=1_000.0)
    deadline = clock.now + TangleApiClient._MAX_RETRY_ELAPSED_SECONDS
    monkeypatch.setattr("tangle_cli.client.time.monotonic", lambda: clock.now)
    sleeps: list[float] = []
    monkeypatch.setattr("tangle_cli.client.time.sleep", sleeps.append)

    session = _ClockAdvancingSession(
        [_response(status_code=503) for _ in range(20)], clock, step=39.9
    )
    client = TangleApiClient("https://api.test", session=session)

    result = client._make_request("GET", "/api/test")

    assert result.status_code == 503
    # Two backoffs fitted in the window (1s, then 2s); the third would have been
    # 4s with only 0.3s left, so the sequence ends there.
    assert sleeps == [1.0, 2.0]
    assert len(session.calls) == 3
    # The attempt cap was nowhere near reached and the clock had not yet passed
    # the deadline, so neither of those is what stopped it.
    assert len(session.calls) < TangleApiClient._MAX_GET_RETRIES + 1
    assert clock.now < deadline
    assert session.responses, "unused responses prove the queue did not stop the retries"


def test_redirect_hops_are_charged_to_the_shared_send_budget(monkeypatch) -> None:
    # Every physical send counts, including same-origin redirect hops. Without
    # charging them, a 307 in front of each 503 would double the number of
    # requests an outage can provoke (and a full 5-hop chain would multiply it
    # sixfold). Hops are charged to the send pool rather than to the attempt
    # count, so the chain is still followed on every attempt.
    monkeypatch.setattr("tangle_cli.client.time.sleep", lambda _delay: None)

    responses: list[requests.Response] = []
    for _ in range(20):
        responses.append(_redirect("https://api.test/api/test", "/api/moved"))
        responses.append(_response(status_code=503))
    session = _FakeSession(responses)
    client = TangleApiClient("https://api.test", session=session)

    result = client._make_request("GET", "/api/test")

    # Seven logical attempts of two sends each spend the pool exactly, and the
    # real 503 surfaces instead of a budget-exhaustion error.
    assert result.status_code == 503
    assert len(session.calls) == TangleApiClient._MAX_PHYSICAL_SENDS == 14
    assert [call["url"] for call in session.calls] == [
        "https://api.test/api/test",
        "https://api.test/api/moved",
    ] * 7


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("hops", range(TangleApiClient._MAX_REDIRECTS + 1))
def test_legal_redirect_chain_survives_an_auth_refresh(monkeypatch, hops, method) -> None:
    # The client advertises support for chains up to _MAX_REDIRECTS deep, and a
    # 401 refresh replays the whole request. Both together are ordinary healthy
    # traffic -- no outage -- so every depth must still reach the backend. This
    # is why physical sends are pooled separately from logical attempts: charging
    # hops against the attempt count would fail depth 3 and beyond outright.
    monkeypatch.setattr("tangle_cli.client.time.sleep", lambda _delay: None)

    class RefreshingClient(TangleApiClient):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.refreshes = 0

        def _refresh_auth(self) -> None:
            self.refreshes += 1

    ok = _response({"ok": True})
    responses = _redirect_chain(hops, _response(status_code=401))
    responses += _redirect_chain(hops, ok)
    session = _FakeSession(responses)
    client = RefreshingClient("https://api.test", session=session)

    result = client._make_request(
        method, "/api/test", json_data={"a": 1} if method == "POST" else None
    )

    assert result is ok
    # Pre-request refresh plus exactly one for the 401.
    assert client.refreshes == 2
    assert len(session.calls) == 2 * (hops + 1)
    assert not session.responses
    # Two full legal chains is one of the two floors the send pool is sized to.
    assert 2 * (hops + 1) <= TangleApiClient._MAX_PHYSICAL_SENDS


def test_worst_case_redirect_chain_stays_within_the_physical_send_cap(monkeypatch) -> None:
    # The reviewer's amplification concern, at its worst: a full-depth chain in
    # front of every retryable 5xx. The send pool, not the chain length, is what
    # bounds the total.
    monkeypatch.setattr("tangle_cli.client.time.sleep", lambda _delay: None)
    hops = TangleApiClient._MAX_REDIRECTS

    responses: list[requests.Response] = []
    for _ in range(10):
        responses += _redirect_chain(hops, _response(status_code=503))
    session = _FakeSession(responses)
    client = TangleApiClient("https://api.test", session=session)

    result = client._make_request("GET", "/api/test")

    assert len(session.calls) == TangleApiClient._MAX_PHYSICAL_SENDS == 14
    # Far below the attempts x chain-length product an uncharged chain would allow.
    assert len(session.calls) < (TangleApiClient._MAX_GET_RETRIES + 1) * (hops + 1)
    # The pool runs out part-way through the third chain, but a completed 503
    # from the second is still the truthful answer for the caller.
    assert result.status_code == 503
    with pytest.raises(requests.HTTPError):
        result.raise_for_status()


def test_redirect_then_5xx_reports_the_backend_status_not_budget_exhaustion(monkeypatch) -> None:
    # End to end through a public operation: a 307 in front of a 503 must still
    # be reported as HTTP 503. Replacing the completed response with a
    # RetryError would hide the real backend failure from raise_for_status --
    # and RetryError is not an HTTPError, so the 404 fallbacks would not see it
    # either.
    monkeypatch.setattr("tangle_cli.client.time.sleep", lambda _delay: None)

    responses: list[requests.Response] = []
    for _ in range(10):
        responses += _redirect_chain(TangleApiClient._MAX_REDIRECTS, _response(status_code=503))
    session = _FakeSession(responses)
    client = TangleApiClient("https://api.test", session=session)

    with pytest.raises(requests.HTTPError) as exc_info:
        client.pipeline_runs_get("run-1")

    assert exc_info.value.response is not None
    assert exc_info.value.response.status_code == 503


def test_direct_get_5xx_still_stops_at_the_logical_attempt_cap(monkeypatch) -> None:
    # The send pool is deliberately larger than the attempt count so redirect
    # chains fit. It must not become extra retries for a request that never
    # redirects.
    monkeypatch.setattr("tangle_cli.client.time.sleep", lambda _delay: None)
    session = _FakeSession([_response(status_code=503) for _ in range(20)])
    client = TangleApiClient("https://api.test", session=session)

    result = client._make_request("GET", "/api/test")

    assert result.status_code == 503
    assert len(session.calls) == TangleApiClient._MAX_GET_RETRIES + 1 == 7
    assert len(session.calls) < TangleApiClient._MAX_PHYSICAL_SENDS


def test_deadline_hit_mid_first_redirect_chain_raises_a_clean_retry_error(monkeypatch) -> None:
    # Exhaustion inside the very first chain has no completed backend response to
    # fall back on, so the exhaustion error itself is the honest answer. It must
    # stay a requests-family error and must not be an HTTPError, or the 404
    # fallbacks in the public helpers would inspect a response that is not there.
    clock = SimpleNamespace(now=1_000.0)
    deadline = clock.now + TangleApiClient._MAX_RETRY_ELAPSED_SECONDS
    monkeypatch.setattr("tangle_cli.client.time.monotonic", lambda: clock.now)
    monkeypatch.setattr("tangle_cli.client.time.sleep", lambda _delay: None)

    responses = _redirect_chain(TangleApiClient._MAX_REDIRECTS, _response({"ok": True}))
    session = _ClockAdvancingSession(responses, clock, step=50.0)
    client = TangleApiClient("https://api.test", session=session)

    with pytest.raises(requests.exceptions.RetryError, match="Retry budget exhausted"):
        client._make_request("GET", "/api/test")

    # Three hops fitted inside the window; the fourth was refused at the send
    # boundary, before the request went out.
    assert len(session.calls) == 3
    assert clock.now >= deadline
    assert all(t < deadline for t in session.send_times)
    assert not issubclass(requests.exceptions.RetryError, requests.HTTPError)


def test_post_429_keeps_legacy_four_attempt_cap_and_backoff(monkeypatch) -> None:
    # POSTs never enter the replay-on-5xx layer, so they keep the historical
    # four-attempt 429 allowance and its 1/2/4s backoff rather than spending the
    # larger shared GET budget on rate limiting alone.
    sleeps: list[float] = []
    monkeypatch.setattr("tangle_cli.client.time.sleep", sleeps.append)
    session = _FakeSession([_response(status_code=429) for _ in range(10)])
    client = TangleApiClient("https://api.test", session=session)

    result = client._make_request("POST", "/api/test", json_data={"a": 1})

    assert result.status_code == 429
    assert len(session.calls) == TangleApiClient._MAX_RATE_LIMIT_RETRIES + 1 == 4
    assert sleeps == [1.0, 2.0, 4.0]


def test_streamed_get_429_keeps_legacy_four_attempt_cap_and_backoff(monkeypatch) -> None:
    # A streamed GET is not replayable either (its consumer owns stream-open
    # retries), so it gets the same legacy 429 treatment as a POST.
    sleeps: list[float] = []
    monkeypatch.setattr("tangle_cli.client.time.sleep", sleeps.append)
    session = _FakeSession([_response(status_code=429) for _ in range(10)])
    client = TangleApiClient("https://api.test", session=session)

    result = client._make_request("GET", "/api/test", stream=True)

    assert result.status_code == 429
    assert len(session.calls) == TangleApiClient._MAX_RATE_LIMIT_RETRIES + 1 == 4
    assert sleeps == [1.0, 2.0, 4.0]


def test_ordinary_get_429_uses_the_shared_budget_not_the_legacy_cap(monkeypatch) -> None:
    # The counterpart to the two tests above: a replayable GET still gets the
    # full shared budget, so restoring the legacy cap did not narrow it. Its
    # doubling backoff is capped by _MAX_RETRY_AFTER_SECONDS (60s), which 32s
    # never reaches, so the worst-case wall time is 63s over seven sends.
    sleeps: list[float] = []
    monkeypatch.setattr("tangle_cli.client.time.sleep", sleeps.append)
    session = _FakeSession([_response(status_code=429) for _ in range(20)])
    client = TangleApiClient("https://api.test", session=session)

    result = client._make_request("GET", "/api/test")

    assert result.status_code == 429
    assert len(session.calls) == TangleApiClient._MAX_GET_RETRIES + 1 == 7
    assert sleeps == [1.0, 2.0, 4.0, 8.0, 16.0, 32.0]
    assert sum(sleeps) == 63.0
    assert max(sleeps) <= TangleApiClient._MAX_RETRY_AFTER_SECONDS


def test_ordinary_get_5xx_backoff_is_capped_lower_than_the_429_backoff(monkeypatch) -> None:
    # Same seven sends as the 429 case, but the transient layer clamps at
    # _MAX_GET_RETRY_BACKOFF_SECONDS (30s), so the final wait is 30s not 32s and
    # the worst-case wall time is 61s. Both stay inside the 120s deadline.
    sleeps: list[float] = []
    monkeypatch.setattr("tangle_cli.client.time.sleep", sleeps.append)
    session = _FakeSession([_response(status_code=503) for _ in range(20)])
    client = TangleApiClient("https://api.test", session=session)

    result = client._make_request("GET", "/api/test")

    assert result.status_code == 503
    assert len(session.calls) == TangleApiClient._MAX_GET_RETRIES + 1 == 7
    assert sleeps == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0]
    assert sum(sleeps) == 61.0
    assert max(sleeps) == TangleApiClient._MAX_GET_RETRY_BACKOFF_SECONDS
    assert sum(sleeps) < TangleApiClient._MAX_RETRY_ELAPSED_SECONDS


@pytest.mark.parametrize("stream", [False, True], ids=["plain", "streamed"])
def test_intermediate_429_responses_are_closed_but_the_final_one_is_usable(
    monkeypatch, stream: bool
) -> None:
    # A superseded 429 holds a pooled connection for the whole backoff if it is
    # never released, and a streamed one is never read at all. Only the response
    # actually handed back to the caller must stay open.
    closed: list[str] = []

    def tracking_response(status_code: int, marker: str, payload: Any = None) -> requests.Response:
        r = _response(payload, status_code=status_code)
        r.close = lambda: closed.append(marker)  # type: ignore[method-assign]
        return r

    monkeypatch.setattr("tangle_cli.client.time.sleep", lambda _delay: None)
    session = _FakeSession(
        [
            tracking_response(429, "429-1"),
            tracking_response(429, "429-2"),
            tracking_response(200, "final", {"ok": True}),
        ]
    )
    client = TangleApiClient("https://api.test", session=session)

    result = client._make_request("GET", "/api/test", stream=stream)

    assert closed == ["429-1", "429-2"]
    assert result.status_code == 200
    assert result.json() == {"ok": True}


def test_a_returned_429_is_not_closed_when_the_rate_limit_rounds_run_out(monkeypatch) -> None:
    # The counterpart to the test above on the give-up path: the last 429 is the
    # answer, so it must survive for the caller to inspect.
    closed: list[str] = []

    def tracking_response(marker: str) -> requests.Response:
        r = _response({"detail": marker}, status_code=429)
        r.close = lambda: closed.append(marker)  # type: ignore[method-assign]
        return r

    monkeypatch.setattr("tangle_cli.client.time.sleep", lambda _delay: None)
    session = _FakeSession([tracking_response(f"r{i}") for i in range(4)])
    client = TangleApiClient("https://api.test", session=session)

    result = client._make_request("POST", "/api/test", json_data={"a": 1})

    assert closed == ["r0", "r1", "r2"]
    assert result.status_code == 429
    assert result.json() == {"detail": "r3"}


def test_retry_error_names_the_deadline_rather_than_the_send_pool(monkeypatch) -> None:
    # Both exhaustion causes stop a send at the same boundary; the message has to
    # say which one, or a slow-backend incident reads as a redirect-loop bug.
    clock = SimpleNamespace(now=1_000.0)
    monkeypatch.setattr("tangle_cli.client.time.monotonic", lambda: clock.now)
    monkeypatch.setattr("tangle_cli.client.time.sleep", lambda _delay: None)

    responses = _redirect_chain(TangleApiClient._MAX_REDIRECTS, _response({"ok": True}))
    session = _ClockAdvancingSession(responses, clock, step=50.0)
    client = TangleApiClient("https://api.test", session=session)

    with pytest.raises(requests.exceptions.RetryError, match="deadline exceeded"):
        client._make_request("GET", "/api/test")


def test_retry_error_names_the_send_pool_when_the_deadline_is_untouched(monkeypatch) -> None:
    # The other branch of the message, with the clock standing still so only the
    # pool can be the cause. The real pool is sized to outlast any single chain,
    # so it is narrowed here to reach the branch at all.
    monkeypatch.setattr("tangle_cli.client.time.monotonic", lambda: 1_000.0)
    monkeypatch.setattr("tangle_cli.client.time.sleep", lambda _delay: None)
    monkeypatch.setattr(TangleApiClient, "_MAX_PHYSICAL_SENDS", 3)

    responses = _redirect_chain(TangleApiClient._MAX_REDIRECTS, _response({"ok": True}))
    session = _FakeSession(responses)
    client = TangleApiClient("https://api.test", session=session)

    with pytest.raises(requests.exceptions.RetryError, match="send pool exhausted"):
        client._make_request("GET", "/api/test")

    assert len(session.calls) == 3


@pytest.mark.parametrize(
    ("redirect_url", "location", "secrets", "expected"),
    [
        pytest.param(
            "https://api.test/api/test",
            "/download?access_token=tok-SECRET",
            ["tok-SECRET", "access_token"],
            "https://api.test/download",
            id="signed-query",
        ),
        pytest.param(
            "https://api.test/api/test",
            "/blob?X-Amz-Signature=amz-SECRET&X-Amz-Credential=AKIA-SECRET",
            ["amz-SECRET", "AKIA-SECRET", "X-Amz-Signature"],
            "https://api.test/blob",
            id="aws-sigv4-query",
        ),
        pytest.param(
            "https://api.test/api/test",
            "/asset#token=frag-SECRET",
            ["frag-SECRET", "#"],
            "https://api.test/asset",
            id="fragment-credential",
        ),
        pytest.param(
            "https://alice:hunter2@api.test/api/test",
            "https://alice:hunter2@api.test/next",
            ["alice", "hunter2"],
            "https://api.test/next",
            id="userinfo",
        ),
        pytest.param(
            "https://api.test:9x9/api/test",
            "https://api.test:9x9/next?X-Amz-Signature=port-SECRET",
            ["port-SECRET", "X-Amz-Signature"],
            "https://api.test:9x9/next",
            id="malformed-port",
        ),
    ],
)
def test_retry_error_after_redirect_keeps_credentials_out_of_the_message(
    monkeypatch, redirect_url: str, location: str, secrets: list[str], expected: str
) -> None:
    # A same-origin redirect can land on a signed URL, and the exhaustion error
    # flows into CLI output and logs. The destination stays nameable through
    # scheme/host/path, but query, fragment, and userinfo must not survive --
    # and a malformed port must not turn the error itself into a ValueError.
    monkeypatch.setattr("tangle_cli.client.time.sleep", lambda _delay: None)
    monkeypatch.setattr(TangleApiClient, "_MAX_PHYSICAL_SENDS", 1)
    session = _FakeSession([_redirect(redirect_url, location)])
    client = TangleApiClient("https://api.test", session=session)

    with pytest.raises(requests.exceptions.RetryError, match="send pool exhausted") as exc_info:
        client._make_request("GET", "/api/test")

    message = str(exc_info.value)
    for secret in secrets:
        assert secret not in message
    assert expected in message


def test_deadline_retry_error_after_redirect_keeps_credentials_out_of_the_message(
    monkeypatch,
) -> None:
    # Same guarantee on the other exhaustion branch: a backoff-free deadline hit
    # right after the redirect hop must not report the signed query either.
    clock = SimpleNamespace(now=1_000.0)
    monkeypatch.setattr("tangle_cli.client.time.monotonic", lambda: clock.now)
    monkeypatch.setattr("tangle_cli.client.time.sleep", lambda _delay: None)

    responses = [_redirect("https://api.test/api/test", "/blob?X-Amz-Signature=amz-SECRET")]
    session = _ClockAdvancingSession(
        responses, clock, step=TangleApiClient._MAX_RETRY_ELAPSED_SECONDS + 10.0
    )
    client = TangleApiClient("https://api.test", session=session)

    with pytest.raises(requests.exceptions.RetryError, match="deadline exceeded") as exc_info:
        client._make_request("GET", "/api/test")

    message = str(exc_info.value)
    assert "amz-SECRET" not in message
    assert "X-Amz-Signature" not in message
    assert "https://api.test/blob" in message


def test_credential_safe_url_never_raises_on_authorities_urlsplit_rejects() -> None:
    # Error formatting runs while an exhaustion error is already being raised,
    # so an authority the parser rejects must degrade to a placeholder.
    assert TangleApiClient._credential_safe_url("https://[::1/api") == "<unparseable URL>"
    assert TangleApiClient._credential_safe_url("") == "<unparseable URL>"


def test_post_401_then_429_shares_the_budget_across_auth_and_rate_limit(monkeypatch) -> None:
    # Mixed composition on the non-replayable path: the auth refresh restarts the
    # rate-limit rounds, so the shared budget is the only thing bounding the
    # total number of physical POSTs.
    sleeps: list[float] = []
    monkeypatch.setattr("tangle_cli.client.time.sleep", sleeps.append)

    class RefreshingClient(TangleApiClient):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.refreshes = 0

        def _refresh_auth(self) -> None:
            self.refreshes += 1

    session = _FakeSession(
        [_response(status_code=401)] + [_response(status_code=429) for _ in range(10)]
    )
    client = RefreshingClient("https://api.test", session=session)

    result = client._make_request("POST", "/api/test", json_data={"a": 1})

    assert result.status_code == 429
    # The 401 send plus one full legacy 429 sequence, still inside the shared cap.
    assert len(session.calls) == 1 + TangleApiClient._MAX_RATE_LIMIT_RETRIES + 1 == 5
    assert len(session.calls) <= TangleApiClient._MAX_GET_RETRIES + 1
    assert sleeps == [1.0, 2.0, 4.0]
    assert client.refreshes == 2


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
