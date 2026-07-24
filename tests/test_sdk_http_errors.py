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
    assert message.startswith(
        "Tangle API request failed (401 Unauthorized) for GET https://api.test/x?"
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
    # Sensitive words without a key=value/key: value assignment, and non-sensitive
    # assignments, stay intact so diagnostics remain useful.
    message = format_http_error(
        _http_error(
            status_code=502,
            reason="Bad Gateway",
            body="Invalid credential: authentication required; status=failed detail=useful page=2",
        )
    )
    assert message.endswith(
        ": Invalid credential: authentication required; status=failed detail=useful page=2"
    )


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
