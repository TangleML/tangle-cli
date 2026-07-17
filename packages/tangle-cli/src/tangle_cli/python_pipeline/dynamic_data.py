"""Dynamic-data argument helpers for Python-authored pipelines.

Tangle runnable arguments can be literals, graph/task edges, or dynamic data
resolved by the runtime (for example a secret reference).  The Python pipeline
emitter needs an explicit wrapper so author code can request a dynamicData
argument without opening support for arbitrary dict constants.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class DynamicData:
    """A task argument value emitted as ``{"dynamicData": ...}``."""

    value: Mapping[str, Any]


def dynamic_secret(name: str) -> DynamicData:
    """Reference a runtime secret by name for a task argument.

    Example emitted YAML::

        openai_api_key:
          dynamicData:
            secret:
              name: OPENAI_API_KEY
    """
    if not isinstance(name, str) or not name:
        raise ValueError("dynamic_secret() requires a non-empty secret name string")
    return DynamicData({"secret": {"name": name}})
