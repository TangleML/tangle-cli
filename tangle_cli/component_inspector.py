"""Component inspection helpers for OpenAPI-backed Tangle clients."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any, Protocol
from urllib.parse import urljoin, urlparse
from weakref import WeakKeyDictionary

import httpx
import yaml

from tangle_cli.api_transport import _request_headers
from tangle_cli.models import ComponentInfo, ComponentSpec

# ============================================================================
# Client protocol helpers
# ============================================================================


class ComponentApiClient(Protocol):
    """Small subset of :class:`tangle_cli.dynamic_discovery_client.TangleDynamicDiscoveryClient` used here."""

    base_url: str

    def call(self, operation_name: str, **params: Any) -> Any: ...


def _request_path(client: ComponentApiClient, path: str) -> httpx.Response:
    """Fetch an API-origin path using a dynamic-discovery client's auth settings.

    ``component_library.yaml`` is not guaranteed to be represented as an
    OpenAPI operation, but it is served from the same origin as the API. This
    helper preserves the dynamic client's base URL and auth/header precedence
    without depending on the removed legacy hand-written client module.
    """

    custom_request_path = getattr(client, "request_path", None)
    if callable(custom_request_path):
        response = custom_request_path(path)
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()
        return response

    base_url = client.base_url.rstrip("/") + "/"
    url = urljoin(base_url, path.lstrip("/"))
    headers = _request_headers(
        getattr(client, "token", None),
        getattr(client, "header", None),
        getattr(client, "auth_header", None),
        getattr(client, "headers", None),
    )
    response = httpx.request(
        "GET",
        url,
        headers=headers,
        timeout=getattr(client, "timeout", 30.0),
    )
    response.raise_for_status()
    return response


def _component_response(client: ComponentApiClient, digest: str) -> dict[str, Any] | None:
    try:
        data = client.call("components.get", digest=digest)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return None
        raise
    return data if isinstance(data, dict) else None


def _published_components(
    client: ComponentApiClient,
    *,
    include_deprecated: bool = False,
    name_substring: str | None = None,
    published_by_substring: str | None = None,
    digest: str | None = None,
) -> list[dict[str, Any]]:
    result = client.call(
        "published-components.list",
        include_deprecated=include_deprecated,
        name_substring=name_substring,
        published_by_substring=published_by_substring,
        digest=digest,
    )
    if isinstance(result, dict) and isinstance(result.get("published_components"), list):
        return result["published_components"]
    if isinstance(result, list):
        return result
    return []


def _resolve_digest(client: ComponentApiClient, digest: str) -> str:
    current = digest
    seen: set[str] = set()

    while True:
        if current in seen:
            break
        seen.add(current)

        published = _published_components(
            client, digest=current, include_deprecated=True
        )
        if not published:
            break

        meta = published[0]
        if not meta.get("deprecated", False):
            break

        superseded_by = meta.get("superseded_by")
        if not superseded_by:
            break

        current = superseded_by

    return current


# ============================================================================
# Standard component library cache
# ============================================================================

_COMPONENT_LIBRARY_PATH = "/component_library.yaml"

# Component libraries are fetched through authenticated clients and may differ by
# base URL or caller. Cache by client identity so long-lived processes can safely
# use multiple Tangle sessions without leaking library entries across them.
_LibraryState = tuple[dict[str, Any], dict[str, dict[str, Any]]]
_component_libraries_by_client: WeakKeyDictionary[Any, _LibraryState] = WeakKeyDictionary()


def _library_fetch_path(client: ComponentApiClient, url: str) -> str | None:
    """Return a same-origin path for a component-library URL, or None.

    The component library is supplied by the Tangle API origin. Treat URLs
    inside it as untrusted input: relative and same-origin URLs are okay, but
    cross-origin URLs would make the CLI issue arbitrary outbound requests from
    the operator's workstation/CI runner.
    """

    base = client.base_url.rstrip("/") + "/"
    base_parts = urlparse(base)
    target_parts = urlparse(urljoin(base, url))
    if target_parts.scheme != base_parts.scheme or target_parts.netloc != base_parts.netloc:
        return None

    path = target_parts.path or "/"
    if target_parts.query:
        path = f"{path}?{target_parts.query}"
    return path


def _fetch_library_component(client: ComponentApiClient, url: str) -> ComponentSpec:
    path = _library_fetch_path(client, url)
    if path is None:
        return ComponentSpec()

    try:
        response = _request_path(client, path)
        return ComponentSpec.from_yaml(response.text)
    except Exception:
        return ComponentSpec()


def _parse_component_library(raw: dict[str, Any], client: ComponentApiClient) -> dict[str, Any]:
    """Parse the component library YAML into entries with full specs.

    Each entry has ``url``, ``digest``, and ``spec`` (full, unstripped). Use
    :func:`_strip_entry` to produce a lightweight view on demand.
    """

    result: dict[str, Any] = {"folders": []}
    for folder in raw.get("folders", []):
        parsed_components = []
        for comp in folder.get("components", []):
            entry: dict[str, Any] = {}
            url = comp.get("url")
            if url:
                entry["url"] = url

            component = ComponentSpec.from_dict(comp)

            # Fall back to URL fetch if no spec resolved. URLs come from the
            # server-supplied component_library.yaml, so only fetch relative or
            # same-origin URLs through the configured API client.
            if not component.data and url:
                component = _fetch_library_component(client, url)

            if component.data:
                component.ensure_digest()
                entry["spec"] = component.data
            if component.digest:
                entry["digest"] = component.digest
            parsed_components.append(entry)
        result["folders"].append({"name": folder.get("name"), "components": parsed_components})
    return result


def _strip_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of a library entry with the spec stripped of bulky fields."""

    spec = ComponentSpec(data=entry.get("spec") or {})
    result = {k: v for k, v in entry.items() if k != "spec"}
    result["spec"] = spec.stripped_spec
    return result


def _ensure_library_loaded(client: ComponentApiClient) -> _LibraryState:
    """Fetch and cache the component library for this client if needed."""

    cached = _component_libraries_by_client.get(client)
    if cached is not None:
        return cached

    try:
        response = _request_path(client, _COMPONENT_LIBRARY_PATH)
        raw = yaml.safe_load(response.text)
        parsed = _parse_component_library(raw, client)
        cache: dict[str, dict[str, Any]] = {}
        for folder in parsed.get("folders", []):
            for comp in folder.get("components", []):
                name = (comp.get("spec") or {}).get("name", "")
                if name:
                    cache[name.lower()] = comp
        state = (parsed, cache)
    except Exception:
        state = ({"folders": []}, {})

    _component_libraries_by_client[client] = state
    return state


def get_standard_library(client: ComponentApiClient) -> dict[str, Any]:
    """Return the standard component library organised by folders.

    Each component entry has a stripped spec (no implementation blocks), an
    optional ``url``, and a ``digest``.
    """

    library_full, _ = _ensure_library_loaded(client)
    return {
        "folders": [
            {
                "name": folder.get("name"),
                "components": [_strip_entry(comp) for comp in folder.get("components", [])],
            }
            for folder in library_full.get("folders", [])
        ],
    }


# ============================================================================
# Core functions (usable by wrappers and CLIs)
# ============================================================================


_TRANSPARENT_IMAGE_PREFIXES = ("python:", "ubuntu:", "debian:", "alpine:")


def transparency_check(spec: ComponentSpec) -> tuple[bool, str]:
    """Check if a component's definition is transparent (source-inspectable).

    Returns a ``(transparent, reason)`` tuple. The *reason* is a short
    human-readable explanation of **why** the component was classified as
    transparent or opaque so that consuming agents can understand the decision
    before applying their own judgment.
    """

    ann = spec.annotations

    if ann.get("python_original_code"):
        return True, "inline Python source code embedded in annotations"

    canonical = ann.get("canonical_location")
    if isinstance(canonical, str) and canonical.startswith(("https://", "http://")):
        return True, f"canonical_location annotation points to {canonical}"

    if ann.get("git_remote_url") and (
        ann.get("component_yaml_path") or ann.get("git_relative_dir")
    ):
        return True, f"git source metadata links to {ann['git_remote_url']}"

    impl = spec.implementation or {}
    container = impl.get("container", {})
    image = container.get("image", "")
    if any(image.startswith(prefix) for prefix in _TRANSPARENT_IMAGE_PREFIXES):
        return (
            True,
            f"uses standard public base image ({image})"
            " — code logic is in the component definition, not hidden in the container",
        )

    return False, "no inline source, canonical location, git metadata, or standard public image found"


def _resolve_git_source(spec: ComponentSpec) -> dict[str, Any] | None:
    """Extract git annotations and resolve to GitHub URLs and local paths."""

    annotations = spec.annotations
    git_url = annotations.get("git_remote_url")
    if not git_url:
        return None

    sha = annotations.get("git_remote_sha", "")
    branch = annotations.get("git_remote_branch", "main")
    component_yaml = annotations.get("component_yaml_path")
    docs_path = annotations.get("documentation_path")
    dockerfile = annotations.get("dockerfile_path")
    relative_dir = annotations.get("git_relative_dir")

    repo_base = git_url.removesuffix(".git")
    ref = sha or branch

    def _full_path(rel_path: str) -> str:
        """Resolve a path relative to git_relative_dir into a git-root-relative path."""

        if relative_dir:
            return str(PurePosixPath(relative_dir, rel_path))
        return str(PurePosixPath(rel_path))

    source: dict[str, Any] = {}
    if component_yaml:
        source["component_yaml"] = f"{repo_base}/blob/{ref}/{_full_path(component_yaml)}"
    if docs_path:
        source["docs"] = f"{repo_base}/blob/{ref}/{_full_path(docs_path)}"
    if dockerfile:
        source["dockerfile"] = f"{repo_base}/blob/{ref}/{_full_path(dockerfile)}"
    if relative_dir:
        source["source_dir"] = f"{repo_base}/tree/{ref}/{relative_dir}"

    return source if source else None


def _enrich_with_spec(
    info: ComponentInfo,
    client: ComponentApiClient,
) -> None:
    """Fetch the full component data and attach it to *info*."""

    if not info.digest:
        return
    try:
        info.component_spec = _get_component_spec(client, info.digest)
    except Exception as e:
        info.spec_error = str(e)


def _get_component_spec(client: ComponentApiClient, digest: str) -> ComponentSpec | None:
    data = _component_response(client, digest)
    return ComponentSpec.from_dict(data) if data is not None else None


def inspect_by_digest(
    client: ComponentApiClient,
    digest: str,
    full_spec: bool = False,
    follow_deprecated: bool = False,
) -> dict[str, Any]:
    """Inspect a single component by digest.

    Fetches the full spec via the ``components.get`` OpenAPI operation and
    publication metadata via ``published-components.list``.
    """

    if follow_deprecated:
        resolved = _resolve_digest(client, digest)
        if resolved != digest:
            digest = resolved

    comp = _get_component_spec(client, digest)
    if comp is None:
        _, library_cache = _ensure_library_loaded(client)
        for entry in library_cache.values():
            if entry.get("digest") == digest:
                out = _strip_entry(entry) if not full_spec else dict(entry)
                return {
                    "status": "success",
                    "source": "component_library",
                    "transparent": True,
                    "transparency_reason": "curated standard component from the component library",
                    "name": (entry.get("spec") or {}).get("name", ""),
                    **out,
                }
        return {"status": "not_found", "digest": digest, "error": f"Component not found: {digest}"}

    published = _published_components(client, digest=digest, include_deprecated=True)
    pub_info = published[0] if published else None

    if pub_info:
        info = ComponentInfo.from_dict(pub_info)
    else:
        info = ComponentInfo(digest=digest)
        info.version = comp.version if comp else None

    info.component_spec = comp

    result: dict[str, Any] = {"status": "success"}
    if not pub_info:
        result["published"] = False
    if comp:
        result["name"] = comp.name
        transparent, transparency_reason = transparency_check(comp)
        result["transparent"] = transparent
        result["transparency_reason"] = transparency_reason
    result.update(info.to_dict(strip_spec=not full_spec))
    if comp:
        git_source = _resolve_git_source(comp)
        if git_source:
            result["source"] = git_source
    return result


def inspect_by_name(
    client: ComponentApiClient,
    name: str,
    include_all_versions: bool = False,
    include_deprecated: bool = False,
    full_spec: bool = False,
    published_by: str | None = None,
) -> dict[str, Any]:
    """Inspect component(s) by name."""

    published = _published_components(
        client,
        name_substring=name,
        include_deprecated=include_deprecated,
        published_by_substring=published_by,
    )
    published = [
        c for c in published if c.get("name", "").lower() == name.lower()
    ]

    if not published:
        _, library_cache = _ensure_library_loaded(client)
        entry = library_cache.get(name.lower())
        if entry:
            out = _strip_entry(entry) if not full_spec else dict(entry)
            return {
                "status": "success",
                "source": "component_library",
                "transparent": True,
                "transparency_reason": "curated standard component from the component library",
                "name": name,
                **out,
            }
        return {
            "status": "not_found",
            "query": name,
            "message": f"No published component found with name: {name}",
        }

    def _version_key(c: dict[str, Any]) -> tuple[int, ...]:
        """Parse version string into numeric tuple for proper sorting."""

        v = c.get("version") or "0.0.1"
        try:
            return tuple(int(p) for p in v.split("."))
        except ValueError:
            return (0, 0, 1)

    published.sort(key=_version_key, reverse=True)

    if not include_all_versions:
        published = published[:1]

    versions: list[dict[str, Any]] = []
    for pub in published:
        info = ComponentInfo.from_dict(pub)
        _enrich_with_spec(info, client)
        entry = info.to_dict(strip_spec=not full_spec)
        if info.component_spec:
            transparent, transparency_reason = transparency_check(info.component_spec)
            entry["transparent"] = transparent
            entry["transparency_reason"] = transparency_reason
            git_source = _resolve_git_source(info.component_spec)
            if git_source:
                entry["source"] = git_source
        versions.append(entry)

    return {
        "status": "success",
        "name": name,
        "version_count": len(versions),
        "versions": versions,
    }


def search_components(
    client: ComponentApiClient,
    name: str | None = None,
    include_deprecated: bool = False,
    published_by: str | None = None,
    digest: str | None = None,
) -> dict[str, Any]:
    """Search for published components."""

    components = _published_components(
        client,
        name_substring=name,
        include_deprecated=include_deprecated,
        published_by_substring=published_by,
        digest=digest,
    )

    results = []
    for comp in components:
        results.append({
            "name": comp.get("name"),
            "digest": comp.get("digest"),
            "version": comp.get("version"),
            "deprecated": comp.get("deprecated", False),
            "description": (comp.get("description") or "")[:200],
        })

    return {
        "status": "success",
        "query": name,
        "count": len(results),
        "components": results,
    }
