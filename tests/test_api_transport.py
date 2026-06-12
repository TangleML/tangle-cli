from types import SimpleNamespace

import pytest

from tangle_cli.api_transport import build_operation_request


def _operation(path: str) -> SimpleNamespace:
    return SimpleNamespace(
        method="GET",
        path=path,
        parameters=[],
        group_name="test",
        command_name="op",
        has_request_body=False,
    )


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
