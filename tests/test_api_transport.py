import json
import time
import urllib.parse
from types import SimpleNamespace

import httpx
import pytest

from tangle_cli.api_transport import (
    _MAX_BACKEND_DETAIL_CHARS,
    _MAX_JSON_DEPTH,
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


@pytest.mark.parametrize(
    "url",
    [
        # ``urlsplit`` defers authority validation until ``hostname``/``port`` is
        # read, so these raise a live ValueError only inside ``sanitize_url``.
        # The authority is dropped wholesale; the path stays diagnostic.
        "https://user:pw@api.test:99999/x",
        "https://api.test:99999/x",
        "https://api.test:bad/x",
        "https://api.test:-1/x",
        "https://[::1]:nope/x",
    ],
)
def test_sanitize_url_fails_closed_on_unparsable_port(url: str) -> None:
    sanitized = sanitize_url(url)

    assert sanitized == "https://<redacted>/x"
    assert "pw" not in sanitized


def test_sanitize_url_fails_closed_on_malformed_ipv6() -> None:
    assert sanitize_url("https://[oops/x?token=SECRETVALUE") == "<redacted>"


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


def _status_error(body: str | bytes, *, status: int = 401) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://api.tangle.test/api/x")
    kwargs: dict[str, object] = {"request": request}
    if isinstance(body, bytes):
        kwargs["content"] = body
    else:
        kwargs["text"] = body
    response = httpx.Response(status, **kwargs)  # type: ignore[arg-type]
    return httpx.HTTPStatusError("error", request=request, response=response)


@pytest.mark.parametrize(
    ("body", "leaked", "expected"),
    [
        ("token=sk-live-0123456789ABCDEF&foo=bar", "sk-live-0123456789ABCDEF", "token=<redacted>"),
        ("password: hunter2secretvalue", "hunter2secretvalue", "password: <redacted>"),
        (
            "<html><body>api_key=AKIA0123456789ABCD failed</body></html>",
            "AKIA0123456789ABCD",
            "api_key=<redacted>",
        ),
        ("credential=BODYSECRET&foo=bar", "BODYSECRET", "credential=<redacted>"),
        ("X-Amz-Signature=DEADBEEF0123456789 expired", "DEADBEEF0123456789", "X-Amz-Signature=<redacted>"),
        ("oauth_token=OAUTH0123456&keep=1", "OAUTH0123456", "oauth_token=<redacted>"),
        ("Signature: PLAINSIG12345", "PLAINSIG12345", "Signature: <redacted>"),
        ("awsaccesskeyid=AKIAX0123&googleaccessid=GOOGLE0123", "AKIAX0123", "awsaccesskeyid=<redacted>"),
    ],
)
def test_format_http_status_error_redacts_secrets_in_non_json_body(
    body: str, leaked: str, expected: str
) -> None:
    # Form, plain-text, and HTML bodies bypass the structured JSON path, so the
    # free-text assignment fallback is what keeps a reflected secret off stderr.
    message = format_http_status_error(_status_error(body))

    assert leaked not in message
    assert expected in message


@pytest.mark.parametrize(
    ("body", "leaked"),
    [
        # Truncated/unparseable JSON never reaches the structured redactor, so the
        # quoted assignments must still be scrubbed by the text fallback.
        ('{"detail": "bad", "token": "abc123", ', "abc123"),
        ('{"password": "secret"', "secret"),
        ('{"a": {"api_key": "AKIA0123"} ', "AKIA0123"),
    ],
)
def test_format_http_status_error_redacts_secrets_in_malformed_json_body(
    body: str, leaked: str
) -> None:
    message = format_http_status_error(_status_error(body))

    assert leaked not in message
    assert "<redacted>" in message


def test_format_http_status_error_redacts_secrets_in_undecodable_body() -> None:
    # Invalid UTF-8 is decoded with replacement rather than raising; the surviving
    # text must still be scrubbed.
    message = format_http_status_error(_status_error(b"\xff\xfetoken=SECRET0123456789xyz"))

    assert "SECRET0123456789xyz" not in message
    assert "token=<redacted>" in message


@pytest.mark.parametrize(
    ("body", "leaked"),
    [
        # Secret past the bound: truncation must not be what hides it.
        ("A" * (_MAX_BACKEND_DETAIL_CHARS + 200) + " token=TAILSECRET0123456789", "TAILSECRET0123456789"),
        # Secret before the bound in an oversized body: redaction still applies.
        ("token=HEADSECRET0123456789 " + "B" * (_MAX_BACKEND_DETAIL_CHARS + 200), "HEADSECRET0123456789"),
    ],
)
def test_format_http_status_error_redacts_before_truncation(body: str, leaked: str) -> None:
    message = format_http_status_error(_status_error(body, status=500))

    assert leaked not in message
    assert len(message) < _MAX_BACKEND_DETAIL_CHARS + 200


def test_format_http_status_error_preserves_non_sensitive_non_json_detail() -> None:
    # Sensitive words that are not the name of an assignment, and assignments
    # whose name is not sensitive, stay intact so backend diagnostics stay useful.
    message = format_http_status_error(
        _status_error(
            "Invalid credentials supplied; authentication required;"
            " status=failed detail=useful page=2 attempt=3/5",
            status=502,
        )
    )

    assert message.endswith(
        ": Invalid credentials supplied; authentication required;"
        " status=failed detail=useful page=2 attempt=3/5"
    )


def test_sensitive_field_name_redacts_regardless_of_how_the_value_looks() -> None:
    # A short, purely alphabetic value is still a credential -- ``password:
    # swordfish`` must not survive on the grounds that it reads like a word. The
    # field name decides, so the surrounding prose is the only thing preserved.
    message = format_http_status_error(
        _status_error("rejected password: swordfish for user alice", status=403)
    )

    assert "swordfish" not in message
    assert message.endswith(": rejected password: <redacted> for user alice")


@pytest.mark.parametrize(
    ("body", "leaked", "expected"),
    [
        # Affixed credential names: the sensitive word is welded to a prefix or
        # suffix, so nothing sits on a word boundary for an alternation to catch.
        ("access_token=ghp_AAAABBBBCCCCDDDD", "ghp_AAAABBBBCCCCDDDD", "access_token=<redacted>"),
        ("refresh_token=rt_zzzzzzzz9999", "rt_zzzzzzzz9999", "refresh_token=<redacted>"),
        ("client_secret=cs_abcdefgh12345678", "cs_abcdefgh12345678", "client_secret=<redacted>"),
        ("id_token=eyJhbGciOiJIUzI1NiJ9.e30.x", "eyJhbGciOiJIUzI1NiJ9.e30.x", "id_token=<redacted>"),
        ("session_token=st_112233445566", "st_112233445566", "session_token=<redacted>"),
        ("api_secret=sk_9988776655", "sk_9988776655", "api_secret=<redacted>"),
        ("my_api_key=mk_5544332211", "mk_5544332211", "my_api_key=<redacted>"),
        ("user.password=hunter2", "hunter2", "user.password=<redacted>"),
        ("X-Refresh-Token: rt_998877", "rt_998877", "X-Refresh-Token: <redacted>"),
    ],
)
def test_affixed_sensitive_field_names_are_redacted(
    body: str, leaked: str, expected: str
) -> None:
    message = format_http_status_error(_status_error(body))

    assert leaked not in message
    assert expected in message


@pytest.mark.parametrize(
    ("body", "leaked", "expected"),
    [
        # Short, purely alphabetic values carry no digits or base64 punctuation,
        # so a "does this look opaque?" test on the value alone misses them.
        ("password:swordfish", "swordfish", "password:<redacted>"),
        ("token:abcdefgh", "abcdefgh", "token:<redacted>"),
        ("secret: hunter", "hunter", "secret: <redacted>"),
        ('{"api_key": "abc"', "abc", '"api_key": "<redacted>'),
    ],
)
def test_short_alphabetic_values_are_redacted(body: str, leaked: str, expected: str) -> None:
    message = format_http_status_error(_status_error(body))

    assert leaked not in message
    assert expected in message


@pytest.mark.parametrize(
    ("body", "leaked", "expected"),
    [
        # The credential follows an auth scheme rather than the separator, so the
        # scheme name is kept as a diagnostic and only the credential is cut.
        (
            "Authorization: Bearer abcdefghijklmnop",
            "abcdefghijklmnop",
            "Authorization: Bearer <redacted>",
        ),
        ('invalid header "Authorization: Bearer sk-XYZ"', "sk-XYZ", "Bearer <redacted>"),
        ("authorization=Basic YWxpY2U6c2VjcmV0", "YWxpY2U6c2VjcmV0", "Basic <redacted>"),
        # No sensitive field name at all -- the scheme is the only signal.
        ("rejected header: Bearer sk-live-0123456789", "sk-live-0123456789", "Bearer <redacted>"),
        ("proxy said Basic dXNlcjpwdw==", "dXNlcjpwdw==", "Basic <redacted>"),
    ],
)
def test_authorization_scheme_credentials_are_redacted(
    body: str, leaked: str, expected: str
) -> None:
    message = format_http_status_error(_status_error(body))

    assert leaked not in message
    assert expected in message


@pytest.mark.parametrize(
    ("body", "leaked", "expected"),
    [
        # An unambiguous scheme always cuts what follows: judging the value's
        # shape would pass a credential that is short or spelled like a word.
        ("rejected header: Bearer abc", "abc", "Bearer <redacted>"),
        ("proxy said Basic secretword", "secretword", "Basic <redacted>"),
        ("bad header: JWT hunter", "hunter", "JWT <redacted>"),
        ("supplied ApiKey swordfish", "swordfish", "ApiKey <redacted>"),
        ("got Digest abc123", "abc123", "Digest <redacted>"),
    ],
)
def test_explicit_scheme_redacts_short_and_word_like_credentials(
    body: str, leaked: str, expected: str
) -> None:
    message = format_http_status_error(_status_error(body))

    assert leaked not in message
    assert expected in message


@pytest.mark.parametrize(
    "body",
    [
        # Prose about the auth mechanics itself, not a credential after a scheme.
        "Bearer token missing from request",
        "Basic realm required",
        "Digest access authentication is not supported",
        "JWT validation failed",
        "Negotiate support disabled",
    ],
)
def test_scheme_prose_survives_redaction(body: str) -> None:
    message = format_http_status_error(_status_error(body, status=502))

    assert message.endswith(f": {body}")
    assert "<redacted>" not in message


@pytest.mark.parametrize(
    ("body", "leaked"),
    [
        # Well-formed JSON whose *string leaves* quote a sensitive assignment
        # back at us. Key-by-key redaction never inspects the leaf text.
        ('{"detail": "invalid token=ghp_SECRET1234 supplied"}', "ghp_SECRET1234"),
        ('{"errors": [{"msg": "password=hunter2 rejected"}]}', "hunter2"),
        ('{"a": {"b": {"c": "access_token=at_9988776655 expired"}}}', "at_9988776655"),
        ('"Authorization: Bearer sk-0123456789"', "sk-0123456789"),
        ('{"detail": "bad client_secret=cs_5544332211"}', "cs_5544332211"),
    ],
)
def test_reflected_assignments_inside_json_string_leaves_are_redacted(
    body: str, leaked: str
) -> None:
    message = format_http_status_error(_status_error(body))

    assert leaked not in message
    assert "<redacted>" in message


def test_json_string_leaf_scrub_keeps_surrounding_prose() -> None:
    body = '{"detail": "invalid token=ghp_SECRET1234 supplied for run abc123"}'

    message = format_http_status_error(_status_error(body))

    assert "ghp_SECRET1234" not in message
    assert "invalid token=<redacted> supplied for run abc123" in message


@pytest.mark.parametrize(
    "body",
    [
        # Non-sensitive assignments, sensitive words used as prose rather than as
        # a field name, and values that merely follow a sensitive-looking word.
        "page=2 of 5 results",
        "token count: 42 exceeded",
        "status=failed detail=useful",
        "Basic authentication failed for this endpoint",
        "run abc123 not found",
        "retry after 30 seconds; attempt=3",
    ],
)
def test_non_sensitive_diagnostics_survive_redaction(body: str) -> None:
    message = format_http_status_error(_status_error(body, status=502))

    assert message.endswith(f": {body}")
    assert "<redacted>" not in message


@pytest.mark.parametrize(
    ("body", "leaked"),
    [
        # A harmless outer field must not consume the sensitive assignment nested
        # in its value: declining a separator has to cost zero characters, or the
        # inner name is never even looked at.
        ('{"detail": "token=SECRET0123456789"', "SECRET0123456789"),
        ('{"detail": "access_token=SECRET0123456789"', "SECRET0123456789"),
        ('{"msg": "d", "err": "password:swordfish"', "swordfish"),
        ("outer=inner_secret=SECRET0123456789", "SECRET0123456789"),
    ],
)
def test_nested_assignment_under_harmless_outer_field_is_reached(
    body: str, leaked: str
) -> None:
    message = format_http_status_error(_status_error(body))

    assert leaked not in message
    assert "<redacted>" in message


@pytest.mark.parametrize(
    "body",
    [
        # Aligned/log-style padding between the name and its separator.
        "api_key      : SECRET0123456789",
        'api_key"      :      "SECRET0123456789',
        "api_key\t=\tSECRET0123456789",
    ],
)
def test_padding_between_field_name_and_separator_still_redacts(body: str) -> None:
    message = format_http_status_error(_status_error(body))

    assert "SECRET0123456789" not in message
    assert "<redacted>" in message


def test_ambiguous_bare_scheme_needs_a_long_credential() -> None:
    # "Token"/"OAuth" are ordinary words, so a bare occurrence redacts only when
    # what follows is long enough to be a credential rather than an identifier.
    redacted = format_http_status_error(_status_error("rejected: Token SECRET0123456789xyz"))
    prose = format_http_status_error(_status_error("Token 12345 expired", status=502))

    assert "SECRET0123456789xyz" not in redacted
    assert "Token <redacted>" in redacted
    assert prose.endswith(": Token 12345 expired")


def test_url_in_detail_is_not_swallowed_as_an_assignment() -> None:
    # ``https:`` reads like ``key: value``; the scheme must not consume the query
    # string, or the credential inside it would never be scanned.
    body = "callback https://cb.tangle.test/hook?token=sk-0123456789&page=2 rejected"

    message = format_http_status_error(_status_error(body))

    assert "sk-0123456789" not in message
    assert "https://cb.tangle.test/hook?token=<redacted>&page=2" in message


@pytest.mark.parametrize(
    ("body", "leaked", "expected"),
    [
        # A credential can ride in a URL or in bare userinfo rather than in an
        # assignment, and a reflected body carries those just as an exception does.
        (
            "callback to https://alice:hunter2@cb.tangle.test/hook failed",
            "hunter2",
            "https://<redacted>@cb.tangle.test/hook",
        ),
        (
            "proxy alice:hunter2@proxy.internal:8080 refused",
            "hunter2",
            "proxy <redacted>@proxy.internal:8080 refused",
        ),
        (
            "upstream https://bucket.s3.test/o?X-Amz-Signature=DEADBEEFSIG expired",
            "DEADBEEFSIG",
            "X-Amz-Signature=<redacted>",
        ),
    ],
)
def test_urls_and_userinfo_in_body_text_are_scrubbed(
    body: str, leaked: str, expected: str
) -> None:
    message = format_http_status_error(_status_error(body, status=502))

    assert leaked not in message
    assert "alice" not in message
    assert expected in message


@pytest.mark.parametrize(
    "body",
    [
        # A reflected URL whose authority does not parse (textual or out-of-range
        # port, malformed IPv6) must fail closed, not raise out of the formatter.
        "see https://api.test:99999/cb?token=sk-0123456789 for details",
        "see https://api.test:bad/cb?token=sk-0123456789 for details",
        "see https://[oops/cb?token=sk-0123456789 for details",
    ],
)
def test_reflected_urls_with_unparsable_authority_fail_closed(body: str) -> None:
    message = format_http_status_error(_status_error(body, status=502))

    assert "\n" not in message
    assert "HTTP 502" in message
    assert "sk-0123456789" not in message
    assert "<redacted>" in message


@pytest.mark.parametrize(
    ("body", "leaked"),
    [
        ('{"detail": "fetch https://alice:hunter2@storage.test/o failed"}', "hunter2"),
        ('{"detail": "proxy alice:hunter2@proxy.internal refused"}', "hunter2"),
        ('{"a": {"b": [{"msg": "https://alice:hunter2@h.test/x"}]}}', "hunter2"),
        (
            '{"a": {"b": {"detail": "https://s.test/o?X-Amz-Signature=DEADBEEFSIG"}}}',
            "DEADBEEFSIG",
        ),
    ],
)
def test_urls_and_userinfo_in_nested_json_leaves_are_scrubbed(body: str, leaked: str) -> None:
    # Key-by-key redaction never inspects leaf text, so the leaf scrub is the only
    # thing standing between a reflected presigned URL and stderr.
    message = format_http_status_error(_status_error(body))

    assert leaked not in message
    assert "<redacted>" in message


_CREDENTIAL_FIELD_NAMES = [
    "access_token",
    "api_key",
    "api_secret",
    "authorization",
    "client_secret",
    "cookie",
    "credential",
    "id_token",
    "my_api_key",
    "password",
    "private_key",
    "refresh_token",
    "session_token",
    "signed_url",
    "token",
    "user.password",
    "X-Api-Key",
    "X-Gateway-Auth",
    "X-Refresh-Token",
]
# Names that merely contain a credential word. Substring matching redacts every
# one of them; matching the trailing token of the name does not.
_CREDENTIAL_LOOKALIKE_FIELD_NAMES = [
    "auth_method",
    "completion_tokens",
    "function_signature",
    "max_tokens",
    "oauth_provider",
    "password_policy",
    "prompt_tokens",
    "secretary_email",
    "signature_version",
    "token_count",
    "tokenizer",
    "X-Author",
]


@pytest.mark.parametrize("name", _CREDENTIAL_FIELD_NAMES)
def test_credential_field_names_are_redacted(name: str) -> None:
    json_message = format_http_status_error(_status_error(f'{{"{name}": "SECRETVALUE"}}'))
    text_message = format_http_status_error(_status_error(f"{name}=SECRETVALUE"))

    assert "SECRETVALUE" not in json_message
    assert "SECRETVALUE" not in text_message


@pytest.mark.parametrize("name", _CREDENTIAL_LOOKALIKE_FIELD_NAMES)
def test_credential_lookalike_field_names_keep_their_values(name: str) -> None:
    json_message = format_http_status_error(_status_error(f'{{"{name}": "PLAINVALUE"}}'))
    text_message = format_http_status_error(_status_error(f"{name}=PLAINVALUE", status=502))

    assert "PLAINVALUE" in json_message
    assert text_message.endswith(f": {name}=PLAINVALUE")


def test_credential_lookalike_header_names_keep_their_values() -> None:
    headers = {name: "PLAINVALUE" for name in _CREDENTIAL_LOOKALIKE_FIELD_NAMES}

    assert _redact_headers(headers) == headers


def test_credential_headers_are_redacted() -> None:
    redacted = _redact_headers({name: "SECRETVALUE" for name in _CREDENTIAL_FIELD_NAMES})

    assert set(redacted.values()) == {"<redacted>"}


def test_signature_is_a_credential_in_a_query_but_not_as_a_body_field() -> None:
    # A presigned grant lives in the query string, so ``signature`` there is the
    # credential itself; a body field named ``function_signature`` is not.
    assert "SIGVALUE" not in sanitize_url("https://api.test/x?signature=SIGVALUE")
    assert (
        sanitize_url("https://api.test/x?function_signature=f(x)")
        == "https://api.test/x?function_signature=f%28x%29"
    )


@pytest.mark.parametrize(
    "body",
    [
        # Adversarial shapes with no exponential structure, all bounded classes.
        "=" * 20000,
        "a" * 20000 + "=",
        ("token" + "_" * 200 + "=abc ") * 2000,
        ("Bearer " + "a" * 100 + " ") * 2000,
        '{"k": "' + ("password=x " * 5000) + '"}',
    ],
)
def test_redaction_stays_linear_on_adversarial_bodies(body: str) -> None:
    start = time.perf_counter()
    message = format_http_status_error(_status_error(body, status=500))
    elapsed = time.perf_counter() - start

    assert elapsed < 2.0
    assert len(message) < _MAX_BACKEND_DETAIL_CHARS + 200


def test_non_json_redaction_is_linear_on_large_bodies() -> None:
    # The assignment pattern uses only bounded/negated classes with no nested
    # quantifier, so a large adversarial body must not trigger catastrophic
    # backtracking.
    body = ("token=" + "a" * 50 + " ") * 20000 + "password: " + "x" * 40
    start = time.perf_counter()
    message = format_http_status_error(_status_error(body))
    elapsed = time.perf_counter() - start

    assert elapsed < 2.0
    assert "aaaaaaaaaa" not in message


@pytest.mark.parametrize(
    "body",
    [
        # Shapes that make a ``scheme://`` or ``user:pass@`` regex re-scan the same
        # run once per starting offset. The scans anchor on ``://`` and ``@``
        # instead, so each character is visited a bounded number of times.
        "a" * 200000 + "=",
        "a@" * 100000,
        "a:" * 100000,
        "://" * 60000,
        "https://u:p@h.test/x " * 10000,
    ],
)
def test_url_and_userinfo_scrubbing_stays_linear(body: str) -> None:
    start = time.perf_counter()
    message = format_http_status_error(_status_error(body, status=500))
    elapsed = time.perf_counter() - start

    assert elapsed < 2.0
    assert len(message) < _MAX_BACKEND_DETAIL_CHARS + 200


def test_schema_fetch_no_body_policy_survives_text_redaction() -> None:
    # The /openapi.json path omits the body entirely rather than relying on
    # redaction, so a reflected credential must not appear even as a marker.
    request = httpx.Request("GET", "https://api.tangle.test/openapi.json")
    response = httpx.Response(401, text="token=REFLECTED0123456789", request=request)
    exc = httpx.HTTPStatusError("unauthorized", request=request, response=response)

    message = format_http_status_error(exc, include_detail=False)

    assert message == "HTTP 401 Unauthorized for GET https://api.tangle.test/openapi.json"
    assert "REFLECTED0123456789" not in message
    assert "<redacted>" not in message


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


def test_format_request_error_fails_closed_on_out_of_range_port() -> None:
    # httpx accepts a request URL whose port is out of range, so a genuine
    # ConnectError can carry one into the formatter.
    request = httpx.Request("GET", "http://alice:pw@api.test:99999/x")
    exc = httpx.ConnectError("[Errno 111] Connection refused", request=request)

    message = format_request_error(exc)

    assert "\n" not in message
    assert message.startswith("Failed to reach GET http://<redacted>/x:")
    assert "pw" not in message
    assert "connection failed" in message


@pytest.mark.parametrize(
    "text",
    [
        "All connection attempts failed for https://api.test:bad/x?token=sk-0123456789",
        "redirect to https://api.test:99999/cb?token=sk-0123456789 refused",
        "redirect to https://[oops/cb?token=sk-0123456789 refused",
    ],
)
def test_describe_request_error_fails_closed_on_unparsable_authority(text: str) -> None:
    message = describe_request_error(httpx.ConnectError(text))

    assert "\n" not in message
    assert "sk-0123456789" not in message
    assert "<redacted>" in message


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


_CAMEL_CASE_CREDENTIAL_NAMES = [
    "accessToken",
    "AccessToken",
    "refreshToken",
    "idToken",
    "sessionToken",
    "authToken",
    "oauth2Token",
    "clientSecret",
    "ClientSecret",
    "apiSecret",
    "apiKey",
    "ApiKey",
    "APIKey",
    "XApiKey",
    "myApiKey",
    "privateKey",
    "secretKey",
    "userPassword",
    "sessionCookie",
]

_CAMEL_CASE_DIAGNOSTIC_NAMES = [
    "maxTokens",
    "MaxTokens",
    "promptTokens",
    "completionTokens",
    "tokenCount",
    "TokenCount",
    "tokenizer",
    "Tokenizer",
    "functionSignature",
    "signatureVersion",
    "passwordPolicy",
    "authMethod",
    "oauthProvider",
    "secretaryEmail",
]


@pytest.mark.parametrize("name", _CAMEL_CASE_CREDENTIAL_NAMES)
def test_camel_case_credential_names_are_redacted_everywhere(name: str) -> None:
    body = json.dumps({name: "PLAINSECRET"})

    assert "PLAINSECRET" not in format_http_status_error(_status_error(body))
    assert _redact_headers({name: "PLAINSECRET"})[name] == "<redacted>"
    assert "PLAINSECRET" not in format_http_status_error(_status_error(f"{name}=PLAINSECRET"))


@pytest.mark.parametrize("name", _CAMEL_CASE_DIAGNOSTIC_NAMES)
def test_camel_case_diagnostic_names_keep_their_values(name: str) -> None:
    body = json.dumps({name: "KEEPME"})

    assert "KEEPME" in format_http_status_error(_status_error(body))
    assert _redact_headers({name: "KEEPME"})[name] == "KEEPME"
    assert "KEEPME" in format_http_status_error(_status_error(f"{name}=KEEPME"))


@pytest.mark.parametrize("depth", [1, 8, 32, 900, 1000, 5000, 9000])
def test_deeply_nested_json_body_stays_one_bounded_line(depth: int) -> None:
    message = format_http_status_error(_status_error("[" * depth + "1" + "]" * depth, status=500))

    assert "\n" not in message
    assert len(message) < _MAX_BACKEND_DETAIL_CHARS + 200


@pytest.mark.parametrize("depth", [8, 900, 3000])
def test_secret_nested_beyond_reach_is_never_emitted(depth: int) -> None:
    node: dict[str, object] = {"access_token": "DEEPSECRET"}
    for _ in range(depth):
        node = {"a": node}

    message = format_http_status_error(_status_error(json.dumps(node), status=500))

    assert "DEEPSECRET" not in message
    assert "\n" not in message


def test_nesting_past_the_depth_bound_is_replaced_wholesale() -> None:
    node: dict[str, object] = {"leaf": "visible-detail"}
    for _ in range(_MAX_JSON_DEPTH + 5):
        node = {"a": node}

    message = format_http_status_error(_status_error(json.dumps(node), status=500))

    assert "visible-detail" not in message
    assert "nesting too deep" in message


def test_shallow_json_is_unaffected_by_the_depth_bound() -> None:
    body = json.dumps({"errors": [{"loc": ["body", "name"], "msg": "field required"}]})

    message = format_http_status_error(_status_error(body))

    assert "field required" in message
    assert "nesting too deep" not in message


@pytest.mark.parametrize(
    "text",
    [
        ":hunter2@proxy.internal",
        "proxy :hunter2@proxy.internal refused",
        "https://:hunter2@proxy.internal/x",
    ],
)
def test_empty_username_userinfo_is_redacted(text: str) -> None:
    message = describe_request_error(httpx.ConnectError(text))

    assert "hunter2" not in message
    assert "proxy.internal" in message


def test_sanitize_url_keeps_non_credential_presigned_parameters() -> None:
    signed = (
        "https://bucket.s3.test/object?X-Amz-Algorithm=AWS4-HMAC-SHA256"
        "&X-Amz-Date=20240101T000000Z&X-Amz-Expires=900&X-Amz-SignedHeaders=host"
        "&X-Amz-Credential=AKIA_LEAK&X-Amz-Signature=DEADBEEFSIG"
        "&X-Amz-Security-Token=SESSIONTOKEN"
    )

    sanitized = sanitize_url(signed)

    assert "AKIA_LEAK" not in sanitized
    assert "DEADBEEFSIG" not in sanitized
    assert "SESSIONTOKEN" not in sanitized
    assert "X-Amz-Algorithm=AWS4-HMAC-SHA256" in sanitized
    assert "X-Amz-Date=20240101T000000Z" in sanitized
    assert "X-Amz-Expires=900" in sanitized
    assert "X-Amz-SignedHeaders=host" in sanitized


_ACCESS_KEY_NAMES = [
    "access_key_id",
    "access-key-id",
    "accessKeyId",
    "AccessKeyID",
    "AccessKeyIdentifier",
    "aws_access_key_id",
    "AwsAccessKeyId",
    "AWSAccessKeyId",
    "google_access_id",
    "GoogleAccessId",
    "access_id",
    "accessId",
    "api_key_id",
    "apiKeyId",
    "secret_key",
    "secretKey",
    "private_key",
    "privateKey",
]

_ACCESS_KEY_LOOKALIKES = [
    "access_key_id_format",
    "accessKeyIdFormat",
    "access_key_id_prefix",
    "private_key_path",
    "privateKeyPath",
    "signature_version",
    "signatureVersion",
    "function_signature",
    "session_id",
    "sessionId",
    "request_id",
    "requestId",
    "user_id",
    "userId",
    "key_id",
    "keyId",
    "correlation_id",
    "traceId",
]


@pytest.mark.parametrize("name", _ACCESS_KEY_NAMES)
def test_access_key_identifiers_are_redacted_everywhere(name: str) -> None:
    body = json.dumps({"error": {name: "AKIA_LEAK"}})

    assert "AKIA_LEAK" not in format_http_status_error(_status_error(body))
    assert _redact_headers({name: "AKIA_LEAK"})[name] == "<redacted>"
    assert "AKIA_LEAK" not in format_http_status_error(_status_error(f"rejected {name}=AKIA_LEAK"))
    assert "AKIA_LEAK" not in sanitize_url(f"https://api.tangle.test/x?{name}=AKIA_LEAK")


@pytest.mark.parametrize("name", _ACCESS_KEY_LOOKALIKES)
def test_access_key_lookalikes_keep_their_values(name: str) -> None:
    body = json.dumps({"error": {name: "KEEPME"}})

    assert "KEEPME" in format_http_status_error(_status_error(body))
    assert _redact_headers({name: "KEEPME"})[name] == "KEEPME"
    assert "KEEPME" in format_http_status_error(_status_error(f"rejected {name}=KEEPME"))
    assert "KEEPME" in sanitize_url(f"https://api.tangle.test/x?{name}=KEEPME")


def test_signed_url_body_value_keeps_its_location_and_loses_its_signature() -> None:
    body = json.dumps(
        {"signed_url": "https://bucket.s3.test/report.csv?X-Amz-Expires=900&X-Amz-Signature=SIGLEAK"}
    )

    message = format_http_status_error(_status_error(body))

    assert "SIGLEAK" not in message
    assert "bucket.s3.test/report.csv" in message
    assert "X-Amz-Expires=900" in message


def test_signed_url_without_a_url_value_is_redacted_whole() -> None:
    message = format_http_status_error(_status_error(json.dumps({"signed_url": "OPAQUEGRANT"})))

    assert "OPAQUEGRANT" not in message


def test_signed_url_header_and_query_are_redacted_whole() -> None:
    assert _redact_headers({"X-Signed-Url": "https://b.test/o?sig=SIGLEAK"})["X-Signed-Url"] == (
        "<redacted>"
    )
    sanitized = sanitize_url("https://api.tangle.test/x?signed_url=https%3A%2F%2Fb.test%2Fo")
    assert "b.test" not in sanitized


_UNBROKEN_RUN_CREDENTIAL_NAMES = [
    "accesskeyid",
    "ACCESSKEYID",
    "accesstoken",
    "accessid",
    "accesskey",
    "apikey",
    "apisecret",
    "apitoken",
    "authtoken",
    "bearertoken",
    "clientsecret",
    "idtoken",
    "privatekey",
    "refreshtoken",
    "secretkey",
    "sessionkey",
    "sessiontoken",
    "awsaccesskeyid",
    "googleaccessid",
]

_UNBROKEN_RUN_LOOKALIKES = [
    "accesskeyidformat",
    "privatekeypath",
    "maxtokens",
    "sessionid",
    "keyid",
    "tokenizer",
    "secretaryemail",
    "signatureversion",
    "functionsignature",
    "requestid",
    "traceid",
    "identifier",
]


@pytest.mark.parametrize("name", _UNBROKEN_RUN_CREDENTIAL_NAMES)
def test_unbroken_lowercase_credential_runs_are_redacted_everywhere(name: str) -> None:
    body = json.dumps({"error": {name: "AKIA_LEAK"}})

    assert "AKIA_LEAK" not in format_http_status_error(_status_error(body))
    assert _redact_headers({name: "AKIA_LEAK"})[name] == "<redacted>"
    assert "AKIA_LEAK" not in format_http_status_error(_status_error(f"denied {name}=AKIA_LEAK"))
    assert "AKIA_LEAK" not in sanitize_url(f"https://api.tangle.test/x?{name}=AKIA_LEAK")


@pytest.mark.parametrize("name", _UNBROKEN_RUN_LOOKALIKES)
def test_unbroken_lowercase_lookalikes_keep_their_values(name: str) -> None:
    body = json.dumps({"error": {name: "KEEPME"}})

    assert "KEEPME" in format_http_status_error(_status_error(body))
    assert _redact_headers({name: "KEEPME"})[name] == "KEEPME"
    assert "KEEPME" in format_http_status_error(_status_error(f"bad {name}=KEEPME"))
    assert "KEEPME" in sanitize_url(f"https://api.tangle.test/x?{name}=KEEPME")


@pytest.mark.parametrize(
    "fragment",
    [
        "access_token=FRAGSECRET&token_type=Bearer&expires_in=3600",
        "accesstoken=FRAGSECRET",
        "sig=FRAGSECRET",
        "signature=FRAGSECRET",
        "credential=FRAGSECRET",
        "X-Amz-Signature=FRAGSECRET",
        "id_token=FRAGSECRET&state=abc",
    ],
)
def test_sanitize_url_redacts_credentials_in_the_fragment(fragment: str) -> None:
    sanitized = sanitize_url(f"https://h.test/callback#{fragment}")

    assert "FRAGSECRET" not in sanitized
    assert "h.test/callback" in sanitized


def test_sanitize_url_keeps_non_credential_fragment_parameters() -> None:
    sanitized = sanitize_url("https://h.test/x#X-Amz-Signature=SIG&X-Amz-Expires=900&page=3")

    assert "SIG" not in sanitized
    assert "X-Amz-Expires=900" in sanitized
    assert "page=3" in sanitized


@pytest.mark.parametrize("fragment", ["section-2", "L42", "installation", ""])
def test_sanitize_url_preserves_a_plain_anchor_fragment(fragment: str) -> None:
    url = f"https://h.test/guide#{fragment}" if fragment else "https://h.test/guide"

    assert sanitize_url(url) == url


def test_sanitize_url_drops_a_fragment_it_cannot_structurally_parse() -> None:
    sanitized = sanitize_url("https://h.test/x#state=ok;access_token=HIDDEN")

    assert "HIDDEN" not in sanitized
    assert sanitized == "https://h.test/x#<redacted>"


def test_request_url_fragment_credential_is_redacted_in_the_status_line() -> None:
    request = httpx.Request("POST", "https://api.tangle.test/x#access_token=REQFRAG")
    response = httpx.Response(401, request=request, text="denied")

    message = format_http_status_error(
        httpx.HTTPStatusError("error", request=request, response=response)
    )

    assert "REQFRAG" not in message


def test_body_url_fragment_credential_is_redacted() -> None:
    body = json.dumps({"detail": "retry at https://h.test/cb#access_token=FRAGSECRET"})

    assert "FRAGSECRET" not in format_http_status_error(_status_error(body))


@pytest.mark.parametrize(
    "fragment",
    [
        "alice:ANCHORSECRET@evil.test",
        ":ANCHORSECRET@evil.test",
        "https://alice:ANCHORSECRET@evil.test",
        "https://alice:ANCHORSECRET@evil.test/path",
        "go/to/alice:ANCHORSECRET@evil.test",
    ],
)
def test_sanitize_url_strips_userinfo_from_an_anchor_fragment(fragment: str) -> None:
    sanitized = sanitize_url(f"https://h.test/guide#{fragment}")

    assert "ANCHORSECRET" not in sanitized
    assert sanitized.startswith("https://h.test/guide#")


@pytest.mark.parametrize("fragment", ["user@example.com", "a.b.c", "step-3/details"])
def test_sanitize_url_keeps_an_anchor_without_userinfo(fragment: str) -> None:
    url = f"https://h.test/guide#{fragment}"

    assert sanitize_url(url) == url


@pytest.mark.parametrize(
    "value",
    [
        "https%3A%2F%2Falice%3AVALSECRET%40evil.test",
        "alice%3AVALSECRET%40evil.test",
        "https://alice:VALSECRET@evil.test",
    ],
)
def test_sanitize_url_strips_userinfo_from_a_non_credential_query_value(value: str) -> None:
    sanitized = sanitize_url(f"https://h.test/x?page=3&next={value}")

    assert "VALSECRET" not in sanitized
    assert "page=3" in sanitized
    assert "evil.test" in sanitized


@pytest.mark.parametrize(
    "value",
    [
        "https%3A%2F%2Falice%3AVALSECRET%40evil.test",
        "alice%3AVALSECRET%40evil.test",
    ],
)
def test_sanitize_url_strips_userinfo_from_a_non_credential_fragment_value(value: str) -> None:
    sanitized = sanitize_url(f"https://h.test/x#page=3&next={value}")

    assert "VALSECRET" not in sanitized
    assert "page=3" in sanitized


def test_sanitize_url_keeps_query_values_that_carry_no_userinfo() -> None:
    url = "https://h.test/x?next=https%3A%2F%2Fdocs.test%2Fa&page=3&q=user%40example.com"

    assert sanitize_url(url) == url


def test_request_url_anchor_userinfo_is_redacted_in_the_failure_message() -> None:
    request = httpx.Request("GET", "https://api.tangle.test/v1/jobs#alice:REQANCHOR@evil.test")
    exc = httpx.ConnectError("connection refused", request=request)

    message = format_request_error(exc)

    assert "REQANCHOR" not in message
    assert "\n" not in message


def test_request_url_encoded_query_userinfo_is_redacted_in_the_status_line() -> None:
    request = httpx.Request(
        "POST", "https://api.tangle.test/x?next=https%3A%2F%2Falice%3AREQQUERY%40evil.test"
    )
    response = httpx.Response(401, request=request, text="denied")

    message = format_http_status_error(
        httpx.HTTPStatusError("error", request=request, response=response)
    )

    assert "REQQUERY" not in message


@pytest.mark.parametrize(
    "url",
    [
        "https://h.test/cb#alice:BODYANCHOR@evil.test",
        "https://h.test/cb#next=https%3A%2F%2Falice%3ABODYANCHOR%40evil.test",
        "https://h.test/cb?next=https%3A%2F%2Falice%3ABODYANCHOR%40evil.test",
        "https://h.test/cb?next=alice%3ABODYANCHOR%40evil.test",
    ],
)
def test_body_url_userinfo_is_redacted_wherever_it_hides(url: str) -> None:
    body = json.dumps({"detail": f"retry at {url}"})

    assert "BODYANCHOR" not in format_http_status_error(_status_error(body))


def _nested(target: str, *, key: str = "next", separator: str = "?") -> str:
    return f"https://h.test/x{separator}{key}={urllib.parse.quote(target, safe='')}"


@pytest.mark.parametrize(
    "parameter",
    [
        "access_token=NESTSECRET",
        "oauth=NESTSECRET",
        "id_token=NESTSECRET",
        "credential=NESTSECRET",
        "security-token=NESTSECRET",
        "X-Amz-Signature=NESTSECRET",
        "x-amz-security-token=NESTSECRET",
    ],
)
def test_sanitize_url_redacts_credentials_of_a_nested_url_value(parameter: str) -> None:
    sanitized = sanitize_url(_nested(f"https://evil.test/cb?{parameter}"))

    assert "NESTSECRET" not in sanitized
    assert "evil.test" in sanitized


@pytest.mark.parametrize("separator", ["?", "#"])
def test_sanitize_url_redacts_a_nested_url_credential_in_query_and_fragment(
    separator: str,
) -> None:
    sanitized = sanitize_url(
        _nested("https://evil.test/cb?access_token=NESTSECRET", separator=separator)
    )

    assert "NESTSECRET" not in sanitized


def test_sanitize_url_redacts_a_credential_in_the_nested_urls_own_fragment() -> None:
    sanitized = sanitize_url(_nested("https://evil.test/cb#access_token=NESTSECRET"))

    assert "NESTSECRET" not in sanitized


def test_sanitize_url_redacts_a_nested_url_that_was_not_percent_encoded() -> None:
    sanitized = sanitize_url("https://h.test/x?next=https://evil.test/cb?access_token=NESTSECRET")

    assert "NESTSECRET" not in sanitized


def test_sanitize_url_redacts_both_userinfo_and_parameters_of_a_nested_url() -> None:
    sanitized = sanitize_url(_nested("https://bob:NESTUSER@evil.test/cb?access_token=NESTPARAM"))

    assert "NESTUSER" not in sanitized
    assert "NESTPARAM" not in sanitized


def test_sanitize_url_keeps_a_nested_url_that_carries_no_credential() -> None:
    url = _nested("https://docs.test/guide?page=3&lang=en")

    assert sanitize_url(url) == url


def test_sanitize_url_stops_descending_after_one_nested_level() -> None:
    """The bound is what keeps an untrusted body from driving unbounded recursion."""

    nested = "https://u:DEEPSECRET@evil.test"
    for _ in range(30):
        nested = f"https://h.test/y?next={urllib.parse.quote(nested, safe='')}"

    sanitized = sanitize_url(nested)

    assert sanitized.startswith("https://h.test/y?next=")
    assert "DEEPSECRET" not in sanitized


@pytest.mark.parametrize(
    "url",
    [
        "https://h.test/x?alice:KEYSECRET@evil.test=1",
        "https://h.test/x?alice%3AKEYSECRET%40evil.test=1",
        "https://h.test/x#alice%3AKEYSECRET%40evil.test=1",
        "https://h.test/x#alice:KEYSECRET@evil.test=1",
    ],
)
def test_sanitize_url_strips_userinfo_from_a_parameter_name(url: str) -> None:
    sanitized = sanitize_url(url)

    assert "KEYSECRET" not in sanitized


def test_nested_url_credential_is_redacted_in_the_status_line() -> None:
    url = _nested("https://evil.test/cb?access_token=REQNESTED")
    request = httpx.Request("POST", url)
    response = httpx.Response(401, request=request, text="denied")

    message = format_http_status_error(
        httpx.HTTPStatusError("error", request=request, response=response)
    )

    assert "REQNESTED" not in message


def test_nested_url_credential_is_redacted_in_the_connection_failure() -> None:
    exc = httpx.ConnectError(
        "refused",
        request=httpx.Request("GET", _nested("https://evil.test/cb?access_token=REQNESTED")),
    )

    message = format_request_error(exc)

    assert "REQNESTED" not in message
    assert "\n" not in message


@pytest.mark.parametrize(
    "target",
    [
        "https://evil.test/cb?access_token=BODYNESTED",
        "https://evil.test/cb#access_token=BODYNESTED",
        "https://bob:BODYNESTED@evil.test/cb?page=1",
    ],
)
def test_nested_url_credential_is_redacted_in_a_reflected_body(target: str) -> None:
    body = json.dumps({"detail": f"redirecting to {_nested(target)}"})

    assert "BODYNESTED" not in format_http_status_error(_status_error(body))


@pytest.mark.parametrize("name", ["oauth", "OAuth", "oauth_token"])
def test_bare_oauth_parameter_is_redacted(name: str) -> None:
    assert "OAUTHSECRET" not in sanitize_url(f"https://h.test/x?{name}=OAUTHSECRET")


@pytest.mark.parametrize("name", ["oauthlib", "oauth_callback", "oauth_version"])
def test_oauth_lookalike_names_keep_their_values(name: str) -> None:
    assert "keepme" in sanitize_url(f"https://h.test/x?{name}=keepme")


@pytest.mark.parametrize(
    "carried",
    [
        "access_token=CARRIEDSECRET",
        "/cb?access_token=CARRIEDSECRET",
        "mailto:ops@evil.test?password=CARRIEDSECRET",
        "data:text/plain,access_token=CARRIEDSECRET",
        "custom:api_key=CARRIEDSECRET",
        "Bearer CARRIEDSECRETCARRIEDSECRET",
    ],
)
@pytest.mark.parametrize("separator", ["?", "#"])
def test_sanitize_url_redacts_an_assignment_carried_in_a_value(
    carried: str, separator: str
) -> None:
    """A credential need not be a parameter of its own to be displayed."""

    url = f"https://h.test/x{separator}state={urllib.parse.quote(carried, safe='')}"

    assert "CARRIEDSECRET" not in sanitize_url(url)


def test_sanitize_url_redacts_an_unencoded_assignment_carried_in_a_value() -> None:
    assert "CARRIEDSECRET" not in sanitize_url("https://h.test/x?state=access_token:CARRIEDSECRET")


def _encoded(times: int, plain: str = "access_token=CARRIEDSECRET") -> str:
    for _ in range(times):
        plain = urllib.parse.quote(plain, safe="")
    return plain


# Every shape a credential arrives in, to be run against every placement and every
# encoding depth. Scanning a decoded layer for one shape and not the others lets the
# missing shape ride out of the sanitizer, so the cross-product is the point.
_CARRIED_PAYLOADS = [
    "access_token=CARRIEDSECRET",
    "alice:pwCARRIEDSECRET@host.test",
    "https://e.test/cb?access_token=CARRIEDSECRET",
    "https://alice:pwCARRIEDSECRET@e.test/cb",
    "/cb?api_key=CARRIEDSECRET",
]


@pytest.mark.parametrize("payload", _CARRIED_PAYLOADS)
@pytest.mark.parametrize("depth", [0, 1, 2, 3, 4, 5, 6])
@pytest.mark.parametrize("separator", ["?", "#"])
def test_sanitize_url_redacts_a_carried_credential_at_any_encoding_depth(
    depth: int, separator: str, payload: str
) -> None:
    """Encoding again must bury the credential, never carry it past the scan."""

    url = f"https://h.test/x{separator}next={_encoded(depth, payload)}"

    assert "CARRIEDSECRET" not in sanitize_url(url)


@pytest.mark.parametrize("payload", _CARRIED_PAYLOADS)
@pytest.mark.parametrize("depth", [1, 2, 3])
@pytest.mark.parametrize("separator", ["?", "#"])
def test_sanitize_url_redacts_a_credential_encoded_into_a_parameter_name(
    depth: int, separator: str, payload: str
) -> None:
    """A name is displayed just as a value is, so it gets the same leaf scrub."""

    url = f"https://h.test/x{separator}{_encoded(depth, payload)}=1"

    assert "CARRIEDSECRET" not in sanitize_url(url)


@pytest.mark.parametrize("payload", _CARRIED_PAYLOADS)
@pytest.mark.parametrize("depth", [0, 1, 2, 3, 4, 5, 6])
def test_plain_anchor_redacts_a_carried_credential_at_any_encoding_depth(
    depth: int, payload: str
) -> None:
    """An anchor is not parsed as pairs, so it needs the same per-layer scan."""

    encoded = _encoded(depth, payload)
    if "=" in encoded or "&" in encoded:
        pytest.skip("carries a separator, so it is parsed as pairs rather than an anchor")

    assert "CARRIEDSECRET" not in sanitize_url(f"https://h.test/x#{encoded}")


@pytest.mark.parametrize("payload", _CARRIED_PAYLOADS)
@pytest.mark.parametrize("depth", [1, 2, 3, 4, 5, 6])
def test_carried_credential_at_depth_is_redacted_on_every_display_path(
    depth: int, payload: str
) -> None:
    """The bound holds on the exception paths and on a reflected body alike."""

    url = f"https://h.test/x?next={_encoded(depth, payload)}"
    request = httpx.Request("GET", url)

    rendered = [
        format_request_error(httpx.ConnectError("refused", request=request)),
        format_request_error(httpx.ConnectTimeout("slow", request=request)),
        format_http_status_error(_status_error(json.dumps({"detail": f"see {url}"}))),
        format_http_status_error(_status_error(f"redirect to {url} failed")),
        format_http_status_error(_status_error(f"<html><p>go {url}</p></html>")),
    ]

    assert not [text for text in rendered if "CARRIEDSECRET" in text]


@pytest.mark.parametrize("payload", _CARRIED_PAYLOADS)
@pytest.mark.parametrize("depth", [2, 3, 4, 5, 6])
def test_deeply_encoded_credential_is_dropped_whole(depth: int, payload: str) -> None:
    """Past the layers actually decoded the value is unexamined, so it is dropped."""

    sanitized = sanitize_url(f"https://h.test/x?next={_encoded(depth, payload)}")

    assert sanitized == "https://h.test/x?next=<redacted>"


@pytest.mark.parametrize(
    "chain",
    ["%25" * 20000, "%2525" * 12000, ("%25" * 3 + "a") * 8000, "%2540" * 12000],
)
def test_hostile_percent_chains_stay_bounded(chain: str) -> None:
    """Decoding is capped, so a hostile value cannot choose how many layers we peel."""

    start = time.perf_counter()

    sanitize_url(f"https://h.test/x?next={chain}")

    assert time.perf_counter() - start < 2.0


def test_scrubbing_a_parameter_name_does_not_change_the_verdict_on_its_value() -> None:
    """Sensitivity is judged on the name as parsed, before it is rewritten."""

    assert sanitize_url("https://h.test/x?access_token=SECRET") == (
        "https://h.test/x?access_token=<redacted>"
    )
    assert sanitize_url("https://h.test/x?session_id=sess_1") == (
        "https://h.test/x?session_id=sess_1"
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://h.test/x?page=2&limit=10",
        "https://h.test/x?dotted.key=1&dash-key=2",
        "https://h.test/x?state=abc%3Ddef",
        "https://h.test/x?state=abc%253Ddef",
        "https://h.test/x?pct=100%25",
    ],
)
def test_benign_names_and_encodings_survive_the_bounded_scrub(url: str) -> None:
    assert sanitize_url(url) == url


def test_sanitize_url_redacts_an_assignment_in_a_plain_anchor() -> None:
    assert "CARRIEDSECRET" not in sanitize_url("https://h.test/guide#api_key:CARRIEDSECRET")


@pytest.mark.parametrize(
    "value",
    ["/cb?page=3", "returning", "state:ok", "abc%3D", "next=/dashboard"],
)
def test_sanitize_url_keeps_a_value_carrying_no_credential(value: str) -> None:
    url = f"https://h.test/x?state={urllib.parse.quote(value, safe='')}"

    assert sanitize_url(url) == url


def test_carried_assignment_is_redacted_in_the_connection_failure() -> None:
    url = f"https://h.test/x?state={urllib.parse.quote('access_token=CARRIEDSECRET', safe='')}"
    exc = httpx.ConnectError("refused", request=httpx.Request("GET", url))

    message = format_request_error(exc)

    assert "CARRIEDSECRET" not in message
    assert "\n" not in message


def test_carried_assignment_is_redacted_in_a_reflected_body() -> None:
    url = f"https://h.test/x?state={urllib.parse.quote('access_token=CARRIEDSECRET', safe='')}"
    body = json.dumps({"detail": f"redirecting to {url}"})

    assert "CARRIEDSECRET" not in format_http_status_error(_status_error(body))
