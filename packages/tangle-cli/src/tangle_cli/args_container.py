"""CLI argument resolution with optional YAML/JSON config files.

This module provides generic config-file behavior shared by Tangle CLI
commands: load one or more config objects, merge each with parsed CLI
arguments, and keep explicit CLI values higher precedence than config values.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import Any, cast

import yaml

from tangle_cli.logger import Logger, get_default_logger
from tangle_cli.utils import apply_defaults

#: The only key reserved by the environment selector. It is an exact key name,
#: not a prefix: other ``_``-prefixed keys stay available for YAML anchors.
SELECT_KEY = "_select"

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


class ConfigFileError(Exception):
    """Raised when there is an error loading or resolving a config file."""


def _render_case_key(key: str) -> str:
    """Render an already-validated configured case key for diagnostics."""

    rendered = "".join(char if char.isprintable() else "?" for char in key)
    if len(rendered) > _MAX_RENDERED_CASE_KEY_LENGTH:
        rendered = rendered[: _MAX_RENDERED_CASE_KEY_LENGTH - 3] + "..."
    return repr(rendered)


class ArgsContainer:
    """Container for resolved CLI arguments with config-file defaults."""

    def __init__(self, resolved: dict[str, Any], raw_config: dict[str, Any]):
        self._config = raw_config
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

        return {key: value for key, value in vars(self).items() if key != "_config"}

    @staticmethod
    def _validate_branch_document(
        branch: Any,
        where: str,
        depth: int = 0,
        memo: _ValidationMemo | None = None,
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
        :meth:`_validate_selector_node`.
        """

        if isinstance(branch, dict):
            branch_dict = cast(dict[Any, Any], branch)
            if SELECT_KEY in branch_dict:
                if depth + 1 >= _MAX_SELECT_DEPTH:
                    raise ConfigFileError(
                        f"{SELECT_KEY} nesting exceeded the maximum depth "
                        f"of {_MAX_SELECT_DEPTH}"
                    )
                ArgsContainer._validate_selector_node(branch_dict, depth + 1, memo)
                return
            if "configs" in branch_dict:
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

        siblings = sorted(
            repr(key) for key in node if not (isinstance(key, str) and key.startswith("_"))
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
            repr(key) for key in selector_dict if key not in ("env", "cases", "default")
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
        if not isinstance(env_name, str) or not _ENV_NAME_PATTERN.match(env_name):
            raise ConfigFileError(
                f"{SELECT_KEY}.env must be a valid environment variable name "
                "matching [A-Za-z_][A-Za-z0-9_]*"
            )

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
                case_value, f"{SELECT_KEY} case {_render_case_key(case_key)}", depth, memo
            )

        default_branch: Any = None
        if "default" in selector_dict:
            default_branch = selector_dict["default"]
            ArgsContainer._validate_branch_document(
                default_branch, f"{SELECT_KEY}.default", depth, memo
            )

        allowed = ", ".join(_render_case_key(key) for key in sorted(cases_dict))
        summary: _SelectorSummary = (env_name, cases_dict, default_branch, allowed)
        if memo is not None:
            previous = memo.get(node_id)
            if previous is None or depth > previous[0]:
                memo[node_id] = (depth, node, summary)
        return summary

    @staticmethod
    def _select_branch(node: dict[str, Any], memo: _ValidationMemo | None = None) -> Any:
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
            node, 0, memo
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
    def _resolve_select(parsed: Any) -> Any:
        """Replace a root ``_select`` node with its selected config branch.

        A selected branch is a complete config document in its own right, so it
        may itself be another root ``_select`` node (multi-dimensional
        selection). Documents without ``_select`` are returned unchanged.

        The whole selector tree is structurally validated up front by
        :meth:`_select_branch`; this loop only walks the chain the environment
        actually selects, and keeps its own bound as a belt-and-braces guard.
        The validation memo lives for exactly this one document resolution.
        """

        memo: _ValidationMemo = {}
        node = parsed
        selections = 0
        while isinstance(node, dict) and SELECT_KEY in cast(dict[Any, Any], node):
            if selections >= _MAX_SELECT_DEPTH:
                raise ConfigFileError(
                    f"{SELECT_KEY} nesting exceeded the maximum depth of {_MAX_SELECT_DEPTH}"
                )
            node = ArgsContainer._select_branch(cast(dict[str, Any], node), memo)
            selections += 1
        return node

    @staticmethod
    def _load_config_file(
        config_path: str | Path | None,
        logger: Logger | None = None,
    ) -> list[dict[str, Any]]:
        """Load a YAML/JSON config file as a list of config dictionaries.

        Supported shapes are a single object, a list of objects, or an object
        with ``_defaults`` and ``configs`` where defaults are applied to each
        config entry. Other top-level keys are ignored, which lets YAML files
        use anchors/shared helper sections.

        A document may instead select one of those shapes at load time with a
        top-level ``_select`` node (see :meth:`_select_branch`).
        """

        log = logger or get_default_logger()
        if config_path is None:
            return [{}]

        path = Path(config_path)
        if not path.exists():
            raise ConfigFileError(f"Config file not found: {config_path}")

        try:
            with path.open(encoding="utf-8") as f:
                if path.suffix in (".yaml", ".yml"):
                    parsed = yaml.safe_load(f)
                    if parsed is None:
                        return [{}]
                else:
                    parsed = json.load(f)
        except (OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
            raise ConfigFileError(f"Error loading config file: {exc}") from exc

        parsed = ArgsContainer._resolve_select(parsed)

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
                merged = apply_defaults(configs_list, defaults)
                assert isinstance(merged, list)
                log.info(f"Loaded config: {path} ({len(merged)} configs with defaults)")
                return merged
            log.info(f"Loaded config: {path} (1 config)")
            return [parsed_dict]

        if isinstance(parsed, list):
            for index, item in enumerate(cast(list[Any], parsed)):
                if not isinstance(item, dict):
                    raise ConfigFileError(
                        "Config file entry "
                        f"{index} must be an object, got {type(item).__name__}"
                    )
            configs = cast(list[dict[str, Any]], parsed)
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
        """Create a converter that accepts enum values by string."""

        def convert(value: Any) -> Any:
            if isinstance(value, str):
                try:
                    return enum_type(value)
                except ValueError as exc:
                    valid_values = [member.value for member in enum_type]
                    raise ConfigFileError(
                        f"Invalid value '{value}' for {field_name}. "
                        f"Valid values: {valid_values}"
                    ) from exc
            return value

        return convert

    @staticmethod
    def _resolve(config: dict[str, Any], **kwargs: Any) -> ArgsContainer:
        """Resolve CLI args against a single config dict.

        Field specs can be:
        - ``(cli_value,)``: required field, config key is parameter name;
        - ``(cli_value, default)``: optional field;
        - ``(cli_value, default, converter)``: optional with converter;
        - ``(config_key, cli_value, default, is_json)``: explicit key;
        - ``(config_key, cli_value, default, is_json, required)``;
        - ``(config_key, cli_value, default, is_json, required, converter)``.
        """

        resolved: dict[str, Any] = {}
        required_fields: list[str] = []

        for param_name, spec in kwargs.items():
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

            if converter is None and isinstance(default_value, Enum):
                converter = ArgsContainer._make_enum_converter(param_name, type(default_value))

            if cli_value is not None and cli_value != default_value:
                value = cli_value
            elif config_key in config:
                value = config[config_key]
            else:
                value = cli_value

            resolved[param_name] = converter(value) if converter and value is not None else value

        for field_name in required_fields:
            if resolved.get(field_name) is None:
                raise ConfigFileError(
                    f"{field_name} is required (via CLI argument or config file)"
                )

        return ArgsContainer(resolved, config)

    @staticmethod
    def load(
        config_path: str | Path | None,
        logger: Logger | None = None,
        **kwargs: Any,
    ) -> list[ArgsContainer]:
        """Load a config file and resolve CLI args against each config entry."""

        configs = ArgsContainer._load_config_file(config_path, logger=logger)
        return [ArgsContainer._resolve(config, **kwargs) for config in configs]


__all__ = ["SELECT_KEY", "ArgsContainer", "ConfigFileError"]
