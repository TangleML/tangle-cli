"""Resolution of the authenticated account, and the symbolic ``me`` publisher.

Compilation is offline, so a compiler cannot write the author's account id into
a component entry. It writes the symbolic publisher :data:`ME` instead, and the
account is resolved at hydration time by whoever is actually authenticated.

Two callers need the account: the publisher, which scopes its version check and
deprecation to the owner, and hydration of entries whose publisher is ``me``.
They must agree, so the parsing lives here once.

They differ only in what an *unknown* account means, so that choice is left to
the caller: the publisher degrades, while resolution must fail closed -- see
:func:`require_authenticated_user_id`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: Symbolic publisher meaning "whoever is authenticated at hydration time".
#: Matched exactly and case-sensitively, so a literal account id that happens to
#: differ in case is never mistaken for the sentinel.
ME = "me"

__all__ = [
    "ME",
    "IdentityUnavailableError",
    "authenticated_user_id",
    "is_symbolic_me",
    "require_authenticated_user_id",
]


class IdentityUnavailableError(RuntimeError):
    """The authenticated account could not be determined."""


def is_symbolic_me(publisher: Any) -> bool:
    """Whether ``publisher`` is the symbolic self-reference rather than an id."""
    return publisher == ME


def authenticated_user_id(client: Any) -> str | None:
    """Return the current user id, or ``None`` if it cannot be read.

    An empty or missing id is as unusable as no answer at all.
    """
    try:
        user_info = client.users_me()
    except Exception:
        return None
    if user_info is None:
        return None
    if isinstance(user_info, Mapping):
        value = user_info.get("id")
    else:
        value = getattr(user_info, "id", None)
    return str(value) if value else None


def require_authenticated_user_id(client: Any) -> str:
    """Return the current user id, raising when it cannot be determined.

    For owner-scoped *resolution* an unknown account must never widen the
    search: continuing unscoped is what would let a component published by
    someone else under the same name be resolved as the author's own code.
    """
    user_id = authenticated_user_id(client)
    if not user_id:
        raise IdentityUnavailableError(
            "Cannot determine the authenticated account, so a component "
            f"published by '{ME}' cannot be resolved. Refusing to fall back to "
            "an unscoped lookup, which could resolve a component owned by "
            "someone else. Re-authenticate and retry."
        )
    return user_id
