"""Strict typing of scalar ArgsContainer fields fed by config or environment strings."""

from __future__ import annotations

import builtins
import traceback
from enum import Enum
from types import SimpleNamespace
from typing import Any

import pytest

from tangle_cli import cli, secrets_cli
from tangle_cli.args_container import (
    ArgsContainer,
    ConfigFileError,
    EnvField,
    strict_bool,
    strict_float,
    strict_int,
)
from tangle_cli.python_pipeline.cfg import load_cfg

SECRET = "FAKE-SECRET-do-not-echo-7f3a9c2e1b"
ENV_VARS = ("DELETE_FORCE", "FLAG", "COUNT", "RATIO", "NAME_VAR", "OTHER")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def _write(tmp_path, text: str, name: str = "config.yaml"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _rendered(excinfo) -> str:
    return "".join(traceback.format_exception(excinfo.value))


# --- the Binks scenario: `tangle sdk secrets delete` with force from _env ------------------


class _FakeClient:
    instances: list[_FakeClient] = []

    def __init__(self, **kwargs: Any) -> None:
        self.calls: list[str] = []
        _FakeClient.instances.append(self)

    def secrets_delete(self, secret_name: str) -> SimpleNamespace:
        self.calls.append(secret_name)
        return SimpleNamespace()


@pytest.fixture
def delete_app(monkeypatch, tmp_path):
    _FakeClient.instances = []
    monkeypatch.setattr(secrets_cli, "LazyTangleApiClient", _FakeClient)
    prompts: list[str] = []

    def fake_input() -> str:
        prompts.append("asked")
        return "n"

    monkeypatch.setattr(builtins, "input", fake_input)
    config = _write(
        tmp_path,
        "secret_name: API_TOKEN\nlog_type: none\nforce: {_env: DELETE_FORCE, default: false}\n",
    )

    def run() -> tuple[list[str], list[str], str]:
        exit_message = ""
        try:
            cli.build_app()(["sdk", "secrets", "delete", "--config", str(config)])
        except SystemExit as exc:
            exit_message = str(exc.code) if exc.code not in (0, None) else ""
        deleted = [name for client in _FakeClient.instances for name in client.calls]
        return prompts, deleted, exit_message

    return run


@pytest.mark.parametrize("value", [None, "false", "FALSE", "no", "0"])
def test_secrets_delete_env_force_false_still_confirms(delete_app, monkeypatch, value) -> None:
    if value is not None:
        monkeypatch.setenv("DELETE_FORCE", value)

    prompts, deleted, exit_message = delete_app()

    assert prompts == ["asked"]
    assert deleted == []
    assert "Delete cancelled" in exit_message


@pytest.mark.parametrize("value", ["true", "True", "yes", "1"])
def test_secrets_delete_env_force_true_skips_confirmation(delete_app, monkeypatch, value) -> None:
    monkeypatch.setenv("DELETE_FORCE", value)

    prompts, deleted, exit_message = delete_app()

    assert prompts == []
    assert deleted == ["API_TOKEN"]
    assert exit_message == ""


@pytest.mark.parametrize("value", ["", "maybe", " true", "false ", "on", SECRET])
def test_secrets_delete_env_force_invalid_is_rejected_without_echo(
    delete_app, monkeypatch, value
) -> None:
    monkeypatch.setenv("DELETE_FORCE", value)

    prompts, deleted, exit_message = delete_app()

    assert prompts == [] and deleted == []
    assert exit_message == (
        "Config error: Invalid value for force from environment variable DELETE_FORCE "
        "(_env at config key 'force'): expected a boolean: true/false, yes/no, or 1/0 "
        "(case-insensitive)"
    )
    if value == SECRET:
        assert SECRET not in exit_message


# --- bool fields -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("true", True), ("TRUE", True), ("yes", True), ("Yes", True), ("1", True),
        ("false", False), ("False", False), ("no", False), ("NO", False), ("0", False),
    ],
)
def test_bool_field_accepts_documented_spellings_from_env(monkeypatch, text, expected) -> None:
    monkeypatch.setenv("FLAG", text)

    [args] = ArgsContainer.load(None, flag=EnvField("FLAG", (False, False)))

    assert args.flag is expected
    assert args.origin("flag") == "env:FLAG"


@pytest.mark.parametrize("text", ["", "maybe", "on", "off", "t", "2", " yes", "tru\u0435", SECRET])
def test_bool_field_rejects_other_env_strings_without_echo(monkeypatch, text) -> None:
    monkeypatch.setenv("FLAG", text)

    with pytest.raises(ConfigFileError) as excinfo:
        ArgsContainer.load(None, flag=EnvField("FLAG", (False, False)))

    assert str(excinfo.value).startswith(
        "Invalid value for flag from environment variable FLAG: expected a boolean"
    )
    if text == SECRET:
        assert SECRET not in _rendered(excinfo)


def test_bool_yaml_string_literals_are_parsed_strictly(tmp_path) -> None:
    config = _write(tmp_path, "a: 'false'\nb: 'yes'\nc: true\nd: 0\n")

    [args] = ArgsContainer.load(
        config, a=(False, False), b=(False, False), c=(False, False), d=(True, True)
    )

    assert (args.a, args.b, args.c, args.d) == (False, True, True, False)


@pytest.mark.parametrize("literal", ["'maybe'", "''", "[x]", "{k: v}", "2", "1.0"])
def test_bool_config_literal_of_wrong_type_is_rejected(tmp_path, literal) -> None:
    config = _write(tmp_path, f"force: {literal}\n")

    with pytest.raises(
        ConfigFileError, match=r"^Invalid value for force from config key 'force': expected a boolean"
    ):
        ArgsContainer.load(config, force=(False, False))


def test_explicit_strict_bool_types_a_none_default_field(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FLAG", "false")
    config = _write(tmp_path, "dry_run: {_env: FLAG}\nallow: 'no'\n")

    [args] = ArgsContainer.load(
        config,
        dry_run=(None, None, strict_bool),
        allow=("allow", None, None, False, False, strict_bool),
        unset=(None, None, strict_bool),
    )

    assert (args.dry_run, args.allow, args.unset) == (False, False, None)


def test_explicit_strict_bool_accepts_cli_bools(tmp_path) -> None:
    [args] = ArgsContainer.load(None, dry_run=(True, None, strict_bool))

    assert args.dry_run is True
    assert args.origin("dry_run") == "cli"


# --- int / float fields ----------------------------------------------------------------------


@pytest.mark.parametrize(("text", "expected"), [("0", 0), ("42", 42), ("-3", -3), ("+7", 7)])
def test_int_field_parses_env_strings(monkeypatch, text, expected) -> None:
    monkeypatch.setenv("COUNT", text)

    [args] = ArgsContainer.load(None, count=EnvField("COUNT", (6, 6)))

    assert args.count == expected and type(args.count) is int


@pytest.mark.parametrize("text", ["", "1.0", "1e3", "0x1f", "1_000", " 5", "５", "true", SECRET])
def test_int_field_rejects_non_integers_without_echo(tmp_path, monkeypatch, text) -> None:
    monkeypatch.setenv("COUNT", text)
    config = _write(tmp_path, "count: {_env: COUNT}\n")

    with pytest.raises(ConfigFileError) as excinfo:
        ArgsContainer.load(config, count=(6, 6))

    assert str(excinfo.value) == (
        "Invalid value for count from environment variable COUNT (_env at config key 'count'): "
        "expected an integer"
    )
    if text == SECRET:
        assert SECRET not in _rendered(excinfo)


@pytest.mark.parametrize("literal", ["true", "1.5", "[1]"])
def test_int_field_rejects_wrong_yaml_types(tmp_path, literal) -> None:
    config = _write(tmp_path, f"count: {literal}\n")

    with pytest.raises(ConfigFileError, match="from config key 'count': expected an integer"):
        ArgsContainer.load(config, count=(6, 6))


@pytest.mark.parametrize(
    ("text", "expected"), [("1.5", 1.5), ("10", 10.0), ("-.5", -0.5), ("2e3", 2000.0), ("3.", 3.0)]
)
def test_float_field_parses_env_strings(monkeypatch, text, expected) -> None:
    monkeypatch.setenv("RATIO", text)

    [args] = ArgsContainer.load(None, ratio=EnvField("RATIO", (600.0, 600.0)))

    assert args.ratio == expected and type(args.ratio) is float


@pytest.mark.parametrize("text", ["", "nan", "inf", "1,5", "abc", "true"])
def test_float_field_rejects_non_numbers(monkeypatch, text) -> None:
    monkeypatch.setenv("RATIO", text)

    with pytest.raises(ConfigFileError, match="from environment variable RATIO: expected a number"):
        ArgsContainer.load(None, ratio=EnvField("RATIO", (600.0, 600.0)))


def test_numeric_yaml_values_pass_through(tmp_path) -> None:
    config = _write(tmp_path, "count: 3\nratio: 2\nlimit: '10'\n")

    [args] = ArgsContainer.load(
        config, count=(6, 6), ratio=(1.0, 1.0), limit=(None, None, strict_int)
    )

    assert (args.count, args.ratio, args.limit) == (3, 2, 10)


def test_strict_converters_directly() -> None:
    assert strict_bool("Yes") is True and strict_bool(0) is False
    assert strict_int("-12") == -12 and strict_float("1.25") == 1.25
    for converter, value in ((strict_bool, 2), (strict_int, True), (strict_float, False)):
        with pytest.raises(ConfigFileError):
            converter(value)


# --- untouched: strings, JSON, repeatables, enums, nested, cfg ------------------------------


class _Mode(Enum):
    FAST = "fast"
    SLOW = "slow"


def test_string_json_repeatable_and_enum_fields_are_not_coerced(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FLAG", "false")
    monkeypatch.setenv("COUNT", "10")
    config = _write(
        tmp_path,
        "name: {_env: FLAG}\n"
        "none_default: {_env: COUNT}\n"
        "payload: {_env: COUNT}\n"
        "header: [{_env: FLAG}]\n"
        "mode: slow\n"
        "nested: {flag: {_env: FLAG}, n: {_env: COUNT}}\n",
    )

    [args] = ArgsContainer.load(
        config,
        name=("default-name", "default-name"),
        none_default=(None, None),
        payload=("payload", None, None, True),
        header=(None, None),
        mode=(_Mode.FAST, _Mode.FAST),
        nested=(None, None),
    )

    assert args.name == "false"
    assert args.none_default == "10"
    assert args.payload == 10
    assert args.header == ["false"]
    assert args.mode is _Mode.SLOW
    # Typing applies to scalar fields only; nested _env values stay strings.
    assert args.nested == {"flag": "false", "n": "10"}


def test_raw_config_keeps_strings(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FLAG", "false")
    config = _write(tmp_path, "force: {_env: FLAG}\n")

    [args] = ArgsContainer.load(config, force=(False, False))

    assert args.force is False
    assert args._config == {"force": "false"}
    assert ArgsContainer._load_config_file(config) == [{"force": "false"}]


def test_cfg_load_path_keeps_its_own_typing(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FLAG", "false")
    path = _write(tmp_path, "force: {_env: FLAG}\nnative: false\n")

    cfg = load_cfg(path, {"cli_flag": "false"})

    assert cfg.force == "false"  # _env stays a string in cfg
    assert cfg.native is False
    assert cfg.cli_flag is False  # --override strings keep YAML coercion


# --- precedence unchanged --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cli", "config", "env", "expected", "origin"),
    [
        (True, "false", "false", True, "cli"),
        (False, "true", "false", True, "config"),
        (False, None, "yes", True, "env:FLAG"),
        (False, None, None, False, "default"),
        (True, None, "no", True, "cli"),
        (False, "no", "yes", False, "config"),
    ],
)
def test_precedence_matrix_with_typing(tmp_path, monkeypatch, cli, config, env, expected, origin) -> None:
    path = _write(tmp_path, f"flag: '{config}'\n" if config is not None else "other: 1\n")
    if env is not None:
        monkeypatch.setenv("FLAG", env)

    [args] = ArgsContainer.load(path, flag=EnvField("FLAG", (cli, False)))

    assert args.flag is expected
    assert args.origin("flag") == origin


def test_defaults_env_source_is_named(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FLAG", "maybe")
    config = _write(
        tmp_path,
        "_defaults:\n  force: {_env: FLAG}\nconfigs:\n  - {}\n  - force: 'nope'\n",
    )

    with pytest.raises(ConfigFileError, match=r"environment variable FLAG \(_env at config key 'force'\)"):
        ArgsContainer.load(config, force=(False, False))

    monkeypatch.setenv("FLAG", "yes")
    with pytest.raises(ConfigFileError, match=r"from config key 'force': expected a boolean"):
        ArgsContainer.load(config, force=(False, False))
