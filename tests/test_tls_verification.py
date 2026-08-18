"""Tests for centralized TLS verification configuration across transports.

Covers environment parsing, explicit precedence, path handling, preservation of
caller-supplied session settings, and propagation into the requests client, the
httpx schema/operation transport, and the dynamic-discovery client. A real
local HTTPS server with a generated private CA exercises the end-to-end
behavior of the secure default, ``TANGLE_API_CA_BUNDLE``, and
``TANGLE_API_VERIFY_TLS=0``.
"""

from __future__ import annotations

import http.server
import json
import os
import shutil
import ssl
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

import httpx
import pytest
import requests

from tangle_cli import cli
from tangle_cli.api_cli import _api_argv_tail
from tangle_cli.api_schema import fetch_schema
from tangle_cli.api_transport import (
    _VERIFY_UNSET,
    configure_cli_verify,
    httpx_verify,
    request_operation,
    resolve_verify,
    resolve_verify_default,
)
from tangle_cli.client import TangleApiClient
from tangle_cli.dynamic_discovery_client import TangleDynamicDiscoveryClient

_TLS_ENV_VARS = ("TANGLE_API_VERIFY_TLS", "TANGLE_API_CA_BUNDLE")


@pytest.fixture(autouse=True)
def _clear_tls_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in _TLS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    configure_cli_verify()  # ensure no leaked process-wide CLI override
    yield
    configure_cli_verify()


# --------------------------------------------------------------------------- #
# Environment parsing and precedence
# --------------------------------------------------------------------------- #


def test_resolve_verify_defaults_to_unset() -> None:
    assert resolve_verify() is _VERIFY_UNSET
    assert resolve_verify_default() is True
    assert httpx_verify() is True


@pytest.mark.parametrize("value", ["0", "false", "FALSE", " no ", "No", "nO"])
def test_verify_tls_false_values_disable_verification(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("TANGLE_API_VERIFY_TLS", value)
    assert resolve_verify() is False
    assert httpx_verify() is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "enabled", "anything"])
def test_verify_tls_other_nonempty_values_keep_verification(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("TANGLE_API_VERIFY_TLS", value)
    assert resolve_verify() is True


def test_empty_verify_tls_is_treated_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TANGLE_API_VERIFY_TLS", "   ")
    assert resolve_verify() is _VERIFY_UNSET


def test_ca_bundle_env_resolves_to_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundle = tmp_path / "ca.pem"
    bundle.write_text("cert", encoding="utf-8")
    monkeypatch.setenv("TANGLE_API_CA_BUNDLE", str(bundle))
    assert resolve_verify() == str(bundle)


def test_empty_ca_bundle_is_treated_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TANGLE_API_CA_BUNDLE", "   ")
    assert resolve_verify() is _VERIFY_UNSET


def test_missing_ca_bundle_fails_early(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TANGLE_API_CA_BUNDLE", "/nonexistent/ca.pem")
    with pytest.raises(SystemExit, match="TANGLE_API_CA_BUNDLE"):
        resolve_verify()


def test_ca_bundle_wins_over_verify_tls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundle = tmp_path / "ca.pem"
    bundle.write_text("cert", encoding="utf-8")
    monkeypatch.setenv("TANGLE_API_CA_BUNDLE", str(bundle))
    monkeypatch.setenv("TANGLE_API_VERIFY_TLS", "0")
    # CA bundle wins and TLS stays verified against the bundle.
    assert resolve_verify() == str(bundle)


def test_explicit_argument_wins_over_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundle = tmp_path / "ca.pem"
    bundle.write_text("cert", encoding="utf-8")
    monkeypatch.setenv("TANGLE_API_CA_BUNDLE", str(bundle))
    assert resolve_verify(False) is False
    assert resolve_verify(True) is True


def test_explicit_pathlike_argument_is_accepted(tmp_path: Path) -> None:
    bundle = tmp_path / "ca.pem"
    bundle.write_text("cert", encoding="utf-8")
    assert resolve_verify(bundle) == str(bundle)


def test_explicit_missing_path_argument_fails_early(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="verify"):
        resolve_verify(str(tmp_path / "missing.pem"))


def test_none_argument_falls_through_to_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TANGLE_API_VERIFY_TLS", "0")
    assert resolve_verify(None) is False


# --------------------------------------------------------------------------- #
# httpx adapter
# --------------------------------------------------------------------------- #


def test_httpx_verify_converts_path_to_ssl_context(tmp_path: Path) -> None:
    ca = _generate_private_ca(tmp_path)
    context = httpx_verify(str(ca.ca_pem))
    assert isinstance(context, ssl.SSLContext)


def test_httpx_verify_passes_booleans_through() -> None:
    assert httpx_verify(True) is True
    assert httpx_verify(False) is False


# --------------------------------------------------------------------------- #
# Transport propagation (mocked)
# --------------------------------------------------------------------------- #


def _operation(path: str, *, method: str = "GET") -> SimpleNamespace:
    return SimpleNamespace(
        method=method,
        path=path,
        parameters=[],
        group_name="test",
        command_name="op",
        has_request_body=False,
    )


def test_request_operation_passes_verify_to_httpx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_request(method: str, url: str, **kwargs: Any) -> httpx.Response:
        captured.update(kwargs)
        return httpx.Response(200, json={}, request=httpx.Request(method, url))

    monkeypatch.setattr("tangle_cli.api_transport.httpx.request", fake_request)
    request_operation(
        _operation("/api/ping"),
        {},
        base_url="https://api.test",
        verify=False,
    )
    assert captured["verify"] is False


def test_request_operation_defaults_to_secure_verify(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_request(method: str, url: str, **kwargs: Any) -> httpx.Response:
        captured.update(kwargs)
        return httpx.Response(200, json={}, request=httpx.Request(method, url))

    monkeypatch.setattr("tangle_cli.api_transport.httpx.request", fake_request)
    request_operation(_operation("/api/ping"), {}, base_url="https://api.test")
    assert captured["verify"] is True


def test_fetch_schema_passes_verify_to_httpx(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_get(url: str, **kwargs: Any) -> httpx.Response:
        captured.update(kwargs)
        return httpx.Response(
            200,
            json={"openapi": "3.1.0", "paths": {}},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr("tangle_cli.api_schema.httpx.get", fake_get)
    fetch_schema("https://api.test", verify=False)
    assert captured["verify"] is False


class _RecordingSession:
    """Minimal session without a ``.verify`` attribute."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        self.calls.append({"method": method, "url": url, **kwargs})
        r = requests.Response()
        r.status_code = 200
        r._content = b"{}"
        r.headers["Content-Type"] = "application/json"
        r.request = requests.Request(method, url).prepare()
        return r


def test_static_client_injects_verify_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TANGLE_API_VERIFY_TLS", "0")
    session = _RecordingSession()
    client = TangleApiClient("https://api.test", session=session)

    client._make_request("GET", "/api/ping")

    assert session.calls[0]["verify"] is False


def test_static_client_preserves_session_when_unset() -> None:
    session = _RecordingSession()
    client = TangleApiClient("https://api.test", session=session)

    client._make_request("GET", "/api/ping")

    # No Tangle TLS setting: do not pass ``verify`` so requests' own
    # session/environment handling (REQUESTS_CA_BUNDLE/CURL_CA_BUNDLE) applies.
    assert "verify" not in session.calls[0]


def test_static_client_explicit_verify_argument_wins() -> None:
    session = _RecordingSession()
    client = TangleApiClient("https://api.test", session=session, verify=False)

    client._make_request("GET", "/api/ping")

    assert session.calls[0]["verify"] is False


# --------------------------------------------------------------------------- #
# Real local HTTPS end-to-end tests with a generated private CA
# --------------------------------------------------------------------------- #


class _GeneratedCa(SimpleNamespace):
    ca_pem: Path
    server_crt: Path
    server_key: Path


def _generate_private_ca(directory: Path) -> _GeneratedCa:
    """Generate a private CA and a localhost server cert using openssl."""

    ca_key = directory / "ca.key"
    ca_pem = directory / "ca.pem"
    server_key = directory / "server.key"
    server_csr = directory / "server.csr"
    server_crt = directory / "server.crt"
    ext = directory / "ext.cnf"
    # Python 3.13's TLS stack rejects a leaf missing an Authority Key Identifier
    # ("Missing Authority Key Identifier"), so the leaf must carry the full,
    # standards-conformant extension set (AKI/SKI, CA:FALSE, key usage, and the
    # server-auth EKU) rather than only a SAN. This keeps the CA-bundle success
    # cases valid across every supported interpreter (3.10-3.13), not just 3.12.
    ext.write_text(
        "subjectAltName=DNS:localhost,IP:127.0.0.1\n"
        "basicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature,keyEncipherment\n"
        "extendedKeyUsage=serverAuth\n"
        "subjectKeyIdentifier=hash\n"
        "authorityKeyIdentifier=keyid,issuer\n",
        encoding="utf-8",
    )

    def _run(args: list[str]) -> None:
        subprocess.run(
            args,
            check=True,
            capture_output=True,
        )

    _run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(ca_key), "-out", str(ca_pem),
        "-subj", "/CN=Tangle Test CA", "-days", "2",
        "-addext", "basicConstraints=critical,CA:TRUE",
        "-addext", "keyUsage=critical,keyCertSign,cRLSign",
        # A subject key identifier on the CA lets the leaf's
        # ``authorityKeyIdentifier=keyid`` resolve to it.
        "-addext", "subjectKeyIdentifier=hash",
    ])
    _run([
        "openssl", "req", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(server_key), "-out", str(server_csr),
        "-subj", "/CN=localhost",
    ])
    _run([
        "openssl", "x509", "-req", "-in", str(server_csr),
        "-CA", str(ca_pem), "-CAkey", str(ca_key), "-CAcreateserial",
        "-out", str(server_crt), "-days", "2", "-extfile", str(ext),
    ])
    return _GeneratedCa(ca_pem=ca_pem, server_crt=server_crt, server_key=server_key)


class _SchemaHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        body = json.dumps({"openapi": "3.1.0", "info": {}, "paths": {}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:  # silence test server logging
        pass


@pytest.fixture(scope="module")
def https_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Any]]:
    if shutil.which("openssl") is None:  # pragma: no cover - environment guard
        pytest.skip("openssl is required for the real HTTPS TLS tests")

    directory = tmp_path_factory.mktemp("tls")
    ca = _generate_private_ca(directory)

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=str(ca.server_crt), keyfile=str(ca.server_key))

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _SchemaHandler)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {
            "base_url": f"https://localhost:{port}",
            "ca_pem": str(ca.ca_pem),
        }
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_httpx_schema_default_verification_fails(
    https_server: dict[str, Any],
) -> None:
    with pytest.raises(httpx.ConnectError):
        fetch_schema(https_server["base_url"])


def test_httpx_schema_ca_bundle_succeeds(
    monkeypatch: pytest.MonkeyPatch, https_server: dict[str, Any]
) -> None:
    monkeypatch.setenv("TANGLE_API_CA_BUNDLE", https_server["ca_pem"])
    schema = fetch_schema(https_server["base_url"])
    assert schema["paths"] == {}


def test_httpx_schema_verify_off_succeeds(
    monkeypatch: pytest.MonkeyPatch, https_server: dict[str, Any]
) -> None:
    monkeypatch.setenv("TANGLE_API_VERIFY_TLS", "0")
    schema = fetch_schema(https_server["base_url"])
    assert schema["paths"] == {}


def test_dynamic_client_from_url_uses_ca_bundle(
    monkeypatch: pytest.MonkeyPatch, https_server: dict[str, Any]
) -> None:
    monkeypatch.setenv("TANGLE_API_CA_BUNDLE", https_server["ca_pem"])
    client = TangleDynamicDiscoveryClient.from_url(https_server["base_url"])
    assert client.operations == ()


def test_dynamic_client_from_url_default_verification_fails(
    https_server: dict[str, Any],
) -> None:
    with pytest.raises(httpx.ConnectError):
        TangleDynamicDiscoveryClient.from_url(https_server["base_url"])


def test_requests_client_default_verification_fails(
    https_server: dict[str, Any],
) -> None:
    client = TangleApiClient(https_server["base_url"])
    with pytest.raises(requests.exceptions.SSLError):
        client._make_request("GET", "/api/ping")


def test_requests_client_ca_bundle_succeeds(
    monkeypatch: pytest.MonkeyPatch, https_server: dict[str, Any]
) -> None:
    monkeypatch.setenv("TANGLE_API_CA_BUNDLE", https_server["ca_pem"])
    client = TangleApiClient(https_server["base_url"])
    response = client._make_request("GET", "/api/ping")
    assert response.status_code == 200


def test_requests_client_verify_off_succeeds(
    monkeypatch: pytest.MonkeyPatch, https_server: dict[str, Any]
) -> None:
    monkeypatch.setenv("TANGLE_API_VERIFY_TLS", "0")
    client = TangleApiClient(https_server["base_url"])
    response = client._make_request("GET", "/api/ping")
    assert response.status_code == 200


# --------------------------------------------------------------------------- #
# Global CLI override: configure_cli_verify and resolver precedence
# --------------------------------------------------------------------------- #


def test_cli_override_disables_verification() -> None:
    configure_cli_verify(verify_tls=False)
    assert resolve_verify() is False


def test_cli_override_enables_verification() -> None:
    configure_cli_verify(verify_tls=True)
    assert resolve_verify() is True


def test_cli_override_ca_bundle_resolves_to_path(tmp_path: Path) -> None:
    bundle = tmp_path / "ca.pem"
    bundle.write_text("cert", encoding="utf-8")
    configure_cli_verify(ca_bundle=bundle)
    assert resolve_verify() == str(bundle)


def test_cli_override_wins_over_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TANGLE_API_VERIFY_TLS", "1")
    configure_cli_verify(verify_tls=False)
    assert resolve_verify() is False


def test_cli_ca_bundle_wins_over_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env_bundle = tmp_path / "env.pem"
    env_bundle.write_text("cert", encoding="utf-8")
    cli_bundle = tmp_path / "cli.pem"
    cli_bundle.write_text("cert", encoding="utf-8")
    monkeypatch.setenv("TANGLE_API_CA_BUNDLE", str(env_bundle))
    configure_cli_verify(ca_bundle=cli_bundle)
    assert resolve_verify() == str(cli_bundle)


def test_explicit_python_argument_wins_over_cli_override() -> None:
    configure_cli_verify(verify_tls=False)
    # A library caller's explicit verify= stays highest precedence.
    assert resolve_verify(True) is True


def test_cli_override_conflict_ca_bundle_and_no_verify_fails(tmp_path: Path) -> None:
    bundle = tmp_path / "ca.pem"
    bundle.write_text("cert", encoding="utf-8")
    with pytest.raises(SystemExit, match="ca-bundle"):
        configure_cli_verify(ca_bundle=bundle, verify_tls=False)


def test_cli_override_ca_bundle_and_verify_true_is_accepted(tmp_path: Path) -> None:
    bundle = tmp_path / "ca.pem"
    bundle.write_text("cert", encoding="utf-8")
    configure_cli_verify(ca_bundle=bundle, verify_tls=True)
    assert resolve_verify() == str(bundle)


def test_cli_override_missing_ca_bundle_fails_early() -> None:
    with pytest.raises(SystemExit, match="ca-bundle"):
        configure_cli_verify(ca_bundle="/nonexistent/ca.pem")


def test_cli_override_clears_back_to_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_cli_verify(verify_tls=False)
    assert resolve_verify() is False
    configure_cli_verify()
    assert resolve_verify() is _VERIFY_UNSET


# --------------------------------------------------------------------------- #
# argv pre-parse and placement
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "argv, expected",
    [
        (["tangle", "--no-verify-tls"], False),
        (["tangle", "--verify-tls"], True),
    ],
)
def test_configure_tls_from_argv_parses_boolean_flags(
    argv: list[str], expected: bool
) -> None:
    cli._configure_tls_from_argv(argv)
    assert resolve_verify() is expected


def test_configure_tls_from_argv_parses_ca_bundle(tmp_path: Path) -> None:
    bundle = tmp_path / "ca.pem"
    bundle.write_text("cert", encoding="utf-8")
    cli._configure_tls_from_argv(["tangle", "--ca-bundle", str(bundle), "api", "ping"])
    assert resolve_verify() == str(bundle)


def test_configure_tls_from_argv_parses_ca_bundle_equals(tmp_path: Path) -> None:
    bundle = tmp_path / "ca.pem"
    bundle.write_text("cert", encoding="utf-8")
    cli._configure_tls_from_argv(["tangle", f"--ca-bundle={bundle}", "sdk"])
    assert resolve_verify() == str(bundle)


def test_configure_tls_from_argv_stops_at_subcommand(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TANGLE_API_VERIFY_TLS", "1")
    # A --no-verify-tls after the subcommand is not a global flag: it must not
    # be consumed here, leaving the env default to apply.
    cli._configure_tls_from_argv(["tangle", "api", "ping", "--no-verify-tls"])
    assert resolve_verify() is True


@pytest.mark.parametrize(
    "argv, expected_tail",
    [
        (["tangle", "--no-verify-tls", "api", "ping"], ["ping"]),
        (["tangle", "--ca-bundle", "ca.pem", "api", "foo", "bar"], ["foo", "bar"]),
        (["tangle", "--ca-bundle=ca.pem", "api", "foo"], ["foo"]),
        (["tangle", "--verify-tls", "api"], []),
    ],
)
def test_api_argv_tail_skips_global_tls_flags(
    argv: list[str], expected_tail: list[str]
) -> None:
    assert _api_argv_tail(argv) == expected_tail


# --------------------------------------------------------------------------- #
# Global override propagation into every transport (mocked)
# --------------------------------------------------------------------------- #


def test_cli_override_propagates_to_static_requests_client() -> None:
    configure_cli_verify(verify_tls=False)
    session = _RecordingSession()
    client = TangleApiClient("https://api.test", session=session)
    client._make_request("GET", "/api/ping")
    assert session.calls[0]["verify"] is False


def test_cli_override_propagates_to_request_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_request(method: str, url: str, **kwargs: Any) -> httpx.Response:
        captured.update(kwargs)
        return httpx.Response(200, json={}, request=httpx.Request(method, url))

    monkeypatch.setattr("tangle_cli.api_transport.httpx.request", fake_request)
    configure_cli_verify(verify_tls=False)
    request_operation(_operation("/api/ping"), {}, base_url="https://api.test")
    assert captured["verify"] is False


def test_cli_override_propagates_to_schema_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_get(url: str, **kwargs: Any) -> httpx.Response:
        captured.update(kwargs)
        return httpx.Response(
            200,
            json={"openapi": "3.1.0", "paths": {}},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr("tangle_cli.api_schema.httpx.get", fake_get)
    configure_cli_verify(verify_tls=False)
    fetch_schema("https://api.test")
    assert captured["verify"] is False


# --------------------------------------------------------------------------- #
# Real local HTTPS end-to-end tests driven by the global CLI override
# --------------------------------------------------------------------------- #


def test_cli_override_ca_bundle_schema_fetch_succeeds(
    https_server: dict[str, Any],
) -> None:
    configure_cli_verify(ca_bundle=https_server["ca_pem"])
    schema = fetch_schema(https_server["base_url"])
    assert schema["paths"] == {}


def test_cli_override_verify_off_schema_fetch_succeeds(
    https_server: dict[str, Any],
) -> None:
    configure_cli_verify(verify_tls=False)
    schema = fetch_schema(https_server["base_url"])
    assert schema["paths"] == {}


def test_cli_override_default_schema_fetch_fails(
    https_server: dict[str, Any],
) -> None:
    configure_cli_verify()
    with pytest.raises(httpx.ConnectError):
        fetch_schema(https_server["base_url"])


def test_cli_override_ca_bundle_dynamic_client_succeeds(
    https_server: dict[str, Any],
) -> None:
    configure_cli_verify(ca_bundle=https_server["ca_pem"])
    client = TangleDynamicDiscoveryClient.from_url(https_server["base_url"])
    assert client.operations == ()


def test_cli_override_ca_bundle_requests_client_succeeds(
    https_server: dict[str, Any],
) -> None:
    configure_cli_verify(ca_bundle=https_server["ca_pem"])
    client = TangleApiClient(https_server["base_url"])
    response = client._make_request("GET", "/api/ping")
    assert response.status_code == 200


# --------------------------------------------------------------------------- #
# Live CLI subprocess: global flags reach `tangle api refresh` over real HTTPS
# --------------------------------------------------------------------------- #


def _run_tangle(
    args: list[str], cache_dir: Path, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "TANGLE_CLI_CACHE_DIR": str(cache_dir)}
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from tangle_cli.cli import main; "
            "sys.argv = ['tangle', *sys.argv[1:]]; main()",
            *args,
        ],
        capture_output=True,
        text=True,
        env=env,
    )


def test_cli_subprocess_ca_bundle_refresh_succeeds(
    https_server: dict[str, Any], tmp_path: Path
) -> None:
    result = _run_tangle(
        [
            "--ca-bundle",
            https_server["ca_pem"],
            "api",
            "refresh",
            "--base-url",
            https_server["base_url"],
        ],
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert "Cached OpenAPI schema" in result.stdout


def test_cli_subprocess_default_refresh_fails(
    https_server: dict[str, Any], tmp_path: Path
) -> None:
    result = _run_tangle(
        ["api", "refresh", "--base-url", https_server["base_url"]],
        tmp_path,
    )
    assert result.returncode != 0
    assert "Failed to fetch" in result.stderr


def test_cli_subprocess_verify_off_refresh_succeeds(
    https_server: dict[str, Any], tmp_path: Path
) -> None:
    result = _run_tangle(
        [
            "--no-verify-tls",
            "api",
            "refresh",
            "--base-url",
            https_server["base_url"],
        ],
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert "Cached OpenAPI schema" in result.stdout


def test_cli_subprocess_ca_bundle_and_no_verify_conflict_fails(
    https_server: dict[str, Any], tmp_path: Path
) -> None:
    result = _run_tangle(
        [
            "--ca-bundle",
            https_server["ca_pem"],
            "--no-verify-tls",
            "api",
            "refresh",
            "--base-url",
            https_server["base_url"],
        ],
        tmp_path,
    )
    assert result.returncode != 0
    assert "ca-bundle" in result.stderr


def test_cli_root_help_lists_global_tls_flags(tmp_path: Path) -> None:
    result = _run_tangle(["--help"], tmp_path)
    assert result.returncode == 0
    assert "--ca-bundle" in result.stdout
    assert "--no-verify-tls" in result.stdout
