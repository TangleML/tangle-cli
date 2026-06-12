"""Publish and deprecate local Tangle component definitions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .logger import Logger, get_default_logger

if TYPE_CHECKING:
    from tangle_api.generated.models import ComponentSpec


@dataclass
class ComponentPublishResult:
    """Structured result for publishing one component."""

    status: str
    component_path: str
    name: str | None = None
    digest: str | None = None
    dry_run: bool = False
    response: Any = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "component_path": self.component_path,
            "name": self.name,
            "digest": self.digest,
            "dry_run": self.dry_run,
            "response": _to_plain(self.response),
            "error": self.error,
        }


@dataclass
class ComponentDeprecationResult:
    """Structured result for deprecating one published component."""

    status: str
    digest: str
    superseded_by: str | None = None
    response: Any = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "digest": self.digest,
            "superseded_by": self.superseded_by,
            "response": _to_plain(self.response),
            "error": self.error,
        }


def load_component_spec(
    component_path: str | Path,
    *,
    annotations: Mapping[str, str] | None = None,
) -> "ComponentSpec":
    """Load a component YAML file into the generated ``ComponentSpec`` model."""

    from tangle_api.generated.models import ComponentSpec

    path = Path(component_path)
    text = path.read_text(encoding="utf-8")
    return ComponentSpec.from_yaml(text, annotations=dict(annotations or {}))


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

    path = Path(component_path)
    spec = load_component_spec(path, annotations=annotations)
    if name:
        spec.name = name
        spec.data["name"] = name
    if description:
        spec.description = description
        spec.data["description"] = description

    component_yaml_path = _component_yaml_path(path, git_root)
    spec.update_fields(
        git_remote_sha=git_remote_sha,
        git_remote_branch=git_remote_branch,
        git_remote_url=git_remote_url,
        image=image,
        component_yaml_path=component_yaml_path,
    )
    return spec


def publish_component(
    client: Any,
    component_path: str | Path,
    *,
    image: str | None = None,
    name: str | None = None,
    description: str | None = None,
    annotations: Mapping[str, str] | None = None,
    dry_run: bool = False,
    git_remote_sha: str | None = None,
    git_remote_branch: str | None = None,
    git_remote_url: str | None = None,
    git_root: str | Path | None = None,
    logger: Logger | None = None,
) -> ComponentPublishResult:
    """Publish one component YAML file using a static Tangle API client."""

    log = logger or get_default_logger()
    path = Path(component_path)
    try:
        spec = prepare_component_for_publish(
            path,
            image=image,
            name=name,
            description=description,
            annotations=annotations,
            git_remote_sha=git_remote_sha,
            git_remote_branch=git_remote_branch,
            git_remote_url=git_remote_url,
            git_root=git_root,
        )
    except Exception as exc:
        log.error(f"❌ Failed to load component {path}: {exc}")
        return ComponentPublishResult(
            status="failed",
            component_path=str(path),
            dry_run=dry_run,
            error=str(exc),
        )

    yaml_text = spec.to_yaml()
    if dry_run:
        log.info(f"[DRY-RUN] Would publish component: {spec.name}")
        return ComponentPublishResult(
            status="dry_run",
            component_path=str(path),
            name=spec.name,
            dry_run=True,
            response={"name": spec.name, "text": yaml_text},
        )

    try:
        response = client.published_components_create(name=spec.name, text=yaml_text)
        digest = _extract_digest(response)
        log.info(f"✅ Published component {spec.name}" + (f" ({digest[:16]}...)" if digest else ""))
        return ComponentPublishResult(
            status="success",
            component_path=str(path),
            name=spec.name,
            digest=digest,
            response=response,
        )
    except Exception as exc:
        log.error(f"❌ Failed to publish component {spec.name}: {exc}")
        return ComponentPublishResult(
            status="failed",
            component_path=str(path),
            name=spec.name,
            error=str(exc),
        )


def deprecate_component(
    client: Any,
    digest: str,
    *,
    superseded_by: str | None = None,
    logger: Logger | None = None,
) -> ComponentDeprecationResult:
    """Mark a published component as deprecated by digest."""

    log = logger or get_default_logger()
    try:
        response = client.published_components_update(
            digest=digest,
            deprecated=True,
            superseded_by=superseded_by,
        )
        log.info(f"✅ Deprecated component {digest[:16]}...")
        if superseded_by:
            log.info(f"   Superseded by: {superseded_by[:16]}...")
        return ComponentDeprecationResult(
            status="success",
            digest=digest,
            superseded_by=superseded_by,
            response=response,
        )
    except Exception as exc:
        log.error(f"❌ Failed to deprecate component {digest[:16]}...: {exc}")
        return ComponentDeprecationResult(
            status="failed",
            digest=digest,
            superseded_by=superseded_by,
            error=str(exc),
        )


def _component_yaml_path(component_path: Path, git_root: str | Path | None) -> str | None:
    if git_root is None:
        return None
    try:
        return str(component_path.resolve().relative_to(Path(git_root).resolve()))
    except ValueError:
        return None


def _extract_digest(response: Any) -> str | None:
    plain = _to_plain(response)
    if isinstance(plain, Mapping):
        digest = plain.get("digest")
        return str(digest) if digest else None
    return None


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
    "ComponentDeprecationResult",
    "ComponentPublishResult",
    "deprecate_component",
    "load_component_spec",
    "prepare_component_for_publish",
    "publish_component",
]
