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
from collections.abc import Callable, Mapping
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


# Query-string parameter names that carry authentication material.  Exact
# matches for short/ambiguous keys that must not be caught by the substring
# rules below (e.g. ``sig``, ``key``, ``auth``).
_SENSITIVE_QUERY_KEYS: frozenset[str] = frozenset({
    "access_token", "personal_access_token", "private_token", "token",
    "api_key", "apikey", "key", "auth", "authorization",
    "password", "passwd", "pwd", "secret",
    "sig", "signature", "x-access-token",
})

# Fail-closed substrings: any query key containing one of these (case-folded)
# is treated as credential-bearing even when it is not an exact known key.  This
# catches provider-specific and future keys such as ``oauth_token``,
# ``X-Amz-Signature``, ``X-Amz-Credential`` or ``X-Amz-Security-Token`` without
# over-matching benign params like ``ref``/``path`` — which is why bare
# ``key``/``sig``/``auth`` stay exact-match only above.
_SENSITIVE_QUERY_SUBSTRINGS: tuple[str, ...] = (
    "token", "secret", "password", "passwd", "credential",
    "signature", "apikey", "api_key", "oauth", "x-amz-", "x-access",
)


def _is_sensitive_query_key(key: str) -> bool:
    """Return whether a URL query parameter name carries authentication material."""
    folded = key.lower()
    if folded in _SENSITIVE_QUERY_KEYS:
        return True
    return any(token in folded for token in _SENSITIVE_QUERY_SUBSTRINGS)


def _redact_sensitive_query(query: str) -> str:
    """Drop credential-bearing parameters from a URL query string.

    Uses a fail-closed predicate (:func:`_is_sensitive_query_key`) so unknown
    credential-shaped keys are dropped rather than allowed through.  A query
    with no sensitive keys is returned byte-for-byte unchanged so that ordinary
    URLs are not silently re-encoded.
    """
    if not query:
        return query

    from urllib.parse import parse_qsl, urlencode

    pairs = parse_qsl(query, keep_blank_values=True)
    if not any(_is_sensitive_query_key(key) for key, _ in pairs):
        return query
    kept = [(key, value) for key, value in pairs if not _is_sensitive_query_key(key)]
    return urlencode(kept)


# Placeholder emitted when a URL-like remote carries credentials but cannot be
# parsed into a clean host (malformed authority, missing host with userinfo,
# malformed IPv6).  Returning this rather than the raw input keeps credential
# material out of persisted annotations, CLI output, logs, and browse links, and
# stops the parser from raising into callers.
_REDACTED_GIT_URL: str = "[redacted-invalid-git-url]"


def _normalize_git_url(url: str) -> str:
    """Normalize a git remote URL to a browsable, credential-free HTTPS URL.

    Converts SSH/SCP forms to HTTPS and strips the ``.git`` suffix so the
    result can build ``/blob/{ref}/{path}`` links directly:

    - ``git@github.com:Org/repo.git``        -> ``https://github.com/Org/repo``
    - ``ssh://git@github.com/Org/repo.git``  -> ``https://github.com/Org/repo``
    - ``https://github.com/Org/repo.git``    -> ``https://github.com/Org/repo``
    - ``https://github.com/Org/repo``        -> unchanged

    Any embedded credentials are removed: ``user:password@`` / ``token@``
    userinfo is stripped from URL-form and scheme-relative remotes and dropped
    entirely from SCP-style remotes, and sensitive query parameters are redacted.
    This guarantees secrets never reach persisted annotations, CLI output, logs,
    or error messages.  Host, port, path, and fragment are preserved.

    Parsing fails closed: a URL-like input whose credential-bearing authority
    cannot be parsed into a clean host (missing host with userinfo, malformed
    IPv6, and similar) yields ``_REDACTED_GIT_URL`` rather than leaking the raw
    ``user:secret@`` text or raising; a malformed textual port is dropped while
    the credential-free host is kept.  Local filesystem paths and hostless
    schemes (e.g. ``file:///path``) are returned unchanged (aside from ``.git``
    stripping).  The function is idempotent.
    """
    from urllib.parse import urlsplit, urlunsplit

    if not url:
        return url

    stripped = url.strip()

    # SCP-style syntax: ``[user@]host:path`` (no scheme).  We drop any userinfo
    # since it is authentication material, and rewrite to https so the result
    # is browsable.  Guard against Windows drive paths (``C:\...``) and against
    # anything that already carries an explicit scheme.
    if "://" not in stripped and not re.match(r"^[A-Za-z]:[\\/]", stripped):
        scp = re.match(r"^(?:[^@/]+@)?([^/:]+):(?!//)(.+)$", stripped)
        if scp:
            stripped = f"https://{scp.group(1)}/{scp.group(2)}"

    # URL-like inputs (explicit scheme or scheme-relative ``//authority``) must
    # fail closed; a bare local path never leaks userinfo, so it is exempt.
    url_like = "://" in stripped or stripped.startswith("//")

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
        return _REDACTED_GIT_URL if url_like else stripped.removesuffix(".git")

    if parts.scheme or parts.netloc:
        if host is None:
            # URL-form/scheme-relative with no parseable host.  If the authority
            # carried userinfo, returning the raw text would leak it — fail
            # closed.  Otherwise it is a legitimately hostless scheme (file://).
            if "@" in parts.netloc:
                return _REDACTED_GIT_URL
            return stripped.removesuffix(".git")
        # Re-bracket IPv6 literals, which ``hostname`` returns without brackets.
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        netloc = host if port is None else f"{host}:{port}"
        out_scheme = "https" if parts.scheme.lower() == "ssh" else parts.scheme.lower()
        query = _redact_sensitive_query(parts.query)
        # Strip ``.git`` from the path itself so it is removed even when a query
        # or fragment follows it.
        path = parts.path.removesuffix(".git")
        return urlunsplit((out_scheme, netloc, path, query, parts.fragment))

    # No scheme and no authority: a bare local path.
    return stripped.removesuffix(".git")


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
