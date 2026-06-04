from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.published_component_response import PublishedComponentResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    digest: str,
    *,
    deprecated: bool | None | Unset = UNSET,
    superseded_by: None | str | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_deprecated: bool | None | Unset
    if isinstance(deprecated, Unset):
        json_deprecated = UNSET
    else:
        json_deprecated = deprecated
    params["deprecated"] = json_deprecated

    json_superseded_by: None | str | Unset
    if isinstance(superseded_by, Unset):
        json_superseded_by = UNSET
    else:
        json_superseded_by = superseded_by
    params["superseded_by"] = json_superseded_by

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "put",
        "url": "/api/published_components/{digest}".format(
            digest=quote(str(digest), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | PublishedComponentResponse | None:
    if response.status_code == 200:
        response_200 = PublishedComponentResponse.from_dict(response.json())

        return response_200

    if response.status_code == 422:
        response_422 = HTTPValidationError.from_dict(response.json())

        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[HTTPValidationError | PublishedComponentResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    digest: str,
    *,
    client: AuthenticatedClient | Client,
    deprecated: bool | None | Unset = UNSET,
    superseded_by: None | str | Unset = UNSET,
) -> Response[HTTPValidationError | PublishedComponentResponse]:
    """Update

    Args:
        digest (str):
        deprecated (bool | None | Unset):
        superseded_by (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | PublishedComponentResponse]
    """

    kwargs = _get_kwargs(
        digest=digest,
        deprecated=deprecated,
        superseded_by=superseded_by,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    digest: str,
    *,
    client: AuthenticatedClient | Client,
    deprecated: bool | None | Unset = UNSET,
    superseded_by: None | str | Unset = UNSET,
) -> HTTPValidationError | PublishedComponentResponse | None:
    """Update

    Args:
        digest (str):
        deprecated (bool | None | Unset):
        superseded_by (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | PublishedComponentResponse
    """

    return sync_detailed(
        digest=digest,
        client=client,
        deprecated=deprecated,
        superseded_by=superseded_by,
    ).parsed


async def asyncio_detailed(
    digest: str,
    *,
    client: AuthenticatedClient | Client,
    deprecated: bool | None | Unset = UNSET,
    superseded_by: None | str | Unset = UNSET,
) -> Response[HTTPValidationError | PublishedComponentResponse]:
    """Update

    Args:
        digest (str):
        deprecated (bool | None | Unset):
        superseded_by (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | PublishedComponentResponse]
    """

    kwargs = _get_kwargs(
        digest=digest,
        deprecated=deprecated,
        superseded_by=superseded_by,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    digest: str,
    *,
    client: AuthenticatedClient | Client,
    deprecated: bool | None | Unset = UNSET,
    superseded_by: None | str | Unset = UNSET,
) -> HTTPValidationError | PublishedComponentResponse | None:
    """Update

    Args:
        digest (str):
        deprecated (bool | None | Unset):
        superseded_by (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | PublishedComponentResponse
    """

    return (
        await asyncio_detailed(
            digest=digest,
            client=client,
            deprecated=deprecated,
            superseded_by=superseded_by,
        )
    ).parsed
