"""Trust controls for hydration features that can execute local Python code."""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import Iterable

import yaml

_TRUSTED_PYTHON_SOURCES: list[str] = []
_ALLOW_ALL_HYDRATION = False
_PACKAGE_CONFIG = Path(__file__).with_name("trusted_hydration.yaml")
_USER_CONFIGS = (
    Path.home() / ".config" / "tangle" / "trusted_hydration.yaml",
    Path.home() / ".tangle" / "trusted_hydration.yaml",
)
_GLOB_CHARS = set("*?[")


def register_trusted_python_source(source: str | os.PathLike[str]) -> None:
    """Register a trusted Python-source root or glob pattern.

    Sources are matched against canonical resolved paths.  A non-glob source
    trusts the exact file if it resolves to a file (or ends in ``.py``), and a
    directory subtree otherwise.  Glob sources are resolved up to their first
    glob segment and matched against resolved candidate paths.
    """

    text = str(source).strip()
    if text:
        _TRUSTED_PYTHON_SOURCES.append(text)


def set_allow_all_hydration(allow: bool = True) -> None:
    """Set a process-wide escape hatch for trusted hydration execution."""

    global _ALLOW_ALL_HYDRATION
    _ALLOW_ALL_HYDRATION = bool(allow)


def is_trusted_python_source(
    path: str | os.PathLike[str],
    *,
    base_dirs: Iterable[str | os.PathLike[str] | None] | None = None,
    trusted_sources: Iterable[str | os.PathLike[str]] | None = None,
    allow_all: bool = False,
) -> bool:
    """Return whether *path* may be executed for ``local_from_python``.

    The candidate path and every root/pattern prefix are canonicalized with
    :meth:`Path.resolve` before matching so ``..`` traversal and symlink escapes
    cannot extend trust outside the intended boundary.
    """

    if allow_all or _ALLOW_ALL_HYDRATION or _env_allow_all():
        return True

    candidate = _canonical(path)
    if candidate is None:
        return False

    for base_dir in base_dirs or ():
        if base_dir and _is_within(candidate, _canonical(base_dir)):
            return True

    for source in _all_configured_sources(trusted_sources):
        if _matches_source(candidate, str(source)):
            return True

    return False


def configured_trusted_python_sources(
    extra_sources: Iterable[str | os.PathLike[str]] | None = None,
) -> list[str]:
    """Return trusted Python-source patterns from registry/config/env/extras."""

    return [str(source) for source in _all_configured_sources(extra_sources)]


def _all_configured_sources(
    extra_sources: Iterable[str | os.PathLike[str]] | None = None,
) -> list[str]:
    sources: list[str] = []
    sources.extend(_TRUSTED_PYTHON_SOURCES)
    sources.extend(_load_configured_sources())
    sources.extend(_env_trusted_sources())
    if extra_sources:
        sources.extend(str(source) for source in extra_sources if str(source).strip())
    return sources


def _env_allow_all() -> bool:
    value = os.environ.get("TANGLE_TRUSTED_HYDRATION_ALLOW_ALL")
    return bool(value and value.strip().lower() in {"1", "true", "yes", "on"})


def _env_trusted_sources() -> list[str]:
    value = os.environ.get("TANGLE_TRUSTED_PYTHON_SOURCES", "")
    if not value.strip():
        return []
    parts: list[str] = []
    for chunk in value.replace(",", os.pathsep).split(os.pathsep):
        chunk = chunk.strip()
        if chunk:
            parts.append(chunk)
    return parts


def _load_configured_sources() -> list[str]:
    sources: list[str] = []
    for path in _config_paths():
        sources.extend(_load_sources_from_file(path))
    return sources


def _config_paths() -> list[Path]:
    paths = [_PACKAGE_CONFIG, *_USER_CONFIGS]
    override = os.environ.get("TANGLE_TRUSTED_HYDRATION_CONFIG")
    if override:
        paths.append(Path(override).expanduser())
    return paths


def _load_sources_from_file(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    trusted = data.get("trusted_hydration", data) if isinstance(data, dict) else data
    if not isinstance(trusted, dict):
        return []
    raw = trusted.get("trusted_python_sources", [])
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [str(item) for item in raw if str(item).strip()]
    return []


def _canonical(path: str | os.PathLike[str] | None) -> Path | None:
    if path is None:
        return None
    try:
        return Path(path).expanduser().resolve()
    except OSError:
        return None


def _is_within(candidate: Path, root: Path | None) -> bool:
    if root is None:
        return False
    return candidate == root or root in candidate.parents


def _matches_source(candidate: Path, source: str) -> bool:
    source = os.path.expandvars(source.strip())
    if not source:
        return False
    if any(char in source for char in _GLOB_CHARS):
        return _matches_glob_source(candidate, source)
    root = _canonical(source)
    if root is None:
        return False
    source_path = Path(source)
    if root.is_file() or source_path.suffix == ".py":
        return candidate == root
    return _is_within(candidate, root)


def _matches_glob_source(candidate: Path, source: str) -> bool:
    raw = Path(source).expanduser()
    parts = raw.parts
    first_glob = next(
        (index for index, part in enumerate(parts) if any(char in part for char in _GLOB_CHARS)),
        None,
    )
    if first_glob is None:
        return _matches_source(candidate, source)
    prefix_parts = parts[:first_glob]
    suffix_parts = parts[first_glob:]
    if prefix_parts:
        prefix = Path(*prefix_parts).resolve()
    else:
        prefix = Path.cwd().resolve()
    pattern = (prefix / Path(*suffix_parts)).as_posix()
    return fnmatch.fnmatch(candidate.as_posix(), pattern)


def trusted_python_source_guidance(path: str | os.PathLike[str]) -> str:
    """Human-readable refusal guidance for an untrusted Python source."""

    return (
        f"Refusing to execute untrusted local_from_python source {Path(path).expanduser()}. "
        "Add an allowlisted trusted Python source with --trusted-source, "
        "trusted_hydration.trusted_python_sources in config, "
        "TANGLE_TRUSTED_PYTHON_SOURCES, or register_trusted_python_source(); "
        "or use --trusted-hydration / set_allow_all_hydration() for trusted inputs. "
        "You can also pre-hydrate trusted specs and submit them with --no-hydrate."
    )
