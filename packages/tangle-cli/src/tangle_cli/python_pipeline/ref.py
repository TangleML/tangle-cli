"""``ref(url=...)`` — callable component handle.

Returns a :class:`CallableRef` that records the user's URL/name/digest
verbatim. Methods that capture additional metadata (``.bind``,
``.named``, ``.with_annotations``) are composable, immutable operations
the tracer can call. Calling a :class:`CallableRef` inside a live
``@pipeline`` trace context records a :class:`TaskNode` into the active
:class:`GraphBuilder`; calling it outside a trace raises ``RuntimeError``.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .errors import CompileError


@dataclass(frozen=True)
class CallableRef:
    """Immutable handle to a Tangle component.

    Core shape: ``url``, ``ref_name``, ``ref_digest``, ``bound_kwargs``,
    ``task_id_hint``, ``annotations``. A parallel ``_task_*`` metadata
    bundle lets a CallableRef produced by ``@task`` be
    ``materialize()``-d into a component YAML at compile time, and a
    ``_registered_*`` bundle lets a CallableRef produced by ``@registered``
    be rewritten to a ``resolve://`` URL pointing at an EXISTING
    ``gen_config.yaml`` (no sidecar generated). The same proxy type is
    reused for ``ref()``, ``@task`` and ``@registered`` so the tracer
    doesn't need to special-case anything; for ``@task`` the ``url`` field
    is left ``None`` and the compile driver fills it in with a
    ``resolve://`` URL pointing at the generated sidecar, while for
    ``@registered`` the ``url`` carries the ``registered://pending``
    sentinel the driver rewrites to the gen_config.yaml ``resolve://`` URL.
    ``@task`` and ``@registered`` are mutually exclusive: a ref is built by
    one decorator or the other, so only one metadata bundle is ever set.
    """

    url: str | None
    ref_name: str | None = None
    ref_digest: str | None = None
    bound_kwargs: dict[str, Any] = field(default_factory=dict)
    task_id_hint: str | None = None
    annotations: dict[str, str] | None = None
    # ``@task`` metadata. ``None`` for ``ref()``-derived refs;
    # populated by the ``@task`` decorator. ``materialize()`` rejects
    # any ref where ``_task_source_path`` is None.
    _task_source_path: Path | None = None
    _task_function_name: str | None = None
    _task_image: str | None = None
    _task_dependencies_from: Path | None = None
    _task_mode: str | None = None
    _task_resolve_root: Path | None = None
    _task_custom_annotations: dict[str, str] | None = None
    # ``@registered`` metadata. ``None`` for ``ref()``/``@task`` refs;
    # populated by the ``@registered`` decorator. Drives the compile-time
    # rewrite of the ``registered://pending`` sentinel URL to a
    # ``resolve://<rel-path>/gen_config.yaml#<fragment>`` URL pointing at an
    # EXISTING gen_config.yaml (NOT sidecar generation -- nothing is
    # written). ``_registered_gen_config`` is the author-supplied path (or
    # ``None`` to default to the nearest ancestor ``gen_config.yaml``);
    # ``_registered_fragment`` is the gen_config top-level key (or ``None``
    # to default to the function name verbatim).
    _registered_source_path: Path | None = None
    _registered_function_name: str | None = None
    _registered_fragment: str | None = None
    _registered_gen_config: str | None = None

    # ------------------------------------------------------------------
    # Builders — all return a fresh CallableRef (immutable composition)

    def bind(self, **kwargs: Any) -> "CallableRef":
        """Return a new CallableRef with ``kwargs`` merged into
        ``bound_kwargs``. Later .bind calls win on conflict."""
        merged = {**self.bound_kwargs, **kwargs}
        return replace(self, bound_kwargs=merged)

    def named(self, task_id: str) -> "CallableRef":
        """Return a new CallableRef whose task ID will be ``task_id``
        instead of the LHS-derived auto ID."""
        return replace(self, task_id_hint=task_id)

    def with_annotations(self, ann: dict[str, Any]) -> "CallableRef":
        """Return a new CallableRef with per-task annotations.

        emit.py renders these as an ``annotations:`` block on the task
        before ``componentRef:``.
        """
        merged: dict[str, str] = dict(self.annotations or {})
        # Values are coerced to str at emit time; here we accept Any but
        # store as a dict to preserve user intent.
        for k, v in ann.items():
            merged[k] = v  # type: ignore[assignment]
        return replace(self, annotations=merged)

    # ------------------------------------------------------------------
    # @task codegen — materialize() writes the component YAML.

    def materialize(self, output_path: Path | None = None) -> Path:
        """Write the component YAML for a ``@task``-derived ref to disk.

        Only valid on CallableRefs produced by the ``@task`` decorator
        — those carry the source path / function name / image metadata
        needed by ``tangle_cli.component_generator.regenerate_yaml``.
        Calling this on a plain ``ref(url=...)`` raises
        :class:`RuntimeError` (the user already has a YAML on the other
        end of that URL — there's nothing for us to generate).

        Args:
            output_path: Where to write the YAML. Defaults to
                ``<source_dir>/generated/<function_name>.yaml`` to
                match the convention used by the emitted
                ``componentRef.url`` (``file://./generated/<stem>.yaml``).

        Returns:
            The resolved ``output_path``.

        Lazy import: ``tangle_cli.component_generator`` is imported only
        here, never at package import time.
        """
        if self._task_source_path is None or self._task_function_name is None:
            raise RuntimeError(
                "CallableRef.materialize() is only valid for refs created "
                "by the @task decorator. This ref was built via ref(url=...) "
                "and points at an existing YAML; there is nothing to "
                "generate."
            )

        if output_path is None:
            output_path = (
                self._task_source_path.parent
                / "generated"
                / f"{self._task_function_name}.yaml"
            )

        # Lazy import keeps package import cheap. ``ComponentGenerator`` is
        # imported only when an authoring ref materializes component YAML.
        from tangle_cli.component_generator import ComponentGenerator

        generator = ComponentGenerator()
        if self._task_custom_annotations:
            image = (
                self._task_image
                or generator.extract_image_from_yaml(output_path)
                or generator.default_container_image
            )
            deps = self._task_dependencies_from or generator.find_dependencies_file(
                self._task_source_path
            )
            output_path.parent.mkdir(parents=True, exist_ok=True)
            generator.generate_component_yaml(
                file_path=self._task_source_path,
                output_path=output_path,
                container_image=image,
                function_name=self._task_function_name,
                dependencies_from=deps,
                custom_annotations=self._task_custom_annotations,
                mode=self._task_mode or "inline",
                resolve_root=self._task_resolve_root,
            )
        else:
            generator.regenerate_yaml(
                python_file=self._task_source_path,
                output_path=output_path,
                function_name=self._task_function_name,
                image=self._task_image,
                dependencies_from=self._task_dependencies_from,
                mode=self._task_mode or "inline",
                resolve_root=self._task_resolve_root,
            )
        return output_path

    # ------------------------------------------------------------------
    # Trace-mode call site

    def __call__(self, **kwargs: Any) -> Any:
        """Trace-mode invocation.

        Records a :class:`TaskNode` into the active :class:`GraphBuilder`
        (looked up via ``trace.current_builder()``) and returns a bare
        :class:`TaskOutputProxy` handle. The task ID is either the value
        captured by ``.named(...)`` or the LHS variable name on the
        call site (resolved via the AST pre-pass map stashed on the
        builder).

        Edge kwargs (``wait_for`` / ``depends_on``) and regular kwargs
        share one ``arguments`` dict in the IR; the value-vs-key
        dispatch happens at emit time. ``.bind(...)`` kwargs are merged
        in last so call-site kwargs win on conflict (same key) and come
        first in insertion order (the bind block is appended).
        """
        # Local import keeps the @ref shell importable during early
        # bootstrap (and avoids the circular dep at module load).
        import sys

        from . import trace
        from .errors import AmbiguousTaskIdError
        from .graph import TaskNode
        from .ids import snake_to_title_case
        from .placeholders import TaskOutputProxy

        builder = trace.current_builder()
        if builder is None:
            raise RuntimeError(
                "CallableRef.__call__ requires an active @pipeline trace "
                "context. Either call this inside a function decorated "
                "with @pipeline, or compile the script with "
                "`tangle sdk pipelines compile`."
            )

        # Resolve the task ID. ``.named(...)`` always wins over the
        # AST-derived auto ID.
        if self.task_id_hint is not None:
            task_id = self.task_id_hint
        else:
            caller_lineno = sys._getframe(1).f_lineno
            lhs_name = builder.lineno_to_lhs.get(caller_lineno)
            if lhs_name is None:
                raise AmbiguousTaskIdError(
                    f"Cannot infer task ID at line {caller_lineno}: the LHS "
                    "is not a single bare variable name. Either bind to a "
                    "single name (e.g. `task_a = ref(...)(...)`) or call "
                    "`.named('Task Id')` on the ref."
                )
            task_id = snake_to_title_case(lhs_name)

        # Merge bound and call-site kwargs: call-site keys come first in
        # insertion order and win on conflict; bound keys are appended.
        merged: dict[str, Any] = {}
        for k, v in kwargs.items():
            merged[k] = v
        for k, v in self.bound_kwargs.items():
            if k not in merged:
                merged[k] = v

        node = TaskNode(
            task_id=task_id,
            ref_url=self.url,
            ref_name=self.ref_name,
            ref_digest=self.ref_digest,
            arguments=merged,
            annotations=dict(self.annotations) if self.annotations else None,
        )
        builder.add_task(node)

        # If this ref was produced by ``@task``, record ``(task_id,
        # self)`` on the builder so the compile driver can (a) auto-emit
        # a sibling ``<out>.components.yaml`` with a ``local_from_python:``
        # entry for each unique @task source file, and (b) rewrite this
        # task's ``componentRef.url`` to
        # ``resolve://./<out_stem>.components.yaml#<fragment>``. Idle
        # for plain ``ref(url=...)`` refs (their YAML is already on
        # the other end of the URL).
        if self._task_source_path is not None:
            builder.task_refs_for_local_from_python.append((task_id, self))

        # If this ref was produced by ``@registered``, record ``(task_id,
        # self)`` on the builder so the compile driver can rewrite this
        # task's ``componentRef.url`` from the ``registered://pending``
        # sentinel to a pure ``resolve://<rel-path>/gen_config.yaml#<fragment>``
        # URL pointing at the operation's EXISTING gen_config.yaml. No
        # sidecar is generated. ``@task`` and ``@registered`` are mutually
        # exclusive, so this branch and the ``@task`` branch above never
        # both fire for one ref.
        if self._registered_source_path is not None:
            builder.task_refs_for_registered.append((task_id, self))

        return TaskOutputProxy(task_id=task_id)


def ref(
    url: str | None = None,
    *,
    name: str | None = None,
    digest: str | None = None,
    tag: str | None = None,
) -> CallableRef:
    """Build a CallableRef pointing at a Tangle component.

    A ref carries a *locator* — either a ``url`` or a published ``name`` —
    and may optionally pin a ``digest`` alongside either (or stand on its
    own). All values are stored verbatim (no normalization). The emitter
    turns the locator into the matching ``componentRef`` form, and the
    hydrator resolves it via ``_fetch_component_by_{url,name,digest}``.

    Supported (WORKING) locator combinations:

    - ``ref(url="resolve://<rel>#<fragment>")`` — relative to the hydrating
      pipeline; also ``file://./…`` / ``gs://…`` / ``https://…`` URLs —
      emits ``{"url": …}``.
    - ``ref(name="my-comp")`` — a published component by name — emits
      ``{"name": …}``.
    - ``ref(name="my-comp", digest="<64hex>")`` — name pinned to a digest —
      emits ``{"name": …, "digest": …}``.
    - ``ref(digest="<64hex>")`` — pin by digest alone — emits
      ``{"digest": …}``.
    - ``ref(url="gs://b/x.yaml", digest="<64hex>")`` — a URL pinned to a
      digest — emits ``{"url": …, "digest": …}``.

    ``tag`` is accepted in the signature for forward compatibility but is
    **NOT supported yet**: the hydrator has no tag fetcher, so a ``tag``
    ref would compile to a dehydrated YAML the hydrator silently leaves
    unresolved. Passing ``tag=`` raises :class:`CompileError`. Pin by
    ``name=`` and/or ``digest=`` instead.

    Raises:
        CompileError: if ``tag`` is passed (deferred); if both ``url`` and
            ``name`` are passed (conflicting primary locators); or if no
            locator at all is given.
    """
    # 1. tag is not resolvable end-to-end yet (hydrator has no tag fetcher).
    if tag is not None:
        raise CompileError(
            "ref(tag=...) is not supported yet: the hydrator resolves "
            "digest/name/url only. Pin by name=... and/or digest=... instead."
        )
    # 2. exactly one PRIMARY locator family: url XOR name (digest is optional
    #    and may accompany either, or stand alone).
    if url is not None and name is not None:
        raise CompileError(
            "ref() takes EITHER url=... OR name=..., not both (conflicting "
            "locators). Use digest=... to pin a version alongside either."
        )
    if url is None and name is None and digest is None:
        raise CompileError(
            "ref() requires a locator: pass url=..., or name=...[, digest=...], "
            "or digest=..."
        )
    return CallableRef(url=url, ref_name=name, ref_digest=digest)
