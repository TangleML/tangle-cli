from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.list_pipeline_jobs_response import ListPipelineJobsResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    page_token: None | str | Unset = UNSET,
    filter_: None | str | Unset = UNSET,
    filter_query: None | str | Unset = UNSET,
    include_pipeline_names: bool | Unset = False,
    include_execution_stats: bool | Unset = False,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_page_token: None | str | Unset
    if isinstance(page_token, Unset):
        json_page_token = UNSET
    else:
        json_page_token = page_token
    params["page_token"] = json_page_token

    json_filter_: None | str | Unset
    if isinstance(filter_, Unset):
        json_filter_ = UNSET
    else:
        json_filter_ = filter_
    params["filter"] = json_filter_

    json_filter_query: None | str | Unset
    if isinstance(filter_query, Unset):
        json_filter_query = UNSET
    else:
        json_filter_query = filter_query
    params["filter_query"] = json_filter_query

    params["include_pipeline_names"] = include_pipeline_names

    params["include_execution_stats"] = include_execution_stats

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/pipeline_runs/",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | ListPipelineJobsResponse | None:
    if response.status_code == 200:
        response_200 = ListPipelineJobsResponse.from_dict(response.json())

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
) -> Response[HTTPValidationError | ListPipelineJobsResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    page_token: None | str | Unset = UNSET,
    filter_: None | str | Unset = UNSET,
    filter_query: None | str | Unset = UNSET,
    include_pipeline_names: bool | Unset = False,
    include_execution_stats: bool | Unset = False,
) -> Response[HTTPValidationError | ListPipelineJobsResponse]:
    """List

    Args:
        page_token (None | str | Unset):
        filter_ (None | str | Unset):
        filter_query (None | str | Unset):
        include_pipeline_names (bool | Unset):  Default: False.
        include_execution_stats (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | ListPipelineJobsResponse]
    """

    kwargs = _get_kwargs(
        page_token=page_token,
        filter_=filter_,
        filter_query=filter_query,
        include_pipeline_names=include_pipeline_names,
        include_execution_stats=include_execution_stats,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    page_token: None | str | Unset = UNSET,
    filter_: None | str | Unset = UNSET,
    filter_query: None | str | Unset = UNSET,
    include_pipeline_names: bool | Unset = False,
    include_execution_stats: bool | Unset = False,
) -> HTTPValidationError | ListPipelineJobsResponse | None:
    """List

    Args:
        page_token (None | str | Unset):
        filter_ (None | str | Unset):
        filter_query (None | str | Unset):
        include_pipeline_names (bool | Unset):  Default: False.
        include_execution_stats (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | ListPipelineJobsResponse
    """

    return sync_detailed(
        client=client,
        page_token=page_token,
        filter_=filter_,
        filter_query=filter_query,
        include_pipeline_names=include_pipeline_names,
        include_execution_stats=include_execution_stats,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    page_token: None | str | Unset = UNSET,
    filter_: None | str | Unset = UNSET,
    filter_query: None | str | Unset = UNSET,
    include_pipeline_names: bool | Unset = False,
    include_execution_stats: bool | Unset = False,
) -> Response[HTTPValidationError | ListPipelineJobsResponse]:
    """List

    Args:
        page_token (None | str | Unset):
        filter_ (None | str | Unset):
        filter_query (None | str | Unset):
        include_pipeline_names (bool | Unset):  Default: False.
        include_execution_stats (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | ListPipelineJobsResponse]
    """

    kwargs = _get_kwargs(
        page_token=page_token,
        filter_=filter_,
        filter_query=filter_query,
        include_pipeline_names=include_pipeline_names,
        include_execution_stats=include_execution_stats,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    page_token: None | str | Unset = UNSET,
    filter_: None | str | Unset = UNSET,
    filter_query: None | str | Unset = UNSET,
    include_pipeline_names: bool | Unset = False,
    include_execution_stats: bool | Unset = False,
) -> HTTPValidationError | ListPipelineJobsResponse | None:
    """List

    Args:
        page_token (None | str | Unset):
        filter_ (None | str | Unset):
        filter_query (None | str | Unset):
        include_pipeline_names (bool | Unset):  Default: False.
        include_execution_stats (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | ListPipelineJobsResponse
    """

    return (
        await asyncio_detailed(
            client=client,
            page_token=page_token,
            filter_=filter_,
            filter_query=filter_query,
            include_pipeline_names=include_pipeline_names,
            include_execution_stats=include_execution_stats,
        )
    ).parsed
