"""HTTP transport helpers shared by the OpenAPI CLI and programmatic client."""

from __future__ import annotations

import json
import os
import re
import urllib.parse
from pathlib import Path
from typing import Any

import httpx

DEFAULT_API_URL = "http://localhost:8000"
DEFAULT_TIMEOUT_SECONDS = 30.0
_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_MISSING = object()


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
