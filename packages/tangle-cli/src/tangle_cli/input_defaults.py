"""Serialize a Python graph-input default into the string the schema wants.

``InputSpec.default`` is ``string | null``, so every default a pipeline
declares from Python has to be rendered as text. Callers inject ``error_cls``
and a field label; diagnostics name the field and the type, never the value.
"""
from __future__ import annotations

import json
import math
from typing import Any

# Types a default may be written as. Anything else is refused rather than
# guessed at: ``str(value)`` on an enum or a dataclass produces text no one
# meant to put in a pipeline document.
SUPPORTED_DEFAULT_TYPES = (str, bool, int, float, list, dict)

# Tangle type strings that pin a default to a Python type. Json and JsonArray
# are deliberately absent: their values are structural, not scalar.
_TANGLE_SCALARS: dict[str, type] = {
    "String": str,
    "Integer": int,
    "Float": float,
    "Boolean": bool,
}


def serialize_input_default(
    value: Any,
    *,
    field: str,
    declared_type: Any = None,
    error_cls: type[Exception] = ValueError,
) -> str | None:
    """Return the YAML form of ``value``, or ``None`` to write no default.

    ``declared_type`` is a Python type or a Tangle type string; when it names
    a builtin scalar the default's type must match it. A Tangle type string
    additionally accepts an already-rendered ``str``, which is the wire form
    and how every default in the corpus is written. Booleans render in Python
    spelling (``"True"``), matching ``@task`` component inputs and the corpus;
    containers render as JSON with sorted keys so the document is reproducible.
    """
    if value is None:
        return None
    if not isinstance(value, SUPPORTED_DEFAULT_TYPES):
        supported = ", ".join(t.__name__ for t in SUPPORTED_DEFAULT_TYPES)
        raise error_cls(
            f"{field} default must be one of {supported} or None; "
            f"got {type(value).__name__}. Render it yourself and pass a string."
        )
    _check_declared_type(value, declared_type, field=field, error_cls=error_cls)
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise error_cls(
                f"{field} default must be a finite number; "
                f"got a non-finite float. JSON has no spelling for it."
            )
        return str(value)
    if isinstance(value, int):
        return str(value)
    try:
        return json.dumps(value, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise error_cls(
            f"{field} default must be JSON-serializable; "
            f"{type(value).__name__} contains a value that is not."
        ) from exc


def _check_declared_type(
    value: Any,
    declared_type: Any,
    *,
    field: str,
    error_cls: type[Exception],
) -> None:
    expected = declared_type
    if isinstance(declared_type, str):
        expected = _TANGLE_SCALARS.get(declared_type)
        # A Tangle type string describes the wire form, where every value is
        # text. A pre-rendered default is therefore always in range, so
        # ported YAML keeps emitting exactly the bytes it did before.
        if isinstance(value, str):
            return
    if expected not in (str, bool, int, float):
        return

    actual = type(value)
    # bool is a subclass of int, so an unguarded isinstance would let
    # ``In[int] = True`` through and emit "True" for an Integer input.
    if expected is bool:
        ok = actual is bool
    elif expected is float:
        # A whole number written for a Float input is idiomatic and appears
        # in the corpus ("0", "72"); it renders as-is, not as "0.0".
        ok = actual is float or actual is int
    else:
        ok = actual is expected

    if not ok:
        raise error_cls(
            f"{field} is declared {expected.__name__} but its default is "
            f"{actual.__name__}. Declare the matching type or convert the default."
        )
