from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.list_published_components_response import ListPublishedComponentsResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    include_deprecated: bool | Unset = False,
    name_substring: None | str | Unset = UNSET,
    published_by_substring: None | str | Unset = UNSET,
    digest: None | str | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["include_deprecated"] = include_deprecated

    json_name_substring: None | str | Unset
    if isinstance(name_substring, Unset):
        json_name_substring = UNSET
    else:
        json_name_substring = name_substring
    params["name_substring"] = json_name_substring

    json_published_by_substring: None | str | Unset
    if isinstance(published_by_substring, Unset):
        json_published_by_substring = UNSET
    else:
        json_published_by_substring = published_by_substring
    params["published_by_substring"] = json_published_by_substring

    json_digest: None | str | Unset
    if isinstance(digest, Unset):
        json_digest = UNSET
    else:
        json_digest = digest
    params["digest"] = json_digest

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/published_components/",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | ListPublishedComponentsResponse | None:
    if response.status_code == 200:
        response_200 = ListPublishedComponentsResponse.from_dict(response.json())

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
) -> Response[HTTPValidationError | ListPublishedComponentsResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    include_deprecated: bool | Unset = False,
    name_substring: None | str | Unset = UNSET,
    published_by_substring: None | str | Unset = UNSET,
    digest: None | str | Unset = UNSET,
) -> Response[HTTPValidationError | ListPublishedComponentsResponse]:
    """List

    Args:
        include_deprecated (bool | Unset):  Default: False.
        name_substring (None | str | Unset):
        published_by_substring (None | str | Unset):
        digest (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | ListPublishedComponentsResponse]
    """

    kwargs = _get_kwargs(
        include_deprecated=include_deprecated,
        name_substring=name_substring,
        published_by_substring=published_by_substring,
        digest=digest,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    include_deprecated: bool | Unset = False,
    name_substring: None | str | Unset = UNSET,
    published_by_substring: None | str | Unset = UNSET,
    digest: None | str | Unset = UNSET,
) -> HTTPValidationError | ListPublishedComponentsResponse | None:
    """List

    Args:
        include_deprecated (bool | Unset):  Default: False.
        name_substring (None | str | Unset):
        published_by_substring (None | str | Unset):
        digest (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | ListPublishedComponentsResponse
    """

    return sync_detailed(
        client=client,
        include_deprecated=include_deprecated,
        name_substring=name_substring,
        published_by_substring=published_by_substring,
        digest=digest,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    include_deprecated: bool | Unset = False,
    name_substring: None | str | Unset = UNSET,
    published_by_substring: None | str | Unset = UNSET,
    digest: None | str | Unset = UNSET,
) -> Response[HTTPValidationError | ListPublishedComponentsResponse]:
    """List

    Args:
        include_deprecated (bool | Unset):  Default: False.
        name_substring (None | str | Unset):
        published_by_substring (None | str | Unset):
        digest (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | ListPublishedComponentsResponse]
    """

    kwargs = _get_kwargs(
        include_deprecated=include_deprecated,
        name_substring=name_substring,
        published_by_substring=published_by_substring,
        digest=digest,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    include_deprecated: bool | Unset = False,
    name_substring: None | str | Unset = UNSET,
    published_by_substring: None | str | Unset = UNSET,
    digest: None | str | Unset = UNSET,
) -> HTTPValidationError | ListPublishedComponentsResponse | None:
    """List

    Args:
        include_deprecated (bool | Unset):  Default: False.
        name_substring (None | str | Unset):
        published_by_substring (None | str | Unset):
        digest (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | ListPublishedComponentsResponse
    """

    return (
        await asyncio_detailed(
            client=client,
            include_deprecated=include_deprecated,
            name_substring=name_substring,
            published_by_substring=published_by_substring,
            digest=digest,
        )
    ).parsed
