"""Shared Cyclopts option annotations for Tangle CLI commands."""

from __future__ import annotations

from typing import Annotated

from cyclopts import Parameter

from .api_transport import DEFAULT_API_URL

BaseUrlOption = Annotated[
    str | None,
    Parameter(
        help=(
            "Tangle API base URL. Defaults to TANGLE_API_URL, then "
            f"{DEFAULT_API_URL}."
        )
    ),
]
TokenOption = Annotated[
    str | None,
    Parameter(help="Bearer token. Defaults to TANGLE_API_TOKEN."),
]
AuthHeaderOption = Annotated[
    str | None,
    Parameter(
        help=(
            "Authorization header value, e.g. 'Bearer TOKEN' or 'Basic BASE64'. "
            "Defaults to TANGLE_API_AUTH_HEADER or TANGLE_AUTH_HEADER."
        )
    ),
]
HeaderOption = Annotated[
    list[str] | None,
    Parameter(
        name="--header",
        alias="-H",
        help=(
            "Custom request header as 'Name: value'. Repeat for multiple. "
            "Applied after TANGLE_API_HEADERS."
        ),
        negative_iterable=(),
    ),
]
ConfigOption = Annotated[
    str | None,
    Parameter(help="YAML/JSON config file providing command defaults."),
]
LogTypeOption = Annotated[
    str,
    Parameter(help="Log output: console, none, file."),
]
