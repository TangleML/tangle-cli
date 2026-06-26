"""CLI argument resolution with optional YAML/JSON config files.

This module provides generic config-file behavior shared by Tangle CLI
commands: load one or more config objects, merge each with parsed CLI
arguments, and keep explicit CLI values higher precedence than config values.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import Any, cast

import yaml

from tangle_cli.logger import Logger, get_default_logger
from tangle_cli.utils import apply_defaults


class ConfigFileError(Exception):
    """Raised when there is an error loading or resolving a config file."""


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
    def _load_config_file(
        config_path: str | Path | None,
        logger: Logger | None = None,
    ) -> list[dict[str, Any]]:
        """Load a YAML/JSON config file as a list of config dictionaries.

        Supported shapes are a single object, a list of objects, or an object
        with ``_defaults`` and ``configs`` where defaults are applied to each
        config entry. Other top-level keys are ignored, which lets YAML files
        use anchors/shared helper sections.
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


__all__ = ["ArgsContainer", "ConfigFileError"]
