"""``@registered`` decorator.

The decorator marks a Python function as an operation that is ALREADY
registered in an existing ``gen_config.yaml`` (a ``resolve://`` config),
typically published separately and pushed by a CI step. Unlike ``@task``
— which authors the operation inline and auto-generates a sibling
``<out>.components.yaml`` sidecar — ``@registered`` generates NOTHING. Its
only job is to stamp a *fragment* + the *gen_config.yaml* the operation is
registered in, so that at compile time the driver can emit a
``componentRef: {url: "resolve://<rel-path>/gen_config.yaml#<fragment>"}``
pointing at that already-existing config.

This fits the common monorepo setup (e.g. the ``world`` zone
``areas/ml/upi/``) where operations are published once into a
hand-maintained ``gen_config.yaml`` and pipelines reference them by
``resolve://`` URL. Authors decorate the registered operation functions
with ``@registered`` and call them from a ``@pipeline`` body; the compiler
takes care of computing the right relative ``resolve://`` URL.

At compile time the driver collects every ``@registered`` ref called
inside a ``@pipeline`` body and rewrites each task's componentRef URL to
``resolve://<rel-path-to-gen_config.yaml>#<fragment>``, computed PER
artifact against that artifact's own output directory (so nested
subpipeline children land on a correct relative path). No sidecar is ever
written — the gen_config.yaml the URL points at already exists on disk.

Crucially, the decorator does NOT call the user function, and it does NOT
touch the filesystem beyond the cheap ``inspect.getfile(fn)`` probe at
decoration time. Resolving the gen_config.yaml (the marker walk, the
nearest-``gen_config.yaml`` walk, the relpath) happens LAZILY in the
compiler, so importing an operation module in isolation (e.g. for the
op's own unit tests) never requires the marker or gen_config to be
present.

Lazy import contract: like ``task.py``, this module does NOT import
``tangle_cli.component_generator`` at module load. It DOES eagerly
install the ``cloud_pipelines`` shim (same as ``task.py``) so generated
ops that do ``from cloud_pipelines import components`` at top level can be
imported by ``@registered`` wrappers without each pipeline having to call
a private tangle_cli helper.

Example::

    from tangle_cli.python_pipeline import registered

    @registered(fragment="run-query", gen_config="shared/components/gen_config.yaml")
    def run_query(sql_query: str = "SELECT 1") -> str:
        '''Run a query.

        Metadata:
            Name: Run Query
            Version: 1.0.0
        '''
        ...

    # ``run_query`` is now a CallableRef -- NOT the function. Calling it
    # inside a @pipeline body records a TaskNode and registers the ref so
    # the compile driver rewrites its componentRef URL to
    # ``resolve://<rel-path>/gen_config.yaml#run-query`` at compile time.
"""
from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any, Callable

# Install the ``cloud_pipelines`` shim eagerly at module load so generated
# components that do ``from cloud_pipelines import components`` at top
# level can be imported by user @registered wrappers without each pipeline
# having to call a private tangle_cli helper. ``component_from_func``
# is NOT ``component_generator`` (the heavy codegen module) -- it's the
# lighter introspection module, and the shim helper itself is a no-op when
# ``cloud_pipelines`` is already in ``sys.modules``.
from tangle_cli.component_from_func import _ensure_cloud_pipelines_shim

from .ref import CallableRef

_ensure_cloud_pipelines_shim()

# Sentinel componentRef URL stamped onto a @registered ref at decoration
# time. The compile driver rewrites it to a real
# ``resolve://<rel-path>/gen_config.yaml#<fragment>`` URL after tracing
# (the relpath depends on the artifact's output dir), so it never reaches
# the written output. This module is the single source of truth for the
# sentinel string; the compiler imports it for the pending-sentinel guard.
_REGISTERED_URL_PLACEHOLDER = "registered://pending"


def registered(
    *,
    fragment: str | None = None,
    gen_config: str | None = None,
) -> Callable[[Callable[..., Any]], CallableRef]:
    """Decorator: mark a function as an operation registered in a gen_config.yaml.

    The decorated function is NEVER executed by the framework. Instead the
    decorator captures the *fragment* and *gen_config* onto a
    :class:`CallableRef`. The compile driver rewrites the task's
    componentRef URL to ``resolve://<rel-path>/gen_config.yaml#<fragment>``,
    computed relative to the compiled artifact's output directory. Hydrate
    then uses the hydrator's own ``resolve://`` resolver to read the
    gen_config.yaml fragment and inline the component spec.

    Unlike ``@task``, ``@registered`` references an EXISTING
    gen_config.yaml (the operation is registered/published elsewhere) and
    generates NO sidecar — so it takes no ``image`` / ``dependencies_from``
    (those live in the operation's own ``gen_config.yaml``
    ``local_from_python`` entry).

    Args:
        fragment: The top-level key in ``gen_config.yaml`` this operation
            is registered under. Used VERBATIM as the ``#fragment`` of the
            emitted ``resolve://`` URL. When omitted, defaults to the
            function name verbatim (no hyphenation). No compile-time
            fragment validation is performed in v1 — a wrong fragment
            surfaces at hydrate time.
        gen_config: Path to the ``gen_config.yaml`` the operation is
            registered in. Resolution (lazy, at compile time):

            - A genuinely remote value (``gs://…`` or ``http(s)://…``) is
              used VERBATIM; the hydrator fetches it directly.
            - A ``file://…`` URL or an absolute filesystem path is resolved
              to a local file, existence-checked at compile time, and
              emitted as a bare absolute ``resolve://`` path. The ``file://``
              scheme is STRIPPED (the hydrator's ``resolve://`` parser does
              not understand it, so ``resolve://file:///abs/...`` would not
              hydrate).
            - An explicit relative path is resolved against the nearest
              ancestor directory of the operation's source file that
              contains the ``oasis.pipeline_component_root.yaml`` zone-root
              marker.
            - When omitted, defaults to the NEAREST ancestor
              ``gen_config.yaml`` of the operation's source file.

    Returns:
        A decorator that, given the user's function, returns a
        :class:`CallableRef`. The returned ref:

        - Has ``url`` set to the ``registered://pending`` sentinel -- the
          compile driver rewrites it to
          ``resolve://<rel-path>/gen_config.yaml#<fragment>`` after tracing.
        - Has ``_registered_*`` metadata populated so the driver can
          resolve the gen_config.yaml and compute the fragment.
        - Behaves like any other ``ref()``-derived ref when called inside a
          ``@pipeline`` trace context.
    """

    def decorator(fn: Callable[..., Any]) -> CallableRef:
        # Capture the absolute path of the source file the user wrote the
        # function in. ``inspect.getfile`` raises TypeError for builtins /
        # dynamically-built functions; the @registered path requires a real
        # on-disk file because the gen_config.yaml resolution walks the
        # source file's ancestor directories.
        try:
            source_path = Path(inspect.getfile(fn)).resolve()
        except (TypeError, OSError) as exc:
            raise RuntimeError(
                f"@registered could not resolve the source file for {fn!r}: {exc}. "
                "@registered only supports functions defined in real .py files; the "
                "compiler walks the source file's directory to resolve the gen_config.yaml."
            ) from exc

        function_name = fn.__name__

        # The sentinel URL is set at decoration time. The compile driver
        # rewrites componentRef.url for @registered-derived refs to
        # ``resolve://<rel-path>/gen_config.yaml#<fragment>`` after tracing
        # (the relpath depends on the output path), so the placeholder
        # never reaches output. Still a pure ref -- no spec/text.
        ref_instance = CallableRef(
            url=_REGISTERED_URL_PLACEHOLDER,
            _registered_source_path=source_path,
            _registered_function_name=function_name,
            _registered_fragment=fragment,
            _registered_gen_config=gen_config,
        )

        # Expose function-like introspection so ``tangle_cli``'s
        # ``extract_interface`` can read signature/docstring directly off
        # the CallableRef. The SAME function is still published normally via
        # its ``gen_config.yaml`` ``local_from_python`` entry, whose
        # resolver does ``getattr(module, fn)`` -> ``inspect.signature``;
        # forwarding these dunders lets that path see the function through
        # the ref. (Identical to @task -- see task.py.)
        #
        # ``object.__setattr__`` bypasses the frozen-dataclass
        # ``__setattr__`` guard so we can add these attrs without mutating
        # any declared dataclass field.
        object.__setattr__(ref_instance, "__name__", function_name)
        object.__setattr__(ref_instance, "__qualname__", fn.__qualname__)
        object.__setattr__(ref_instance, "__module__", fn.__module__)
        object.__setattr__(ref_instance, "__doc__", fn.__doc__)
        object.__setattr__(ref_instance, "__wrapped__", fn)
        object.__setattr__(ref_instance, "__signature__", inspect.signature(fn))
        # Forward annotations too -- some introspection paths read
        # ``__annotations__`` directly rather than through signature.
        object.__setattr__(ref_instance, "__annotations__", dict(fn.__annotations__))

        return ref_instance

    return decorator
