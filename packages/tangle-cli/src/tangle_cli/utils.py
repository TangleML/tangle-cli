"""
Generic utility functions for tangle-cli.

YAML parsing/dumping, version comparison, digest computation, git metadata
extraction, and pipeline-spec traversal.
"""

import hashlib
import os
import re
import subprocess
from collections import OrderedDict
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

import yaml

from tangle_cli.logger import Logger, get_default_logger

# =============================================================================
# Generic Data Helpers
# =============================================================================


def _strip_text_from_graph(implementation: dict[str, Any]) -> None:
    """Recursively remove raw component text from graph component references."""

    graph = implementation.get("graph", {})
    for task_data in graph.get("tasks", {}).values():
        ref = task_data.get("componentRef")
        if not ref:
            continue
        ref.pop("text", None)
        spec = ref.get("spec", {})
        nested_impl = spec.get("implementation")
        if nested_impl and "graph" in nested_impl:
            _strip_text_from_graph(nested_impl)


def add_official_prefix(name: str | None) -> str | None:
    """Return the official component name variant used by registry searches."""

    if name and not name.startswith("[Official]"):
        return f"[Official] {name}"
    return name


def _value_from_mapping_or_object(value: object, key: str, default: Any = None) -> Any:
    """Read a field from a mapping, generated model, or attribute object."""

    if isinstance(value, Mapping):
        return value.get(key, default)

    get = getattr(value, "get", None)
    if callable(get):
        return get(key, default)

    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        data = to_dict()
        if isinstance(data, Mapping):
            return data.get(key, default)

    return getattr(value, key, default)


def _optional_str(value: Any) -> str | None:
    """Return *value* only when it is already a string."""

    return value if isinstance(value, str) else None


# =============================================================================
# Numeric Helpers
# =============================================================================


def clamp(value: float, lower: float, upper: float) -> float:
    """Return value bounded to the inclusive ``[lower, upper]`` range."""
    return min(max(value, lower), upper)


# =============================================================================
# Environment Helpers
# =============================================================================

# Values accepted as truthy for boolean-style env vars across Tangle tooling.
_TRUTHY_ENV_VALUES = ("1", "true", "yes")


def tangle_verbose_enabled() -> bool:
    """Return True if the ``TANGLE_VERBOSE`` env var is set to a truthy value.

    Truthy values (case-insensitive): ``"1"``, ``"true"``, ``"yes"``. This is
    the canonical check used by the API client, publisher, and hydrator so
    that verbose-only diagnostics behave consistently across the codebase.
    """
    return os.environ.get("TANGLE_VERBOSE", "").lower() in _TRUTHY_ENV_VALUES


# =============================================================================
# Component-Path Conventions
# =============================================================================


def find_documentation_path_for_yaml(yaml_path: Path) -> str | None:
    """Return ``docs/<stem>.md`` next to a component YAML, if it exists.

    Encodes the convention that a component YAML at ``foo/bar.yaml`` carries
    its human-readable docs at ``foo/docs/bar.md``. Returns the absolute
    path as a string, or ``None`` when no such file exists.
    """
    docs_path = yaml_path.parent / "docs" / f"{yaml_path.stem}.md"
    return str(docs_path.resolve()) if docs_path.exists() else None


# =============================================================================
# String / Template Helpers
# =============================================================================

# Recognizes ``${name}`` or ``${name:-default}`` placeholders. The syntax
# is borrowed from POSIX parameter expansion for familiarity, but these
# placeholders have nothing to do with shells, processes, or environments
# — they're filled from an explicit ``vars`` dict, never from
# ``os.environ``. ``name`` follows Python identifier rules (letter or
# underscore start, then alphanumerics / underscores). ``default`` is
# everything up to the closing ``}`` and may be empty (``${name:-}``).
#
# Convention: prefer lowercase / snake_case ``name``s. Uppercase reads as
# an env-var reference and risks misleading readers about what's actually
# providing the values.
_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


class UnsetVarError(KeyError):
    """Raised when a strict ``${name}`` placeholder has no value and no default.

    A ``KeyError`` subclass so existing ``except KeyError`` handlers keep
    working; the dedicated type lets callers distinguish unresolved
    placeholders from incidental ``KeyError``s if they want a clearer
    error message.
    """


def expand_vars(text: str, vars: dict[str, str]) -> str:
    """Expand ``${name}`` / ``${name:-default}`` placeholders in ``text``.

    Mirrors ``os.path.expandvars`` in syntax, but reads from an explicit
    ``vars`` dict instead of ``os.environ`` — these are *not* environment
    variables, despite the syntax similarity. Lowercase / snake_case
    names are conventional here (uppercase would mislead readers who treat
    the same syntax as env-var interpolation in shells/Docker/etc.).
    Recognized forms:

    * ``${name}`` — strict; raises :class:`UnsetVarError` (a ``KeyError``
      subclass) if ``name`` is missing from ``vars``.
    * ``${name:-default}`` — falls back to the literal ``default`` text when
      ``name`` is missing. ``${name:-}`` substitutes the empty string.

    Substitution is purely textual; values are inserted verbatim. Callers
    that interpolate into structured formats (YAML, JSON, shell commands,
    …) should quote the placeholder appropriately so unusual values can't
    break the surrounding syntax — e.g. for YAML, write
    ``image: "${image:-}"`` so a value beginning with ``*`` doesn't get
    parsed as an alias reference.

    Args:
        text: The text containing zero or more placeholders.
        vars: Flat ``{name: stringified_value}`` map. Empty/None falls back
            to a no-op when no placeholders are present in ``text``.

    Returns:
        ``text`` with every recognized placeholder replaced.

    Raises:
        UnsetVarError: A strict ``${name}`` placeholder had no
            corresponding entry in ``vars``.
    """
    if not vars and "${" not in text:
        return text

    def _replace(m: re.Match[str]) -> str:
        name = m.group(1)
        default = m.group(2)
        if name in vars:
            return vars[name]
        if default is not None:
            return default
        raise UnsetVarError(name)

    return _VAR_RE.sub(_replace, text)


def resolve_input_path(path: Path, config_dir: Path | None) -> Path:
    """Resolve a relative input path by trying cwd first, then the config directory.

    Used to make config file entries portable: a relative input path like
    ``pipelines/foo.yaml`` is tried against the cwd first (preserving existing
    behavior), then against the config file's directory as a fallback.

    Args:
        path: Input path to resolve.
        config_dir: Directory of the config file. If ``None``, path is returned unchanged.

    Returns:
        The resolved absolute path, or the original path if nothing matched.
    """
    if config_dir is None or path.is_absolute() or path.exists():
        return path
    candidate = config_dir / path
    return candidate.resolve() if candidate.exists() else path


# =============================================================================
# Dict merge helpers
# =============================================================================


def apply_defaults(
    entries: dict[str, Any] | list[dict[str, Any]],
    defaults: dict[str, Any],
) -> dict[str, Any] | list[dict[str, Any]]:
    """Shallow-merge *defaults* into *entries* (entry values take precedence).

    Works on a single dict, a list of dicts, or a dict-of-dicts (keyed entries).
    For a dict-of-dicts, keys starting with ``_`` are excluded from merging
    (they are metadata like ``_defaults`` itself).

    Args:
        entries: The entries to merge defaults into.
        defaults: Default values (overridden by entry values).

    Returns:
        Merged result in the same shape as *entries*.
    """
    if isinstance(entries, list):
        return [{**defaults, **item} if isinstance(item, dict) else item for item in entries]
    return {**defaults, **entries}


# =============================================================================
# Digest Utilities
# =============================================================================


def compute_text_digest(text: str) -> str:
    """Compute a SHA256 digest from raw text.

    Args:
        text: The text to hash.

    Returns:
        Hex digest string.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_spec_digest(spec: dict[str, Any]) -> str:
    """Compute a SHA256 digest for a component spec.

    Args:
        spec: The component spec dict.

    Returns:
        Hex digest string.
    """
    # Serialize spec to YAML with sorted keys for deterministic output
    yaml_str = dump_yaml(spec, sort_keys=True)
    return compute_text_digest(yaml_str)


# Type alias for task processor callback
# Receives (task_name, task_data, path, base_dir) and returns processed task_data.
TaskProcessor = Callable[[str, dict[str, Any], str, Path | None, dict[str, Any] | None], dict[str, Any]]


def is_subgraph_spec(spec: dict[str, Any] | None) -> bool:
    """Check if a spec contains a subgraph (has implementation.graph)."""
    if not spec:
        return False
    return "graph" in spec.get("implementation", {})


def is_graph_task(task_data: dict[str, Any]) -> bool:
    """Check if a task has a componentRef that is a subgraph.

    Args:
        task_data: The task dict to check.

    Returns:
        True if the task has a componentRef with nested implementation.graph.
    """
    component_ref = task_data.get("componentRef")
    if not isinstance(component_ref, dict):
        return False
    return is_subgraph_spec(component_ref.get("spec", {}))


def get_component_ref_info(component_ref: dict[str, Any]) -> tuple[str, str]:
    """Extract name and digest from a componentRef.

    Args:
        component_ref: The componentRef dict (must have spec.name and digest).

    Returns:
        Tuple of (name, digest).
    """
    name = component_ref.get("spec", {}).get("name", "unknown")
    digest = component_ref.get("digest", "unknown")
    return name, digest


def _strip_internal_annotations(spec: dict[str, Any]) -> None:
    """Remove all internal underscore-prefixed keys from a spec dict.

    These keys (e.g. ``_source_dir``, ``_recursive_params``) are used during
    traversal and must not leak into the final output.
    """
    for key in [k for k in spec if k.startswith("_")]:
        del spec[key]


def _extract_source_dir(spec: dict[str, Any], fallback: Path | None) -> Path | None:
    """Extract and remove _source_dir annotation from a spec.

    When a component is loaded from a local file, _source_dir is set to the
    directory containing that file. This allows nested file:// references to
    be resolved relative to the file they appear in, not the top-level pipeline.
    """
    source_dir = spec.pop("_source_dir", None)
    if source_dir is not None:
        return Path(source_dir)
    return fallback


def _extract_recursive_params(
    spec: dict[str, Any], fallback: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Extract and remove _recursive_params annotation from a spec.

    When recursive context is active, _recursive_params carries the accumulated
    template parameters for this subtree. Works like _source_dir: the value is
    consumed here and threaded through the recursive traversal.
    """
    return spec.pop("_recursive_params", fallback)


def traverse_pipeline_tasks(
    spec: dict[str, Any],
    parent_name: str,
    task_processor: TaskProcessor,
    base_dir: Path | None = None,
    recursive_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Traverse a pipeline/component spec and process each task recursively.

    This function walks through implementation.graph.tasks. For each task:
    - If it's a subgraph (has componentRef with nested graph), recurse into it without processing
    - Otherwise, call task_processor to handle the task

    When a nested spec has a '_source_dir' annotation (set when a component was
    loaded from a local file), the base_dir is updated for that subtree so that
    nested file:// references resolve relative to the loaded file.

    Similarly, '_recursive_params' carries accumulated template parameters for
    recursive context propagation. Like _source_dir, the value is extracted from
    specs at recursion boundaries and threaded through to the task processor.

    Args:
        spec: The component/pipeline spec with implementation.graph.tasks structure.
        parent_name: Name prefix for path display (e.g., pipeline name).
        task_processor: Callback to process non-subgraph tasks.
                       Receives (task_name, task_data, path, base_dir, recursive_params)
                       and returns the processed task dict.
        base_dir: Base directory for resolving relative file paths. Updated
                 automatically when entering specs loaded from local files
                 (via _source_dir annotation).
        recursive_params: Accumulated template parameters for recursive context.
                         Updated automatically when entering specs with
                         _recursive_params annotation.

    Returns:
        The spec with all tasks processed (including nested subgraph tasks).
    """
    implementation = spec.get("implementation", {})
    graph = implementation.get("graph", {})
    tasks = graph.get("tasks", {})

    if not tasks:
        return spec

    processed_tasks = {}
    for task_name, task_data in tasks.items():
        path = f"{parent_name}.{task_name}" if parent_name else task_name

        # If task is a subgraph, recurse into it without processing
        if is_graph_task(task_data):
            component_ref = task_data["componentRef"]
            nested_spec = component_ref.get("spec", {})
            nested_name = component_ref.get("name", task_name)
            nested_base_dir = _extract_source_dir(nested_spec, base_dir)
            nested_params = _extract_recursive_params(nested_spec, recursive_params)

            resolved_nested_spec = traverse_pipeline_tasks(
                nested_spec, nested_name, task_processor, nested_base_dir, nested_params
            )
            _strip_internal_annotations(resolved_nested_spec)

            if resolved_nested_spec != nested_spec:
                processed_task = dict(task_data)
                # Use spec name as fallback, compute digest if not present
                new_ref = {
                    "name": component_ref.get("name") or nested_spec.get("name", ""),
                    "digest": component_ref.get("digest") or compute_spec_digest(resolved_nested_spec),
                    "spec": resolved_nested_spec,
                }
                processed_task["componentRef"] = new_ref
            else:
                processed_task = task_data
        else:
            # Process non-subgraph tasks, passing current base_dir and recursive params
            processed_task = task_processor(task_name, task_data, path, base_dir, recursive_params)

            # If processing created a subgraph, recurse into it
            if is_graph_task(processed_task):
                component_ref = processed_task["componentRef"]
                nested_spec = component_ref.get("spec", {})
                nested_name = component_ref.get("name", task_name)
                nested_base_dir = _extract_source_dir(nested_spec, base_dir)
                nested_params = _extract_recursive_params(nested_spec, recursive_params)

                resolved_nested_spec = traverse_pipeline_tasks(
                    nested_spec, nested_name, task_processor, nested_base_dir, nested_params
                )
                _strip_internal_annotations(resolved_nested_spec)

                if resolved_nested_spec != nested_spec:
                    processed_task = dict(processed_task)
                    # Use spec name as fallback, compute digest if not present
                    new_ref = {
                        "name": component_ref.get("name") or nested_spec.get("name", ""),
                        "digest": component_ref.get("digest") or compute_spec_digest(resolved_nested_spec),
                        "spec": resolved_nested_spec,
                    }
                    processed_task["componentRef"] = new_ref
            else:
                # Strip internal annotations from non-subgraph specs (no nested tasks to resolve)
                cr = processed_task.get("componentRef")
                if isinstance(cr, dict):
                    s = cr.get("spec")
                    if isinstance(s, dict):
                        _strip_internal_annotations(s)

        processed_tasks[task_name] = processed_task

    # Rebuild the spec with processed tasks
    result = dict(spec)
    result["implementation"] = dict(implementation)
    result["implementation"]["graph"] = dict(graph)
    result["implementation"]["graph"]["tasks"] = processed_tasks
    return result


def parse_yaml_string(yaml_content, logger: Logger | None = None):
    """
    Parse a YAML string into a data structure.

    Args:
        yaml_content: YAML string content

    Returns:
        Parsed data structure or None if parsing fails
    """
    log = logger or get_default_logger()

    # Setup YAML to properly handle OrderedDict and compact lists
    def represent_ordereddict(dumper, data):
        return dumper.represent_dict(data.items())

    yaml.add_representer(OrderedDict, represent_ordereddict)

    try:
        return yaml.safe_load(yaml_content)
    except Exception as e:
        import traceback
        log.error(f"YAML parsing error: {e}")
        log.error(f"Traceback: {traceback.format_exc()}")
        return None


class _LiteralBlockDumper(yaml.SafeDumper):
    """YAML dumper that uses literal block style (|) for multiline strings."""
    pass


def _literal_str_representer(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    if '\n' in data:
        return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='|')
    return dumper.represent_scalar('tag:yaml.org,2002:str', data)


_LiteralBlockDumper.add_representer(str, _literal_str_representer)


def dump_yaml(data: dict[str, Any], sort_keys: bool = False, width: int | None = None) -> str:
    """
    Dump a data structure to a YAML string with consistent formatting.

    Multiline strings are rendered using literal block style (|).

    Args:
        data: Dictionary to serialize to YAML
        sort_keys: Whether to sort dictionary keys (default: False)
        width: Line width limit (default: None, no limit)

    Returns:
        YAML string
    """
    return yaml.dump(
        data, Dumper=_LiteralBlockDumper,
        default_flow_style=False, sort_keys=sort_keys, allow_unicode=True, width=width,
    )


def get_version_from_data(data):
    """
    Extract version from a data dictionary (parsed YAML structure).

    Checks metadata.annotations.version first (preferred), then falls back
    to top-level version for backward compatibility.

    Args:
        data: Dictionary containing the parsed YAML structure

    Returns:
        Version string or None if not found
    """
    if not data:
        return None

    # Check metadata.annotations.version first (preferred location)
    metadata = data.get('metadata')
    if metadata:
        annotations = metadata.get('annotations')
        if annotations and 'version' in annotations:
            return str(annotations['version'])

    # Fall back to top-level version for backward compatibility
    if 'version' in data:
        return str(data['version'])

    return None


def get_version_component(parts, index, default=0):
    """
    Get version component at index as int, or default if not parseable.

    Args:
        parts: List of version components
        index: Index to retrieve
        default: Default value if component is missing or not numeric

    Returns:
        Integer version component or default
    """
    try:
        return int(parts[index]) if index < len(parts) else default
    except (ValueError, TypeError, IndexError):
        return default


def compare_versions(a: str, b: str) -> int:
    """Compare two version strings component-wise, returning -1, 0, or 1.

    Unlike :func:`check_versions`, this pads the shorter version with
    zeros so that ``1.0.1`` is correctly greater than ``1.0``.

    Args:
        a: First version string (e.g. "1.2.3").
        b: Second version string (e.g. "1.2").

    Returns:
        -1 if a < b, 0 if a == b, 1 if a > b.
    """
    a_parts = a.split(".")
    b_parts = b.split(".")
    length = max(len(a_parts), len(b_parts))
    for i in range(length):
        a_val = get_version_component(a_parts, i)
        b_val = get_version_component(b_parts, i)
        if a_val > b_val:
            return 1
        if a_val < b_val:
            return -1
    return 0


def check_versions(local_version, latest_version, check_precedence=False):
    """Check if a version update should proceed.

    Thin wrapper around :func:`compare_versions` for backward compatibility.

    Args:
        local_version: The local version string.
        latest_version: The latest published version (or None if not found).
        check_precedence: If True, return True only when *local* is strictly
            newer.  If False (default), return True when versions differ.

    Returns:
        bool: True if should proceed with update, False if should skip.
    """
    if not latest_version:
        return True

    cmp = compare_versions(local_version, latest_version)

    if check_precedence:
        return cmp > 0
    return cmp != 0


# =============================================================================
# Git info collection
# =============================================================================


def get_git_root(directory: Path) -> Path | None:
    """Find the git repository root for a directory."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(directory), capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return Path(result.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


def get_git_info(directory: Path, logger: Logger | None = None) -> dict[str, str]:
    """Collect git metadata for annotations.

    Uses subprocess git commands to avoid requiring gitpython.
    The returned dict includes a ``_git_root`` key (absolute path to the
    repository root) so callers can compute relative paths without a
    second subprocess call.  This key is prefixed with ``_`` to signal
    it is not a component annotation and should not be persisted.
    """
    info: dict[str, str] = {}

    try:
        # Find git root
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(directory), capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            if logger:
                stderr = result.stderr.strip() if result.stderr else "unknown reason"
                logger.warn(f"⚠️  Not a git repository ({stderr}). "
                            "Will try CI environment variables.")
        else:
            git_root = Path(result.stdout.strip())
            info["_git_root"] = str(git_root)

            # git_relative_dir
            try:
                rel_dir = directory.resolve().relative_to(git_root)
                info["git_relative_dir"] = rel_dir.as_posix()
            except ValueError:
                pass

            # git_local_branch
            result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=str(directory), capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                info["git_local_branch"] = result.stdout.strip()

            # git_local_sha
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(directory), capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                info["git_local_sha"] = result.stdout.strip()

            # Tracking branch info
            result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
                cwd=str(directory), capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                tracking = result.stdout.strip()  # e.g., "origin/main"
                parts = tracking.split("/", 1)
                if len(parts) == 2:
                    remote_name, remote_branch = parts
                    info["git_remote_branch"] = remote_branch

                    # Remote URL
                    result = subprocess.run(
                        ["git", "remote", "get-url", remote_name],
                        cwd=str(directory), capture_output=True, text=True, timeout=5,
                    )
                    if result.returncode == 0:
                        info["git_remote_url"] = result.stdout.strip()

                    # Remote SHA
                    result = subprocess.run(
                        ["git", "rev-parse", tracking],
                        cwd=str(directory), capture_output=True, text=True, timeout=5,
                    )
                    if result.returncode == 0:
                        info["git_remote_sha"] = result.stdout.strip()

            # Fallback: if no tracking branch, use local sha/branch and origin URL
            if "git_remote_url" not in info:
                result = subprocess.run(
                    ["git", "remote", "get-url", "origin"],
                    cwd=str(directory), capture_output=True, text=True, timeout=5,
                )
                if result.returncode == 0:
                    info["git_remote_url"] = result.stdout.strip()
            if "git_remote_sha" not in info and "git_local_sha" in info:
                info["git_remote_sha"] = info["git_local_sha"]
            if "git_remote_branch" not in info and "git_local_branch" in info:
                info["git_remote_branch"] = info["git_local_branch"]

    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        if logger:
            logger.warn(f"⚠️  Git not available ({type(e).__name__}: {e}). "
                        "Will try CI environment variables.")

    # Fallback: populate missing fields from CI environment variables
    _fill_from_ci_env(info)

    # Normalize SSH git URLs to HTTPS (e.g. git@github.com:Org/repo.git -> https://github.com/Org/repo.git)
    if "git_remote_url" in info:
        info["git_remote_url"] = _normalize_git_url(info["git_remote_url"])

    # Log resolved git metadata and warn about missing fields
    if logger:
        logger.info("   Git metadata resolved:")
        logger.info(f"     _git_root: {info.get('_git_root', '(not set)')}")
        logger.info(f"     git_remote_sha: {info.get('git_remote_sha', '(not set)')}")
        logger.info(f"     git_remote_branch: {info.get('git_remote_branch', '(not set)')}")
        logger.info(f"     git_remote_url: {info.get('git_remote_url', '(not set)')}")

        missing = []
        if "_git_root" not in info:
            missing.append("git_root (needed for component_yaml_path)")
        if "git_remote_url" not in info:
            missing.append("git_remote_url")
        if "git_remote_sha" not in info:
            missing.append("git_remote_sha")
        if "git_remote_branch" not in info:
            missing.append("git_remote_branch")
        if missing:
            logger.warn(
                f"⚠️  Missing git metadata: {', '.join(missing)}. "
                "Published components will lack source links and transparency signals. "
                "Pass --git-remote-sha/--git-remote-branch/--git-remote-url or run from a git repo."
            )

    return info


def set_component_yaml_path(rel_path: str, annotations: dict[str, str], *, overwrite: bool = True) -> None:
    """Split a repo-relative path into git_relative_dir and component_yaml_path annotations.

    Given ``"a/b/comp.yaml"``, sets ``git_relative_dir="a/b"`` and
    ``component_yaml_path="comp.yaml"``.  For a bare filename like
    ``"comp.yaml"``, only ``component_yaml_path`` is set.

    Args:
        overwrite: If False, preserve existing values (setdefault semantics).
    """
    parts = rel_path.rsplit("/", 1)
    if overwrite:
        if len(parts) == 2:
            annotations["git_relative_dir"] = parts[0]
            annotations["component_yaml_path"] = parts[1]
        else:
            annotations["component_yaml_path"] = rel_path
    else:
        if len(parts) == 2:
            annotations.setdefault("git_relative_dir", parts[0])
            annotations.setdefault("component_yaml_path", parts[1])
        else:
            annotations.setdefault("component_yaml_path", rel_path)


def normalize_annotation_paths(
    yaml_path: "str | Path",
    git_root: "str | Path",
    annotations: dict[str, str],
) -> None:
    """Normalize ``dockerfile_path`` and ``documentation_path`` to be relative to ``git_relative_dir``.

    Component authors may write path annotations relative to the YAML file's
    directory (e.g. ``../../../../dockerfiles/foo.Dockerfile``) or relative to
    ``git_relative_dir`` (e.g. ``dockerfiles/foo.Dockerfile``).  This function
    resolves each path using filesystem checks and re-expresses it relative to
    the final ``git_relative_dir``.

    Resolution order for each path annotation:

    1. Relative to ``git_relative_dir`` — if the file exists, leave the value
       as-is (already correct).
    2. Relative to the YAML file's parent directory — if the file exists,
       re-express it relative to ``git_relative_dir``.
    3. If neither resolves to an existing file, leave the value unchanged.

    This is a no-op when ``git_relative_dir`` equals the YAML file's parent
    directory (the common case).

    Args:
        yaml_path: Filesystem path to the component YAML file.
        git_root: Filesystem path to the git repository root.
        annotations: The ``metadata.annotations`` dict (modified in place).
    """
    import os
    from pathlib import Path as _Path

    git_relative_dir = annotations.get("git_relative_dir")
    if not git_relative_dir:
        return

    git_root = _Path(git_root)
    yaml_parent = _Path(yaml_path).resolve().parent
    git_rel_dir_abs = (git_root / git_relative_dir).resolve()

    # If git_relative_dir resolves to the YAML parent, paths are equivalent — skip
    if git_rel_dir_abs == yaml_parent:
        return

    for key in ("dockerfile_path", "documentation_path"):
        value = annotations.get(key)
        if not value:
            continue

        # 1. Already relative to git_relative_dir?
        candidate_git = git_rel_dir_abs / value
        if candidate_git.resolve().exists():
            continue  # already correct

        # 2. Relative to YAML parent dir?
        candidate_yaml = yaml_parent / value
        if candidate_yaml.resolve().exists():
            # Re-express relative to git_relative_dir.  Use os.path.relpath
            # rather than Path.relative_to so that files *above*
            # git_relative_dir produce ``../`` prefixed paths.
            normalized = os.path.relpath(
                str(candidate_yaml.resolve()), str(git_rel_dir_abs)
            )
            annotations[key] = normalized


# CI environment variables probed for git metadata (checked in order, first
# match wins).  Covers Buildkite, GitHub Actions, and GitLab CI out of the
# box.  Wrapper packages can prepend additional CI-system-specific variables
# by monkey-patching these module attributes at import time.
_CI_GIT_ROOT_VARS: tuple[str, ...] = ("BUILDKITE_BUILD_CHECKOUT_PATH", "GITHUB_WORKSPACE", "CI_PROJECT_DIR")
_CI_SHA_VARS: tuple[str, ...] = ("BUILDKITE_COMMIT", "GITHUB_SHA", "CI_COMMIT_SHA")
_CI_BRANCH_VARS: tuple[str, ...] = ("BUILDKITE_BRANCH", "GITHUB_REF_NAME", "CI_COMMIT_BRANCH")
_CI_REPO_URL_VARS: tuple[str, ...] = ("BUILDKITE_REPO", "GITHUB_SERVER_URL", "CI_REPOSITORY_URL")


# Credential words that are conclusive as the final word of a canonicalized name.
# Terminal position rather than substring containment is what redacts
# ``x-api-key``/``apiKey``/``cloud-auth``/``X-Amz-Security-Token`` while preserving
# benign git metadata that merely mentions them (``author``, ``token_count``,
# ``keychain_path``, ``function_signature``, ``authorization_url``).  Unbroken
# lower-case runs are listed in full because no separator or camelCase boundary
# exists to split them.
_CREDENTIAL_TERMINAL_WORDS: frozenset[str] = frozenset({
    "apikey", "auth", "authentication", "authorization", "bearer", "cookie",
    "credential", "credentials", "key", "keys", "oauth", "passphrase", "passwd",
    "password", "pwd", "secret", "secrets", "session", "token", "tokens",
    "accessid", "accesskey", "accesskeyid", "accesstoken", "apisecret",
    "apitoken", "authtoken", "awsaccesskeyid", "bearertoken", "clientsecret",
    "googleaccessid", "idtoken", "privatekey", "refreshtoken", "secretkey",
    "sessionkey", "sessiontoken",
})

# Two-word endings that are credentials even though the final word is not one on
# its own, so ``presigned-url`` is redacted while ``authorization_url`` is kept.
_CREDENTIAL_PHRASES: frozenset[tuple[str, str]] = frozenset({
    ("presigned", "url"), ("signed", "url"),
})

# Words that turn a trailing credential word into a quantity (``max_tokens``,
# ``num_keys``), which is metadata rather than a secret.
_QUANTIFIER_WORDS: frozenset[str] = frozenset({
    "avg", "count", "max", "mean", "min", "n", "num", "size", "total",
})

# Trailing words that name an identifier rather than a value.  They are peeled off
# so ``token_id``/``apiKeyId`` are recognized, and after peeling a word such as
# ``access`` is itself conclusive (``AWSAccessKeyId``, ``GoogleAccessId``) while
# ``account_id``/``request_id``/``trace_id`` stay benign.
_IDENTIFIER_SUFFIX_WORDS: frozenset[str] = frozenset({"id", "ident", "identifier"})
_CREDENTIAL_ID_WORDS: frozenset[str] = frozenset({"access"})

# A trailing version marker (``authToken2``, ``x-api-key-v2``) is not part of the
# credential name and is peeled off before classification.  The ``v`` and the digits
# are separate words after canonicalization, so both spellings are matched.
_VERSION_SUFFIX_RE = re.compile(r"^(?:v[0-9]*|[0-9]+)$")

# Signed-URL parameters whose names are only credentials as an exact whole key.
# Matching exactly is what separates ``signature``/``sig`` from
# ``function_signature`` and ``SignatureVersion``.
_SIGNED_URL_QUERY_RE = re.compile(
    r"x-(?:amz|goog|ms)-(?:signature|credential|security-token)"
    r"|sig|signature|awsaccesskeyid|googleaccessid",
    re.IGNORECASE,
)

_KEY_WORD_BOUNDARY_RE = re.compile(
    r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|(?<=[A-Za-z])(?=[0-9])"
)
_KEY_WORD_SPLIT_RE = re.compile(r"[^A-Za-z0-9]+")


def _key_words(key: str) -> list[str]:
    """Split a parameter name into lower-case words on separators and camelCase."""
    spaced = _KEY_WORD_BOUNDARY_RE.sub("-", key)
    return [word for word in _KEY_WORD_SPLIT_RE.split(spaced.lower()) if word]


def _names_credential(words: list[str]) -> bool:
    """Return whether a canonicalized word list ends in a credential name."""
    if len(words) >= 2 and (words[-2], words[-1]) in _CREDENTIAL_PHRASES:
        return True
    if words[-1] not in _CREDENTIAL_TERMINAL_WORDS:
        return False
    return len(words) < 2 or words[-2] not in _QUANTIFIER_WORDS


def _is_sensitive_name(name: str) -> bool:
    """Return whether a canonicalized parameter name carries a credential.

    Identifier and version suffixes are peeled off one at a time so that
    ``token_id``, ``apiKeyId``, ``authToken2``, and ``x-api-key-v2`` reduce to a
    recognizable credential name, while names that only *end* in an identifier
    (``account_id``, ``request_id``) reduce to something benign and are kept.
    """
    words = _key_words(name)
    while words:
        if _names_credential(words):
            return True
        last = words[-1]
        if last in _IDENTIFIER_SUFFIX_WORDS:
            words = words[:-1]
            if words and words[-1] in _CREDENTIAL_ID_WORDS:
                return True
            continue
        if _VERSION_SUFFIX_RE.match(last):
            words = words[:-1]
            continue
        return False
    return False


def _is_sensitive_query_key(key: str) -> bool:
    """Return whether a URL query parameter name carries authentication material.

    Names are canonicalized into words first, so hyphen, underscore, and
    camelCase spellings of the same key (``x-api-key``, ``api_key``, ``apiKey``)
    are treated identically.  Classification is by terminal word and exact whole
    key rather than by substring, which is what lets benign metadata such as
    ``author``, ``max_tokens``, ``function_signature``, and ``SignatureVersion``
    survive while provider-specific credential spellings are still caught.
    Percent-encoded spellings of the name itself (``access%5Ftoken``,
    ``%74oken``) are screened through the same collapsed copies as every other
    check, since the consumer that decodes the link also decodes the key.
    """
    return _screen_reveals(
        key.strip(),
        lambda form: _SIGNED_URL_QUERY_RE.fullmatch(form) or _is_sensitive_name(form),
    )


# A benign key can still carry a credential in its *value*: a nested URL
# (``?ref=https://u:s@h/p``), bare userinfo (``?ref=u:s@h``), or a second
# assignment the query parser folds into the value (``?ref=main?access_token=…``,
# since only the first ``?`` delimits the query).  Percent-encoded spellings count
# because whatever consumes the link decodes them.
#
# Both patterns are written to run in linear time on hostile input, since they are
# applied to attacker-influenced text of unbounded length.  The lookbehinds pin
# each attempt to the start of a scheme token or a path segment instead of letting
# it restart at every offset, and the second class in the userinfo pattern excludes
# ``:`` so the two repeats cannot split the same run in more than one way.
_NESTED_SCHEME_RE = re.compile(
    r"(?<![A-Za-z0-9+.\-])[A-Za-z][A-Za-z0-9+.\-]*:(?:/|%2f)", re.IGNORECASE
)
_NESTED_USERINFO_RE = re.compile(
    r"(?<![^/\s@])[^/\s@]*:[^/\s@:]*(?:@|%40)", re.IGNORECASE
)

# The structural patterns above recognize literal separators plus one encoded
# spelling, but a percent-encoded userinfo (``user%3Asecret%40host``) has
# neither and decodes back into a credential wherever the link is consumed.
# Screening therefore also judges percent-decoded copies of the text, while the
# output is always rebuilt from the original bytes, so benign encodings survive
# exactly as written.
#
# Re-encoding nests: ``@`` → ``%40`` → ``%2540`` → ``%252540``, i.e. an octet at
# encode depth *k* is ``%`` + ``25``*(k-1) + hex.  Decoding one layer per pass
# would need as many passes as the attacker chose a depth, so a pass instead
# collapses the whole ``%(25)*HH`` tower to its final character at once — any
# uniformly nested octet is closed in a single pass regardless of depth.
# Escape sequences can also be assembled *across* replacements (``%%3430``,
# where a pass must first produce ``%40`` before it exists to decode), and by
# staggering that shape an attacker buys one pass per layer at a threefold size
# cost per level.  The pass cap therefore cannot chase every input to its fixed
# point; instead, text still resolving when the cap runs out is reported as
# matching, so the URL fails closed rather than passing bytes screening never
# examined.  Each pass is one linear scan whose backtracking is bounded to a
# single ``25`` pair and strictly shortens the text, so with the fixed cap the
# total cost stays linear.
_NESTED_PERCENT_OCTET_RE = re.compile(r"%(?:25)*([0-9A-Fa-f]{2})")
_MAX_PERCENT_COLLAPSE_PASSES: int = 4


def _collapse_percent_octets(text: str) -> str:
    """Decode every percent octet, however deeply its ``%`` is itself encoded."""
    return _NESTED_PERCENT_OCTET_RE.sub(
        lambda match: chr(int(match.group(1), 16)), text
    )


def _screen_reveals(text: str, match: Callable[[str], object]) -> bool:
    """Judge *text* and its percent-collapsed copies against *match*.

    Screening only — callers rebuild output from the original bytes.  Returns
    true when any form matches, or when the pass cap is exhausted while octets
    are still resolving and the remaining bytes cannot be examined.
    """
    if match(text):
        return True
    for _ in range(_MAX_PERCENT_COLLAPSE_PASSES):
        if "%" not in text:
            return False
        decoded = _collapse_percent_octets(text)
        if decoded == text:
            return False
        if match(decoded):
            return True
        text = decoded
    return "%" in text and _collapse_percent_octets(text) != text


_ASSIGNMENT_NAME_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)


def _embedded_assignment_names(value: str) -> Iterator[str]:
    """Yield the names of ``name=…``/``name:…`` assignments embedded in *value*.

    A name is the longest run of ``[A-Za-z0-9._-]`` that is followed by optional
    whitespace and then ``=`` or ``:``, starting at the run's first letter.

    Deliberately a hand-written scan rather than a pattern.  The equivalent
    regex has to be retried at every offset of a run and backtracks across the
    rest of it each time, which is quadratic: a benign ``?ref=`` followed by
    32 KB of letters took eleven seconds, and that value arrives from a
    server-supplied annotation.  This loop visits each character a bounded
    number of times, so cost is linear in ``len(value)`` with no backtracking.
    """
    length = len(value)
    index = 0
    while index < length:
        if value[index] not in _ASSIGNMENT_NAME_CHARS:
            index += 1
            continue
        first_letter = -1
        while index < length and value[index] in _ASSIGNMENT_NAME_CHARS:
            if first_letter < 0 and value[index].isalpha():
                first_letter = index
            index += 1
        run_end = index
        while index < length and value[index].isspace():
            index += 1
        if first_letter >= 0 and index < length and value[index] in "=:":
            yield value[first_letter:run_end]


def _value_is_unsafe(value: str) -> bool:
    """Return whether a parameter value embeds credential material.

    Matching is structural — a nested scheme, a userinfo pattern, or an embedded
    assignment whose name :func:`_is_sensitive_query_key` recognizes — so ordinary
    values (``main``, ``refs/heads/topic``, ``AWS4-HMAC-SHA256``) are untouched.

    Nested names are judged by the same predicate as top-level keys.  The narrower
    :func:`_is_sensitive_name` misses the signed-URL family (``sig``,
    ``X-Amz-Signature``), which would then be dropped in key position but
    preserved once folded into a benign value such as ``?ref=main?sig=…``.
    """
    def _form_is_unsafe(form: str) -> bool:
        if _NESTED_SCHEME_RE.search(form) or _NESTED_USERINFO_RE.search(form):
            return True
        return any(map(_is_sensitive_query_key, _embedded_assignment_names(form)))

    return _screen_reveals(value, _form_is_unsafe)


def _is_unsafe_pair(key: str, value: str) -> bool:
    """Return whether a query/fragment parameter must be dropped.

    The key is screened for embedded credential structure as well as by name:
    a userinfo blob lands in key position whenever the attacker omits ``=``
    around it (``?u:s@h=1`` parses as key ``u:s@h``).
    """
    return (
        _is_sensitive_query_key(key)
        or _value_is_unsafe(key)
        or _value_is_unsafe(value)
    )


def _redact_sensitive_query(query: str) -> str:
    """Drop credential-bearing parameters from a URL query string.

    Keys are classified by :func:`_is_sensitive_query_key`, which recognizes
    credential names across separator and casing variants rather than only an
    enumerated allowlist.  Values are screened separately by
    :func:`_value_is_unsafe`, because a benign key can still carry a nested
    credential URL.  A query with nothing sensitive is returned byte-for-byte
    unchanged so that ordinary URLs are not silently re-encoded.

    ``parse_qsl`` silently discards fields without ``=``, so a credential
    hidden in such a field would never reach the pair screen; those fields are
    screened directly, and when one is unsafe the result is rebuilt from the
    classifiable pairs only.
    """
    if not query:
        return query

    from urllib.parse import parse_qsl, urlencode

    pairs = parse_qsl(query, keep_blank_values=True)
    bare_field_unsafe = any(
        _value_is_unsafe(field)
        for field in query.split("&")
        if field and "=" not in field
    )
    if not bare_field_unsafe and not any(
        _is_unsafe_pair(key, value) for key, value in pairs
    ):
        return query
    kept = [(key, value) for key, value in pairs if not _is_unsafe_pair(key, value)]
    return urlencode(kept)


# A fragment that embeds a nested URL or userinfo (``#https://u:s@h/p``,
# ``#user:secret@host``) cannot be classified parameter-by-parameter, so it is
# dropped whole.  Percent-encoded spellings are covered too because a fragment is
# decoded by whatever consumes the link.
_FRAGMENT_UNSAFE_RE = re.compile(
    r"(?<![A-Za-z0-9+.\-])[A-Za-z][A-Za-z0-9+.\-]*:(?:/|%2f)|@|%40", re.IGNORECASE
)


def _redact_sensitive_fragment(fragment: str) -> str:
    """Drop credential material from a URL fragment, preserving benign anchors.

    Git hosts use fragments as document anchors (``#readme``, ``#L42``), which are
    useful and carry no secrets, so they are returned byte-for-byte.  OAuth
    implicit-flow responses and signed links instead put credentials in the
    fragment (``#access_token=…``), so a query-like fragment is filtered by
    parameter name using the same classifier as the query string.  Anything that
    cannot be classified — a nested URL or userinfo — fails closed and is dropped.
    """
    if not fragment:
        return fragment

    if _screen_reveals(fragment, _FRAGMENT_UNSAFE_RE.search):
        return ""
    if not _screen_reveals(fragment, lambda form: "=" in form):
        return fragment
    if "=" not in fragment:
        # Query-like only once decoded (``access_token%3D…``): the parameters
        # cannot be filtered without rewriting the fragment's bytes, so it is
        # dropped whole.
        return ""

    from urllib.parse import parse_qsl, urlencode

    pairs = parse_qsl(fragment, keep_blank_values=True)
    if not pairs:
        return ""
    if not any(_is_unsafe_pair(key, value) for key, value in pairs):
        return fragment
    kept = [(key, value) for key, value in pairs if not _is_unsafe_pair(key, value)]
    return urlencode(kept)


def _decoded_delimiter_unsafe(form: str) -> bool:
    """Screen a decoded ``?`` or ``#`` suffix revealed inside path or host text.

    ``urlsplit`` splits on literal delimiters only, so an encoded one
    (``repo%3Faccess_token%3D…``) keeps its parameters inside the path or the
    reported hostname, where the query and fragment classifiers never look.
    The suffix is judged by those same classifiers; the caller fails closed on
    a credential because the parameters cannot be dropped without rewriting
    the text's bytes.
    """
    for index, char in enumerate(form):
        if char == "?":
            query, _, fragment = form[index + 1 :].partition("#")
            return (
                _redact_sensitive_query(query) != query
                or _redact_sensitive_fragment(fragment) != fragment
            )
        if char == "#":
            fragment = form[index + 1 :]
            return _redact_sensitive_fragment(fragment) != fragment
    return False


# Placeholder emitted when a URL-like remote carries credentials but cannot be
# parsed into a clean host (malformed authority, missing host with userinfo,
# malformed IPv6).  Returning this rather than the raw input keeps credential
# material out of persisted annotations, CLI output, logs, and browse links, and
# stops the parser from raising into callers.
_REDACTED_GIT_URL: str = "[redacted-invalid-git-url]"

# RFC 3986 scheme syntax followed by the start of a hier-part.  The slash is the
# structural discriminator between URL-form and SCP-form: every URL-form remote has
# one (``https:/``, ``git+https://``, ``hg:/``, ``foo:/``) and no SCP remote does
# (``github.com:Org/repo``).  Recognizing the shape instead of enumerating known
# schemes keeps any scheme — including unknown and malformed ones — out of the
# SCP rewrite, which would otherwise mistake the scheme for a hostname and
# re-embed the userinfo it was supposed to strip.
_URL_FORM_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:/")

# A Windows drive path is a local filesystem path: the drive letter must not be
# read as a scheme or as an SCP hostname.
_WINDOWS_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")

# ``git remote`` transport-helper syntax, ``<transport>::<address>``, e.g.
# ``git::https://user:secret@host/repo.git``.  The transport name needs two or
# more characters so a Windows drive (``C::``) cannot match, and ``::`` must
# follow the name directly so an IPv6 authority (``https://[::1]/repo``) cannot.
_TRANSPORT_HELPER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]+::(.*)$", re.DOTALL)
_MAX_TRANSPORT_HELPER_DEPTH: int = 4

# SCP-style syntax, ``[user@]host:path``.  ``host`` is anchored to a hostname or a
# bracketed IPv6 literal, and ``path`` must not start with a slash, so a URL-form
# input can never be parsed this way.  Userinfo is matched only to be discarded.
_SCP_RE = re.compile(
    r"^(?:[^@/]*@)?"
    r"(?P<host>[A-Za-z0-9._\-]+|\[[0-9A-Fa-f:.]+\])"
    r":(?P<path>(?!/).*)$",
    re.DOTALL,
)

# Userinfo shape (``user:secret@`` or ``token@``) in the first path segment.  An
# empty or absent authority pushes credentials into the path — as in
# ``https:///user:secret@Org/repo.git`` — where they would otherwise survive.
# Anchoring to the first segment leaves later ``@`` in real paths alone.
_PATH_USERINFO_RE = re.compile(r"^/*[^/@]*@")

# ``urlsplit`` discards tabs and newlines before parsing, so leaving them in the
# text we rebuild from would make the sanitizer disagree with its own parser about
# where the authority ends.  Removing them up front keeps the two in step and stops
# a remote from smuggling a line break into an annotation or a log line.
_URL_CONTROL_CHARS_RE = re.compile(r"[\t\r\n]")


def _has_path_userinfo(path: str) -> bool:
    """Return whether a path carries authentication material.

    Three shapes qualify: userinfo in the leading segment, which is what an
    empty or mangled authority degrades into; a ``user:secret@`` pattern in any
    later segment, which is what a scheme-like prefix leaves behind once its
    colon has been consumed as the SCP host separator; and a credential-bearing
    query or fragment behind an encoded delimiter.  Percent-decoded copies
    are screened too, so an encoded spelling of any shape
    (``user%3Asecret%40host``) is caught in any casing.
    """
    return _screen_reveals(
        path,
        lambda form: _PATH_USERINFO_RE.match(form)
        or _NESTED_USERINFO_RE.search(form)
        or _decoded_delimiter_unsafe(form),
    )


def _sanitize_pathlike(text: str) -> str:
    """Sanitize text that has no authority of its own: a local path or a
    hostless/driver-letter URL.

    These shapes never reach the authority-handling code, so without this they
    would be returned verbatim — yet a caller-supplied remote can still put
    credentials in a query, a fragment, or a userinfo-shaped leading segment
    (``token@host``, ``a/b:secret@c``).  Userinfo fails closed; query and
    fragment go through the same classifiers as a full URL.  Rebuilt by string
    surgery because ``urlunsplit`` cannot round-trip an empty authority.
    """
    from urllib.parse import urlsplit

    base = text.split("#", 1)[0].split("?", 1)[0].strip()
    if _has_path_userinfo(base):
        return _REDACTED_GIT_URL
    base = base.removesuffix(".git")
    try:
        parts = urlsplit(text)
    except ValueError:
        return base
    query = _redact_sensitive_query(parts.query)
    fragment = _redact_sensitive_fragment(parts.fragment)
    return (
        base + (f"?{query}" if query else "") + (f"#{fragment}" if fragment else "")
    ).strip()


def _normalize_git_url(url: str) -> str:
    """Normalize a git remote URL to a browsable, credential-free HTTPS URL.

    Converts SSH/SCP forms to HTTPS and strips the ``.git`` suffix so the
    result can build ``/blob/{ref}/{path}`` links directly:

    - ``git@github.com:Org/repo.git``        -> ``https://github.com/Org/repo``
    - ``ssh://git@github.com/Org/repo.git``  -> ``https://github.com/Org/repo``
    - ``https://github.com/Org/repo.git``    -> ``https://github.com/Org/repo``
    - ``https://github.com/Org/repo``        -> unchanged

    Embedded credentials are removed: ``user:password@`` / ``token@`` userinfo is
    stripped from URL-form and scheme-relative remotes, dropped entirely from
    SCP-style remotes, and credential-bearing query and fragment parameters are
    redacted by name (see :func:`_is_sensitive_query_key`) so secrets do not reach
    persisted annotations, CLI output, logs, or error messages.  Host, port, path,
    and benign document anchors are preserved.

    Parsing fails closed: a URL-like input that cannot be parsed into a clean
    host yields ``_REDACTED_GIT_URL`` rather than leaking the raw
    ``user:secret@`` text or raising.  That covers a malformed authority
    (unterminated IPv6), userinfo with no host, an empty or slash-mangled
    authority that pushes the userinfo into the path
    (``https:///user:secret@Org/repo.git``), and a transport-helper address that
    is not itself a sanitizable URL (``ext::ssh user@host …``).  A malformed
    textual port is dropped while the credential-free host is kept.  Local
    filesystem paths and hostless schemes (e.g. ``file:///path``) are returned
    unchanged aside from ``.git`` stripping and query/fragment redaction.
    Re-normalizing a result is a no-op, except that each pass removes one ``.git``
    suffix, since a repository directory may legitimately be named ``repo.git``.
    """
    from urllib.parse import urlsplit, urlunsplit

    if not url:
        return url

    stripped = _URL_CONTROL_CHARS_RE.sub("", url).strip()

    if _WINDOWS_PATH_RE.match(stripped):
        return _sanitize_pathlike(stripped)

    # Transport-helper remotes wrap a real address: ``git::https://u:s@host/repo``.
    # Unwrap to the inner transport so it can be sanitized structurally.  Only an
    # address that is itself URL-form (or another helper) can be; a helper command
    # such as ``ext::ssh -p 22 user@host %S /repo`` cannot, so it fails closed.
    for _ in range(_MAX_TRANSPORT_HELPER_DEPTH):
        helper = _TRANSPORT_HELPER_RE.match(stripped)
        if helper is None:
            break
        inner = helper.group(1).strip()
        if not _URL_FORM_RE.match(inner) and not _TRANSPORT_HELPER_RE.match(inner):
            return _REDACTED_GIT_URL
        stripped = inner
    else:
        if _TRANSPORT_HELPER_RE.match(stripped):
            return _REDACTED_GIT_URL

    url_like = bool(_URL_FORM_RE.match(stripped)) or stripped.startswith("//")

    # SCP-style syntax: ``[user@]host:path``.  Any userinfo is dropped since it is
    # authentication material, and the remote is rewritten to https so the result
    # is browsable.  A scheme-like prefix with a non-slash hier-part
    # (``https:user:secret@Org/repo``) can still reach this branch, where the
    # scheme would be read as the host and the credentials would land in the path;
    # a userinfo shape in the rewritten path is therefore fatal, not normalized.
    if not url_like:
        scp = _SCP_RE.match(stripped)
        if scp is not None:
            if _has_path_userinfo(scp.group("path")):
                return _REDACTED_GIT_URL
            stripped = f"https://{scp.group('host')}/{scp.group('path')}"
            url_like = True

    try:
        parts = urlsplit(stripped)
        host = parts.hostname
        try:
            port = parts.port
        except ValueError:
            # Malformed textual port (e.g. ``host:notaport``): drop the port but
            # keep the credential-free host so the link stays browsable.
            port = None
    except ValueError:
        # Malformed authority (e.g. unterminated IPv6 ``[::1``).
        return _REDACTED_GIT_URL if url_like else _sanitize_pathlike(stripped)

    if parts.scheme or parts.netloc:
        if host is None:
            # URL-form/scheme-relative with no parseable host.  An empty or
            # malformed authority leaves the credentials in the netloc or in the
            # leading path segment, so returning the raw text would leak them —
            # fail closed on either shape.  Otherwise this is a legitimately
            # hostless scheme (``file:///path``) whose query still needs
            # redacting before the text is handed back.
            if "@" in parts.netloc or _has_path_userinfo(parts.path):
                return _REDACTED_GIT_URL
            return _sanitize_pathlike(stripped)
        # A credential can also sit in the path of an otherwise well-formed URL
        # (``https://host/a:secret@b``), which the authority rewrite below would
        # carry through untouched.  Encoded spellings are screened on decoded
        # copies so ``user%3Asecret%40b`` cannot slip past the literal pattern,
        # and neither can parameters behind an encoded ``?``/``#``.
        if _screen_reveals(
            parts.path,
            lambda form: _NESTED_USERINFO_RE.search(form)
            or _decoded_delimiter_unsafe(form),
        ):
            return _REDACTED_GIT_URL
        # ``urlsplit`` only recognizes a literal ``@`` as the userinfo delimiter,
        # so a percent-encoded userinfo stays inside the hostname
        # (``https://user%3As%40evil/repo``) and would be emitted as the "host"
        # of the rebuilt URL, one decode away from a credential.
        if "%" in host and _screen_reveals(
            host, lambda form: "@" in form or _decoded_delimiter_unsafe(form)
        ):
            return _REDACTED_GIT_URL
        # Re-bracket IPv6 literals, which ``hostname`` returns without brackets.
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        netloc = host if port is None else f"{host}:{port}"
        out_scheme = "https" if parts.scheme.lower() == "ssh" else parts.scheme.lower()
        query = _redact_sensitive_query(parts.query)
        fragment = _redact_sensitive_fragment(parts.fragment)
        # Strip ``.git`` from the path itself so it is removed even when a query
        # or fragment follows it.
        path = parts.path.removesuffix(".git")
        return urlunsplit((out_scheme, netloc, path, query, fragment)).strip()

    # No scheme and no authority: a bare local path.
    return _sanitize_pathlike(stripped)


def _fill_from_ci_env(info: dict[str, str]) -> None:
    """Fill missing git info fields from common CI environment variables.

    The env var lists are defined as module-level constants
    (``_CI_GIT_ROOT_VARS``, ``_CI_SHA_VARS``, ``_CI_BRANCH_VARS``,
    ``_CI_REPO_URL_VARS``) so they can be extended to support new CI systems.
    """
    import os

    if "_git_root" not in info:
        for var in _CI_GIT_ROOT_VARS:
            val = os.environ.get(var)
            if val:
                info["_git_root"] = val
                break

    if "git_remote_sha" not in info:
        for var in _CI_SHA_VARS:
            val = os.environ.get(var)
            if val:
                info["git_remote_sha"] = val
                break

    if "git_remote_branch" not in info:
        for var in _CI_BRANCH_VARS:
            val = os.environ.get(var)
            if val:
                info["git_remote_branch"] = val
                break

    if "git_remote_url" not in info:
        for var in _CI_REPO_URL_VARS:
            val = os.environ.get(var)
            if val:
                # GITHUB_SERVER_URL needs GITHUB_REPOSITORY appended
                if var == "GITHUB_SERVER_URL":
                    repo = os.environ.get("GITHUB_REPOSITORY", "")
                    if repo:
                        val = f"{val}/{repo}"
                    else:
                        continue
                info["git_remote_url"] = val
                break
