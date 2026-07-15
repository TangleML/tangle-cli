"""Compile a Python-authored Tangle pipeline to a single YAML file.

Surface::

    tangle sdk pipelines compile <pipeline.py> -o <out.yaml> [--pipeline name] [--override key=value ...]

The compile driver:

1. Loads ``pipeline.py`` as a Python module via ``importlib``.
2. Selects the root :class:`PipelineFn` via ``vars(mod).values()`` — the
   single one defined in the file, or the one named by ``--pipeline`` when
   the file defines several (e.g. a parent + same-file nested children).
3. Loads the cfg from ``<script_dir>/<config_path>`` and overlays
   ``--override key=value`` pairs (CLI wins). ``cfg`` is compile-time
   only — its values are baked into emitted constants, never copied
   into the output YAML.
4. Traces the pipeline and emits the body dict.
5. Writes the single dehydrated pipeline YAML. No wrapper config and no
   ``.yaml.j2`` template sidecar are written. When the pipeline uses
   ``@task``-decorated components, an auxiliary ``<stem>.components.yaml``
   resolver sidecar is also written and each ``@task`` task's
   ``componentRef`` is rewritten to a pure
   ``resolve://./<stem>.components.yaml#<fragment>`` URL.

Exit codes (surfaced by the CLI layer):
- 0 — success.
- 1 — :class:`tangle_cli.python_pipeline.errors.CompileError`.

The generic compile logic lives here as free functions plus the
:class:`PipelineCompiler` command handler (a :class:`TangleCliHandler`
subclass, mirroring :class:`tangle_cli.pipeline_hydrator.PipelineHydrator`).
Distributions that carry a zone concept extend the :data:`ZONE_ROOT_MARKERS`
seam so the compiler can resolve an explicit relative
``@registered(gen_config="rel/path")`` against a zone root.
"""

from __future__ import annotations

import importlib.util
import inspect
import os
import re
import sys
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, get_type_hints

import yaml

from .handler import TangleCliHandler
from .python_pipeline.cfg import Cfg, _coerce_override, load_cfg
from .python_pipeline.compiler_context import (
    BroadcastLayer,
    CompileContext,
    PipelineCompileKey,
    SubgraphArtifact,
    canonical_repo_path,
    overrides_fingerprint,
)
from .python_pipeline.emit import _TASK_URL_PLACEHOLDER, emit_pipeline
from .python_pipeline.errors import CompileError
from .python_pipeline.pipeline import PipelineFn
from .python_pipeline.ref import CallableRef
from .python_pipeline.registered import _REGISTERED_URL_PLACEHOLDER
from .python_pipeline.subpipeline import _SUBPIPELINE_URL_PLACEHOLDER, SubpipelineRef
from .python_pipeline.trace import trace_pipeline
from .python_pipeline.types import In
from .schema_validation import SchemaValidationError, validate_dehydrated_pipeline
from .utils import dump_yaml


@dataclass
class CompileResult:
    """Outcome of a :func:`compile_pipeline` call.

    Attributes:
        pipeline_path: Resolved path of the single emitted pipeline YAML.
        components_path: Path of the ``@task`` ``local_from_python``
            resolver sidecar, or ``None`` when no sidecar was emitted.
        task_count: Number of tasks traced into the ROOT graph.
        warnings: Non-fatal messages collected during compilation.
        subgraph_paths: Paths of the child graph sidecars written under
            ``<stem>.subgraphs/`` for nested ``subpipeline(...)`` calls.
            Empty for single-pipeline / ``@task``-only compiles. Additive
            field — existing callers that ignore it are unaffected.
    """

    pipeline_path: Path
    components_path: Path | None = None
    task_count: int = 0
    warnings: list[str] = field(default_factory=list)
    subgraph_paths: list[Path] = field(default_factory=list)


def compile_pipeline(
    script: Path,
    output: Path,
    overrides: Mapping[str, str] | None = None,
    *,
    pipeline_name: str | None = None,
    emit_components_sidecar: bool = True,
) -> CompileResult:
    """Compile ``script`` to a single pipeline YAML at ``output``.

    Args:
        script: Path to the Python authoring file. The file holds the
            single root ``@pipeline``-decorated function, or several
            (a parent + same-file nested children, or independent
            siblings) one of which is selected by ``pipeline_name``.
        output: Path for the compiled pipeline YAML. The dehydrated
            pipeline body is always written here. When the pipeline uses
            ``@task`` components an additional ``<stem>.components.yaml``
            resolver sidecar is written alongside it. No wrapper config
            and no ``.yaml.j2`` sidecar are ever written.
        overrides: Already-parsed ``key=value`` config overrides. CLI
            wins over file-defined values.
        pipeline_name: When the file defines several pipelines, selects
            which root to compile — matched against the decorated
            function ``__name__`` first, then the ``@pipeline`` display
            name (``--pipeline``). Optional (and ignored unless it must
            disambiguate) when the file defines exactly one pipeline.
            Same-file nested children are reached via the selected root's
            ``subpipeline(...)`` calls, not by this name.
        emit_components_sidecar: When ``True`` (the default), ``@task``
            components are preserved by emitting a sibling
            ``<stem>.components.yaml`` ``local_from_python`` resolver and
            rewriting their ``componentRef`` to a pure ``resolve://`` URL.
            When ``False`` and the pipeline uses ``@task`` components,
            compilation fails with a :class:`CompileError` (a valid
            dehydrated pipeline cannot be produced without the sidecar).
            Pipelines that use only ``ref(url=...)`` are unaffected.

    Returns:
        A :class:`CompileResult`. ``components_path`` is the sidecar path
        when one was written, else ``None``.

    Raises:
        CompileError: for user-facing problems (missing script, no/
            multiple ``@pipeline`` functions, missing/invalid config,
            unreachable ``@task`` source/dependency files).
    """
    overrides = dict(overrides or {})

    # 1. Validate the script path.
    script_path = Path(script).resolve()
    if not script_path.exists():
        raise CompileError(f"pipeline file not found: {script}")

    # The root module is exec'd under a unique synthetic name (restored by
    # ``_load_pipeline_fn``), but its top-level ``from sibling import ...``
    # statements (and trace-time sibling imports inside cycle-style children)
    # register sibling modules under their REAL names; left behind they would
    # let a SUBSEQUENT in-process compile of a DIFFERENT bundle reuse the
    # STALE cached sibling. We purge bundle-local modules AFTER the full
    # compile, so the already-captured child ``PipelineFn`` objects
    # (referenced by the traced functions) stay valid throughout (P2 fix).
    # Collect every source directory the compile imports from (root + each
    # child's own source dir, which may differ from the root's) so the purge
    # also reaches children authored in sibling directories.
    purge_dirs: set[Path] = {script_path.parent.resolve()}

    # Python caches imported modules by NAME (not path), so a PRE-EXISTING
    # ``sys.modules`` entry for a bundle-local sibling (left by a prior
    # in-process compile, a test helper, or a REPL) would shadow this bundle's
    # OWN sibling file. Evict any such shadowing entry up front so the root's
    # ``from sibling import ...`` resolves FRESH from this bundle (P2 fix, the
    # pre-existing-pollution half; the after-compile purge below handles the
    # entries this compile itself adds).
    _evict_shadowed_bundle_modules(script_path.parent)

    try:
        # 2. Load the user pipeline module + select the ROOT PipelineFn. When
        #    the file defines several pipelines, ``pipeline_name``
        #    (``--pipeline``) chooses which to emit. Imported
        #    child pipelines are reachable to ``subpipeline(child)`` but are
        #    never selectable compile targets; same-file nested children are
        #    compiled via the ``subpipeline`` recursion below.
        pipeline_fn = _load_pipeline_fn(script_path, pipeline_name)

        # 3. Resolve the root output path up front. Child subgraph sidecars are
        #    written under ``<stem>.subgraphs/`` next to it, and the relative
        #    ``file://`` / ``resolve://`` URLs that point at the bundle are
        #    derived from these paths.
        output_path = output.resolve() if output.is_absolute() else (Path.cwd() / output).resolve()

        # 4. Build the recursive compile context. ALL artifacts (root + every
        #    nested child sidecar) are traced, emitted, and rewritten IN MEMORY
        #    first; nothing is written until the whole bundle validates.
        #    ``planned_files`` lets asset-policy validation accept
        #    refs to sidecars the compiler is about to write.
        ctx = CompileContext(
            root_output_path=output_path,
            subgraph_dir=output_path.with_name(output_path.stem + ".subgraphs"),
            root_overrides=overrides,
            emit_components_sidecar=emit_components_sidecar,
            source_dirs=purge_dirs,
        )

        # 5. Compile the root (and, recursively, all children) into in-memory
        #    artifacts. The root cfg is resolved relative to the script's own
        #    directory; each child's cfg is resolved relative to ITS OWN source
        #    directory inside ``_compile_pipeline_fn``.
        root_artifact = _compile_pipeline_fn(
            pipeline_fn,
            output_path,
            ctx,
            overrides,
            is_root=True,
            base_dir=script_path.parent,
        )

        # 6. The full bundle: the root plus every deduped child in the registry.
        #    Keying children by compile key means a child reached twice (or via
        #    a diamond) appears exactly once.
        artifacts: list[SubgraphArtifact] = [root_artifact, *ctx.registry.values()]

        # 7. Validate EVERY artifact before writing ANY file. Per artifact:
        #    validate_dehydrated_pipeline (top-level guard + schema +
        #    no-template scan + semantic checks) -> dump -> reload -> validate
        #    again -> asset policy. On any failure nothing is written.
        for artifact in artifacts:
            _validate_artifact(artifact, ctx)

        # 8. Write the whole bundle only after every artifact validated.
        for artifact in artifacts:
            _write_artifact(artifact)

        return CompileResult(
            pipeline_path=root_artifact.output_path,
            components_path=root_artifact.components_path,
            task_count=root_artifact.task_count,
            warnings=ctx.warnings,
            subgraph_paths=[a.output_path for a in artifacts if not a.is_root],
        )
    finally:
        # Purge bundle-local modules so a subsequent in-process compile
        # re-imports them fresh. Runs on success AND on error.
        _purge_bundle_local_modules(purge_dirs)


# ---------------------------------------------------------------------------
# Recursive in-memory compile


@contextmanager
def _temp_sys_path(directory: Path) -> Iterator[None]:
    """Temporarily put ``directory`` on ``sys.path`` (leak-free).

    The root module is exec'd by :func:`_load_pipeline_fn` with its source
    dir on ``sys.path``, but that entry is removed before tracing. Some
    fixtures defer sibling imports to TRACE time (e.g. the cycle fixtures
    ``from cycle_b import ...`` inside the body), so the source dir must be
    importable while the body runs. The entry is restored on exit so
    repeated/concurrent compiles never leak global import state.
    """
    d = str(directory)
    added = d not in sys.path
    if added:
        sys.path.insert(0, d)
    try:
        yield
    finally:
        if added:
            try:
                sys.path.remove(d)
            except ValueError:  # pragma: no cover — defensive
                pass


def _resolve_cfg_for_pipeline(
    pipeline_fn: PipelineFn,
    base_dir: Path,
    overrides: Mapping[str, Any],
    ctx: CompileContext,
    *,
    is_root: bool,
    output_path: Path,
) -> tuple[Cfg, dict[str, Any], Path]:
    """Resolve + load a pipeline's cfg relative to its own source dir.

    Returns ``(cfg, raw_cfg, cfg_path)``. The config file is loaded when the
    function takes a ``cfg`` parameter OR the pipeline is flagged
    ``propagate_config`` (which must read its own config to broadcast it), and
    otherwise skipped — a pipeline with neither tolerates a missing config.yaml.
    A ``config=`` declared without a matching use appends an explanatory
    warning; a root that received ``--override`` values it cannot consume is a
    hard :class:`CompileError`.
    """
    cfg_path = _resolve_cfg_path_in_dir(pipeline_fn, base_dir)
    uses_cfg = _pipeline_accepts_cfg(pipeline_fn)
    should_load_config = uses_cfg or pipeline_fn.propagate_config
    if should_load_config:
        _assert_config_output_path_is_separate(
            cfg_path,
            output_path,
            pipeline_fn=pipeline_fn,
            is_root=is_root,
        )
        loaded_cfg, raw_cfg = _load_cfg_and_raw(
            cfg_path,
            overrides,
            coerce=is_root,
            pipeline_fn=pipeline_fn,
            usage="cfg" if uses_cfg else "propagate_config",
        )
        cfg = loaded_cfg if uses_cfg else Cfg({})
        if not uses_cfg and pipeline_fn.config_path:
            ctx.warnings.append(
                f"pipeline {pipeline_fn.name!r} declares config={pipeline_fn.config_path!r} "
                "but its function has no `cfg` parameter. The config file is still "
                "loaded because propagate_config=True broadcasts it to descendant "
                "subpipelines. Add a `cfg` parameter if this pipeline also needs to "
                "read the config directly."
            )
        return cfg, raw_cfg, cfg_path

    if is_root and overrides:
        keys = sorted(overrides)
        raise CompileError(
            f"pipeline {pipeline_fn.name!r} received --override values {keys!r}, "
            "but its function has no `cfg` parameter and propagate_config is "
            "not enabled. Add a `cfg` parameter to read compile-time config, "
            "enable propagate_config=True to broadcast overrides to descendants, "
            "or remove the unused override(s)."
        )
    if pipeline_fn.config_path:
        ctx.warnings.append(
            f"pipeline {pipeline_fn.name!r} declares config={pipeline_fn.config_path!r} "
            "but its function has no `cfg` parameter, so the config file was "
            "not loaded. Remove `config=` when no compile-time config is needed, "
            "or add a `cfg` parameter to use it."
        )
    return Cfg({}), {}, cfg_path


def _compile_pipeline_fn(
    pipeline_fn: PipelineFn,
    output_path: Path,
    ctx: CompileContext,
    overrides: Mapping[str, Any],
    *,
    is_root: bool,
    base_dir: Path,
    rebroadcast_overrides: Mapping[str, Any] | None = None,
) -> SubgraphArtifact:
    """Trace + emit ``pipeline_fn`` into an in-memory :class:`SubgraphArtifact`.

    Does NOT write any file. The given ``pipeline_fn`` is ALREADY imported
    (the root via :func:`_load_pipeline_fn`; a child via its parent module's
    ``import``), so it is traced directly — the module is never reloaded,
    which sidesteps ``sys.modules`` cache issues with sibling children.

    Steps:

    1. Resolve + load cfg relative to ``base_dir`` (the pipeline's OWN
       source dir — each pipeline loads its own config).
    2. Trace in a fresh ``GraphBuilder`` and emit the dehydrated body.
    3. Build (do NOT write) this artifact's ``@task`` ``local_from_python``
       components sidecar and rewrite its ``@task`` refs to pure
       ``resolve://`` URLs.
    4. Compile any ``subpipeline(child)`` children recursively and
       rewrite the parent task refs to pure ``file://`` URLs.

    Returns the planned artifact; validation + writing happen later in
    :func:`compile_pipeline` once the whole bundle is built.
    """
    # Record this pipeline's source dir so the driver can purge any modules
    # it imports from there after the compile (P2 sibling-import leak fix).
    try:
        ctx.source_dirs.add(base_dir.resolve())
    except OSError:  # pragma: no cover — defensive
        pass

    # Evict any stale modules that would shadow THIS pipeline's bundle-local
    # siblings before tracing, so trace-time sibling imports inside the body
    # (e.g. cycle-style ``from sibling import ...``) resolve fresh from the
    # bundle rather than a cached entry pointing elsewhere (P2 fix).
    _evict_shadowed_bundle_modules(base_dir)

    # 1. cfg is resolved + loaded relative to the pipeline's own source dir.
    #    The ROOT coerces its CLI ``--override`` strings (yaml.safe_load); a
    #    CHILD receives already-typed native ``effective`` overrides (broadcast
    #    + explicit ``.override_config``) that must pass through unchanged.
    cfg, raw_cfg, cfg_path = _resolve_cfg_for_pipeline(
        pipeline_fn,
        base_dir,
        overrides,
        ctx,
        is_root=is_root,
        output_path=output_path,
    )

    # 2. Trace + emit. The source dir is on sys.path so any trace-time
    #    sibling imports inside the body resolve. ``emit_pipeline`` also
    #    returns the set of argument JSON paths wrapped in ``raw(...)`` —
    #    legitimate RUNTIME template placeholders the no-template-delimiter
    #    output guard must skip for THIS artifact's body.
    with _temp_sys_path(base_dir):
        builder = trace_pipeline(pipeline_fn, cfg=cfg, inputs={})
    body_dict, exempt_paths = emit_pipeline(builder)

    # 3. Compute the canonical compile key for dedup / cycle detection.
    key = _compile_key_for(pipeline_fn, cfg_path, overrides)

    # 4. @task local_from_python sidecar for THIS artifact (built, not
    #    written). Each @task ref is rewritten to a pure
    #    ``resolve://./<stem>.components.yaml#<fragment>`` URL BEFORE schema
    #    validation, so the transient ``local-from-python://pending``
    #    placeholder never reaches output.
    components_entries: dict[str, Any] = {}
    components_path: Path | None = None
    task_refs = builder.task_refs_for_local_from_python
    if task_refs and not ctx.emit_components_sidecar:
        task_ids = sorted(tid for tid, _ref in task_refs)
        raise CompileError(
            "pipeline uses @task component(s) that require a "
            "local_from_python resolver sidecar, but "
            "emit_components_sidecar=False. Set it to True (the default) to "
            f"compile @task pipelines. Offending task(s): {task_ids!r}."
        )
    if task_refs:
        components_path = output_path.with_name(output_path.stem + ".components.yaml")
        components_entries = _build_local_from_python_components(task_refs, components_yaml_dir=components_path.parent)
        _rewrite_task_componentref_urls(
            body_dict=body_dict,
            task_refs=task_refs,
            components_yaml_name=components_path.name,
        )

    # 4b. A CHILD artifact is written under ``<root>.subgraphs/``, away from
    #     its own source directory. Its author-written relative local refs
    #     (plain ``ref(url="file://./leaf.yaml")``) point at files next to
    #     the child SOURCE, so relocate them to be relative to the child
    #     SIDECAR directory — the URL still resolves to the SAME original
    #     file (no copying), just from the sidecar's location. Compiler-
    #     managed refs (``@task`` resolver + subpipeline) already point at
    #     bundle files and are skipped. The root is never relocated: its
    #     output dir is its bundle root (in-place compile contract).
    if not is_root:
        _relocate_child_local_refs(
            body_dict=body_dict,
            builder=builder,
            source_dir=base_dir,
            sidecar_dir=output_path.parent,
        )

    # 4c. @registered refs point at an EXISTING gen_config.yaml (the
    #     operation is registered/published elsewhere), so there is no
    #     sidecar to generate. Rewrite each registered task's
    #     ``registered://pending`` sentinel to a pure
    #     ``resolve://<rel-path>/gen_config.yaml#<fragment>`` URL, computed
    #     against THIS artifact's output dir so nested subpipeline children
    #     (written under ``<root>.subgraphs/``) get a correct relative path.
    #     Runs AFTER 4b relocation so it never touches the relocation pass.
    registered_refs = builder.task_refs_for_registered
    if registered_refs:
        _rewrite_registered_componentref_urls(
            body_dict=body_dict,
            registered_refs=registered_refs,
            artifact_output_dir=output_path.parent,
        )

    artifact = SubgraphArtifact(
        key=key,
        output_path=output_path,
        body=body_dict,
        is_root=is_root,
        task_count=len(builder.tasks),
        components_entries=components_entries or None,
        components_path=components_path if components_entries else None,
        exempt_paths=exempt_paths,
    )

    # 5. Register the files this artifact will write so asset-policy
    #    validation accepts refs to them before they exist on disk.
    ctx.planned_files.add(output_path.resolve())
    if artifact.components_path is not None:
        ctx.planned_files.add(artifact.components_path.resolve())

    # 6. Compile every nested subpipeline child into its own graph sidecar
    #    and rewrite this artifact's subpipeline tasks to pure ``file://``
    #    refs. This artifact's key is on the active stack while children are
    #    compiled so a child that reaches back here is detected as a cycle
    #    detected. Subpipeline children appear only when the trace recorded
    #    them; root-only / @task-only compiles skip this entirely.
    #
    #    When ``propagate_config`` is set, push a broadcast layer carrying
    #    THIS pipeline's OWN config — a flagged pipeline broadcasts its own
    #    config, never a value handed to it from above.
    #    The layer is ``raw_cfg`` overlaid with ``rebroadcast``. ``rebroadcast``
    #    is this pipeline's OWN re-broadcastable overrides:
    #      * ROOT (``rebroadcast_overrides is None``) — its CLI ``--override``
    #        values, COERCED with the same ``yaml.safe_load`` coercion
    #        ``load_cfg(coerce=True)`` applied to the root's own cfg, so the
    #        broadcast carries the typed value the root itself sees,
    #        not the raw CLI string.
    #      * flagged CHILD (``rebroadcast_overrides == {}``) — nothing extra:
    #        a flagged child broadcasts ONLY its own ``raw_cfg``. The explicit
    #        ``.override_config`` values set on the edge INTO this child have
    #        their DEPTH governed by the CALLER's flag,
    #        so the caller — not this child — pushes a per-edge layer for them
    #        (see ``_process_subpipeline_children``). This keeps "nearest
    #        flagged ancestor that DEFINES the key wins": an outer ancestor's
    #        value still reaches descendants via the ancestor's OWN layer, which
    #        remains on the stack.
    #    Nearest-wins falls out of stack order: an inner layer is iterated last
    #    in ``_effective_overrides_for_child`` and overwrites outer layers. BOTH
    #    stacks must pop in ``finally`` so a child that raises CompileError
    #    leaves them symmetric.
    if rebroadcast_overrides is None:
        # ROOT: coerce CLI override strings to the YAML type the root's own cfg
        # already carries, so the broadcast layer is type-consistent with cfg.
        rebroadcast: Mapping[str, Any] = {k: _coerce_override(v) for k, v in overrides.items()}
    else:
        rebroadcast = rebroadcast_overrides
    ctx.active_stack.append(key)
    pushed_broadcast = False
    if pipeline_fn.propagate_config:
        ctx.broadcast_stack.append(BroadcastLayer(config={**raw_cfg, **dict(rebroadcast)}))
        pushed_broadcast = True
    try:
        _process_subpipeline_children(artifact, builder, ctx, parent_propagate_config=pipeline_fn.propagate_config)
    finally:
        ctx.active_stack.pop()
        if pushed_broadcast:
            ctx.broadcast_stack.pop()

    return artifact


def _process_subpipeline_children(
    parent_artifact: SubgraphArtifact,
    builder: Any,
    ctx: CompileContext,
    *,
    parent_propagate_config: bool,
) -> None:
    """Compile each ``subpipeline(child)(...)`` recorded during ``builder``'s
    trace and rewrite the parent task's ``componentRef`` to a pure
    ``file://`` URL pointing at the child's graph sidecar.

    For every ``(task_id, SubpipelineRef)`` recorded in
    ``builder.task_refs_for_subpipelines``:

    * compute the child :class:`PipelineCompileKey`;
    * CYCLE check — if the key is already on ``ctx.active_stack`` raise a
      :class:`CompileError` naming the full chain; this also
      covers self-reference (``A -> A``);
    * MAX-DEPTH guard — refuse chains deeper than ``ctx.max_depth``;
    * DEDUP — if the key is already compiled (``ctx.registry``) reuse that
      child's sidecar; otherwise recurse via :func:`_compile_pipeline_fn`
      into ``<root_stem>.subgraphs/<child-slug>-<hash8>.yaml`` (ALL children
      and grandchildren live flat under the ROOT's ``.subgraphs/`` dir);
    * rewrite the parent task's ``componentRef`` to a pure ``file://`` URL
      relative to the REFERENCING artifact's directory (root→child:
      ``./compiled.subgraphs/<child>.yaml``; child→grandchild same dir:
      ``./<gc>.yaml``), replacing the ``subpipeline://pending`` sentinel.

    Writes nothing — it only mutates ``parent_artifact.body`` and populates
    ``ctx.registry`` / ``ctx.planned_files``.
    """
    sub_refs: list[tuple[str, SubpipelineRef]] = builder.task_refs_for_subpipelines
    if not sub_refs:
        return

    parent_tasks = parent_artifact.body.get("implementation", {}).get("graph", {}).get("tasks", {})
    # task_id -> declared child output names, used by the cross-file
    # ``taskOutput`` safety net once every child has been compiled.
    subpipeline_output_names: dict[str, set[str]] = {}
    for task_id, sub_ref in sub_refs:
        child_fn = sub_ref.child
        child_base_dir = _pipeline_base_dir(child_fn, fallback=ctx.subgraph_dir.parent)
        child_cfg_path = _resolve_cfg_path_in_dir(child_fn, child_base_dir)

        # Resolve this child's EFFECTIVE overrides: broadcast from flagged
        # ancestors (lenient, nearest-wins) + the explicit ``.override_config``
        # on this edge (strict, wins). When NEITHER a broadcast layer is
        # active NOR an explicit override was set, skip the raw-cfg read
        # entirely and use ``{}``. The fast path does NOT let a config-taking
        # child without a config.yaml compile — that child still raises
        # "config file not found" in its own ``_compile_pipeline_fn``. What it
        # buys is: one fewer raw-cfg read, and exact preservation of the
        # default per-pipeline config isolation (empty effective overrides -> empty
        # ``overrides_fingerprint`` -> the SAME dedup key the child gets with no
        # feature in play) whenever there is nothing to broadcast or override.
        # Ambient PASS-THROUGH context: the resolved
        # broadcast/override keys this child does NOT declare and therefore
        # flow PAST it, unchanged, to its descendants. It is folded into the
        # child's compile key so the SAME child reached under two different
        # ancestor-broadcast contexts gets distinct keys (and distinct
        # sidecars) whenever the difference can affect its descendants — even
        # when the child's OWN effective overrides are identical.
        ambient_passthrough: dict[str, Any] = {}
        if ctx.broadcast_stack or sub_ref.config_overrides:
            # A config-less child (no ``cfg`` param, no ``config=``) has no
            # ``config.yaml`` on disk. Under an active broadcast (or an
            # explicit ``.override_config``) we still consult the child's
            # declared keys to drive the same-name overlay and the ambient
            # pass-through — but a MISSING file means the child simply declares
            # nothing to overlay, so treat it as ``{}`` and let every broadcast
            # key flow PAST it to descendants. Reading it strictly here would
            # hard-fail a config-less intermediate purely because an ancestor
            # broadcasts. A config-DECLARING child whose file is genuinely
            # missing is still caught, with guidance, later in
            # ``_load_cfg_and_raw`` (via the child's own ``_compile_pipeline_fn``).
            child_raw = _read_raw_cfg(child_cfg_path) if child_cfg_path.exists() else {}
            effective = _effective_overrides_for_child(
                child_raw=child_raw,
                broadcast_stack=ctx.broadcast_stack,
                explicit=sub_ref.config_overrides,
                child_name=child_fn.name,
                parent_task_id=task_id,
            )
            # Resolve the ambient context from the stack AS IT STANDS NOW (the
            # per-edge override INTO this child is not pushed yet — correct,
            # it is already in this child's ``effective``). The pass-through is
            # exactly the resolved keys the child does not declare.
            if ctx.broadcast_stack:
                resolved_ambient = _resolve_ambient_context(ctx.broadcast_stack)
                ambient_passthrough = {k: v for k, v in resolved_ambient.items() if k not in child_raw}
        else:
            effective = {}

        child_key = _compile_key_for(
            child_fn,
            child_cfg_path,
            effective,
            fingerprint_context=f"on subpipeline task {task_id!r} -> child pipeline {child_fn.name!r}",
            ambient_context=ambient_passthrough or None,
        )

        # Cycle detection (covers self-reference). active_stack holds the
        # chain from the root down to (and including) the artifact whose
        # children we are compiling.
        if child_key in ctx.active_stack:
            chain = [*ctx.active_stack, child_key]
            chain_str = " -> ".join(k.display() for k in chain)
            raise CompileError(
                f"nested pipeline cycle detected: {chain_str}. Recursive "
                "Python pipelines are not supported; break the cycle or use a "
                "published component boundary."
            )

        child_artifact = ctx.registry.get(child_key)
        if child_artifact is None:
            # Max-depth guard: the chain TO the child would be one deeper
            # than the current stack.
            if len(ctx.active_stack) + 1 > ctx.max_depth:
                chain = [*ctx.active_stack, child_key]
                chain_str = " -> ".join(k.display() for k in chain)
                raise CompileError(
                    f"nested pipeline max depth {ctx.max_depth} exceeded: "
                    f"{chain_str}. Reduce nesting depth or restructure the "
                    "pipeline graph."
                )
            slug = _slugify(child_fn.name)
            child_output_path = ctx.subgraph_dir / f"{slug}-{child_key.hash8()}.yaml"
            # Per-edge explicit-override depth: an
            # explicit ``.override_config`` set on THIS edge flows deep iff the
            # CALLER (this parent) is flagged. When it is, push a broadcast
            # layer carrying JUST this edge's explicit overrides as the INNERMOST
            # (nearest) layer while the child's subtree compiles, then pop it.
            # This makes the explicit override (a) win over the parent's own
            # broadcast for the child's DESCENDANTS (explicit always wins, and
            # nearest-wins puts it last), and (b) reach descendants that declare
            # the key (lenient). The child ITSELF already got the explicit
            # override strictly via ``effective``; this per-edge layer is pushed
            # AROUND the recursion only, so it affects the child's DESCENDANTS,
            # not the child's own ``effective`` / ``child_key`` (computed above).
            # The flagged-child re-broadcast passes ``{}`` — a flagged child
            # broadcasts only its OWN config; this edge's explicit override
            # depth belongs to the caller and is handled by THIS layer.
            pushed_edge_override = False
            if parent_propagate_config and sub_ref.config_overrides:
                # ``explicit=True`` puts this layer in the explicit tier, which
                # is resolved AFTER the whole broadcast tier in
                # ``_effective_overrides_for_child`` — so this edge's override
                # outranks even a NEARER flagged descendant's own-config
                # broadcast of the same key.
                ctx.broadcast_stack.append(BroadcastLayer(config=dict(sub_ref.config_overrides), explicit=True))
                pushed_edge_override = True
            try:
                child_artifact = _compile_pipeline_fn(
                    child_fn,
                    child_output_path,
                    ctx,
                    effective,
                    is_root=False,
                    base_dir=child_base_dir,
                    rebroadcast_overrides={},
                )
            finally:
                if pushed_edge_override:
                    ctx.broadcast_stack.pop()
            ctx.registry[child_key] = child_artifact
            parent_artifact.children.append(child_artifact)
        else:
            # Reused (dedup / diamond) — still a child of this parent for
            # structural completeness, but compiled only once.
            if child_artifact not in parent_artifact.children:
                parent_artifact.children.append(child_artifact)

        # Rewrite this task's componentRef to a pure file:// URL relative to
        # the REFERENCING artifact's directory.
        rel = _relpath_posix(child_artifact.output_path, parent_artifact.output_path.parent)
        if task_id not in parent_tasks:
            raise CompileError(
                f"subpipeline ref recorded for task {task_id!r} but no such "
                "task in the emitted body (internal error)."
            )
        parent_tasks[task_id]["componentRef"] = {"url": f"file://{rel}"}

        # INPUT interface validation. The compiled child body
        # is the source of truth for declared inputs; ``wait_for`` /
        # ``depends_on`` are NOT special — they must be declared child
        # In[...] inputs if passed.
        _validate_subpipeline_inputs(
            parent_task_id=task_id,
            parent_args=parent_tasks[task_id].get("arguments", {}),
            child_artifact=child_artifact,
        )
        subpipeline_output_names[task_id] = _child_output_names(child_artifact.body)

    # OUTPUT cross-file validation — a safety net beyond the
    # strict SubpipelineOutputProxy: every ``taskOutput`` in the parent body
    # that targets a subpipeline task must name an output the child declares.
    _validate_subpipeline_output_refs(parent_artifact.body, subpipeline_output_names)


def _child_input_specs(child_body: dict[str, Any]) -> tuple[list[str], set[str]]:
    """Return ``(declared_input_names, required_input_names)`` from a child's
    compiled body ``inputs`` block.

    An input is REQUIRED unless it is marked ``optional`` (the tracer sets
    ``optional: true`` for any ``In[T]`` parameter that has a default).
    Declared names preserve declaration order for stable error messages.
    """
    inputs = child_body.get("inputs", []) or []
    names: list[str] = []
    required: set[str] = set()
    for spec in inputs:
        if not isinstance(spec, dict):
            continue
        name = spec.get("name")
        if name is None:
            continue
        names.append(name)
        if not spec.get("optional"):
            required.add(name)
    return names, required


def _child_output_names(child_body: dict[str, Any]) -> set[str]:
    """Return the set of output names declared by a child's compiled body."""
    outs = child_body.get("outputs", []) or []
    return {name for o in outs if isinstance(o, dict) and isinstance(name := o.get("name"), str)}


def _validate_subpipeline_inputs(
    *,
    parent_task_id: str,
    parent_args: dict[str, Any],
    child_artifact: SubgraphArtifact,
) -> None:
    """Validate a subpipeline call's arguments against the child interface.

    Rejects an UNKNOWN argument name (not a declared child input) and a
    MISSING REQUIRED child input (declared, non-optional, not supplied).
    Omitted optional/default child inputs are allowed. Raises a clear
    :class:`CompileError` BEFORE any file is written.
    """
    declared, required = _child_input_specs(child_artifact.body)
    child_name = child_artifact.key.pipeline_name
    arg_names = set(parent_args.keys())

    unknown = sorted(arg_names - set(declared))
    if unknown:
        raise CompileError(
            f"subpipeline task {parent_task_id!r} passes unknown input "
            f"{unknown[0]!r} to child pipeline {child_name!r}. Declared child "
            f"inputs: {declared}."
        )

    missing = sorted(required - arg_names)
    if missing:
        raise CompileError(
            f"subpipeline task {parent_task_id!r} calls child pipeline "
            f"{child_name!r} without required input {missing[0]!r}. Pass "
            f"{missing[0]}=... or give the child In[...] parameter a default."
        )


def _validate_subpipeline_output_refs(
    body: dict[str, Any],
    subpipeline_output_names: dict[str, set[str]],
) -> None:
    """Assert every ``taskOutput`` targeting a subpipeline task names a
    declared child output.

    Scans both task ``arguments`` and graph ``outputValues``. This is the
    compiler-owned cross-file safety net that protects the serialized YAML
    even if a proxy bug let an undeclared output slip through.
    """
    if not subpipeline_output_names:
        return
    graph = body.get("implementation", {}).get("graph", {})
    tasks = graph.get("tasks", {}) if isinstance(graph, dict) else {}

    def _check(value: Any, loc: str) -> None:
        if not isinstance(value, dict):
            return
        task_output = value.get("taskOutput")
        if not isinstance(task_output, dict):
            return
        target = task_output.get("taskId")
        if target not in subpipeline_output_names:
            return
        out_name = task_output.get("outputName")
        declared = subpipeline_output_names[target]
        if out_name not in declared:
            raise CompileError(
                f"{loc} references output {out_name!r} of subpipeline task "
                f"{target!r}, but that child pipeline declares only "
                f"{sorted(declared)}."
            )

    if isinstance(tasks, dict):
        for task_id, task in tasks.items():
            if not isinstance(task, dict):
                continue
            arguments = task.get("arguments")
            if isinstance(arguments, dict):
                for arg_name, value in arguments.items():
                    _check(value, f"task {task_id!r} argument {arg_name!r}")
    output_values = graph.get("outputValues") if isinstance(graph, dict) else None
    if isinstance(output_values, dict):
        for out_key, value in output_values.items():
            _check(value, f"outputValues key {out_key!r}")


def _pipeline_base_dir(pipeline_fn: PipelineFn, *, fallback: Path) -> Path:
    """Resolve the source directory a child pipeline's cfg + sibling imports
    are relative to.

    Prefers the ``@pipeline`` decorator's captured ``caller_dir`` (the
    child's own source file directory), then the decorated
    function's source file, then ``fallback`` (the root bundle directory)
    for dynamically built pipelines with no on-disk source.
    """
    if pipeline_fn.caller_dir is not None:
        return pipeline_fn.caller_dir
    src = _pipeline_source_path(pipeline_fn)
    if src is not None:
        return src.parent
    return fallback


def _slugify(name: str) -> str:
    """Slugify a pipeline display name for a child sidecar filename.

    Lowercases, replaces any run of non-alphanumeric characters with a
    single hyphen, and trims leading/trailing hyphens (``"Judge Options"``
    -> ``"judge-options"``). Collisions between two children that slug to
    the same name are disambiguated by the ``hash8`` suffix.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "pipeline"


def _compile_key_for(
    pipeline_fn: PipelineFn,
    cfg_path: Path,
    overrides: Mapping[str, Any],
    *,
    fingerprint_context: str | None = None,
    ambient_context: Mapping[str, Any] | None = None,
) -> PipelineCompileKey:
    """Build the canonical :class:`PipelineCompileKey` for ``pipeline_fn``.

    Paths are canonicalised to repo-relative POSIX inside a git
    repo, else resolved absolute POSIX. A dynamically built pipeline whose
    source cannot be located falls back to a qualname-derived sentinel so it
    still produces a stable, distinct key.

    ``fingerprint_context`` is a human label naming the affected pipeline /
    child edge; it is woven into the :func:`overrides_fingerprint` error if an
    override value is not a compile-time constant, so a non-serializable
    ``.override_config`` value surfaces as an actionable :class:`CompileError`
    rather than a bare ``TypeError``.

    ``ambient_context`` is the AMBIENT PASS-THROUGH context: the
    active broadcast/override keys this node does NOT declare and therefore
    flow PAST it to its descendants. Folding it into the fingerprint keeps the
    same node distinct when it is reached under two different ancestor-broadcast
    contexts that its descendants would resolve differently.

    Fingerprint encoding (collision-proof, byte-stable):

    * **Empty / ``None`` ambient** (always true for the default-isolation
      compile) -> ``overrides_fingerprint(overrides)`` UNCHANGED, byte-for-byte
      identical to the fingerprint with no ambient envelope.
    * **Non-empty ambient** -> a small envelope keyed by reserved sentinels
      (``"\\x00effective"`` / ``"\\x00ambient"``) that cannot collide with real
      config keys, routed through :func:`overrides_fingerprint` so its
      non-serializable-value -> :class:`CompileError` detection (which recurses
      into dicts) still fires for BOTH effective and ambient values.
    """
    src = _pipeline_source_path(pipeline_fn)
    if src is not None:
        source_canon = canonical_repo_path(src)
    else:
        source_canon = f"<dynamic:{pipeline_fn.fn.__qualname__}>"
    if ambient_context:
        # Reserved keys (NUL-prefixed) can never be real config keys, so an
        # envelope never collides with an effective-only fingerprint over the
        # same keys (e.g. effective={x:1},ambient={} vs effective={x:1},ambient={y:2}).
        fingerprint_input: Mapping[str, Any] = {
            "\x00effective": dict(sorted(overrides.items())),
            "\x00ambient": dict(sorted(ambient_context.items())),
        }
    else:
        # Byte-identical to today's key: the default-isolation guarantee.
        fingerprint_input = overrides
    return PipelineCompileKey(
        source_path=source_canon,
        function_qualname=pipeline_fn.fn.__qualname__,
        pipeline_name=pipeline_fn.name,
        config_path=canonical_repo_path(cfg_path),
        overrides_fingerprint=overrides_fingerprint(fingerprint_input, context=fingerprint_context),
    )


def _validate_artifact(artifact: SubgraphArtifact, ctx: CompileContext) -> None:
    """Validate one planned artifact in memory (no writes).

    Runs a leftover-sentinel scan (a missed @task / subpipeline rewrite),
    ``validate_dehydrated_pipeline`` on the body, a dump/reload
    re-validation (to catch dumper issues), and the relative-local-ref asset
    policy. The dumped text is cached on the artifact so the write pass
    emits exactly the bytes that were validated.
    """
    body = artifact.body
    label = None if artifact.is_root else _artifact_label(artifact)

    # No pending compiler sentinel may survive into a written artifact. A
    # surviving sentinel means a missed @task or subpipeline rewrite — fail
    # with a targeted internal error BEFORE schema validation so the cause
    # is obvious, and write nothing.
    _assert_no_pending_sentinels(body, label)

    # ``raw(...)`` argument paths whose template delimiters are legitimate
    # RUNTIME placeholders the no-template-delimiter output guard must skip
    # for THIS artifact's body. Empty unless the artifact used ``raw(...)``.
    exempt_paths = artifact.exempt_paths

    try:
        validate_dehydrated_pipeline(body, exempt_paths)
    except SchemaValidationError as e:
        if label is None:
            raise CompileError(str(e)) from e
        raise CompileError(f"{label}: {e}") from e

    dumped = dump_yaml(body, sort_keys=False)
    reloaded = yaml.safe_load(dumped)
    try:
        validate_dehydrated_pipeline(reloaded, exempt_paths)
    except SchemaValidationError as e:
        prefix = "" if label is None else f"{label}: "
        raise CompileError(f"{prefix}compiled YAML failed re-validation after dump/reload: {e}") from e

    # Asset policy. EVERY
    # artifact's relative local refs are validated relative to THAT
    # artifact's own output directory:
    #   * the ROOT body relative to the root output dir (``label`` is None,
    #     preserving the verbatim single-pipeline error message);
    #   * each CHILD body relative to ITS child-sidecar dir (``label`` adds
    #     child-sidecar + task context to the error).
    # Generated bundle files the compiler is about to write — child graph
    # sidecars and child ``@task`` components sidecars — are in
    # ``ctx.planned_files`` and count as present, so the compiler-managed
    # parent→child / child→child / child @task refs pass. A child's
    # author-written relative leaf ref was relocated to be relative to
    # the child-sidecar dir, so it is validated against the real source-side
    # file via ``../``; a missing external leaf fails clearly here, before
    # any file is written.
    _validate_local_component_refs_for_artifact(
        body,
        artifact.output_path.parent,
        ctx.planned_files,
        artifact_label=label,
    )
    artifact.dumped_text = dumped


# Pending componentRef sentinels stamped during trace/emit. All MUST be
# rewritten before validation; a survivor is an internal compiler bug.
_PENDING_SENTINELS = (
    _SUBPIPELINE_URL_PLACEHOLDER,
    _TASK_URL_PLACEHOLDER,
    _REGISTERED_URL_PLACEHOLDER,
)


def _assert_no_pending_sentinels(body_dict: dict[str, Any], artifact_label: str | None) -> None:
    """Raise if any task ``componentRef.url`` still holds a pending compiler
    sentinel (``subpipeline://pending`` / ``local-from-python://pending`` /
    ``registered://pending``).

    These are stamped during trace/emit and rewritten to pure refs by the
    compile driver; a survivor means a rewrite was missed. Fail clearly with
    task context so the bug is obvious, and (because this runs before the
    write pass) leave nothing on disk.
    """
    where = artifact_label or "root pipeline"
    tasks = body_dict.get("implementation", {}).get("graph", {}).get("tasks", {})
    for task_id, task in tasks.items():
        if not isinstance(task, dict):
            continue
        cref = task.get("componentRef")
        if not isinstance(cref, dict):
            continue
        url = cref.get("url")
        if isinstance(url, str) and url in _PENDING_SENTINELS:
            raise CompileError(
                f"internal error: {where} task {task_id!r} still carries the "
                f"unresolved compiler sentinel {url!r} in its componentRef "
                "(a missed @task / subpipeline rewrite). No output was written."
            )


def _write_artifact(artifact: SubgraphArtifact) -> None:
    """Write a validated artifact (body YAML + optional @task sidecar).

    Only called after the WHOLE bundle has validated, so no partial bundle
    is ever left on disk.
    """
    assert artifact.dumped_text is not None  # set by _validate_artifact
    artifact.output_path.parent.mkdir(parents=True, exist_ok=True)
    artifact.output_path.write_text(artifact.dumped_text)
    if artifact.components_entries:
        assert artifact.components_path is not None  # narrow for type-checkers
        artifact.components_path.write_text(dump_yaml(artifact.components_entries, sort_keys=False))


def _artifact_label(artifact: SubgraphArtifact) -> str:
    """Short ``child pipeline '<name>' (<sidecar filename>)`` label for
    error messages on a non-root artifact."""
    return f"child pipeline {artifact.key.pipeline_name!r} " f"({artifact.output_path.name})"


# ---------------------------------------------------------------------------
# @task local_from_python sidecar helpers
#
# These build the
# ``<stem>.components.yaml`` resolver config for @task-derived refs and
# rewrite each task's componentRef to a pure ``resolve://`` URL. Paths are
# POSIX and relative to the sidecar directory so the compiled bundle is
# portable (the sidecar and the files it points at can move together).


def _fragment_for_task(ref: CallableRef) -> str:
    """Stable fragment key for a @task ref in the components sidecar.

    Uses the hyphenated function name, matching the ``local_from_python``
    resolver's output-filename convention
    (``my_task`` -> ``my-task``). Multiple call sites for the SAME
    function share one fragment — the local_from_python entry is keyed by
    source file, not by call site.
    """
    assert ref._task_function_name is not None  # only @task refs reach here
    return ref._task_function_name.replace("_", "-")


def _relpath_posix(target: Path, start: Path) -> str:
    """``os.path.relpath`` but always POSIX-style for YAML stability.

    Raises:
        CompileError: when no relative path can be formed (e.g. ``target``
            and ``start`` are on different drives on Windows).
    """
    try:
        rel = os.path.relpath(str(target), str(start))
    except ValueError as e:
        raise CompileError(
            f"cannot form a relative path from {start} to {target}: {e}. "
            "Compile into the pipeline source directory, or reference the "
            "component with an absolute file://, gs://, or resolve:// URL."
        ) from e
    # Normalise to POSIX separators so the sidecar diffs are stable.
    rel = rel.replace(os.sep, "/")
    # Prefix bare relative paths with ``./`` to match the
    # local_from_python convention.
    if not rel.startswith(".") and not rel.startswith("/"):
        rel = "./" + rel
    return rel


# ----------------------------------------------------------------------------
# @registered URL rewrite. Unlike @task (which builds a sidecar), @registered
# references an EXISTING gen_config.yaml: the driver only resolves that config
# and rewrites each registered task's ``registered://pending`` sentinel to a
# pure ``resolve://<rel-path>/gen_config.yaml#<fragment>`` URL. Resolution
# touches the filesystem (marker walk, nearest-gen_config walk, relpath) and so
# happens lazily here at compile time, never at decoration/import time.


def _fragment_for_registered(ref: CallableRef) -> str:
    """Fragment key for a @registered ref's ``resolve://`` URL.

    Per decision 2, the author-supplied ``fragment`` is used VERBATIM as the
    top-level key in ``gen_config.yaml``. When omitted, it defaults to the
    function name VERBATIM (no hyphenation, unlike :func:`_fragment_for_task`)
    — gen_config fragments are hand-authored keys, not generated filenames, so
    we must not mangle them.
    """
    fragment = ref._registered_fragment or ref._registered_function_name
    assert fragment is not None  # only @registered refs reach here
    return fragment


# Zone-root marker filenames — the extension seam for distributions that
# carry a zone concept. An explicit relative ``@registered(gen_config=...)``
# path is resolved against the nearest ancestor directory that contains one
# of these marker files (see :func:`_find_zone_root`).
#
# EMPTY by default: the open-source ``tangle`` CLI has no zone concept, so an
# explicit relative gen_config path is unsupported until a downstream
# distribution registers a marker. A distribution that DOES carry a zone
# concept appends its own marker filename at import time to restore zone-root
# resolution. This mirrors the mutable module-level registry seams
# elsewhere in the CLI (e.g. the hydrator's ``COMPONENT_RESOLVERS``): mutate
# the list in place rather than rebinding it, so callers that already imported
# the name observe the addition.
ZONE_ROOT_MARKERS: list[str] = []


def _find_zone_root(source: Path) -> Path | None:
    """Return the nearest ancestor dir of ``source`` holding a zone-root
    marker (see :data:`ZONE_ROOT_MARKERS`), or ``None`` if none exists.

    An explicit ``@registered(gen_config="rel/path")`` is resolved relative to
    this directory (decision 1) so authors write the gen_config path the same
    way the distribution's existing ``resolve://`` references do. When no
    markers are registered (the default open-source build) this always returns
    ``None`` and explicit relative gen_config paths are rejected with an
    actionable error.
    """
    if not ZONE_ROOT_MARKERS:
        return None
    for ancestor in source.parents:
        if any((ancestor / marker).is_file() for marker in ZONE_ROOT_MARKERS):
            return ancestor
    return None


def _find_nearest_gen_config(source: Path) -> Path | None:
    """Return the nearest ancestor ``gen_config.yaml`` of ``source``, or
    ``None`` if none exists.

    Used as the default when ``@registered`` is given no explicit
    ``gen_config=`` (decision 1): the operation file is assumed to live under
    (or beside) the gen_config.yaml it is registered in.
    """
    for ancestor in source.parents:
        candidate = ancestor / "gen_config.yaml"
        if candidate.is_file():
            return candidate
    return None


def _resolve_registered_gen_config(ref: CallableRef) -> Path | str:
    """Resolve a @registered ref's gen_config to a value :func:`_rewrite_registered_componentref_urls` emits.

    Return contract (this is what ``_rewrite`` keys off of):

    - a returned ``str`` is emitted VERBATIM after the ``resolve://`` scheme,
      with NO relpath. Two cases produce a ``str``:
        * a genuinely remote scheme (``gs://`` / ``http://`` / ``https://``) —
          the hydrator fetches it directly and it cannot be existence-checked
          at compile time;
        * a validated absolute LOCAL path — the resolved, on-disk-checked
          ``os.fspath(...)`` of a ``file://`` URL or an absolute filesystem
          path. Emitted as ``resolve:///abs/x.yaml#frag``, which the hydrator
          resolves via ``Path("/abs/x.yaml")``.
    - a returned ``Path`` is relpath'd against the artifact output dir by
      ``_rewrite`` (the relative/marker and omitted/nearest cases).

    Resolution rules (decision 1):

    - ``gs://`` / ``http(s)://`` -> returned VERBATIM as a ``str``.
    - ``file://...`` -> the ``file://`` prefix is STRIPPED and the remainder
      treated as a LOCAL path. WHY strip: the hydrator's ``resolve://`` parser
      does NOT understand a nested ``file://`` (it does ``Path(file_path)``
      with no scheme stripping and special-cases ``gs://`` only), so a
      ``file://``-wrapped payload like ``resolve://file:///abs/x.yaml`` fails
      to hydrate — ``Path("file:///abs/x.yaml")`` is mis-parsed as a relative
      segment. Stripping at compile time makes documented ``file://`` usage
      actually resolvable. A non-absolute remainder is resolved against the op
      source file's directory. The path is then ``.resolve()``-d,
      existence-checked, and returned as ``os.fspath(...)`` (a ``str``).
    - an absolute filesystem path (no scheme) -> ``.resolve()``-d,
      existence-checked, returned as ``os.fspath(...)`` (a ``str``).
    - explicit relative ``gen_config=`` -> resolved against the zone root
      (the nearest ancestor holding a registered :data:`ZONE_ROOT_MARKERS`
      marker), existence-checked, returned as a ``Path``.
    - omitted ``gen_config`` -> the nearest ancestor ``gen_config.yaml``,
      returned as a ``Path``.

    Raises:
        CompileError: when an explicit relative path has no zone-root marker
            above the source; when an omitted path finds no ancestor
            gen_config.yaml; when an explicit relative path resolves to a
            non-existent file; or when a ``file://`` URL / absolute path
            resolves to a path that does not exist on disk.
    """
    raw = ref._registered_gen_config
    source = ref._registered_source_path
    assert source is not None  # only @registered refs reach here

    # Genuinely remote schemes -> used verbatim; the hydrator fetches them
    # directly and they can't be existence-checked at compile time.
    if raw is not None and (raw.startswith("gs://") or raw.startswith("http://") or raw.startswith("https://")):
        return raw

    # file:// URL or absolute filesystem path -> a validated absolute LOCAL
    # path, emitted verbatim (no relpath). The file:// scheme is stripped here
    # because the hydrator's resolve:// parser doesn't understand it.
    if raw is not None and (raw.startswith("file://") or os.path.isabs(raw)):
        if raw.startswith("file://"):
            # Mirror the hydrator's own file:// stripping
            # (pipeline_hydrator.py: ``url[len("file://"):]``).
            local = raw[len("file://") :]
            local_path = Path(local)
            # A non-absolute remainder (e.g. ``file://rel/x.yaml``) is resolved
            # against the op source file's directory.
            if not local_path.is_absolute():
                local_path = source.parent / local_path
        else:
            local_path = Path(raw)
        resolved = local_path.resolve()
        if not resolved.exists():
            raise CompileError(
                f"@registered(gen_config={raw!r}) resolves to {resolved}, which does not " "exist on disk."
            )
        return os.fspath(resolved)

    if raw is not None:
        # Explicit relative path -> marker-relative (zone root).
        root = _find_zone_root(source)
        if root is None:
            if ZONE_ROOT_MARKERS:
                markers = " / ".join(repr(m) for m in ZONE_ROOT_MARKERS)
                raise CompileError(
                    f"@registered(gen_config={raw!r}) is a relative path but no "
                    f"zone-root marker ({markers}) was found in any ancestor "
                    f"directory of {source}. Add the marker at the zone root, or "
                    "pass an absolute / gs:// gen_config path instead."
                )
            raise CompileError(
                f"@registered(gen_config={raw!r}) is a relative path, but this "
                "build has no zone-root markers registered, so a zone root "
                f"cannot be located for {source}. Pass an absolute / file:// / "
                "gs:// gen_config path instead, or omit gen_config to use the "
                "nearest ancestor 'gen_config.yaml'."
            )
        resolved = (root / raw).resolve()
    else:
        # Omitted -> nearest ancestor gen_config.yaml.
        nearest = _find_nearest_gen_config(source)
        if nearest is None:
            raise CompileError(
                "@registered could not find a 'gen_config.yaml' in any ancestor "
                f"directory of {source}. Pass gen_config=... explicitly (relative "
                "to the zone-root marker, or an absolute / gs:// path)."
            )
        resolved = nearest

    if not resolved.exists():
        raise CompileError(
            f"@registered gen_config not found on disk: {resolved}. Check the "
            "path and the zone-root marker location."
        )
    return resolved


def _rewrite_registered_componentref_urls(
    *,
    body_dict: dict[str, Any],
    registered_refs: list[tuple[str, CallableRef]],
    artifact_output_dir: Path,
) -> None:
    """Rewrite each @registered task's ``componentRef`` to a pure resolve URL.

    Mutates ``body_dict`` in place. Each entry in ``registered_refs`` is a
    ``(task_id, CallableRef)`` tuple; the task_id is the key under
    ``implementation.graph.tasks`` whose componentRef is replaced with
    ``{"url": "resolve://<rel-path>/gen_config.yaml#<fragment>"}``.

    The relative path is computed against ``artifact_output_dir`` (this
    artifact's own output directory) so nested subpipeline children, written
    under ``<root>.subgraphs/``, get a correct relative path. The emission
    form depends on what :func:`_resolve_registered_gen_config` returns:

    - ``gs://`` / ``http(s)://`` -> emitted verbatim after ``resolve://``
      (genuinely remote, no relpath).
    - a ``file://`` URL or an absolute path -> resolved to a validated
      absolute LOCAL path (existence-checked at compile time) and emitted as
      an absolute ``resolve://`` URL — the ``file://`` scheme is stripped at
      resolve time so the hydrator's ``resolve://`` parser can read it.
    - relative / marker / omitted -> relpath'd against ``artifact_output_dir``.
    """
    tasks = body_dict.get("implementation", {}).get("graph", {}).get("tasks", {})
    for task_id, ref in registered_refs:
        target = _resolve_registered_gen_config(ref)
        fragment = _fragment_for_registered(ref)
        if isinstance(target, str):
            # Remote / absolute override -> used verbatim, no relpath.
            url = f"resolve://{target}#{fragment}"
        else:
            url = f"resolve://{_relpath_posix(target, artifact_output_dir)}#{fragment}"
        if task_id not in tasks:
            raise CompileError(
                f"@registered ref recorded for task {task_id!r} but no such task "
                "in the emitted body (internal error)."
            )
        tasks[task_id]["componentRef"] = {"url": url}


def _relocate_child_local_refs(
    *,
    body_dict: dict[str, Any],
    builder: Any,
    source_dir: Path,
    sidecar_dir: Path,
) -> None:
    """Rewrite a child's author-written relative local componentRefs from
    being relative to its SOURCE dir to relative to its SIDECAR dir.

    A child compiles into ``<root>.subgraphs/`` but its ``ref(url=...)``
    URLs were authored relative to the child's own source file. Rewriting
    them keeps each ref pointing at the SAME original file (no copying) so
    the existing hydrator — which resolves child refs relative to the
    loaded sidecar's directory — still finds it.

    Skips compiler-managed tasks (``@task`` resolver sidecars, nested
    subpipeline refs, and ``@registered`` refs): those are rewritten by
    their own driver passes to URLs already computed against the sidecar /
    output directory, not source-relative files. Every relative form (bare
    ``file://x.yaml``, ``./`` and ``../``) is relocated; only absolute /
    remote URLs (``file:///``, ``gs://``, ``http(s)://``) are left
    untouched.
    """
    managed: set[str] = {tid for tid, _ref in builder.task_refs_for_local_from_python}
    managed |= {tid for tid, _ref in builder.task_refs_for_subpipelines}
    managed |= {tid for tid, _ref in builder.task_refs_for_registered}
    tasks = body_dict.get("implementation", {}).get("graph", {}).get("tasks", {})
    for task_id, task in tasks.items():
        if task_id in managed or not isinstance(task, dict):
            continue
        cref = task.get("componentRef")
        if not isinstance(cref, dict):
            continue
        url = cref.get("url")
        if not isinstance(url, str):
            continue
        relocated = _relocate_relative_local_url(url, source_dir, sidecar_dir)
        if relocated is not None:
            cref["url"] = relocated


def _relocate_relative_local_url(url: str, source_dir: Path, sidecar_dir: Path) -> str | None:
    """Return ``url`` rewritten relative to ``sidecar_dir`` instead of
    ``source_dir``, preserving the scheme and any ``#fragment``.

    EVERY RELATIVE local ``file://``/``resolve://`` URL is relocated --
    bare (``file://child.yaml``), ``./`` (``file://./child.yaml``) and
    ``../`` (``file://../child.yaml``) forms alike -- because the hydrator
    resolves ANY non-absolute ``file://``/``resolve://`` path relative to
    the loaded YAML's directory (see
    ``PipelineHydrator._fetch_component_from_file_url``). Returns ``None``
    (leave as-is) for absolute (``file:///abs``) or remote URLs the
    compiler must not touch, for the empty path, AND for payloads that are
    themselves a remote/absolute URL (``resolve://gs://…``,
    ``resolve://https://…``, ``file://gs://…``) — those are not relative
    local paths and must reach the hydrator as-authored rather than being
    mangled into a bogus local path (the hydrator supports ``gs://``
    resolve configs; unsupported nested-remote refs surface their own
    clear error there).
    """
    for scheme in ("file://", "resolve://"):
        if not url.startswith(scheme):
            continue
        rest = url[len(scheme) :]
        path_part, sep, fragment = rest.partition("#")
        # Skip ABSOLUTE local refs (``file:///abs`` -> ``path_part`` starts
        # with ``/``), the empty path, AND nested remote/absolute payloads
        # (``resolve://gs://…``, ``file://gs://…``, ``resolve://https://…``).
        # A nested URL always contains ``://``; a relative local path never
        # does, so ``"://" in path_part`` cleanly discriminates the two.
        # (A scheme-looking relative path like ``file://foo://bar.yaml`` is
        # also skipped — an intentional, acceptable tradeoff: such ambiguous
        # scheme-looking local paths are unsupported, and skipping is
        # preferred over a brittle hydrator-scheme allowlist.)
        if not path_part or path_part.startswith("/") or "://" in path_part:
            return None
        target = (source_dir / path_part).resolve()
        new_rel = _relpath_posix(target, sidecar_dir)
        return f"{scheme}{new_rel}#{fragment}" if sep else f"{scheme}{new_rel}"
    return None


def _build_local_from_python_components(
    task_refs: list[tuple[str, CallableRef]], *, components_yaml_dir: Path
) -> dict[str, Any]:
    """Build the ``<stem>.components.yaml`` content for @task refs.

    Returns an ordered map ``{fragment: {name?, local_from_python:
    {image?, function, dependencies_from?, file}}}``, DEDUPED by FUNCTION
    (the fragment = hyphenated function name). The SAME @task function
    called from multiple task sites collapses to one entry; TWO DISTINCT
    @task functions defined in ONE file each get their own entry (they
    share the same ``file:`` but carry different ``function:`` keys and
    distinct fragments). This matches how ``_rewrite_task_componentref_urls``
    points each task at its OWN function fragment — deduping by source path
    instead would drop every function but the first and leave the others'
    ``resolve://...#<fragment>`` refs dangling.

    The ``function`` field is always emitted so hydrate's
    ``regenerate_yaml`` extracts the right function: it otherwise defaults
    to the file STEM, which is wrong whenever the @task function name
    differs from the source filename (the common case).

    Paths in ``local_from_python.{file,dependencies_from}`` are POSIX and
    relative to ``components_yaml_dir`` so the sidecar is portable: as
    long as the layout under that directory matches at compile- and
    hydrate-time, the paths resolve correctly.

    Raises:
        CompileError: when two distinct source files map to the same
            fragment (function-name collision), when a referenced local
            file (the @task source or its ``dependencies_from``) is
            unreachable, or when a relative path cannot be formed (see
            :func:`_relpath_posix`).
    """
    seen_fragments: dict[str, Path] = {}
    entries: dict[str, Any] = {}
    for _task_id, ref in task_refs:
        source = ref._task_source_path
        if source is None:  # defensive — only @task refs are recorded here
            continue
        fragment = _fragment_for_task(ref)

        prior_source = seen_fragments.get(fragment)
        if prior_source is not None:
            # Already emitted this fragment. Fine when it is the SAME source
            # (the same @task called from multiple sites). A DIFFERENT
            # source sharing the function name would silently collide on the
            # resolve:// fragment, so reject it loudly.
            if prior_source != source:
                raise CompileError(
                    "two distinct @task source files map to the same sidecar "
                    f"fragment {fragment!r}: {prior_source} and {source}. "
                    "Rename one of the @task functions so each has a unique "
                    "name (the function name becomes the resolve:// fragment)."
                )
            continue

        if not source.exists():
            raise CompileError(
                f"@task source file is unreachable: {source}. Compile into "
                "the pipeline source directory, or reference the component "
                "with an absolute file://, gs://, or resolve:// URL."
            )

        local_from_python: dict[str, Any] = {}
        if ref._task_image is not None:
            local_from_python["image"] = ref._task_image
        # Always pin the function name. Without it the hydrator defaults
        # to the file stem and extracts the wrong symbol.
        assert ref._task_function_name is not None
        local_from_python["function"] = ref._task_function_name
        if ref._task_dependencies_from is not None:
            deps = ref._task_dependencies_from
            if not deps.exists():
                raise CompileError(
                    f"@task dependencies_from file is unreachable: {deps}. "
                    "Point dependencies_from at an existing file or drop it."
                )
            local_from_python["dependencies_from"] = _relpath_posix(deps, components_yaml_dir)
        local_from_python["file"] = _relpath_posix(source, components_yaml_dir)

        seen_fragments[fragment] = source
        # The component name is NOT emitted here. A top-level ``name`` on a
        # resolve entry means "resolve a published component by this name" to
        # the hydrator (PipelineHydrator._resolve_primary), which would let a
        # same-named library component silently win over this local @task. The
        # component's name comes from its source docstring (``Metadata: Name:``)
        # at hydrate time, read by regenerate_yaml.
        entry: dict[str, Any] = {"local_from_python": local_from_python}
        entries[fragment] = entry
    return entries


def _rewrite_task_componentref_urls(
    *,
    body_dict: dict[str, Any],
    task_refs: list[tuple[str, CallableRef]],
    components_yaml_name: str,
) -> None:
    """Rewrite each @task task's ``componentRef`` to a pure resolve URL.

    Mutates ``body_dict`` in place. Each entry in ``task_refs`` is a
    ``(task_id, CallableRef)`` tuple; the task_id is the key under
    ``implementation.graph.tasks`` whose componentRef is replaced with
    ``{"url": "resolve://./<components_yaml_name>#<fragment>"}``.
    """
    tasks = body_dict.get("implementation", {}).get("graph", {}).get("tasks", {})
    for task_id, ref in task_refs:
        fragment = _fragment_for_task(ref)
        url = f"resolve://./{components_yaml_name}#{fragment}"
        if task_id not in tasks:
            raise CompileError(
                f"@task ref recorded for task {task_id!r} but no such task in " "the emitted body (internal error)."
            )
        tasks[task_id]["componentRef"] = {"url": url}


def _relative_local_ref_target(url: str) -> str | None:
    """Return the relative path portion of a RELATIVE local componentRef
    URL, stripped of any ``#fragment``.

    Matches EVERY relative ``file://``/``resolve://`` form -- bare
    (``file://child.yaml``), ``file://./``, ``file://../`` and the
    ``resolve://`` equivalents -- because the hydrator resolves ANY
    non-absolute path relative to the compiled YAML's directory. Returns
    ``None`` for absolute (``file:///``), remote (``gs://``,
    ``http(s)://``), empty, payloads that are themselves a remote/absolute
    URL (``resolve://gs://…``, ``resolve://https://…``, ``file://gs://…``),
    or otherwise non-relative URLs — those are the user's responsibility
    and are not checked at compile time (the hydrator handles supported
    nested-remote configs like ``resolve://gs://…`` and surfaces its own
    clear error for unsupported ones).
    """
    for scheme in ("file://", "resolve://"):
        if url.startswith(scheme):
            rest = url[len(scheme) :].split("#", 1)[0]
            # Skip absolute local refs (``file:///abs``), the empty path,
            # AND nested remote/absolute payloads (``resolve://gs://…``,
            # ``file://gs://…``, ``resolve://https://…``). A nested URL
            # always contains ``://``; a relative local path never does, so
            # ``"://" in rest`` cleanly discriminates the two. (A
            # scheme-looking relative path like ``file://foo://bar.yaml`` is
            # also skipped — an intentional, acceptable tradeoff over a
            # brittle hydrator-scheme allowlist.) Treat every remaining
            # relative form (bare, ``./``, ``../``) as a relative local ref,
            # mirroring the hydrator.
            if not rest or rest.startswith("/") or "://" in rest:
                return None
            return rest
    return None


def _validate_local_component_refs_for_artifact(
    body_dict: dict[str, Any],
    output_dir: Path,
    planned_files: set[Path],
    *,
    artifact_label: str | None = None,
) -> None:
    """Assert every RELATIVE local componentRef target either exists relative
    to ``output_dir`` or is a file the compiler is about to write.

    Hydrate resolves componentRef URLs relative to the artifact YAML's own
    location, so a relative ``file://./x`` / ``resolve://./x`` ref is only
    resolvable if ``x`` sits next to that artifact. Generated bundle files
    (child sidecars, ``@task`` components sidecars) are passed in
    ``planned_files`` — they validate as "present" before they are written.
    External relative refs must already exist on disk.

    Args:
        artifact_label: ``None`` for the ROOT (uses the legacy error wording
            relative to the OUTPUT directory); a ``child pipeline '<name>'
            (<file>)`` label for a child sidecar (uses child-context wording
            relative to the child sidecar's directory).

    Raises:
        CompileError: with actionable guidance when a referenced local
            component file is unreachable. We do NOT silently copy files.
    """
    tasks = body_dict.get("implementation", {}).get("graph", {}).get("tasks", {})
    for task_id, task in tasks.items():
        if not isinstance(task, dict):
            continue
        cref = task.get("componentRef")
        if not isinstance(cref, dict):
            continue
        url = cref.get("url")
        if not isinstance(url, str):
            continue
        rel = _relative_local_ref_target(url)
        if rel is None:
            continue
        target = (output_dir / rel).resolve()
        if target in planned_files:
            continue  # a sidecar this same compile is about to write
        if target.exists():
            continue
        if artifact_label is None:
            raise CompileError(
                f"task {task_id!r} references local component {url!r}, but the "
                f"target does not exist relative to the output directory: "
                f"{target}. Hydrate resolves componentRef URLs relative to the "
                "compiled YAML's location, so the referenced file must sit "
                "next to the compiled output. Fix options: compile into the "
                "pipeline source directory so referenced files are colocated "
                "with the output; place the referenced component next to the "
                "compiled YAML; or use an absolute file:///… / gs://… URL or a "
                "published name: ref."
            )
        raise CompileError(
            f"{artifact_label} task {task_id!r} references local component "
            f"{url!r}, but hydrate will resolve it relative to the child "
            f"sidecar directory: {target}. Place the referenced component next "
            "to the child sidecar, compile into a bundle directory with that "
            "layout, or use an absolute file:///… / gs://… / resolve://… "
            "reference."
        )


# ---------------------------------------------------------------------------
# Orchestration helpers


def _evict_shadowed_bundle_modules(bundle_dir: Path) -> None:
    """Evict ``sys.modules`` entries that shadow a module file in ``bundle_dir``.

    Python caches imported modules by NAME, not path, so a previously imported
    top-level module (from a prior in-process compile, a test helper, or a
    REPL) named e.g. ``child_pipeline`` would be reused by a sibling
    ``from child_pipeline import ...`` even when the bundle being compiled
    ships its OWN, DIFFERENT ``child_pipeline.py``. Evicting any cached entry
    whose name matches a ``.py`` stem in ``bundle_dir`` but whose ``__file__``
    differs forces a FRESH import from the bundle's ``sys.path``. Re-importing
    is always safe; the after-compile purge then drops the freshly imported
    entries too, leaving global import state clean.
    """
    try:
        entries = list(bundle_dir.iterdir())
    except OSError:  # pragma: no cover — defensive (missing/inaccessible dir)
        return
    for entry in entries:
        if entry.suffix != ".py":
            continue
        mod = sys.modules.get(entry.stem)
        if mod is None:
            continue
        file = getattr(mod, "__file__", None)
        if not file:
            continue
        try:
            same = Path(file).resolve() == entry.resolve()
        except OSError:  # pragma: no cover — defensive
            same = False
        if not same:
            del sys.modules[entry.stem]


def _purge_bundle_local_modules(source_dirs: set[Path]) -> None:
    """Drop every ``sys.modules`` entry loaded from the bundle's source tree.

    ``_load_pipeline_fn`` exec's the root module under a unique synthetic name
    (restored in its own ``finally``), but the root's top-level
    ``from sibling import ...`` statements — and any trace-time sibling
    imports inside cycle-style children — register sibling/helper modules
    under their REAL names in ``sys.modules``. Those entries are not cleaned
    up by the synthetic-name / ``sys.path`` restoration, so a SUBSEQUENT
    in-process compile of a DIFFERENT bundle that happens to define a module
    with the SAME name (e.g. another ``child_pipeline.py`` in a different temp
    dir) would reuse the STALE cached module and validate against the wrong
    child.

    This is called AFTER the full compile (success or failure). By then the
    traced functions already hold their child ``PipelineFn`` objects by
    reference, so removing the ``sys.modules`` entries is safe — the only goal
    is that the next compile re-imports fresh.

    A module is purged whenever its ``__file__`` resolves under one of
    ``source_dirs`` (the root script dir and every compiled child's own source
    dir). The ``modules_before`` set is deliberately NOT consulted: the
    up-front eviction may have REPLACED a pre-existing cached name with this
    bundle's own module, so a name that pre-dated the compile can still hold a
    bundle-local module that must be cleared. Modules outside the bundle tree
    (stdlib, site-packages, the CLI package itself) are never touched. The
    leak-free synthetic-root-name and ``sys.path`` restoration in
    ``_load_pipeline_fn`` / ``_temp_sys_path`` is left intact.
    """
    resolved_dirs: list[Path] = []
    for d in source_dirs:
        try:
            resolved_dirs.append(d.resolve())
        except OSError:  # pragma: no cover — defensive
            continue
    if not resolved_dirs:
        return
    for name in list(sys.modules):
        mod = sys.modules.get(name)
        file = getattr(mod, "__file__", None)
        if not file:
            continue
        try:
            mod_path = Path(file).resolve()
        except OSError:  # pragma: no cover — defensive
            continue
        if any(mod_path.is_relative_to(d) for d in resolved_dirs):
            del sys.modules[name]


def _candidate_names(fn: PipelineFn) -> tuple[str, str]:
    """Return ``(function_name, display_name)`` for ``--pipeline`` matching.

    ``function_name`` is the decorated function's ``__name__`` (a clean,
    shell-friendly identifier such as ``train_model``); ``display_name`` is
    the ``@pipeline(name=...)`` value emitted as the YAML ``name:`` (may
    contain spaces, e.g. ``"Train Model"``).
    Selection matches the function name first, then the display name.
    """
    func_name = getattr(fn.fn, "__name__", "") or ""
    return (func_name, fn.name)


def _format_candidates(candidates: list[PipelineFn]) -> str:
    """Render candidates as ``function_name ("Display Name")`` for errors."""
    return ", ".join(f'{func} ("{disp}")' for func, disp in (_candidate_names(c) for c in candidates))


def _select_by_name(candidates: list[PipelineFn], pipeline_name: str) -> list[PipelineFn]:
    """Filter ``candidates`` matching ``pipeline_name``.

    The function ``__name__`` is matched first (preferred — it is the
    shell-friendly identifier and the documented disambiguator); only when
    no function name matches does the ``@pipeline`` display name apply. This
    ordering means a file with two same-display-name siblings can always be
    disambiguated by passing a function name.
    """
    by_func = [c for c in candidates if _candidate_names(c)[0] == pipeline_name]
    if by_func:
        return by_func
    return [c for c in candidates if _candidate_names(c)[1] == pipeline_name]


def _load_pipeline_fn(module_path: Path, pipeline_name: str | None = None) -> PipelineFn:
    """Load ``module_path`` as a module and return a single PipelineFn.

    When the file defines exactly one root pipeline it is returned
    directly. When it defines several, ``pipeline_name`` selects which one
    to compile (matched against the function ``__name__`` first, then the
    ``@pipeline`` display name, selected via ``--pipeline``). The selected
    pipeline's same-file nested children are still reachable to
    ``subpipeline(child)(...)`` and are compiled via the recursion driver,
    never via this candidate list.

    Leak-free: the module's parent dir is temporarily added to
    ``sys.path`` (so top-level ``import`` of sibling modules resolves
    during exec) and the module is registered under a UNIQUE name in
    ``sys.modules``. Both are restored in a ``finally`` so repeated or
    concurrent in-process compiles never collide and no global import
    state leaks. The loaded module object stays alive via the returned
    ``PipelineFn`` regardless of the ``sys.modules`` cleanup.
    """
    module_dir = str(module_path.parent)
    # Unique module name so repeated/concurrent in-process compiles don't
    # collide and so we can cleanly remove our own sys.modules entry.
    module_name = f"_tangle_user_pipeline_{uuid.uuid4().hex}"

    spec = importlib.util.spec_from_file_location(module_name, str(module_path))
    if spec is None or spec.loader is None:
        raise CompileError(f"could not load module spec from {module_path}")
    mod = importlib.util.module_from_spec(spec)

    # Snapshot global import state so we can restore it leak-free.
    added_to_sys_path = module_dir not in sys.path
    prior_mod = sys.modules.get(module_name)
    if added_to_sys_path:
        sys.path.insert(0, module_dir)
    sys.modules[module_name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        # Re-surface user-module exceptions wrapped as CompileError so
        # the CLI exits 1 cleanly.
        raise CompileError(f"error importing pipeline file {module_path}: {e}") from e
    finally:
        # Restore the sys.modules entry (delete ours, or put back prior).
        if prior_mod is not None:
            sys.modules[module_name] = prior_mod
        else:
            sys.modules.pop(module_name, None)
        # Remove the dir from sys.path only if we added it.
        if added_to_sys_path:
            try:
                sys.path.remove(module_dir)
            except ValueError:  # pragma: no cover — defensive
                pass

    all_candidates = [v for v in vars(mod).values() if isinstance(v, PipelineFn)]
    if not all_candidates:
        raise CompileError(f"no @pipeline-decorated function found in {module_path}")

    # Root discovery selects the pipeline(s) DEFINED IN the target file. A
    # parent module that imports child pipelines (so it can wrap them with
    # ``subpipeline(child)(...)``) must not count those imports as roots.
    # Compare each candidate's source file against
    # ``module_path``; imported children resolve to a DIFFERENT file and are
    # ignored as compile targets (they remain reachable to ``subpipeline``).
    # Several pipelines DEFINED in this one file are now allowed: when more
    # than one is local the caller selects which to emit via ``pipeline_name``
    # (``--pipeline``); same-file nested children are then compiled through
    # the ``subpipeline`` recursion, not picked from this candidate list.
    target = module_path.resolve()
    local_candidates: list[PipelineFn] = []
    undetermined: list[PipelineFn] = []
    for cand in all_candidates:
        src = _pipeline_source_path(cand)
        if src is None:
            undetermined.append(cand)
        elif src == target:
            local_candidates.append(cand)
        # else: defined in another file (imported child) -> not a root.

    if not local_candidates and not undetermined:
        # Every candidate is defined in another file: the target imports
        # pipelines but defines none of its own.
        names = [getattr(c, "name", "?") for c in all_candidates]
        raise CompileError(
            f"no @pipeline-decorated function defined in {module_path}; found "
            f"only imported pipeline(s): {names!r}. Define the compile-target "
            "@pipeline in this file (imported child pipelines are wrapped with "
            "subpipeline(child)(...), not compiled directly)."
        )

    if pipeline_name is not None:
        # Explicit selection (``--pipeline``). Match against the function
        # name then the display name, across local AND undetermined-source
        # candidates (an exec'd module still exposes ``fn.__name__``).
        # Imported children are never selectable as a root.
        searchable = [*local_candidates, *undetermined]
        matches = _select_by_name(searchable, pipeline_name)
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise CompileError(
                f"pipeline {pipeline_name!r} not found in {module_path}; "
                f"available: {_format_candidates(searchable)}."
            )
        # Two or more matched (same display name on distinct functions).
        func_names = ", ".join(_candidate_names(c)[0] for c in matches)
        raise CompileError(
            f"pipeline name {pipeline_name!r} is ambiguous in {module_path}; " f"use the function name: {func_names}."
        )

    # No explicit selection. Prefer locals; fall back to undetermined with
    # the single-candidate dynamic auto-select: a single
    # exec-built PipelineFn whose source cannot be resolved is still a valid
    # root, but two undetermined candidates cannot be told apart from
    # imports, so they fall through to the multiple-pipelines error.
    if local_candidates:
        candidates = local_candidates
    else:
        if len(all_candidates) == 1:
            return all_candidates[0]
        candidates = undetermined

    if len(candidates) == 1:
        return candidates[0]

    raise CompileError(
        f"multiple @pipeline-decorated functions found in {module_path}: "
        f"{_format_candidates(candidates)}. Pass --pipeline <name> to select "
        "one (function name or display name)."
    )


def _pipeline_source_path(pipeline_fn: PipelineFn) -> Path | None:
    """Resolve the source file that DEFINES ``pipeline_fn``'s function.

    Returns the symlink-resolved :class:`Path`, or ``None`` when the
    source is UNDETERMINED — i.e. a dynamically built function whose
    source cannot be located. Used by :func:`_load_pipeline_fn` to
    distinguish locally defined root pipelines from imported child
    pipelines, and to honour the single-candidate dynamic
    fallback.

    A source is treated as undetermined (``None``) when:

    * it cannot be read at all (``inspect`` failure, no ``co_filename``);
    * it is a pseudo filename produced by ``exec``/``eval``/the REPL,
      e.g. ``<string>`` / ``<stdin>`` (matched as ``<...>``); or
    * the resolved path does not exist on disk (so it cannot be
      meaningfully compared against the real target module file).
    """
    fn = pipeline_fn.fn
    src: str | None
    try:
        src = inspect.getsourcefile(fn) or inspect.getfile(fn)
    except (TypeError, OSError):
        src = None
    if not src:
        code = getattr(fn, "__code__", None)
        src = getattr(code, "co_filename", None)
    if not src:
        return None
    # Pseudo filenames from exec/eval/REPL ("<string>", "<stdin>", ...)
    # are not real on-disk sources — treat as undetermined so the
    # single-candidate dynamic fallback in _load_pipeline_fn can apply.
    if src.startswith("<") and src.endswith(">"):
        return None
    try:
        resolved = Path(src).resolve()
    except OSError:  # pragma: no cover — defensive
        return None
    # A non-existent path (e.g. a synthetic co_filename) cannot be
    # compared against the target module file — treat as undetermined.
    if not resolved.exists():
        return None
    return resolved


def _pipeline_accepts_cfg(pipeline_fn: PipelineFn) -> bool:
    """Return whether the pipeline function has a real ``cfg`` parameter.

    ``@pipeline(config=...)`` only matters when the authored function accepts a
    parameter named ``cfg`` (and that parameter is not an ``In[T]`` graph input).
    Pipelines without such a parameter cannot observe compile-time config, so
    requiring the file would make stale decorator metadata unnecessarily fatal.
    """
    try:
        sig = inspect.signature(pipeline_fn.fn)
    except (TypeError, ValueError):  # pragma: no cover — defensive
        return False
    param = sig.parameters.get("cfg")
    if param is None:
        return False

    annotation = param.annotation
    try:
        resolved_hints = get_type_hints(pipeline_fn.fn, include_extras=True)
        annotation = resolved_hints.get("cfg", annotation)
    except Exception:
        pass
    return getattr(annotation, "__origin__", None) is not In


def _resolve_cfg_path_in_dir(pipeline_fn: PipelineFn, base_dir: Path) -> Path:
    """Resolve ``@pipeline(config=...)`` relative to ``base_dir``.

    ``base_dir`` is the pipeline's OWN source directory — the root script's
    parent for the root, or the child ``PipelineFn``'s source directory for
    a nested child (each pipeline loads its own config relative
    to its own file). If the decorator omits ``config=``, defaults to
    ``<base_dir>/config.yaml``.
    """
    if pipeline_fn.config_path:
        cfg_path = Path(pipeline_fn.config_path)
    else:
        cfg_path = Path("config.yaml")
    if not cfg_path.is_absolute():
        cfg_path = base_dir / cfg_path
    return cfg_path.resolve()


def _assert_config_output_path_is_separate(
    cfg_path: Path,
    output_path: Path,
    *,
    pipeline_fn: PipelineFn,
    is_root: bool,
) -> None:
    """Reject ``@pipeline(config=...)`` paths that collide with output.

    The config file is a compile-time INPUT read before the compiler writes
    anything. Treating a missing output path as an empty config would mask
    typos, and treating an existing output path as config would make the
    compiler read a previous compiled artifact as its input config. Fail
    before any writes with guidance that explains the two distinct paths.
    """
    if cfg_path.resolve() != output_path.resolve():
        return

    output_label = "--output" if is_root else "generated child sidecar output"
    raise CompileError(
        f"@pipeline config for {pipeline_fn.name!r} resolves to the same path as "
        f"the {output_label}: {cfg_path}. The `config=` argument names a "
        "compile-time input config file; it is read before the compiled YAML "
        "is written. The compiler creates the output file automatically after "
        "validation, so do not point `config=` at the output. Use a separate "
        "config file (for example an empty `*.compile_config.yaml`) or omit "
        "`config=` to use `config.yaml`, and keep `--output` for the compiled YAML."
    )


def _load_cfg_and_raw(
    cfg_path: Path,
    overrides: Mapping[str, Any],
    *,
    coerce: bool = True,
    pipeline_fn: PipelineFn,
    usage: str = "cfg",
):
    """Load cfg via ``cfg.load_cfg`` AND read the raw YAML dict.

    The raw dict (config.yaml WITHOUT overrides applied) is returned so the
    caller can build a broadcast layer from this pipeline's OWN config.
    ``coerce`` is threaded to :func:`load_cfg`: the ROOT passes ``coerce=True``
    so its CLI ``--override`` strings keep YAML coercion; a CHILD passes
    ``coerce=False`` so its already-typed native ``effective`` overrides pass
    through unchanged. Overrides are applied to the :class:`Cfg` object only —
    the compiled YAML carries no config keys.
    """
    import yaml

    if not cfg_path.exists():
        source = (
            f"@pipeline(config={pipeline_fn.config_path!r})"
            if pipeline_fn.config_path
            else "the default config.yaml"
        )
        if usage == "propagate_config":
            reason = (
                "has propagate_config=True, so "
                f"{source} is required as the config payload to broadcast to "
                "descendant subpipelines. Create the file, remove `config=`, "
                "or disable propagate_config if there is no config to broadcast."
            )
        else:
            reason = (
                "has a `cfg` parameter, so "
                f"{source} is required as a compile-time input. Create the file, "
                "or remove the `cfg` parameter (and omit `config=`) if the "
                "pipeline does not use compile-time config."
            )
        raise CompileError(
            f"config file not found: {cfg_path}. Pipeline {pipeline_fn.name!r} {reason}"
        )
    raw = yaml.safe_load(cfg_path.read_text()) or {}
    if not isinstance(raw, dict):
        raise CompileError(f"config at {cfg_path} must be a YAML mapping, got {type(raw).__name__}")
    cfg = load_cfg(cfg_path, overrides=dict(overrides), coerce=coerce)
    return cfg, raw


def _read_raw_cfg(cfg_path: Path) -> dict[str, Any]:
    """Read a child's raw config.yaml as a plain mapping (no overrides).

    Used to drive the strict/lenient override resolution in
    :func:`_effective_overrides_for_child` — both the broadcast same-name
    overlay and the explicit ``.override_config`` typo check key off the keys
    the child actually declares. Mirrors the error style of
    :func:`_load_cfg_and_raw` (missing file / non-mapping config).
    """
    import yaml

    if not cfg_path.exists():
        raise CompileError(f"config file not found: {cfg_path}")
    raw = yaml.safe_load(cfg_path.read_text()) or {}
    if not isinstance(raw, dict):
        raise CompileError(f"config at {cfg_path} must be a YAML mapping, got {type(raw).__name__}")
    return raw


def _resolve_broadcast_stack(
    broadcast_stack: list[BroadcastLayer],
    *,
    key_filter: set[str] | None,
) -> dict[str, Any]:
    """Resolve the active broadcast stack into a single key/value map applying
    the two precedence tiers (broadcast tier, then explicit tier).

    Tiers, lowest -> highest:

    1. **Broadcast tier** — layers with ``explicit=False`` (a flagged
       pipeline's own config). Iterated OUTER -> INNER so the nearest layer
       that defines a key wins WITHIN the tier.
    2. **Explicit tier** — layers with ``explicit=True`` (a flagged caller's
       per-edge ``.override_config`` that flows deep). Iterated OUTER -> INNER
       (nearest-wins) and applied AFTER the entire broadcast tier, so the
       explicit tier ALWAYS outranks the broadcast tier regardless of depth.

    ``key_filter`` is the lenient same-name guard: when given (the set of keys
    a node actually declares), only those keys are folded in — keys the node
    does not declare are skipped. Pass ``None`` to fold in EVERY key present in
    the stack (used by the ambient pass-through resolver, which needs the full
    resolved context over all keys regardless of what any single node declares).

    This is the single source of the tier logic; both
    :func:`_effective_overrides_for_child` and :func:`_resolve_ambient_context`
    route through it so their precedence can never diverge.
    """

    def _allowed(key: str) -> bool:
        return key_filter is None or key in key_filter

    resolved: dict[str, Any] = {}
    # Broadcast tier first (own-config layers), outer -> inner (inner wins).
    for layer in broadcast_stack:
        if layer.explicit:
            continue
        for key, value in layer.config.items():
            if _allowed(key):
                resolved[key] = value
    # Explicit tier on top (per-edge override layers), outer -> inner. Applied
    # after the whole broadcast tier so explicit always outranks broadcast.
    for layer in broadcast_stack:
        if not layer.explicit:
            continue
        for key, value in layer.config.items():
            if _allowed(key):
                resolved[key] = value
    return resolved


def _effective_overrides_for_child(
    *,
    child_raw: Mapping[str, Any],
    broadcast_stack: list[BroadcastLayer],
    explicit: Mapping[str, Any],
    child_name: str,
    parent_task_id: str,
) -> dict[str, Any]:
    """Resolve a child's effective overrides across the two precedence tiers
    plus the strict direct edge.

    Layered lowest -> highest precedence:

    1. **Broadcast tier** (lenient, nearest-wins) — flagged ancestors' own
       config flowing deep. Applied only for keys the child declares
       (``key in child_raw``).
    2. **Explicit tier** (lenient, nearest-wins) — a flagged caller's per-edge
       ``.override_config`` that flows deep. Applied AFTER the whole broadcast
       tier so it always outranks broadcast, even from a NEARER flagged
       descendant. Still lenient (``key in child_raw``).
    3. The **direct edge** ``.override_config`` into THIS child (``explicit``
       param) — STRICT: a key NOT present in the child's own ``config.yaml``
       is a :class:`CompileError` (typo protection); otherwise it overlays,
       winning over both tiers.

    Tiers 1-2 are resolved by the shared :func:`_resolve_broadcast_stack`
    helper (with the child's declared keys as the lenient ``key_filter``).
    """
    declared = set(child_raw)
    effective: dict[str, Any] = _resolve_broadcast_stack(broadcast_stack, key_filter=declared)
    # 3. Direct-edge explicit overrides — strict, wins over both tiers.
    for key, value in explicit.items():
        if key not in child_raw:
            raise CompileError(
                f"subpipeline task {parent_task_id!r} sets .override_config("
                f"{key}=...) for child pipeline {child_name!r}, but {key!r} is "
                f"not a key in that child's config.yaml. Declared child config "
                f"keys: {sorted(child_raw)}."
            )
        effective[key] = value
    return effective


def _resolve_ambient_context(broadcast_stack: list[BroadcastLayer]) -> dict[str, Any]:
    """Resolve the FULL ambient context map over ALL keys in the active stack.

    Applies the SAME two precedence tiers as
    :func:`_effective_overrides_for_child` via the shared
    :func:`_resolve_broadcast_stack` helper, but with NO ``key_filter`` — every
    key present in the stack is folded in, regardless of what any single node
    declares. Used to compute the ambient PASS-THROUGH context:
    the keys that flow PAST a node, unchanged, to its descendants.
    """
    return _resolve_broadcast_stack(broadcast_stack, key_filter=None)


# ---------------------------------------------------------------------------
# Command handler


class PipelineCompiler(TangleCliHandler):
    """Compile a Python-authored pipeline to a dehydrated YAML bundle.

    The object-oriented entry point for the compile command, mirroring
    :class:`tangle_cli.pipeline_hydrator.PipelineHydrator`: a
    :class:`~tangle_cli.handler.TangleCliHandler` subclass that drives the
    module-level :func:`compile_pipeline` free functions and reports the
    written artifacts (and any non-fatal warnings) through ``self.log``.

    Compilation is fully offline — it traces the local Python authoring file
    and emits YAML — so no Tangle API client is required; the handler base is
    still used for its shared logger/``dry_run`` plumbing and to give
    downstream distributions a single class to subclass.

    Distributions that carry a zone concept subclass this handler (and extend
    the :data:`ZONE_ROOT_MARKERS` seam) so an explicit relative
    ``@registered(gen_config=...)`` resolves against a zone root, while the
    generic trace/emit/validate/write logic stays here.
    """

    def compile_file(
        self,
        script: Path,
        output: Path,
        *,
        overrides: Mapping[str, str] | None = None,
        pipeline_name: str | None = None,
        emit_components_sidecar: bool = True,
    ) -> CompileResult:
        """Compile ``script`` to a single dehydrated pipeline YAML at ``output``.

        Thin object-oriented wrapper over :func:`compile_pipeline`: it runs the
        compile and logs the written artifact paths and any non-fatal warnings
        through ``self.log``. See :func:`compile_pipeline` for the full
        argument, return, and error contract — in particular it raises
        :class:`CompileError` for user-facing problems (missing script, no /
        multiple ``@pipeline`` functions, invalid config, unreachable
        ``@task`` sources) and :class:`SchemaValidationError` when a compiled
        artifact fails dehydrated-schema validation.
        """
        result = compile_pipeline(
            script,
            output,
            overrides,
            pipeline_name=pipeline_name,
            emit_components_sidecar=emit_components_sidecar,
        )
        self.log.info(f"wrote {result.pipeline_path}")
        if result.components_path is not None:
            self.log.info(f"wrote {result.components_path}")
        for subgraph_path in result.subgraph_paths:
            self.log.info(f"wrote {subgraph_path}")
        self.log.info(f"compiled {result.task_count} task(s)")
        for warning in result.warnings:
            self.log.info(f"warning: {warning}")
        return result
