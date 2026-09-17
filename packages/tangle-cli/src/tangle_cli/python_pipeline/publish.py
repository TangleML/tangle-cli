"""``@Publish`` decorator -- an inert metadata stub.

This package deliberately contains no publication implementation. ``@Publish``
records a component name and version for a local ``@task``, and the compiler
writes the neutral marker shown below into the generated
``<stem>.components.yaml`` resolver sidecar, beside that component's
``local_from_python`` block; neither action publishes, contacts a registry, or
expresses publication policy, and decorating changes nothing about how the
component is generated. Some current consumers are closed source, though their
implementations are expected to be opened in the future. The marker schema is
the integration contract and is kept deliberately neutral so any
implementation can consume it::

    orders-loader:
      name: orders-loader
      version: "1.0"
      publisher: me
      local_from_python: {...}
      publish: true

Separate tooling reads ``publish`` and decides whether, when, and under what
policy to publish. The declaration is emitted as the entry's OWN generic
``name``/``version`` fields, so ordinary hydration looks the published
component up at the exact declared version and uses ``local_from_python`` when
no such component exists yet. A registry error is NOT a fallback: resolution
fails closed rather than quietly building from local source.

``publish`` is deliberately a separate literal ``true`` and is never inferred
from ``name`` + ``version`` + ``local_from_python``, so a hand-written or
fallback entry of that shape is not silently published.

``publisher`` is the SYMBOLIC value ``me``, matched exactly and
case-sensitively, and is resolved at hydration time to whoever is
authenticated; compiling stays offline and never learns or records an account
id. This keeps the lookup owner-scoped: an unscoped name search would let a
component published by someone else under the same name and version be
resolved in its place, and a name is not an identity control. If the account
cannot be determined, resolution fails closed rather than widening.

The symbol is interpreted only on an entry that also carries ``publish: true``,
which the compiler always writes together. ``me`` is therefore NOT a reserved
account id: in a hand-authored entry, and in any entry without the marker, a
publisher is used verbatim -- including one that happens to be ``me``. An entry
with no publisher keeps the ordinary cross-publisher behaviour.

Ordering: ``@Publish`` goes directly ABOVE ``@task``, so it receives the
``CallableRef`` that ``@task`` produced::

    from tangle_cli.python_pipeline import Publish, task

    @Publish(component_name="orders-loader", version="1.0")
    @task(image="python:3.11")
    def load_orders(source: str = "orders") -> str:
        '''Load orders.'''
        ...

The declaration is stored as REAL ``CallableRef`` fields, so it survives
fluent composition (``.bind()``, ``.named()``) and is carried by refs imported
from an already-loaded module. This decorator does not consult the registry,
read the filesystem, or perform any I/O.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .errors import CompileError
from .ref import CallableRef

_USAGE = (
    "@Publish(component_name='my-component', version='1.0') directly above "
    "the @task it publishes"
)


def _refuse(message: str) -> CompileError:
    return CompileError(f"@Publish {message}")


def _require_text(value: Any, *, field: str) -> str:
    """Validate one author-supplied string.

    Both values are echoed into diagnostics and written into a YAML document,
    so control characters are rejected at the authoring boundary rather than
    escaped at each point of use.
    """
    if not isinstance(value, str) or not value.strip():
        raise _refuse(f"requires a non-empty string {field!r}. Write {_USAGE}.")
    text = value.strip()
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in text):
        raise _refuse(f"{field!r} must not contain control characters.")
    return text


@dataclass(frozen=True)
class Publish:
    """Declare that the decorated ``@task`` component should be published.

    Args:
        component_name: Registry name to publish under. Required, non-empty,
            and never derived from the function name -- the registry name is a
            deliberate, stable identifier that must not change because someone
            renamed a Python function. A distinctive name is the author's
            responsibility.
        version: Version to publish. Required and non-empty; there is no
            default and none is inferred.

    Returns:
        The same :class:`CallableRef`, carrying the publication declaration.

    Raises:
        CompileError: If either value is missing, blank, or contains control
            characters, or if the decorated object is not a ``@task`` ref
            (a plain function, a ``ref()``/``@registered`` ref, or ``@task``
            applied in the wrong order).
    """

    component_name: str
    version: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "component_name", _require_text(self.component_name, field="component_name")
        )
        object.__setattr__(self, "version", _require_text(self.version, field="version"))

    def __call__(self, target: Any) -> CallableRef:
        if not isinstance(target, CallableRef) or target._task_source_path is None:
            raise _refuse(
                f"can only publish a @task component, but it was applied to "
                f"{type(target).__name__}. Write {_USAGE}."
            )
        if target._task_publish_name is not None:
            raise _refuse(
                f"is already declared on this task as "
                f"{target._task_publish_name!r} {target._task_publish_version}. "
                f"One publication per task."
            )
        return target._replace(
            _task_publish_name=self.component_name,
            _task_publish_version=self.version,
        )
