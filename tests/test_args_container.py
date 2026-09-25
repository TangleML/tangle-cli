from __future__ import annotations

import importlib
import json
import time

import pytest

from tangle_cli.args_container import ArgsContainer, ConfigFileError


def test_load_none_returns_single_empty_config() -> None:
    [args] = ArgsContainer.load(None, name=(None, None))

    assert args.name is None
    assert args._config == {}


def test_load_yaml_object_and_cli_precedence(tmp_path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        "name: from-config\n"
        "limit: 5\n"
        "payload:\n"
        "  enabled: true\n",
        encoding="utf-8",
    )

    [args] = ArgsContainer.load(
        config,
        name=("from-cli", None),
        limit=(None, None),
        payload=(None, None),
    )

    assert args.name == "from-cli"
    assert args.limit == 5
    assert args.payload == {"enabled": True}


def test_load_json_list(tmp_path) -> None:
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps([
            {"name": "one"},
            {"name": "two"},
        ]),
        encoding="utf-8",
    )

    args = ArgsContainer.load(config, name=(None, None))

    assert [entry.name for entry in args] == ["one", "two"]


def test_load_defaults_configs_shape(tmp_path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        "_defaults:\n"
        "  base_url: https://api.default\n"
        "configs:\n"
        "  - name: a\n"
        "  - name: b\n"
        "    base_url: https://api.override\n",
        encoding="utf-8",
    )

    args = ArgsContainer.load(config, name=(None, None), base_url=(None, None))

    assert [(entry.name, entry.base_url) for entry in args] == [
        ("a", "https://api.default"),
        ("b", "https://api.override"),
    ]


def test_required_field_can_come_from_config(tmp_path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("digest: sha256:abc\n", encoding="utf-8")

    [args] = ArgsContainer.load(config, digest=(None,))

    assert args.digest == "sha256:abc"


def test_required_field_missing_raises() -> None:
    with pytest.raises(ConfigFileError, match="digest is required"):
        ArgsContainer.load(None, digest=(None,))


def test_json_converter_accepts_strings_and_objects(tmp_path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("body:\n  name: demo\n", encoding="utf-8")

    [from_config] = ArgsContainer.load(
        config,
        body=("body", None, None, True, False),
    )
    [from_cli] = ArgsContainer.load(
        None,
        body=("body", '{"name":"cli"}', None, True, False),
    )

    assert from_config.body == {"name": "demo"}
    assert from_cli.body == {"name": "cli"}


def test_invalid_config_shape_raises(tmp_path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("- ok\n", encoding="utf-8")

    with pytest.raises(ConfigFileError, match="entry 0 must be an object"):
        ArgsContainer.load(config, name=(None, None))


# ---------------------------------------------------------------------------
# ``_select`` environment-driven config selection
# ---------------------------------------------------------------------------


def _write(tmp_path, text: str, name: str = "config.yaml"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


TWO_CASE_CONFIG = (
    "_select:\n"
    "  env: TANGLE_ENV\n"
    "  cases:\n"
    "    dev:\n"
    "      name: dev-name\n"
    "      base_url: https://api.dev\n"
    "    prod:\n"
    "      name: prod-name\n"
    "      base_url: https://api.prod\n"
)


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [("dev", ("dev-name", "https://api.dev")), ("prod", ("prod-name", "https://api.prod"))],
)
def test_select_picks_each_case(tmp_path, monkeypatch, env_value, expected) -> None:
    config = _write(tmp_path, TWO_CASE_CONFIG)
    monkeypatch.setenv("TANGLE_ENV", env_value)

    [args] = ArgsContainer.load(config, name=(None, None), base_url=(None, None))

    assert (args.name, args.base_url) == expected


def test_select_match_is_case_sensitive(tmp_path, monkeypatch) -> None:
    config = _write(tmp_path, TWO_CASE_CONFIG)
    monkeypatch.setenv("TANGLE_ENV", "DEV")

    with pytest.raises(ConfigFileError, match="does not match any configured"):
        ArgsContainer.load(config, name=(None, None))


@pytest.mark.parametrize("env_value", [" dev", "dev ", "\tdev", "dev\n"])
def test_select_does_not_trim_env_value(tmp_path, monkeypatch, env_value) -> None:
    config = _write(tmp_path, TWO_CASE_CONFIG)
    monkeypatch.setenv("TANGLE_ENV", env_value)

    with pytest.raises(ConfigFileError, match="does not match any configured"):
        ArgsContainer.load(config, name=(None, None))


def test_select_does_not_interpolate_env_value(tmp_path, monkeypatch) -> None:
    config = _write(
        tmp_path,
        "_select:\n"
        "  env: TANGLE_ENV\n"
        "  cases:\n"
        "    ${OTHER}:\n"
        "      name: interpolated\n",
    )
    monkeypatch.setenv("OTHER", "dev")
    monkeypatch.setenv("TANGLE_ENV", "dev")

    with pytest.raises(ConfigFileError, match="does not match any configured"):
        ArgsContainer.load(config, name=(None, None))


def test_select_missing_env_fails_closed_without_default(tmp_path, monkeypatch) -> None:
    config = _write(tmp_path, TWO_CASE_CONFIG)
    monkeypatch.delenv("TANGLE_ENV", raising=False)

    with pytest.raises(ConfigFileError) as excinfo:
        ArgsContainer.load(config, name=(None, None))

    message = str(excinfo.value)
    assert "TANGLE_ENV" in message
    assert "is not set" in message
    # Fails closed: no implicit production/default branch is used.
    assert "prod-name" not in message


def test_select_empty_string_env_is_not_a_match(tmp_path, monkeypatch) -> None:
    config = _write(tmp_path, TWO_CASE_CONFIG)
    monkeypatch.setenv("TANGLE_ENV", "")

    with pytest.raises(ConfigFileError, match="does not match any configured"):
        ArgsContainer.load(config, name=(None, None))


def test_select_unknown_value_lists_cases_without_echoing_raw_value(
    tmp_path, monkeypatch
) -> None:
    config = _write(tmp_path, TWO_CASE_CONFIG)
    secret = "s3cr3t-tenant-name"
    monkeypatch.setenv("TANGLE_ENV", secret)

    with pytest.raises(ConfigFileError) as excinfo:
        ArgsContainer.load(config, name=(None, None))

    message = str(excinfo.value)
    assert secret not in message
    assert "'dev'" in message and "'prod'" in message


def test_select_unknown_value_with_control_chars_is_not_echoed(tmp_path, monkeypatch) -> None:
    config = _write(tmp_path, TWO_CASE_CONFIG)
    monkeypatch.setenv("TANGLE_ENV", "dev\x1b[31m\r\n\x07injected")

    with pytest.raises(ConfigFileError) as excinfo:
        ArgsContainer.load(config, name=(None, None))

    message = str(excinfo.value)
    assert "injected" not in message
    assert not any(not char.isprintable() for char in message)


def test_select_case_keys_with_control_chars_are_rejected(tmp_path, monkeypatch) -> None:
    config = _write(
        tmp_path,
        '_select:\n'
        '  env: TANGLE_ENV\n'
        '  cases:\n'
        '    "dev\\u001b[31m\\r\\n": {name: bad}\n',
    )
    monkeypatch.setenv("TANGLE_ENV", "dev")

    with pytest.raises(ConfigFileError) as excinfo:
        ArgsContainer.load(config, name=(None, None))

    message = str(excinfo.value)
    assert "must be non-empty printable strings" in message
    assert not any(not char.isprintable() for char in message)


def test_select_validation_runs_before_env_lookup(tmp_path, monkeypatch) -> None:
    config = _write(
        tmp_path,
        "_select:\n  env: TANGLE_ENV\n  cases: not-a-mapping\n",
    )
    monkeypatch.delenv("TANGLE_ENV", raising=False)

    with pytest.raises(ConfigFileError, match=r"_select\.cases must be an object"):
        ArgsContainer.load(config, name=(None, None))


@pytest.mark.parametrize(
    ("body", "match"),
    [
        ("_select: not-a-mapping\n", r"_select must be an object"),
        ("_select: []\n", r"_select must be an object"),
        ("_select:\n  cases:\n    dev: {}\n", r"requires an 'env'"),
        ("_select:\n  env: TANGLE_ENV\n", r"requires a 'cases'"),
        (
            "_select:\n  env: TANGLE_ENV\n  cases:\n    dev: {}\n  fallback: dev\n",
            r"supports only 'env', 'cases', and 'default'",
        ),
        (
            "_select:\n  env: TANGLE_ENV\n  cases:\n    dev: {}\n  else: dev\n",
            r"supports only 'env', 'cases', and 'default'",
        ),
        (
            "_select:\n  env: TANGLE_ENV\n  cases:\n    dev: {}\n  defaults:\n    a: b\n",
            r"supports only 'env', 'cases', and 'default'",
        ),
        (
            "_select:\n  env: TANGLE_ENV\n  cases:\n    dev: {}\n  _default: dev\n",
            r"supports only 'env', 'cases', and 'default'",
        ),
        ("_select:\n  env: 9BAD\n  cases:\n    dev: {}\n", r"_select\.env must be a valid"),
        ("_select:\n  env: 'A B'\n  cases:\n    dev: {}\n", r"_select\.env must be a valid"),
        ("_select:\n  env: ''\n  cases:\n    dev: {}\n", r"_select\.env must be a valid"),
        ("_select:\n  env: 42\n  cases:\n    dev: {}\n", r"_select\.env must be a valid"),
        ("_select:\n  env: TANGLE_ENV\n  cases: {}\n", r"at least one case"),
        (
            "_select:\n  env: TANGLE_ENV\n  cases:\n    - dev\n",
            r"_select\.cases must be an object",
        ),
        (
            "_select:\n  env: TANGLE_ENV\n  cases:\n    123: {}\n",
            r"case 0 is invalid",
        ),
        (
            "_select:\n  env: TANGLE_ENV\n  cases:\n    '': {}\n",
            r"case 0 is invalid",
        ),
    ],
)
def test_select_malformed_shapes_are_rejected(tmp_path, monkeypatch, body, match) -> None:
    config = _write(tmp_path, body)
    monkeypatch.setenv("TANGLE_ENV", "dev")

    with pytest.raises(ConfigFileError, match=match):
        ArgsContainer.load(config, name=(None, None))


@pytest.mark.parametrize("branch", ["dev: just-a-string", "dev: 5", "dev: null"])
def test_select_branch_must_be_object_or_list(tmp_path, monkeypatch, branch) -> None:
    config = _write(tmp_path, f"_select:\n  env: TANGLE_ENV\n  cases:\n    {branch}\n")
    monkeypatch.setenv("TANGLE_ENV", "dev")

    with pytest.raises(ConfigFileError, match="must be an object or a list"):
        ArgsContainer.load(config, name=(None, None))


MIXED_BRANCH_CONFIG = (
    "_select:\n"
    "  env: TANGLE_ENV\n"
    "  cases:\n"
    "    good:\n"
    "      name: ok\n"
    "    bad: just-a-scalar\n"
)


@pytest.mark.parametrize("env_value", ["good", "bad", "unknown", None])
def test_select_unselected_malformed_branch_fails_identically(
    tmp_path, monkeypatch, env_value
) -> None:
    """A malformed branch fails the same way no matter what the environment says."""

    config = _write(tmp_path, MIXED_BRANCH_CONFIG)
    if env_value is None:
        monkeypatch.delenv("TANGLE_ENV", raising=False)
    else:
        monkeypatch.setenv("TANGLE_ENV", env_value)

    with pytest.raises(ConfigFileError) as excinfo:
        ArgsContainer.load(config, name=(None, None))

    message = str(excinfo.value)
    assert message == "_select case 'bad' must be an object or a list, got str"


def test_select_malformed_branch_is_reported_before_env_lookup(tmp_path, monkeypatch) -> None:
    config = _write(tmp_path, MIXED_BRANCH_CONFIG)
    monkeypatch.delenv("TANGLE_ENV", raising=False)

    # Shape wins over the missing-environment diagnostic.
    with pytest.raises(ConfigFileError, match="must be an object or a list"):
        ArgsContainer.load(config, name=(None, None))


NESTED_MALFORMED_CONFIG = (
    "_select:\n"
    "  env: TANGLE_ENV\n"
    "  cases:\n"
    "    dev:\n"
    "      name: dev-name\n"
    "    prod:\n"
    "      _select:\n"
    "        env: TANGLE_REGION\n"
    "        cases:\n"
    "          us: just-a-scalar\n"
)


@pytest.mark.parametrize("env_value", ["dev", "prod", "unknown", None])
def test_select_nested_dormant_branch_is_structurally_validated(
    tmp_path, monkeypatch, env_value
) -> None:
    """Structural validation recurses through nested selectors, selected or not."""

    config = _write(tmp_path, NESTED_MALFORMED_CONFIG)
    monkeypatch.delenv("TANGLE_REGION", raising=False)
    if env_value is None:
        monkeypatch.delenv("TANGLE_ENV", raising=False)
    else:
        monkeypatch.setenv("TANGLE_ENV", env_value)

    with pytest.raises(ConfigFileError) as excinfo:
        ArgsContainer.load(config, name=(None, None))

    assert str(excinfo.value) == "_select case 'us' must be an object or a list, got str"


def test_select_dormant_branch_env_is_not_required(tmp_path, monkeypatch) -> None:
    """Only env *lookups* stay lazy: a dormant selector needs no variable set."""

    config = _write(
        tmp_path,
        "_select:\n"
        "  env: TANGLE_ENV\n"
        "  cases:\n"
        "    dev:\n"
        "      name: dev-name\n"
        "    prod:\n"
        "      _select:\n"
        "        env: TANGLE_REGION\n"
        "        cases:\n"
        "          us:\n"
        "            name: prod-us\n",
    )
    monkeypatch.setenv("TANGLE_ENV", "dev")
    monkeypatch.delenv("TANGLE_REGION", raising=False)

    [args] = ArgsContainer.load(config, name=(None, None))

    assert args.name == "dev-name"


# Malformed documents that must be rejected identically whether they sit at the
# top level, in a selected case, in a dormant case, or in a ``default`` branch.
MALFORMED_DOCUMENTS = {
    "scalar_in_list": [["just-a-scalar"], "entry 0 must be an object"],
    "mixed_list": [[{"name": "ok"}, "scalar"], "entry 1 must be an object"],
    "int_in_list": [[3], "entry 0 must be an object"],
    "null_in_list": [[None], "entry 0 must be an object"],
    "bad_defaults": [{"_defaults": "scalar", "configs": []}, "_defaults must be an object"],
    "list_defaults": [{"_defaults": [], "configs": []}, "_defaults must be an object"],
    "bad_configs": [{"_defaults": {}, "configs": "scalar"}, "configs must be a list"],
    "null_configs": [{"configs": None}, "configs must be a list"],
    "mapping_configs": [{"configs": {"a": 1}}, "configs must be a list"],
    "bad_configs_entry": [
        {"_defaults": {}, "configs": [{"name": "ok"}, 5]},
        "configs entry 1 must be an object",
    ],
}


@pytest.mark.parametrize("case_name", sorted(MALFORMED_DOCUMENTS))
@pytest.mark.parametrize("placement", ["selected_case", "dormant_case", "default"])
@pytest.mark.parametrize("env_state", ["match", "unmatched", "unset"])
def test_malformed_branch_rejected_regardless_of_placement_or_env(
    tmp_path, monkeypatch, case_name, placement, env_state
) -> None:
    document, expected = MALFORMED_DOCUMENTS[case_name]
    good = {"name": "ok"}
    if placement == "selected_case":
        selector = {"env": "TANGLE_ENV", "cases": {"dev": document, "other": good}}
    elif placement == "dormant_case":
        selector = {"env": "TANGLE_ENV", "cases": {"dev": good, "other": document}}
    else:
        selector = {"env": "TANGLE_ENV", "cases": {"dev": good}, "default": document}

    config = _write(
        tmp_path, json.dumps({"_select": selector}), name=f"{case_name}-{placement}.json"
    )
    if env_state == "unset":
        monkeypatch.delenv("TANGLE_ENV", raising=False)
    else:
        monkeypatch.setenv("TANGLE_ENV", "dev" if env_state == "match" else "nope")

    with pytest.raises(ConfigFileError, match=expected):
        ArgsContainer.load(config, name=(None, None))


@pytest.mark.parametrize("case_name", sorted(MALFORMED_DOCUMENTS))
def test_malformed_branch_matches_top_level_loader_rejection(
    tmp_path, monkeypatch, case_name
) -> None:
    """Branch prevalidation stays in lockstep with the plain top-level loader."""

    document, expected = MALFORMED_DOCUMENTS[case_name]
    top_level = _write(tmp_path, json.dumps(document), name=f"{case_name}-top.json")
    branched = _write(
        tmp_path,
        json.dumps({"_select": {"env": "TANGLE_ENV", "cases": {"dev": document}}}),
        name=f"{case_name}-branch.json",
    )
    monkeypatch.setenv("TANGLE_ENV", "dev")

    with pytest.raises(ConfigFileError, match=expected):
        ArgsContainer.load(top_level, name=(None, None))
    with pytest.raises(ConfigFileError, match=expected):
        ArgsContainer.load(branched, name=(None, None))


# Documents the plain loader accepts must stay acceptable as branches too.
WELL_FORMED_DOCUMENTS = {
    "empty_list": [[], []],
    "empty_configs": [{"_defaults": {"name": "d"}, "configs": []}, []],
    "empty_mapping": [{}, [None]],
    "single_mapping": [{"name": "one"}, ["one"]],
    "list_of_mappings": [[{"name": "one"}, {"name": "two"}], ["one", "two"]],
    "defaults_configs": [
        {"_defaults": {"name": "d"}, "configs": [{}, {"name": "override"}]},
        ["d", "override"],
    ],
    "configs_without_defaults": [{"configs": [{"name": "one"}]}, ["one"]],
}


@pytest.mark.parametrize("case_name", sorted(WELL_FORMED_DOCUMENTS))
@pytest.mark.parametrize("placement", ["top_level", "selected_case", "default"])
def test_well_formed_documents_load_identically_at_top_level_and_as_branches(
    tmp_path, monkeypatch, case_name, placement
) -> None:
    document, expected = WELL_FORMED_DOCUMENTS[case_name]
    if placement == "top_level":
        payload = document
        monkeypatch.delenv("TANGLE_ENV", raising=False)
    elif placement == "selected_case":
        payload = {"_select": {"env": "TANGLE_ENV", "cases": {"dev": document}}}
        monkeypatch.setenv("TANGLE_ENV", "dev")
    else:
        payload = {
            "_select": {
                "env": "TANGLE_ENV",
                "cases": {"dev": {"name": "unused"}},
                "default": document,
            }
        }
        monkeypatch.delenv("TANGLE_ENV", raising=False)

    config = _write(tmp_path, json.dumps(payload), name=f"{case_name}-{placement}.json")

    args = ArgsContainer.load(config, name=(None, None))

    assert [entry.name for entry in args] == expected


def test_dormant_nested_selector_shape_is_validated(tmp_path, monkeypatch) -> None:
    """Recursive structural validation covers the nested selector's own shape."""

    config = _write(
        tmp_path,
        json.dumps(
            {
                "_select": {
                    "env": "TANGLE_ENV",
                    "cases": {
                        "dev": {"name": "dev-name"},
                        "prod": {"_select": {"env": "9BAD", "cases": {"us": {}}}},
                    },
                }
            }
        ),
        name="dormant-bad-env-name.json",
    )
    monkeypatch.setenv("TANGLE_ENV", "dev")

    with pytest.raises(ConfigFileError, match=r"_select\.env must be a valid"):
        ArgsContainer.load(config, name=(None, None))


def test_dormant_nested_selector_alias_is_rejected(tmp_path, monkeypatch) -> None:
    config = _write(
        tmp_path,
        json.dumps(
            {
                "_select": {
                    "env": "TANGLE_ENV",
                    "cases": {"dev": {"name": "dev-name"}},
                    "default": {
                        "_select": {
                            "env": "TANGLE_REGION",
                            "cases": {"us": {}},
                            "else": {},
                        }
                    },
                }
            }
        ),
        name="dormant-alias.json",
    )
    monkeypatch.setenv("TANGLE_ENV", "dev")

    with pytest.raises(ConfigFileError, match=r"supports only 'env', 'cases', and 'default'"):
        ArgsContainer.load(config, name=(None, None))


def _nested_select_config(depth: int) -> dict:
    node: dict = {"name": f"depth-{depth}"}
    for _ in range(depth):
        node = {"_select": {"env": "TANGLE_ENV", "cases": {"dev": node}}}
    return node


@pytest.mark.parametrize("depth", [1, 2, 31, 32])
def test_select_nesting_at_or_below_max_depth_is_accepted(tmp_path, monkeypatch, depth) -> None:
    config = _write(
        tmp_path, json.dumps(_nested_select_config(depth)), name=f"nested-{depth}.json"
    )
    monkeypatch.setenv("TANGLE_ENV", "dev")

    [args] = ArgsContainer.load(config, name=(None, None))

    assert args.name == f"depth-{depth}"


def test_select_nesting_above_max_depth_is_rejected(tmp_path, monkeypatch) -> None:
    config = _write(tmp_path, json.dumps(_nested_select_config(33)), name="nested-33.json")
    monkeypatch.setenv("TANGLE_ENV", "dev")

    with pytest.raises(ConfigFileError, match="exceeded the maximum depth of 32"):
        ArgsContainer.load(config, name=(None, None))


def test_select_rejects_ordinary_sibling_keys(tmp_path, monkeypatch) -> None:
    config = _write(
        tmp_path,
        "name: leaked\n"
        "_select:\n"
        "  env: TANGLE_ENV\n"
        "  cases:\n"
        "    dev:\n"
        "      name: dev-name\n",
    )
    monkeypatch.setenv("TANGLE_ENV", "dev")

    with pytest.raises(ConfigFileError, match="must be the only regular key"):
        ArgsContainer.load(config, name=(None, None))


def test_select_rejects_defaults_and_configs_siblings(tmp_path, monkeypatch) -> None:
    config = _write(
        tmp_path,
        "_defaults:\n"
        "  base_url: https://api.default\n"
        "configs:\n"
        "  - name: a\n"
        "_select:\n"
        "  env: TANGLE_ENV\n"
        "  cases:\n"
        "    dev:\n"
        "      name: dev-name\n",
    )
    monkeypatch.setenv("TANGLE_ENV", "dev")

    # ``configs`` is an ordinary key, so it is refused; ``_defaults`` is not.
    with pytest.raises(ConfigFileError, match="must be the only regular key"):
        ArgsContainer.load(config, name=(None, None))


def test_select_allows_underscore_helper_siblings_and_anchors(tmp_path, monkeypatch) -> None:
    config = _write(
        tmp_path,
        "_shared: &shared\n"
        "  base_url: https://api.shared\n"
        "  log_type: none\n"
        "_select:\n"
        "  env: TANGLE_ENV\n"
        "  cases:\n"
        "    dev:\n"
        "      <<: *shared\n"
        "      name: dev-name\n"
        "    prod:\n"
        "      <<: *shared\n"
        "      name: prod-name\n"
        "      base_url: https://api.prod\n",
    )
    monkeypatch.setenv("TANGLE_ENV", "dev")

    [args] = ArgsContainer.load(
        config, name=(None, None), base_url=(None, None), log_type=(None, None)
    )

    assert (args.name, args.base_url, args.log_type) == ("dev-name", "https://api.shared", "none")


def test_select_branch_single_mapping_shape(tmp_path, monkeypatch) -> None:
    config = _write(
        tmp_path,
        "_select:\n  env: TANGLE_ENV\n  cases:\n    dev:\n      name: only\n",
    )
    monkeypatch.setenv("TANGLE_ENV", "dev")

    args = ArgsContainer.load(config, name=(None, None))

    assert [entry.name for entry in args] == ["only"]


def test_select_branch_list_shape(tmp_path, monkeypatch) -> None:
    config = _write(
        tmp_path,
        "_select:\n"
        "  env: TANGLE_ENV\n"
        "  cases:\n"
        "    dev:\n"
        "      - name: one\n"
        "      - name: two\n",
    )
    monkeypatch.setenv("TANGLE_ENV", "dev")

    args = ArgsContainer.load(config, name=(None, None))

    assert [entry.name for entry in args] == ["one", "two"]


def test_select_branch_defaults_and_configs_shape(tmp_path, monkeypatch) -> None:
    config = _write(
        tmp_path,
        "_select:\n"
        "  env: TANGLE_ENV\n"
        "  cases:\n"
        "    dev:\n"
        "      _defaults:\n"
        "        base_url: https://api.dev\n"
        "      configs:\n"
        "        - name: a\n"
        "        - name: b\n"
        "          base_url: https://api.override\n"
        "    prod:\n"
        "      _defaults:\n"
        "        base_url: https://api.prod\n"
        "      configs:\n"
        "        - name: p\n",
    )
    monkeypatch.setenv("TANGLE_ENV", "dev")

    args = ArgsContainer.load(config, name=(None, None), base_url=(None, None))

    assert [(entry.name, entry.base_url) for entry in args] == [
        ("a", "https://api.dev"),
        ("b", "https://api.override"),
    ]


def test_select_branch_defaults_and_configs_errors_still_apply(tmp_path, monkeypatch) -> None:
    config = _write(
        tmp_path,
        "_select:\n"
        "  env: TANGLE_ENV\n"
        "  cases:\n"
        "    dev:\n"
        "      _defaults: not-an-object\n"
        "      configs:\n"
        "        - name: a\n",
    )
    monkeypatch.setenv("TANGLE_ENV", "dev")

    with pytest.raises(ConfigFileError, match="_defaults must be an object"):
        ArgsContainer.load(config, name=(None, None))


def test_select_branch_list_entry_shape_is_validated(tmp_path, monkeypatch) -> None:
    config = _write(
        tmp_path,
        "_select:\n  env: TANGLE_ENV\n  cases:\n    dev:\n      - ok\n",
    )
    monkeypatch.setenv("TANGLE_ENV", "dev")

    with pytest.raises(ConfigFileError, match="entry 0 must be an object"):
        ArgsContainer.load(config, name=(None, None))


def test_select_nested_multi_dimension(tmp_path, monkeypatch) -> None:
    config = _write(
        tmp_path,
        "_select:\n"
        "  env: TANGLE_ENV\n"
        "  cases:\n"
        "    dev:\n"
        "      _select:\n"
        "        env: TANGLE_REGION\n"
        "        cases:\n"
        "          us:\n"
        "            name: dev-us\n"
        "          eu:\n"
        "            name: dev-eu\n"
        "    prod:\n"
        "      _select:\n"
        "        env: TANGLE_REGION\n"
        "        cases:\n"
        "          us:\n"
        "            name: prod-us\n",
    )
    monkeypatch.setenv("TANGLE_ENV", "dev")
    monkeypatch.setenv("TANGLE_REGION", "eu")

    [args] = ArgsContainer.load(config, name=(None, None))

    assert args.name == "dev-eu"


def test_select_nested_inner_env_missing_fails_closed(tmp_path, monkeypatch) -> None:
    config = _write(
        tmp_path,
        "_select:\n"
        "  env: TANGLE_ENV\n"
        "  cases:\n"
        "    dev:\n"
        "      _select:\n"
        "        env: TANGLE_REGION\n"
        "        cases:\n"
        "          us:\n"
        "            name: dev-us\n",
    )
    monkeypatch.setenv("TANGLE_ENV", "dev")
    monkeypatch.delenv("TANGLE_REGION", raising=False)

    with pytest.raises(ConfigFileError, match="TANGLE_REGION .* is not set"):
        ArgsContainer.load(config, name=(None, None))


def test_select_nested_branch_can_be_defaults_configs(tmp_path, monkeypatch) -> None:
    config = _write(
        tmp_path,
        "_select:\n"
        "  env: TANGLE_ENV\n"
        "  cases:\n"
        "    dev:\n"
        "      _select:\n"
        "        env: TANGLE_REGION\n"
        "        cases:\n"
        "          us:\n"
        "            _defaults:\n"
        "              base_url: https://us.dev\n"
        "            configs:\n"
        "              - name: a\n"
        "              - name: b\n",
    )
    monkeypatch.setenv("TANGLE_ENV", "dev")
    monkeypatch.setenv("TANGLE_REGION", "us")

    args = ArgsContainer.load(config, name=(None, None), base_url=(None, None))

    assert [(entry.name, entry.base_url) for entry in args] == [
        ("a", "https://us.dev"),
        ("b", "https://us.dev"),
    ]


def test_select_recursive_alias_hits_depth_guard(tmp_path, monkeypatch) -> None:
    config = _write(
        tmp_path,
        "&loop\n_select:\n  env: TANGLE_ENV\n  cases:\n    dev: *loop\n",
    )
    monkeypatch.setenv("TANGLE_ENV", "dev")

    with pytest.raises(ConfigFileError, match="exceeded the maximum depth"):
        ArgsContainer.load(config, name=(None, None))


def test_select_works_in_json_configs(tmp_path, monkeypatch) -> None:
    config = _write(
        tmp_path,
        json.dumps({"_select": {"env": "TANGLE_ENV", "cases": {"dev": {"name": "json-dev"}}}}),
        name="config.json",
    )
    monkeypatch.setenv("TANGLE_ENV", "dev")

    [args] = ArgsContainer.load(config, name=(None, None))

    assert args.name == "json-dev"


def test_select_is_an_exact_key_not_a_prefix(tmp_path, monkeypatch) -> None:
    config = _write(tmp_path, "_selector:\n  env: TANGLE_ENV\nname: plain\n")
    monkeypatch.delenv("TANGLE_ENV", raising=False)

    [args] = ArgsContainer.load(config, name=(None, None))

    assert args.name == "plain"


def test_non_selector_configs_are_unchanged_with_env_set(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TANGLE_ENV", "prod")
    single = _write(tmp_path, "name: plain\n_helper: ignored\n", name="single.yaml")
    listed = _write(tmp_path, "- name: one\n- name: two\n", name="list.yaml")
    defaulted = _write(
        tmp_path,
        "_defaults:\n  base_url: https://api.default\nconfigs:\n  - name: a\n",
        name="defaults.yaml",
    )

    [one] = ArgsContainer.load(single, name=(None, None))
    listed_args = ArgsContainer.load(listed, name=(None, None))
    [defaulted_args] = ArgsContainer.load(defaulted, name=(None, None), base_url=(None, None))

    assert one.name == "plain"
    assert [entry.name for entry in listed_args] == ["one", "two"]
    assert (defaulted_args.name, defaulted_args.base_url) == ("a", "https://api.default")


def test_select_cli_precedence_and_raw_config_are_branch_scoped(tmp_path, monkeypatch) -> None:
    config = _write(tmp_path, TWO_CASE_CONFIG)
    monkeypatch.setenv("TANGLE_ENV", "prod")

    [args] = ArgsContainer.load(
        config, name=("from-cli", None), base_url=(None, None)
    )

    assert args.name == "from-cli"
    assert args.base_url == "https://api.prod"
    # Downstream commands only ever see the selected branch, never the selector.
    assert args._config == {"name": "prod-name", "base_url": "https://api.prod"}
    assert "_select" not in args._config
    assert args.to_dict() == {"name": "from-cli", "base_url": "https://api.prod"}


def test_select_downstream_required_field_validation_sees_selected_branch(
    tmp_path, monkeypatch
) -> None:
    config = _write(
        tmp_path,
        "_select:\n"
        "  env: TANGLE_ENV\n"
        "  cases:\n"
        "    dev:\n"
        "      digest: sha256:dev\n"
        "    prod: {}\n",
    )

    monkeypatch.setenv("TANGLE_ENV", "dev")
    [args] = ArgsContainer.load(config, digest=(None,))
    assert args.digest == "sha256:dev"

    monkeypatch.setenv("TANGLE_ENV", "prod")
    with pytest.raises(ConfigFileError, match="digest is required"):
        ArgsContainer.load(config, digest=(None,))


def test_select_downstream_converters_apply_to_selected_branch(tmp_path, monkeypatch) -> None:
    config = _write(
        tmp_path,
        "_select:\n"
        "  env: TANGLE_ENV\n"
        "  cases:\n"
        "    dev:\n"
        "      body:\n"
        "        name: demo\n",
    )
    monkeypatch.setenv("TANGLE_ENV", "dev")

    [args] = ArgsContainer.load(config, body=("body", None, None, True, False))

    assert args.body == {"name": "demo"}


# ---------------------------------------------------------------------------
# Explicit ``_select.default`` fallback branch
# ---------------------------------------------------------------------------


DEFAULT_CONFIG = (
    "_select:\n"
    "  env: TANGLE_ENV\n"
    "  cases:\n"
    "    dev:\n"
    "      name: dev-name\n"
    "    prod:\n"
    "      name: prod-name\n"
    "  default:\n"
    "    name: fallback-name\n"
)


def test_select_default_used_when_env_missing(tmp_path, monkeypatch) -> None:
    config = _write(tmp_path, DEFAULT_CONFIG)
    monkeypatch.delenv("TANGLE_ENV", raising=False)

    [args] = ArgsContainer.load(config, name=(None, None))

    assert args.name == "fallback-name"


@pytest.mark.parametrize(
    "env_value", ["", "DEV", " dev", "dev ", "staging", "s3cr3t-tenant", "dev\x1b[31m\r\n"]
)
def test_select_default_used_when_value_does_not_match(tmp_path, monkeypatch, env_value) -> None:
    config = _write(tmp_path, DEFAULT_CONFIG)
    monkeypatch.setenv("TANGLE_ENV", env_value)

    [args] = ArgsContainer.load(config, name=(None, None))

    assert args.name == "fallback-name"


@pytest.mark.parametrize(
    ("env_value", "expected"), [("dev", "dev-name"), ("prod", "prod-name")]
)
def test_select_exact_case_beats_default(tmp_path, monkeypatch, env_value, expected) -> None:
    config = _write(tmp_path, DEFAULT_CONFIG)
    monkeypatch.setenv("TANGLE_ENV", env_value)

    [args] = ArgsContainer.load(config, name=(None, None))

    assert args.name == expected


def test_select_without_default_still_fails_closed(tmp_path, monkeypatch) -> None:
    """No implicit fallback: the no-``default`` contract is unchanged."""

    config = _write(tmp_path, TWO_CASE_CONFIG)

    monkeypatch.delenv("TANGLE_ENV", raising=False)
    with pytest.raises(ConfigFileError, match="is not set"):
        ArgsContainer.load(config, name=(None, None))

    monkeypatch.setenv("TANGLE_ENV", "staging")
    with pytest.raises(ConfigFileError, match="does not match any configured"):
        ArgsContainer.load(config, name=(None, None))


def test_select_default_does_not_echo_unmatched_raw_env(tmp_path, monkeypatch, capsys) -> None:
    config = _write(tmp_path, DEFAULT_CONFIG)
    secret = "s3cr3t-tenant\x1b[31m"
    monkeypatch.setenv("TANGLE_ENV", secret)

    [args] = ArgsContainer.load(config, name=(None, None))

    captured = capsys.readouterr()
    assert args.name == "fallback-name"
    assert "s3cr3t-tenant" not in captured.out + captured.err
    assert args.to_dict() == {"name": "fallback-name"}
    assert args._config == {"name": "fallback-name"}


@pytest.mark.parametrize("bad", ["just-a-scalar", "5", "true", "null", ""])
@pytest.mark.parametrize("env_state", ["match", "unmatched", "unset"])
def test_select_malformed_default_rejected_pre_env_in_every_env_state(
    tmp_path, monkeypatch, bad, env_state
) -> None:
    config = _write(
        tmp_path,
        "_select:\n"
        "  env: TANGLE_ENV\n"
        "  cases:\n"
        "    dev:\n"
        "      name: dev-name\n"
        f"  default: {bad}\n",
    )
    if env_state == "unset":
        monkeypatch.delenv("TANGLE_ENV", raising=False)
    else:
        monkeypatch.setenv("TANGLE_ENV", "dev" if env_state == "match" else "nope")

    with pytest.raises(ConfigFileError, match=r"_select\.default must be an object or a list"):
        ArgsContainer.load(config, name=(None, None))


def test_select_default_branch_single_mapping_shape(tmp_path, monkeypatch) -> None:
    config = _write(tmp_path, DEFAULT_CONFIG)
    monkeypatch.delenv("TANGLE_ENV", raising=False)

    args = ArgsContainer.load(config, name=(None, None))

    assert [entry.name for entry in args] == ["fallback-name"]


def test_select_default_branch_list_shape(tmp_path, monkeypatch) -> None:
    config = _write(
        tmp_path,
        "_select:\n"
        "  env: TANGLE_ENV\n"
        "  cases:\n"
        "    dev:\n"
        "      name: dev-name\n"
        "  default:\n"
        "    - name: one\n"
        "    - name: two\n",
    )
    monkeypatch.delenv("TANGLE_ENV", raising=False)

    args = ArgsContainer.load(config, name=(None, None))

    assert [entry.name for entry in args] == ["one", "two"]


def test_select_default_branch_defaults_and_configs_shape(tmp_path, monkeypatch) -> None:
    config = _write(
        tmp_path,
        "_select:\n"
        "  env: TANGLE_ENV\n"
        "  cases:\n"
        "    dev:\n"
        "      name: dev-name\n"
        "  default:\n"
        "    _defaults:\n"
        "      base_url: https://api.fallback\n"
        "    configs:\n"
        "      - name: a\n"
        "      - name: b\n"
        "        base_url: https://api.override\n",
    )
    monkeypatch.setenv("TANGLE_ENV", "unmatched")

    args = ArgsContainer.load(config, name=(None, None), base_url=(None, None))

    assert [(entry.name, entry.base_url) for entry in args] == [
        ("a", "https://api.fallback"),
        ("b", "https://api.override"),
    ]


def test_select_default_branch_shape_errors_still_apply(tmp_path, monkeypatch) -> None:
    config = _write(
        tmp_path,
        "_select:\n"
        "  env: TANGLE_ENV\n"
        "  cases:\n"
        "    dev:\n"
        "      name: dev-name\n"
        "  default:\n"
        "    - ok\n",
    )
    monkeypatch.delenv("TANGLE_ENV", raising=False)

    with pytest.raises(ConfigFileError, match="entry 0 must be an object"):
        ArgsContainer.load(config, name=(None, None))


def test_select_default_branch_can_be_nested_selector(tmp_path, monkeypatch) -> None:
    config = _write(
        tmp_path,
        "_select:\n"
        "  env: TANGLE_ENV\n"
        "  cases:\n"
        "    dev:\n"
        "      name: dev-name\n"
        "  default:\n"
        "    _select:\n"
        "      env: TANGLE_REGION\n"
        "      cases:\n"
        "        us:\n"
        "          name: fallback-us\n"
        "      default:\n"
        "        name: fallback-anywhere\n",
    )
    monkeypatch.delenv("TANGLE_ENV", raising=False)

    monkeypatch.setenv("TANGLE_REGION", "us")
    [us] = ArgsContainer.load(config, name=(None, None))
    assert us.name == "fallback-us"

    monkeypatch.delenv("TANGLE_REGION", raising=False)
    [anywhere] = ArgsContainer.load(config, name=(None, None))
    assert anywhere.name == "fallback-anywhere"


def test_select_nested_default_can_still_fail_closed(tmp_path, monkeypatch) -> None:
    """An outer ``default`` does not grant the inner selector a fallback."""

    config = _write(
        tmp_path,
        "_select:\n"
        "  env: TANGLE_ENV\n"
        "  cases:\n"
        "    dev:\n"
        "      name: dev-name\n"
        "  default:\n"
        "    _select:\n"
        "      env: TANGLE_REGION\n"
        "      cases:\n"
        "        us:\n"
        "          name: fallback-us\n",
    )
    monkeypatch.delenv("TANGLE_ENV", raising=False)
    monkeypatch.delenv("TANGLE_REGION", raising=False)

    with pytest.raises(ConfigFileError, match="TANGLE_REGION .* is not set"):
        ArgsContainer.load(config, name=(None, None))


def test_select_default_is_not_a_magic_case_key(tmp_path, monkeypatch) -> None:
    """A case literally named ``default`` is an ordinary exact-match case."""

    config = _write(
        tmp_path,
        "_select:\n"
        "  env: TANGLE_ENV\n"
        "  cases:\n"
        "    default:\n"
        "      name: case-named-default\n",
    )

    monkeypatch.setenv("TANGLE_ENV", "default")
    [matched] = ArgsContainer.load(config, name=(None, None))
    assert matched.name == "case-named-default"

    # Without a sibling ``default`` branch it still fails closed.
    monkeypatch.setenv("TANGLE_ENV", "other")
    with pytest.raises(ConfigFileError, match="does not match any configured"):
        ArgsContainer.load(config, name=(None, None))


def test_select_case_named_default_and_default_branch_coexist(tmp_path, monkeypatch) -> None:
    config = _write(
        tmp_path,
        "_select:\n"
        "  env: TANGLE_ENV\n"
        "  cases:\n"
        "    default:\n"
        "      name: case-named-default\n"
        "  default:\n"
        "    name: fallback-branch\n",
    )

    monkeypatch.setenv("TANGLE_ENV", "default")
    [matched] = ArgsContainer.load(config, name=(None, None))
    assert matched.name == "case-named-default"

    monkeypatch.setenv("TANGLE_ENV", "other")
    [fell_back] = ArgsContainer.load(config, name=(None, None))
    assert fell_back.name == "fallback-branch"


def test_select_default_still_requires_valid_cases(tmp_path, monkeypatch) -> None:
    """``default`` does not soften validation of the selector or its cases."""

    config = _write(
        tmp_path,
        "_select:\n"
        "  env: TANGLE_ENV\n"
        "  cases:\n"
        "    dev: just-a-scalar\n"
        "  default:\n"
        "    name: fallback-name\n",
    )
    monkeypatch.delenv("TANGLE_ENV", raising=False)

    with pytest.raises(ConfigFileError, match=r"_select case 'dev' must be an object or a list"):
        ArgsContainer.load(config, name=(None, None))


def test_select_default_in_json_config(tmp_path, monkeypatch) -> None:
    config = _write(
        tmp_path,
        json.dumps(
            {
                "_select": {
                    "env": "TANGLE_ENV",
                    "cases": {"dev": {"name": "json-dev"}},
                    "default": {"name": "json-fallback"},
                }
            }
        ),
        name="config.json",
    )
    monkeypatch.delenv("TANGLE_ENV", raising=False)

    [args] = ArgsContainer.load(config, name=(None, None))

    assert args.name == "json-fallback"


def test_select_default_downstream_required_field_validation(tmp_path, monkeypatch) -> None:
    config = _write(
        tmp_path,
        "_select:\n"
        "  env: TANGLE_ENV\n"
        "  cases:\n"
        "    dev:\n"
        "      digest: sha256:dev\n"
        "  default: {}\n",
    )
    monkeypatch.delenv("TANGLE_ENV", raising=False)

    with pytest.raises(ConfigFileError, match="digest is required"):
        ArgsContainer.load(config, digest=(None,))


# ---------------------------------------------------------------------------
# Shared-alias (DAG) structural validation must stay linear
# ---------------------------------------------------------------------------


def _alias_dag_yaml(levels: int) -> str:
    """Selector chain where every level is reached through two aliased edges.

    Naive recursion revalidates the shared subtree once per incoming edge and
    costs O(2**levels); memoized validation is linear in the node count.
    """

    parts = ["_n0: &n0\n  name: leaf\n"]
    for index in range(1, levels + 1):
        parts.append(
            f"_n{index}: &n{index}\n"
            "  _select:\n"
            "    env: TANGLE_ENV\n"
            "    cases:\n"
            f"      a: *n{index - 1}\n"
            f"      b: *n{index - 1}\n"
        )
    parts.append(
        "_select:\n"
        "  env: TANGLE_ENV\n"
        "  cases:\n"
        f"    a: *n{levels}\n"
        f"    b: *n{levels}\n"
    )
    return "".join(parts)


class _ValidationBudgetExceeded(Exception):
    """Raised by the instrumented validator when a linear budget is blown."""


@pytest.fixture
def selector_visit_budget(monkeypatch):
    """Count structural selector validations and abort as soon as a cap is hit.

    Aborting mid-load matters: a regression to exponential validation would
    otherwise hang the suite instead of failing it.
    """

    original = ArgsContainer._validate_selector_node
    state = {"calls": 0, "cap": 10**9}

    def counting(node, depth=0, memo=None, **kwargs):
        state["calls"] += 1
        if state["calls"] > state["cap"]:
            raise _ValidationBudgetExceeded(
                f"structural validation exceeded {state['cap']} selector visits"
            )
        return original(node, depth, memo, **kwargs)

    monkeypatch.setattr(ArgsContainer, "_validate_selector_node", staticmethod(counting))
    return state


@pytest.mark.parametrize("levels", [5, 10, 20, 29])
def test_alias_dag_validation_is_linear_not_exponential(
    tmp_path, monkeypatch, selector_visit_budget, levels
) -> None:
    config = _write(tmp_path, _alias_dag_yaml(levels), name=f"dag-{levels}.yaml")
    monkeypatch.setenv("TANGLE_ENV", "a")
    # Linear headroom; naive recursion would be ~2**levels for these inputs.
    selector_visit_budget["cap"] = 10 * (levels + 1)

    [args] = ArgsContainer.load(config, name=(None, None))

    assert args.name == "leaf"
    assert selector_visit_budget["calls"] <= 10 * (levels + 1)


def test_alias_dag_near_depth_limit_completes_quickly(
    tmp_path, monkeypatch, selector_visit_budget
) -> None:
    config = _write(tmp_path, _alias_dag_yaml(29), name="dag-29.yaml")
    monkeypatch.setenv("TANGLE_ENV", "a")
    selector_visit_budget["cap"] = 10 * 30

    start = time.perf_counter()
    [args] = ArgsContainer.load(config, name=(None, None))
    elapsed = time.perf_counter() - start

    assert args.name == "leaf"
    # Deliberately loose so it cannot flake on a slow machine; the exponential
    # behavior this guards took multiple seconds by depth 18 alone.
    assert elapsed < 5.0


def test_alias_dag_malformed_shared_leaf_still_rejected(
    tmp_path, monkeypatch, selector_visit_budget
) -> None:
    """Memoization must not let a shared malformed branch slip through."""

    config = _write(
        tmp_path,
        _alias_dag_yaml(12).replace("_n0: &n0\n  name: leaf\n", "_n0: &n0 just-a-scalar\n"),
        name="dag-bad-leaf.yaml",
    )
    monkeypatch.setenv("TANGLE_ENV", "a")
    selector_visit_budget["cap"] = 10 * 13

    with pytest.raises(ConfigFileError, match="must be an object or a list"):
        ArgsContainer.load(config, name=(None, None))


def _shared_node_at_two_depths_yaml(deep_levels: int, prefix_levels: int) -> str:
    """Anchor one chain, then reach it both shallowly and far too deeply.

    The shallow edge is validated first, so a memo that ignored remaining depth
    would wrongly accept the deep edge.
    """

    parts = ["_d0: &d0\n  name: leaf\n"]
    for index in range(1, deep_levels + 1):
        parts.append(
            f"_d{index}: &d{index}\n"
            "  _select:\n"
            "    env: TANGLE_ENV\n"
            "    cases:\n"
            f"      a: *d{index - 1}\n"
        )
    parts.append(
        f"_k1: &k1\n  _select:\n    env: TANGLE_ENV\n    cases:\n      a: *d{deep_levels}\n"
    )
    for index in range(2, prefix_levels + 1):
        parts.append(
            f"_k{index}: &k{index}\n"
            "  _select:\n"
            "    env: TANGLE_ENV\n"
            "    cases:\n"
            f"      a: *k{index - 1}\n"
        )
    parts.append(
        "_select:\n"
        "  env: TANGLE_ENV\n"
        "  cases:\n"
        f"    a: *d{deep_levels}\n"
        f"    b: *k{prefix_levels}\n"
    )
    return "".join(parts)


def test_memo_does_not_skip_under_stricter_remaining_depth(tmp_path, monkeypatch) -> None:
    config = _write(
        tmp_path, _shared_node_at_two_depths_yaml(20, 15), name="shared-two-depths.yaml"
    )
    monkeypatch.setenv("TANGLE_ENV", "a")

    with pytest.raises(ConfigFileError, match="exceeded the maximum depth of 32"):
        ArgsContainer.load(config, name=(None, None))


def test_shared_node_within_depth_budget_is_accepted(tmp_path, monkeypatch) -> None:
    """The same shape stays valid while both paths fit the budget."""

    config = _write(tmp_path, _shared_node_at_two_depths_yaml(20, 5), name="shared-ok.yaml")
    monkeypatch.setenv("TANGLE_ENV", "a")

    [args] = ArgsContainer.load(config, name=(None, None))

    assert args.name == "leaf"


def test_no_module_level_validation_cache_accumulates(tmp_path, monkeypatch) -> None:
    """The memo is scoped to one document load; nothing accumulates globally."""

    module = importlib.import_module("tangle_cli.args_container")
    config = _write(tmp_path, _alias_dag_yaml(8), name="no-global-cache.yaml")
    monkeypatch.setenv("TANGLE_ENV", "a")

    ArgsContainer.load(config, name=(None, None))
    before = {
        name: len(value)
        for name, value in vars(module).items()
        if isinstance(value, (dict, list, set))
    }
    for _ in range(3):
        ArgsContainer.load(config, name=(None, None))
    after = {
        name: len(value)
        for name, value in vars(module).items()
        if isinstance(value, (dict, list, set))
    }

    grew = {name: (before[name], size) for name, size in after.items() if size > before[name]}
    assert grew == {}


def test_memo_is_per_load_not_global(tmp_path, monkeypatch) -> None:
    """A second load re-validates: no cross-load cache can mask a bad file."""

    good = _write(tmp_path, _alias_dag_yaml(8), name="memo-good.yaml")
    bad = _write(
        tmp_path,
        _alias_dag_yaml(8).replace(
            "_n0: &n0\n  name: leaf\n", "_n0: &n0\n  _defaults: 5\n  configs: []\n"
        ),
        name="memo-bad.yaml",
    )
    monkeypatch.setenv("TANGLE_ENV", "a")

    [args] = ArgsContainer.load(good, name=(None, None))
    assert args.name == "leaf"

    with pytest.raises(ConfigFileError, match="_defaults must be an object"):
        ArgsContainer.load(bad, name=(None, None))

    # And the good file still loads afterwards.
    [again] = ArgsContainer.load(good, name=(None, None))
    assert again.name == "leaf"


def test_cyclic_alias_in_dormant_branch_hits_depth_guard(tmp_path, monkeypatch) -> None:
    config = _write(
        tmp_path,
        "_loop: &loop\n"
        "  _select:\n"
        "    env: TANGLE_REGION\n"
        "    cases:\n"
        "      us: *loop\n"
        "_select:\n"
        "  env: TANGLE_ENV\n"
        "  cases:\n"
        "    dev:\n"
        "      name: dev-name\n"
        "    prod: *loop\n",
        name="dormant-cycle.yaml",
    )
    monkeypatch.setenv("TANGLE_ENV", "dev")
    monkeypatch.delenv("TANGLE_REGION", raising=False)

    with pytest.raises(ConfigFileError, match="exceeded the maximum depth of 32"):
        ArgsContainer.load(config, name=(None, None))


def test_self_cyclic_alias_terminates_under_memoization(tmp_path, monkeypatch) -> None:
    config = _write(
        tmp_path,
        "&loop\n_select:\n  env: TANGLE_ENV\n  cases:\n    dev: *loop\n    other: *loop\n",
        name="self-cycle.yaml",
    )
    monkeypatch.setenv("TANGLE_ENV", "dev")

    start = time.perf_counter()
    with pytest.raises(ConfigFileError, match="exceeded the maximum depth of 32"):
        ArgsContainer.load(config, name=(None, None))
    assert time.perf_counter() - start < 5.0
