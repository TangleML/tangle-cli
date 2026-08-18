"""SDK-layer HTTP status error handling.

SDK commands raise ``requests.HTTPError`` on non-2xx responses. These tests
cover the shared formatter and confirm the pipeline-runs dispatch points
(read/query, submit, and annotation commands) render a clean nonzero error
instead of a raw traceback, while client-internal recovery (the 404 run-id ->
execution-id fallback and post-submit run recovery) is preserved end to end.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import requests
import yaml

from tangle_cli import cli, pipeline_runs_cli
from tangle_cli.cli_helpers import _HTTP_ERROR_BODY_LIMIT, format_http_error
from tangle_cli.pipeline_run_details import PipelineRunDetails
from tangle_cli.pipeline_run_manager import PipelineRunError, PipelineRunHooks, PipelineRunManager


def _http_error(
    *,
    status_code: int = 500,
    reason: str = "Internal Server Error",
    method: str = "GET",
    url: str = "https://api.test/api/pipeline_runs/missing",
    body: str = "boom",
) -> requests.HTTPError:
    resp = requests.Response()
    resp.status_code = status_code
    resp.reason = reason
    resp._content = body.encode("utf-8")
    resp.request = requests.Request(method, url).prepare()
    return requests.HTTPError(f"{status_code} error", response=resp)


# --------------------------------------------------------------------------
# Formatter
# --------------------------------------------------------------------------


def test_format_http_error_includes_status_reason_method_url_and_body() -> None:
    message = format_http_error(
        _http_error(status_code=404, reason="Not Found", method="GET", url="https://api.test/x", body="missing run")
    )
    assert message == "Tangle API request failed (404 Not Found) for GET https://api.test/x: missing run"


def test_format_http_error_omits_body_when_empty() -> None:
    message = format_http_error(_http_error(status_code=500, reason="Server Error", body="   "))
    assert message == "Tangle API request failed (500 Server Error) for GET https://api.test/api/pipeline_runs/missing"


def test_format_http_error_truncates_long_body() -> None:
    message = format_http_error(_http_error(body="x" * 5000))
    _, _, rendered_body = message.partition(": ")
    assert rendered_body == "x" * _HTTP_ERROR_BODY_LIMIT + "... (truncated)"


def test_format_http_error_collapses_body_to_one_line() -> None:
    message = format_http_error(_http_error(body='{\n  "error": "bad\r\nrequest",\n\t"detail":  "x"\n}'))
    assert "\n" not in message
    assert "\r" not in message
    assert "\t" not in message
    assert message.endswith(': { "error": "bad request", "detail": "x" }')


def test_format_http_error_without_response_falls_back_to_str() -> None:
    exc = requests.HTTPError("opaque failure")
    assert format_http_error(exc) == "Tangle API request failed: opaque failure"


def test_format_http_error_redacts_url_userinfo_and_credential_query() -> None:
    message = format_http_error(
        _http_error(
            status_code=401,
            reason="Unauthorized",
            method="GET",
            url="https://user:s3cret@api.test/x?access_token=abc&page=2",
            body="nope",
        )
    )
    assert "s3cret" not in message
    assert "user:" not in message
    assert "abc" not in message
    assert "access_token=<redacted>" in message
    assert "page=2" in message
    # The userinfo is replaced rather than silently dropped, so the message still
    # says a credential was in the URL -- which is what a caller must fix.
    assert message.startswith(
        "Tangle API request failed (401 Unauthorized) for GET https://<redacted>@api.test/x?"
    )


def test_format_http_error_redacts_sensitive_keys_in_json_body_before_truncation() -> None:
    payload = {
        "detail": "boom",
        "token": "super-secret-token",
        "nested": {"password": "hunter2", "ok": "keep"},
    }
    message = format_http_error(
        _http_error(status_code=500, reason="Server Error", body=json.dumps(payload))
    )
    assert "super-secret-token" not in message
    assert "hunter2" not in message
    assert "<redacted>" in message
    assert "boom" in message
    assert "keep" in message


@pytest.mark.parametrize(
    "url, leaked, expected_redacted",
    [
        (
            "https://api.test/o?X-Amz-Credential=AKIALEAK&X-Amz-Signature=DEADBEEFSIG&X-Amz-Expires=900",
            ["DEADBEEFSIG", "AKIALEAK"],
            ["X-Amz-Signature=<redacted>", "X-Amz-Credential=<redacted>"],
        ),
        (
            "https://api.test/x?sig=SECRETSIG&signature=SECRET2&page=2",
            ["SECRETSIG", "SECRET2"],
            ["sig=<redacted>", "signature=<redacted>"],
        ),
        (
            "https://api.test/y?api_key=APIKEYLEAK&oauth_token=OAUTHLEAK&keep=1",
            ["APIKEYLEAK", "OAUTHLEAK"],
            ["api_key=<redacted>", "oauth_token=<redacted>"],
        ),
        (
            "https://api.test/z?awsaccesskeyid=AKIAX&googleaccessid=GOOGLEID&next=5",
            ["AKIAX", "GOOGLEID"],
            ["awsaccesskeyid=<redacted>", "googleaccessid=<redacted>"],
        ),
    ],
)
def test_format_http_error_redacts_signed_url_query_keys(
    url: str, leaked: list[str], expected_redacted: list[str]
) -> None:
    message = format_http_error(_http_error(status_code=401, reason="Unauthorized", url=url, body="nope"))
    for secret in leaked:
        assert secret not in message
    for fragment in expected_redacted:
        assert fragment in message


def test_format_http_error_preserves_non_sensitive_query_keys() -> None:
    message = format_http_error(
        _http_error(url="https://api.test/p?page=2&design=cool&assignment=1", body="nope")
    )
    assert "page=2" in message
    assert "design=cool" in message
    assert "assignment=1" in message


@pytest.mark.parametrize(
    "body, leaked, expected_redacted",
    [
        ("credential=BODYSECRET&foo=bar", "BODYSECRET", "credential=<redacted>"),
        ("password=hunter2; note=ok", "hunter2", "password=<redacted>"),
        ("<html>token: sk-live-0123456789ABCDEF</html>", "sk-live-0123456789ABCDEF", "token: <redacted>"),
        ("oauth_token=OAUTHPLAIN api_key=APIPLAIN", "OAUTHPLAIN", "oauth_token=<redacted>"),
        ("Signature: PLAINSIG12345", "PLAINSIG12345", "Signature: <redacted>"),
    ],
)
def test_format_http_error_redacts_secrets_in_non_json_body(
    body: str, leaked: str, expected_redacted: str
) -> None:
    message = format_http_error(_http_error(status_code=400, reason="Bad Request", body=body))
    assert leaked not in message
    assert expected_redacted in message


def test_format_http_error_preserves_non_sensitive_non_json_body() -> None:
    # Non-sensitive assignments and surrounding prose stay intact so diagnostics
    # remain useful; only the value of a credential-named field is cut.
    message = format_http_error(
        _http_error(
            status_code=502,
            reason="Bad Gateway",
            body="Upstream rejected the request; status=failed detail=useful page=2",
        )
    )
    assert message.endswith(
        ": Upstream rejected the request; status=failed detail=useful page=2"
    )


def test_format_http_error_redacts_credential_assignment_regardless_of_value() -> None:
    # A field explicitly named ``credential`` loses its value whether or not the
    # value looks opaque: gating on "does this look like a secret?" would let a
    # short or wordlike credential (``token: hunter2``) through. The field name is
    # kept, so the message still says which field the backend objected to.
    message = format_http_error(
        _http_error(status_code=502, reason="Bad Gateway", body="Invalid credential: invalid")
    )
    assert message.endswith(": Invalid credential: <redacted>")


def test_format_http_error_fails_closed_on_malformed_url() -> None:
    # A URL that urlsplit rejects (invalid IPv6) must fall closed to <redacted>
    # rather than leaking the raw value. Set it directly on the response since
    # requests refuses to prepare such a URL.
    resp = requests.Response()
    resp.status_code = 500
    resp.reason = "Server Error"
    resp._content = b"boom"
    resp.url = "https://[oops/x"
    resp.request = None
    message = format_http_error(requests.HTTPError("500 error", response=resp))
    assert "[oops" not in message
    assert "<redacted>" in message


@pytest.mark.parametrize(
    "url",
    [
        # ``urlsplit`` defers authority validation until the port is read, so
        # these reach the formatter as a live ValueError rather than a parse
        # failure. The authority is dropped; the path stays diagnostic.
        "https://api.test:bad/x",
        "https://user:pw@api.test:99999/x",
        "https://api.test:-1/x",
        "https://[::1]:nope/x",
    ],
)
def test_format_http_error_fails_closed_on_unparsable_port(url: str) -> None:
    resp = requests.Response()
    resp.status_code = 502
    resp.reason = "Bad Gateway"
    resp._content = b"boom"
    resp.url = url
    resp.request = None
    message = format_http_error(requests.HTTPError("502 error", response=resp))
    assert "pw" not in message
    assert "<redacted>" in message


def test_format_http_error_redacts_userinfo_query_and_body_credentials_together() -> None:
    message = format_http_error(
        _http_error(
            status_code=401,
            reason="Unauthorized",
            method="POST",
            url="https://alice:hunter2@api.test/x?access_token=QUERYSECRET",
            body="credential=BODYSECRET",
        )
    )
    assert "alice" not in message
    assert "hunter2" not in message
    assert "QUERYSECRET" not in message
    assert "BODYSECRET" not in message
    assert message == (
        "Tangle API request failed (401 Unauthorized) for "
        "POST https://<redacted>@api.test/x?access_token=<redacted>: credential=<redacted>"
    )


@pytest.mark.parametrize(
    "field",
    [
        "access_token",
        "refresh_token",
        "id_token",
        "sessionToken",
        "accessToken",
        "client_secret",
        "user_credential",
        "tangle_access_token",
        "X-Access-Token",
        "myApiKey",
        "aws_secret_access_key",
        "AwsAccessKeyId",
    ],
)
@pytest.mark.parametrize("separator", ["=", ": ", ":", " = ", '":"'])
def test_format_http_error_redacts_affixed_credential_fields_in_non_json_body(
    field: str, separator: str
) -> None:
    """Prefixed, snake_case, camelCase, and quoted spellings are all covered.

    The field name is recovered by a bounded lookbehind and judged by its trailing
    tokens, so no alternation has to enumerate ``tangle_access_token`` or
    ``sessionToken`` for their values to be cut.
    """

    secret = "s3cretOpaqueValue123"
    message = format_http_error(
        _http_error(status_code=400, reason="Bad Request", body=f"rejected {field}{separator}{secret}")
    )
    assert secret not in message
    assert "<redacted>" in message
    assert field in message


@pytest.mark.parametrize(
    "body",
    [
        "Authorization: Bearer s3cretOpaqueValue123",
        "Authorization: Basic YWxpY2U6aHVudGVyMg==",
        "authorization: bearer s3cretOpaqueValue123",
        "Proxy-Authorization: Bearer s3cretOpaqueValue123",
        "rejected header: Bearer s3cretOpaqueValue123",
        "Digest s3cretOpaqueValue123 was rejected",
    ],
)
def test_format_http_error_redacts_auth_scheme_credentials(body: str) -> None:
    """The scheme name is diagnostic and kept; the credential after it is cut."""

    message = format_http_error(_http_error(status_code=401, reason="Unauthorized", body=body))
    assert "s3cretOpaqueValue123" not in message
    assert "YWxpY2U6aHVudGVyMg==" not in message
    assert "<redacted>" in message


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("Bearer abc", "Bearer <redacted>"),
        ("Bearer a", "Bearer <redacted>"),
        ("Bearer token", "Bearer <redacted>"),
        ("Bearer TOKEN", "Bearer <redacted>"),
        ("Basic secret", "Basic <redacted>"),
        ("Basic Access", "Basic <redacted>"),
        ("Basic credentials", "Basic <redacted>"),
        ("Basic hunter", "Basic <redacted>"),
        ("bearer abc", "bearer <redacted>"),
        ("BASIC secret", "BASIC <redacted>"),
        ("bEaReR x", "bEaReR <redacted>"),
        ("digest word was rejected", "digest <redacted> was rejected"),
        ("Digest word was rejected", "Digest <redacted> was rejected"),
        ("rejected header: Bearer abc", "rejected header: Bearer <redacted>"),
        ("Bearer abc.", "Bearer <redacted>"),
        ("server rejected Basic c2VjcmV0, retry later", "server rejected Basic <redacted>, retry later"),
        ("Negotiate opaquevalue", "Negotiate <redacted>"),
        ("Basic authentication failed", "Basic <redacted> failed"),
        ("Bearer token expired", "Bearer token <redacted>"),
        ("<html><body>Bearer token</body></html>", "<html><body>Bearer <redacted></body></html>"),
        ("<p>Authorization: bearer token</p>", "<p>Authorization: bearer <redacted></p>"),
    ],
)
def test_format_http_error_redacts_short_word_like_scheme_credentials(
    body: str, expected: str
) -> None:
    """An explicit scheme always redacts what follows; value shape is not trusted."""

    message = format_http_error(_http_error(status_code=401, reason="Unauthorized", body=body))
    assert message.endswith(f": {expected}")


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("Bearer Bearer sk", "Bearer Bearer <redacted>"),
        ("Basic Bearer sk", "Basic Bearer <redacted>"),
        ("bearer BASIC sk", "bearer BASIC <redacted>"),
        ("Bearer Bearer Bearer abc", "Bearer Bearer Bearer <redacted>"),
        ("Bearer Basic Digest Negotiate abc", "Bearer Basic Digest Negotiate <redacted>"),
        ("Token Bearer sk", "Token Bearer <redacted>"),
        ("Bearer Token sk", "Bearer Token <redacted>"),
        ("Bearer token s3cretOpaqueValue123", "Bearer token <redacted>"),
        ("Bearer token a", "Bearer token <redacted>"),
        ("Bearer token count", "Bearer token <redacted>"),
        ("bearer TOKEN s3cret", "bearer TOKEN <redacted>"),
        ("oauth Bearer sk", "oauth Bearer <redacted>"),
        ("Basic Bearer jwt", "Basic Bearer <redacted>"),
        ("Basic Bearer jwt eyJhbGciOiJIUzI1NiJ9.p.s", "Basic Bearer jwt <redacted>"),
        ("<p>Bearer token s3cret</p>", "<p>Bearer token <redacted></p>"),
        (
            "Authorization: Bearer token s3cretOpaqueValue123",
            "Authorization: Bearer token <redacted>",
        ),
        ("Token Token s3cretOpaqueValue123", "Token Token <redacted>"),
        ("Authorization: Basic Bearer sk", "Authorization: Basic Bearer <redacted>"),
        ("rejected: bEaReR bAsIc sk", "rejected: bEaReR bAsIc <redacted>"),
        ("<p>Bearer Bearer sk</p>", "<p>Bearer Bearer <redacted></p>"),
        (
            "server said Basic Bearer sk, retry later",
            "server said Basic Bearer <redacted>, retry later",
        ),
    ],
)
def test_format_http_error_redacts_credentials_after_chained_schemes(
    body: str, expected: str
) -> None:
    """A doubled scheme cannot shield the credential from a non-overlapping scan."""

    message = format_http_error(_http_error(status_code=401, reason="Unauthorized", body=body))
    assert message.endswith(f": {expected}")


@pytest.mark.parametrize("depth", [1, 2, 3, 5, 8, 13, 20, 50, 100])
@pytest.mark.parametrize("link", ["Bearer ", "Bearer token ", "bAsIc BEARER "])
def test_format_http_error_redacts_credential_after_any_chain_depth(
    depth: int, link: str
) -> None:
    chain = link * depth
    message = format_http_error(
        _http_error(status_code=401, reason="Unauthorized", body=f"{chain}s3cretOpaqueValue123")
    )
    assert "s3cretOpaqueValue123" not in message
    assert message.endswith(f": {chain}<redacted>")


@pytest.mark.parametrize(
    ("leaf", "expected"),
    [
        ("Basic Bearer sk", "Basic Bearer <redacted>"),
        ("Bearer token s3cretOpaqueValue123", "Bearer token <redacted>"),
    ],
)
def test_format_http_error_redacts_chained_schemes_in_json_leaves(
    leaf: str, expected: str
) -> None:
    message = format_http_error(
        _http_error(status_code=401, reason="Unauthorized", body=json.dumps({"detail": leaf}))
    )
    assert expected in message
    assert " sk" not in message
    assert "s3cretOpaqueValue123" not in message


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("Bearer\ns3cretOpaqueValue123", "Bearer <redacted>"),
        ("Bearer\ts3cretOpaqueValue123", "Bearer <redacted>"),
        ("Bearer\r\ns3cretOpaqueValue123", "Bearer <redacted>"),
        ("Bearer\nBearer\ns3cretOpaqueValue123", "Bearer Bearer <redacted>"),
        ("Bearer\ntoken\ns3cretOpaqueValue123", "Bearer token <redacted>"),
        ("Authorization:\nBearer\ns3cretOpaqueValue123", "Authorization: Bearer <redacted>"),
        (
            "Authorization:\nBearer\ntoken\ns3cretOpaqueValue123",
            "Authorization: Bearer token <redacted>",
        ),
        ("password:\nhunter2secret", "password: <redacted>"),
        ("access_token =\n s3cretOpaqueValue123", "access_token = <redacted>"),
    ],
)
def test_format_http_error_redacts_across_newline_separators(body: str, expected: str) -> None:
    """Display collapses whitespace after redaction; a newline must not hide a value."""

    message = format_http_error(_http_error(status_code=401, reason="Unauthorized", body=body))
    assert "s3cretOpaqueValue123" not in message
    assert "hunter2secret" not in message
    assert message.endswith(f": {expected}")


@pytest.mark.parametrize(
    "payload",
    [
        {"detail": "Bearer token"},
        {"detail": "basic Access"},
        {"errors": [{"message": "rejected: Bearer TOKEN"}]},
    ],
)
def test_format_http_error_redacts_word_like_scheme_values_in_json_leaves(
    payload: dict[str, Any],
) -> None:
    message = format_http_error(
        _http_error(status_code=401, reason="Unauthorized", body=json.dumps(payload))
    )
    assert "<redacted>" in message
    assert "token" not in message.lower().split(": ", 1)[1]
    assert "access" not in message.lower()


@pytest.mark.parametrize(
    "body",
    [
        'Basic realm="api"',
        "Bearer realm=api",
        'basic realm="api"',
        'BEARER realm="api"',
        'Bearer realm="example", error="invalid_token", error_description="expired"',
        'Digest realm="tangle", qop="auth", algorithm=MD5, nonce="f2a9"',
        'OAuth realm="Example"',
        "Bearer Bearer realm=api",
        "Bearer token realm=api",
        'Basic Digest realm="api"',
    ],
)
def test_format_http_error_keeps_auth_challenge_parameters(body: str) -> None:
    """A ``WWW-Authenticate`` challenge carries directives, not a credential."""

    message = format_http_error(_http_error(status_code=401, reason="Unauthorized", body=body))
    assert message.endswith(f": {body}")
    assert "<redacted>" not in message


@pytest.mark.parametrize(
    "payload",
    [
        {"detail": "invalid token=s3cretOpaqueValue123 supplied"},
        {"message": "Authorization: Bearer s3cretOpaqueValue123"},
        {"detail": ["access_token=s3cretOpaqueValue123"]},
        {"error": {"message": "password: s3cretOpaqueValue123"}},
        {"errors": [{"detail": "client_secret=s3cretOpaqueValue123"}]},
        {"detail": "password=//s3cretOpaqueValue123"},
        {"error": {"message": "token: //s3cretOpaqueValue123"}},
    ],
)
def test_format_http_error_redacts_sensitive_assignments_in_json_string_leaves(
    payload: dict[str, Any],
) -> None:
    """A harmless outer key can still quote a credential back at us."""

    message = format_http_error(
        _http_error(status_code=400, reason="Bad Request", body=json.dumps(payload))
    )
    assert "s3cretOpaqueValue123" not in message
    assert "<redacted>" in message


@pytest.mark.parametrize(
    "body",
    [
        "password=//s3cretOpaqueValue123",
        "client_secret=//czNjcmV0T3BhcXVlYjY0dg==",
        "token: //s3cretOpaqueValue123",
        "password=/s3cretOpaqueValue123",
        '{"password": "//s3cretOpaqueValue123',
        "a=1&password=//s3cretOpaqueValue123&b=2",
        "<p>password=//s3cretOpaqueValue123</p>",
        "token://s3cretOpaqueValue123",
    ],
)
def test_format_http_error_redacts_slash_prefixed_credential_values(body: str) -> None:
    """A sensitive field's value is not exempted by beginning with slashes."""

    message = format_http_error(_http_error(status_code=400, reason="Bad Request", body=body))
    assert "s3cretOpaqueValue123" not in message
    assert "czNjcmV0" not in message
    assert "<redacted>" in message


@pytest.mark.parametrize(
    "body",
    [
        "see https://api.test/callback ok",
        "endpoint=https://api.test/path retry",
        "docs: https://api.test/help",
        "path: /var/log/app.log",
        "ratio: 1//2 of requests",
    ],
)
def test_format_http_error_keeps_non_sensitive_slash_assignments(body: str) -> None:
    message = format_http_error(_http_error(status_code=400, reason="Bad Request", body=body))
    assert message.endswith(f": {body}")
    assert "<redacted>" not in message


@pytest.mark.parametrize(
    "body, kept",
    [
        ("see https://api.test/cb?access_token=s3cretOpaqueValue123", "api.test/cb"),
        ("see https://alice:s3cretOpaqueValue123@api.test/cb", "api.test/cb"),
        ('{"detail": "https://api.test/cb?api_key=s3cretOpaqueValue123"}', "api.test/cb"),
        ('{"detail": "https://alice:s3cretOpaqueValue123@api.test/cb"}', "api.test/cb"),
        ('<a href="https://api.test/c?api_key=s3cretOpaqueValue123">x</a>', "api.test/c"),
        ("redirect to /cb#access_token=s3cretOpaqueValue123&token_type=Bearer", "/cb"),
        ("connect alice:s3cretOpaqueValue123@db.internal failed", "db.internal"),
    ],
)
def test_format_http_error_redacts_urls_reflected_in_body(body: str, kept: str) -> None:
    """A credential reflected inside a URL in the body loses only the credential.

    The scheme, host, and path survive, because "wrong host" and "expired grant"
    are different failures and the message has to tell them apart.
    """

    message = format_http_error(_http_error(status_code=400, reason="Bad Request", body=body))
    assert "s3cretOpaqueValue123" not in message
    assert kept in message


def test_format_http_error_keeps_host_and_path_of_presigned_url_field() -> None:
    """A ``signed_url`` field is scrubbed structurally rather than dropped whole."""

    payload = {
        "presigned_url": (
            "https://bucket.s3.amazonaws.com/obj?X-Amz-Signature=DEADBEEFSIG"
            "&X-Amz-Expires=900&X-Amz-Date=20240101T000000Z"
        )
    }
    message = format_http_error(
        _http_error(status_code=403, reason="Forbidden", body=json.dumps(payload))
    )
    assert "DEADBEEFSIG" not in message
    assert "bucket.s3.amazonaws.com/obj" in message
    assert "X-Amz-Expires=900" in message
    assert "X-Amz-Date=20240101T000000Z" in message


@pytest.mark.parametrize(
    "query",
    [
        # SigV4 parameters that are not the credential stay readable, because an
        # expired or misdated link is diagnosed from exactly these.
        "X-Amz-Date=20240101T000000Z",
        "X-Amz-Expires=900",
        "X-Amz-Algorithm=AWS4-HMAC-SHA256",
        "X-Amz-SignedHeaders=host",
        # Field names that merely contain a credential word as a non-final token.
        "max_tokens=500",
        "tokenizer=gpt2",
        "token_count=17",
        "tokens_used=42",
        "function_signature=fn",
        "signatureVersion=4",
        "access_key_id_format=hex",
        "password_policy=strict",
        "private_key_path=etc-k.pem",
        "secretaryEmail=alice",
        "session_id=abc123",
        "requestId=r-1",
        "keyboard=qwerty",
    ],
)
def test_format_http_error_does_not_over_redact_diagnostic_query_keys(query: str) -> None:
    message = format_http_error(
        _http_error(status_code=400, reason="Bad Request", url=f"https://api.test/x?{query}", body="nope")
    )
    assert query in message
    assert "<redacted>" not in message


@pytest.mark.parametrize(
    "body",
    [
        "max_tokens=500 exceeds the model limit",
        "tokenizer=gpt2 is unsupported",
        "function_signature=fn(a,b) is invalid",
        "token_count=17 below minimum",
        "Invalid operation id",
        "Token count exceeded",
        "token count: 42 exceeds the limit",
        "Token 12345 expired",
        "Supported schemes: Bearer, Basic, and Digest",
        "max tokens exceeded",
        "Token Token count",
        "token oauth flow enabled",
    ],
)
def test_format_http_error_does_not_over_redact_diagnostic_prose(body: str) -> None:
    message = format_http_error(_http_error(status_code=400, reason="Bad Request", body=body))
    assert message.endswith(f": {body}")


@pytest.mark.parametrize(
    "body_factory",
    [
        pytest.param(
            lambda pad, secret: json.dumps({"access_token": secret, "pad": pad}), id="json"
        ),
        pytest.param(lambda pad, secret: f"{pad}&access_token={secret}", id="form"),
        pytest.param(lambda pad, secret: f"{pad} access_token={secret}", id="text"),
    ],
)
def test_format_http_error_redacts_before_truncation(body_factory: Any) -> None:
    """Redaction runs on the whole body, not on the surviving prefix.

    The secret is long enough that truncating first would keep several hundred of
    its characters, and the pad is sized so that once the assignment collapses to
    ``<redacted>`` the message no longer needs truncating at all. Truncate-first
    therefore leaks and also loses the placeholder.
    """

    secret = "S3cret" + "x" * 400
    pad = "detail=useful " * 135
    body = body_factory(pad, secret)
    assert len(body) > _HTTP_ERROR_BODY_LIMIT
    message = format_http_error(_http_error(status_code=500, reason="Server Error", body=body))
    assert secret not in message
    assert "S3cret" not in message
    assert "<redacted>" in message


def test_format_http_error_survives_deeply_nested_json_body() -> None:
    """A few kilobytes of ``[[[[...]]]]`` must not exhaust the interpreter stack.

    Two safe renderings are possible, and which one appears is decided by the
    interpreter rather than by this formatter: CPython's JSON scanner carries its
    own recursion limit, so on some supported versions the body parses and the
    depth-bounded walk replaces the unexamined subtree with its sentinel, while on
    others ``json.loads`` gives up first and the body is scrubbed as text instead.
    Both satisfy the contract that matters -- the call returns, the secret is gone,
    and the message stays one bounded line -- so accepting either keeps this test
    portable across every declared Python version rather than pinning the parser's
    recursion limit.
    """

    body = "[" * 9000 + '"access_token=s3cretOpaqueValue123"' + "]" * 9000
    message = format_http_error(_http_error(status_code=500, reason="Server Error", body=body))
    assert "s3cretOpaqueValue123" not in message
    assert "\n" not in message
    assert "\r" not in message
    _, _, rendered_body = message.partition(": ")
    assert len(rendered_body) <= _HTTP_ERROR_BODY_LIMIT + len("... (truncated)")
    depth_bounded = "nesting too deep" in rendered_body
    text_fallback = rendered_body == "[" * _HTTP_ERROR_BODY_LIMIT + "... (truncated)"
    assert depth_bounded or text_fallback


def test_format_http_error_redacts_every_key_of_a_wide_json_body() -> None:
    payload = {f"tenant{index}_access_token": "s3cretOpaqueValue123" for index in range(5000)}
    message = format_http_error(
        _http_error(status_code=500, reason="Server Error", body=json.dumps(payload))
    )
    assert "s3cretOpaqueValue123" not in message


@pytest.mark.parametrize(
    "body",
    [
        # One unbroken run per shape the scanners anchor on: ``=``/``:``
        # separators, ``@`` userinfo, and ``://`` URL starts. A quadratic scan
        # would not return on these.
        ("token=" + "a" * 40 + " ") * 20000 + "password: s3cretOpaqueValue123",
        "<p>" + "a@" * 100000 + "</p><p>api_key=s3cretOpaqueValue123</p>",
        "x://" * 50000 + " api_key=s3cretOpaqueValue123",
        "a" * 200000 + ":" + "b" * 200000,
        "%" * 100000 + "state=access_token%253Ds3cretOpaqueValue123",
        "Bearer " * 60000 + "s3cretOpaqueValue123",
        "Bearer x " * 30000 + "api_key=s3cretOpaqueValue123",
        "Bearer token " * 30000 + "s3cretOpaqueValue123",
        "token " * 100000 + "api_key=s3cretOpaqueValue123",
    ],
)
def test_format_http_error_stays_linear_on_adversarial_bodies(body: str) -> None:
    import time

    start = time.perf_counter()
    message = format_http_error(_http_error(status_code=500, reason="Server Error", body=body))
    elapsed = time.perf_counter() - start
    # A generous ceiling: these complete in well under a second, so anything
    # near it means a scan went superlinear.
    assert elapsed < 10.0
    assert "s3cretOpaqueValue123" not in message
    assert len(message) < _HTTP_ERROR_BODY_LIMIT + 200


@pytest.mark.parametrize(
    "url",
    [
        # A credential buried under extra percent-encoding layers is not legible
        # to a scan that decodes once, so the parameter fails closed instead.
        "https://api.test/x?state=access_token%253Ds3cretOpaqueValue123",
        "https://api.test/x?next=alice%253As3cretOpaqueValue123%2540host",
        "https://api.test/x?next=https%3A%2F%2Fh%2Fcb%3Faccess_token%3Ds3cretOpaqueValue123",
        "https://api.test/x#access_token=s3cretOpaqueValue123&token_type=Bearer",
    ],
)
def test_format_http_error_redacts_credentials_hidden_in_url_parameters(url: str) -> None:
    message = format_http_error(
        _http_error(status_code=401, reason="Unauthorized", url=url, body="nope")
    )
    assert "s3cretOpaqueValue123" not in message
    assert "api.test/x" in message


# --------------------------------------------------------------------------
# pipeline-runs commands
# --------------------------------------------------------------------------


def test_pipeline_runs_status_renders_http_error_without_traceback(monkeypatch) -> None:
    class RaisingClient:
        base_url = "https://api.test"

        def pipeline_runs_get(self, *args: Any, **kwargs: Any) -> Any:
            raise _http_error(status_code=500, reason="Internal Server Error", body="kaboom")

    monkeypatch.setattr(pipeline_runs_cli, "LazyTangleApiClient", lambda **kwargs: RaisingClient())
    app = cli.build_app()

    with pytest.raises(SystemExit) as exc_info:
        app(["sdk", "pipeline-runs", "status", "missing-run"])

    assert exc_info.value.code == (
        "Tangle API request failed (500 Internal Server Error) for "
        "GET https://api.test/api/pipeline_runs/missing: kaboom"
    )


def test_pipeline_runs_details_preserves_404_execution_fallback(monkeypatch, capsys) -> None:
    """A 404 the client recovers from must not be intercepted by the new catch."""

    from tangle_cli.client import TangleApiClient

    def make_response(payload: Any, status_code: int) -> requests.Response:
        resp = requests.Response()
        resp.status_code = status_code
        resp.reason = "Not Found" if status_code == 404 else "OK"
        resp._content = b"" if payload is None else json.dumps(payload).encode("utf-8")
        if payload is not None:
            resp.headers["Content-Type"] = "application/json"
        resp.request = requests.Request("GET", "https://api.test/x").prepare()
        return resp

    execution_payload = {
        "id": "missing-run",
        "task_spec": {"componentRef": {"spec": {"name": "pipeline"}}},
        "child_task_execution_ids": {},
        "input_artifacts": {},
        "output_artifacts": {},
    }

    class FakeSession:
        def __init__(self) -> None:
            self.responses = [make_response(None, 404), make_response(execution_payload, 200)]

        def request(self, *args: Any, **kwargs: Any) -> requests.Response:
            return self.responses.pop(0)

    real_client = TangleApiClient("https://api.test", session=FakeSession())
    monkeypatch.setattr(pipeline_runs_cli, "LazyTangleApiClient", lambda **kwargs: real_client)
    app = cli.build_app()

    with pytest.raises(SystemExit) as exc_info:
        app(["sdk", "pipeline-runs", "details", "missing-run"])

    assert exc_info.value.code in (0, None)
    payload = json.loads(capsys.readouterr().out)
    assert payload["run"]["id"] == "missing-run"


def test_pipeline_runs_submit_renders_http_error_without_traceback(monkeypatch, tmp_path: Path) -> None:
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        yaml.safe_dump({"name": "Demo", "implementation": {"graph": {"tasks": {}}}}),
        encoding="utf-8",
    )

    class RaisingClient:
        base_url = "https://api.test"

        def pipeline_runs_create(self, body: Any = None) -> Any:
            raise _http_error(
                status_code=403,
                reason="Forbidden",
                method="POST",
                url="https://api.test/api/pipeline_runs",
                body="denied",
            )

    monkeypatch.setattr(pipeline_runs_cli, "LazyTangleApiClient", lambda **kwargs: RaisingClient())
    app = cli.build_app()

    with pytest.raises(SystemExit) as exc_info:
        app(
            [
                "sdk",
                "pipeline-runs",
                "submit",
                str(pipeline_path),
                "--no-hydrate",
                "--submit-recovery-attempts",
                "0",
            ]
        )

    assert exc_info.value.code == (
        "Tangle API request failed (403 Forbidden) for POST https://api.test/api/pipeline_runs: denied"
    )


def test_submit_error_hook_receives_pipeline_run_error_with_http_cause() -> None:
    class RaisingClient:
        def pipeline_runs_create(self, body: Any = None) -> Any:
            raise _http_error(status_code=500, reason="Internal Server Error", body="kaboom")

    errors: list[Exception] = []

    class Hooks(PipelineRunHooks):
        def on_submit_error(self, error: Exception, *, context: Any) -> None:
            errors.append(error)

    manager = PipelineRunManager(client=RaisingClient(), hooks=Hooks())

    with pytest.raises(PipelineRunError, match="kaboom"):
        manager.submit_pipeline_spec(
            {"name": "Explodes", "implementation": {"graph": {"tasks": {}}}},
            hydrate=False,
        )

    assert len(errors) == 1
    assert isinstance(errors[0], PipelineRunError)
    assert isinstance(errors[0].__cause__, requests.HTTPError)


def test_pipeline_runs_annotations_list_renders_http_error_without_traceback(monkeypatch) -> None:
    class RaisingClient:
        base_url = "https://api.test"

        def pipeline_runs_annotations(self, id: str) -> Any:
            raise _http_error(
                status_code=500,
                reason="Internal Server Error",
                url="https://api.test/api/pipeline_runs/run-1/annotations",
                body="kaboom",
            )

    monkeypatch.setattr(pipeline_runs_cli, "LazyTangleApiClient", lambda **kwargs: RaisingClient())
    app = cli.build_app()

    with pytest.raises(SystemExit) as exc_info:
        app(["sdk", "pipeline-runs", "annotations", "list", "run-1"])

    assert exc_info.value.code == (
        "Tangle API request failed (500 Internal Server Error) for "
        "GET https://api.test/api/pipeline_runs/run-1/annotations: kaboom"
    )


def test_pipeline_runs_annotations_set_renders_http_error_without_traceback(monkeypatch) -> None:
    class RaisingClient:
        base_url = "https://api.test"

        def pipeline_runs_put_annotations(self, id: str, key: str, value: Any = None) -> None:
            raise _http_error(
                status_code=409,
                reason="Conflict",
                method="PUT",
                url="https://api.test/api/pipeline_runs/run-1/annotations/owner",
                body="conflict",
            )

    monkeypatch.setattr(pipeline_runs_cli, "LazyTangleApiClient", lambda **kwargs: RaisingClient())
    app = cli.build_app()

    with pytest.raises(SystemExit) as exc_info:
        app(["sdk", "pipeline-runs", "annotations", "set", "run-1", "owner", "bob"])

    assert exc_info.value.code == (
        "Tangle API request failed (409 Conflict) for "
        "PUT https://api.test/api/pipeline_runs/run-1/annotations/owner: conflict"
    )


def test_graph_state_output_reports_formatted_http_error_per_run() -> None:
    class RaisingClient:
        def pipeline_runs_get(self, run_id: str) -> Any:
            raise _http_error(
                status_code=500,
                reason="Internal Server Error",
                url="https://api.test/api/pipeline_runs/run-1",
                body="kaboom",
            )

    result = PipelineRunDetails(client=RaisingClient()).get_graph_state_output(["run-1"])

    assert result["results"][0]["error"] == (
        "Tangle API request failed (500 Internal Server Error) for "
        "GET https://api.test/api/pipeline_runs/run-1: kaboom"
    )
