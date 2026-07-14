"""Config loader: YAML → attribute-access object.

``Cfg`` is a pure, operation-agnostic attribute-access wrapper. It holds
NO operation-specific knowledge (no SQL, BigQuery table FQN building, or
other domain helpers): a pipeline that needs a fully-qualified BigQuery
table name (or any other domain-specific value) builds it in its own
pipeline-local helper code and passes the final string in as a constant.

Contract:

- ``cfg.<flat_key>`` returns the value (flat keys win over nested).
- ``cfg.<a>.<b>`` returns the nested value if ``a`` maps to a dict.
- Unknown keys raise :class:`UnknownCfgKeyError`.
- ``--override key=value`` pairs overlay file values (CLI wins).
- ``--override`` *values* are typed via ``yaml.safe_load`` (parity with
  file values, which already pass through ``yaml.safe_load``): so
  ``--override batch_size=100`` yields ``int 100`` (not ``"100"``) and
  ``--override dry_run=false`` yields ``bool False`` (not the truthy
  string ``"false"``). The one special case is an EMPTY value: ``key=``
  maps to ``""`` (an empty string), NOT ``None`` — a bare ``--override
  key=`` reads as "set to empty string". Authors who want an explicit
  null write ``--override key=null``. Because the same loader the config
  file uses is reused, the YAML 1.1 quirks are inherited and CONSISTENT
  with file values (e.g. ``yes/no/on/off`` -> bool, ``3:14`` -> 194,
  ``007`` -> 7, ``0x1F`` -> 31, ``1_000`` -> 1000, ``1.0`` -> float; but
  ``1e3`` stays a string). Quote to force a string: ``--override
  version='"1.0"'``.
- ``template_file:`` in the source is rejected — the Python authoring
  layer IS the template authoring layer.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from .errors import CompileError, UnknownCfgKeyError


def _coerce_override(value: str) -> Any:
    """Type a single ``--override key=value`` *value* string.

    Override values arrive from the CLI as raw strings (see
    ``pipeline_compiler._parse_overrides``). To give an override the SAME
    Python type the equivalent ``key: value`` line in ``config.yaml`` would
    (which is already typed via ``yaml.safe_load``), we run each value
    through the same loader.

    The one deviation from raw YAML is the EMPTY string: ``yaml.safe_load("")``
    is ``None``, but a bare ``--override key=`` reads as "set to empty
    string", so we keep ``""`` as ``""``. (An explicit null is available via
    ``--override key=null``.)
    """
    if value == "":
        return ""
    return yaml.safe_load(value)


class _CfgNested:
    """A nested mapping inside ``Cfg`` — same attribute-access semantics."""

    def __init__(self, data: dict[str, Any]) -> None:
        object.__setattr__(self, "_data", data)

    def __getattr__(self, key: str) -> Any:
        data: dict[str, Any] = object.__getattribute__(self, "_data")
        if key not in data:
            raise UnknownCfgKeyError(f"unknown config key: {key}")
        value = data[key]
        if isinstance(value, dict):
            return _CfgNested(value)
        return value


class Cfg:
    """Attribute-access wrapper around a flat-ish config dict.

    Read-only at runtime; immutability is not enforced (Python attribute
    semantics) but writes are not part of the public API.
    """

    def __init__(self, data: dict[str, Any]) -> None:
        # Use object.__setattr__ to avoid recursing through __getattr__.
        object.__setattr__(self, "_data", dict(data))

    # ------------------------------------------------------------------
    # Public attribute-access surface

    def __getattr__(self, key: str) -> Any:
        data: dict[str, Any] = object.__getattribute__(self, "_data")
        if key in data:
            value = data[key]
            if isinstance(value, dict):
                return _CfgNested(value)
            return value
        raise UnknownCfgKeyError(f"unknown config key: {key!r}. Available keys: " f"{sorted(data.keys())!r}")


def load_cfg(path: Path, overrides: Mapping[str, Any] | None = None, *, coerce: bool = True) -> Cfg:
    """Load YAML at ``path`` and overlay ``overrides`` on top.

    Two overlay paths, selected by ``coerce``:

    * ``coerce=True`` (the default, the CLI ``--override`` path): override
      *values* are raw strings typed via :func:`_coerce_override` (i.e.
      ``yaml.safe_load``), so an override yields the SAME Python type the
      equivalent line in ``config.yaml`` would: ``batch_size=100`` ->
      ``int 100``, ``dry_run=false`` -> ``bool False``, ``ratio=0.1`` ->
      ``float 0.1``, ``name=foo`` -> ``"foo"``. The empty value ``key=`` is
      special-cased to ``""`` (not ``None``); ``key=null`` -> ``None``. The
      same YAML 1.1 quirks the file loader has are inherited (and thus
      consistent); quote to force a string (``--override version='"1.0"'``).
    * ``coerce=False`` (the NATIVE pass-through path, used for programmatic
      ``.override_config`` / broadcast values): override values are
      already-typed Python natives and are overlaid AS-IS, NOT re-coerced
      through ``yaml.safe_load``. Re-coercion is correct only for raw CLI
      strings — it would, e.g., turn the string ``"no"`` into ``False`` or
      fail on a non-string value.

    See the module docstring for the full coercion contract.

    Args:
        path: Path to the user's config.yaml.
        overrides: Optional mapping of override pairs. Under ``coerce=True``
            these are raw ``--override key=value`` strings; under
            ``coerce=False`` they are already-typed native values. CLI /
            override wins on conflict.
        coerce: When ``True`` (default), values are coerced to their YAML
            type before overlaying. When ``False``, values pass through
            unchanged (native overlay).

    Returns:
        A :class:`Cfg` ready for attribute access.

    Raises:
        CompileError: if the config carries a ``template_file:`` key
            (the authoring layer emits the template itself; the source
            config must not point at a separate Jinja template).
        FileNotFoundError: if ``path`` doesn't exist.
    """
    raw = yaml.safe_load(Path(path).read_text()) or {}
    if not isinstance(raw, dict):
        raise CompileError(f"config at {path} must be a YAML mapping, got {type(raw).__name__}")
    if "template_file" in raw:
        raise CompileError(
            f"config at {path} has a top-level `template_file:` key, but "
            "the Python authoring layer emits the pipeline directly. Remove "
            "this key from your config.yaml — the framework is your "
            "templating layer."
        )
    merged = dict(raw)
    if overrides:
        if coerce:
            # Coerce each raw CLI override string to its YAML type so an
            # override behaves identically to the same key written in the
            # config file (which is already typed via yaml.safe_load above).
            # Coercion is internal to load_cfg only: the override dict stays
            # dict[str, str] everywhere upstream and the compile-cache
            # fingerprint keeps hashing the raw CLI strings.
            merged.update({k: _coerce_override(v) for k, v in overrides.items()})
        else:
            # Native pass-through: values are already-typed Python natives
            # (from .override_config / broadcast). Overlay verbatim — no
            # yaml.safe_load re-coercion (it would mangle e.g. "no" -> False).
            merged.update(dict(overrides))
    return Cfg(merged)
