from types import SimpleNamespace

import pytest

from tangle_cli.api_transport import build_operation_request, default_base_url


def _operation(path: str) -> SimpleNamespace:
    return SimpleNamespace(
        method="GET",
        path=path,
        parameters=[],
        group_name="test",
        command_name="op",
        has_request_body=False,
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
