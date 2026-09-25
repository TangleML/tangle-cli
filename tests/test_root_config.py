"""``TANGLE_ROOT_CONFIG``: per-command root config layered beneath ``--config``."""

from __future__ import annotations

import json
import traceback
from pathlib import Path

import pytest
import yaml

from tangle_cli import api_cli, cli, secrets_cli
from tangle_cli.args_container import (
    ArgsContainer,
    ConfigFileError,
    EnvField,
    load_config,
    normalize_command,
    strict_bool,
)
from tangle_cli.cli_helpers import (
    dispatched_command,
    dispatching,
    include_env_credentials_for_args,
    load_args_or_exit,
    load_config_or_exit,
)
from tangle_cli.python_pipeline.cfg import load_cfg

SECRET = "FAKE-SECRET-do-not-echo-7f3a9c2e1b"
ENV_VARS = ("RC_ENV", "RC_TOKEN", "RC_FLAG", "RC_TIER")
CMD = "tangle sdk pipeline-runs submit"
OTHER = "tangle-deploy pipeline-run submit"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def _write(directory: Path, name: str, text: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


def _commands(entries: dict) -> str:
    return yaml.safe_dump({"commands": entries}, sort_keys=False)


@pytest.fixture
def root(tmp_path, monkeypatch):
    def set_root(text: str, name: str = "root.yaml") -> Path:
        path = _write(tmp_path / "root", name, text)
        monkeypatch.setenv("TANGLE_ROOT_CONFIG", str(path))
        return path

    return set_root


def _cmd(tmp_path: Path, text: str, name: str = "cmd.yaml") -> Path:
    return _write(tmp_path / "cmd", name, text)


def _load(config, command=CMD, **specs):
    return ArgsContainer.load(config, command=command, **specs)


# --- unset / empty / no identity -----------------------------------------------------------


def test_unset_root_config_leaves_loading_unchanged(tmp_path) -> None:
    config = _cmd(tmp_path, "a: 1\nannotations: {team: x}\n")

    [entry] = load_config(config, command=CMD)
    [args] = _load(config, a=(None, None), annotations=(None, None))

    assert entry.values == ArgsContainer._load_config_file(config)[0]
    assert (args.a, args.annotations) == (1, {"team": "x"})
    assert load_config(None, command=CMD)[0].values == {}


def test_empty_root_config_is_a_no_op(monkeypatch) -> None:
    monkeypatch.setenv("TANGLE_ROOT_CONFIG", "")

    [args] = _load(None, a=("dflt", "dflt"))

    assert args.a == "dflt"


def test_library_call_without_command_identity_ignores_root(tmp_path, root) -> None:
    root(_commands({CMD: {"a": "from-root"}}))
    config = _cmd(tmp_path, "b: 2\n")

    [args] = ArgsContainer.load(config, a=("dflt", "dflt"), b=(None, None))
    assert (args.a, args.b) == ("dflt", 2)
    assert load_config(None)[0].values == {}
    assert ArgsContainer._load_config_file(config) == [{"b": 2}]


def test_no_identity_does_not_even_read_a_broken_root(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TANGLE_ROOT_CONFIG", str(tmp_path / "missing.yaml"))

    [args] = ArgsContainer.load(None, a=("dflt", "dflt"))

    assert args.a == "dflt"


# --- command selection -----------------------------------------------------------------------


def test_matching_command_entry_is_the_config(root) -> None:
    path = root(_commands({CMD: {"base_url": "https://api.root", "annotations": {"team": "search"}}}))

    [args] = _load(None, base_url=(None, None), annotations=(None, None))

    assert args.base_url == "https://api.root"
    assert args.annotations == {"team": "search"}
    assert args.config_source("base_url") == path.resolve()


def test_non_matching_command_gets_no_root_config(root, tmp_path) -> None:
    root(_commands({OTHER: {"annotations": {"team": "search"}}}))
    config = _cmd(tmp_path, "limit: 3\n")

    [args] = _load(config, annotations=(None, None), limit=(None, None))

    assert (args.annotations, args.limit) == (None, 3)


def test_both_clis_have_distinct_entries(root) -> None:
    root(_commands({CMD: {"who": "tangle"}, OTHER: {"who": "tangle-deploy"}}))

    [upstream] = _load(None, who=(None, None))
    [downstream] = _load(None, command=OTHER, who=(None, None))

    assert (upstream.who, downstream.who) == ("tangle", "tangle-deploy")


@pytest.mark.parametrize(
    ("key", "identity"),
    [
        ("tangle  sdk\tpipeline-runs   submit", CMD),
        ("tangle-cli sdk pipeline-runs submit", CMD),
        (CMD, "tangle-cli sdk pipeline-runs submit"),
        (CMD, " tangle sdk pipeline-runs submit "),
    ],
)
def test_command_names_are_normalized(root, key, identity) -> None:
    root(_commands({key: {"a": 1}}))

    [args] = _load(None, command=identity, a=(None, None))

    assert args.a == 1


def test_normalize_command_maps_alias_and_deprecated_prefixes() -> None:
    aliases = {"td": "tangle-deploy", "tangle-deploy pipeline-runs": "tangle-deploy pipeline-run"}

    assert normalize_command("td  pipeline-runs submit", aliases) == "tangle-deploy pipeline-run submit"
    assert normalize_command("tangle-deploy pipeline-runs", aliases) == "tangle-deploy pipeline-run"
    assert normalize_command("tangle-deploy pipeline-runsx submit", aliases) == (
        "tangle-deploy pipeline-runsx submit"
    )
    assert normalize_command("tangle-cli sdk secrets list") == "tangle sdk secrets list"
    # A cyclic alias table terminates instead of looping.
    assert normalize_command("a x", {"a": "b", "b": "a"}) in ("a x", "b x")


def test_command_match_is_case_sensitive_and_exact(root) -> None:
    root(_commands({"Tangle sdk pipeline-runs submit": {"a": 1}, "tangle sdk pipeline-runs": {"a": 2}}))

    [args] = _load(None, a=(None, None))

    assert args.a is None


def test_near_miss_command_key_warns_without_values(root, capsys) -> None:
    root(_commands({"tangle sdk pipeline-run submit": {"token": SECRET}}))

    [args] = _load(None, token=(None, None))

    assert args.token is None
    err = capsys.readouterr().err
    assert "has no entry for 'tangle sdk pipeline-runs submit'" in err
    assert "did you mean 'tangle sdk pipeline-run submit'" in err
    assert SECRET not in err


def test_normalize_command_rejects_empty() -> None:
    with pytest.raises(ConfigFileError):
        normalize_command("   ")


# --- layering --------------------------------------------------------------------------------


def test_command_config_deep_merges_over_root_entry(tmp_path, root) -> None:
    root(
        _commands(
            {
                CMD: {
                    "base_url": "https://api.root",
                    "annotations": {"team": "search", "tier": "gold"},
                    "header": ["X-Root: 1"],
                    "nested": {"a": {"x": 1, "y": 2}, "keep": True},
                }
            }
        )
    )
    config = _cmd(
        tmp_path,
        "annotations: {owner: me, tier: silver}\nheader: ['X-Cmd: 2']\nnested: {a: {y: 20, z: 30}}\n",
    )

    [args] = _load(
        config,
        base_url=(None, None),
        annotations=(None, None),
        header=(None, None),
        nested=(None, None),
    )

    assert args.base_url == "https://api.root"
    assert args.annotations == {"team": "search", "tier": "silver", "owner": "me"}
    assert args.header == ["X-Cmd: 2"]  # lists replace wholesale
    assert args.nested == {"a": {"x": 1, "y": 20, "z": 30}, "keep": True}


def test_scalar_and_type_changes_replace(tmp_path, root) -> None:
    root(_commands({CMD: {"limit": 5, "annotations": {"team": "x"}, "name": "root"}}))
    config = _cmd(tmp_path, "limit: 7\nannotations: [not-a-map]\n")

    [entry] = load_config(config, command=CMD)

    assert entry.values == {"limit": 7, "annotations": ["not-a-map"], "name": "root"}


def test_cli_beats_both_and_replaces_whole_field(tmp_path, root) -> None:
    root(_commands({CMD: {"annotations": {"team": "x"}, "limit": 5}}))
    config = _cmd(tmp_path, "annotations: {owner: y}\n")

    [args] = _load(config, annotations=({"cli": "z"}, None), limit=(9, None))

    assert (args.annotations, args.limit) == ({"cli": "z"}, 9)
    assert args.config_source("annotations") is None


def test_precedence_cli_config_root_env_default(tmp_path, root, monkeypatch) -> None:
    root(_commands({CMD: {"a": "root", "b": "root", "c": "root"}}))
    config = _cmd(tmp_path, "a: config\nb: config\n")
    monkeypatch.setenv("RC_TIER", "env")

    [args] = _load(
        config,
        a=EnvField("RC_TIER", ("cli", "dflt")),
        b=EnvField("RC_TIER", ("dflt", "dflt")),
        c=EnvField("RC_TIER", ("dflt", "dflt")),
        d=EnvField("RC_TIER", ("dflt", "dflt")),
        e=("dflt", "dflt"),
    )

    assert (args.a, args.b, args.c, args.d, args.e) == ("cli", "config", "root", "env", "dflt")


def test_typed_coercion_applies_after_merge(tmp_path, root, monkeypatch) -> None:
    root(_commands({CMD: {"force": {"_env": "RC_FLAG", "default": False}, "retries": "3", "dry_run": "yes"}}))
    monkeypatch.setenv("RC_FLAG", "false")

    [args] = _load(None, force=(False, False), retries=(0, 0), dry_run=(None, None, strict_bool))
    assert (args.force, args.retries, args.dry_run) == (False, 3, True)

    monkeypatch.setenv("RC_FLAG", "maybe")
    unrelated = _cmd(tmp_path, "other: 1\n", name="unrelated.yaml")
    with pytest.raises(
        ConfigFileError, match=r"environment variable RC_FLAG \(_env at config key 'force'\)"
    ):
        _load(unrelated, force=(False, False))

    [args] = _load(_cmd(tmp_path, "force: 'true'\n"), force=(False, False))
    assert args.force is True


# --- null ------------------------------------------------------------------------------------


def test_null_in_command_config_unsets_an_inherited_key(tmp_path, root, monkeypatch) -> None:
    root(_commands({CMD: {"token": "root-token", "annotations": {"team": "x", "owner": "y"}}}))
    config = _cmd(tmp_path, "token: null\nannotations: {team: null}\n")
    monkeypatch.setenv("RC_TIER", "from-env")

    [entry] = load_config(config, command=CMD)
    [args] = _load(
        config,
        token=EnvField("RC_TIER", ("dflt", "dflt")),
        annotations=(None, None),
    )

    assert entry.values == {"annotations": {"owner": "y"}}
    assert "token" not in entry.sources
    assert args.token == "from-env"  # falls through to the env tier, then the default
    assert args.annotations == {"owner": "y"}
    [no_env] = _load(config, token=("dflt", "dflt"))
    assert no_env.token == "dflt"


def test_null_where_root_sets_nothing_is_ignored_while_layering(tmp_path, root, monkeypatch) -> None:
    root(_commands({CMD: {"annotations": {"team": "x"}}}))
    config = _cmd(
        tmp_path,
        "token: null\nhydrate: null\nannotations: {owner: null}\nextra: {a: null, b: 1}\nlist: [1, null]\n",
    )
    monkeypatch.setenv("RC_TIER", "from-env")

    [entry] = load_config(config, command=CMD)
    [args] = _load(
        config,
        token=EnvField("RC_TIER", (None, None)),
        hydrate=(True, True),
        annotations=(None, None),
    )

    # Absent, not None: mapping leaves drop; list items are values and stay.
    assert entry.values == {"annotations": {"team": "x"}, "extra": {"b": 1}, "list": [1, None]}
    assert (args.token, args.hydrate) == ("from-env", True)


def test_null_without_an_active_root_entry_keeps_todays_meaning(tmp_path, root) -> None:
    config = _cmd(tmp_path, "token: null\nhydrate: null\nannotations: {owner: null}\n")
    expected = {"token": None, "hydrate": None, "annotations": {"owner": None}}

    [unset] = ArgsContainer.load(config, command=CMD, token=("dflt", "dflt"), hydrate=(True, True))
    assert load_config(config, command=CMD)[0].values == expected
    assert (unset.token, unset.hydrate) == (None, None)

    root(_commands({OTHER: {"token": "other"}}))  # set, but no entry for this command
    assert load_config(config, command=CMD)[0].values == expected
    assert load_config(config)[0].values == expected  # no command identity


@pytest.mark.parametrize(
    ("document", "location"),
    [
        ({"commands": {CMD: {"token": None}}}, r"commands\.tangle sdk pipeline-runs submit\.token"),
        ({"commands": {OTHER: {"a": {"b": [1, None]}}}}, r"commands\.tangle-deploy pipeline-run submit\.a\.b\[1\]"),
        ({"_shared": None, "commands": {}}, r"_shared"),
    ],
)
def test_null_anywhere_in_root_is_rejected(root, document, location) -> None:
    path = root(yaml.safe_dump(document))

    with pytest.raises(ConfigFileError, match=rf"^TANGLE_ROOT_CONFIG \({path}\): null is not allowed"):
        _load(None, token=(None, None))
    with pytest.raises(ConfigFileError, match=rf"\(at {location}\)$"):
        _load(None, token=(None, None))


# --- _select / _env --------------------------------------------------------------------------


def test_document_select_picks_a_command_map(root, monkeypatch) -> None:
    root(
        "_select:\n"
        "  env: RC_ENV\n"
        "  cases:\n"
        f"    prod: {{commands: {{'{CMD}': {{base_url: https://api.prod, token: {{_env: RC_TOKEN}}}}}}}}\n"
        f"    dev: {{commands: {{'{CMD}': {{base_url: https://api.dev, token: {{_env: RC_DORMANT}}}}}}}}\n"
    )
    monkeypatch.setenv("RC_ENV", "prod")
    monkeypatch.setenv("RC_TOKEN", "tok")

    [args] = _load(None, base_url=(None, None), token=(None, None))

    assert (args.base_url, args.token) == ("https://api.prod", "tok")


def test_entry_select_and_env_resolve_inside_the_entry(tmp_path, root, monkeypatch) -> None:
    root(
        "commands:\n"
        f"  '{CMD}':\n"
        "    _select:\n"
        "      env: RC_ENV\n"
        "      cases:\n"
        "        prod: {base_url: https://api.prod, annotations: {env: prod}, token: {_env: RC_TOKEN}}\n"
        "      default: {base_url: https://api.dev}\n"
        f"  '{OTHER}': {{token: {{_env: RC_UNSET_FOR_OTHER_COMMAND}}}}\n"
    )
    monkeypatch.setenv("RC_ENV", "prod")
    monkeypatch.setenv("RC_TOKEN", "tok")
    config = _cmd(tmp_path, "annotations: {owner: me}\n")

    [args] = _load(config, base_url=(None, None), token=(None, None), annotations=(None, None))
    assert (args.base_url, args.token) == ("https://api.prod", "tok")
    assert args.annotations == {"env": "prod", "owner": "me"}

    monkeypatch.delenv("RC_ENV")
    [dev] = _load(None, base_url=(None, None))
    assert dev.base_url == "https://api.dev"


@pytest.mark.parametrize("selection", [None, "prod"])
def test_malformed_directive_in_any_command_fails_in_every_env(root, monkeypatch, selection) -> None:
    if selection:
        monkeypatch.setenv("RC_ENV", selection)
    path = root(_commands({CMD: {"a": 1}, OTHER: {"token": {"_env": "RC_TOKEN", "fallback": "x"}}}))

    with pytest.raises(ConfigFileError, match=rf"^TANGLE_ROOT_CONFIG \({path}\): _env at commands\.tangle-deploy"):
        _load(None, a=(None, None))


def test_root_select_unmatched_fails_closed_without_echo(root, monkeypatch) -> None:
    path = root(f"commands:\n  '{CMD}':\n    _select: {{env: RC_ENV, cases: {{prod: {{a: 1}}}}}}\n")
    monkeypatch.setenv("RC_ENV", SECRET)

    with pytest.raises(ConfigFileError) as excinfo:
        _load(None, a=(None, None))

    assert str(excinfo.value).startswith(f"TANGLE_ROOT_CONFIG ({path}): Environment variable RC_ENV")
    assert SECRET not in "".join(traceback.format_exception(excinfo.value))


# --- multi-config documents ------------------------------------------------------------------


def test_list_and_defaults_documents_merge_per_entry(tmp_path, root) -> None:
    root(_commands({CMD: {"base_url": "https://api.root", "limit": 1, "annotations": {"team": "x"}}}))
    listed = _cmd(tmp_path, "- {name: a}\n- {name: b, annotations: {owner: y}}\n", name="list.yaml")
    defaults = _cmd(
        tmp_path,
        "_defaults:\n  limit: 5\n  annotations: {owner: d}\n"
        "configs:\n  - {name: a}\n  - {name: b, limit: 9, annotations: {env: z}}\n",
        name="defaults.yaml",
    )

    base = {"base_url": "https://api.root"}
    assert [e.values for e in load_config(listed, command=CMD)] == [
        {**base, "limit": 1, "annotations": {"team": "x"}, "name": "a"},
        {**base, "limit": 1, "annotations": {"team": "x", "owner": "y"}, "name": "b"},
    ]
    # root < _defaults (still a shallow merge in its own file) < entry.
    assert [e.values for e in load_config(defaults, command=CMD)] == [
        {**base, "limit": 5, "annotations": {"team": "x", "owner": "d"}, "name": "a"},
        {**base, "limit": 9, "annotations": {"team": "x", "env": "z"}, "name": "b"},
    ]


# --- relative paths --------------------------------------------------------------------------


def test_config_source_names_the_file_each_value_came_from(tmp_path, root) -> None:
    root_path = root(_commands({CMD: {"module": "pipelines/root.py", "output": "out/root.yaml"}}))
    config = _cmd(tmp_path, "output: out/cmd.yaml\n")

    [args] = _load(config, module=(None, None), output=(None, None), other=(None, None))

    assert args.config_source("module") == root_path.resolve()
    assert args.config_source("output") == config.resolve()
    assert args.config_source("other") is None
    source = args.config_source("module")
    assert source is not None and source.parent != config.resolve().parent


def test_config_source_without_root_config(tmp_path) -> None:
    config = _cmd(tmp_path, "output: out.yaml\n")

    [args] = ArgsContainer.load(config, output=(None, None))

    assert args.config_source("output") == config.resolve()


# --- shape errors ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("base_url: https://x\n", "must be a mapping with a top-level 'commands' selector"),
        ("", "must be a mapping with a top-level 'commands' selector"),
        ("- {a: 1}\n", "must be a mapping with a top-level 'commands' selector"),
        (_commands({CMD: {}}) + "base_url: x\n", "only 'commands' and underscore-prefixed helper keys"),
        ("commands: [a]\n", "'commands' must map command names to config objects"),
        ("commands: {_env: RC_TOKEN}\n", "'commands' must map command names to config objects"),
        (_commands({CMD: [1]}), f"'commands' entry '{CMD}' must be a config object"),
        (_commands({CMD: {"_env": "RC_TOKEN"}}), f"'commands' entry '{CMD}' must be a config object"),
        ("commands: {'  ': {}}\n", "'commands' keys must be non-empty command names"),
        (
            _commands({CMD: {}, "tangle-cli sdk pipeline-runs submit": {}}),
            f"'commands' names command '{CMD}' more than once",
        ),
    ],
)
def test_malformed_root_shapes_fail_closed(root, text, message) -> None:
    path = root(text)

    with pytest.raises(ConfigFileError) as excinfo:
        _load(None, a=(None, None))

    assert str(excinfo.value).startswith(f"TANGLE_ROOT_CONFIG ({path}): ")
    assert message in str(excinfo.value)


def test_underscore_helper_keys_allow_yaml_anchors(root) -> None:
    root(
        "_shared: &shared {annotations: {team: search}}\n"
        "commands:\n"
        f"  '{CMD}': *shared\n"
        f"  '{OTHER}': {{<<: *shared, extra: 1}}\n"
    )

    [upstream] = _load(None, annotations=(None, None))
    [downstream] = _load(None, command=OTHER, annotations=(None, None), extra=(None, None))

    assert upstream.annotations == downstream.annotations == {"team": "search"}
    assert downstream.extra == 1


def test_missing_and_directory_root_fail_closed(tmp_path, monkeypatch) -> None:
    missing = tmp_path / "nope.yaml"
    monkeypatch.setenv("TANGLE_ROOT_CONFIG", str(missing))
    with pytest.raises(ConfigFileError) as excinfo:
        _load(None, a=(None, None))
    assert str(excinfo.value) == f"TANGLE_ROOT_CONFIG names a config file that does not exist: {missing}"

    monkeypatch.setenv("TANGLE_ROOT_CONFIG", str(tmp_path))
    with pytest.raises(ConfigFileError, match=r"^TANGLE_ROOT_CONFIG must name a config file: "):
        _load(None, a=(None, None))


@pytest.mark.parametrize(
    ("name", "text"),
    [("root.yaml", f"commands: {{x: '{SECRET}\n"), ("root.json", f'{{"commands": "{SECRET}",}}')],
)
def test_unparsable_root_names_var_and_path_without_echo(root, name, text) -> None:
    path = root(text, name=name)

    with pytest.raises(ConfigFileError) as excinfo:
        _load(None, a=(None, None))

    assert str(excinfo.value).startswith(f"TANGLE_ROOT_CONFIG ({path}): ")
    assert SECRET not in "".join(traceback.format_exception(excinfo.value))


# --- CLI integration and other consumers -------------------------------------------------------


def test_tangle_launcher_sets_the_dispatched_command_identity(root, monkeypatch, capsys) -> None:
    root(
        _commands(
            {
                "tangle sdk secrets delete": {"secret_name": "FROM_ROOT", "force": True, "log_type": "none"},
                "tangle sdk secrets list": {"force": False},
            }
        )
    )

    class Client:
        deleted: list[str] = []

        def __init__(self, **kwargs) -> None:
            pass

        def secrets_delete(self, secret_name: str):
            Client.deleted.append(secret_name)

    monkeypatch.setattr(secrets_cli, "LazyTangleApiClient", Client)

    try:
        cli.build_app().meta(["sdk", "secrets", "delete"])
    except SystemExit as exc:
        assert exc.code in (0, None)

    assert Client.deleted == ["FROM_ROOT"]
    assert json.loads(capsys.readouterr().out)["secret_name"] == "FROM_ROOT"
    assert dispatched_command() is None  # the identity does not leak past dispatch


def test_direct_app_call_without_dispatcher_gets_no_root(root, monkeypatch) -> None:
    root(_commands({"tangle sdk secrets delete": {"secret_name": "FROM_ROOT"}}))

    with pytest.raises(SystemExit, match="secret_name is required"):
        cli.build_app()(["sdk", "secrets", "delete"])


def test_launcher_identity_ignores_options_and_arguments() -> None:
    app = cli.build_app()

    assert cli._command_identity(app, ("sdk", "secrets", "delete", "--force", "NAME")) == (
        "tangle sdk secrets delete"
    )
    assert cli._command_identity(app, ()) is None


def test_launcher_identity_canonicalizes_cyclopts_aliases() -> None:
    from cyclopts import App

    app = App(name="t")
    group = App(name=["group", "group-old"])
    app.command(group)

    @group.command(name=["run", "run-old"])
    def run() -> None:  # pragma: no cover - never invoked
        return None

    assert cli._command_identity(app, ("group-old", "run-old", "--x", "1")) == "tangle group run"


def test_load_args_or_exit_passes_the_dispatched_command(root) -> None:
    root(_commands({CMD: {"a": "from-root"}}))

    [outside] = load_args_or_exit(None, a=(None, None))
    with dispatching(CMD):
        [inside] = load_args_or_exit(None, a=(None, None))

    assert (outside.a, inside.a) == (None, "from-root")


def test_load_config_or_exit_and_api_preparse_use_the_command_entry(tmp_path, root) -> None:
    root(
        _commands(
            {
                CMD: {"base_url": "https://api.submit", "token": "root-token"},
                "tangle api pipeline-runs list": {"base_url": "https://api.list"},
            }
        )
    )
    config = _cmd(tmp_path, "token: cmd-token\n")

    with dispatching(CMD):
        assert load_config_or_exit(None) == {"base_url": "https://api.submit", "token": "root-token"}
        assert load_config_or_exit(str(config))["token"] == "cmd-token"
    assert load_config_or_exit(None) == {}
    assert api_cli._config_value_from_argv(["pipeline-runs", "list", "--limit", "1"], "base_url") == (
        "https://api.list"
    )
    assert api_cli._config_value_from_argv(["pipeline-runs", "get", "ID"], "base_url") is None


def test_root_base_url_counts_as_config_for_credential_isolation(root) -> None:
    root(_commands({CMD: {"base_url": "https://api.root"}}))

    [args] = _load(None, base_url=(None, None))

    assert include_env_credentials_for_args(args, cli_base_url=None) is False


def test_pipeline_cfg_is_not_layered(tmp_path, root) -> None:
    root(_commands({CMD: {"value": "from-root"}}))
    cfg_path = _write(tmp_path / "pipe", "config.yaml", "own: 1\n")

    with dispatching(CMD):
        cfg = load_cfg(cfg_path)

    assert cfg.own == 1
    with pytest.raises(Exception, match="unknown config key"):
        _ = cfg.value
