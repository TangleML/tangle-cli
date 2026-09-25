"""Pipeline ``cfg`` files resolve ``_select`` and ``_env`` like ``--config`` files."""

from __future__ import annotations

import traceback
from pathlib import Path

import pytest
import yaml

from tangle_cli.pipeline_compiler import compile_pipeline
from tangle_cli.python_pipeline.cfg import load_cfg
from tangle_cli.python_pipeline.compiler_context import (
    PipelineCompileKey,
    canonical_repo_path,
    overrides_fingerprint,
)
from tangle_cli.python_pipeline.errors import CompileError

SECRET = "FAKE-SECRET-do-not-echo-7f3a9c2e1b"
ENV_VARS = ("CFG_ENV", "CFG_TOKEN", "CFG_LIMIT", "CFG_CHILD_ENV")

_TASK = (
    "from tangle_cli.python_pipeline import In, Out, pipeline, subpipeline, task\n"
    "\n"
    "@task(image='python:3.12')\n"
    "def echo(value: str = 'task-default', kind: str = 'task-default'):\n"
    "    '''Echo.\n"
    "\n"
    "    Metadata:\n"
    "        Name: Echo\n"
    "    '''\n"
    "    print(value, kind)\n"
    "\n"
)

ROOT_ONLY = _TASK + (
    "@pipeline('Root', config='root_config.yaml')\n"
    "def root(seed: In[str], cfg) -> Out[str]:\n"
    "    run = echo(value=cfg.value, kind=type(cfg.limit).__name__, wait_for=seed)\n"
    "    return run\n"
)

WITH_CHILD = _TASK + (
    "@pipeline('Child', config='child_config.yaml')\n"
    "def child(seed: In[str], cfg) -> Out[str]:\n"
    "    run = echo(value=cfg.value, kind=type(cfg.limit).__name__, wait_for=seed)\n"
    "    return run\n"
    "\n"
    "@pipeline('Root', config='root_config.yaml', propagate_config={propagate})\n"
    "def root(seed: In[str], cfg) -> Out[str]:\n"
    "    return subpipeline(child){edge}.named('Run Child')(seed=seed)\n"
)

SELECT_ROOT = (
    "_shared: &shared\n"
    "  limit: 5\n"
    "_select:\n"
    "  env: CFG_ENV\n"
    "  cases:\n"
    "    prod:\n"
    "      <<: *shared\n"
    "      value: prod-value\n"
    "    dev:\n"
    "      value: dev-value\n"
    "      limit: 1.5\n"
    "  default:\n"
    "    value: default-value\n"
    "    limit: 0\n"
)

SELECT_CHILD = (
    "_select:\n"
    "  env: CFG_ENV\n"
    "  cases:\n"
    "    prod: {value: child-prod, limit: 1, extra: prod-only}\n"
    "    dev: {value: child-dev, limit: 2}\n"
    "    also-prod: {value: child-prod, limit: 1, extra: prod-only}\n"
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def _project(tmp_path: Path, source: str, root_config: str, child_config: str | None = None):
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    (project / "pipeline.py").write_text(source)
    (project / "root_config.yaml").write_text(root_config)
    if child_config is not None:
        (project / "child_config.yaml").write_text(child_config)
    return project / "pipeline.py"


def _args(path: Path) -> dict:
    data = yaml.safe_load(path.read_text())
    (task,) = data["implementation"]["graph"]["tasks"].values()
    return {key: task["arguments"][key] for key in ("value", "kind")}


def _compile(src: Path, tmp_path: Path, name: str = "compiled", **kwargs):
    out = tmp_path / "out" / f"{name}.yaml"
    result = compile_pipeline(src, out, pipeline_name="Root", **kwargs)
    return out, result


def _child_sidecar(result) -> Path:
    (path,) = [p for p in result.subgraph_paths if p.name.startswith("child-")]
    return path


# --- root config.yaml ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("selection", "value", "kind"),
    [("prod", "prod-value", "int"), ("dev", "dev-value", "float")],
)
def test_root_select_picks_branch_with_native_types(
    tmp_path, monkeypatch, selection, value, kind
) -> None:
    monkeypatch.setenv("CFG_ENV", selection)
    src = _project(tmp_path, ROOT_ONLY, SELECT_ROOT)

    out, _ = _compile(src, tmp_path)

    assert _args(out) == {"value": value, "kind": kind}


@pytest.mark.parametrize("selection", [None, "staging"])
def test_root_select_default_fallback(tmp_path, monkeypatch, selection) -> None:
    if selection is not None:
        monkeypatch.setenv("CFG_ENV", selection)
    src = _project(tmp_path, ROOT_ONLY, SELECT_ROOT)

    out, _ = _compile(src, tmp_path)

    assert _args(out) == {"value": "default-value", "kind": "int"}


def test_root_select_unmatched_without_default_fails_closed_without_echo(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("CFG_ENV", SECRET)
    config = SELECT_ROOT.split("  default:")[0]
    src = _project(tmp_path, ROOT_ONLY, config)

    with pytest.raises(CompileError) as excinfo:
        _compile(src, tmp_path)

    message = str(excinfo.value)
    assert "Environment variable CFG_ENV does not match any configured _select case" in message
    assert "'dev', 'prod'" in message
    assert "root_config.yaml" in message
    assert SECRET not in "".join(traceback.format_exception(excinfo.value))
    assert not (tmp_path / "out").exists()


def test_root_select_unset_without_default_fails_closed(tmp_path) -> None:
    src = _project(tmp_path, ROOT_ONLY, SELECT_ROOT.split("  default:")[0])

    with pytest.raises(CompileError, match="CFG_ENV is required by _select but is not set"):
        _compile(src, tmp_path)


def test_select_root_with_regular_sibling_rejected(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CFG_ENV", "prod")
    src = _project(tmp_path, ROOT_ONLY, "value: stray\n" + SELECT_ROOT)

    with pytest.raises(CompileError, match="_select must be the only regular key"):
        _compile(src, tmp_path)


# --- _env in cfg ------------------------------------------------------------------------


def test_env_directive_in_cfg_stays_string(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CFG_TOKEN", "from-env")
    monkeypatch.setenv("CFG_LIMIT", "7")
    src = _project(
        tmp_path, ROOT_ONLY, "value: {_env: CFG_TOKEN}\nlimit: {_env: CFG_LIMIT, default: 3}\n"
    )

    out, _ = _compile(src, tmp_path)

    # `7` is NOT YAML-coerced to int, unlike a raw CLI --override string.
    assert _args(out) == {"value": "from-env", "kind": "str"}


def test_env_directive_default_in_cfg_is_stringified(tmp_path) -> None:
    src = _project(
        tmp_path, ROOT_ONLY, "value: {_env: CFG_TOKEN, default: d}\nlimit: {_env: CFG_LIMIT, default: 3}\n"
    )

    out, _ = _compile(src, tmp_path)

    assert _args(out) == {"value": "d", "kind": "str"}


def test_env_directive_missing_in_cfg_names_var_and_path(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CFG_ENV", "prod")
    config = SELECT_ROOT.replace("value: prod-value", "value: {_env: CFG_TOKEN}")
    src = _project(tmp_path, ROOT_ONLY, config)

    with pytest.raises(CompileError) as excinfo:
        _compile(src, tmp_path)

    message = str(excinfo.value)
    assert "Environment variable CFG_TOKEN is required by _env at value" in message
    assert "within the selected _select branch" in message


def test_env_directive_inside_selected_branch(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CFG_ENV", "prod")
    monkeypatch.setenv("CFG_TOKEN", "tok")
    config = SELECT_ROOT.replace("value: prod-value", "value: {_env: CFG_TOKEN}").replace(
        "value: dev-value", "value: {_env: CFG_UNSET_IN_DORMANT}"
    )
    src = _project(tmp_path, ROOT_ONLY, config)

    out, _ = _compile(src, tmp_path)

    assert _args(out) == {"value": "tok", "kind": "int"}


@pytest.mark.parametrize("selection", ["prod", "dev", None])
def test_dormant_branch_structure_is_checked_in_every_env(
    tmp_path, monkeypatch, selection
) -> None:
    if selection is not None:
        monkeypatch.setenv("CFG_ENV", selection)
    config = SELECT_ROOT.replace("value: dev-value", "value: {_env: CFG_TOKEN, fallback: x}")
    src = _project(tmp_path, ROOT_ONLY, config)

    with pytest.raises(CompileError, match=r"_env at _select\.cases\.dev\.value allows only"):
        _compile(src, tmp_path)


@pytest.mark.parametrize("selection", ["prod", "dev", None])
def test_template_file_rejected_in_any_branch(tmp_path, monkeypatch, selection) -> None:
    if selection is not None:
        monkeypatch.setenv("CFG_ENV", selection)
    config = SELECT_ROOT.replace("value: dev-value", "value: dev-value\n      template_file: x.j2")
    src = _project(tmp_path, ROOT_ONLY, config)

    with pytest.raises(CompileError, match="top-level `template_file:` key"):
        _compile(src, tmp_path)


@pytest.mark.parametrize("branch", ["[a, b]", "just-a-string"])
def test_non_mapping_branch_rejected_in_every_env(tmp_path, monkeypatch, branch) -> None:
    monkeypatch.setenv("CFG_ENV", "prod")
    config = SELECT_ROOT.replace("  default:\n    value: default-value\n    limit: 0\n", "")
    config += f"    broken: {branch}\n"
    src = _project(tmp_path, ROOT_ONLY, config)

    with pytest.raises(CompileError, match=r"_select case 'broken' must be a mapping"):
        _compile(src, tmp_path)


def test_configs_key_is_ordinary_cfg_data(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CFG_ENV", "prod")
    monkeypatch.setenv("CFG_TOKEN", "t")
    config = (
        "_select:\n"
        "  env: CFG_ENV\n"
        "  cases:\n"
        "    prod:\n"
        "      configs: [1, {_env: CFG_TOKEN}]\n"
        "      _defaults: plain\n"
    )
    path = tmp_path / "config.yaml"
    path.write_text(config)

    cfg = load_cfg(path)

    assert cfg.configs == [1, "t"]
    assert cfg._defaults == "plain"


# --- overrides --------------------------------------------------------------------------


def test_cli_override_wins_over_selected_branch(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CFG_ENV", "prod")
    src = _project(tmp_path, ROOT_ONLY, SELECT_ROOT)

    out, _ = _compile(src, tmp_path, overrides={"value": "from-cli", "limit": "true"})

    assert _args(out) == {"value": "from-cli", "kind": "bool"}


def test_cli_override_wins_over_env_directive(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CFG_TOKEN", "from-env")
    src = _project(tmp_path, ROOT_ONLY, "value: {_env: CFG_TOKEN}\nlimit: 1\n")

    out, _ = _compile(src, tmp_path, overrides={"value": "from-cli"})

    assert _args(out)["value"] == "from-cli"


def _child_project(tmp_path, *, propagate=False, edge="", root_config=None, child=SELECT_CHILD):
    source = WITH_CHILD.replace("{propagate}", str(propagate)).replace("{edge}", edge)
    return _project(tmp_path, source, root_config or "unused: 1\n", child)


@pytest.mark.parametrize(
    ("selection", "value", "kind"), [("prod", "child-prod", "int"), ("dev", "child-dev", "int")]
)
def test_child_config_select(tmp_path, monkeypatch, selection, value, kind) -> None:
    monkeypatch.setenv("CFG_ENV", selection)
    src = _child_project(tmp_path)

    _, result = _compile(src, tmp_path)

    assert _args(_child_sidecar(result)) == {"value": value, "kind": kind}


def test_override_config_key_checked_against_selected_branch(tmp_path, monkeypatch) -> None:
    src = _child_project(tmp_path, edge=".override_config(extra='edge')")

    monkeypatch.setenv("CFG_ENV", "prod")
    _compile(src, tmp_path, name="prod")

    monkeypatch.setenv("CFG_ENV", "dev")
    with pytest.raises(CompileError, match=r"'extra' is not a key in that child's config.yaml"):
        _compile(src, tmp_path, name="dev")


def test_override_config_wins_over_selected_child_branch(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CFG_ENV", "dev")
    src = _child_project(tmp_path, edge=".override_config(value='edge')")

    _, result = _compile(src, tmp_path)

    assert _args(_child_sidecar(result))["value"] == "edge"


# --- propagate_config broadcast ---------------------------------------------------------


@pytest.mark.parametrize("selection", ["prod", "dev"])
def test_broadcast_carries_resolved_root_values(tmp_path, monkeypatch, selection) -> None:
    monkeypatch.setenv("CFG_ENV", selection)
    monkeypatch.setenv("CFG_TOKEN", f"{selection}-token")
    root_config = (
        "_select:\n"
        "  env: CFG_ENV\n"
        "  cases:\n"
        "    prod: {value: {_env: CFG_TOKEN}, limit: 9}\n"
        "    dev: {value: {_env: CFG_TOKEN}, limit: 8}\n"
    )
    src = _child_project(tmp_path, propagate=True, root_config=root_config)

    _, result = _compile(src, tmp_path)

    # The child declares `value`/`limit`, so the root's RESOLVED values win;
    # a broadcast still holding `_select`/`_env` nodes would not match.
    assert _args(_child_sidecar(result)) == {"value": f"{selection}-token", "kind": "int"}


# --- compile identity ---------------------------------------------------------------------


def _legacy_child_sidecar_name(src: Path) -> str:
    key = PipelineCompileKey(
        source_path=canonical_repo_path(src.resolve()),
        function_qualname="child",
        pipeline_name="Child",
        config_path=canonical_repo_path((src.parent / "child_config.yaml").resolve()),
        overrides_fingerprint=overrides_fingerprint({}),
    )
    return f"child-{key.hash8()}.yaml"


def test_compile_identity_differs_across_selections(tmp_path, monkeypatch) -> None:
    src = _child_project(tmp_path)
    names = {}
    for selection in ("prod", "dev", "also-prod"):
        monkeypatch.setenv("CFG_ENV", selection)
        _, result = _compile(src, tmp_path, name=selection)
        names[selection] = _child_sidecar(result).name

    assert names["prod"] != names["dev"]
    # Identical resolved values keep an identical identity.
    assert names["prod"] == names["also-prod"]
    assert _legacy_child_sidecar_name(src) not in names.values()


def test_compile_identity_differs_across_env_values(tmp_path, monkeypatch) -> None:
    src = _child_project(tmp_path, child="value: {_env: CFG_TOKEN}\nlimit: 1\n")
    names = []
    for token in ("a", "b", "a"):
        monkeypatch.setenv("CFG_TOKEN", token)
        _, result = _compile(src, tmp_path, name=f"run-{len(names)}")
        names.append(_child_sidecar(result).name)

    assert names[0] != names[1]
    assert names[0] == names[2]


def test_compile_identity_unchanged_without_directives(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CFG_ENV", "prod")
    src = _child_project(tmp_path, child="value: plain\nlimit: 4\n")

    out, result = _compile(src, tmp_path)

    assert _child_sidecar(result).name == _legacy_child_sidecar_name(src)
    assert _args(_child_sidecar(result)) == {"value": "plain", "kind": "int"}
    assert "_select" not in out.read_text()


def test_directive_free_cfg_is_unchanged(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CFG_ENV", "prod")
    path = tmp_path / "config.yaml"
    # Only a ROOT `_select` selects; a nested one stays ordinary data.
    path.write_text(
        "a: 1\nnested: {_select: {env: CFG_ENV, cases: {prod: 1}}, x: [1, 2]}\n_helper: true\n"
    )

    cfg = load_cfg(path, {"a": "2"})

    assert cfg.a == 2
    assert cfg.nested.x == [1, 2]
    assert cfg.nested._select.env == "CFG_ENV"
    assert cfg.nested._select.cases.prod == 1
    assert cfg._helper is True


def test_resolved_cfg_document_holds_no_directive_nodes(tmp_path, monkeypatch) -> None:
    from tangle_cli.python_pipeline.cfg import read_cfg_document

    monkeypatch.setenv("CFG_ENV", "prod")
    monkeypatch.setenv("CFG_TOKEN", "t")
    path = tmp_path / "config.yaml"
    path.write_text(SELECT_ROOT.replace("value: prod-value", "value: {_env: CFG_TOKEN}"))

    document = read_cfg_document(path)

    assert document.data == {"limit": 5, "value": "t"}
    assert document.env_dependent
    assert len(document.branches) == 3  # prod, dev, default -- dormant included


def test_compile_key_config_identity_envelope() -> None:
    from tangle_cli.pipeline_compiler import _compile_key_for
    from tangle_cli.python_pipeline import pipeline

    @pipeline("Keyed")
    def keyed() -> None:  # pragma: no cover - never traced
        return None

    cfg_path = Path("/nonexistent/config.yaml")
    legacy = _compile_key_for(keyed, cfg_path, {"a": 1})
    with_a = _compile_key_for(keyed, cfg_path, {"a": 1}, config_identity="a" * 64)
    with_b = _compile_key_for(keyed, cfg_path, {"a": 1}, config_identity="b" * 64)

    assert legacy.overrides_fingerprint == overrides_fingerprint({"a": 1})
    assert len({legacy, with_a, with_b}) == 3
    assert with_a == _compile_key_for(keyed, cfg_path, {"a": 1}, config_identity="a" * 64)
