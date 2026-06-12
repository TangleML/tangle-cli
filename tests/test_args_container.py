from __future__ import annotations

import json

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
