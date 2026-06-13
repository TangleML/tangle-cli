"""Pipeline hydrator for expanding local Tangle pipeline YAML files.

This module is intentionally a close OSS port of
``tangle_deploy.pipeline_hydrator``.  The generic reference-resolution code and
method names are preserved where possible so future upstream diffs are easy to
compare.  Shopify/Oasis-only infrastructure integrations are omitted, and
Docker/from-container materialization paths raise explicit unsupported errors.
"""

from __future__ import annotations

import copy
import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from inspect import Parameter, signature
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from . import utils
from .api_transport import DEFAULT_TIMEOUT_SECONDS
from .component_generator import regenerate_yaml
from .hydration_trust import is_trusted_python_source, trusted_python_source_guidance
from .logger import Logger, get_default_logger
from .utils import add_official_prefix

if TYPE_CHECKING:
    from .client import TangleApiClient
    from .models import ComponentInfo


class HydrationError(RuntimeError):
    """Raised when a pipeline cannot be hydrated safely in OSS mode."""


class UnsupportedHydrationFeatureError(HydrationError):
    """Raised for TD features intentionally excluded from the OSS CLI."""


@dataclass(frozen=True)
class HydratedPipeline:
    """Result returned by :meth:`PipelineHydrator.hydrate_file`."""

    data: dict[str, Any]
    content: str
    resolved_count: int


@dataclass(frozen=True)
class ResolverContext:
    """Structured context passed to component resolvers and URI hooks.

    The legacy resolver signature ``(hydrator, value, path, base_dir)`` remains
    supported. New downstream resolvers can accept a fifth ``context`` argument
    to avoid reaching into hydrator internals for source/base/output/trust state.
    """

    kind: str
    value: Any
    path: str
    base_dir: Path | None
    base_dirs: tuple[Path, ...]
    source_path: Path | None = None
    output_folder: Path | None = None
    verbose: bool = False
    trusted_python_sources: tuple[str, ...] = ()
    allow_all_hydration: bool = False
    error_policy: str = "warn"
    resolution_overrides: Mapping[str, Any] | None = None


ComponentResolver = Callable[..., tuple[str, dict[str, Any]] | None]
UriReader = Callable[["PipelineHydrator", str, ResolverContext], str | None]
UriWriter = Callable[["PipelineHydrator", str, str, ResolverContext], None]

COMPONENT_RESOLVERS: dict[str, ComponentResolver] = {}
URI_READERS: dict[str, UriReader] = {}
URI_WRITERS: dict[str, UriWriter] = {}


def register_component_resolver(kind: str, resolver: ComponentResolver) -> None:
    """Register or replace a component resolver.

    ``kind`` is a reference kind or URI scheme such as ``file``, ``resolve``,
    ``http``, ``https``, ``name``, ``digest``, ``local``, or
    ``local_from_python``.  Downstream packages can monkey-patch this registry;
    for example, tangle-deploy can add ``local_from_docker`` without forking
    the hydrator.
    """

    COMPONENT_RESOLVERS[kind] = resolver


def available_component_resolvers() -> list[str]:
    """Return registered resolver kinds in stable display order."""

    return sorted(COMPONENT_RESOLVERS)


def register_uri_reader(scheme: str, reader: UriReader) -> None:
    """Register a native-free URI reader hook for schemes such as ``gs``.

    OSS provides the dispatch seam only; downstream packages own credentials and
    scheme-specific SDK dependencies.
    """

    URI_READERS[scheme] = reader


def register_uri_writer(scheme: str, writer: UriWriter) -> None:
    """Register a native-free URI writer hook for schemes such as ``gs``."""

    URI_WRITERS[scheme] = writer


def available_uri_readers() -> list[str]:
    """Return registered URI reader schemes in stable display order."""

    return sorted(URI_READERS)


def available_uri_writers() -> list[str]:
    """Return registered URI writer schemes in stable display order."""

    return sorted(URI_WRITERS)


def _available_resolvers_text(resolvers: Mapping[str, Any]) -> str:
    return ", ".join(sorted(resolvers)) or "(none)"


def render_template(
    template_path: Path,
    context: dict[str, Any],
    overrides: dict[str, Any] | None = None,
) -> str:
    """Render a Jinja2 template with the given context.

    Ported from TD's ``render_template`` helper, including ``include_raw``.
    """

    from jinja2 import Environment, FileSystemLoader

    template_dir = template_path.parent
    template_name = template_path.name
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        keep_trailing_newline=True,
    )

    def include_raw(path: str) -> str:
        """Include a file's contents without Jinja2 processing."""
        assert env.loader is not None
        return env.loader.get_source(env, path)[0]

    env.globals["include_raw"] = include_raw
    template = env.get_template(template_name)

    merged_context = dict(context)
    if overrides:
        merged_context.update(overrides)

    return template.render(**merged_context)


class PipelineHydrator:
    """Hydrates pipeline YAML by resolving component references.

    This class mirrors TD's ``PipelineHydrator`` shape.  Supported generic refs:
    ``digest``, ``name``, ``url`` (``file://``, ``http(s)://``, ``resolve://``),
    resolve-config ``local`` and ``local_from_python``.  Unsupported TD refs:
    GCS and Docker/from-container materialization.
    """

    def __init__(
        self,
        client: TangleApiClient | None = None,
        upgrade_deprecated: bool = True,
        verbose: bool = False,
        enable_resolution: bool = True,
        postprocess_task: Callable[[str, dict[str, Any], str], dict[str, Any]] | None = None,
        logger: Logger | None = None,
        resolution_overrides: dict[str, Any] | None = None,
        *,
        base_url: str | None = None,
        token: str | None = None,
        auth_header: str | None = None,
        header: list[str] | None = None,
        include_env_credentials: bool = True,
        component_resolvers: Mapping[str, ComponentResolver] | None = None,
        uri_readers: Mapping[str, UriReader] | None = None,
        uri_writers: Mapping[str, UriWriter] | None = None,
        trusted_python_sources: list[str] | None = None,
        allow_all_hydration: bool = False,
        recursive_context: str | None = None,
        error_policy: str = "warn",
    ) -> None:
        self.client = client
        self._client_options = {
            "base_url": base_url,
            "token": token,
            "auth_header": auth_header,
            "header": header,
            "include_env_credentials": include_env_credentials,
        }
        self.cache: dict[str, Any] = {}
        self.upgrade_deprecated = upgrade_deprecated
        self.verbose = verbose
        self.enable_resolution = enable_resolution
        self._postprocess_callback = postprocess_task
        self.log = logger or get_default_logger()
        self.component_resolvers: dict[str, ComponentResolver] = dict(COMPONENT_RESOLVERS)
        if component_resolvers:
            self.component_resolvers.update(component_resolvers)
        self.uri_readers: dict[str, UriReader] = dict(URI_READERS)
        if uri_readers:
            self.uri_readers.update(uri_readers)
        self.uri_writers: dict[str, UriWriter] = dict(URI_WRITERS)
        if uri_writers:
            self.uri_writers.update(uri_writers)
        self.resolution_overrides: dict[str, Any] = resolution_overrides or {}
        self.trusted_python_sources = trusted_python_sources or []
        self.allow_all_hydration = allow_all_hydration
        self.recursive_context = self._recursive_context_value(recursive_context)
        self._global_params: dict[str, Any] = {}
        self.error_policy = error_policy
        self._resolution_overrides_str: dict[str, str] = {
            k: str(v) for k, v in self.resolution_overrides.items()
        }

    def _api_client(self) -> TangleApiClient:
        if self.client is None:
            from . import client as client_module

            self.client = client_module.TangleApiClient(
                timeout=DEFAULT_TIMEOUT_SECONDS,
                **self._client_options,
            )
        return self.client

    @staticmethod
    def _recursive_context_value(value: Any) -> str | None:
        if value is None:
            return None
        raw = getattr(value, "value", value)
        normalized = str(raw).replace("_", "-").lower()
        if normalized in {"parent-priority", "parent"}:
            return "parent-priority"
        if normalized in {"child-priority", "child"}:
            return "child-priority"
        raise ValueError(f"Unsupported recursive_context: {value!r}")

    def _cache_key(self, ref_type: str, ref_value: str) -> str:
        """Compute a cache key for a component reference."""
        key = f"{ref_type}:{ref_value}"
        if self.recursive_context and self._global_params:
            params_hash = hash(json.dumps(self._global_params, sort_keys=True, default=str))
            return f"{key}:ctx={params_hash}"
        return key

    def _merge_with_global_params(self, child_params: dict[str, Any]) -> dict[str, Any]:
        """Merge child template params with inherited recursive-context params.

        ``parent-priority`` means inherited params win on conflicts;
        ``child-priority`` means the child template config wins.
        """

        if not self.recursive_context or not self._global_params:
            return dict(child_params)
        if self.recursive_context == "parent-priority":
            merged = dict(child_params)
            merged.update(self._global_params)
            return merged
        merged = dict(self._global_params)
        merged.update(child_params)
        return merged

    def _resolver_base_dirs(self, base_dir: Path | None) -> tuple[Path, ...]:
        dirs = [path.resolve() for path in (base_dir, Path.cwd()) if path is not None]
        seen: set[Path] = set()
        result: list[Path] = []
        for path in dirs:
            if path not in seen:
                seen.add(path)
                result.append(path)
        return tuple(result)

    def _resolve_context_path(self, value: Any, base_dir: Path | None) -> Path | None:
        if not value:
            return None
        path = Path(str(value))
        if path.is_absolute():
            return path.resolve()
        return (base_dir / path).resolve() if base_dir is not None else path.resolve()

    def make_resolver_context(
        self,
        kind: str,
        value: Any,
        path: str,
        base_dir: Path | None,
    ) -> ResolverContext:
        """Build the structured context passed to downstream resolver hooks."""

        source_path = None
        output_folder = None
        if isinstance(value, (str, Path)) and "://" not in str(value):
            source_path = self._resolve_context_path(value, base_dir)
        elif isinstance(value, dict):
            source_path = self._resolve_context_path(
                value.get("file") or value.get("source"), base_dir
            )
            output_folder = self._resolve_context_path(value.get("output_folder"), base_dir)
        return ResolverContext(
            kind=kind,
            value=value,
            path=path,
            base_dir=base_dir,
            base_dirs=self._resolver_base_dirs(base_dir),
            source_path=source_path,
            output_folder=output_folder,
            verbose=self.verbose,
            trusted_python_sources=tuple(self.trusted_python_sources),
            allow_all_hydration=self.allow_all_hydration,
            error_policy=self.error_policy,
            resolution_overrides=self.resolution_overrides,
        )

    @staticmethod
    def _accepts_resolver_context(resolver: ComponentResolver) -> bool:
        try:
            params = signature(resolver).parameters.values()
        except (TypeError, ValueError):
            return True
        positional = [
            p for p in params
            if p.kind in {Parameter.POSITIONAL_ONLY, Parameter.POSITIONAL_OR_KEYWORD}
        ]
        return any(p.kind == Parameter.VAR_POSITIONAL for p in params) or len(positional) >= 5

    def _call_component_resolver(
        self,
        resolver: ComponentResolver,
        value: Any,
        path: str,
        base_dir: Path | None,
        context: ResolverContext,
    ) -> tuple[str, dict[str, Any]] | None:
        if self._accepts_resolver_context(resolver):
            return resolver(self, value, path, base_dir, context)
        return resolver(self, value, path, base_dir)

    @staticmethod
    def _uri_scheme(uri: str) -> str | None:
        if "://" not in uri:
            return None
        return uri.split("://", 1)[0]

    def _read_uri_text(
        self,
        uri: str,
        kind: str,
        context: ResolverContext | None = None,
    ) -> str | None:
        scheme = self._uri_scheme(uri)
        if not scheme or scheme == "file":
            path = Path(uri[7:] if uri.startswith("file://") else uri)
            return path.read_text(encoding="utf-8")
        reader = self.uri_readers.get(scheme)
        if reader is None:
            raise UnsupportedHydrationFeatureError(
                f"Unsupported {kind} URI scheme {scheme!r}. Registered URI readers: "
                f"{_available_resolvers_text(self.uri_readers)}"
            )
        hook_context = context or self.make_resolver_context(scheme, uri, kind, None)
        return reader(self, uri, hook_context)

    def _write_uri_text(
        self,
        uri: str,
        content: str,
        context: ResolverContext | None = None,
    ) -> None:
        scheme = self._uri_scheme(uri)
        if not scheme or scheme == "file":
            path = Path(uri[7:] if uri.startswith("file://") else uri)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return
        writer = self.uri_writers.get(scheme)
        if writer is None:
            raise UnsupportedHydrationFeatureError(
                f"Unsupported output URI scheme {scheme!r}. Registered URI writers: "
                f"{_available_resolvers_text(self.uri_writers)}"
            )
        hook_context = context or self.make_resolver_context(scheme, uri, "output", None)
        writer(self, uri, content, hook_context)

    def available_component_resolvers(self) -> list[str]:
        """Return resolver kinds available on this hydrator instance."""

        return sorted(self.component_resolvers)

    def register_component_resolver(self, kind: str, resolver: ComponentResolver) -> None:
        """Register or replace a resolver on this hydrator instance."""

        self.component_resolvers[kind] = resolver

    def _unsupported_resolver(self, kind: str) -> UnsupportedHydrationFeatureError:
        return UnsupportedHydrationFeatureError(
            f"Unsupported component resolver {kind!r}. Available resolvers: "
            f"{_available_resolvers_text(self.component_resolvers)}"
        )

    def _resolve_registered_component(
        self,
        kind: str,
        value: Any,
        path: str,
        base_dir: Path | None,
    ) -> tuple[str, dict[str, Any]] | None:
        resolver = self.component_resolvers.get(kind)
        if resolver is None:
            raise self._unsupported_resolver(kind)
        context = self.make_resolver_context(kind, value, path, base_dir)
        return self._call_component_resolver(resolver, value, path, base_dir, context)

    def fetch_component(self, digest: str) -> tuple[str, dict[str, Any]]:
        """Fetch a component, optionally following deprecation successors."""
        client = self._api_client()
        current_digest = client.resolve_digest(digest) if self.upgrade_deprecated else digest
        spec = client.get_component_spec(current_digest).data
        if self.verbose:
            self.log.info(f"   [verbose] get_component_spec({current_digest}):")
            self.log.info(json.dumps(spec, indent=2, default=str))
        return current_digest, copy.deepcopy(spec)

    def _fetch_component_by_digest(
        self,
        digest: str,
        path: str,
        base_dir: Path | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Fetch a component by digest and return as dict."""
        self.log.info(f"   Fetching component: {digest[:16]}... ({path})")
        return self.fetch_component(digest)

    def _find_latest_version_component(
        self,
        components: list[ComponentInfo],
    ) -> tuple[str, dict[str, Any]]:
        """Find the component with the highest version from a list."""
        client = self._api_client()

        def _fetch(digest: str) -> tuple[str, dict[str, Any]]:
            spec = client.get_component_spec(digest)
            if not spec:
                raise HydrationError(f"Component not found: {digest}")
            return digest, copy.deepcopy(spec.data)

        digests = [c.digest for c in components if c.digest]
        if not digests:
            raise HydrationError("No components with a digest found")
        if len(digests) == 1:
            return _fetch(digests[0])

        versioned_components: list[tuple[str, dict[str, Any], str]] = []
        for digest in digests:
            try:
                _, spec = _fetch(digest)
                version = utils.get_version_from_data(spec)
                if version:
                    versioned_components.append((digest, spec, version))
            except Exception as exc:
                self.log.warn(f"   ⚠️ Failed to fetch component {digest[:16]}...: {exc}")

        if not versioned_components:
            return _fetch(digests[0])

        best_digest, best_spec, best_version = versioned_components[0]
        for digest, spec, version in versioned_components[1:]:
            if utils.compare_versions(version, best_version) > 0:
                best_digest, best_spec, best_version = digest, spec, version
        return best_digest, best_spec

    def _fetch_component_by_name(
        self,
        component_name: str,
        path: str,
        base_dir: Path | None = None,
    ) -> tuple[str, dict[str, Any]] | None:
        """Fetch a component by name and return as dict."""
        self.log.info(f"   Finding component by name: {component_name}... ({path})")
        search_names = [component_name, add_official_prefix(component_name)]
        existing = self._api_client().find_existing_components(search_names, verbose=False)
        if not existing:
            self.log.warn(f"   ⚠️ No component found with name: {component_name}")
            return None
        found_digest, spec = self._find_latest_version_component(existing)
        self.log.info(f"   Found digest: {found_digest[:16]}...")
        return found_digest, spec

    def _fetch_component_by_url(
        self,
        url: str,
        path: str,
        base_dir: Path | None = None,
    ) -> tuple[str, dict[str, Any]] | None:
        """Fetch a component by URL and return as dict."""
        scheme = url.split("://", 1)[0] if "://" in url else "url"
        if scheme in self.uri_readers:
            return self._fetch_component_from_uri(url, path, base_dir)
        return self._resolve_registered_component(scheme, url, path, base_dir)

    def fetch_remote_component(
        self,
        url: str,
        path: str,
        base_dir: Path | None = None,
    ) -> tuple[str, dict[str, Any]] | None:
        """Fetch a remote HTTP(S) component.

        Kept as an overridable hook for downstream packages that need custom
        transport, auth, mirrors, or auditing.
        """
        self.log.info(f"   Downloading component from URL: {url}... ({path})")
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                yaml_text = response.read().decode("utf-8")
            spec = yaml.safe_load(yaml_text)
        except urllib.error.URLError as exc:
            raise HydrationError(f"Failed to download YAML from {url}: {exc}") from exc
        except yaml.YAMLError as exc:
            raise HydrationError(f"Failed to parse downloaded YAML from {url}: {exc}") from exc

        if not isinstance(spec, dict):
            raise HydrationError(f"Component YAML at {url} must be a mapping")
        digest = utils.compute_text_digest(yaml_text)
        self.log.info(
            f"   ✅ Downloaded component: {spec.get('name', 'unknown')} "
            f"(digest: {digest[:16]}...)"
        )
        return digest, spec

    def _fetch_component_from_uri(
        self,
        url: str,
        path: str,
        base_dir: Path | None = None,
    ) -> tuple[str, dict[str, Any]] | None:
        """Fetch component YAML through a registered URI reader hook."""

        context = self.make_resolver_context(self._uri_scheme(url) or "url", url, path, base_dir)
        yaml_text = self._read_uri_text(url, "component", context)
        if yaml_text is None:
            return None
        try:
            spec = yaml.safe_load(yaml_text)
        except yaml.YAMLError as exc:
            raise HydrationError(f"Failed to parse component YAML from {url}: {exc}") from exc
        if not isinstance(spec, dict):
            raise HydrationError(f"Component YAML at {url} must be a mapping")
        if "template_file" in spec:
            raise UnsupportedHydrationFeatureError(
                f"template_file configs are not supported for non-local URI {url!r}"
            )
        digest = utils.compute_text_digest(yaml_text)
        self.log.info(
            f"   ✅ Loaded component: {spec.get('name', 'unknown')} "
            f"(digest: {digest[:16]}...)"
        )
        return digest, spec

    def load_gcs_uri(
        self,
        url: str,
        path: str,
        base_dir: Path | None = None,
    ) -> tuple[str, dict[str, Any]] | None:
        """Compatibility hook for downstream GCS support via URI readers."""

        return self._fetch_component_from_uri(url, path, base_dir)

    def _render_template_config(
        self,
        file_path: Path,
        config: dict[str, Any],
        overrides: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any]] | None:
        """If config contains template_file, render the Jinja2 template."""
        if "template_file" not in config:
            return None

        template_path = config["template_file"]
        full_template_path = (file_path.parent / template_path).resolve()
        if not full_template_path.exists():
            self.log.warn(f"   ⚠️ Template file not found: {full_template_path}")
            return None

        context = {k: v for k, v in config.items() if k != "template_file"}
        if overrides:
            context.update(overrides)
        self.log.info(f"   🔧 Rendering template: {template_path}")
        rendered = render_template(full_template_path, context)
        spec = yaml.safe_load(rendered)
        if not isinstance(spec, dict):
            self.log.warn(f"   ⚠️ Rendered template produced invalid YAML: {full_template_path}")
            return None
        return rendered, spec

    def _fetch_component_from_file_url(
        self,
        url: str,
        display_path: str,
        base_dir: Path | None = None,
    ) -> tuple[str, dict[str, Any]] | None:
        """Fetch a component from a file:// URL.

        Supports absolute and relative ``file://`` URLs and template configs,
        matching TD's generic local-file behavior.
        """
        file_path = url[7:]
        self.log.info(f"   Loading component from file URL: {url}... ({display_path})")
        path_obj = Path(file_path)
        if not path_obj.is_absolute() and base_dir:
            path_obj = (base_dir / path_obj).resolve()
        else:
            path_obj = path_obj.resolve()
        if not path_obj.exists():
            self.log.warn(f"   ⚠️ Component file not found: {path_obj}")
            return None

        try:
            yaml_text = path_obj.read_text(encoding="utf-8")
            spec = yaml.safe_load(yaml_text)
        except Exception as exc:
            raise HydrationError(f"Error reading component file {path_obj}: {exc}") from exc
        if not isinstance(spec, dict):
            raise HydrationError(f"Component file {path_obj} must contain a mapping")

        if "template_file" in spec:
            merged_params: dict[str, Any] | None = None
            if self.recursive_context and self._global_params:
                child_params = {k: v for k, v in spec.items() if k != "template_file"}
                merged_params = self._merge_with_global_params(child_params)
                spec = {"template_file": spec["template_file"], **merged_params}
            result = self._render_template_config(path_obj, spec)
            if result is None:
                return None
            yaml_text, spec = result
            if merged_params is not None:
                spec["_recursive_params"] = merged_params

        # Match TD provenance behavior: nested refs inside a loaded component
        # resolve relative to the component file that contains them, not the
        # original top-level pipeline file.
        spec["_source_dir"] = str(path_obj.parent)

        digest = utils.compute_text_digest(yaml_text)
        self.log.info(
            f"   ✅ Loaded component: {spec.get('name', 'unknown')} "
            f"(digest: {digest[:16]}...)"
        )
        return digest, spec

    def _fetch_component_by_resolve_url(
        self,
        url: str,
        path: str,
        base_dir: Path | None = None,
    ) -> tuple[str, dict[str, Any]] | None:
        """Fetch a component using a resolve:// URL pointing to a config."""
        file_path = url[len("resolve://"):]
        fragment: str | None = None
        if "#" in file_path:
            file_path, fragment = file_path.rsplit("#", 1)

        self.log.info(f"   Resolving component via config: {url}... ({path})")

        scheme = self._uri_scheme(file_path)
        source: str
        if scheme and scheme != "file":
            source = file_path
            nested_base_dir = None
            text = self._read_uri_text(
                file_path,
                "resolve config",
                self.make_resolver_context(scheme, file_path, path, base_dir),
            )
            if text is None:
                return None
        else:
            raw_path = file_path[7:] if file_path.startswith("file://") else file_path
            path_obj = Path(raw_path)
            if not path_obj.is_absolute() and base_dir:
                path_obj = (base_dir / path_obj).resolve()
            else:
                path_obj = path_obj.resolve()
            if not path_obj.exists():
                self.log.warn(f"   ⚠️ Resolve config not found: {path_obj}")
                return None
            source = str(path_obj)
            nested_base_dir = path_obj.parent
            try:
                text = path_obj.read_text(encoding="utf-8")
            except Exception as exc:
                raise HydrationError(f"Error reading resolve config {path_obj}: {exc}") from exc

        try:
            text = utils.expand_vars(text, self._resolution_overrides_str)
            config = yaml.safe_load(text)
        except utils.UnsetVarError as exc:
            self.log.warn(f"   ⚠️ Resolve config {source}: unset variable {exc}")
            return None
        except Exception as exc:
            raise HydrationError(f"Error parsing resolve config {source}: {exc}") from exc

        if fragment is not None:
            if not isinstance(config, dict) or fragment not in config:
                self.log.warn(
                    f"   ⚠️ Fragment '{fragment}' not found in resolve config {source}"
                )
                return None
            entry = config[fragment]
            defaults = config.get("_defaults")
            if isinstance(defaults, dict) and isinstance(entry, (dict, list)):
                config = utils.apply_defaults(entry, defaults)
            else:
                config = entry

        return self._resolve_from_config(config, path, nested_base_dir)

    def _resolve_from_config(
        self,
        config: dict[str, Any] | list[dict[str, Any]],
        path: str,
        base_dir: Path | None = None,
    ) -> tuple[str, dict[str, Any]] | None:
        """Resolve a component from a parsed resolve config."""
        entries = config if isinstance(config, list) else [config]
        for i, entry in enumerate(entries):
            if not isinstance(entry, dict):
                self.log.warn(f"   ⚠️ Resolve config entry {i} is not a dict, skipping")
                continue
            result = self._try_resolve_entry(entry, path, base_dir)
            if result is not None:
                return result
        self.log.warn(f"   ⚠️ No resolve config entry matched at {path}")
        return None

    def _try_resolve_entry(
        self,
        entry: dict[str, Any],
        path: str,
        base_dir: Path | None = None,
    ) -> tuple[str, dict[str, Any]] | None:
        """Try to resolve a single resolve-config entry.

        Ported from TD, with resolver dispatch made registry-backed.
        """
        primary = self._resolve_primary(entry, path, base_dir)
        local_result = self._resolve_local_side(entry, path, base_dir)

        if not primary and not local_result:
            return None
        if not primary:
            self.log.info("   Resolve: primary source failed, using local source")
            return local_result
        if not local_result:
            return primary
        return self._pick_higher_version(primary, local_result, path)

    def _resolve_primary(
        self,
        entry: dict[str, Any],
        path: str,
        base_dir: Path | None = None,
    ) -> tuple[str, dict[str, Any]] | None:
        """Resolve the primary source from a resolve-config entry."""
        for kind in ("digest", "url"):
            if kind not in entry:
                continue
            value = entry[kind]
            if kind == "digest":
                self.log.info(f"   Resolve: trying digest={str(value)[:16]}...")
            elif kind == "url":
                self.log.info(f"   Resolve: trying url={value}")
            return self._resolve_registered_component(kind, value, path, base_dir)
        if "name" in entry:
            return self._resolve_by_name_with_filters(entry)
        if any(kind in entry for kind in self._resolve_entry_kinds()):
            return None
        self.log.warn(
            "   ⚠️ Resolve config entry has no registered resolver key. "
            f"Available resolvers: {_available_resolvers_text(self.component_resolvers)}"
        )
        return None

    def _resolve_local_side(
        self,
        entry: dict[str, Any],
        path: str,
        base_dir: Path | None = None,
    ) -> tuple[str, dict[str, Any]] | None:
        for kind in self._resolve_entry_kinds():
            if kind not in entry or kind in {"digest", "url", "name"}:
                continue
            value = entry[kind]
            if value:
                return self._resolve_registered_component(kind, value, path, base_dir)
        return None

    def _resolve_entry_kinds(self) -> tuple[str, ...]:
        builtin_kinds = (
            "digest",
            "url",
            "name",
            "local",
            "local_from_python",
            "local_from_docker",
            "local_from_container",
            "from_docker",
            "from_container",
            "from-docker",
            "from-container",
        )
        return tuple(dict.fromkeys((*builtin_kinds, *self.component_resolvers)))

    def _resolve_by_name_with_filters(
        self,
        entry: dict[str, Any],
    ) -> tuple[str, dict[str, Any]] | None:
        """Resolve a component by name with optional filters."""
        component_name = entry["name"]
        publisher = entry.get("publisher")
        version_constraint = entry.get("version")
        required_annotations = entry.get("annotations")

        search_names = [component_name, add_official_prefix(component_name)]
        candidates = self._api_client().find_existing_components(
            search_names,
            verbose=False,
            published_by=publisher,
        )
        if not candidates:
            self.log.info(f"   Resolve: no candidates for name={component_name}")
            return None

        if version_constraint:
            if not _parse_version_constraint(version_constraint):
                raise HydrationError(f"Invalid version constraint: '{version_constraint}'")
            candidates = _filter_by_version_constraint(candidates, version_constraint)
            if not candidates:
                self.log.info(
                    f"   Resolve: no candidates matching version {version_constraint}"
                )
                return None

        if required_annotations:
            candidates = self._filter_by_annotations(candidates, required_annotations)
            if not candidates:
                self.log.info(
                    f"   Resolve: no candidates matching annotations {required_annotations}"
                )
                return None

        found_digest, spec = self._find_latest_version_component(candidates)
        self.log.info(
            f"   Resolve: matched {spec.get('name', 'unknown')} "
            f"(digest: {found_digest[:16]}...)"
        )
        return found_digest, spec

    def _filter_by_annotations(
        self,
        candidates: list[ComponentInfo],
        required_annotations: dict[str, Any],
    ) -> list[ComponentInfo]:
        result: list[ComponentInfo] = []
        for candidate in candidates:
            if not candidate.digest:
                continue
            try:
                spec = self._api_client().get_component_spec(candidate.digest).data
            except Exception:
                continue
            annotations = spec.get("metadata", {}).get("annotations", {})
            if _annotations_match(annotations, required_annotations):
                result.append(candidate)
        return result

    def _resolve_local_file(
        self,
        local_path: str,
        path: str,
        base_dir: Path | None = None,
    ) -> tuple[str, dict[str, Any]] | None:
        """Resolve a local file path to a component spec."""
        raw_path = local_path[7:] if local_path.startswith("file://") else local_path
        path_obj = Path(raw_path)
        if not path_obj.is_absolute() and base_dir is not None:
            path_obj = (base_dir / path_obj).resolve()
        else:
            path_obj = path_obj.resolve()
        if not path_obj.exists():
            return None
        file_url = local_path if local_path.startswith("file://") else f"file://{local_path}"
        self.log.info(f"   Resolve: loading local file {local_path}")
        return self._fetch_component_by_url(file_url, path, base_dir)

    def _resolve_local_from_python(
        self,
        gen_config: Any,
        path: str,
        base_dir: Path | None = None,
    ) -> tuple[str, dict[str, Any]] | None:
        """Generate a component YAML from a Python source file and resolve it."""
        if not isinstance(gen_config, dict):
            self.log.warn("   ⚠️ 'local_from_python' must be a dict")
            return None
        file_field = gen_config.get("file")
        if not file_field:
            self.log.warn("   ⚠️ 'local_from_python' requires a 'file' field")
            return None

        def _resolve_path(p: str | Path | None) -> Path | None:
            if not p:
                return None
            pp = Path(p)
            if pp.is_absolute():
                return pp
            return (base_dir / pp).resolve() if base_dir is not None else pp.resolve()

        python_file = _resolve_path(file_field)
        if python_file is None or not python_file.exists():
            self.log.warn(f"   ⚠️ local_from_python file not found: {python_file}")
            return None
        python_file = python_file.resolve()
        resolve_root = _resolve_path(gen_config.get("resolve_root"))
        trust_base_dirs = [base_dir, Path.cwd()]
        if not is_trusted_python_source(
            python_file,
            base_dirs=trust_base_dirs,
            trusted_sources=self.trusted_python_sources,
            allow_all=self.allow_all_hydration,
        ):
            raise HydrationError(trusted_python_source_guidance(python_file))

        output_folder = _resolve_path(gen_config.get("output_folder"))
        if output_folder is None:
            if base_dir is None:
                self.log.warn("   ⚠️ local_from_python requires output_folder")
                return None
            output_folder = (base_dir / "generated").resolve()
        output_folder.mkdir(parents=True, exist_ok=True)

        out_path = output_folder / (python_file.stem.replace("_", "-") + ".yaml")
        success = regenerate_yaml(
            python_file=python_file,
            output_path=out_path,
            function_name=gen_config.get("function"),
            custom_name=gen_config.get("name"),
            image=gen_config.get("image"),
            dependencies_from=_resolve_path(gen_config.get("dependencies_from")),
            strip_code=bool(gen_config.get("strip_code", False)),
            verbose=False,
            mode=str(gen_config.get("mode", "inline")),
            resolve_root=resolve_root,
        )
        if not success or not out_path.exists():
            self.log.warn(f"   ⚠️ local_from_python failed to generate {out_path}")
            return None
        return self._resolve_local_file(str(out_path), path, base_dir)

    def _pick_higher_version(
        self,
        primary: tuple[str, dict[str, Any]],
        local: tuple[str, dict[str, Any]],
        path: str,
    ) -> tuple[str, dict[str, Any]]:
        primary_version = utils.get_version_from_data(primary[1])
        local_version = utils.get_version_from_data(local[1])
        if local_version and primary_version:
            if utils.compare_versions(local_version, primary_version) > 0:
                return local
            return primary
        if local_version and not primary_version:
            return local
        return primary

    def _resolve_task(
        self,
        task_name: str,
        task_data: dict[str, Any],
        path: str,
        base_dir: Path | None = None,
        recursive_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Resolve component references to full componentRef with spec."""
        if recursive_params is not None:
            self._global_params = recursive_params
        else:
            self._global_params = {}
        if not isinstance(task_data, dict):
            return task_data

        legacy_mappings = [
            ("componentUrl", "url"),
            ("componentName", "name"),
            ("componentDigest", "digest"),
        ]
        for legacy_key, ref_type in legacy_mappings:
            if legacy_key in task_data:
                ref_value = task_data[legacy_key]
                if ref_value and self.enable_resolution:
                    return self._resolve_component_ref(
                        task_name,
                        task_data,
                        path,
                        ref_type,
                        ref_value,
                        remove_key=legacy_key,
                        base_dir=base_dir,
                    )
                return task_data

        if "componentRef" not in task_data:
            return task_data
        component_ref = task_data["componentRef"]
        if not isinstance(component_ref, dict) or "spec" in component_ref:
            return task_data
        if not self.enable_resolution:
            return task_data

        present_refs = [
            (key, component_ref[key])
            for key in ("digest", "name", "url")
            if key in component_ref and component_ref[key]
        ]
        if not present_refs:
            return task_data
        if len(present_refs) == 1:
            ref_type, ref_value = present_refs[0]
            return self._resolve_component_ref(
                task_name,
                task_data,
                path,
                ref_type,
                ref_value,
                remove_key="componentRef",
                base_dir=base_dir,
            )
        return self._resolve_best_ref(task_name, task_data, path, present_refs, base_dir)

    def _resolve_component_ref(
        self,
        task_name: str,
        task_data: dict[str, Any],
        path: str,
        ref_type: str,
        ref_value: str,
        remove_key: str,
        base_dir: Path | None = None,
    ) -> dict[str, Any]:
        """Resolve a component reference to full componentRef with spec."""
        cache_key = self._cache_key(ref_type, ref_value)
        if cache_key not in self.cache:
            result = self._resolve_registered_component(ref_type, ref_value, path, base_dir)
            if result is None:
                if not self._postprocess_callback:
                    raise HydrationError(f"Component not found: {ref_type}={ref_value} at {path}")
                processed = self._postprocess_callback(task_name, task_data, path)
                component_ref = processed.get("componentRef")
                if not component_ref:
                    raise HydrationError(f"Component not found: {ref_type}={ref_value} at {path}")
            else:
                digest, spec = result
                component_ref = {
                    "name": spec.get("name", ""),
                    "digest": digest,
                    "spec": spec,
                }
                if self._postprocess_callback:
                    new_task = {k: v for k, v in task_data.items() if k != remove_key}
                    new_task["componentRef"] = component_ref
                    processed = self._postprocess_callback(task_name, new_task, path)
                    component_ref = processed.get("componentRef", component_ref)
            self.cache[cache_key] = component_ref

        new_task = {k: v for k, v in task_data.items() if k != remove_key}
        new_task["componentRef"] = copy.deepcopy(self.cache[cache_key])
        return new_task

    def _try_resolve_single_ref(
        self,
        ref_type: str,
        ref_value: str,
        path: str,
        base_dir: Path | None,
    ) -> tuple[str, str, str | None, dict[str, Any]] | None:
        """Resolve one ref and return metadata for best-ref selection."""
        cache_key = self._cache_key(ref_type, ref_value)
        try:
            if cache_key not in self.cache:
                result = self._resolve_registered_component(ref_type, ref_value, path, base_dir)
                if result is None:
                    self.log.warn(f"   ⚠️ Could not resolve {ref_type}={ref_value}")
                    return None
                digest, spec = result
                self.cache[cache_key] = {
                    "name": spec.get("name", ""),
                    "digest": digest,
                    "spec": spec,
                }
            component_ref = self.cache[cache_key]
            version = utils.get_version_from_data(component_ref.get("spec", {}))
            return (ref_type, ref_value, version, component_ref)
        except Exception as exc:
            self.log.warn(f"   ⚠️ Failed to resolve {ref_type}={ref_value}: {exc}")
            return None

    @staticmethod
    def _pick_best_candidate(
        candidates: list[tuple[str, str, str | None, dict[str, Any]]],
    ) -> tuple[str, str, str | None, dict[str, Any]]:
        """Pick candidate with highest version; tie-break digest > name > url."""
        priority = {"digest": 0, "name": 1, "url": 2}

        def _is_better(candidate, current):
            c_type, _, c_ver, _ = candidate
            b_type, _, b_ver, _ = current
            if c_ver and b_ver:
                cmp = utils.compare_versions(c_ver, b_ver)
                if cmp != 0:
                    return cmp > 0
            elif c_ver and not b_ver:
                return True
            elif not c_ver and b_ver:
                return False
            return priority.get(c_type, 99) < priority.get(b_type, 99)

        best = candidates[0]
        for candidate in candidates[1:]:
            if _is_better(candidate, best):
                best = candidate
        return best

    def _resolve_best_ref(
        self,
        task_name: str,
        task_data: dict[str, Any],
        path: str,
        refs: list[tuple[str, str]],
        base_dir: Path | None = None,
    ) -> dict[str, Any]:
        """Resolve multiple refs and pick the highest version."""
        candidates = []
        for ref_type, ref_value in refs:
            result = self._try_resolve_single_ref(ref_type, ref_value, path, base_dir)
            if result:
                candidates.append(result)
        if not candidates:
            ref_type, ref_value = refs[0]
            return self._resolve_component_ref(
                task_name,
                task_data,
                path,
                ref_type,
                ref_value,
                remove_key="componentRef",
                base_dir=base_dir,
            )
        _chosen_type, _chosen_value, _chosen_version, chosen_ref = self._pick_best_candidate(
            candidates
        )
        new_task = {k: v for k, v in task_data.items() if k != "componentRef"}
        new_task["componentRef"] = copy.deepcopy(chosen_ref)
        return new_task

    def resolve_components(
        self,
        data: dict[str, Any],
        base_dir: Path | None = None,
    ) -> dict[str, Any]:
        """Traverse pipeline YAML and resolve componentRef references."""

        def process_task(
            task_name: str,
            task_data: dict[str, Any],
            path: str,
            task_base_dir: Path | None = None,
            recursive_params: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            return self._resolve_task(
                task_name, task_data, path, task_base_dir, recursive_params
            )

        pipeline_name = data.get("name", "pipeline")
        initial_params = self._global_params.copy() if self._global_params else None
        return utils.traverse_pipeline_tasks(
            data, pipeline_name, process_task, base_dir, initial_params
        )

    @property
    def resolved_count(self) -> int:
        """Return the number of resolved components."""
        return len(self.cache)

    def hydrate_file(
        self,
        input_file: Path | str,
        output_file: Path | str | None = None,
        overrides: dict[str, str] | None = None,
    ) -> HydratedPipeline:
        """Hydrate a pipeline YAML file."""
        self._global_params = {}
        try:
            input_str = str(input_file)
            input_scheme = self._uri_scheme(input_str)
            input_path: Path | None = None
            base_dir: Path | None = None
            try:
                if input_scheme and input_scheme != "file":
                    yaml_text = self._read_uri_text(
                        input_str,
                        "pipeline",
                        self.make_resolver_context(input_scheme, input_str, "pipeline", None),
                    )
                    config = yaml.safe_load(yaml_text) if yaml_text is not None else None
                else:
                    raw_input = input_str[7:] if input_str.startswith("file://") else input_str
                    input_path = Path(raw_input)
                    base_dir = input_path.parent.resolve()
                    config = yaml.safe_load(input_path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise HydrationError(f"Failed to read pipeline YAML {input_file}: {exc}") from exc
            if config is None:
                config = {}
            if not isinstance(config, dict):
                raise HydrationError("Pipeline YAML must contain a top-level mapping")

            if "template_file" in config:
                if input_path is None:
                    raise UnsupportedHydrationFeatureError(
                        "template_file configs require a local pipeline input"
                    )
                result = self._render_template_config(input_path, config, overrides=overrides)
                if result is None:
                    raise HydrationError(
                        f"Template file not found: {(base_dir / config['template_file']).resolve()}"
                    )
                _, output_yaml = result
                if self.recursive_context:
                    self._global_params = {k: v for k, v in config.items() if k != "template_file"}
                    if overrides:
                        self._global_params.update(overrides)
                self.log.info(f"✅ Hydrated {input_file}")
            else:
                output_yaml = config
                self.log.info(f"✅ Copied {input_file}")

            output_yaml = self.resolve_components(output_yaml, base_dir=base_dir)
            output_content = utils.dump_yaml(output_yaml)
            if output_file is not None:
                output_str = str(output_file)
                output_scheme = self._uri_scheme(output_str)
                if output_scheme and output_scheme != "file":
                    self._write_uri_text(
                        output_str,
                        output_content,
                        self.make_resolver_context(output_scheme, output_str, "output", base_dir),
                    )
                else:
                    raw_output = output_str[7:] if output_str.startswith("file://") else output_str
                    output_path = Path(raw_output)
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    output_path.write_text(output_content, encoding="utf-8")
            return HydratedPipeline(output_yaml, output_content, self.resolved_count)
        finally:
            self._global_params = {}


# =============================================================================
# Resolve config helpers (ported from TD)
# =============================================================================


def _annotations_match(annotations: Mapping[str, Any], required: dict[str, Any]) -> bool:
    """Check if a component's annotations satisfy all required constraints."""
    for key, expected in required.items():
        actual = annotations.get(key)
        if isinstance(expected, list):
            if actual not in [str(v) for v in expected]:
                return False
        else:
            if actual != str(expected):
                return False
    return True


def _parse_version_constraint(constraint: str) -> list[tuple[str, str]]:
    """Parse a version constraint string into ``(operator, version)`` pairs."""
    parts = [p.strip() for p in constraint.split(",") if p.strip()]
    result: list[tuple[str, str]] = []
    for part in parts:
        match = re.match(r"^(>=|<=|!=|>|<|==)?\s*(\d[\d.]*)", part)
        if match:
            op = match.group(1) or "=="
            version = match.group(2)
            result.append((op, version))
    return result


def _version_satisfies(version: str, constraint: str) -> bool:
    """Check if a version string satisfies a constraint."""
    parsed = _parse_version_constraint(constraint)
    if not parsed:
        raise HydrationError(f"Invalid version constraint: '{constraint}'")

    for op, target in parsed:
        cmp = utils.compare_versions(version, target)
        satisfied = (
            (op == ">=" and cmp >= 0)
            or (op == ">" and cmp > 0)
            or (op == "<=" and cmp <= 0)
            or (op == "<" and cmp < 0)
            or (op == "==" and cmp == 0)
            or (op == "!=" and cmp != 0)
        )
        if not satisfied:
            return False
    return True


def _filter_by_version_constraint(
    candidates: list[ComponentInfo],
    constraint: str,
) -> list[ComponentInfo]:
    """Filter candidates whose version satisfies a constraint string."""
    return [
        c for c in candidates
        if c.version and _version_satisfies(c.version, constraint)
    ]


class DehydrateChoice:
    """Constants preserved from TD for future dehydrate porting."""

    DIGEST = "d"
    NAME = "n"
    URL = "u"
    FILE = "f"
    KEEP = "k"
    AUTO = "a"


# ---- Resolver registry -----------------------------------------------------


def _resolve_digest(
    hydrator: PipelineHydrator,
    value: Any,
    path: str,
    base_dir: Path | None,
) -> tuple[str, dict[str, Any]] | None:
    return hydrator._fetch_component_by_digest(str(value), path, base_dir)


def _resolve_name(
    hydrator: PipelineHydrator,
    value: Any,
    path: str,
    base_dir: Path | None,
) -> tuple[str, dict[str, Any]] | None:
    return hydrator._fetch_component_by_name(str(value), path, base_dir)


def _resolve_url(
    hydrator: PipelineHydrator,
    value: Any,
    path: str,
    base_dir: Path | None,
) -> tuple[str, dict[str, Any]] | None:
    return hydrator._fetch_component_by_url(str(value), path, base_dir)


def _resolve_file(
    hydrator: PipelineHydrator,
    value: Any,
    path: str,
    base_dir: Path | None,
) -> tuple[str, dict[str, Any]] | None:
    return hydrator._fetch_component_from_file_url(str(value), path, base_dir)


def _resolve_resolve(
    hydrator: PipelineHydrator,
    value: Any,
    path: str,
    base_dir: Path | None,
) -> tuple[str, dict[str, Any]] | None:
    return hydrator._fetch_component_by_resolve_url(str(value), path, base_dir)


def _resolve_http(
    hydrator: PipelineHydrator,
    value: Any,
    path: str,
    base_dir: Path | None,
) -> tuple[str, dict[str, Any]] | None:
    return hydrator.fetch_remote_component(str(value), path, base_dir)


def _resolve_local(
    hydrator: PipelineHydrator,
    value: Any,
    path: str,
    base_dir: Path | None,
) -> tuple[str, dict[str, Any]] | None:
    return hydrator._resolve_local_file(str(value), path, base_dir)


def _resolve_local_from_python(
    hydrator: PipelineHydrator,
    value: Any,
    path: str,
    base_dir: Path | None,
) -> tuple[str, dict[str, Any]] | None:
    return hydrator._resolve_local_from_python(value, path, base_dir)


register_component_resolver("digest", _resolve_digest)
register_component_resolver("name", _resolve_name)
register_component_resolver("url", _resolve_url)
register_component_resolver("file", _resolve_file)
register_component_resolver("resolve", _resolve_resolve)
register_component_resolver("http", _resolve_http)
register_component_resolver("https", _resolve_http)
register_component_resolver("local", _resolve_local)
register_component_resolver("local_from_python", _resolve_local_from_python)
