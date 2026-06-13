from types import SimpleNamespace

import httpx
import pytest

from tangle_cli.api_transport import (
    build_operation_request,
    default_base_url,
    request_operation,
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


def test_request_operation_verbose_env_logs_redacted_body(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("TANGLE_VERBOSE", "1")

    def fake_request(*args, **kwargs):
        return httpx.Response(
            200,
            json={"id": "run-1", "secret": "response-secret"},
            headers={"X-Api-Key": "response-key"},
            request=httpx.Request("POST", "https://api.test/api/pipeline_runs/"),
        )

    monkeypatch.setattr("tangle_cli.api_transport.httpx.request", fake_request)

    request_operation(
        _operation("/api/pipeline_runs/", method="POST", has_request_body=True),
        {},
        base_url="https://api.test",
        auth_header="Bearer request-secret",
        header_entries=["Cloud-Auth: cloud-secret"],
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
    assert "request-token" not in logs
    assert "response-secret" not in logs
    assert "response-key" not in logs


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
