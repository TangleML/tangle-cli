"""Read/write helpers for Tangle secret metadata and values.

Secret values are accepted only for explicit create/update operations and are
never included in returned metadata dictionaries.
"""

from __future__ import annotations

import os
from typing import Any, Protocol

from .handler import TangleCliHandler


class SecretClient(Protocol):
    """Subset of the generated static client used by secret commands."""

    def secrets_list(self) -> Any: ...

    def secrets_create(
        self,
        secret_name: str,
        secret_value: str,
        description: str | None = None,
        expires_at: str | None = None,
    ) -> Any: ...

    def secrets_update(
        self,
        secret_name: str,
        secret_value: str,
        description: str | None = None,
        expires_at: str | None = None,
    ) -> Any: ...

    def secrets_delete(self, secret_name: str) -> Any: ...


class SecretValueError(ValueError):
    """Raised when secret value CLI/config inputs are invalid."""


class SecretsManager(TangleCliHandler):
    """Secret resource manager with injectable client construction.

    Downstream packages can inject an authenticated client directly or provide a
    lazy ``client_factory``. Returned dictionaries intentionally omit secret
    values and only include metadata.
    """

    def __init__(
        self,
        client: SecretClient | None = None,
        *,
        client_factory: Any | None = None,
        logger: Any | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(client=client, client_factory=client_factory, logger=logger, **kwargs)

    @staticmethod
    def resolve_secret_value(value: str | None, from_env: str | None) -> str:
        """Resolve the secret value from either ``--value`` or ``--from-env``.

        Error messages intentionally mention only the option/env-var name and
        never include the secret value.
        """

        if value is not None and from_env is not None:
            raise SecretValueError("specify either --value or --from-env, not both")
        if from_env is not None:
            resolved = os.environ.get(from_env)
            if resolved is None:
                raise SecretValueError(f"environment variable '{from_env}' is not set")
            return resolved
        if value is not None:
            return value
        raise SecretValueError("either --value or --from-env is required")

    @staticmethod
    def secret_metadata(secret: Any) -> dict[str, Any]:
        """Return JSON-safe secret metadata, excluding any secret value fields."""

        entry: dict[str, Any] = {}
        for field in ("secret_name", "created_at", "updated_at", "expires_at", "description"):
            value = _value_from_mapping_or_object(secret, field)
            if value is not None:
                entry[field] = str(value)
        return entry

    def list(self) -> dict[str, Any]:
        """List secret metadata without exposing secret values."""

        response = self._require_client().secrets_list()
        raw_secrets = _value_from_mapping_or_object(response, "secrets", []) or []
        secrets = [self.secret_metadata(secret) for secret in raw_secrets]
        return {"status": "success", "count": len(secrets), "secrets": secrets}

    def create(
        self,
        secret_name: str,
        *,
        value: str | None = None,
        from_env: str | None = None,
        description: str | None = None,
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        """Create a secret using generated static API operations."""

        secret_value = self.resolve_secret_value(value, from_env)
        secret = self._require_client().secrets_create(
            secret_name,
            secret_value,
            description=description,
            expires_at=expires_at,
        )
        return {"status": "success", "action": "created", "secret": self.secret_metadata(secret)}

    def update(
        self,
        secret_name: str,
        *,
        value: str | None = None,
        from_env: str | None = None,
        description: str | None = None,
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        """Update a secret using generated static API operations."""

        secret_value = self.resolve_secret_value(value, from_env)
        secret = self._require_client().secrets_update(
            secret_name,
            secret_value,
            description=description,
            expires_at=expires_at,
        )
        return {"status": "success", "action": "updated", "secret": self.secret_metadata(secret)}

    def delete(self, secret_name: str) -> dict[str, Any]:
        """Delete a secret using generated static API operations."""

        self._require_client().secrets_delete(secret_name)
        return {"status": "success", "action": "deleted", "secret_name": secret_name}


def _value_from_mapping_or_object(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


__all__ = [
    "SecretClient",
    "SecretValueError",
    "SecretsManager",
]
