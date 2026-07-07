"""In-memory compile context for recursive (nested) pipeline compilation.

The compiler must build and validate ALL artifacts (the root pipeline plus
every child subgraph
sidecar) IN MEMORY before writing ANY file, so a late validation, cycle,
or asset-policy failure never leaves a partial bundle on disk.

Types defined here:

* :class:`PipelineCompileKey` — a frozen, hashable identity for a
  ``(source, function, pipeline_name, config, overrides)`` tuple,
  canonicalised per Decision B. Two ``subpipeline(child)`` call sites that
  reach the SAME child collapse to one sidecar because they share a key
  (Decision M dedup); two different children that slug to the same display
  name stay distinct because their ``hash8`` differs.
* :class:`SubgraphArtifact` — one planned, in-memory artifact (root or
  child): its dehydrated ``body`` dict, the ``output_path`` it will be
  written to, an optional ``@task`` ``local_from_python`` components
  sidecar (entries + path), and the child sub-artifacts it references.
* :class:`CompileContext` — the recursion state shared across a single
  ``compile_pipeline`` call: a ``registry`` for dedup, an ``active_stack``
  for cycle detection (Decision L), a ``max_depth`` guard, and the
  ``planned_files`` set consulted by asset-policy validation (Decision J).

This module is intentionally free of compile orchestration logic — it only
holds the shared shapes and the canonicalisation helpers. The recursive
compile driver lives in the CLI's pipeline compiler.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import CompileError


def find_git_root(start: Path) -> Path | None:
    """Return the nearest ancestor directory containing ``.git``, or ``None``.

    Walks ``start`` (or its parent, if ``start`` is a file) upward. Used to
    canonicalise compile-key paths to repo-relative form so in-repo fixture
    sidecar hashes stay stable across machines (Decision B).
    """
    cur = start if start.is_dir() else start.parent
    for d in (cur, *cur.parents):
        if (d / ".git").exists():
            return d
    return None


def canonical_repo_path(p: Path) -> str:
    """Canonical POSIX path for a compile key, per Decision B.

    Resolves symlinks, then returns the repo-relative POSIX path when ``p``
    lives inside a git repo, otherwise the resolved absolute POSIX path.
    Sources outside a git root therefore produce machine-specific keys,
    which is acceptable for v1 local compiles.
    """
    resolved = p.resolve()
    git_root = find_git_root(resolved)
    if git_root is not None:
        try:
            return resolved.relative_to(git_root).as_posix()
        except ValueError:  # pragma: no cover — resolved not under git_root
            pass
    return resolved.as_posix()


def overrides_fingerprint(overrides: Mapping[str, Any] | None, *, context: str | None = None) -> str:
    """Stable SHA-256 fingerprint of compile-time overrides.

    Overrides are sorted by key so the fingerprint is order-independent.
    Empty for the default isolated compile (Decision F), but the field is
    part of the key so the child-override API produces distinct sidecars for
    distinct effective overrides (Decision M case 3). Override values must be
    JSON-serializable scalars/containers (str/int/float/bool/None/list/dict);
    ``json.dumps`` serializes them stably, so a native int and the string of
    that int hash differently (a desirable distinction).

    A non-serializable override value (e.g. a ``_CfgNested`` from
    ``cfg.<dict_key>``, or any arbitrary object) raises a :class:`CompileError`
    naming the offending key — instead of leaking a bare ``TypeError`` past the
    CLI's ``CompileError``-only handling. ``context`` is woven into the message
    so the user can tell WHICH ``.override_config`` / override produced it.

    Args:
        overrides: The effective overrides to fingerprint.
        context: Optional label naming the affected pipeline/child edge,
            appended to the error message for an actionable diagnostic.
    """
    items = dict(sorted((overrides or {}).items()))
    try:
        blob = json.dumps(items, sort_keys=True, separators=(",", ":"))
    except TypeError as e:
        # Identify the first offending key/value for an actionable message.
        offending = next(
            ((k, v) for k, v in items.items() if not _is_json_scalar_or_container(v)),
            None,
        )
        where = f" {context}" if context else ""
        if offending is not None:
            key, value = offending
            raise CompileError(
                f"override value for key {key!r}{where} is not a compile-time "
                f"constant: got {type(value).__name__}. .override_config / "
                "config overrides accept only scalar values (str, int, float, "
                "bool, None) or plain lists/dicts of them — e.g. pass "
                f"`cfg.some_key` (a resolved string), not a nested config "
                "object or an arbitrary Python object."
            ) from e
        raise CompileError(
            f"a config override value{where} is not JSON-serializable "
            f"({e}). .override_config / config overrides accept only scalar "
            "values (str, int, float, bool, None) or plain lists/dicts of them."
        ) from e
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _is_json_scalar_or_container(value: Any) -> bool:
    """Best-effort check that ``value`` is a JSON-serializable native.

    Used only to pinpoint the offending key in :func:`overrides_fingerprint`'s
    error path; the authoritative serializability check is ``json.dumps``
    itself. Recurses into plain lists/tuples and dicts.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, (list, tuple)):
        return all(_is_json_scalar_or_container(v) for v in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _is_json_scalar_or_container(v) for k, v in value.items())
    return False


@dataclass(frozen=True)
class PipelineCompileKey:
    """Frozen, hashable identity for a compiled pipeline artifact.

    Fields are already canonicalised (see :func:`canonical_repo_path` and
    :func:`overrides_fingerprint`) so equality / hashing dedups the same
    child reached through different paths (Decision M) and so :meth:`hash8`
    is stable for in-repo sources.
    """

    source_path: str
    function_qualname: str
    pipeline_name: str
    config_path: str
    overrides_fingerprint: str

    def canonical_fields(self) -> dict[str, str]:
        """The sorted-key JSON object hashed to produce :meth:`hash8`.

        Matches Decision B's canonical field set exactly:
        ``source_path``, ``function_qualname``, ``pipeline_name``,
        ``config_path``, ``overrides``.
        """
        return {
            "config_path": self.config_path,
            "function_qualname": self.function_qualname,
            "overrides": self.overrides_fingerprint,
            "pipeline_name": self.pipeline_name,
            "source_path": self.source_path,
        }

    def hash8(self) -> str:
        """First 8 hex chars of SHA-256 over :meth:`canonical_fields`."""
        blob = json.dumps(self.canonical_fields(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:8]

    def display(self) -> str:
        """Human-readable ``Name (source_path)`` for cycle / error chains."""
        return f"{self.pipeline_name} ({self.source_path})"


@dataclass(frozen=True)
class BroadcastLayer:
    """One layer of config that flows down a flagged ancestor's subtree.

    Layers are pushed onto ``CompileContext.broadcast_stack`` outermost first
    and resolve in TWO precedence tiers (PROPAGATE_CONFIG_DESIGN §4, lines
    128-159), lowest -> highest:

    * **Broadcast tier** (``explicit=False``) — a flagged pipeline's OWN
      ``config.yaml`` (overlaid with any overrides it received), pushed by
      ``_compile_pipeline_fn``. Same-name, lenient, nearest-wins WITHIN the
      tier (inner scope shadows outer).
    * **Explicit tier** (``explicit=True``) — a flagged caller's per-edge
      ``.override_config`` values, pushed by ``_process_subpipeline_children``
      so they flow deep into the child's subtree. Same-name, lenient,
      nearest-wins WITHIN the tier — but the WHOLE explicit tier is applied
      AFTER the whole broadcast tier, so an explicit override set by an
      ancestor ALWAYS outranks a nearer flagged descendant's own-config
      broadcast of the same key, regardless of depth.

    ``config`` is the key/value map this layer contributes.
    """

    config: Mapping[str, Any]
    # ``True`` only for per-edge ``.override_config`` layers (the explicit
    # tier); the flagged-pipeline own-config broadcast layer leaves this
    # ``False``. See the two-tier resolution above.
    explicit: bool = False


@dataclass
class SubgraphArtifact:
    """One planned, in-memory compiled artifact (root pipeline or child).

    Built by ``_compile_pipeline_fn``; validated and written by
    ``compile_pipeline`` only after the WHOLE bundle has been built, so a
    late failure writes nothing (Decision C / J).
    """

    key: PipelineCompileKey
    output_path: Path
    body: dict[str, Any]
    is_root: bool = False
    task_count: int = 0
    # ``@task`` ``local_from_python`` resolver sidecar for THIS artifact.
    # ``components_entries`` is the sidecar's content; ``components_path``
    # is where it will be written (next to ``output_path``). Both ``None``
    # when the artifact uses no ``@task`` components.
    components_entries: dict[str, Any] | None = None
    components_path: Path | None = None
    # Child sub-artifacts this artifact references (for structure only —
    # the authoritative write/dedup list is ``CompileContext.registry``).
    children: list["SubgraphArtifact"] = field(default_factory=list)
    # Cached dumped YAML text, filled during validation so the write pass
    # does not re-dump (and writes the exact bytes that were validated).
    dumped_text: str | None = None
    # JSON paths (dot-delimited, in the form ``iter_template_delimiters``
    # yields) whose template delimiters are legitimate RUNTIME placeholders
    # — every argument wrapped in ``raw(...)`` in THIS artifact's body.
    # The no-template-delimiter output guard skips exactly these paths while
    # still failing on any other delimiter. Empty when the artifact uses no
    # ``raw(...)`` value (the common case). Per-artifact because each body is
    # emitted and validated independently.
    exempt_paths: set[str] = field(default_factory=set)


@dataclass
class CompileContext:
    """Shared recursion state for a single ``compile_pipeline`` call."""

    root_output_path: Path
    subgraph_dir: Path
    root_overrides: dict[str, str] = field(default_factory=dict)
    emit_components_sidecar: bool = True
    max_depth: int = 32
    # Compiled CHILD artifacts keyed by compile key (Decision M dedup).
    # The root is NOT stored here; it is returned directly.
    registry: dict[PipelineCompileKey, SubgraphArtifact] = field(default_factory=dict)
    # Keys on the current recursive compile chain, for cycle detection
    # (Decision L). Pushed before recursing into a child, popped after.
    active_stack: list[PipelineCompileKey] = field(default_factory=list)
    # Active config-broadcast layers from flagged ancestors on the current
    # chain. Pushed (outermost first) by a ``propagate_config=True`` pipeline
    # before compiling its children, popped after. Nearest (last) layer that
    # defines a key wins; lenient same-name overlay onto each descendant.
    broadcast_stack: list[BroadcastLayer] = field(default_factory=list)
    # Resolved paths the compiler WILL write (root output, child sidecars,
    # and any components sidecars). Asset-policy validation treats these as
    # already-existing so refs to not-yet-written sidecars do not fail.
    planned_files: set[Path] = field(default_factory=set)
    # Resolved SOURCE directories the compile imports from (root script dir +
    # each child ``PipelineFn``'s own source dir). The driver purges
    # newly-imported modules under these dirs from ``sys.modules`` after the
    # compile so a subsequent in-process compile of a different bundle does
    # not reuse a stale cached sibling module (the P2 sibling-import leak).
    source_dirs: set[Path] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)
