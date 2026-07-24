from types import SimpleNamespace

import httpx
import pytest

from tangle_cli.api_transport import (
    _MAX_BACKEND_DETAIL_CHARS,
    _redact_headers,
    build_operation_request,
    default_base_url,
    describe_request_error,
    format_http_status_error,
    format_request_error,
    request_operation,
    sanitize_url,
    tangle_verbose_enabled,
)


def _operation(path: str, *, method: str = "GET", has_request_body: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        method=method,
        path=path,
        parameters=[],
        group_name="test",
        command_name="op",
        has_request_body=has_request_body,
    )


@pytest.mark.parametrize(
    "env_name",
    [
        "TANGLE_API_AUTH_HEADER",
        "TANGLE_AUTH_HEADER",
        "TANGLE_API_HEADERS",
        "TANGLE_API_TOKEN",
    ],
)
def test_default_base_url_rejects_ambient_auth_for_implicit_localhost(
    monkeypatch: pytest.MonkeyPatch,
    env_name: str,
) -> None:
    monkeypatch.delenv("TANGLE_API_URL", raising=False)
    monkeypatch.setenv(env_name, "secret")

    with pytest.raises(SystemExit, match="refusing to send credentials to default"):
        default_base_url()


def test_default_base_url_allows_implicit_localhost_without_ambient_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TANGLE_API_URL", raising=False)
    for env_name in (
        "TANGLE_API_AUTH_HEADER",
        "TANGLE_AUTH_HEADER",
        "TANGLE_API_HEADERS",
        "TANGLE_API_TOKEN",
    ):
        monkeypatch.delenv(env_name, raising=False)

    assert default_base_url() == "http://localhost:8000"


def test_default_base_url_allows_explicit_api_url_with_ambient_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TANGLE_API_URL", "https://api.tangle.test")
    monkeypatch.setenv("TANGLE_API_TOKEN", "secret-token")

    assert default_base_url() == "https://api.tangle.test"


def test_build_operation_request_allows_explicit_localhost_with_ambient_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TANGLE_API_URL", raising=False)
    monkeypatch.setenv("TANGLE_API_TOKEN", "secret-token")

    _method, url, headers, _content = build_operation_request(
        _operation("/health"),
        {},
        base_url="http://localhost:8000",
    )

    assert url == "http://localhost:8000/health"
    assert headers["Authorization"] == "Bearer secret-token"


@pytest.mark.parametrize("value", [None, "", "0", "false", "False", "no", "off"])
def test_tangle_verbose_false_values(monkeypatch: pytest.MonkeyPatch, value: str | None) -> None:
    if value is None:
        monkeypatch.delenv("TANGLE_VERBOSE", raising=False)
    else:
        monkeypatch.setenv("TANGLE_VERBOSE", value)

    assert tangle_verbose_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on"])
def test_tangle_verbose_truthy_values(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("TANGLE_VERBOSE", value)

    assert tangle_verbose_enabled() is True


def test_request_operation_does_not_log_bodies_when_verbose_false(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("TANGLE_VERBOSE", "0")

    def fake_request(*args, **kwargs):
        return httpx.Response(
            200,
            json={"id": "run-1", "secret": "response-secret"},
            request=httpx.Request("POST", "https://api.test/api/pipeline_runs/"),
        )

    monkeypatch.setattr("tangle_cli.api_transport.httpx.request", fake_request)

    request_operation(
        _operation("/api/pipeline_runs/", method="POST", has_request_body=True),
        {},
        base_url="https://api.test",
        auth_header="Bearer request-secret",
        body={"name": "demo", "token": "request-token"},
    )

    assert capsys.readouterr().err == ""


def test_redact_headers_matches_auth_segments_without_redacting_author_names() -> None:
    headers = {"X-Gateway-Auth": "secret", "X-Author": "alice"}

    assert _redact_headers(headers) == {"X-Gateway-Auth": "<redacted>", "X-Author": "alice"}


def test_request_operation_verbose_env_logs_redacted_body(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("TANGLE_VERBOSE", "1")

    def fake_request(*args, **kwargs):
        return httpx.Response(
            200,
            json={
                "id": "run-1",
                "secret": "response-secret",
                "signed_url": "https://storage.test/object?X-Goog-Signature=response-signature",
            },
            headers={"X-Api-Key": "response-key"},
            request=httpx.Request("POST", "https://api.test/api/pipeline_runs/"),
        )

    monkeypatch.setattr("tangle_cli.api_transport.httpx.request", fake_request)

    request_operation(
        _operation("/api/pipeline_runs/", method="POST", has_request_body=True),
        {},
        base_url="https://api.test",
        auth_header="Bearer request-secret",
        header_entries=["Cloud-Auth: cloud-secret", "X-Gateway-Auth: gateway-secret"],
        body={"name": "demo", "token": "request-token"},
    )

    logs = capsys.readouterr().err
    assert "[tangle-api] request: POST https://api.test/api/pipeline_runs/" in logs
    assert "request body" in logs
    assert "response body" in logs
    assert "demo" in logs
    assert "run-1" in logs
    assert "request-secret" not in logs
    assert "cloud-secret" not in logs
    assert "gateway-secret" not in logs
    assert "request-token" not in logs
    assert "response-secret" not in logs
    assert "response-key" not in logs
    assert "response-signature" not in logs


def test_request_operation_verbose_env_redacts_opaque_component_text(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("TANGLE_VERBOSE", "1")

    def fake_request(*args, **kwargs):
        return httpx.Response(
            200,
            json={"id": "component-1", "text": "response-yaml-with-secret-token"},
            request=httpx.Request("POST", "https://api.test/api/components/"),
        )

    monkeypatch.setattr("tangle_cli.api_transport.httpx.request", fake_request)

    request_operation(
        _operation("/api/components/", method="POST", has_request_body=True),
        {},
        base_url="https://api.test",
        auth_header="Bearer request-secret",
        body={
            "name": "demo-component",
            "text": "component:\n  env:\n    TOKEN: hard-coded-component-secret\n",
        },
    )

    logs = capsys.readouterr().err
    assert "demo-component" in logs
    assert "<redacted document>" in logs
    assert "hard-coded-component-secret" not in logs
    assert "response-yaml-with-secret-token" not in logs


def test_build_operation_request_rejects_absolute_url_paths() -> None:
    with pytest.raises(ValueError, match="must be relative"):
        build_operation_request(
            _operation("https://attacker.example/collect"),
            {},
            base_url="https://api.tangle.test",
            token="secret-token",
        )


def test_build_operation_request_rejects_network_path_reference() -> None:
    with pytest.raises(ValueError, match="must be relative"):
        build_operation_request(
            _operation("//attacker.example/collect"),
            {},
            base_url="https://api.tangle.test",
            token="secret-token",
        )


def test_build_operation_request_allows_relative_paths() -> None:
    method, url, headers, content = build_operation_request(
        _operation("/api/components/{id}"),
        {},
        base_url="https://api.tangle.test",
        token="secret-token",
    )

    assert method == "GET"
    assert url == "https://api.tangle.test/api/components/{id}"
    assert headers["Authorization"] == "Bearer secret-token"
    assert content is None


def test_sanitize_url_strips_userinfo() -> None:
    sanitized = sanitize_url("https://alice:hunter2@api.tangle.test:8443/api/x?limit=5")

    assert "hunter2" not in sanitized
    assert "alice" not in sanitized
    assert sanitized == "https://<redacted>@api.tangle.test:8443/api/x?limit=5"


@pytest.mark.parametrize(
    "param",
    ["token", "access_token", "api_key", "signature", "X-Amz-Signature", "sig"],
)
def test_sanitize_url_redacts_credential_query_params(param: str) -> None:
    sanitized = sanitize_url(f"https://api.tangle.test/api/x?{param}=SECRETVALUE&limit=5")

    assert "SECRETVALUE" not in sanitized
    assert "<redacted>" in sanitized
    assert "limit=5" in sanitized


def test_sanitize_url_redacts_presigned_url_signature() -> None:
    signed = (
        "https://bucket.s3.amazonaws.com/object?"
        "X-Amz-Credential=AKIA_LEAK&X-Amz-Signature=DEADBEEFSIG&X-Amz-Expires=900"
    )

    sanitized = sanitize_url(signed)

    assert "AKIA_LEAK" not in sanitized
    assert "DEADBEEFSIG" not in sanitized
    assert "bucket.s3.amazonaws.com" in sanitized


def test_sanitize_url_preserves_plain_url() -> None:
    assert sanitize_url("http://api.test/api/pipeline_runs/") == "http://api.test/api/pipeline_runs/"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://[2001:db8::1]/api/x", "https://[2001:db8::1]/api/x"),
        ("https://[2001:db8::1]:8443/api/x", "https://[2001:db8::1]:8443/api/x"),
        (
            "https://alice:hunter2@[2001:db8::1]:8443/api/x",
            "https://<redacted>@[2001:db8::1]:8443/api/x",
        ),
        (
            "https://alice:hunter2@[2001:db8::1]:8443/api/x?token=SECRET&limit=5",
            "https://<redacted>@[2001:db8::1]:8443/api/x?token=<redacted>&limit=5",
        ),
    ],
)
def test_sanitize_url_rebrackets_ipv6_literals(url: str, expected: str) -> None:
    sanitized = sanitize_url(url)

    assert sanitized == expected
    assert "hunter2" not in sanitized
    assert "SECRET" not in sanitized


def test_format_http_status_error_is_one_line_with_status_method_and_url() -> None:
    request = httpx.Request("GET", "https://alice:pw@api.tangle.test/api/x")
    response = httpx.Response(404, text='{"detail": "missing"}', request=request)
    exc = httpx.HTTPStatusError("client error", request=request, response=response)

    message = format_http_status_error(exc)

    assert "\n" not in message
    assert "HTTP 404 Not Found for GET" in message
    assert "pw" not in message
    assert "missing" in message


def test_format_http_status_error_bounds_and_normalizes_detail() -> None:
    request = httpx.Request("POST", "https://api.tangle.test/api/x")
    body = "line one\n\n   line two\t" + "A" * 5000
    response = httpx.Response(500, text=body, request=request)
    exc = httpx.HTTPStatusError("server error", request=request, response=response)

    message = format_http_status_error(exc)

    assert "\n" not in message and "\t" not in message
    assert "line one line two" in message
    assert message.endswith("…")
    assert len(message) < _MAX_BACKEND_DETAIL_CHARS + 200


def test_format_http_status_error_redacts_reflected_json_secrets() -> None:
    request = httpx.Request("POST", "https://api.tangle.test/api/x")
    body = '{"detail": "invalid credential", "credential": "BODYSECRET", "token": "abc123"}'
    response = httpx.Response(401, text=body, request=request)
    exc = httpx.HTTPStatusError("unauthorized", request=request, response=response)

    message = format_http_status_error(exc)

    assert "\n" not in message
    assert "BODYSECRET" not in message
    assert "abc123" not in message
    assert "<redacted>" in message
    # Non-sensitive detail stays visible so the backend message is still useful.
    assert "invalid credential" in message


def test_format_http_status_error_preserves_non_json_detail() -> None:
    request = httpx.Request("POST", "https://api.tangle.test/api/x")
    response = httpx.Response(502, text="upstream unavailable", request=request)
    exc = httpx.HTTPStatusError("bad gateway", request=request, response=response)

    message = format_http_status_error(exc)

    assert message.endswith("upstream unavailable")


def test_format_http_status_error_can_omit_detail() -> None:
    request = httpx.Request("GET", "https://api.tangle.test/openapi.json")
    response = httpx.Response(401, text="secret-token", request=request)
    exc = httpx.HTTPStatusError("unauthorized", request=request, response=response)

    message = format_http_status_error(exc, include_detail=False)

    assert message == "HTTP 401 Unauthorized for GET https://api.tangle.test/openapi.json"
    assert "secret-token" not in message


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (httpx.ConnectError("connection refused"), "connection failed: connection refused"),
        (httpx.ConnectTimeout("timed out"), "connection timed out"),
        (httpx.ReadTimeout("slow"), "read timed out"),
        (httpx.PoolTimeout("busy"), "connection pool timed out"),
        (httpx.ProxyError("bad proxy"), "proxy error: bad proxy"),
        (
            httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] self-signed certificate"),
            "TLS error",
        ),
    ],
)
def test_describe_request_error_is_actionable(exc: httpx.RequestError, expected: str) -> None:
    assert expected in describe_request_error(exc)


def test_format_request_error_is_one_line_and_redacts_url() -> None:
    request = httpx.Request("GET", "https://alice:pw@api.tangle.test/api/x?token=SECRET")
    exc = httpx.ConnectError("connection refused", request=request)

    message = format_request_error(exc)

    assert "\n" not in message
    assert message.startswith("Failed to reach GET ")
    assert "pw" not in message
    assert "SECRET" not in message
    assert "connection refused" in message


@pytest.mark.parametrize(
    ("exc", "secrets"),
    [
        (
            httpx.ProxyError(
                "unable to connect to proxy http://proxyuser:proxypass@proxy.internal:8080"
            ),
            ["proxyuser", "proxypass"],
        ),
        (
            httpx.ConnectError(
                "connection failed while fetching "
                "https://bucket.s3.amazonaws.com/o?X-Amz-Signature=DEADBEEFSIG&X-Amz-Expires=900"
            ),
            ["DEADBEEFSIG"],
        ),
        (
            httpx.ConnectError("refused for user:secretpw@10.0.0.5"),
            ["secretpw"],
        ),
    ],
)
def test_describe_request_error_scrubs_embedded_secrets(
    exc: httpx.RequestError, secrets: list[str]
) -> None:
    message = describe_request_error(exc)

    assert "\n" not in message
    assert "<redacted>" in message
    for secret in secrets:
        assert secret not in message


def test_describe_request_error_preserves_benign_diagnostics() -> None:
    refused = describe_request_error(httpx.ConnectError("[Errno 111] Connection refused"))
    assert "connection failed" in refused
    assert "[Errno 111] Connection refused" in refused

    tls = describe_request_error(
        httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] self-signed certificate")
    )
    assert "TLS error" in tls
    assert "CERTIFICATE_VERIFY_FAILED" in tls


def test_format_request_error_scrubs_secrets_in_exception_text() -> None:
    request = httpx.Request("POST", "https://api.tangle.test/api/x")
    exc = httpx.ProxyError(
        "proxy http://proxyuser:proxypass@proxy.internal:8080 rejected", request=request
    )

    message = format_request_error(exc)

    assert "\n" not in message
    assert "proxyuser" not in message
    assert "proxypass" not in message
    assert message.startswith("Failed to reach POST https://api.tangle.test/api/x:")
