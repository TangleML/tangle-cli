from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.user_settings_response import UserSettingsResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    setting_names: list[str] | None | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_setting_names: list[str] | None | Unset
    if isinstance(setting_names, Unset):
        json_setting_names = UNSET
    elif isinstance(setting_names, list):
        json_setting_names = setting_names

    else:
        json_setting_names = setting_names
    params["setting_names"] = json_setting_names

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/users/me/settings",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | UserSettingsResponse | None:
    if response.status_code == 200:
        response_200 = UserSettingsResponse.from_dict(response.json())

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
) -> Response[HTTPValidationError | UserSettingsResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    setting_names: list[str] | None | Unset = UNSET,
) -> Response[HTTPValidationError | UserSettingsResponse]:
    """Get Settings

     Gets user settings.

    If `setting_names` is specified, returns only those settings.

    Args:
        setting_names (list[str] | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | UserSettingsResponse]
    """

    kwargs = _get_kwargs(
        setting_names=setting_names,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    setting_names: list[str] | None | Unset = UNSET,
) -> HTTPValidationError | UserSettingsResponse | None:
    """Get Settings

     Gets user settings.

    If `setting_names` is specified, returns only those settings.

    Args:
        setting_names (list[str] | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | UserSettingsResponse
    """

    return sync_detailed(
        client=client,
        setting_names=setting_names,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    setting_names: list[str] | None | Unset = UNSET,
) -> Response[HTTPValidationError | UserSettingsResponse]:
    """Get Settings

     Gets user settings.

    If `setting_names` is specified, returns only those settings.

    Args:
        setting_names (list[str] | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | UserSettingsResponse]
    """

    kwargs = _get_kwargs(
        setting_names=setting_names,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    setting_names: list[str] | None | Unset = UNSET,
) -> HTTPValidationError | UserSettingsResponse | None:
    """Get Settings

     Gets user settings.

    If `setting_names` is specified, returns only those settings.

    Args:
        setting_names (list[str] | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | UserSettingsResponse
    """

    return (
        await asyncio_detailed(
            client=client,
            setting_names=setting_names,
        )
    ).parsed
