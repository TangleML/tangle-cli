"""Environment-sourced values: the ``_env`` config directive and ``EnvField``."""

from __future__ import annotations

import json
import traceback
from enum import Enum

import pytest

import tangle_cli.args_container as args_container_module
from tangle_cli.api_transport import _header_entries
from tangle_cli.args_container import ArgsContainer, ConfigFileError, EnvField

SECRET = "FAKE-SECRET-do-not-echo-7f3a9c2e1b"


def _write(tmp_path, text: str, name: str = "config.yaml"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _load(tmp_path, text: str, name: str = "config.yaml"):
    return ArgsContainer._load_config_file(_write(tmp_path, text, name))


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("TOKEN", "OTHER", "SEL", "HDR", "PAYLOAD", "MODE", "MISSING", "LIMIT"):
        monkeypatch.delenv(name, raising=False)


# --- _env directive: set / unset / empty / default -------------------------------------


def test_env_directive_reads_set_variable(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TOKEN", "abc")

    assert _load(tmp_path, "token: {_env: TOKEN}\nname: plain\n") == [
        {"token": "abc", "name": "plain"}
    ]


def test_env_directive_unset_without_default_fails_closed(tmp_path) -> None:
    config = _write(tmp_path, "limit: 3\nauth:\n  token: {_env: TOKEN}\n")

    with pytest.raises(ConfigFileError) as excinfo:
        ArgsContainer._load_config_file(config)

    message = str(excinfo.value)
    assert "Environment variable TOKEN is required by _env at auth.token" in message
    assert str(config) in message
    assert "not set" in message


def test_env_directive_empty_string_counts_as_set(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TOKEN", "")

    assert _load(tmp_path, "token: {_env: TOKEN, default: fallback}\n") == [{"token": ""}]


def test_env_directive_default_used_only_when_unset(tmp_path, monkeypatch) -> None:
    text = "token: {_env: TOKEN, default: fallback}\n"
    assert _load(tmp_path, text) == [{"token": "fallback"}]

    monkeypatch.setenv("TOKEN", "real")
    assert _load(tmp_path, text) == [{"token": "real"}]


@pytest.mark.parametrize(
    ("default", "expected"),
    [
        ("5", "5"),
        ("1.5", "1.5"),
        ("true", "true"),
        ("no", "false"),
        ("'007'", "007"),
        ("''", ""),
        ("2024-01-02", "2024-01-02"),
    ],
)
def test_env_directive_default_is_stringified(tmp_path, default, expected) -> None:
    [config] = _load(tmp_path, f"value: {{_env: TOKEN, default: {default}}}\n")

    assert config["value"] == expected
    assert type(config["value"]) is str


def test_env_directive_value_is_string_even_when_numeric(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LIMIT", "10")

    [config] = _load(tmp_path, "limit: {_env: LIMIT, default: 5}\n")

    assert config["limit"] == "10"


@pytest.mark.parametrize("default", ["null", "~", "[a]", "{a: 1}"])
def test_env_directive_non_scalar_or_null_default_rejected(tmp_path, monkeypatch, default) -> None:
    monkeypatch.setenv("TOKEN", "set")  # rejected structurally, even when set

    with pytest.raises(ConfigFileError, match=r"_env at token: default must be a string"):
        _load(tmp_path, f"token: {{_env: TOKEN, default: {default}}}\n")


# --- _env directive: placement -----------------------------------------------------------


def test_env_directive_in_nested_maps_and_lists(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TOKEN", "t")
    monkeypatch.setenv("OTHER", "o")
    text = (
        "body:\n"
        "  items:\n"
        "    - {_env: TOKEN}\n"
        "    - plain\n"
        "    - nested: {deep: {_env: OTHER}}\n"
        "  keep: 3\n"
    )

    assert _load(tmp_path, text) == [
        {"body": {"items": ["t", "plain", {"nested": {"deep": "o"}}], "keep": 3}}
    ]


def test_env_directive_nested_error_names_list_path(tmp_path) -> None:
    with pytest.raises(ConfigFileError, match=r"_env at body\.items\[1\]\.deep in "):
        _load(tmp_path, "body:\n  items:\n    - x\n    - deep: {_env: MISSING}\n")


def test_env_directive_in_defaults_and_configs(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TOKEN", "t")
    monkeypatch.setenv("OTHER", "o")
    text = (
        "_defaults:\n"
        "  token: {_env: TOKEN}\n"
        "configs:\n"
        "  - name: a\n"
        "  - name: {_env: OTHER}\n"
    )

    assert _load(tmp_path, text) == [
        {"token": "t", "name": "a"},
        {"token": "t", "name": "o"},
    ]


@pytest.mark.parametrize(
    ("text", "location"),
    [
        ("_defaults:\n  token: {_env: MISSING}\nconfigs:\n  - {}\n", "_defaults.token"),
        ("configs:\n  - {}\n  - token: {_env: MISSING}\n", r"configs\[1\]\.token"),
        ("- {}\n- token: {_env: MISSING}\n", r"\[1\]\.token"),
    ],
)
def test_env_directive_missing_location_per_document_shape(tmp_path, text, location) -> None:
    with pytest.raises(ConfigFileError, match=rf"MISSING is required by _env at {location} "):
        _load(tmp_path, text)


def test_env_directive_in_json_config(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TOKEN", "t")
    text = json.dumps({"token": {"_env": "TOKEN"}, "n": {"_env": "OTHER", "default": 2}})

    assert _load(tmp_path, text, name="config.json") == [{"token": "t", "n": "2"}]


def test_env_directive_shared_alias_resolved_consistently(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TOKEN", "t")
    text = "a: &tok {_env: TOKEN}\nb: *tok\nc: [*tok]\n"

    assert _load(tmp_path, text) == [{"a": "t", "b": "t", "c": ["t"]}]


def test_top_level_env_helper_key_is_not_a_directive(tmp_path) -> None:
    # A document/config-entry mapping is never itself a directive, even when
    # the document uses _env elsewhere.
    text = "_env: &e {region: us}\nname: x\ntoken: {_env: TOKEN, default: d}\n"
    assert _load(tmp_path, text) == [{"_env": {"region": "us"}, "name": "x", "token": "d"}]

    listed = "- _env: TOKEN\n  token: {_env: TOKEN, default: d}\n"
    assert _load(tmp_path, listed, name="list.yaml") == [{"_env": "TOKEN", "token": "d"}]


def test_env_directive_in_recursive_alias_rejected(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TOKEN", "t")

    with pytest.raises(ConfigFileError, match="recursive YAML alias"):
        _load(tmp_path, "a: &a\n  tok: {_env: TOKEN}\n  self: *a\n")


def test_recursive_alias_without_directive_still_loads(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TOKEN", "t")
    # The document uses _env elsewhere, so the resolver walks the cycle.
    [config] = _load(tmp_path, "tok: {_env: TOKEN}\na: &a\n  self: *a\n")

    assert config["tok"] == "t"
    assert config["a"]["self"] is config["a"]


# --- _env directive: interplay with _select ----------------------------------------------

SELECT_WITH_ENV = (
    "_select:\n"
    "  env: SEL\n"
    "  cases:\n"
    "    a:\n"
    "      token: {_env: TOKEN}\n"
    "    b:\n"
    "      token: {_env: OTHER}\n"
    "  default:\n"
    "    token: {_env: MISSING, default: dflt}\n"
)


def test_env_directive_in_selected_branch_is_resolved(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SEL", "a")
    monkeypatch.setenv("TOKEN", "t")

    # OTHER (dormant branch b) is unset and not required.
    assert _load(tmp_path, SELECT_WITH_ENV) == [{"token": "t"}]


def test_env_directive_in_default_branch_is_resolved(tmp_path) -> None:
    assert _load(tmp_path, SELECT_WITH_ENV) == [{"token": "dflt"}]


def test_env_directive_missing_in_selected_branch_names_selection(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SEL", "b")

    with pytest.raises(ConfigFileError) as excinfo:
        _load(tmp_path, SELECT_WITH_ENV)

    message = str(excinfo.value)
    assert "Environment variable OTHER is required by _env at token" in message
    assert "within the selected _select branch" in message
    assert "'b'" not in message


def test_env_directive_in_unused_select_helper_is_not_looked_up(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SEL", "a")
    text = "_unused: {token: {_env: MISSING}}\n" + SELECT_WITH_ENV.replace(
        "{_env: TOKEN}", "plain"
    )

    assert _load(tmp_path, text) == [{"token": "plain"}]


def test_env_directive_in_ignored_top_level_key_is_not_looked_up(tmp_path) -> None:
    text = "_shared: {token: {_env: MISSING}}\nconfigs:\n  - name: a\n"

    assert _load(tmp_path, text) == [{"name": "a"}]


@pytest.mark.parametrize("selection", ["a", "b", None])
def test_malformed_directive_in_dormant_branch_fails_in_every_env(
    tmp_path, monkeypatch, selection
) -> None:
    if selection is not None:
        monkeypatch.setenv("SEL", selection)
    monkeypatch.setenv("TOKEN", "t")
    monkeypatch.setenv("OTHER", "o")
    text = SELECT_WITH_ENV.replace(
        "token: {_env: OTHER}", "token: {_env: OTHER, fallback: x}"
    )

    with pytest.raises(
        ConfigFileError,
        match=r"_env at _select\.cases\.b\.token allows only an optional 'default'",
    ):
        _load(tmp_path, text)


def test_malformed_directive_in_nested_dormant_selector_rejected(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SEL", "a")
    text = (
        "_select:\n"
        "  env: SEL\n"
        "  cases:\n"
        "    a: {name: x}\n"
        "    b:\n"
        "      _select:\n"
        "        env: OTHER\n"
        "        cases:\n"
        "          c:\n"
        "            configs:\n"
        "              - token: {_env: 1BAD}\n"
    )

    with pytest.raises(
        ConfigFileError,
        match=r"_env at _select\.cases\.b\._select\.cases\.c\.configs\[0\]\.token must be",
    ):
        _load(tmp_path, text)


def test_select_behavior_unchanged_for_directive_free_selectors(tmp_path, monkeypatch) -> None:
    text = "_select:\n  env: SEL\n  cases:\n    a: {name: x}\n"

    with pytest.raises(ConfigFileError, match="Environment variable SEL is required by _select"):
        _load(tmp_path, text)
    monkeypatch.setenv("SEL", "a")
    assert _load(tmp_path, text) == [{"name": "x"}]


# --- _env directive: structural errors ----------------------------------------------------


@pytest.mark.parametrize("name", ["1BAD", "has-dash", "''", "5", "null", "[A]", "'A B'"])
def test_env_directive_invalid_name_rejected(tmp_path, monkeypatch, name) -> None:
    monkeypatch.setenv("TOKEN", "t")

    with pytest.raises(
        ConfigFileError,
        match=r"_env at token must be a valid environment variable name matching",
    ):
        _load(tmp_path, f"token: {{_env: {name}}}\n")


def test_env_directive_invalid_name_is_not_echoed(tmp_path) -> None:
    with pytest.raises(ConfigFileError) as excinfo:
        _load(tmp_path, f"token: {{_env: '{SECRET}-x'}}\n")

    assert SECRET not in str(excinfo.value)


@pytest.mark.parametrize("sibling", ["fallback: x", "_note: x", "env: X", "defaults: x"])
def test_env_directive_stray_sibling_rejected(tmp_path, monkeypatch, sibling) -> None:
    monkeypatch.setenv("TOKEN", "t")

    with pytest.raises(ConfigFileError, match=r"_env at token allows only an optional 'default'"):
        _load(tmp_path, f"token: {{_env: TOKEN, {sibling}}}\n")


# --- no value echo -------------------------------------------------------------------------


class _Mode(Enum):
    FAST = "fast"
    SLOW = "slow"


def test_env_values_never_echoed_on_conversion_errors(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("TOKEN", SECRET)
    config = _write(tmp_path, "mode: {_env: TOKEN}\npayload: {_env: TOKEN}\n")

    with pytest.raises(ConfigFileError) as enum_error:
        ArgsContainer.load(config, mode=(None, _Mode.FAST))
    with pytest.raises(ConfigFileError) as json_error:
        ArgsContainer.load(config, payload=("payload", None, None, True))

    assert "Invalid value for mode" in str(enum_error.value)
    assert "Invalid JSON for payload" in str(json_error.value)
    for excinfo in (enum_error, json_error):
        # The rendered traceback (message plus any displayed cause/context).
        assert SECRET not in "".join(traceback.format_exception(excinfo.value))
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err


def test_env_field_conversion_error_does_not_echo_value(monkeypatch) -> None:
    monkeypatch.setenv("LIMIT", SECRET)

    with pytest.raises(ConfigFileError) as excinfo:
        ArgsContainer.load(None, limit=EnvField("LIMIT", (None, None, int)))

    assert str(excinfo.value) == "Invalid value for limit from environment variable LIMIT"
    assert SECRET not in "".join(traceback.format_exception(excinfo.value))


def test_origin_reports_env_name_not_value(monkeypatch) -> None:
    monkeypatch.setenv("TOKEN", SECRET)

    [args] = ArgsContainer.load(None, token=EnvField("TOKEN", (None, None)))

    assert args.token == SECRET
    assert args.origin("token") == "env:TOKEN"
    assert "_origins" not in args.to_dict()


# --- EnvField tier ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cli", "config", "env", "expected", "origin"),
    [
        (True, True, True, "cli", "cli"),
        (True, True, False, "cli", "cli"),
        (True, False, True, "cli", "cli"),
        (True, False, False, "cli", "cli"),
        (False, True, True, "config", "config"),
        (False, True, False, "config", "config"),
        (False, False, True, "env", "env:TOKEN"),
        (False, False, False, "dflt", "default"),
    ],
)
def test_env_field_precedence_matrix(
    tmp_path, monkeypatch, cli, config, env, expected, origin
) -> None:
    config_path = _write(tmp_path, "token: config\n" if config else "other: 1\n")
    if env:
        monkeypatch.setenv("TOKEN", "env")

    [args] = ArgsContainer.load(
        config_path, token=EnvField("TOKEN", ("cli" if cli else "dflt", "dflt"))
    )

    assert args.token == expected
    assert args.origin("token") == origin


def test_config_env_directive_beats_env_field_tier(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TOKEN", "from-directive")
    monkeypatch.setenv("OTHER", "from-tier")
    config = _write(tmp_path, "token: {_env: TOKEN}\n")

    [args] = ArgsContainer.load(config, token=EnvField("OTHER", (None, None)))

    assert args.token == "from-directive"
    assert args.origin("token") == "config"


def test_env_field_empty_string_counts_as_set(monkeypatch) -> None:
    monkeypatch.setenv("TOKEN", "")

    [args] = ArgsContainer.load(None, token=EnvField("TOKEN", ("dflt", "dflt")))

    assert args.token == ""
    assert args.origin("token") == "env:TOKEN"


def test_env_field_satisfies_required_and_names_var_when_missing(monkeypatch) -> None:
    with pytest.raises(
        ConfigFileError,
        match=(
            r"^token is required \(via CLI argument, config file, "
            r"or environment variable TOKEN\)$"
        ),
    ):
        ArgsContainer.load(None, token=EnvField("TOKEN", (None,)))

    monkeypatch.setenv("TOKEN", "t")
    [args] = ArgsContainer.load(None, token=EnvField("TOKEN", (None,)))
    assert args.token == "t"


def test_env_field_wraps_every_tuple_shape(monkeypatch) -> None:
    monkeypatch.setenv("TOKEN", "7")

    [args] = ArgsContainer.load(
        None,
        one=EnvField("TOKEN", (None,)),
        two=EnvField("TOKEN", (None, None)),
        three=EnvField("TOKEN", (None, None, int)),
        four=EnvField("TOKEN", ("four_key", None, None, True)),
        five=EnvField("TOKEN", ("five_key", None, None, False, True)),
        six=EnvField("TOKEN", ("six_key", None, None, False, False, int)),
    )

    assert args.to_dict() == {
        "one": "7", "two": "7", "three": 7, "four": 7, "five": "7", "six": 7,
    }


def test_env_field_json_conversion(monkeypatch) -> None:
    monkeypatch.setenv("PAYLOAD", '{"a": [1, 2]}')

    [args] = ArgsContainer.load(
        None, payload=EnvField("PAYLOAD", ("payload", None, None, True))
    )

    assert args.payload == {"a": [1, 2]}


def test_env_directive_json_field_conversion(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PAYLOAD", '{"a": 1}')
    config = _write(tmp_path, "payload: {_env: PAYLOAD}\nflag: {_env: MISSING, default: true}\n")

    [args] = ArgsContainer.load(
        config,
        payload=("payload", None, None, True),
        flag=("flag", None, None, True),
    )

    assert args.payload == {"a": 1}
    assert args.flag is True


def test_env_values_feed_repeatable_header_fields(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HDR", "X-A: 1")
    config = _write(tmp_path, "header:\n  - {_env: HDR}\n  - 'X-B: 2'\n")

    [from_config] = ArgsContainer.load(config, header=(None, None))
    [from_tier] = ArgsContainer.load(None, header=EnvField("HDR", (None, None)))

    assert _header_entries(from_config.header) == ["X-A: 1", "X-B: 2"]
    assert from_tier.header == "X-A: 1"
    assert _header_entries(from_tier.header) == ["X-A: 1"]


def test_env_field_enum_conversion(monkeypatch) -> None:
    monkeypatch.setenv("MODE", "slow")

    [args] = ArgsContainer.load(None, mode=EnvField("MODE", (_Mode.FAST, _Mode.FAST)))

    assert args.mode is _Mode.SLOW


@pytest.mark.parametrize("env", ["", "1BAD", "A-B", None])
def test_env_field_rejects_invalid_names(env) -> None:
    with pytest.raises(ValueError, match="EnvField.env must be a valid environment variable"):
        EnvField(env, (None, None))  # type: ignore[arg-type]


@pytest.mark.parametrize("spec", [(), ("a",) * 7, [None, None]])
def test_env_field_rejects_invalid_specs(spec) -> None:
    with pytest.raises(ValueError, match="EnvField.spec"):
        EnvField("TOKEN", spec)  # type: ignore[arg-type]


# --- unchanged behavior without directives or env tier -------------------------------------


def test_plain_specs_never_read_same_named_env(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("token", "env")
    monkeypatch.setenv("TOKEN", "env")

    [args] = ArgsContainer.load(_write(tmp_path, "other: 1\n"), token=(None, None))

    assert args.token is None
    assert args.origin("token") == "default"


def test_plain_specs_origins(tmp_path) -> None:
    [args] = ArgsContainer.load(
        _write(tmp_path, "b: config\n"), a=("cli", None), b=(None, None), c=(None, None)
    )

    assert (args.origin("a"), args.origin("b"), args.origin("c")) == ("cli", "config", "default")
    assert args.origin("unknown") is None
    assert args.to_dict() == {"a": "cli", "b": "config", "c": None}


def test_directive_free_documents_skip_env_resolution(tmp_path, monkeypatch) -> None:
    def forbidden(*_args, **_kwargs):
        raise AssertionError("resolver must not run without an _env directive")

    monkeypatch.setattr(args_container_module, "_EnvValueResolver", forbidden)
    monkeypatch.setenv("SEL", "a")
    text = (
        "_shared: &s {env: TOKEN, default: x}\n"
        "_select:\n"
        "  env: SEL\n"
        "  cases:\n"
        "    a:\n"
        "      _defaults: {opts: *s}\n"
        "      configs: [{name: a, list: [1, {k: v}]}]\n"
    )

    assert _load(tmp_path, text) == [
        {"opts": {"env": "TOKEN", "default": "x"}, "name": "a", "list": [1, {"k": "v"}]}
    ]
