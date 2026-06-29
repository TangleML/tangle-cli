"""Publish components to the Tangle API.

This module intentionally mirrors the generic publisher behavior from
``tangle-deploy`` while depending only on OSS ``tangle_cli`` primitives and the
checked-in/generated static API client. Provider-specific auth wrappers,
notification plumbing, and a separate ``publish-all`` CLI are kept downstream.
"""

from __future__ import annotations

import inspect
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import tangle_cli.utils as utils

from .handler import TangleCliHandler
from .logger import Logger

if TYPE_CHECKING:
    from tangle_api.generated.models import ComponentSpec


class ProcessingOutcome(str, Enum):
    """Outcome of processing one component publish operation."""

    SKIP = "skip"
    PROCEED = "proceed"
    SUCCESS = "success"
    ERROR = "error"


@dataclass
class ProcessingResult:
    """Result for one component publish/deprecate processing step."""

    outcome: ProcessingOutcome
    local_version: str | None = None
    latest_version: str | None = None
    spec: Any = None
    reason: str | None = None
    digest: str | None = None
    response: Any = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": self.outcome.value,
            "outcome": self.outcome.value,
            "local_version": self.local_version,
            "latest_version": self.latest_version,
            "reason": self.reason,
            "digest": self.digest,
            "response": _to_plain(self.response),
        }
        if self.spec is not None:
            payload["name"] = getattr(self.spec, "name", None)
        return {key: value for key, value in payload.items() if value is not None}


@dataclass(frozen=True)
class ComponentPublishContext:
    """Structured context passed to component publish hooks.

    The context is additive: hooks may keep the original historical signatures,
    or add a keyword-only ``context`` parameter to receive publisher metadata,
    batch configuration, and accumulated per-component results.
    """

    publisher: "ComponentPublisher"
    dry_run: bool
    git_remote_sha: str | None = None
    git_remote_branch: str | None = None
    git_remote_url: str | None = None
    git_repo: str | None = None
    git_root: str | None = None
    published_by: str | None = None
    batch_config: Sequence[Mapping[str, Any]] | None = None
    component_config: Mapping[str, Any] | None = None
    component_path: str | None = None
    result: ProcessingResult | None = None
    results: Sequence[tuple[str, ProcessingResult]] = field(default_factory=tuple)


class ComponentPublishHook(Protocol):
    """Extension hook for downstream publishers.

    Downstream packages can implement one or more methods to observe publish
    batches (for example, to send notification summaries) without OSS importing
    or knowing about those systems. Implementations that need richer metadata may
    add ``context: ComponentPublishContext | None = None`` as a keyword
    parameter; hooks without that parameter continue to work.
    """

    def before_batch(
        self,
        components_config: Sequence[Mapping[str, Any]],
        context: ComponentPublishContext | None = None,
    ) -> None: ...

    def after_component(
        self,
        component_path: str,
        result: ProcessingResult,
        context: ComponentPublishContext | None = None,
    ) -> None: ...

    def after_batch(
        self,
        results: Sequence[tuple[str, ProcessingResult]],
        context: ComponentPublishContext | None = None,
    ) -> None: ...


# ============================================================================
# Publisher
# ============================================================================


class ComponentPublisher(TangleCliHandler):
    """Publisher for Tangle components."""

    component_spec_model: type[Any] | None = None

    def __init__(
        self,
        dry_run: bool = False,
        git_remote_sha: str | None = None,
        git_remote_branch: str | None = None,
        git_remote_url: str | None = None,
        git_repo: str | None = None,
        git_root: str | Path | None = None,
        published_by: str | None = None,
        client: Any = None,
        client_factory: Callable[[], Any] | None = None,
        hooks: Sequence[ComponentPublishHook] | None = None,
        logger: Logger | None = None,
        base_url: str | None = None,
    ) -> None:
        """Initialize the ComponentPublisher.

        Args mirror the generic ``tangle-deploy`` publisher shape, with
        provider-specific notification/auth fields intentionally omitted.
        ``client_factory`` is a downstream seam for lazily constructing a custom
        authenticated client; subclasses may also override :meth:`_get_client`
        for more control.
        """

        super().__init__(
            dry_run=dry_run,
            client=client,
            client_factory=client_factory,
            logger=logger,
            base_url=base_url,
        )
        self.published_by = published_by
        self.hooks = list(hooks or [])
        self.results: list[tuple[str, ProcessingResult]] = []

        git_info = utils.get_git_info(Path.cwd(), logger=self.log)
        self._git_root = str(git_root or git_info.get("_git_root") or "") or None
        self.git_remote_sha = git_remote_sha or git_info.get("git_remote_sha")
        self.git_remote_branch = git_remote_branch or git_info.get("git_remote_branch")
        self.git_remote_url = git_remote_url or git_info.get("git_remote_url")
        self.git_repo = git_repo

    def _component_spec_model(self) -> type[Any]:
        if self.component_spec_model is not None:
            return self.component_spec_model
        try:
            from tangle_api.generated.models import ComponentSpec
        except ModuleNotFoundError as exc:
            if exc.name == "tangle_api":
                raise RuntimeError(
                    "Native generated Tangle API bindings are required for component publishing. "
                    "Install tangle-cli[native] or provide a local tangle_api.generated package."
                ) from exc
            raise
        return ComponentSpec

    def component_digest(self, component: Any) -> str | None:
        """Return a published component digest from mapping or object shapes."""

        if isinstance(component, Mapping):
            digest = component.get("digest")
            return str(digest) if digest else None
        digest = getattr(component, "digest", None)
        return str(digest) if digest else None

    def current_user_id(self, client: Any) -> str | None:
        """Return the current Tangle user id for owner-scoped lookups."""

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

    def perform_version_check(self, spec: Any) -> ProcessingResult:
        """Perform owner-scoped version checking for a component.

        If ``published_by`` is omitted, the current authenticated user is
        resolved via ``client.users_me().id``. Failure to determine an owner is
        an error so callers do not accidentally compare/deprecate components
        owned by others.
        """

        local_version = spec.version
        self.log.info(f"   Local version: {local_version}")

        latest_version = None

        if self.dry_run:
            test_version = os.environ.get("TEST_LATEST_VERSION")
            if test_version:
                latest_version = test_version
                self.log.info(f"   Remote version (test): {latest_version}")
        else:
            client = self._get_client()
            if client is None:
                return ProcessingResult(
                    outcome=ProcessingOutcome.ERROR,
                    local_version=str(local_version),
                    latest_version=None,
                    reason="Failed to create API client",
                )

            filter_by = self.published_by or self.current_user_id(client)
            if not filter_by:
                self.log.error(
                    "❌ Cannot determine current user — aborting to avoid deprecating components owned by others"
                )
                return ProcessingResult(
                    outcome=ProcessingOutcome.ERROR,
                    local_version=str(local_version),
                    latest_version=None,
                    reason="Cannot determine current user for author filtering",
                )

            existing_components = client.find_existing_components(
                spec.search_names,
                verbose=False,
                published_by=filter_by,
            )

            if existing_components:
                for component in existing_components:
                    digest = self.component_digest(component)
                    if not digest:
                        continue
                    try:
                        full_spec = client.get_component_spec(digest)
                        remote_version = full_spec.version if full_spec else None
                        if remote_version and (
                            not latest_version or utils.compare_versions(remote_version, latest_version) > 0
                        ):
                            latest_version = remote_version
                    except Exception as exc:
                        self.log.warn(f"   Warning: Failed to get version for component {digest[:16]}: {exc}")
                        continue

                if latest_version:
                    self.log.info(f"   Remote version: {latest_version}")
                else:
                    self.log.info(
                        f"   ℹ️  Found {len(existing_components)} component(s) but couldn't extract version"
                    )

        should_proceed = not latest_version or utils.compare_versions(local_version, latest_version) != 0

        if should_proceed:
            is_older = latest_version is not None and utils.compare_versions(latest_version, local_version) > 0
            version_suffix = " (older)" if is_older else ""
            self.log.info(
                "   ➡️  Version "
                + (f"{latest_version}{version_suffix}" if latest_version else "new")
                + f" → {local_version}"
            )
            return ProcessingResult(
                outcome=ProcessingOutcome.PROCEED,
                local_version=local_version,
                latest_version=latest_version,
                spec=spec,
            )

        self.log.info(f"   ⏭️  Skipping: Version {local_version} unchanged")

        return ProcessingResult(
            outcome=ProcessingOutcome.SKIP,
            local_version=local_version,
            latest_version=latest_version,
            spec=spec,
            reason=f"Version {local_version} unchanged (matches remote)",
        )

    def deprecate_old_components(
        self,
        existing_components: Sequence[Any],
        new_digest: str,
    ) -> int:
        """Deprecate previous component versions after a successful publish.

        ``existing_components`` must already be owner-scoped by the caller.
        This method refuses to operate without a client and skips the newly
        published digest to avoid self-deprecation.
        """

        if not existing_components:
            return 0

        client = self._get_client()
        if not client:
            self.log.warn("   ⚠️ Cannot deprecate components without TangleApiClient")
            return 0

        self.log.info(f"   Deprecating {len(existing_components)} previous version(s)...")
        deprecation_count = 0

        for old_component in existing_components:
            old_digest = self.component_digest(old_component)
            if old_digest and old_digest != new_digest:
                try:
                    result = client.published_components_update(
                        digest=old_digest,
                        deprecated=True,
                        superseded_by=new_digest,
                    )
                    if result:
                        deprecation_count += 1
                        self.log.info(f"   ✅ Successfully deprecated component {old_digest[:16]}...")
                    else:
                        self.log.warn(
                            f"   ⚠️  No response from deprecation request for component {old_digest[:16]}..."
                        )
                except Exception as exc:
                    self.log.warn(f"   ⚠️  Warning: Failed to deprecate component {old_digest[:16]}...: {exc}")

        if deprecation_count > 0:
            self.log.info(f"   ✅ Deprecated {deprecation_count} old version(s)")

        return deprecation_count

    def load_component_spec(
        self,
        component_path: str | Path,
        *,
        annotations: Mapping[str, str] | None = None,
    ) -> "ComponentSpec":
        """Load a component YAML file into the generated ``ComponentSpec`` model."""

        text = read_component_yaml_text(component_path)
        return self._component_spec_model().from_yaml(text, annotations=dict(annotations or {}))

    def prepare_component_for_publish(
        self,
        component_path: str | Path,
        *,
        image: str | None = None,
        name: str | None = None,
        description: str | None = None,
        annotations: Mapping[str, str] | None = None,
    ) -> "ComponentSpec":
        """Load and apply generic publish-time overrides/metadata."""

        spec = self.load_component_spec(component_path, annotations=annotations)
        if name:
            spec.name = name
            spec.data["name"] = name
        if description:
            spec.description = description
            spec.data["description"] = description
        component_yaml_path = None
        if self._git_root:
            try:
                component_yaml_path = str(Path(component_path).resolve().relative_to(Path(self._git_root).resolve()))
            except ValueError:
                pass
        spec.update_fields(
            git_remote_sha=self.git_remote_sha,
            git_remote_branch=self.git_remote_branch,
            git_remote_url=self.git_remote_url,
            image=image,
            component_yaml_path=component_yaml_path,
        )
        return spec

    def deprecate_component(
        self,
        digest: str,
        superseded_by: str | None = None,
    ) -> dict[str, Any]:
        """Deprecate a published component by digest."""

        client = self._get_client()
        if not client:
            return {
                "success": False,
                "digest": digest,
                "error": "Failed to create TangleApiClient",
            }

        try:
            result = client.published_components_update(
                digest=digest,
                deprecated=True,
                superseded_by=superseded_by,
            )
            self.log.info(f"✅ Deprecated component {digest[:16]}...")
            if superseded_by:
                self.log.info(f"   Superseded by: {superseded_by[:16]}...")

            return {
                "success": True,
                "digest": digest,
                "superseded_by": superseded_by,
                "response": _to_plain(result),
            }
        except Exception as exc:
            self.log.error(f"❌ Failed to deprecate component {digest[:16]}...: {exc}")
            return {
                "success": False,
                "digest": digest,
                "error": str(exc),
            }

    def publish_component(
        self,
        file_path: str | Path,
        image: str | None = None,
        name: str | None = None,
        description: str | None = None,
        annotations: dict[str, str] | None = None,
    ) -> ProcessingResult:
        """Publish a component to the Tangle Component Library with version checking."""

        try:
            path = Path(file_path)
            local_yaml_content = read_component_yaml_text(path)
        except Exception as exc:
            self.log.error(f"❌ Failed to read file {file_path}: {exc}")
            return ProcessingResult(
                outcome=ProcessingOutcome.ERROR,
                local_version=None,
                latest_version=None,
                reason=f"Failed to read file {file_path}: {exc}",
            )

        try:
            spec = self._component_spec_model().from_yaml(local_yaml_content, annotations=dict(annotations or {}))
            if spec.version is None:
                self.log.warn("   ⏭️  Skipping: Component version is required but not found in YAML")
                return ProcessingResult(
                    outcome=ProcessingOutcome.SKIP,
                    local_version=None,
                    latest_version=None,
                    spec=spec,
                    reason="Component version is required but not found in YAML",
                )
        except ValueError as exc:
            self.log.error(f"   ❌ {exc}")
            return ProcessingResult(
                outcome=ProcessingOutcome.ERROR,
                local_version=None,
                latest_version=None,
                reason=str(exc),
            )

        if name:
            spec.name = name
            spec.data["name"] = name
        if description:
            spec.description = description
            spec.data["description"] = description

        client = self._get_client()
        if not client and not self.dry_run:
            self.log.error("❌ Failed to create TangleApiClient")
            return ProcessingResult(
                outcome=ProcessingOutcome.ERROR,
                local_version=None,
                latest_version=None,
                spec=spec,
                reason="Failed to create TangleApiClient",
            )

        version_check_result = self.perform_version_check(spec=spec)

        if version_check_result.outcome == ProcessingOutcome.SKIP:
            self.log.info(f"   ⏭️  Skipping API publish: {version_check_result.reason}")
            return version_check_result
        if version_check_result.outcome == ProcessingOutcome.ERROR:
            self.log.error(f"   ❌ Cannot proceed due to error: {version_check_result.reason}")
            return version_check_result

        component_yaml_path = None
        if self._git_root:
            try:
                component_yaml_path = str(Path(file_path).resolve().relative_to(Path(self._git_root).resolve()))
            except ValueError:
                pass

        spec.update_fields(
            self.git_remote_sha,
            self.git_remote_branch,
            git_remote_url=self.git_remote_url,
            image=image,
            component_yaml_path=component_yaml_path,
        )

        spec_annotations = (getattr(spec, "data", None) or {}).get("metadata", {}).get("annotations")
        if self._git_root and spec_annotations:
            utils.normalize_annotation_paths(Path(file_path), self._git_root, spec_annotations)

        local_yaml_content = spec.to_yaml()

        if self.dry_run:
            self.log.info(f"[DRY-RUN] Would publish component: {spec.name}")
            return ProcessingResult(
                outcome=ProcessingOutcome.SUCCESS,
                local_version=version_check_result.local_version,
                latest_version=version_check_result.latest_version,
                spec=spec,
                reason=f"Dry-run: would publish {spec.name}",
                response={"name": spec.name, "text": local_yaml_content},
            )

        filter_by = self.published_by or self.current_user_id(client)
        if not filter_by:
            self.log.error(
                "❌ Cannot determine current user — aborting to avoid deprecating components owned by others"
            )
            return ProcessingResult(
                outcome=ProcessingOutcome.ERROR,
                local_version=version_check_result.local_version,
                latest_version=version_check_result.latest_version,
                spec=spec,
                reason="Cannot determine current user for author filtering",
            )
        existing_components = client.find_existing_components(spec.search_names, verbose=True, published_by=filter_by)

        try:
            result = client.published_components_create(name=spec.name, text=local_yaml_content)
            plain_result = _to_plain(result)
            new_digest = plain_result.get("digest") if isinstance(plain_result, Mapping) else None

            if new_digest:
                self.log.info(f"✅ Published: {spec.name} (digest: {str(new_digest)[:16]}...)")
                self.deprecate_old_components(existing_components, str(new_digest))
                return ProcessingResult(
                    outcome=ProcessingOutcome.SUCCESS,
                    local_version=version_check_result.local_version,
                    latest_version=version_check_result.latest_version,
                    spec=spec,
                    reason=f"Successfully published with digest: {new_digest}",
                    digest=str(new_digest),
                    response=result,
                )

            self.log.warn("⚠️ Component published but no digest returned")
            return ProcessingResult(
                outcome=ProcessingOutcome.ERROR,
                local_version=version_check_result.local_version,
                latest_version=version_check_result.latest_version,
                spec=spec,
                reason="Component published but no digest returned",
                response=result,
            )
        except Exception as exc:
            self.log.error(f"❌ Request failed: {exc}")
            return ProcessingResult(
                outcome=ProcessingOutcome.ERROR,
                local_version=version_check_result.local_version,
                latest_version=version_check_result.latest_version,
                spec=spec,
                reason=f"Request failed: {exc}",
            )

    def publish_components(self, components_config: list[dict[str, Any]]) -> int:
        """Publish components with per-component configuration to the Tangle API."""

        self.log.info("\n" + "=" * 60)
        self.log.info(f"📤 Publishing {len(components_config)} component(s) to Tangle API")
        self.log.info("=" * 60)

        batch_context = self._publish_context(batch_config=components_config)
        self._run_hook("before_batch", components_config, context=batch_context)
        all_results: list[tuple[str, ProcessingResult]] = []

        for config in components_config:
            component_path = config.get("component_path")
            image = config.get("image")
            custom_name = config.get("name")
            custom_description = config.get("description")
            custom_annotations = config.get("annotations")

            if not component_path:
                self.log.error(f"\n❌ Error: Missing 'component_path' in configuration: {config}")
                error_result = ProcessingResult(
                    outcome=ProcessingOutcome.ERROR,
                    local_version=None,
                    latest_version=None,
                    reason="Missing 'component_path' in configuration",
                )
                all_results.append(("<missing_path>", error_result))
                self._run_hook(
                    "after_component",
                    "<missing_path>",
                    error_result,
                    context=self._publish_context(
                        batch_config=components_config,
                        component_config=config,
                        component_path="<missing_path>",
                        result=error_result,
                        results=all_results,
                    ),
                )
                continue

            component_name = custom_name or Path(component_path).stem
            self.log.info(f"\n📦 Publishing component: {component_name}")
            self.log.info(f"   Source: {component_path}")
            if image:
                self.log.info(f"   Image: {image}")
            if custom_name:
                self.log.info(f"   Custom name: {custom_name}")
            if custom_description:
                desc_preview = custom_description[:50] + ("..." if len(custom_description) > 50 else "")
                self.log.info(f"   Custom description: {desc_preview}")
            if custom_annotations:
                self.log.info(f"   Custom annotations: {list(custom_annotations.keys())}")

            try:
                result = self.publish_component(
                    component_path,
                    image=image,
                    name=custom_name,
                    description=custom_description,
                    annotations=custom_annotations,
                )
            except Exception as exc:
                result = ProcessingResult(
                    outcome=ProcessingOutcome.ERROR,
                    local_version=None,
                    latest_version=None,
                    reason=f"Unexpected error: {exc}",
                )
                self.log.error(f"   ❌ Unexpected error: {exc}")
            all_results.append((str(component_path), result))
            self._run_hook(
                "after_component",
                str(component_path),
                result,
                context=self._publish_context(
                    batch_config=components_config,
                    component_config=config,
                    component_path=str(component_path),
                    result=result,
                    results=all_results,
                ),
            )

        success_count = sum(1 for _, result in all_results if result.outcome == ProcessingOutcome.SUCCESS)
        skip_count = sum(1 for _, result in all_results if result.outcome == ProcessingOutcome.SKIP)
        error_count = sum(1 for _, result in all_results if result.outcome == ProcessingOutcome.ERROR)

        self.log.info("\n" + "=" * 60)
        self.log.info("📊 Tangle API Publish Summary")
        self.log.info("=" * 60)
        self.log.info(f"Total components found: {len(all_results)}")
        self.log.info(f"Successfully published: {success_count}")
        self.log.info(f"Skipped (version check): {skip_count}")
        self.log.info(f"Failed: {error_count}")

        error_results = [(path, result) for path, result in all_results if result.outcome == ProcessingOutcome.ERROR]
        if error_results:
            self.log.error("\n❌ Error details:")
            for path, result in error_results:
                component_name = result.spec.name if result.spec else Path(path).stem
                self.log.error(f"   • {component_name}: {result.reason}")

        self.results = all_results
        self._run_hook(
            "after_batch",
            all_results,
            context=self._publish_context(batch_config=components_config, results=all_results),
        )

        if len(all_results) == 0:
            self.log.warn("\n⚠️  No components specified in configuration")
            return 1
        if error_count > 0:
            if error_count == len(all_results):
                self.log.error("\n❌ All components failed to publish")
            else:
                self.log.error(f"\n❌ {error_count} component(s) failed to publish")
            return 1
        return 0

    def _publish_context(
        self,
        *,
        batch_config: Sequence[Mapping[str, Any]] | None = None,
        component_config: Mapping[str, Any] | None = None,
        component_path: str | None = None,
        result: ProcessingResult | None = None,
        results: Sequence[tuple[str, ProcessingResult]] | None = None,
    ) -> ComponentPublishContext:
        return ComponentPublishContext(
            publisher=self,
            dry_run=self.dry_run,
            git_remote_sha=self.git_remote_sha,
            git_remote_branch=self.git_remote_branch,
            git_remote_url=self.git_remote_url,
            git_repo=self.git_repo,
            git_root=self._git_root,
            published_by=self.published_by,
            batch_config=batch_config,
            component_config=component_config,
            component_path=component_path,
            result=result,
            results=tuple(results or ()),
        )

    def _run_hook(self, method_name: str, *args: Any, context: ComponentPublishContext | None = None) -> None:
        for hook in self.hooks:
            method = getattr(hook, method_name, None)
            if not method:
                continue
            if context is not None and _hook_accepts_context(method):
                method(*args, context=context)
            else:
                method(*args)


# ============================================================================
# Internal helpers
# ============================================================================


def read_component_yaml_text(component_path: str | Path) -> str:
    """Read component YAML text from disk."""

    return Path(component_path).read_text(encoding="utf-8")


def deprecate_old_components(
    existing_components: Sequence[Any],
    new_digest: str,
    client: Any = None,
    logger: Logger | None = None,
) -> int:
    """Deprecate old versions of a component after publishing a new one."""

    return ComponentPublisher(client=client, logger=logger).deprecate_old_components(
        existing_components,
        new_digest,
    )


def perform_version_check(
    spec: Any,
    dry_run: bool,
    client: Any = None,
    logger: Logger | None = None,
    published_by: str | None = None,
) -> ProcessingResult:
    """Perform owner-scoped version checking for a component."""

    return ComponentPublisher(
        dry_run=dry_run,
        client=client,
        logger=logger,
        published_by=published_by,
    ).perform_version_check(spec)


def publish_component_to_tangle(
    file_path: str | Path,
    dry_run: bool = False,
    git_remote_sha: str | None = None,
    git_remote_branch: str | None = None,
    git_remote_url: str | None = None,
    git_repo: str | None = None,
    image: str | None = None,
    name: str | None = None,
    description: str | None = None,
    annotations: dict[str, str] | None = None,
    client: Any = None,
    client_factory: Callable[[], Any] | None = None,
    published_by: str | None = None,
) -> ProcessingResult:
    """Publish one component using ``ComponentPublisher.publish_component``."""

    publisher = ComponentPublisher(
        dry_run=dry_run,
        client=client,
        client_factory=client_factory,
        git_remote_sha=git_remote_sha,
        git_remote_branch=git_remote_branch,
        git_remote_url=git_remote_url,
        git_repo=git_repo,
        published_by=published_by,
    )
    return publisher.publish_component(
        file_path,
        image=image,
        name=name,
        description=description,
        annotations=annotations,
    )


def publish_component(client: Any, component_path: str | Path, **kwargs: Any) -> ProcessingResult:
    """Compatibility wrapper around ``ComponentPublisher`` for one component."""

    publisher = ComponentPublisher(
        dry_run=bool(kwargs.pop("dry_run", False)),
        git_remote_sha=kwargs.pop("git_remote_sha", None),
        git_remote_branch=kwargs.pop("git_remote_branch", None),
        git_remote_url=kwargs.pop("git_remote_url", None),
        git_root=kwargs.pop("git_root", None),
        git_repo=kwargs.pop("git_repo", None),
        published_by=kwargs.pop("published_by", None),
        client=client,
        client_factory=kwargs.pop("client_factory", None),
        logger=kwargs.pop("logger", None),
    )
    return publisher.publish_component(component_path, **kwargs)


def deprecate_component(
    client: Any,
    digest: str,
    *,
    superseded_by: str | None = None,
    logger: Logger | None = None,
) -> dict[str, Any]:
    """Compatibility wrapper around ``ComponentPublisher.deprecate_component``."""

    return ComponentPublisher(client=client, logger=logger).deprecate_component(
        digest,
        superseded_by=superseded_by,
    )


def prepare_component_for_publish(
    component_path: str | Path,
    *,
    image: str | None = None,
    name: str | None = None,
    description: str | None = None,
    annotations: Mapping[str, str] | None = None,
    git_remote_sha: str | None = None,
    git_remote_branch: str | None = None,
    git_remote_url: str | None = None,
    git_root: str | Path | None = None,
) -> "ComponentSpec":
    """Load and apply generic publish-time overrides/metadata."""

    return ComponentPublisher(
        git_remote_sha=git_remote_sha,
        git_remote_branch=git_remote_branch,
        git_remote_url=git_remote_url,
        git_root=git_root,
    ).prepare_component_for_publish(
        component_path,
        image=image,
        name=name,
        description=description,
        annotations=annotations,
    )


def _hook_accepts_context(method: Any) -> bool:
    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        return False
    for parameter in signature.parameters.values():
        if parameter.kind == inspect.Parameter.VAR_KEYWORD:
            return True
        if parameter.name == "context":
            return True
    return False


def _to_plain(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "model_dump"):
        return value.model_dump(by_alias=True, exclude_none=True)
    if isinstance(value, Mapping):
        return {key: _to_plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_plain(item) for item in value]
    return value


__all__ = [
    "ComponentPublishContext",
    "ComponentPublishHook",
    "ComponentPublisher",
    "ProcessingOutcome",
    "ProcessingResult",
    "deprecate_component",
    "deprecate_old_components",
    "perform_version_check",
    "prepare_component_for_publish",
    "publish_component",
    "publish_component_to_tangle",
    "read_component_yaml_text",
]
