from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.component_library import ComponentLibrary
from ...models.component_library_response import ComponentLibraryResponse
from ...models.http_validation_error import HTTPValidationError
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    body: ComponentLibrary,
    hide_from_search: bool | Unset = False,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    params: dict[str, Any] = {}

    params["hide_from_search"] = hide_from_search

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/component_libraries/",
        "params": params,
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ComponentLibraryResponse | HTTPValidationError | None:
    if response.status_code == 200:
        response_200 = ComponentLibraryResponse.from_dict(response.json())

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
) -> Response[ComponentLibraryResponse | HTTPValidationError]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: ComponentLibrary,
    hide_from_search: bool | Unset = False,
) -> Response[ComponentLibraryResponse | HTTPValidationError]:
    """Create

    Args:
        hide_from_search (bool | Unset):  Default: False.
        body (ComponentLibrary):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ComponentLibraryResponse | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        body=body,
        hide_from_search=hide_from_search,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    body: ComponentLibrary,
    hide_from_search: bool | Unset = False,
) -> ComponentLibraryResponse | HTTPValidationError | None:
    """Create

    Args:
        hide_from_search (bool | Unset):  Default: False.
        body (ComponentLibrary):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ComponentLibraryResponse | HTTPValidationError
    """

    return sync_detailed(
        client=client,
        body=body,
        hide_from_search=hide_from_search,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: ComponentLibrary,
    hide_from_search: bool | Unset = False,
) -> Response[ComponentLibraryResponse | HTTPValidationError]:
    """Create

    Args:
        hide_from_search (bool | Unset):  Default: False.
        body (ComponentLibrary):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ComponentLibraryResponse | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        body=body,
        hide_from_search=hide_from_search,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: ComponentLibrary,
    hide_from_search: bool | Unset = False,
) -> ComponentLibraryResponse | HTTPValidationError | None:
    """Create

    Args:
        hide_from_search (bool | Unset):  Default: False.
        body (ComponentLibrary):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ComponentLibraryResponse | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
            hide_from_search=hide_from_search,
        )
    ).parsed
