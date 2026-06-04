from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.list_annotations_api_pipeline_runs_id_annotations_get_response_list_annotations_api_pipeline_runs_id_annotations_get import (
    ListAnnotationsApiPipelineRunsIdAnnotationsGetResponseListAnnotationsApiPipelineRunsIdAnnotationsGet,
)
from ...types import Response


def _get_kwargs(
    id: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/pipeline_runs/{id}/annotations/".format(
            id=quote(str(id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> (
    HTTPValidationError
    | ListAnnotationsApiPipelineRunsIdAnnotationsGetResponseListAnnotationsApiPipelineRunsIdAnnotationsGet
    | None
):
    if response.status_code == 200:
        response_200 = ListAnnotationsApiPipelineRunsIdAnnotationsGetResponseListAnnotationsApiPipelineRunsIdAnnotationsGet.from_dict(
            response.json()
        )

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
) -> Response[
    HTTPValidationError
    | ListAnnotationsApiPipelineRunsIdAnnotationsGetResponseListAnnotationsApiPipelineRunsIdAnnotationsGet
]:
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
) -> Response[
    HTTPValidationError
    | ListAnnotationsApiPipelineRunsIdAnnotationsGetResponseListAnnotationsApiPipelineRunsIdAnnotationsGet
]:
    """List Annotations

    Args:
        id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | ListAnnotationsApiPipelineRunsIdAnnotationsGetResponseListAnnotationsApiPipelineRunsIdAnnotationsGet]
    """

    kwargs = _get_kwargs(
        id=id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    id: str,
    *,
    client: AuthenticatedClient | Client,
) -> (
    HTTPValidationError
    | ListAnnotationsApiPipelineRunsIdAnnotationsGetResponseListAnnotationsApiPipelineRunsIdAnnotationsGet
    | None
):
    """List Annotations

    Args:
        id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | ListAnnotationsApiPipelineRunsIdAnnotationsGetResponseListAnnotationsApiPipelineRunsIdAnnotationsGet
    """

    return sync_detailed(
        id=id,
        client=client,
    ).parsed


async def asyncio_detailed(
    id: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[
    HTTPValidationError
    | ListAnnotationsApiPipelineRunsIdAnnotationsGetResponseListAnnotationsApiPipelineRunsIdAnnotationsGet
]:
    """List Annotations

    Args:
        id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | ListAnnotationsApiPipelineRunsIdAnnotationsGetResponseListAnnotationsApiPipelineRunsIdAnnotationsGet]
    """

    kwargs = _get_kwargs(
        id=id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    id: str,
    *,
    client: AuthenticatedClient | Client,
) -> (
    HTTPValidationError
    | ListAnnotationsApiPipelineRunsIdAnnotationsGetResponseListAnnotationsApiPipelineRunsIdAnnotationsGet
    | None
):
    """List Annotations

    Args:
        id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | ListAnnotationsApiPipelineRunsIdAnnotationsGetResponseListAnnotationsApiPipelineRunsIdAnnotationsGet
    """

    return (
        await asyncio_detailed(
            id=id,
            client=client,
        )
    ).parsed
