"""HTTP transport helpers shared by the OpenAPI CLI and programmatic client."""

from __future__ import annotations

import json
import os
import re
import ssl
import sys
import urllib.parse
from pathlib import Path
from typing import Any

import httpx

DEFAULT_API_URL = "http://localhost:8000"
DEFAULT_TIMEOUT_SECONDS = 30.0
_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_MISSING = object()

# Canonical resolved TLS verification value: ``True``/``False`` or a CA bundle
# path. Both requests and httpx transports adapt this single contract.
VerifyValue = bool | str
VerifyArgument = bool | str | os.PathLike[str] | None
_VERIFY_UNSET = object()
_TLS_FALSE_VALUES = frozenset({"0", "false", "no"})

# Process-wide TLS override set from global CLI flags (``--ca-bundle`` /
# ``--verify-tls`` / ``--no-verify-tls``). It sits between an explicit
# ``verify=`` argument and the environment variables in :func:`resolve_verify`,
# so a single resolver serves every transport, including the schema discovery
# that runs before normal command dispatch.
_CLI_VERIFY_OVERRIDE: Any = _VERIFY_UNSET
_SENSITIVE_HEADER_NAMES = {"authorization", "cloud-auth", "cookie", "x-api-key"}
_SENSITIVE_KEY_RE = re.compile(
    r"(authorization|authentication|(^|[-_])auth($|[-_])|cloud[-_]?auth|cookie|x[-_]?api[-_]?key|token|secret|password|credential|pre[-_]?signed[-_]?url|signed[-_]?url)",
    re.IGNORECASE,
)
_REDACTED = "<redacted>"
_REDACTED_DOCUMENT = "<redacted document>"
_OPAQUE_DOCUMENT_KEY_NAMES = {
    "component_yaml",
    "dockerfile",
    "manifest",
    "pipeline_yaml",
    "text",
    "yaml",
}


def tangle_verbose_enabled() -> bool:
    value = os.environ.get("TANGLE_VERBOSE")
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_verify_flag(raw: str) -> bool:
    """Interpret ``TANGLE_API_VERIFY_TLS``.

    Only the case/space-insensitive values ``0``, ``false``, and ``no`` disable
    verification; any other nonempty value keeps it enabled.
    """

    return raw.strip().lower() not in _TLS_FALSE_VALUES


def _validate_ca_bundle(path: str, source: str) -> str:
    """Return an existing CA bundle path or fail before any network request."""

    candidate = Path(path).expanduser()
    if not candidate.is_file():
        raise SystemExit(
            f"{source} points to a CA bundle that does not exist: {path!r}. "
            "Provide a path to an existing PEM file, or unset it to use the "
            "system trust store."
        )
    return str(candidate)


def _coerce_explicit_verify(value: bool | str | os.PathLike[str]) -> VerifyValue:
    if isinstance(value, bool):
        return value
    if isinstance(value, (str, os.PathLike)):
        return _validate_ca_bundle(os.fspath(value), "verify")
    raise SystemExit("verify must be a bool or a path to a CA bundle file")


def configure_cli_verify(
    ca_bundle: str | os.PathLike[str] | None = None,
    verify_tls: bool | None = None,
) -> None:
    """Install (or clear) the process-wide TLS override from global CLI flags.

    ``ca_bundle`` is a path to a PEM trust store; ``verify_tls`` is the tri-state
    ``--verify-tls`` / ``--no-verify-tls`` flag where ``None`` means the flag was
    not supplied. A ``--ca-bundle`` combined with an explicit
    ``--no-verify-tls`` is contradictory and fails fast before any network
    request. Passing neither clears any previously installed override.

    The resolved value is validated here so an invalid or missing CA bundle
    fails before dynamic schema discovery. It is consulted by
    :func:`resolve_verify` for every transport.
    """

    global _CLI_VERIFY_OVERRIDE
    if ca_bundle is not None:
        if verify_tls is False:
            raise SystemExit(
                "--ca-bundle cannot be combined with --no-verify-tls: a CA "
                "bundle only takes effect when verification is enabled. Drop "
                "one of the two flags."
            )
        _CLI_VERIFY_OVERRIDE = _validate_ca_bundle(os.fspath(ca_bundle), "--ca-bundle")
        return
    if verify_tls is not None:
        _CLI_VERIFY_OVERRIDE = bool(verify_tls)
        return
    _CLI_VERIFY_OVERRIDE = _VERIFY_UNSET


def resolve_verify(verify: Any = _VERIFY_UNSET) -> Any:
    """Resolve the effective TLS verification setting.

    Precedence, highest to lowest: an explicit ``verify`` argument, the global
    CLI override (``--ca-bundle`` / ``--verify-tls`` / ``--no-verify-tls``), a
    nonempty ``TANGLE_API_CA_BUNDLE``, ``TANGLE_API_VERIFY_TLS``, then a secure
    enabled default. When none of these are set, ``_VERIFY_UNSET`` is returned
    so callers can preserve library and caller defaults (for example requests'
    ``REQUESTS_CA_BUNDLE``/``CURL_CA_BUNDLE`` handling and a caller-supplied
    ``Session.verify``). Empty environment values are treated as unset.
    """

    if verify is not _VERIFY_UNSET and verify is not None:
        return _coerce_explicit_verify(verify)
    if _CLI_VERIFY_OVERRIDE is not _VERIFY_UNSET:
        return _CLI_VERIFY_OVERRIDE
    ca_bundle = os.environ.get("TANGLE_API_CA_BUNDLE", "")
    if ca_bundle.strip():
        return _validate_ca_bundle(ca_bundle.strip(), "TANGLE_API_CA_BUNDLE")
    flag = os.environ.get("TANGLE_API_VERIFY_TLS", "")
    if flag.strip():
        return _parse_verify_flag(flag)
    return _VERIFY_UNSET


def resolve_verify_default(verify: Any = _VERIFY_UNSET) -> VerifyValue:
    """Like :func:`resolve_verify`, but fall back to the secure ``True`` default."""

    resolved = resolve_verify(verify)
    return True if resolved is _VERIFY_UNSET else resolved


def httpx_verify(verify: Any = _VERIFY_UNSET) -> bool | ssl.SSLContext:
    """Adapt the resolved verify value to what httpx expects.

    httpx 0.28 deprecates string CA-bundle paths, so a path is turned into an
    :class:`ssl.SSLContext`; booleans pass through unchanged.
    """

    resolved = resolve_verify_default(verify)
    if isinstance(resolved, bool):
        return resolved
    return ssl.create_default_context(cafile=resolved)


def _redact_headers(headers: dict[str, Any] | None) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for name, value in (headers or {}).items():
        normalized_name = name.lower()
        redacted[name] = (
            _REDACTED
            if normalized_name in _SENSITIVE_HEADER_NAMES or _SENSITIVE_KEY_RE.search(name)
            else value
        )
    return redacted


def _redact_sensitive_values(value: Any, key: str | None = None) -> Any:
    if key and _SENSITIVE_KEY_RE.search(key):
        return _REDACTED
    if key and key.lower() in _OPAQUE_DOCUMENT_KEY_NAMES and isinstance(value, str) and value:
        return _REDACTED_DOCUMENT
    if isinstance(value, dict):
        return {str(k): _redact_sensitive_values(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_sensitive_values(item) for item in value]
    return value


def _safe_json_text(value: Any) -> str:
    redacted = _redact_sensitive_values(value)
    try:
        return json.dumps(redacted, indent=2, sort_keys=True, default=str)
    except TypeError:
        return str(redacted)


def _content_to_text(content: bytes | str | None) -> str:
    if content is None:
        return "<empty>"
    if isinstance(content, bytes):
        if not content:
            return "<empty>"
        text = content.decode("utf-8", errors="replace")
    else:
        text = content
    if not text:
        return "<empty>"
    try:
        parsed = json.loads(text)
    except Exception:
        return text
    return _safe_json_text(parsed)


def log_http_exchange(
    logger: Any,
    *,
    method: str,
    url: str,
    request_headers: dict[str, Any] | None = None,
    request_body: Any = None,
    response_status: int | None = None,
    response_headers: dict[str, Any] | None = None,
    response_body: bytes | str | None = None,
) -> None:
    """Log a redacted HTTP exchange for TANGLE_VERBOSE diagnostics."""

    emit = getattr(logger, "info", None)
    if not callable(emit):
        emit = lambda message: print(message, file=sys.stderr, flush=True)
    emit(f"[tangle-api] request: {method} {url}")
    emit(f"[tangle-api] request headers: {_safe_json_text(_redact_headers(request_headers))}")
    if isinstance(request_body, (bytes, str)) or request_body is None:
        request_body_text = _content_to_text(request_body)
    else:
        request_body_text = _safe_json_text(request_body)
    emit(f"[tangle-api] request body: {request_body_text}")
    if response_status is not None:
        emit(f"[tangle-api] response status: {response_status}")
    if response_headers is not None:
        emit(f"[tangle-api] response headers: {_safe_json_text(_redact_headers(response_headers))}")
    if response_body is not None:
        emit(f"[tangle-api] response body: {_content_to_text(response_body)}")


def default_base_url() -> str:
    configured_url = os.environ.get("TANGLE_API_URL")
    if configured_url:
        return _normalize_base_url(configured_url)
    if _ambient_auth_env_present():
        raise SystemExit(
            "TANGLE_API_URL is required when Tangle auth environment variables "
            f"are set; refusing to send credentials to default {DEFAULT_API_URL}"
        )
    return _normalize_base_url(DEFAULT_API_URL)


def _ambient_auth_env_present() -> bool:
    return any(
        os.environ.get(name)
        for name in (
            "TANGLE_API_AUTH_HEADER",
            "TANGLE_AUTH_HEADER",
            "TANGLE_API_HEADERS",
            "TANGLE_API_TOKEN",
        )
    )


def default_token() -> str | None:
    return os.environ.get("TANGLE_API_TOKEN") or None


def default_auth_header() -> str | None:
    return os.environ.get("TANGLE_API_AUTH_HEADER") or os.environ.get("TANGLE_AUTH_HEADER") or None


def _normalize_base_url(base_url: str) -> str:
    base_url = base_url.strip().rstrip("/")
    if base_url.endswith("/openapi.json"):
        base_url = base_url[: -len("/openapi.json")]
    return base_url.rstrip("/")


def _openapi_url(base_url: str) -> str:
    base_url = base_url.strip().rstrip("/")
    if base_url.endswith("/openapi.json"):
        return base_url
    return urllib.parse.urljoin(base_url + "/", "openapi.json")


def _request_headers(
    token: str | None,
    cli_header_entries: list[str] | str | None,
    cli_auth_header: str | None,
    extra_headers: dict[str, str] | None = None,
    *,
    include_env_credentials: bool = True,
) -> dict[str, str]:
    """Build request headers without printing or otherwise exposing secrets.

    Precedence, lowest to highest:
    default Accept header, ``TANGLE_API_HEADERS``, auth env vars,
    bearer token, explicit auth header, CLI/header entries, explicit mapping.
    """

    headers = {"Accept": "application/json"}
    if include_env_credentials:
        headers.update(_headers_from_env())
        env_auth_header = default_auth_header()
        if env_auth_header:
            headers["Authorization"] = _normalize_auth_header(
                env_auth_header, "TANGLE_API_AUTH_HEADER"
            )
        token = token or default_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if cli_auth_header:
        headers["Authorization"] = _normalize_auth_header(cli_auth_header, "--auth-header")
    headers.update(_parse_header_entries(_header_entries(cli_header_entries), "--header"))
    if extra_headers:
        for name, value in extra_headers.items():
            _validate_header(name, str(value), "headers")
            headers[name] = str(value)
    return headers


def _normalize_auth_header(raw: str, source: str) -> str:
    """Accept either an Authorization value or ``Authorization: value``."""

    value = raw.strip()
    if value.lower().startswith("authorization:"):
        value = value.split(":", 1)[1].strip()
    if not value or "\n" in value or "\r" in value:
        raise SystemExit(f"Invalid {source}; expected an authorization header value")
    return value


def _headers_from_env() -> dict[str, str]:
    raw = os.environ.get("TANGLE_API_HEADERS")
    if not raw or not raw.strip():
        return {}
    return _parse_header_entries(_env_header_entries(raw), "TANGLE_API_HEADERS")


def _env_header_entries(raw: str) -> list[str]:
    """Parse env headers as JSON object/list or newline-separated entries."""

    raw = raw.strip()
    if raw[0] in "[{":
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SystemExit("Invalid TANGLE_API_HEADERS JSON") from exc
        if isinstance(parsed, dict):
            return [f"{name}: {value}" for name, value in parsed.items()]
        if isinstance(parsed, list) and all(isinstance(item, str) for item in parsed):
            return parsed
        raise SystemExit("TANGLE_API_HEADERS must be a JSON object or string list")
    return [line.strip() for line in raw.splitlines() if line.strip()]


def _header_entries(entries: list[str] | str | None) -> list[str]:
    if entries is None:
        return []
    if isinstance(entries, str):
        return [entries]
    return list(entries)


def _parse_header_entries(entries: list[str], source: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for entry in entries:
        if ":" in entry:
            name, value = entry.split(":", 1)
        elif "=" in entry:
            name, value = entry.split("=", 1)
        else:
            raise SystemExit(f"Invalid {source} entry; expected 'Name: value'")
        name = name.strip()
        value = value.strip()
        _validate_header(name, value, source)
        headers[name] = value
    return headers


def _validate_header(name: str, value: str, source: str) -> None:
    if not name or not _HEADER_NAME_RE.fullmatch(name) or "\n" in value or "\r" in value:
        raise SystemExit(f"Invalid {source} header name or value")


def request_operation(
    operation: Any,
    values: dict[str, Any],
    *,
    base_url: str | None = None,
    token: str | None = None,
    auth_header: str | None = None,
    header_entries: list[str] | str | None = None,
    headers: dict[str, str] | None = None,
    body: Any = _MISSING,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    allow_body_file_references: bool = False,
    include_env_credentials: bool = True,
    verify: Any = _VERIFY_UNSET,
) -> httpx.Response:
    """Dispatch one normalized OpenAPI operation as an HTTP request.

    ``values`` contains operation params using either generated Python names or
    original OpenAPI names. The returned response has already had
    ``raise_for_status()`` applied, matching the generated CLI behavior.
    """

    method, url, request_headers, content = build_operation_request(
        operation,
        values,
        base_url=base_url,
        token=token,
        auth_header=auth_header,
        header_entries=header_entries,
        headers=headers,
        body=body,
        allow_body_file_references=allow_body_file_references,
        include_env_credentials=include_env_credentials,
    )
    response = httpx.request(
        method,
        url,
        content=content,
        headers=request_headers,
        timeout=timeout,
        verify=httpx_verify(verify),
    )
    if tangle_verbose_enabled():
        log_http_exchange(
            None,
            method=method,
            url=url,
            request_headers=request_headers,
            request_body=content,
            response_status=response.status_code,
            response_headers=dict(response.headers),
            response_body=response.text,
        )
    response.raise_for_status()
    return response


def build_operation_request(
    operation: Any,
    values: dict[str, Any],
    *,
    base_url: str | None = None,
    token: str | None = None,
    auth_header: str | None = None,
    header_entries: list[str] | str | None = None,
    headers: dict[str, str] | None = None,
    body: Any = _MISSING,
    allow_body_file_references: bool = False,
    include_env_credentials: bool = True,
) -> tuple[str, str, dict[str, str], bytes | None]:
    """Build method, URL, headers, and body bytes for an operation."""

    base_url = _normalize_base_url(base_url or default_base_url())
    path = operation.path
    query: dict[str, Any] = {}
    body_fields: dict[str, Any] = {}
    remaining = dict(values)

    for parameter in operation.parameters:
        if parameter.local_name in remaining:
            value = remaining.pop(parameter.local_name)
        elif parameter.original_name in remaining:
            value = remaining.pop(parameter.original_name)
        else:
            if parameter.location == "path" and parameter.required:
                raise TypeError(f"Missing required path parameter: {parameter.local_name}")
            if parameter.location in {"query", "body"} and parameter.required:
                # A required body field can also be satisfied by the generic body.
                if parameter.location == "body" and body is not _MISSING and body is not None:
                    continue
                raise TypeError(f"Missing required parameter: {parameter.local_name}")
            continue
        if value is None:
            continue
        if parameter.location == "path":
            path = path.replace(
                "{" + parameter.original_name + "}",
                urllib.parse.quote(str(value), safe=""),
            )
        elif parameter.location == "query":
            query[parameter.original_name] = value
        elif parameter.location == "body":
            body_fields[parameter.original_name] = value

    if remaining:
        names = ", ".join(sorted(remaining))
        raise TypeError(f"Unexpected parameter(s) for {operation.group_name}.{operation.command_name}: {names}")

    url = _join_operation_url(base_url, path)
    if query:
        url = f"{url}?{_urlencode_query(query)}"

    request_body = None
    if operation.has_request_body:
        if body is _MISSING:
            body = None
        request_body = (
            _coerce_body_argument(
                body, allow_file_references=allow_body_file_references
            )
            if body is not None
            else None
        )
    if body_fields:
        if request_body is None:
            request_body = {}
        if not isinstance(request_body, dict):
            raise TypeError("body must be a JSON object when body field parameters are used")
        request_body.update(body_fields)

    request_headers = _request_headers(
        token,
        header_entries,
        auth_header,
        headers,
        include_env_credentials=include_env_credentials,
    )
    content = _body_to_content(request_body)
    if content is not None and "Content-Type" not in request_headers:
        request_headers["Content-Type"] = "application/json"
    return operation.method, url, request_headers, content


def _join_operation_url(base_url: str, path: str) -> str:
    """Join a schema path to ``base_url`` without allowing origin changes."""

    parsed_path = urllib.parse.urlparse(path)
    if parsed_path.scheme or parsed_path.netloc:
        raise ValueError(f"OpenAPI operation path must be relative: {path!r}")
    return urllib.parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))


def _urlencode_query(query: dict[str, Any]) -> str:
    """Encode query params, preserving repeated values for list options."""

    items: list[tuple[str, Any]] = []
    for key, value in query.items():
        if isinstance(value, (list, tuple)):
            items.extend((key, item) for item in value)
        else:
            items.append((key, value))
    return urllib.parse.urlencode(items, doseq=True)


def _load_body_argument(body: str) -> Any:
    """Parse a CLI ``--body`` value; leading ``@`` reads JSON from a file."""

    if body.startswith("@"):
        body = Path(body[1:]).expanduser().read_text(encoding="utf-8")
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON body: {exc}") from exc


def _coerce_body_argument(body: Any, *, allow_file_references: bool = False) -> Any:
    if not isinstance(body, str):
        return body
    if allow_file_references:
        return _load_body_argument(body)
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return body


def _body_to_content(request_body: Any) -> bytes | None:
    if request_body is None:
        return None
    if isinstance(request_body, bytes):
        return request_body
    if isinstance(request_body, bytearray):
        return bytes(request_body)
    return json.dumps(request_body).encode("utf-8")
