"""Transport-failure handling for the static requests client and CLI boundary."""

from __future__ import annotations

import socket
from typing import Annotated, Any

import pytest
import requests
from cyclopts import App, Parameter

from tangle_cli import cli
from tangle_cli.api_transport import (
    format_transport_error,
    sanitize_destination,
    transport_error_reason,
)
from tangle_cli.client import TangleApiClient, TangleApiTransportError


class RaisingSession:
    """A requests-like session whose every request raises a transport error."""

    def __init__(self, exc: BaseException) -> None:
        self.exc = exc
        self.calls = 0

    def request(self, *args: Any, **kwargs: Any) -> requests.Response:
        self.calls += 1
        raise self.exc


def _client(exc: BaseException) -> TangleApiClient:
    return TangleApiClient("https://api.test", session=RaisingSession(exc))


def _prepared(method: str | None = "GET", url: str = "https://api.test/api/x") -> requests.PreparedRequest:
    request = requests.PreparedRequest()
    request.method = method
    request.url = url
    return request


# --- URL sanitization -------------------------------------------------------


def test_sanitize_destination_strips_userinfo_and_query() -> None:
    sanitized = sanitize_destination(
        "https://user:pass@proxy.internal:8080/api/x?token=SEKRET&sig=abc#frag"
    )
    assert sanitized == "https://proxy.internal:8080/api/x"
    assert "pass" not in sanitized
    assert "SEKRET" not in sanitized


def test_sanitize_destination_drops_signed_url_query() -> None:
    sanitized = sanitize_destination(
        "https://bucket.example.com/artifact?X-Amz-Signature=deadbeef&X-Amz-Credential=k"
    )
    assert sanitized == "https://bucket.example.com/artifact"


def test_sanitize_destination_bare_path_drops_query() -> None:
    assert sanitize_destination("/api/pipeline_runs?token=abc") == "/api/pipeline_runs"


def test_sanitize_destination_rebrackets_ipv6_host() -> None:
    # ``hostname`` drops the brackets; without restoring them the origin renders as
    # an ambiguous ``::1:8080`` and stops being a valid URL.
    assert (
        sanitize_destination("https://user:pw@[::1]:8080/api/x?token=SEKRET")
        == "https://[::1]:8080/api/x"
    )
    assert sanitize_destination("http://[2001:db8::1]/api/y") == "http://[2001:db8::1]/api/y"


@pytest.mark.parametrize("value", [None, ""])
def test_sanitize_destination_handles_empty(value: str | None) -> None:
    assert sanitize_destination(value) is None


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com:bad/api/x?token=SEKRET",
        "http://user:pw@example.com:99999/api/x",
        "http://[::1/api/x?token=SEKRET",
        "http://user:pw@[::1/api/y",
    ],
)
def test_sanitize_destination_falls_back_on_malformed_authority(url: str) -> None:
    # ``hostname``/``port`` are parsed lazily and raise ``ValueError`` for a
    # non-numeric port or a malformed IPv6 authority; the helper must swallow that
    # and return a safe fallback rather than letting it escape past the boundary.
    result = sanitize_destination(url)
    assert result is None
    assert "SEKRET" not in (result or "")
    assert "pw" not in (result or "")


# --- reason classification --------------------------------------------------


@pytest.mark.parametrize(
    ("exc", "reason"),
    [
        (requests.exceptions.SSLError("x"), "TLS verification failed"),
        (requests.exceptions.ProxyError("x"), "proxy connection failed"),
        (requests.exceptions.ConnectTimeout("x"), "connection timed out"),
        (requests.exceptions.ReadTimeout("x"), "read timed out"),
        (requests.exceptions.Timeout("x"), "request timed out"),
        (requests.exceptions.ChunkedEncodingError("x"), "connection closed mid-response"),
        (requests.exceptions.ConnectionError("x"), "could not connect"),
        (requests.exceptions.RequestException("x"), "request failed"),
    ],
)
def test_transport_error_reason(exc: BaseException, reason: str) -> None:
    assert transport_error_reason(exc) == reason


def test_format_transport_error_uses_request_metadata() -> None:
    exc = requests.exceptions.ConnectionError("boom", request=_prepared("POST"))
    assert format_transport_error(exc) == (
        "Could not reach Tangle API (POST https://api.test/api/x): could not connect"
    )


def test_format_transport_error_without_destination() -> None:
    assert (
        format_transport_error(requests.exceptions.Timeout("slow"))
        == "Could not reach Tangle API: request timed out"
    )


def test_format_transport_error_never_echoes_raw_text() -> None:
    # A raw requests message can embed the request path with query secrets.
    exc = requests.exceptions.ConnectionError(
        "HTTPConnectionPool: Max retries exceeded with url: /x?token=SEKRET",
        request=_prepared(url="https://api.test/x?token=SEKRET"),
    )
    message = format_transport_error(exc)
    assert "SEKRET" not in message
    assert "token" not in message


# --- client boundary --------------------------------------------------------


def test_client_wraps_connection_error_as_domain_error() -> None:
    cause = requests.exceptions.ConnectionError("refused")
    client = _client(cause)
    with pytest.raises(TangleApiTransportError) as excinfo:
        client.pipeline_runs_get("run-1")
    message = str(excinfo.value)
    assert message == (
        "Could not reach Tangle API (GET https://api.test/api/pipeline_runs/run-1): "
        "could not connect"
    )
    assert "Traceback" not in message
    # Cause preserved for programmatic hooks.
    assert excinfo.value.__cause__ is cause


def test_client_wraps_timeout() -> None:
    client = _client(requests.exceptions.ReadTimeout("read timed out"))
    with pytest.raises(TangleApiTransportError) as excinfo:
        client.pipeline_runs_get("run-1")
    assert str(excinfo.value).endswith("read timed out")


def test_client_wraps_ssl_error() -> None:
    client = _client(requests.exceptions.SSLError("certificate verify failed"))
    with pytest.raises(TangleApiTransportError) as excinfo:
        client.pipeline_runs_get("run-1")
    assert "TLS verification failed" in str(excinfo.value)


def test_client_wraps_chunked_encoding_error() -> None:
    client = _client(requests.exceptions.ChunkedEncodingError("peer closed"))
    with pytest.raises(TangleApiTransportError) as excinfo:
        client.pipeline_runs_get("run-1")
    assert "connection closed mid-response" in str(excinfo.value)


def test_client_proxy_error_does_not_leak_credentials() -> None:
    # Proxy credentials can appear in the destination URL; they must not surface.
    request = _prepared(url="https://user:s3cr3t@api.test/api/pipeline_runs/run-1")
    cause = requests.exceptions.ProxyError("Cannot connect to proxy", request=request)
    client = _client(cause)
    with pytest.raises(TangleApiTransportError) as excinfo:
        client.pipeline_runs_get("run-1")
    message = str(excinfo.value)
    assert "s3cr3t" not in message
    assert "user:" not in message
    assert "proxy connection failed" in message


def test_client_does_not_intercept_errors_carrying_a_response() -> None:
    response = requests.Response()
    response.status_code = 500
    cause = requests.exceptions.RequestException("server error", response=response)
    client = _client(cause)
    with pytest.raises(requests.exceptions.RequestException) as excinfo:
        client.pipeline_runs_get("run-1")
    # Re-raised unchanged, not wrapped, so status handling stays intact.
    assert excinfo.value is cause
    assert not isinstance(excinfo.value, TangleApiTransportError)


def test_client_does_not_swallow_programmer_errors() -> None:
    client = _client(ValueError("bad code"))
    with pytest.raises(ValueError):
        client.pipeline_runs_get("run-1")


def test_client_real_refused_port() -> None:
    # No mocking: a genuinely closed localhost port must yield a clean domain error.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    client = TangleApiClient(f"http://127.0.0.1:{port}", timeout=2.0)
    with pytest.raises(TangleApiTransportError) as excinfo:
        client.pipeline_runs_get("run-1")
    message = str(excinfo.value)
    assert message.startswith(
        f"Could not reach Tangle API (GET http://127.0.0.1:{port}/api/pipeline_runs/run-1)"
    )
    assert "Traceback" not in message


@pytest.mark.parametrize(
    "base_url",
    [
        "http://example.com:bad",
        "http://user:s3cr3t@example.com:bad",
        "http://[::1",
        "http://user:s3cr3t@[::1",
    ],
)
def test_client_wraps_malformed_authority_as_domain_error(base_url: str) -> None:
    # A non-numeric port surfaces from the request layer while an unterminated IPv6
    # authority raises during URL construction; both must chain through the clean
    # domain error rather than escaping as a bare ``ValueError``, and neither may
    # leak embedded credentials.
    client = TangleApiClient(base_url)
    with pytest.raises(TangleApiTransportError) as excinfo:
        client.pipeline_runs_get("run-1")
    message = str(excinfo.value)
    assert message.startswith("Could not reach Tangle API")
    assert "s3cr3t" not in message
    assert "Traceback" not in message


# --- wrapping boundary -------------------------------------------------------
#
# ``_make_request`` and every retry/rate-limit/redirect layer beneath it re-raise
# the original ``requests`` exception subtypes; conversion to a clean
# TangleApiTransportError happens only at the ``_send_request`` public boundary.
# Keeping the low-level method raw is what lets a transient-retry layer inserted
# below ``_make_request`` classify and retry connect/timeout failures. These tests
# pin that split so it cannot silently regress back into an inner layer.


def test_inner_request_strategy_reraises_raw_transport_exception() -> None:
    # The redirect layer that issues session.request must let the original requests
    # exception through unconverted, so an enclosing retry layer can classify it.
    cause = requests.exceptions.ConnectionError("refused")
    client = _client(cause)
    with pytest.raises(requests.exceptions.ConnectionError) as excinfo:
        client._request_with_same_origin_redirects(
            "GET",
            "https://api.test/api/x",
            params=None,
            json_data=None,
            extra_headers=None,
            timeout=1.0,
            request_kwargs={},
        )
    assert excinfo.value is cause
    assert not isinstance(excinfo.value, TangleApiTransportError)


def test_make_request_surfaces_raw_transport_exception() -> None:
    # _make_request stays raw so a transient-retry layer wrapping it sees the
    # original subtype; only _send_request converts. This guards the composition
    # with a later retry change that retries connect/timeout failures.
    cause = requests.exceptions.ConnectionError("refused")
    client = _client(cause)
    with pytest.raises(requests.exceptions.ConnectionError) as excinfo:
        client._make_request("GET", "/api/x")
    assert excinfo.value is cause
    assert not isinstance(excinfo.value, TangleApiTransportError)


def test_send_request_converts_at_public_boundary() -> None:
    cause = requests.exceptions.ConnectionError("refused")
    client = _client(cause)
    with pytest.raises(TangleApiTransportError) as excinfo:
        client._send_request("GET", "/api/pipeline_runs/run-1")
    assert str(excinfo.value) == (
        "Could not reach Tangle API (GET https://api.test/api/pipeline_runs/run-1): "
        "could not connect"
    )
    assert excinfo.value.__cause__ is cause
    assert "Traceback" not in str(excinfo.value)


def test_send_request_preserves_ssl_reason() -> None:
    cause = requests.exceptions.SSLError("certificate verify failed")
    client = _client(cause)
    with pytest.raises(TangleApiTransportError) as excinfo:
        client._send_request("GET", "/api/x")
    assert "TLS verification failed" in str(excinfo.value)
    assert excinfo.value.__cause__ is cause


def test_send_request_propagates_http_error_with_response_unchanged() -> None:
    # Status errors carry a response and stay the caller's responsibility; the
    # boundary must not swallow them into a transport error.
    response = requests.Response()
    response.status_code = 502
    cause = requests.exceptions.HTTPError("bad gateway", response=response)
    client = _client(cause)
    with pytest.raises(requests.exceptions.HTTPError) as excinfo:
        client._send_request("GET", "/api/x")
    assert excinfo.value is cause
    assert not isinstance(excinfo.value, TangleApiTransportError)


# --- CLI boundary -----------------------------------------------------------


def _app_raising(exc: BaseException) -> App:
    app = App(name="probe")

    @app.command(name="call")
    def _call() -> None:
        raise exc

    # ``run`` enters through ``app.meta``; a passthrough launcher mirrors the real
    # root app so the fake dispatches identically. A sibling branch may enrich this
    # launcher with global options, but the runner keeps routing through it.
    @app.meta.default
    def _launcher(*tokens: Annotated[str, Parameter(allow_leading_hyphen=True)]) -> None:
        app(tokens)

    return app


def test_run_renders_domain_error_one_line(monkeypatch, capsys) -> None:
    exc = TangleApiTransportError(
        "Could not reach Tangle API (GET https://api.test/api/x): could not connect",
        request=_prepared(),
    )
    monkeypatch.setattr(cli, "build_app", lambda: _app_raising(exc))

    exit_code = cli.run(["call"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.err.strip() == (
        "Could not reach Tangle API (GET https://api.test/api/x): could not connect"
    )
    assert "Traceback" not in captured.err


def test_run_formats_raw_requests_exception_safety_net(monkeypatch, capsys) -> None:
    # A raw requests error that bypassed the client is formatted (and redacted) here.
    exc = requests.exceptions.ConnectionError(
        "boom", request=_prepared(url="https://api.test/x?token=SEKRET")
    )
    monkeypatch.setattr(cli, "build_app", lambda: _app_raising(exc))

    exit_code = cli.run(["call"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.err.strip() == (
        "Could not reach Tangle API (GET https://api.test/x): could not connect"
    )
    assert "SEKRET" not in captured.err


def test_run_reraises_errors_with_response(monkeypatch) -> None:
    response = requests.Response()
    response.status_code = 500
    exc = requests.exceptions.HTTPError("server error", response=response)
    monkeypatch.setattr(cli, "build_app", lambda: _app_raising(exc))

    with pytest.raises(requests.exceptions.HTTPError):
        cli.run(["call"])


def test_run_propagates_normal_exit(monkeypatch) -> None:
    app = App(name="probe")

    @app.command(name="ok")
    def _ok() -> None:
        return None

    @app.meta.default
    def _launcher(*tokens: Annotated[str, Parameter(allow_leading_hyphen=True)]) -> None:
        app(tokens)

    monkeypatch.setattr(cli, "build_app", lambda: app)

    with pytest.raises(SystemExit) as excinfo:
        cli.run(["ok"])
    assert excinfo.value.code == 0


def test_run_dispatches_through_meta_app(monkeypatch) -> None:
    # ``run`` must enter via ``app.meta`` so a sibling branch that installs global
    # root options on ``app.meta.default`` (e.g. TLS flags applied before dynamic
    # schema discovery) stays on the dispatch path. A launcher that is bypassed
    # would silently drop those options, so pin that the launcher actually runs.
    app = App(name="probe")
    seen: list[str] = []

    @app.command(name="ok")
    def _ok() -> None:
        return None

    @app.meta.default
    def _launcher(*tokens: Annotated[str, Parameter(allow_leading_hyphen=True)]) -> None:
        seen.append("launcher")
        app(tokens)

    monkeypatch.setattr(cli, "build_app", lambda: app)

    with pytest.raises(SystemExit):
        cli.run(["ok"])
    assert seen == ["launcher"]


def test_run_composes_root_option_with_transport_failure(monkeypatch, capsys) -> None:
    # Combined integration: a global root option parsed by the meta launcher (the
    # slot #43 uses for ``--ca-bundle`` / ``--no-verify-tls``) is applied before the
    # command runs, and a static-client transport failure raised by that command
    # still renders as one clean stderr line with a nonzero exit. This proves the
    # two boundaries compose without this module importing the TLS feature.
    applied: dict[str, Any] = {}
    app = App(name="probe")

    @app.command(name="call")
    def _call() -> None:
        raise TangleApiTransportError(
            "Could not reach Tangle API (GET https://api.test/api/x): could not connect",
            request=_prepared(),
        )

    @app.meta.default
    def _launcher(
        *tokens: Annotated[str, Parameter(allow_leading_hyphen=True)],
        strict: bool = False,
    ) -> None:
        applied["strict"] = strict
        app(tokens)

    monkeypatch.setattr(cli, "build_app", lambda: app)

    exit_code = cli.run(["--strict", "call"])

    captured = capsys.readouterr()
    assert applied["strict"] is True
    assert exit_code == 1
    assert captured.err.strip() == (
        "Could not reach Tangle API (GET https://api.test/api/x): could not connect"
    )
    assert "Traceback" not in captured.err


def test_run_does_not_swallow_programmer_errors(monkeypatch) -> None:
    monkeypatch.setattr(cli, "build_app", lambda: _app_raising(ValueError("bug")))

    with pytest.raises(ValueError):
        cli.run(["call"])
