"""Version constraints in ``resolve://`` configs, against server-shaped data.

The published-components listing has no ``version`` field: a component's
version exists only in its spec (``GET /api/components/{digest}``). These
tests drive the real client over a fake transport that returns exactly those
shapes, so a filter reading a version off the listing rows cannot pass.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import pytest
import requests
import yaml

from tangle_cli.client import TangleApiClient
from tangle_cli.pipeline_hydrator import HydrationError, PipelineHydrator

_NAME = "orders-loader"
V1 = "sha256:" + "1" * 64
V2 = "sha256:" + "2" * 64
UNVERSIONED = "sha256:" + "3" * 64
GONE = "sha256:" + "4" * 64  # listed, but its spec 404s
OLD = "sha256:" + "5" * 64  # deprecated, superseded by V2
OLDER = "sha256:" + "6" * 64  # deprecated, superseded by OLD
ZIG_1, ZIG_3, ZIG_15 = ("sha256:" + c * 64 for c in "789")
KEEP = "sha256:" + "a" * 64  # deprecated, superseded by GONE
LOOP_A, LOOP_B = ("sha256:" + c * 64 for c in "bc")
GUARDED = "sha256:" + "d" * 64  # deprecated, superseded by DENIED
DENIED = "sha256:" + "e" * 64  # listed, but reading its spec is forbidden


def _published(
    digest: str,
    version: str | None,
    *,
    deprecated: bool = False,
    superseded_by: str | None = None,
) -> dict[str, Any]:
    return {
        "digest": digest,
        "version": version,
        "deprecated": deprecated,
        "superseded_by": superseded_by,
    }


_CATALOG = [
    _published(V1, "1.0.0"),
    _published(V2, "2.1.0"),
    _published(UNVERSIONED, None),
    _published(GONE, "9.0.0"),
]
# Successor chains (superseded_by): OLDER 0.5.0 -> OLD 1.0.0 -> V2 2.1.0, and
# ZIG_1 1.0.0 -> ZIG_3 3.0.0 -> ZIG_15 1.5.0, KEEP 1.0.0 -> GONE (spec 404s),
# the cycle LOOP_A 1.0.0 <-> LOOP_B 3.0.0, and GUARDED 1.0.0 -> DENIED (spec 403s).
_WITH_SUCCESSORS = [
    *_CATALOG,
    _published(OLD, "1.0.0", deprecated=True, superseded_by=V2),
    _published(OLDER, "0.5.0", deprecated=True, superseded_by=OLD),
    _published(ZIG_1, "1.0.0", deprecated=True, superseded_by=ZIG_3),
    _published(ZIG_3, "3.0.0", deprecated=True, superseded_by=ZIG_15),
    _published(ZIG_15, "1.5.0"),
    _published(KEEP, "1.0.0", deprecated=True, superseded_by=GONE),
    _published(LOOP_A, "1.0.0", deprecated=True, superseded_by=LOOP_B),
    _published(LOOP_B, "3.0.0", deprecated=True, superseded_by=LOOP_A),
    _published(GUARDED, "1.0.0", deprecated=True, superseded_by=DENIED),
    _published(DENIED, "1.5.0"),
]


class _ServerShapedClient(TangleApiClient):
    """Real client; only the HTTP exchange is faked."""

    def __init__(self, catalog: list[dict[str, Any]]) -> None:
        super().__init__("https://tangle.test", include_env_credentials=False)
        self._catalog = catalog

    def _make_request(
        self,
        method: str,
        path: str,
        params: Any = None,
        json_data: Any = None,
        **kwargs: Any,
    ) -> requests.Response:
        params = params or {}
        if path == "/api/published_components/":
            rows = [
                {
                    # PublishedComponentResponse: no version field.
                    "digest": c["digest"],
                    "name": _NAME,
                    "published_by": "someone",
                    "deprecated": c["deprecated"],
                    "superseded_by": c["superseded_by"],
                    "url": None,
                }
                for c in self._catalog
                if (params.get("include_deprecated") or not c["deprecated"])
                and params.get("digest") in (None, c["digest"])
                and (params.get("name_substring") or "").lower() in _NAME
            ]
            return _response(200, {"published_components": rows})
        digest = unquote(path.rsplit("/", 1)[-1])
        component = next((c for c in self._catalog if c["digest"] == digest), None)
        if digest == DENIED:
            return _response(403, {"detail": "Forbidden"})
        if component is None or digest == GONE:
            return _response(404, {"detail": "Not found"})
        spec: dict[str, Any] = {"name": _NAME}
        if component["version"]:
            spec["metadata"] = {"annotations": {"version": component["version"]}}
        return _response(200, {"digest": digest, "text": yaml.safe_dump(spec)})


def _response(status: int, payload: Any) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response.reason = {200: "OK", 403: "Forbidden"}.get(status, "Not Found")
    response.url = "https://tangle.test/api"
    response.headers["Content-Type"] = "application/json"
    response._content = json.dumps(payload).encode()
    return response


def _resolve_ref(
    tmp_path: Path,
    config: dict[str, Any],
    *,
    catalog: list[dict[str, Any]] = _CATALOG,
    **hydrator_options: Any,
) -> dict[str, Any]:
    """Resolve one task through ``resolve://...#c`` and return its componentRef."""
    (tmp_path / "components.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    pipeline = {
        "name": "p",
        "implementation": {
            "graph": {"tasks": {"t": {"componentRef": {"url": "resolve://./components.yaml#c"}}}}
        },
    }
    hydrator = PipelineHydrator(client=_ServerShapedClient(catalog), **hydrator_options)
    resolved = hydrator.resolve_components(pipeline, base_dir=tmp_path)
    return resolved["implementation"]["graph"]["tasks"]["t"]["componentRef"]


def _hydrate(
    tmp_path: Path,
    config: dict[str, Any],
    *,
    catalog: list[dict[str, Any]] = _CATALOG,
    **hydrator_options: Any,
) -> str:
    """Return the digest that would ship."""
    return _resolve_ref(tmp_path, config, catalog=catalog, **hydrator_options)["digest"]


def _write_local(tmp_path: Path, version: str) -> None:
    local = {
        "name": _NAME,
        "metadata": {"annotations": {"version": version}},
        "implementation": {"container": {"image": "python:3.12"}},
    }
    (tmp_path / "local.yaml").write_text(yaml.safe_dump(local), encoding="utf-8")


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        # Name entries: candidates are filtered on their spec's version.
        pytest.param({"c": {"name": _NAME}}, V2, id="name-no-constraint-latest"),
        pytest.param({"c": {"name": _NAME, "version": ">=1.0"}}, V2, id="name-compatible-latest"),
        pytest.param({"c": {"name": _NAME, "version": "<2"}}, V1, id="name-compatible-older"),
        pytest.param({"c": {"name": _NAME, "version": "==2.1.0"}}, V2, id="name-exact"),
        pytest.param(
            {"_defaults": {"version": "<2"}, "c": {"name": _NAME}},
            V1,
            id="name-defaults-version-inherited",
        ),
        # Versionless and 404ing candidates never match a constraint.
        pytest.param({"c": {"name": _NAME, "version": ">=3"}}, None, id="name-no-match"),
        # Digest entries: the pinned spec must satisfy the constraint.
        pytest.param({"c": {"digest": V1}}, V1, id="digest-no-constraint"),
        pytest.param({"c": {"digest": V1, "version": "<2"}}, V1, id="digest-compatible"),
        pytest.param({"c": {"digest": V1, "version": ">=2"}}, None, id="digest-incompatible"),
        pytest.param(
            {"c": {"digest": UNVERSIONED, "version": ">=0"}}, None, id="digest-versionless"
        ),
        pytest.param(
            {"_defaults": {"version": ">=2"}, "c": {"digest": V1}},
            None,
            id="digest-defaults-version-inherited",
        ),
        # A mismatching digest is a miss, not a cue to search by name.
        pytest.param(
            {"c": {"digest": V1, "name": _NAME, "version": ">=2"}},
            None,
            id="digest-mismatch-does-not-search-name",
        ),
        # A miss falls through to the next configured entry.
        pytest.param(
            {"c": [{"name": _NAME, "version": ">=3"}, {"name": _NAME, "version": "<2"}]},
            V1,
            id="name-miss-falls-through",
        ),
        pytest.param(
            {"c": [{"digest": V1, "version": ">=2"}, {"name": _NAME, "version": ">=2"}]},
            V2,
            id="digest-miss-falls-through",
        ),
    ],
)
def test_version_constraint(tmp_path: Path, config: dict[str, Any], expected: str | None) -> None:
    if expected is None:
        with pytest.raises(HydrationError, match="Component not found"):
            _hydrate(tmp_path, config)
    else:
        assert _hydrate(tmp_path, config) == expected


@pytest.mark.parametrize(
    ("pin", "constraint", "upgrade_deprecated", "expected"),
    [
        pytest.param(OLD, None, True, V2, id="unconstrained-upgrades"),
        pytest.param(OLD, ">=2", True, V2, id="successor-satisfies"),
        pytest.param(OLD, "<2", True, OLD, id="only-deprecated-pin-satisfies"),
        pytest.param(OLDER, ">=1,<2", True, OLD, id="passes-out-of-range-pin-then-stops"),
        pytest.param(OLDER, "<2", True, OLD, id="follows-successors-within-range"),
        pytest.param(OLDER, "<1", True, OLDER, id="only-original-pin-satisfies"),
        pytest.param(ZIG_1, "<2", True, ZIG_1, id="stops-before-leaving-the-range"),
        pytest.param(KEEP, "<2", True, KEEP, id="missing-successor-keeps-accepted"),
        pytest.param(LOOP_A, "<5", True, LOOP_B, id="cycle-keeps-last-accepted"),
        pytest.param(OLD, ">=3", True, None, id="nothing-in-chain-satisfies"),
        pytest.param(OLD, "<2", False, OLD, id="no-upgrade-pin-satisfies"),
        pytest.param(OLD, ">=2", False, None, id="no-upgrade-successor-not-considered"),
    ],
)
def test_digest_constraint_follows_successors_while_they_satisfy_it(
    tmp_path: Path,
    pin: str,
    constraint: str | None,
    upgrade_deprecated: bool,
    expected: str | None,
) -> None:
    entry: dict[str, Any] = {"digest": pin}
    if constraint:
        entry["version"] = constraint
    options: dict[str, Any] = {
        "catalog": _WITH_SUCCESSORS,
        "upgrade_deprecated": upgrade_deprecated,
    }
    if expected is None:
        with pytest.raises(HydrationError, match="Component not found"):
            _hydrate(tmp_path, {"c": entry}, **options)
    else:
        assert _hydrate(tmp_path, {"c": entry}, **options) == expected


def test_one_pin_resolves_per_entry_constraint_without_cross_talk(tmp_path: Path) -> None:
    (tmp_path / "components.yaml").write_text(
        yaml.safe_dump({"old": {"digest": OLD, "version": "<2"}, "latest": {"digest": OLD}}),
        encoding="utf-8",
    )
    tasks = {
        name: {"componentRef": {"url": f"resolve://./components.yaml#{name}"}}
        for name in ("old", "latest", "old-again")
    }
    tasks["old-again"]["componentRef"]["url"] = "resolve://./components.yaml#old"
    pipeline = {"name": "p", "implementation": {"graph": {"tasks": tasks}}}

    resolved = PipelineHydrator(client=_ServerShapedClient(_WITH_SUCCESSORS)).resolve_components(
        pipeline, base_dir=tmp_path
    )

    refs = {
        name: task["componentRef"]
        for name, task in resolved["implementation"]["graph"]["tasks"].items()
    }
    shipped = {
        name: (ref["digest"], ref["spec"]["metadata"]["annotations"]["version"])
        for name, ref in refs.items()
    }
    assert shipped == {
        "old": (OLD, "1.0.0"),
        "latest": (V2, "2.1.0"),
        "old-again": (OLD, "1.0.0"),
    }


def test_digest_miss_falls_back_to_the_entry_local_source(tmp_path: Path) -> None:
    # Older than V1, so the local copy ships only because the digest missed.
    _write_local(tmp_path, "0.1.0")

    ref = _resolve_ref(
        tmp_path, {"c": {"digest": V1, "version": ">=2", "local": "./local.yaml"}}
    )

    assert ref["digest"] != V1
    assert ref["spec"]["metadata"]["annotations"]["version"] == "0.1.0"


def test_constrained_digest_that_does_not_exist_is_an_error_not_a_miss(tmp_path: Path) -> None:
    _write_local(tmp_path, "0.1.0")
    entry = {"digest": "sha256:" + "0" * 64, "version": ">=1", "local": "./local.yaml"}

    with pytest.raises(HydrationError, match="Component not found: sha256:0"):
        _hydrate(tmp_path, {"c": entry})


def test_constrained_digest_does_not_hide_a_forbidden_successor(tmp_path: Path) -> None:
    _write_local(tmp_path, "0.1.0")
    entry = {"digest": GUARDED, "version": "<2", "local": "./local.yaml"}

    with pytest.raises(requests.HTTPError, match="403"):
        _hydrate(tmp_path, {"c": entry}, catalog=_WITH_SUCCESSORS)


def test_custom_digest_resolver_result_is_checked_not_replaced(tmp_path: Path) -> None:
    # The built-in resolver would ship OLD (1.0.0) for "<2"; this one returns 2.1.0.
    def resolve_to_newest(hydrator: Any, value: Any, path: str, base_dir: Any) -> Any:
        return value, {"name": _NAME, "metadata": {"annotations": {"version": "2.1.0"}}}

    _write_local(tmp_path, "0.1.0")
    ref = _resolve_ref(
        tmp_path,
        {"c": {"digest": OLD, "version": "<2", "local": "./local.yaml"}},
        catalog=_WITH_SUCCESSORS,
        component_resolvers={"digest": resolve_to_newest},
    )

    assert ref["spec"]["metadata"]["annotations"]["version"] == "0.1.0"


@pytest.mark.parametrize("locator", [{"name": _NAME}, {"digest": V1}])
def test_constraint_that_parses_to_nothing_is_rejected(
    tmp_path: Path, locator: dict[str, str]
) -> None:
    with pytest.raises(HydrationError, match="Invalid version constraint"):
        _hydrate(tmp_path, {"c": {**locator, "version": "banana"}})
