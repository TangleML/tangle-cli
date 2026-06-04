from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.pipeline_run_response import PipelineRunResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    id: str,
    *,
    include_execution_stats: bool | Unset = False,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["include_execution_stats"] = include_execution_stats

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/pipeline_runs/{id}".format(
            id=quote(str(id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | PipelineRunResponse | None:
    if response.status_code == 200:
        response_200 = PipelineRunResponse.from_dict(response.json())

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
) -> Response[HTTPValidationError | PipelineRunResponse]:
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
    include_execution_stats: bool | Unset = False,
) -> Response[HTTPValidationError | PipelineRunResponse]:
    """Get

    Args:
        id (str):
        include_execution_stats (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | PipelineRunResponse]
    """

    kwargs = _get_kwargs(
        id=id,
        include_execution_stats=include_execution_stats,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    id: str,
    *,
    client: AuthenticatedClient | Client,
    include_execution_stats: bool | Unset = False,
) -> HTTPValidationError | PipelineRunResponse | None:
    """Get

    Args:
        id (str):
        include_execution_stats (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | PipelineRunResponse
    """

    return sync_detailed(
        id=id,
        client=client,
        include_execution_stats=include_execution_stats,
    ).parsed


async def asyncio_detailed(
    id: str,
    *,
    client: AuthenticatedClient | Client,
    include_execution_stats: bool | Unset = False,
) -> Response[HTTPValidationError | PipelineRunResponse]:
    """Get

    Args:
        id (str):
        include_execution_stats (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | PipelineRunResponse]
    """

    kwargs = _get_kwargs(
        id=id,
        include_execution_stats=include_execution_stats,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    id: str,
    *,
    client: AuthenticatedClient | Client,
    include_execution_stats: bool | Unset = False,
) -> HTTPValidationError | PipelineRunResponse | None:
    """Get

    Args:
        id (str):
        include_execution_stats (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | PipelineRunResponse
    """

    return (
        await asyncio_detailed(
            id=id,
            client=client,
            include_execution_stats=include_execution_stats,
        )
    ).parsed
