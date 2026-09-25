"""Canonical ``editor.*`` layout keys, value formats and validation.

Layout is descriptive: it never affects execution, component digests or the
cache. Stdlib-only and outside :mod:`tangle_cli.python_pipeline` so the
layout command and the submit-time gate import it without the authoring
stack; callers inject their own ``error_cls``.
"""
from __future__ import annotations

import json
import math
from typing import Any

#: Task / graph-input / graph-output coordinates. The value is a JSON object
#: STRING (the editor stringifies and parses it), ``{"x", "y"}`` plus optional
#: ``"width"``/``"height"``; coordinates may be negative.
POSITION_ANNOTATION = "editor.position"

#: Root ``metadata.annotations`` rendering direction.
FLOW_DIRECTION_ANNOTATION = "editor.flow-direction"

#: Accepted ``editor.flow-direction`` values; ``top-to-bottom`` is legacy.
FLOW_DIRECTIONS: tuple[str, ...] = ("left-to-right", "top-to-bottom")


def position_annotation_value(
    x: float,
    y: float,
    *,
    width: float | None = None,
    height: float | None = None,
    error_cls: type[Exception] = ValueError,
) -> str:
    """Return the canonical ``editor.position`` value for ``x`` / ``y``.

    Key order is ``x, y, width, height``; absent dimensions are omitted
    rather than written as null.

    Raises:
        error_cls: If a coordinate is not a finite real number. Messages name
            the coordinate and its type, never the value.
    """

    position: dict[str, float] = {
        "x": _finite_number("x", x, error_cls),
        "y": _finite_number("y", y, error_cls),
    }
    if width is not None:
        position["width"] = _finite_number("width", width, error_cls)
    if height is not None:
        position["height"] = _finite_number("height", height, error_cls)
    return json.dumps(position)


def validate_flow_direction(
    value: Any, *, error_cls: type[Exception] = ValueError
) -> str:
    """Return ``value`` if it is a supported ``editor.flow-direction``.

    Raises:
        error_cls: If it is not one of :data:`FLOW_DIRECTIONS`.
    """

    if value in FLOW_DIRECTIONS:
        return str(value)
    allowed = ", ".join(repr(direction) for direction in FLOW_DIRECTIONS)
    raise error_cls(
        f"flow_direction must be one of {allowed}; got "
        f"{type(value).__name__}."
    )


def _finite_number(name: str, value: Any, error_cls: type[Exception]) -> float:
    # bool is an int subclass, and True would silently serialize as 1.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise error_cls(
            f"position coordinate {name!r} must be a real number; got "
            f"{type(value).__name__}."
        )
    if not math.isfinite(value):
        # json.dumps emits bare NaN / Infinity, which JSON.parse rejects.
        raise error_cls(f"position coordinate {name!r} must be finite.")
    return value
