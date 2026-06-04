from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.component_library_response import ComponentLibraryResponse
from ...models.http_validation_error import HTTPValidationError
from ...types import UNSET, Response, Unset


def _get_kwargs(
    id: str,
    *,
    include_component_texts: bool | Unset = False,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["include_component_texts"] = include_component_texts

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/component_libraries/{id}".format(
            id=quote(str(id), safe=""),
        ),
        "params": params,
    }

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
    id: str,
    *,
    client: AuthenticatedClient | Client,
    include_component_texts: bool | Unset = False,
) -> Response[ComponentLibraryResponse | HTTPValidationError]:
    """Get

    Args:
        id (str):
        include_component_texts (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ComponentLibraryResponse | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        id=id,
        include_component_texts=include_component_texts,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    id: str,
    *,
    client: AuthenticatedClient | Client,
    include_component_texts: bool | Unset = False,
) -> ComponentLibraryResponse | HTTPValidationError | None:
    """Get

    Args:
        id (str):
        include_component_texts (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ComponentLibraryResponse | HTTPValidationError
    """

    return sync_detailed(
        id=id,
        client=client,
        include_component_texts=include_component_texts,
    ).parsed


async def asyncio_detailed(
    id: str,
    *,
    client: AuthenticatedClient | Client,
    include_component_texts: bool | Unset = False,
) -> Response[ComponentLibraryResponse | HTTPValidationError]:
    """Get

    Args:
        id (str):
        include_component_texts (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ComponentLibraryResponse | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        id=id,
        include_component_texts=include_component_texts,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    id: str,
    *,
    client: AuthenticatedClient | Client,
    include_component_texts: bool | Unset = False,
) -> ComponentLibraryResponse | HTTPValidationError | None:
    """Get

    Args:
        id (str):
        include_component_texts (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ComponentLibraryResponse | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            id=id,
            client=client,
            include_component_texts=include_component_texts,
        )
    ).parsed
