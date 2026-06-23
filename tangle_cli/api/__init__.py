"""Client access for the Tangle API CLI.

The generated ``tangle api ...`` commands send their HTTP requests through a
single, globally-initialized :class:`~tangle_cli.api.client.Client` instance.
Use :func:`get_client` to obtain it and :func:`set_client` to override it
(for example, to point at a different server or to inject authentication).

The default client reads its configuration from the environment:

* ``TANGLE_API_URL`` -- base URL of the API server
  (default: ``http://127.0.0.1:8000``).
* ``TANGLE_API_TOKEN`` -- if set, requests are sent with bearer authentication.
"""

import os

from . import client

__all__ = [
    "AuthenticatedClient",
    "Client",
    "DEFAULT_BASE_URL",
    "get_client",
    "set_client",
]

AuthenticatedClient = client.AuthenticatedClient
Client = client.Client

DEFAULT_BASE_URL = "http://127.0.0.1:8000"

_client: client.Client | None = None


def _build_default_client() -> client.Client:
    base_url = os.environ.get("TANGLE_API_URL", DEFAULT_BASE_URL)
    token = os.environ.get("TANGLE_API_TOKEN")
    if token:
        return client.AuthenticatedClient(base_url=base_url, token=token)
    return client.Client(base_url=base_url)


def get_client() -> client.Client:
    """Return the global API client, creating a default one if needed."""
    global _client
    if _client is None:
        _client = _build_default_client()
    return _client


def set_client(client: client.Client) -> None:
    """Replace the global API client used by the generated CLI commands."""
    global _client
    _client = client
