from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.get_container_execution_state_response import GetContainerExecutionStateResponse
from ...models.http_validation_error import HTTPValidationError
from ...types import UNSET, Response, Unset


def _get_kwargs(
    id: str,
    *,
    include_execution_nodes_linked_to_same_container_execution: bool | None | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_include_execution_nodes_linked_to_same_container_execution: bool | None | Unset
    if isinstance(include_execution_nodes_linked_to_same_container_execution, Unset):
        json_include_execution_nodes_linked_to_same_container_execution = UNSET
    else:
        json_include_execution_nodes_linked_to_same_container_execution = (
            include_execution_nodes_linked_to_same_container_execution
        )
    params["include_execution_nodes_linked_to_same_container_execution"] = (
        json_include_execution_nodes_linked_to_same_container_execution
    )

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/executions/{id}/container_state".format(
            id=quote(str(id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> GetContainerExecutionStateResponse | HTTPValidationError | None:
    if response.status_code == 200:
        response_200 = GetContainerExecutionStateResponse.from_dict(response.json())

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
) -> Response[GetContainerExecutionStateResponse | HTTPValidationError]:
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
    include_execution_nodes_linked_to_same_container_execution: bool | None | Unset = UNSET,
) -> Response[GetContainerExecutionStateResponse | HTTPValidationError]:
    """Get Container Execution State

    Args:
        id (str):
        include_execution_nodes_linked_to_same_container_execution (bool | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[GetContainerExecutionStateResponse | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        id=id,
        include_execution_nodes_linked_to_same_container_execution=include_execution_nodes_linked_to_same_container_execution,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    id: str,
    *,
    client: AuthenticatedClient | Client,
    include_execution_nodes_linked_to_same_container_execution: bool | None | Unset = UNSET,
) -> GetContainerExecutionStateResponse | HTTPValidationError | None:
    """Get Container Execution State

    Args:
        id (str):
        include_execution_nodes_linked_to_same_container_execution (bool | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        GetContainerExecutionStateResponse | HTTPValidationError
    """

    return sync_detailed(
        id=id,
        client=client,
        include_execution_nodes_linked_to_same_container_execution=include_execution_nodes_linked_to_same_container_execution,
    ).parsed


async def asyncio_detailed(
    id: str,
    *,
    client: AuthenticatedClient | Client,
    include_execution_nodes_linked_to_same_container_execution: bool | None | Unset = UNSET,
) -> Response[GetContainerExecutionStateResponse | HTTPValidationError]:
    """Get Container Execution State

    Args:
        id (str):
        include_execution_nodes_linked_to_same_container_execution (bool | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[GetContainerExecutionStateResponse | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        id=id,
        include_execution_nodes_linked_to_same_container_execution=include_execution_nodes_linked_to_same_container_execution,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    id: str,
    *,
    client: AuthenticatedClient | Client,
    include_execution_nodes_linked_to_same_container_execution: bool | None | Unset = UNSET,
) -> GetContainerExecutionStateResponse | HTTPValidationError | None:
    """Get Container Execution State

    Args:
        id (str):
        include_execution_nodes_linked_to_same_container_execution (bool | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        GetContainerExecutionStateResponse | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            id=id,
            client=client,
            include_execution_nodes_linked_to_same_container_execution=include_execution_nodes_linked_to_same_container_execution,
        )
    ).parsed
