"""CLI argument resolution with optional YAML/JSON config files.

This module provides generic config-file behavior shared by Tangle CLI
commands: load one or more config objects, merge each with parsed CLI
arguments, and keep explicit CLI values higher precedence than config values.

Precedence per field is CLI > config > environment (only for fields wrapped
in :class:`EnvField`) > default. Config values may themselves be read from
the environment with the ``{_env: NAME}`` value directive.

``TANGLE_ROOT_CONFIG`` may name a per-command root config: the entry for the
command passed as ``command=`` deep-merges beneath that command's ``--config``,
so precedence is CLI > ``--config`` > ``TANGLE_ROOT_CONFIG`` > environment >
default (see :func:`load_config`).

Scalar fields are typed strictly: a field whose default is a bool/int/float
(or whose spec names :func:`strict_bool` / :func:`strict_int` /
:func:`strict_float`) parses a config or environment string into that type
and rejects anything else, so ``"false"`` can never reach a flag as a truthy
string.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import difflib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, cast

import yaml

from tangle_cli.logger import Logger, get_default_logger
from tangle_cli.utils import apply_defaults

#: The only key reserved by the environment selector. It is an exact key name,
#: not a prefix: other ``_``-prefixed keys stay available for YAML anchors.
SELECT_KEY = "_select"

#: Key of the value directive ``{_env: NAME}`` / ``{_env: NAME, default: V}``.
#: Recognized only in value positions, never on a document or config-entry
#: mapping itself, so a top-level ``_env:`` helper/anchor key is unaffected.
ENV_KEY = "_env"
_ENV_DIRECTIVE_KEYS = (ENV_KEY, "default")
# Bounds recursion while rewriting a document that actually uses ``_env``.
_MAX_ENV_VALUE_DEPTH = 256

#: Environment variable naming the per-command root config file.
ROOT_CONFIG_ENV = "TANGLE_ROOT_CONFIG"
#: The one required top-level key of a TANGLE_ROOT_CONFIG document.
ROOT_COMMANDS_KEY = "commands"
#: Alternate command-path prefixes normalized to their canonical form.
COMMAND_ALIASES: Mapping[str, str] = {"tangle-cli": "tangle"}
# Bounds recursion while deep-merging TANGLE_ROOT_CONFIG beneath a command config.
_MAX_MERGE_DEPTH = 256
_MAX_RENDERED_PATH_LENGTH = 160

# Guards against recursive YAML aliases producing an endless selector chain.
_MAX_SELECT_DEPTH = 32
_ENV_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MAX_CASE_KEY_LENGTH = 128
_MAX_RENDERED_CASE_KEY_LENGTH = 64

#: ``(env_name, cases, default_branch, rendered_case_list)``.
_SelectorSummary = tuple[str, "dict[Any, Any]", Any, str]
#: ``id(node) -> (greatest_depth_validated, node_pin, summary)``. Built per
#: document load and thrown away with it; there is no global/cross-load cache.
_ValidationMemo = dict[int, tuple[int, Any, _SelectorSummary]]
#: Raw key/index segments from a document root to a value, rendered lazily.
_ConfigPath = tuple[Any, ...]


class ConfigFileError(Exception):
    """Raised when there is an error loading or resolving a config file."""


def _is_env_name(name: Any) -> bool:
    """Return whether *name* is a valid environment variable name.

    The single name rule shared by ``_select.env``, ``_env``, and
    :class:`EnvField`.
    """

    return isinstance(name, str) and bool(_ENV_NAME_PATTERN.match(name))


_ENV_NAME_RULE = "must be a valid environment variable name matching [A-Za-z_][A-Za-z0-9_]*"


def _scrub_config_key(key: Any) -> str:
    """Scrub and length-cap a config key without quoting it."""

    text = key if isinstance(key, str) else str(key)
    rendered = "".join(char if char.isprintable() else "?" for char in text)
    if len(rendered) > _MAX_RENDERED_CASE_KEY_LENGTH:
        rendered = rendered[: _MAX_RENDERED_CASE_KEY_LENGTH - 3] + "..."
    return rendered


def _render_config_key(key: Any) -> str:
    """Render a config-document key safely for a diagnostic message.

    Config keys come from user files, so a diagnostic must never echo them
    verbatim: non-printable characters are replaced and the result is length
    capped before being quoted. Accepts non-string keys (YAML permits them) so
    callers can report a bad key without first proving it is a string.
    """

    return repr(_scrub_config_key(key))


def _render_config_path(path: _ConfigPath) -> str:
    """Render a key path such as ``configs[0].token`` for a diagnostic.

    Every key is scrubbed like :func:`_render_config_key`; an overlong path
    keeps its tail, which is the part that locates the value.
    """

    rendered = ""
    for segment in path:
        if isinstance(segment, int) and not isinstance(segment, bool):
            rendered += f"[{segment}]"
        else:
            rendered += ("." if rendered else "") + _scrub_config_key(segment)
    rendered = rendered or "<document root>"
    if len(rendered) > _MAX_RENDERED_PATH_LENGTH:
        rendered = "..." + rendered[-(_MAX_RENDERED_PATH_LENGTH - 3) :]
    return rendered


def _stringify_env_default(value: Any, location: str) -> str:
    """Return the string form of an ``_env`` ``default`` scalar.

    A default is stringified so a field has one type whether or not the
    variable is set; downstream converters then type both paths identically.
    Numbers and booleans use their JSON spelling (``true``, ``5``, ``1.5``),
    so JSON-typed fields parse a default back to the authored value; dates use
    ISO format. ``null`` has no string form and is rejected rather than
    guessed at (quote ``''`` for an empty string).
    """

    if isinstance(value, str):
        return value
    if isinstance(value, (bool, int, float)):
        return json.dumps(value)
    if isinstance(value, datetime.date):
        return value.isoformat()
    type_name = "null" if value is None else type(value).__name__
    raise ConfigFileError(
        f"{ENV_KEY} at {location}: default must be a string, number, boolean, "
        f"or date scalar, got {type_name}"
    )


def _is_container(node: Any) -> bool:
    return isinstance(node, (dict, list))


def _parse_env_directive(node: dict[Any, Any], path: _ConfigPath) -> tuple[str, str | None]:
    """Validate one ``_env`` directive node; return ``(name, default)``.

    ``default`` is the stringified default, or ``None`` when none was
    authored. Reads no environment variable.
    """

    location = _render_config_path(path)
    unexpected = sorted(
        _render_config_key(key) for key in node if key not in _ENV_DIRECTIVE_KEYS
    )
    if unexpected:
        raise ConfigFileError(
            f"{ENV_KEY} at {location} allows only an optional 'default' beside it, "
            f"got unexpected keys: {', '.join(unexpected)}"
        )
    name = node[ENV_KEY]
    if not _is_env_name(name):
        raise ConfigFileError(f"{ENV_KEY} at {location} {_ENV_NAME_RULE}")
    default = _stringify_env_default(node["default"], location) if "default" in node else None
    return name, default


class _EnvValueResolver:
    """Replace ``_env`` directives in config values with environment strings.

    Copy-on-write: a container without directives is returned as the same
    object, and each shared (YAML alias) node is resolved once. A directive
    reached through a recursive alias cannot be represented and is rejected.
    Diagnostics name the variable and key path, never a value.
    """

    def __init__(self, context: str) -> None:
        self._context = context
        self._memo: dict[int, Any] = {}
        self._in_progress: set[int] = set()
        self._back_refs: set[int] = set()

    def resolve_config_object(self, obj: dict[str, Any], path: _ConfigPath) -> dict[str, Any]:
        """Resolve every value of one config entry; the entry itself is not a value."""

        resolved: dict[str, Any] = {}
        changed = False
        for key, value in obj.items():
            new_value = self._resolve_value(value, (*path, key), 1)
            resolved[key] = new_value
            changed = changed or new_value is not value
        return resolved if changed else obj

    def _resolve_value(self, node: Any, path: _ConfigPath, depth: int) -> Any:
        if not _is_container(node):
            return node
        node_id = id(node)
        if node_id in self._memo:
            return self._memo[node_id]
        if node_id in self._in_progress:
            self._back_refs.add(node_id)
            return node
        if depth > _MAX_ENV_VALUE_DEPTH:
            raise ConfigFileError(
                f"Config nesting at {_render_config_path(path)} exceeds "
                f"{_MAX_ENV_VALUE_DEPTH} levels in a document that uses {ENV_KEY}"
            )

        result: Any
        if isinstance(node, dict) and ENV_KEY in node:
            result = self._lookup(cast(dict[Any, Any], node), path)
        else:
            self._in_progress.add(node_id)
            try:
                result = self._resolve_container(node, path, depth)
            finally:
                self._in_progress.discard(node_id)
            if node_id in self._back_refs and result is not node:
                raise ConfigFileError(
                    f"{ENV_KEY} cannot be used inside a recursive YAML alias "
                    f"(at {_render_config_path(path)})"
                )
        self._memo[node_id] = result
        return result

    def _resolve_container(self, node: Any, path: _ConfigPath, depth: int) -> Any:
        changed = False
        if isinstance(node, dict):
            mapping = cast(dict[Any, Any], node)
            resolved_dict: dict[Any, Any] = {}
            for key, value in mapping.items():
                new_value = self._resolve_value(value, (*path, key), depth + 1)
                resolved_dict[key] = new_value
                changed = changed or new_value is not value
            return resolved_dict if changed else mapping
        items = cast(list[Any], node)
        resolved_list: list[Any] = []
        for index, value in enumerate(items):
            new_value = self._resolve_value(value, (*path, index), depth + 1)
            resolved_list.append(new_value)
            changed = changed or new_value is not value
        return resolved_list if changed else items

    def _lookup(self, node: dict[Any, Any], path: _ConfigPath) -> str:
        name, default = _parse_env_directive(node, path)
        # An empty string is a set variable.
        if name in os.environ:
            return os.environ[name]
        if default is not None:
            return default
        raise ConfigFileError(
            f"Environment variable {name} is required by {ENV_KEY} at "
            f"{_render_config_path(path)}{self._context} but is not set"
        )


@dataclass(frozen=True)
class EnvField:
    """Opt one :meth:`ArgsContainer.load` field spec into the environment tier.

    ``token=EnvField("TANGLE_TOKEN", (cli_token, None))`` wraps any ordinary
    tuple *spec* unchanged; the field then resolves CLI > config >
    ``os.environ[env]`` > default. The raw string (empty counts as set) goes
    through the spec's usual converter. No field reads the environment unless
    wrapped, and no name is derived automatically.
    """

    env: str
    spec: tuple[Any, ...]

    def __post_init__(self) -> None:
        if not _is_env_name(self.env):
            raise ValueError(f"EnvField.env {_ENV_NAME_RULE}")
        spec: Any = self.spec
        if not isinstance(spec, tuple) or not 1 <= len(cast(tuple[Any, ...], spec)) <= 6:
            raise ValueError("EnvField.spec must be an ArgsContainer field-spec tuple")


class _ScalarValueError(ConfigFileError):
    """A strict scalar converter's value-free rejection reason."""


#: The only accepted boolean spellings, matched case-insensitively and untrimmed.
_BOOL_STRINGS = {"true": True, "false": False, "yes": True, "no": False, "1": True, "0": False}
_INT_PATTERN = re.compile(r"[+-]?[0-9]+")
_FLOAT_PATTERN = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")


def strict_bool(value: Any) -> bool:
    """Field converter: a bool, the integer 0/1, or one of ``true``/``false``,
    ``yes``/``no``, ``1``/``0`` (case-insensitive). Anything else -- the empty
    string included -- is rejected without echoing the value."""

    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.isascii() and value.lower() in _BOOL_STRINGS:
        return _BOOL_STRINGS[value.lower()]
    raise _ScalarValueError("expected a boolean: true/false, yes/no, or 1/0 (case-insensitive)")


def strict_int(value: Any) -> int:
    """Field converter: an int, or a string of ASCII digits with an optional sign."""

    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and _INT_PATTERN.fullmatch(value):
        return int(value)
    raise _ScalarValueError("expected an integer")


def strict_float(value: Any) -> float:
    """Field converter: an int/float, or a decimal string (optional exponent).

    ``nan``/``inf`` spellings are rejected.
    """

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and _FLOAT_PATTERN.fullmatch(value):
        return float(value)
    raise _ScalarValueError("expected a number")


_STRICT_SCALARS: dict[type, Callable[[Any], Any]] = {
    bool: strict_bool,
    int: strict_int,
    float: strict_float,
}


def _direct_env_sources(entry: dict[str, Any]) -> dict[str, str]:
    """Map each top-level key whose value is an ``_env`` directive to its variable."""

    sources: dict[str, str] = {}
    for key, value in entry.items():
        if isinstance(value, dict) and ENV_KEY in value:
            name = cast(dict[Any, Any], value)[ENV_KEY]
            if _is_env_name(name):
                sources[key] = name
    return sources


class ArgsContainer:
    """Container for resolved CLI arguments with config-file defaults."""

    #: Instance attributes that are bookkeeping, not resolved fields.
    _PRIVATE_ATTRS = ("_config", "_config_sources", "_field_config_keys")

    def __init__(
        self,
        resolved: dict[str, Any],
        raw_config: dict[str, Any],
        config_sources: dict[Any, Path] | None = None,
        field_config_keys: dict[str, Any] | None = None,
    ):
        self._config = raw_config
        self._config_sources = dict(config_sources or {})
        self._field_config_keys = dict(field_config_keys or {})
        for key, value in resolved.items():
            setattr(self, key, value)

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(f"'{type(self).__name__}' has no attribute '{name}'")

    def get(self, key: str, cli_value: Any = None, cli_default: Any = None) -> Any:
        """Return a resolved value while preserving explicit CLI precedence."""

        if cli_value != cli_default:
            return cli_value
        if key in self._config:
            return self._config[key]
        return cli_value

    def to_dict(self) -> dict[str, Any]:
        """Return resolved public values as a dictionary."""

        return {
            key: value
            for key, value in vars(self).items()
            if key not in ArgsContainer._PRIVATE_ATTRS
        }

    def config_source(self, name: str) -> Path | None:
        """Return the config file that supplied field *name*, else ``None``.

        For resolving a relative path value against the directory of the file
        it was written in: a value inherited from ``TANGLE_ROOT_CONFIG`` names the
        root file, a value from ``--config`` names that file. ``None`` when
        the value came from the CLI, the environment tier, or the default.
        Granularity is the top-level config key.
        """

        if name not in self._field_config_keys:
            return None
        return self._config_sources.get(self._field_config_keys[name])

    @staticmethod
    def _validate_branch_document(
        branch: Any,
        where: str,
        depth: int = 0,
        memo: _ValidationMemo | None = None,
        *,
        mapping_only: bool = False,
    ) -> None:
        """Validate a selector branch as a complete config document root.

        Mirrors the shape and cardinality rules :meth:`_load_config_file`
        applies to a whole document, so a branch is rejected while the selector
        is validated rather than later during load. A branch whose root is
        another ``_select`` is structurally validated recursively, bounded by
        the same maximum nesting depth. No environment variable is read here:
        dormant branches are shape-checked but never impose their own
        environment requirements. Command-specific field names and types stay
        the business of the selected branch, later.

        *memo* makes YAML alias DAGs linear instead of exponential; see
        :meth:`_validate_selector_node`. With *mapping_only* (pipeline ``cfg``
        documents) every branch must be a mapping and ``_defaults``/``configs``
        carry no meaning.
        """

        if mapping_only and not isinstance(branch, dict):
            raise ConfigFileError(f"{where} must be a mapping, got {type(branch).__name__}")
        if isinstance(branch, dict):
            branch_dict = cast(dict[Any, Any], branch)
            if SELECT_KEY in branch_dict:
                if depth + 1 >= _MAX_SELECT_DEPTH:
                    raise ConfigFileError(
                        f"{SELECT_KEY} nesting exceeded the maximum depth "
                        f"of {_MAX_SELECT_DEPTH}"
                    )
                ArgsContainer._validate_selector_node(
                    branch_dict, depth + 1, memo, mapping_only=mapping_only
                )
                return
            if "configs" in branch_dict and not mapping_only:
                defaults = branch_dict.get("_defaults", {})
                if not isinstance(defaults, dict):
                    raise ConfigFileError(
                        f"{where}: _defaults must be an object, got {type(defaults).__name__}"
                    )
                configs_list = branch_dict.get("configs")
                if not isinstance(configs_list, list):
                    raise ConfigFileError(
                        f"{where}: configs must be a list, got {type(configs_list).__name__}"
                    )
                for index, item in enumerate(cast(list[Any], configs_list)):
                    if not isinstance(item, dict):
                        raise ConfigFileError(
                            f"{where}: configs entry {index} must be an object, "
                            f"got {type(item).__name__}"
                        )
            return

        if isinstance(branch, list):
            for index, item in enumerate(cast(list[Any], branch)):
                if not isinstance(item, dict):
                    raise ConfigFileError(
                        f"{where}: entry {index} must be an object, got {type(item).__name__}"
                    )
            return

        raise ConfigFileError(
            f"{where} must be an object or a list, got {type(branch).__name__}"
        )

    @staticmethod
    def _validate_selector_node(
        node: dict[str, Any],
        depth: int = 0,
        memo: _ValidationMemo | None = None,
        *,
        mapping_only: bool = False,
    ) -> _SelectorSummary:
        """Structurally validate one ``_select`` node without reading the env.

        Validates the node's siblings, the selector shape, the environment
        variable *name*, every case key, and every immediate case and
        ``default`` branch as a complete config document root -- recursing
        through nested selectors. Returns ``(env_name, cases, default_branch,
        rendered_case_list)`` for the caller to resolve against the
        environment. ``default_branch`` is ``None`` when no ``default`` was
        authored, which is unambiguous because a valid branch is never ``None``.

        YAML anchors let one selector node be reached through many edges, so
        *memo* records the greatest depth at which each node already validated
        cleanly. Validating at some depth proves the subtree fits the depth
        budget from there, and a shallower position only has more budget, so a
        node seen again at that depth or shallower can be skipped. That turns
        an alias DAG from exponential into linear work while leaving the depth
        cap and cycle behavior intact: a true alias cycle keeps descending to
        strictly greater depths, is never memo-skipped, and still trips the cap.
        The memo also pins each node object, so an ``id()`` cannot be recycled
        mid-validation.
        """

        node_id = id(node)
        if memo is not None:
            seen = memo.get(node_id)
            if seen is not None and depth <= seen[0]:
                return seen[2]

        # Every key reaching a diagnostic goes through the ONE capped/scrubbed
        # renderer: these keys are user input, and an unbounded repr() would
        # let a hostile document paste control characters or a wall of text
        # into a compile/CI log.
        siblings = sorted(
            _render_config_key(key)
            for key in node
            if not (isinstance(key, str) and key.startswith("_"))
        )
        if siblings:
            raise ConfigFileError(
                f"{SELECT_KEY} must be the only regular key at its level; "
                f"remove or underscore-prefix: {', '.join(siblings)}"
            )

        selector = node[SELECT_KEY]
        if not isinstance(selector, dict):
            raise ConfigFileError(
                f"{SELECT_KEY} must be an object, got {type(selector).__name__}"
            )
        selector_dict = cast(dict[Any, Any], selector)

        unexpected = sorted(
            _render_config_key(key)
            for key in selector_dict
            if key not in ("env", "cases", "default")
        )
        if unexpected:
            raise ConfigFileError(
                f"{SELECT_KEY} supports only 'env', 'cases', and 'default', "
                f"got unexpected keys: {', '.join(unexpected)}"
            )
        if "env" not in selector_dict:
            raise ConfigFileError(f"{SELECT_KEY} requires an 'env' environment variable name")
        if "cases" not in selector_dict:
            raise ConfigFileError(f"{SELECT_KEY} requires a 'cases' object")

        env_name = selector_dict["env"]
        if not _is_env_name(env_name):
            raise ConfigFileError(f"{SELECT_KEY}.env {_ENV_NAME_RULE}")

        cases = selector_dict["cases"]
        if not isinstance(cases, dict):
            raise ConfigFileError(
                f"{SELECT_KEY}.cases must be an object, got {type(cases).__name__}"
            )
        cases_dict = cast(dict[Any, Any], cases)
        if not cases_dict:
            raise ConfigFileError(f"{SELECT_KEY}.cases must define at least one case")

        for index, (case_key, case_value) in enumerate(cases_dict.items()):
            if (
                not isinstance(case_key, str)
                or not case_key
                or len(case_key) > _MAX_CASE_KEY_LENGTH
                or not case_key.isprintable()
            ):
                raise ConfigFileError(
                    f"{SELECT_KEY}.cases keys must be non-empty printable strings of at most "
                    f"{_MAX_CASE_KEY_LENGTH} characters (case {index} is invalid)"
                )
            # Every branch document is shape-checked, not just the selected one,
            # so a malformed selector fails identically in every environment.
            ArgsContainer._validate_branch_document(
                case_value,
                f"{SELECT_KEY} case {_render_config_key(case_key)}",
                depth,
                memo,
                mapping_only=mapping_only,
            )

        default_branch: Any = None
        if "default" in selector_dict:
            default_branch = selector_dict["default"]
            ArgsContainer._validate_branch_document(
                default_branch, f"{SELECT_KEY}.default", depth, memo, mapping_only=mapping_only
            )

        allowed = ", ".join(_render_config_key(key) for key in sorted(cases_dict))
        summary: _SelectorSummary = (env_name, cases_dict, default_branch, allowed)
        if memo is not None:
            previous = memo.get(node_id)
            if previous is None or depth > previous[0]:
                memo[node_id] = (depth, node, summary)
        return summary

    @staticmethod
    def _select_branch(
        node: dict[str, Any],
        memo: _ValidationMemo | None = None,
        *,
        mapping_only: bool = False,
    ) -> Any:
        """Validate one ``_select`` node and return the branch chosen by the env.

        The whole selector shape, the environment variable name, every
        configured case key, and every immediate case and ``default`` branch --
        including nested selectors, recursively -- are validated before the
        environment is read, so a malformed selector fails identically in every
        environment. Only *environment lookups* are lazy: a dormant branch is
        shape-checked but never requires its own variable to be set.
        Selection is exact and case sensitive: the raw ``os.environ`` value is
        never trimmed, case folded, interpolated, or echoed back.

        Fallback exists only when authored explicitly. With a ``default``
        branch, an unset variable or an unmatched value resolves to it; an
        exact case match always wins over it. Without ``default``, both stay
        hard errors -- there is no implicit default and no implicit production.
        """

        env_name, cases_dict, default_branch, allowed = ArgsContainer._validate_selector_node(
            node, 0, memo, mapping_only=mapping_only
        )

        # Environment is read only after the selector itself is known to be valid.
        if env_name not in os.environ:
            if default_branch is not None:
                return default_branch
            raise ConfigFileError(
                f"Environment variable {env_name} is required by {SELECT_KEY} but is not set; "
                f"configured cases: {allowed}"
            )
        env_value = os.environ[env_name]
        if env_value not in cases_dict:
            if default_branch is not None:
                return default_branch
            raise ConfigFileError(
                f"Environment variable {env_name} does not match any configured "
                f"{SELECT_KEY} case; configured cases: {allowed}"
            )

        return cases_dict[env_value]

    @staticmethod
    def _resolve_select(
        parsed: Any,
        memo: _ValidationMemo | None = None,
        *,
        mapping_only: bool = False,
    ) -> Any:
        """Replace a root ``_select`` node with its selected config branch.

        A selected branch is a complete config document in its own right, so it
        may itself be another root ``_select`` node (multi-dimensional
        selection). Documents without ``_select`` are returned unchanged.

        The whole selector tree is structurally validated up front by
        :meth:`_select_branch`; this loop only walks the chain the environment
        actually selects, and keeps its own bound as a belt-and-braces guard.
        The validation memo lives for exactly this one document resolution; a
        caller may pass the memo it already used to validate this document.
        """

        memo = {} if memo is None else memo
        node = parsed
        selections = 0
        while isinstance(node, dict) and SELECT_KEY in cast(dict[Any, Any], node):
            if selections >= _MAX_SELECT_DEPTH:
                raise ConfigFileError(
                    f"{SELECT_KEY} nesting exceeded the maximum depth of {_MAX_SELECT_DEPTH}"
                )
            node = ArgsContainer._select_branch(
                cast(dict[str, Any], node), memo, mapping_only=mapping_only
            )
            selections += 1
        return node

    @staticmethod
    def _validate_env_directives(
        document: Any, *, mapping_only: bool = False
    ) -> tuple[bool, list[Any]]:
        """Structurally validate every ``_env`` directive; read no variable.

        Walks the whole raw document -- every ``_select`` case and ``default``
        branch (dormant ones included) and helper sections -- so a malformed
        directive fails identically in every environment. Selector structure
        must already be validated. Shared nodes are visited once per role.
        Returns whether any directive exists, and every candidate document
        root (each non-selector branch, dormant ones included).
        """

        found = False
        branches: list[Any] = []
        seen: set[tuple[str, int]] = set()
        stack: list[tuple[str, Any, _ConfigPath]] = [("document", document, ())]

        def push(role: str, items: list[tuple[Any, _ConfigPath]]) -> None:
            # Reversed so diagnostics follow document order.
            stack.extend((role, node, path) for node, path in reversed(items))

        while stack:
            role, node, path = stack.pop()
            if not _is_container(node) or (role, id(node)) in seen:
                continue
            seen.add((role, id(node)))
            if isinstance(node, list):
                child_role = "value" if role == "value" else "entry"
                if role != "entry":
                    items = cast(list[Any], node)
                    push(child_role, [(item, (*path, i)) for i, item in enumerate(items)])
                continue

            node_dict = cast(dict[Any, Any], node)
            if role == "document" and SELECT_KEY not in node_dict:
                branches.append(node_dict)
            if role == "value" and ENV_KEY in node_dict:
                _parse_env_directive(node_dict, path)
                found = True
            elif role == "document" and SELECT_KEY in node_dict:
                selector = cast(dict[Any, Any], node_dict[SELECT_KEY])
                helpers = [(v, (*path, k)) for k, v in node_dict.items() if k != SELECT_KEY]
                candidates = [
                    (branch, (*path, SELECT_KEY, "cases", case))
                    for case, branch in cast(dict[Any, Any], selector["cases"]).items()
                ]
                if "default" in selector:
                    candidates.append((selector["default"], (*path, SELECT_KEY, "default")))
                push("document", candidates)
                push("value", helpers)
            elif role == "document" and "configs" in node_dict and not mapping_only:
                for key, value in reversed(list(node_dict.items())):
                    if key == "_defaults":
                        push("entry", [(value, (*path, key))])
                    elif key == "configs" and isinstance(value, list):
                        push("document", [(value, (*path, key))])
                    else:
                        push("value", [(value, (*path, key))])
            else:
                # A config entry (or a plain-object document): its values are values.
                push("value", [(v, (*path, k)) for k, v in node_dict.items()])
        return found, branches

    @staticmethod
    def _load_config_file(
        config_path: str | Path | None,
        logger: Logger | None = None,
    ) -> list[dict[str, Any]]:
        """Load a YAML/JSON config file as a list of config dictionaries.

        See :meth:`_load_config_entries`, which this wraps without provenance.
        """

        return [entry for entry, _ in ArgsContainer._load_config_entries(config_path, logger)]

    @staticmethod
    def _parse_config_file(path: Path) -> Any:
        """Parse one YAML (``.yaml``/``.yml``) or JSON file; ``None`` for empty YAML."""

        try:
            with path.open(encoding="utf-8") as f:
                if path.suffix in (".yaml", ".yml"):
                    return yaml.safe_load(f)
                return json.load(f)
        except (OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
            raise ConfigFileError(f"Error loading config file: {exc}") from exc

    @staticmethod
    def _load_config_entries(
        config_path: str | Path | None,
        logger: Logger | None = None,
    ) -> list[tuple[dict[str, Any], dict[str, str]]]:
        """Load a YAML/JSON config file as ``(config, env_sources)`` pairs.

        ``env_sources`` maps each top-level key read through an ``_env``
        directive to its variable name, for diagnostics only.

        Supported shapes are a single object, a list of objects, or an object
        with ``_defaults`` and ``configs`` where defaults are applied to each
        config entry. Other top-level keys are ignored, which lets YAML files
        use anchors/shared helper sections.

        A document may instead select one of those shapes at load time with a
        top-level ``_select`` node (see :meth:`_select_branch`).

        ``{_env: NAME}`` / ``{_env: NAME, default: V}`` value directives are
        then replaced by the variable's string value. Every directive is shape
        checked first, but only those in the selected document's config
        entries (and ``_defaults``) are looked up.

        This loader reads exactly one file and never consults ``TANGLE_ROOT_CONFIG``;
        see :func:`load_config` for that.
        """

        log = logger or get_default_logger()
        if config_path is None:
            return [({}, {})]

        path = Path(config_path)
        if not path.exists():
            raise ConfigFileError(f"Config file not found: {config_path}")

        parsed = ArgsContainer._parse_config_file(path)
        if parsed is None:
            return [({}, {})]

        resolution = resolve_config_document(parsed, path)
        parsed = resolution.document

        def resolve_entry(
            entry: dict[str, Any], entry_path: _ConfigPath
        ) -> tuple[dict[str, Any], dict[str, str]]:
            return resolution.resolve_entry(entry, entry_path), _direct_env_sources(entry)

        if isinstance(parsed, dict):
            parsed_dict = cast(dict[str, Any], parsed)
            if "configs" in parsed_dict:
                defaults = parsed_dict.get("_defaults", {})
                configs_list = parsed_dict.get("configs", [])
                if not isinstance(defaults, dict):
                    raise ConfigFileError(
                        f"_defaults must be an object, got {type(defaults).__name__}"
                    )
                if not isinstance(configs_list, list):
                    raise ConfigFileError(
                        f"configs must be a list, got {type(configs_list).__name__}"
                    )
                for index, item in enumerate(configs_list):
                    if not isinstance(item, dict):
                        raise ConfigFileError(
                            "configs entry "
                            f"{index} must be an object, got {type(item).__name__}"
                        )
                defaults, defaults_sources = resolve_entry(
                    cast(dict[str, Any], defaults), ("_defaults",)
                )
                entries = [
                    resolve_entry(item, ("configs", index))
                    for index, item in enumerate(cast(list[dict[str, Any]], configs_list))
                ]
                merged = apply_defaults([entry for entry, _ in entries], defaults)
                assert isinstance(merged, list)
                log.info(f"Loaded config: {path} ({len(merged)} configs with defaults)")
                return [
                    (
                        merged_entry,
                        {
                            **{k: v for k, v in defaults_sources.items() if k not in entry},
                            **sources,
                        },
                    )
                    for merged_entry, (entry, sources) in zip(merged, entries, strict=True)
                ]
            log.info(f"Loaded config: {path} (1 config)")
            return [resolve_entry(parsed_dict, ())]

        if isinstance(parsed, list):
            for index, item in enumerate(cast(list[Any], parsed)):
                if not isinstance(item, dict):
                    raise ConfigFileError(
                        "Config file entry "
                        f"{index} must be an object, got {type(item).__name__}"
                    )
            configs = [
                resolve_entry(item, (index,))
                for index, item in enumerate(cast(list[dict[str, Any]], parsed))
            ]
            log.info(f"Loaded config: {path} ({len(configs)} configs)")
            return configs

        raise ConfigFileError(
            "Config file must contain an object or list of objects, "
            f"got {type(parsed).__name__}"
        )

    @staticmethod
    def _make_json_converter(field_name: str) -> Callable[[Any], Any]:
        """Create a converter that accepts parsed JSON or JSON text."""

        def convert(value: Any) -> Any:
            if value is None:
                return None
            if isinstance(value, (dict, list)):
                return cast(Any, value)
            if isinstance(value, str):
                if value in ("", "{}", "[]", "null"):
                    return None
                try:
                    return json.loads(value)
                except json.JSONDecodeError as exc:
                    raise ConfigFileError(f"Invalid JSON for {field_name}: {exc}") from exc
            raise ConfigFileError(
                f"{field_name} must be a dict, list, or JSON string, "
                f"got {type(value).__name__}"
            )

        return convert

    @staticmethod
    def _make_enum_converter(field_name: str, enum_type: type[Enum]) -> Callable[[Any], Any]:
        """Create a converter that accepts enum values by string.

        The rejected value is not echoed (it may come from the environment).
        """

        def convert(value: Any) -> Any:
            if isinstance(value, str):
                try:
                    return enum_type(value)
                except ValueError:
                    valid_values = [member.value for member in enum_type]
                    raise ConfigFileError(
                        f"Invalid value for {field_name}. Valid values: {valid_values}"
                    ) from None
            return value

        return convert

    @staticmethod
    def _resolve(
        config: dict[str, Any],
        env_sources: dict[str, str] | None = None,
        loaded: LoadedConfig | None = None,
        /,
        **kwargs: Any,
    ) -> ArgsContainer:
        """Resolve CLI args against a single config dict.

        Field specs can be:
        - ``(cli_value,)``: required field, config key is parameter name;
        - ``(cli_value, default)``: optional field;
        - ``(cli_value, default, converter)``: optional with converter;
        - ``(config_key, cli_value, default, is_json)``: explicit key;
        - ``(config_key, cli_value, default, is_json, required)``;
        - ``(config_key, cli_value, default, is_json, required, converter)``;
        - :class:`EnvField` wrapping any of the above to add the env tier.

        Precedence: an explicit CLI value (one differing from the spec
        default), then the config key, then the ``EnvField`` variable, then
        the CLI/default value.

        Without a converter, a bool/int/float default types the field: config
        and environment values go through :func:`strict_bool` /
        :func:`strict_int` / :func:`strict_float` (CLI values are already
        typed by the parser). A rejection names the field and its source --
        config key or variable -- never the value. *env_sources* maps config
        keys read through ``_env`` to their variables, for those messages.
        """

        resolved: dict[str, Any] = {}
        field_config_keys: dict[str, Any] = {}
        required_fields: list[str] = []
        env_names: dict[str, str] = {}

        for param_name, spec in kwargs.items():
            env_name: str | None = None
            if isinstance(spec, EnvField):
                env_name = spec.env
                env_names[param_name] = env_name
                spec = spec.spec
            converter = None
            default_value = None
            if len(spec) == 1:
                (cli_value,) = spec
                config_key = param_name
                required_fields.append(param_name)
            elif len(spec) == 2:
                cli_value, default_value = spec
                config_key = param_name
            elif len(spec) == 3:
                cli_value, default_value, converter = spec
                config_key = param_name
            elif len(spec) == 4:
                config_key, cli_value, default_value, is_json = spec
                if is_json:
                    converter = ArgsContainer._make_json_converter(param_name)
            elif len(spec) == 5:
                config_key, cli_value, default_value, is_json, required = spec
                if is_json:
                    converter = ArgsContainer._make_json_converter(param_name)
                if required:
                    required_fields.append(param_name)
            else:
                config_key, cli_value, default_value, is_json, required, converter = spec
                if is_json:
                    converter = ArgsContainer._make_json_converter(param_name)
                if required:
                    required_fields.append(param_name)

            inferred_scalar = False
            if converter is None and isinstance(default_value, Enum):
                converter = ArgsContainer._make_enum_converter(param_name, type(default_value))
            elif converter is None and type(default_value) in _STRICT_SCALARS:
                converter = _STRICT_SCALARS[type(default_value)]
                inferred_scalar = True

            if cli_value is not None and cli_value != default_value:
                value, origin = cli_value, "cli"
            elif config_key in config:
                value, origin = config[config_key], "config"
            elif env_name is not None and env_name in os.environ:
                value, origin = os.environ[env_name], f"env:{env_name}"
            else:
                value, origin = cli_value, "default"

            # An inferred type only parses config/env values; CLI values are typed.
            if converter and value is not None and not (inferred_scalar and origin in ("cli", "default")):
                from_env_tier = env_name is not None and origin == f"env:{env_name}"
                try:
                    value = converter(value)
                except _ScalarValueError as exc:
                    source = ArgsContainer._describe_source(
                        origin, config_key, env_name, env_sources or {}
                    )
                    raise ConfigFileError(
                        f"Invalid value for {param_name} from {source}: {exc}"
                    ) from None
                except (ConfigFileError, ValueError, TypeError):
                    if not from_env_tier:
                        raise
                    # A converter message may quote the raw value.
                    raise ConfigFileError(
                        f"Invalid value for {param_name} from environment "
                        f"variable {env_name}"
                    ) from None
            resolved[param_name] = value
            if origin == "config":
                field_config_keys[param_name] = config_key

        for field_name in required_fields:
            if resolved.get(field_name) is None:
                if field_name in env_names:
                    raise ConfigFileError(
                        f"{field_name} is required (via CLI argument, config file, "
                        f"or environment variable {env_names[field_name]})"
                    )
                raise ConfigFileError(
                    f"{field_name} is required (via CLI argument or config file)"
                )

        if loaded is None:
            return ArgsContainer(resolved, config, field_config_keys=field_config_keys)
        return ArgsContainer(
            resolved,
            config,
            config_sources=loaded.sources,
            field_config_keys=field_config_keys,
        )

    @staticmethod
    def _describe_source(
        origin: str, config_key: Any, env_name: str | None, env_sources: dict[str, str]
    ) -> str:
        """Name where a rejected value came from, without the value."""

        if origin == "config":
            key = _render_config_key(config_key)
            if config_key in env_sources:
                return (
                    f"environment variable {env_sources[config_key]} "
                    f"({ENV_KEY} at config key {key})"
                )
            return f"config key {key}"
        if env_name is not None and origin == f"env:{env_name}":
            return f"environment variable {env_name}"
        return "the CLI argument" if origin == "cli" else "the default"

    @staticmethod
    def load(
        config_path: str | Path | None,
        logger: Logger | None = None,
        *,
        command: str | None = None,
        **kwargs: Any,
    ) -> list[ArgsContainer]:
        """Load a config file and resolve CLI args against each config entry.

        *command* is the running command's full path (e.g. ``"tangle sdk
        secrets delete"``); its ``TANGLE_ROOT_CONFIG`` entry, if any, is layered
        beneath the config (see :func:`load_config`). Without it the root
        config is ignored.
        """

        return [
            ArgsContainer._resolve(entry.values, entry.env_sources, entry, **kwargs)
            for entry in load_config(config_path, logger, command=command)
        ]


@dataclass(frozen=True)
class LoadedConfig:
    """One resolved config entry, layered over ``TANGLE_ROOT_CONFIG``.

    ``values`` is the merged mapping a command sees. ``sources`` maps each
    top-level key to the file that supplied it (``--config`` for any key it
    defines, else the ``TANGLE_ROOT_CONFIG`` file), for resolving relative paths
    against the right directory.
    """

    values: dict[str, Any]
    sources: dict[Any, Path] = field(default_factory=lambda: {})
    env_sources: dict[str, str] = field(default_factory=lambda: {}, repr=False)


def _deep_merge(
    base: dict[Any, Any], override: dict[Any, Any], path: _ConfigPath = ()
) -> dict[Any, Any]:
    """Merge *override* over *base*: mappings merge key by key; lists and
    scalars replace wholesale. A ``null`` mapping value in *override* means
    "absent": it removes a key *base* provides and is dropped otherwise."""

    if len(path) > _MAX_MERGE_DEPTH:
        raise ConfigFileError(
            f"config nesting exceeds {_MAX_MERGE_DEPTH} levels while merging over {ROOT_CONFIG_ENV}"
        )
    merged = dict(base)
    for key, value in override.items():
        if value is None:
            merged.pop(key, None)
            continue
        current = merged.get(key)
        if key in merged and isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(
                cast(dict[Any, Any], current), cast(dict[Any, Any], value), (*path, key)
            )
        elif isinstance(value, dict):
            # A mapping the root does not have: still drop its null leaves.
            merged[key] = _deep_merge({}, cast(dict[Any, Any], value), (*path, key))
        else:
            merged[key] = value
    return merged


def _reject_nulls(document: Any) -> None:
    """Reject any ``null`` value in a TANGLE_ROOT_CONFIG document, naming its key path."""

    seen: set[int] = set()
    stack: list[tuple[Any, _ConfigPath]] = [(document, ())]
    while stack:
        node, path = stack.pop()
        if isinstance(node, dict):
            if id(node) in seen:
                continue
            seen.add(id(node))
            items: list[tuple[Any, Any]] = list(cast(dict[Any, Any], node).items())
        elif isinstance(node, list):
            if id(node) in seen:
                continue
            seen.add(id(node))
            items = list(enumerate(cast(list[Any], node)))
        else:
            continue
        for key, value in reversed(items):
            if value is None:
                raise ConfigFileError(
                    f"null is not allowed in {ROOT_CONFIG_ENV} (at {_render_config_path((*path, key))})"
                )
            stack.append((value, (*path, key)))


def normalize_command(command: str, aliases: Mapping[str, str] | None = None) -> str:
    """Canonical command identity: the full invocation path, program first.

    Whitespace runs collapse to single spaces, then the longest whole-token
    prefix found in *aliases* (default :data:`COMMAND_ALIASES`, e.g.
    ``tangle-cli`` -> ``tangle``) is replaced by its canonical path, so an
    alias or deprecated group/command name maps to the one real path.
    Matching is otherwise exact and case-sensitive.
    """

    parts = command.split() if isinstance(command, str) else []
    if not parts:
        raise ConfigFileError("a command identity must be a non-empty string")
    table = {
        " ".join(k.split()): " ".join(v.split())
        for k, v in (COMMAND_ALIASES if aliases is None else aliases).items()
    }
    # Rewrite to a fixed point (a renamed program and a renamed group can both
    # apply); bounded so a cyclic alias table cannot loop.
    for _ in range(len(table) + 1):
        for length in range(len(parts), 0, -1):
            prefix = " ".join(parts[:length])
            if prefix in table and table[prefix] != prefix:
                parts = [*table[prefix].split(), *parts[length:]]
                break
        else:
            break
    return " ".join(parts)


def _root_config_entry(command: str, logger: Logger | None) -> LoadedConfig | None:
    """Load the running *command*'s ``TANGLE_ROOT_CONFIG`` entry, or ``None``.

    ``None`` when ``TANGLE_ROOT_CONFIG`` is unset/empty or has no entry for
    *command*. A set variable naming a missing, unreadable, or malformed file
    fails closed; diagnostics name the variable, the path, and command keys,
    never a config value.
    """

    raw = os.environ.get(ROOT_CONFIG_ENV)
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.exists():
        raise ConfigFileError(f"{ROOT_CONFIG_ENV} names a config file that does not exist: {path}")
    if not path.is_file():
        raise ConfigFileError(f"{ROOT_CONFIG_ENV} must name a config file: {path}")
    try:
        return _select_root_entry(path, command, logger or get_default_logger())
    except ConfigFileError as exc:
        raise ConfigFileError(f"{ROOT_CONFIG_ENV} ({path}): {exc}") from exc


def _select_root_entry(path: Path, command: str, log: Logger) -> LoadedConfig | None:
    parsed = ArgsContainer._parse_config_file(path)
    _reject_nulls(parsed)
    # A document-level _select picks among whole `commands` documents first;
    # every _env in the file (dormant commands included) is shape-checked here
    # but none is read.
    document = resolve_config_document(parsed, path, mapping_only=True).document
    if not isinstance(document, dict) or ROOT_COMMANDS_KEY not in document:
        raise ConfigFileError(
            f"must be a mapping with a top-level '{ROOT_COMMANDS_KEY}' selector "
            "keyed by command, e.g. 'tangle sdk pipeline-runs submit'"
        )
    document_dict = cast(dict[Any, Any], document)
    extra = sorted(
        _render_config_key(key)
        for key in document_dict
        if key != ROOT_COMMANDS_KEY and not (isinstance(key, str) and key.startswith("_"))
    )
    if extra:
        raise ConfigFileError(
            f"only '{ROOT_COMMANDS_KEY}' and underscore-prefixed helper keys may appear at "
            f"the top level, got: {', '.join(extra)}"
        )
    commands = document_dict[ROOT_COMMANDS_KEY]
    if not isinstance(commands, dict) or ENV_KEY in commands:
        raise ConfigFileError(f"'{ROOT_COMMANDS_KEY}' must map command names to config objects")

    table: dict[str, tuple[Any, Any]] = {}
    for key, entry in cast(dict[Any, Any], commands).items():
        rendered = _render_config_key(key)
        if not isinstance(key, str) or not key.split():
            raise ConfigFileError(
                f"'{ROOT_COMMANDS_KEY}' keys must be non-empty command names, got {rendered}"
            )
        name = normalize_command(key)
        if name in table:
            raise ConfigFileError(
                f"'{ROOT_COMMANDS_KEY}' names command {_render_config_key(name)} more than once"
            )
        if not isinstance(entry, dict) or ENV_KEY in entry:
            raise ConfigFileError(
                f"'{ROOT_COMMANDS_KEY}' entry {rendered} must be a config object"
            )
        table[name] = (key, entry)

    if command not in table:
        close = difflib.get_close_matches(command, list(table), n=1, cutoff=0.85)
        if close:
            log.warn(
                f"{ROOT_CONFIG_ENV} ({path}) has no entry for {_render_config_key(command)}; "
                f"did you mean {_render_config_key(close[0])}? No root config applied."
            )
        return None

    key, entry = table[command]
    # Then the entry's own _select (if any) and its _env lookups -- only here.
    resolution = resolve_config_document(entry, path, mapping_only=True)
    selected = cast(dict[str, Any], resolution.document)
    values = resolution.resolve_entry(selected, (ROOT_COMMANDS_KEY, key))
    source = path.resolve()
    return LoadedConfig(values, {k: source for k in values}, _direct_env_sources(selected))


def load_config(
    config_path: str | Path | None,
    logger: Logger | None = None,
    *,
    command: str | None = None,
) -> list[LoadedConfig]:
    """Load ``--config`` entries layered over the command's ``TANGLE_ROOT_CONFIG`` entry.

    The one loader for commands that take ``--config``; ``ArgsContainer.load``
    uses it, and callers reading config values directly should too.
    *command* is the running command's full path (see :func:`normalize_command`),
    passed explicitly by the CLI layer. With no *command* (e.g. a library
    call), or no ``TANGLE_ROOT_CONFIG`` entry for it, this returns exactly the
    ``--config`` entries and never reads the root file. Otherwise:

    * no ``--config`` -> the root entry is the single entry;
    * each ``--config`` entry (after that file's own ``_defaults``, which stay
      a shallow merge) is deep-merged over the root entry: mappings merge key
      by key and lists and scalars replace. ``null`` is rejected anywhere in
      the root file. In a ``--config`` layered over a root entry, a ``null``
      mapping value means "absent" at any depth: it unsets a key the root
      entry provides and is dropped where the root does not set the key, so
      resolution falls through to the env tier and default. Without an active
      root entry, ``--config`` ``null`` keeps its existing meaning.

    Each file resolves its own ``_select`` / ``_env`` before merging.
    """

    identity = normalize_command(command) if command is not None else None
    root = _root_config_entry(identity, logger) if identity is not None else None
    if root is not None and config_path is None:
        return [root]
    entries = ArgsContainer._load_config_entries(config_path, logger)

    command_source = Path(config_path).resolve() if config_path is not None else None
    loaded: list[LoadedConfig] = []
    for values, env_sources in entries:
        if root is None:
            own = {key: command_source for key in values} if command_source is not None else {}
            loaded.append(LoadedConfig(values, own, env_sources))
            continue
        merged = _deep_merge(root.values, values)
        loaded.append(
            LoadedConfig(
                merged,
                {
                    key: root.sources[key] if key not in values else cast(Path, command_source)
                    for key in merged
                },
                {
                    **{k: v for k, v in root.env_sources.items() if k not in values},
                    **{k: v for k, v in env_sources.items() if k in merged},
                },
            )
        )
    return loaded


@dataclass(frozen=True)
class ResolvedConfigDocument:
    """A config document after ``_select``, before its ``_env`` lookups.

    ``document`` is the selected document root; callers apply their own shape
    rules and then :meth:`resolve_entry` to each config mapping they keep, so
    only directives in used entries are looked up. ``branches`` holds every
    candidate document root, dormant ones included, for fail-closed shape
    checks. ``env_dependent`` is true when the result can depend on the
    environment (a root ``_select`` or any ``_env``).
    """

    document: Any
    branches: tuple[Any, ...]
    env_dependent: bool
    _resolver: _EnvValueResolver | None

    def resolve_entry(self, entry: dict[str, Any], path: _ConfigPath = ()) -> dict[str, Any]:
        """Replace the ``_env`` directives in one config mapping's values."""

        if self._resolver is None:
            return entry
        return self._resolver.resolve_config_object(entry, path)


def resolve_config_document(
    parsed: Any, source: str | Path, *, mapping_only: bool = False
) -> ResolvedConfigDocument:
    """Resolve a parsed config document's root ``_select`` chain and ``_env`` plan.

    The single document-level resolution shared by ``--config`` files and
    pipeline ``cfg`` files: the selector tree and every ``_env`` directive are
    structurally validated first (dormant branches included, no variable
    read), then the selector chain is resolved against the environment.
    *mapping_only* makes every branch a plain mapping (pipeline ``cfg``);
    otherwise branches take the ``--config`` shapes. *source* labels
    ``_env`` diagnostics. Raises :class:`ConfigFileError`.
    """

    memo: _ValidationMemo = {}
    is_select = isinstance(parsed, dict) and SELECT_KEY in cast(dict[Any, Any], parsed)
    if is_select:
        ArgsContainer._validate_selector_node(
            cast(dict[str, Any], parsed), 0, memo, mapping_only=mapping_only
        )
    uses_env, branches = ArgsContainer._validate_env_directives(
        parsed, mapping_only=mapping_only
    )
    selected = ArgsContainer._resolve_select(parsed, memo, mapping_only=mapping_only)
    resolver: _EnvValueResolver | None = None
    if uses_env:
        within = " (within the selected _select branch)" if is_select else ""
        resolver = _EnvValueResolver(f" in {source}{within}")
    return ResolvedConfigDocument(selected, tuple(branches), is_select or uses_env, resolver)


__all__ = [
    "COMMAND_ALIASES",
    "ENV_KEY",
    "ROOT_COMMANDS_KEY",
    "ROOT_CONFIG_ENV",
    "SELECT_KEY",
    "ArgsContainer",
    "ConfigFileError",
    "EnvField",
    "LoadedConfig",
    "ResolvedConfigDocument",
    "resolve_config_document",
    "strict_bool",
    "strict_float",
    "strict_int",
    "load_config",
    "normalize_command",
]
