import datetime
from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.body_create_secret_api_secrets_post import BodyCreateSecretApiSecretsPost
from ...models.http_validation_error import HTTPValidationError
from ...models.secret_info_response import SecretInfoResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    body: BodyCreateSecretApiSecretsPost,
    secret_name: str,
    description: None | str | Unset = UNSET,
    expires_at: datetime.datetime | None | Unset = UNSET,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    params: dict[str, Any] = {}

    params["secret_name"] = secret_name

    json_description: None | str | Unset
    if isinstance(description, Unset):
        json_description = UNSET
    else:
        json_description = description
    params["description"] = json_description

    json_expires_at: None | str | Unset
    if isinstance(expires_at, Unset):
        json_expires_at = UNSET
    elif isinstance(expires_at, datetime.datetime):
        json_expires_at = expires_at.isoformat()
    else:
        json_expires_at = expires_at
    params["expires_at"] = json_expires_at

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/secrets/",
        "params": params,
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | SecretInfoResponse | None:
    if response.status_code == 200:
        response_200 = SecretInfoResponse.from_dict(response.json())

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
) -> Response[HTTPValidationError | SecretInfoResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: BodyCreateSecretApiSecretsPost,
    secret_name: str,
    description: None | str | Unset = UNSET,
    expires_at: datetime.datetime | None | Unset = UNSET,
) -> Response[HTTPValidationError | SecretInfoResponse]:
    """Create Secret

    Args:
        secret_name (str):
        description (None | str | Unset):
        expires_at (datetime.datetime | None | Unset):
        body (BodyCreateSecretApiSecretsPost):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | SecretInfoResponse]
    """

    kwargs = _get_kwargs(
        body=body,
        secret_name=secret_name,
        description=description,
        expires_at=expires_at,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    body: BodyCreateSecretApiSecretsPost,
    secret_name: str,
    description: None | str | Unset = UNSET,
    expires_at: datetime.datetime | None | Unset = UNSET,
) -> HTTPValidationError | SecretInfoResponse | None:
    """Create Secret

    Args:
        secret_name (str):
        description (None | str | Unset):
        expires_at (datetime.datetime | None | Unset):
        body (BodyCreateSecretApiSecretsPost):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | SecretInfoResponse
    """

    return sync_detailed(
        client=client,
        body=body,
        secret_name=secret_name,
        description=description,
        expires_at=expires_at,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: BodyCreateSecretApiSecretsPost,
    secret_name: str,
    description: None | str | Unset = UNSET,
    expires_at: datetime.datetime | None | Unset = UNSET,
) -> Response[HTTPValidationError | SecretInfoResponse]:
    """Create Secret

    Args:
        secret_name (str):
        description (None | str | Unset):
        expires_at (datetime.datetime | None | Unset):
        body (BodyCreateSecretApiSecretsPost):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | SecretInfoResponse]
    """

    kwargs = _get_kwargs(
        body=body,
        secret_name=secret_name,
        description=description,
        expires_at=expires_at,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: BodyCreateSecretApiSecretsPost,
    secret_name: str,
    description: None | str | Unset = UNSET,
    expires_at: datetime.datetime | None | Unset = UNSET,
) -> HTTPValidationError | SecretInfoResponse | None:
    """Create Secret

    Args:
        secret_name (str):
        description (None | str | Unset):
        expires_at (datetime.datetime | None | Unset):
        body (BodyCreateSecretApiSecretsPost):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | SecretInfoResponse
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
            secret_name=secret_name,
            description=description,
            expires_at=expires_at,
        )
    ).parsed
