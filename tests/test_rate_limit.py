"""Tests for client-side request pacing (``tangle_cli.rate_limit`` + client integration)."""

from __future__ import annotations

from typing import Any

import pytest
import requests

from tangle_cli import client as client_module
from tangle_cli import rate_limit as rl
from tangle_cli.client import TangleApiClient


class FakeClock:
    """Deterministic clock: ``sleep`` advances time and records the wait."""

    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(round(seconds, 6))
        self.now += seconds


def _limiter(rps: float | None, clock: FakeClock, *, thread: list[int] | None = None, rand: float = 0.5):
    current = thread if thread is not None else [1]
    return rl.RequestRateLimiter(
        rps, clock=clock.clock, sleep=clock.sleep, rand=lambda: rand, thread_id=lambda: current[0]
    )


def test_requests_are_spaced_at_the_configured_rate():
    clock = FakeClock()
    limiter = _limiter(8, clock)

    for _ in range(5):
        limiter.acquire()

    assert clock.sleeps == [0.125, 0.125, 0.125, 0.125]  # first slot is immediate
    assert limiter.max_per_second == 8


def test_idle_time_is_not_banked_into_a_burst():
    clock = FakeClock()
    limiter = _limiter(4, clock)

    limiter.acquire()
    clock.now += 10  # long idle
    limiter.acquire()
    limiter.acquire()

    assert clock.sleeps == [0.25]


@pytest.mark.parametrize("rps", [None, 0, -1])
def test_disabled_pacing_never_waits(rps):
    clock = FakeClock()
    limiter = _limiter(rps, clock)

    for _ in range(10):
        assert limiter.acquire() == 0.0

    assert clock.sleeps == [] and limiter.max_per_second is None


def test_pause_holds_other_threads_with_jitter_but_not_the_pausing_thread():
    clock = FakeClock()
    thread = [1]
    limiter = _limiter(None, clock, thread=thread, rand=0.5)

    limiter.pause(5.0)  # thread 1 got a 429 and sleeps through its own backoff
    assert limiter.acquire() == 0.0  # thread 1 itself is exempt

    thread[0] = 2
    waited = limiter.acquire()  # another worker waits for the pause + jitter

    assert waited == pytest.approx(5.0 + 0.5 * min(rl.PAUSE_JITTER_MAX_SECONDS, rl.PAUSE_JITTER_FRACTION * 5.0))
    thread[0] = 3
    assert limiter.acquire() == 0.0  # the pause is over


def test_pause_combines_with_pacing_and_only_extends():
    clock = FakeClock()
    thread = [1]
    limiter = _limiter(2, clock, thread=thread, rand=0.0)

    limiter.pause(3.0)
    limiter.pause(1.0)  # a shorter pause never shortens the active one
    thread[0] = 2
    first = limiter.acquire()
    second = limiter.acquire()

    assert first == pytest.approx(3.0)
    assert second == pytest.approx(0.5)  # back to the 2 req/s spacing after the pause
    limiter.pause(0)  # no-op
    limiter.pause(-1)


def test_a_pause_that_starts_while_a_caller_sleeps_still_holds_it():
    clock = FakeClock()
    thread = [2]
    limiter = rl.RequestRateLimiter(
        8, clock=clock.clock, rand=lambda: 0.5, thread_id=lambda: thread[0], sleep=None
    )
    paused = []

    def sleep(seconds: float) -> None:
        clock.sleep(seconds)
        if not paused:  # another worker gets a 429 (Retry-After: 10) while this one sleeps
            paused.append(True)
            thread[0] = 1
            limiter.pause(10.0)
            thread[0] = 2

    limiter._sleep = sleep
    limiter.acquire()  # slot at t=100
    waited = limiter.acquire()  # sleeps 0.125 for its slot, then must honour the new pause

    # 0.125 for the slot, then 10s pause + jitter (min(2, 0.2*10) * 0.5 = 1.0).
    assert waited == pytest.approx(0.125 + 11.0)
    assert clock.now == pytest.approx(100.0 + 11.125)


def test_a_lower_live_rate_applies_to_the_very_next_request():
    clock = FakeClock()
    limiter = _limiter(8, clock)

    limiter.acquire()
    limiter.set_rate(4)  # caller reacts to pushback by slowing down
    limiter.acquire()
    limiter.set_rate(None)
    limiter.acquire()

    assert clock.sleeps == [0.25]  # new 4 req/s spacing, then no pacing at all


def test_a_slot_beyond_the_deadline_is_refused_without_sleeping():
    clock = FakeClock()
    thread = [1]
    limiter = _limiter(None, clock, thread=thread, rand=0.0)

    limiter.pause(60.0)  # another worker got a 429 with Retry-After: 60
    thread[0] = 2

    assert limiter.acquire(max_wait=30.0) is None
    assert clock.sleeps == []
    assert limiter.acquire(max_wait=90.0) == pytest.approx(60.0)


def test_set_rate_keeps_an_active_pause():
    clock = FakeClock()
    thread = [1]
    limiter = _limiter(8, clock, thread=thread, rand=0.0)

    limiter.pause(4.0)
    limiter.set_rate(2)
    thread[0] = 2

    assert limiter.acquire() == pytest.approx(4.0)
    assert limiter.max_per_second == 2


class _Session:
    def __init__(self, responses: list[requests.Response]) -> None:
        self.responses = responses
        self.calls = 0

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        self.calls += 1
        return self.responses.pop(0)


def _response(status: int, headers: dict[str, str] | None = None) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response._content = b"{}"
    response.headers.update(headers or {})
    return response


def _client(responses: list[requests.Response], **kwargs: Any) -> TangleApiClient:
    return TangleApiClient("https://api.test", session=_Session(responses), **kwargs)


def test_client_default_and_override():
    assert TangleApiClient.DEFAULT_MAX_REQUESTS_PER_SECOND == 8.0  # CPython default
    assert _client([])._request_rate_limiter().max_per_second == 8.0
    assert _client([], max_requests_per_second=None)._request_rate_limiter().max_per_second is None

    client = _client([], max_requests_per_second=2)
    first = client._request_rate_limiter()
    assert first.max_per_second == 2 and client._request_rate_limiter() is first  # one shared limiter
    first.pause(30.0)
    client.max_requests_per_second = 5
    updated = client._request_rate_limiter()
    assert updated is first and updated.max_per_second == 5  # updated in place
    assert updated._paused_until > 0  # the active pause survives the change


def test_every_physical_send_acquires_a_slot(monkeypatch):
    monkeypatch.setattr("tangle_cli.client.time.sleep", lambda _seconds: None)
    client = _client([_response(503), _response(200)], max_requests_per_second=None)
    acquired: list[str] = []
    limiter = client._request_rate_limiter()
    monkeypatch.setattr(limiter, "acquire", lambda **_: acquired.append("slot") or 0.0)

    response = client._make_request("GET", "/api/x")

    assert response.status_code == 200
    assert acquired == ["slot", "slot"]  # the retry is paced too


def test_limiter_wait_happens_before_the_send_budget_deadline_check(monkeypatch):
    """A send delayed by pacing past the retry deadline is refused, not sent late."""

    client = _client([_response(200)], max_requests_per_second=None)
    events: list[str] = []
    limiter = client._request_rate_limiter()
    monkeypatch.setattr(limiter, "acquire", lambda **_: events.append("acquire") or 0.0)
    real_budget = client_module._RetryBudget

    class RecordingBudget(real_budget):  # type: ignore[misc, valid-type]
        def try_consume_send(self) -> bool:
            events.append("budget")
            return super().try_consume_send()

    monkeypatch.setattr(client_module, "_RetryBudget", RecordingBudget)

    assert client._make_request("GET", "/api/x").status_code == 200
    assert events == ["acquire", "budget"]


def _slow_limiter(monkeypatch, client: TangleApiClient, seconds: float) -> list[float]:
    """Fake clock + a limiter that holds every slot for *seconds*."""

    monkeypatch.setattr(client, "_MAX_RETRY_ELAPSED_SECONDS", 5.0)
    monkeypatch.setattr("tangle_cli.client.time.sleep", lambda _seconds: None)
    now = [1000.0]
    monkeypatch.setattr(client_module.time, "monotonic", lambda: now[0])

    def slow_acquire(max_wait: float | None = None) -> float | None:
        now[0] += seconds
        return seconds

    monkeypatch.setattr(client._request_rate_limiter(), "acquire", slow_acquire)
    return now


def test_queueing_for_the_first_slot_does_not_spend_the_retry_deadline(monkeypatch):
    """A worker that waits behind others for its first send is not retrying."""

    session = _Session([_response(200)])
    client = TangleApiClient("https://api.test", session=session, max_requests_per_second=None)
    _slow_limiter(monkeypatch, client, 10.0)  # first slot: 10s, past the 5s retry deadline

    assert client._make_request("GET", "/api/x").status_code == 200
    assert session.calls == 1


def test_a_retry_whose_pacing_wait_outlasts_the_deadline_is_not_sent(monkeypatch):
    session = _Session([_response(503), _response(200)])
    client = TangleApiClient("https://api.test", session=session, max_requests_per_second=None)
    _slow_limiter(monkeypatch, client, 10.0)

    response = client._make_request("GET", "/api/x")

    assert response.status_code == 503  # the retry was refused at the deadline, not sent late
    assert session.calls == 1


def test_auth_refresh_and_header_snapshots_are_serialized(monkeypatch):
    events: list[str] = []

    class RecordingLock:
        def __enter__(self):
            events.append("enter")

        def __exit__(self, *exc):
            events.append("exit")

    client = _client([_response(200)], max_requests_per_second=None)
    client.__dict__["_state_lock"] = RecordingLock()
    monkeypatch.setattr(client, "_refresh_auth", lambda: events.append("refresh"))
    real_headers = client._headers
    monkeypatch.setattr(client, "_headers", lambda extra=None: events.append("headers") or real_headers(extra))

    client._make_request("GET", "/api/x")

    assert events == ["enter", "refresh", "exit", "enter", "headers", "exit"]


def test_first_slot_is_taken_after_the_auth_refresh(monkeypatch):
    """Workers released together from a slow, serialized refresh are still paced."""

    events: list[str] = []
    client = _client([_response(200)], max_requests_per_second=None)
    monkeypatch.setattr(client, "_refresh_auth", lambda: events.append("refresh"))
    monkeypatch.setattr(client._request_rate_limiter(), "acquire", lambda **_: events.append("acquire") or 0.0)
    real_request = client.session.request
    monkeypatch.setattr(client.session, "request", lambda *a, **k: events.append("send") or real_request(*a, **k))

    client._make_request("GET", "/api/x")

    assert events == ["refresh", "acquire", "send"]


def test_client_state_lock_is_reentrant_and_shared():
    client = _client([])
    lock = client._client_state_lock()

    assert client._client_state_lock() is lock
    with lock, lock:  # a refresh that issues requests through the client re-enters it
        pass


def test_a_retry_slot_beyond_the_deadline_returns_the_last_answer_without_waiting(monkeypatch):
    session = _Session([_response(503), _response(200)])
    client = TangleApiClient("https://api.test", session=session, max_requests_per_second=None)
    monkeypatch.setattr("tangle_cli.client.time.sleep", lambda _seconds: None)
    limiter = client._request_rate_limiter()
    seen: list[float | None] = []

    def acquire(max_wait: float | None = None) -> float | None:
        seen.append(max_wait)
        return None if max_wait is not None else 0.0  # the retry's slot is past the deadline

    monkeypatch.setattr(limiter, "acquire", acquire)

    response = client._make_request("GET", "/api/x")

    assert response.status_code == 503 and session.calls == 1
    assert seen[0] is None and seen[1] is not None  # first slot unbounded, retry slot bounded


def test_streamed_open_honours_503_retry_after_and_pauses(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("tangle_cli.client.time.sleep", sleeps.append)

    class StreamSession(_Session):
        def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
            response = super().request(method, url, **kwargs)
            response._content_consumed = True  # buffered fake: close() has no raw stream to release
            return response

    busy = _response(503, {"Retry-After": "12"})
    ok = _response(200)
    client = TangleApiClient("https://api.test", session=StreamSession([busy, ok]), max_requests_per_second=None)
    pauses: list[float] = []
    monkeypatch.setattr(client._request_rate_limiter(), "pause", pauses.append)
    monkeypatch.setattr(client, "_make_stream_body_idle_unbounded", lambda _response: None)

    assert client.stream_execution_container_log("exec-1").status_code == 200
    assert sleeps == [12.0] and pauses == [12.0]


def test_429_pauses_the_shared_limiter_for_the_retry_after(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("tangle_cli.client.time.sleep", sleeps.append)
    client = _client([_response(429, {"Retry-After": "7"}), _response(200)], max_requests_per_second=None)
    pauses: list[float] = []
    monkeypatch.setattr(client._request_rate_limiter(), "pause", pauses.append)

    assert client._make_request("GET", "/api/x").status_code == 200
    assert pauses == [7.0]
    assert sleeps == [7.0]  # the retrying request's own wait is unchanged


def test_503_honours_retry_after_and_pauses(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("tangle_cli.client.time.sleep", sleeps.append)
    client = _client([_response(503, {"Retry-After": "4"}), _response(200)], max_requests_per_second=None)
    pauses: list[float] = []
    monkeypatch.setattr(client._request_rate_limiter(), "pause", pauses.append)

    assert client._make_request("GET", "/api/x").status_code == 200
    assert pauses == [4.0] and sleeps == [4.0]


def test_other_5xx_keep_their_backoff_without_pausing(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("tangle_cli.client.time.sleep", sleeps.append)
    client = _client([_response(502, {"Retry-After": "9"}), _response(200)], max_requests_per_second=None)
    pauses: list[float] = []
    monkeypatch.setattr(client._request_rate_limiter(), "pause", pauses.append)

    assert client._make_request("GET", "/api/x").status_code == 200
    assert pauses == [] and sleeps == [1.0]
