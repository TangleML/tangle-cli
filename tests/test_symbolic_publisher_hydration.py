"""Owner scoping of entries whose publisher is the symbolic ``me``.

The compiler emits ``publisher: me`` because compilation is offline. Hydration
resolves it to the authenticated account, which is what keeps the lookup from
being a global name search in which anyone's same-named component is a
candidate. These tests pin that identity control at the layer every consumer
inherits, not just in one downstream CLI.
"""

from __future__ import annotations

from typing import Any

import pytest

from tangle_cli.authenticated_identity import IdentityUnavailableError
from tangle_cli.client import TangleApiClient
from tangle_cli.models import ComponentInfo
from tangle_cli.pipeline_hydrator import PipelineHydrator

_ME = "user-me"
_GENERATION = {"file": "daily_pulse.py", "function": "load_orders", "image": "img:1"}


def _rows(*specs: tuple[str, str | None]) -> list[ComponentInfo]:
    return [
        ComponentInfo(name="orders-loader", digest=digest, version="1.0", published_by=owner)
        for digest, owner in specs
    ]


class _Client:
    """Client whose listing layer behaves like the server: substring owner."""

    base_url = "https://tangle.example"
    logger = None

    def __init__(self, rows: list[ComponentInfo], *, identity: Any = _ME) -> None:
        self._rows = rows
        self._identity = identity
        self.owner_filters: list[str | None] = []

    def users_me(self) -> Any:
        if isinstance(self._identity, Exception):
            raise self._identity
        return {"id": self._identity} if self._identity is not None else None

    def list_published_component_infos(
        self,
        include_deprecated: bool = False,
        name_substring: str | None = None,
        published_by_substring: str | None = None,
        digest: str | None = None,
        *,
        fetch_specs: bool = False,
    ) -> list[ComponentInfo]:
        out = self._rows
        if published_by_substring:
            out = [i for i in out if published_by_substring in (i.published_by or "")]
        if name_substring:
            out = [i for i in out if name_substring.lower() in i.name.lower()]
        return out

    def find_existing_components(self, *args: Any, **kwargs: Any) -> list[ComponentInfo]:
        self.owner_filters.append(kwargs.get("published_by"))
        return TangleApiClient.find_existing_components(self, *args, **kwargs)

    def get_component_spec(self, digest: str) -> dict[str, Any]:
        return {"name": "orders-loader", "digest": digest}


def _entry(**over: Any) -> dict[str, Any]:
    base = {
        "name": "orders-loader",
        "version": "1.0",
        "publisher": "me",
        "local_from_python": dict(_GENERATION),
        "publish": True,
    }
    base.update(over)
    return base


def test_symbolic_me_resolves_to_the_authenticated_account() -> None:
    client = _Client(_rows(("sha256:mine", _ME)))

    resolved = PipelineHydrator(client=client)._resolve_by_name_with_filters(_entry())

    assert resolved is not None
    assert resolved[0] == "sha256:mine"
    assert client.owner_filters == [_ME]  # the symbol never reaches the API


def test_a_foreign_component_is_not_selected_even_when_returned_first() -> None:
    client = _Client(_rows(("sha256:attacker", "attacker"), ("sha256:mine", _ME)))

    resolved = PipelineHydrator(client=client)._resolve_by_name_with_filters(_entry())

    assert resolved is not None
    assert resolved[0] == "sha256:mine"


def test_a_foreign_only_collision_yields_no_candidate() -> None:
    """Someone else holding the name must not supply the code; the caller then
    falls back to the local candidate."""
    client = _Client(_rows(("sha256:attacker", "attacker")))

    assert PipelineHydrator(client=client)._resolve_by_name_with_filters(_entry()) is None


@pytest.mark.parametrize(
    "identity",
    [
        pytest.param(RuntimeError("auth expired"), id="lookup-raises"),
        pytest.param(None, id="no-user"),
        pytest.param("", id="empty-id"),
    ],
)
def test_an_unresolvable_identity_fails_closed(identity: Any) -> None:
    """Never widen to a global search: that is the vulnerability."""
    client = _Client(_rows(("sha256:mine", _ME)), identity=identity)

    with pytest.raises(IdentityUnavailableError, match="unscoped"):
        PipelineHydrator(client=client)._resolve_by_name_with_filters(_entry())
    assert client.owner_filters == []  # no request was issued at all


def test_a_literal_publisher_id_is_used_verbatim() -> None:
    client = _Client(_rows(("sha256:theirs", "other-team")))

    resolved = PipelineHydrator(client=client)._resolve_by_name_with_filters(
        _entry(publisher="other-team")
    )

    assert resolved is not None
    assert client.owner_filters == ["other-team"]


def test_an_entry_without_a_publisher_keeps_the_existing_global_behaviour() -> None:
    """Hand-authored cross-publisher resolution is unchanged."""
    client = _Client(_rows(("sha256:someone-elses", "someone-else")))
    entry = {"name": "orders-loader", "version": "1.0"}

    resolved = PipelineHydrator(client=client)._resolve_by_name_with_filters(entry)

    assert resolved is not None
    assert client.owner_filters == [None]


def test_a_publisher_that_merely_resembles_the_sentinel_stays_literal() -> None:
    """``ME`` is not ``me``: only the exact symbol is self-referential, so a
    real account id is never swapped for the current user."""
    client = _Client(_rows(("sha256:odd", "ME")))

    resolved = PipelineHydrator(client=client)._resolve_by_name_with_filters(
        _entry(publisher="ME")
    )

    assert resolved is not None
    assert client.owner_filters == ["ME"]


def test_an_unmarked_entry_treats_me_as_a_literal_account_id() -> None:
    """``me`` is only self-referential on a compiler-authored marked entry.

    Hand-authored configs predate the sentinel, where ``publisher: me`` meant a
    registry account literally named ``me`` -- the API does not reserve the
    string. Substituting there would silently retarget an existing config, or
    fail it on an auth error, so the marker is required too.
    """
    client = _Client(_rows(("sha256:literal-me", "me")))
    entry = {"name": "orders-loader", "version": "1.0", "publisher": "me"}

    resolved = PipelineHydrator(client=client)._resolve_by_name_with_filters(entry)

    assert resolved is not None
    assert resolved[0] == "sha256:literal-me"
    assert client.owner_filters == ["me"]  # passed through, not substituted


def test_an_unmarked_entry_with_me_does_not_require_authentication() -> None:
    """The compatibility guarantee has teeth: an unmarked config must keep
    resolving even when no account can be determined."""
    client = _Client(_rows(("sha256:literal-me", "me")), identity=RuntimeError("no auth"))
    entry = {"name": "orders-loader", "version": "1.0", "publisher": "me"}

    resolved = PipelineHydrator(client=client)._resolve_by_name_with_filters(entry)

    assert resolved is not None
