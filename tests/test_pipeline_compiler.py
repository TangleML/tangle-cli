"""Tests for ``tangle sdk pipelines compile`` (the ported PipelineCompiler).

Ported from tangle-deploy's ``test_pipeline_compiler.py``. The generic
compile behavior — free functions, dehydrated single-file output, runnable
argument emission, ``@task`` sidecars, and user-facing failure modes — is
exercised directly against ``tangle_cli.pipeline_compiler``. CLI coverage
targets the cyclopts ``compile`` command (``tangle sdk pipelines compile``)
and the ``compile_pipeline_file`` facade rather than tangle-deploy's typer
surface.
"""
import shutil
import sys
from pathlib import Path

import pytest
import yaml

from tangle_cli import cli
from tangle_cli.pipeline_compiler import (
    ZONE_ROOT_MARKERS,
    CompileResult,
    PipelineCompiler,
    _parse_overrides,
    compile_pipeline,
)
from tangle_cli.pipelines import PipelineValidationError, compile_pipeline_file
from tangle_cli.python_pipeline.errors import CompileError
from tangle_cli.schema_validation import validate_dehydrated_data

FIXTURES = Path(__file__).parent / "fixtures" / "python_pipeline"


def run_app(app, args: list[str]) -> None:
    try:
        app(args)
    except SystemExit as exc:
        if exc.code not in (0, None):
            raise


def _provide_noop(out: Path) -> None:
    """Colocate the referenced ``noop.yaml`` component next to the output.

    Fixtures like ``pipeline.py`` / ``multi_arg_pipeline.py`` reference
    ``file://./noop.yaml``; the compiler validates that relative local
    componentRef targets exist relative to the OUTPUT directory, so the
    referenced component must sit next to the compiled YAML.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURES / "noop.yaml", out.parent / "noop.yaml")


# ---------------------------------------------------------------------------
# Free-function compile: single dehydrated YAML, result contract, sys hygiene.


def test_compile_writes_single_dehydrated_yaml(tmp_path):
    out = tmp_path / "compiled.yaml"
    _provide_noop(out)
    compile_pipeline(FIXTURES / "pipeline.py", out)

    # No wrapper/template sidecars are written. The output dir may legitimately
    # also contain the referenced component (noop.yaml) we colocated above.
    assert out.exists()
    assert not out.with_name("compiled.yaml.j2").exists()
    assert not list(tmp_path.glob("*.yaml.j2"))
    # This (non-@task) pipeline emits NO components sidecar.
    assert not (tmp_path / "compiled.components.yaml").exists()

    data = yaml.safe_load(out.read_text())
    # No wrapper config: no ``template_file`` and no copied cfg keys.
    assert "template_file" not in data
    assert "foo" not in data
    # It is a pipeline body.
    assert data["name"] == "Noop Pipeline"
    assert "implementation" in data

    tasks = data["implementation"]["graph"]["tasks"]
    assert "Wait For Noop" in tasks
    task_body = tasks["Wait For Noop"]

    # componentRef is a PURE ref — no inline spec / text.
    cref = task_body["componentRef"]
    assert cref == {"url": "file://./noop.yaml"}
    assert "spec" not in cref
    assert "text" not in cref

    # wait_for graph input is emitted as a {graphInput: {inputName}} edge.
    assert task_body["arguments"]["wait_for"] == {
        "graphInput": {"inputName": "parent_wait_token"}
    }

    # The Out[str] return wires to outputValues via {taskOutput: {...}}.
    assert data["implementation"]["graph"]["outputValues"]["wait_for_output"] == {
        "taskOutput": {"taskId": "Wait For Noop", "outputName": "wait_for_output"}
    }


def test_compile_pipeline_returns_result(tmp_path):
    out = tmp_path / "compiled.yaml"
    _provide_noop(out)
    result = compile_pipeline(FIXTURES / "pipeline.py", out)
    assert isinstance(result, CompileResult)
    assert result.pipeline_path == out.resolve()
    assert result.components_path is None
    assert result.task_count == 1
    assert result.warnings == []


def test_compile_creates_missing_output_parent(tmp_path):
    """The normal ``--output`` path is created at write time after validation."""
    out = tmp_path / "nested" / "compiled.yaml"

    result = compile_pipeline(FIXTURES / "task_pipeline.py", out)

    assert result.pipeline_path == out.resolve()
    assert out.exists()
    assert out.with_name("compiled.components.yaml").exists()


def test_compile_pipeline_does_not_leak_sys_state(tmp_path):
    """compile_pipeline must not leave residue on sys.path / sys.modules."""
    path_before = list(sys.path)
    modules_before = set(sys.modules.keys())

    out = tmp_path / "compiled.yaml"
    _provide_noop(out)
    compile_pipeline(FIXTURES / "pipeline.py", out)

    # sys.path is byte-for-byte identical → no fixture dir residue.
    assert sys.path == path_before
    fixtures_dir = str(FIXTURES.resolve())
    if fixtures_dir not in path_before:
        assert fixtures_dir not in sys.path

    # No leftover user-module entry in sys.modules.
    new_modules = set(sys.modules.keys()) - modules_before
    leaked_user_modules = [
        m for m in new_modules if m.startswith("_tangle_user_pipeline_")
    ]
    assert leaked_user_modules == []


def test_parse_overrides_rejects_missing_equals():
    assert _parse_overrides(["a=1", "b=two"]) == {"a": "1", "b": "two"}
    with pytest.raises(CompileError):
        _parse_overrides(["no_equals"])


# ---------------------------------------------------------------------------
# Free-function compile: user-facing failure modes.


def test_compile_unresolvable_local_ref_fails(tmp_path):
    """A relative file:// componentRef whose target is NOT colocated with
    the output fails clearly with guidance, and writes no output."""
    out = tmp_path / "compiled.yaml"
    # Deliberately do NOT colocate noop.yaml next to the output.
    with pytest.raises(CompileError) as exc:
        compile_pipeline(FIXTURES / "pipeline.py", out)
    msg = str(exc.value)
    assert "file://./noop.yaml" in msg
    assert "output directory" in msg
    # Guidance for the user.
    assert "compile into the pipeline source directory" in msg.lower()
    # No output written on failure (neither pipeline nor sidecar).
    assert not out.exists()
    assert not (tmp_path / "compiled.components.yaml").exists()


def test_compile_empty_graph_fails(tmp_path):
    """A pipeline whose body calls no ref(...) -> CompileError."""
    out = tmp_path / "compiled.yaml"
    with pytest.raises(CompileError) as exc:
        compile_pipeline(FIXTURES / "empty_pipeline.py", out)
    assert "no tasks" in str(exc.value)
    assert not out.exists()


def test_compile_duplicate_task_id_fails(tmp_path):
    """Two tasks forced to the same id via .named('Dup') -> CompileError."""
    out = tmp_path / "compiled.yaml"
    with pytest.raises(CompileError) as exc:
        compile_pipeline(FIXTURES / "dup_task_pipeline.py", out)
    assert "duplicate task id" in str(exc.value)
    assert "Dup" in str(exc.value)
    assert not out.exists()


def test_compile_rejects_config_output_path_collision(tmp_path):
    """If ``@pipeline(config=...)`` points at ``--output``, fail clearly.

    ``config=`` is a compile-time input, not the output file to create. The
    compiler should not auto-create the output early and then accidentally
    read that empty file as config (or read a stale compiled YAML as config).
    """
    out = tmp_path / "compiled.yaml"
    src = tmp_path / "self_config_pipeline.py"
    src.write_text(
        "from tangle_cli.python_pipeline import pipeline\n"
        "\n"
        "@pipeline('Self Config Pipeline', config='compiled.yaml')\n"
        "def self_config_pipeline(cfg):\n"
        "    pass\n"
    )

    with pytest.raises(CompileError) as exc:
        compile_pipeline(src, out)

    msg = str(exc.value)
    assert "same path as the --output" in msg
    assert "compile-time input config file" in msg
    assert "creates the output file automatically" in msg
    assert "*.compile_config.yaml" in msg
    assert not out.exists()


def test_compile_without_cfg_param_ignores_missing_explicit_config(tmp_path):
    """A stale ``config=`` decorator does not block compile when unused.

    If the function has no ``cfg`` parameter, the config file is not observable
    by the pipeline body. Compile succeeds with a warning instead of requiring
    a pointless local config file.
    """
    out = tmp_path / "compiled.yaml"
    _provide_noop(out)
    src = tmp_path / "no_cfg_pipeline.py"
    src.write_text(
        "from tangle_cli.python_pipeline import In, Out, pipeline, ref\n"
        "\n"
        "@pipeline('No Cfg Pipeline', config='missing_compile_config.yaml')\n"
        "def no_cfg_pipeline(parent_wait_token: In[str]) -> Out[str]:\n"
        "    wait_for_noop = ref(url='file://./noop.yaml')(wait_for=parent_wait_token)\n"
        "    return wait_for_noop\n"
    )

    result = compile_pipeline(src, out)

    assert out.exists()
    assert result.warnings == [
        "pipeline 'No Cfg Pipeline' declares config='missing_compile_config.yaml' "
        "but its function has no `cfg` parameter, so the config file was not "
        "loaded. Remove `config=` when no compile-time config is needed, or add "
        "a `cfg` parameter to use it."
    ]


def test_compile_missing_config_with_cfg_param_explains_contract(tmp_path):
    """A missing config still fails when the pipeline body accepts ``cfg``."""
    out = tmp_path / "compiled.yaml"
    src = tmp_path / "needs_cfg_pipeline.py"
    src.write_text(
        "from tangle_cli.python_pipeline import In, Out, pipeline, ref\n"
        "\n"
        "@pipeline('Needs Cfg Pipeline', config='missing_compile_config.yaml')\n"
        "def needs_cfg_pipeline(parent_wait_token: In[str], cfg) -> Out[str]:\n"
        "    wait_for_noop = ref(url='file://./noop.yaml')(wait_for=parent_wait_token)\n"
        "    return wait_for_noop\n"
    )

    with pytest.raises(CompileError) as exc:
        compile_pipeline(src, out)

    msg = str(exc.value)
    assert "config file not found" in msg
    assert "has a `cfg` parameter" in msg
    assert "@pipeline(config='missing_compile_config.yaml')" in msg
    assert "compile-time input" in msg
    assert "remove the `cfg` parameter" in msg
    assert not out.exists()


def test_compile_rejects_overrides_without_cfg_param(tmp_path):
    out = tmp_path / "compiled.yaml"
    _provide_noop(out)
    src = tmp_path / "no_cfg_pipeline.py"
    src.write_text(
        "from tangle_cli.python_pipeline import In, Out, pipeline, ref\n"
        "\n"
        "@pipeline('No Cfg Pipeline')\n"
        "def no_cfg_pipeline(parent_wait_token: In[str]) -> Out[str]:\n"
        "    wait_for_noop = ref(url='file://./noop.yaml')(wait_for=parent_wait_token)\n"
        "    return wait_for_noop\n"
    )

    with pytest.raises(CompileError) as exc:
        compile_pipeline(src, out, overrides={"mode": "dry_run"})

    msg = str(exc.value)
    assert "received --override values ['mode']" in msg
    assert "has no `cfg` parameter" in msg
    assert not out.exists()


@pytest.mark.parametrize(
    "literal, type_name",
    [
        ("42", "int"),
        ("3.14", "float"),
        ("True", "bool"),
        ("None", "NoneType"),
        ("[1, 2, 'three']", "list"),
        ("{'mode': 'dry_run'}", "dict"),
    ],
)
def test_compile_non_string_constant_rejected(literal, type_name, tmp_path):
    """A non-string constant (int/float/bool/None/list/dict) is rejected at
    compile with a generic stringify-guidance error, and NO output is
    written. The runnable Tangle argument contract is string-only."""
    out = tmp_path / "compiled.yaml"
    _provide_noop(out)
    # The pipeline is compiled from its own source dir, where the compiler
    # loads <dir>/config.yaml by default; provide an empty one.
    (tmp_path / "config.yaml").write_text("{}\n")
    src = tmp_path / "bad_const_pipeline.py"
    src.write_text(
        "from tangle_cli.python_pipeline import In, Out, pipeline, ref\n"
        "\n"
        "@pipeline('Bad Const Pipeline')\n"
        "def bad_const_pipeline(parent_wait_token: In[str]) -> Out[str]:\n"
        f"    run_bad = ref(url='file://./noop.yaml')(bad={literal}, "
        "wait_for=parent_wait_token)\n"
        "    return run_bad\n"
    )
    with pytest.raises(CompileError) as exc:
        compile_pipeline(src, out)

    msg = str(exc.value)
    assert "unsupported constant type" in msg
    assert type_name in msg
    assert "bad" in msg  # names the offending argument
    # Carries the stringify guidance and the runnable value contract.
    assert "json.dumps" in msg
    assert "string constants, graphInput, or taskOutput" in msg
    # No partial output is written on failure.
    assert not out.exists()


# ---------------------------------------------------------------------------
# @task sidecar emission.


def test_compile_task_decorator_emits_sidecar(tmp_path):
    """@task pipelines compile: the main YAML is written and a
    ``<stem>.components.yaml`` resolver sidecar is emitted alongside it."""
    out = tmp_path / "compiled.yaml"
    result = compile_pipeline(FIXTURES / "task_pipeline.py", out)
    assert out.exists()
    assert result.components_path == out.with_name("compiled.components.yaml")
    assert result.components_path.exists()
    # Main pipeline validates as dehydrated (resolve:// URL is valid).
    validate_dehydrated_data(yaml.safe_load(out.read_text()))


# ---------------------------------------------------------------------------
# Runnable argument-value emission (raw string constant / graphInput /
# taskOutput). Dispatch is on the VALUE's type, never the argument KEY.


@pytest.fixture(scope="module")
def multi_arg_args(tmp_path_factory):
    """Compiled ``arguments`` block of the multi-arg fixture's two tasks."""
    tmp_path = tmp_path_factory.mktemp("multi_arg")
    out = tmp_path / "compiled.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURES / "noop.yaml", out.parent / "noop.yaml")
    compile_pipeline(FIXTURES / "multi_arg_pipeline.py", out)
    data = yaml.safe_load(out.read_text())
    tasks = data["implementation"]["graph"]["tasks"]
    return data, tasks["Produce Data"]["arguments"], tasks["Consume Data"]["arguments"]


def test_compile_string_constants_emit_raw(multi_arg_args):
    """Each string constant emits as a RAW string (the runnable Tangle
    argument shape) — no ``constantValue`` wrapper. A ``json.dumps``
    payload is just another string constant (the author stringifies
    structured data, never tangle-cli)."""
    _data, produce, _consume = multi_arg_args

    assert produce["a_string"] == "hello world"
    assert produce["a_multiline"] == "line one\nline two\n"
    assert produce["a_json_payload"] == '{"mode": "dry_run", "batch_size": 100}'

    # Every constant is a plain string — no wrapper object anywhere.
    for value in (
        produce["a_string"],
        produce["a_multiline"],
        produce["a_json_payload"],
    ):
        assert isinstance(value, str)


def test_compile_graph_input_is_key_independent(multi_arg_args):
    """An ``In`` param emits ``graphInput`` regardless of the argument
    key — both the edge key ``wait_for`` and the plain key ``run_mode``."""
    _data, produce, _consume = multi_arg_args

    # Edge key.
    assert produce["wait_for"] == {"graphInput": {"inputName": "parent_wait_token"}}

    # NON-edge key bound to an In param ALSO emits graphInput (proves the
    # emitter dispatches on the value type, not the argument name).
    assert produce["run_mode"] == {"graphInput": {"inputName": "run_mode"}}


def test_compile_task_outputs_bare_and_named(multi_arg_args):
    """Bare task output -> ``outputName: wait_for_output``; attribute
    access -> that named output."""
    _data, _produce, consume = multi_arg_args

    # Bare proxy via ``depends_on`` -> canonical done sentinel.
    assert consume["depends_on"] == {
        "taskOutput": {"taskId": "Produce Data", "outputName": "wait_for_output"}
    }

    # ``produce_data.rows_written`` -> named output.
    assert consume["rows"] == {
        "taskOutput": {"taskId": "Produce Data", "outputName": "rows_written"}
    }


def test_compile_depends_on_key_preserved_verbatim(multi_arg_args):
    """The literal ``depends_on`` key is preserved as the argument key —
    only the value is wrapped."""
    _data, _produce, consume = multi_arg_args
    assert "depends_on" in consume
    # And it was not rewritten to ``wait_for`` or anything else.
    assert "wait_for" not in consume


def test_compile_validates_schema(multi_arg_args):
    """The compiled output passes packaged-schema validation. (compile
    already validates internally; this asserts it explicitly too.)"""
    data, _produce, _consume = multi_arg_args
    # Should not raise.
    validate_dehydrated_data(data)


# ---------------------------------------------------------------------------
# PipelineCompiler handler + ZONE_ROOT_MARKERS seam.


def test_pipeline_compiler_compile_file_returns_result(tmp_path):
    """The object-oriented handler drives the same compile and returns the
    module-level CompileResult, mirroring PipelineHydrator."""
    out = tmp_path / "compiled.yaml"
    _provide_noop(out)
    result = PipelineCompiler().compile_file(FIXTURES / "pipeline.py", out)
    assert isinstance(result, CompileResult)
    assert result.pipeline_path == out.resolve()
    assert result.task_count == 1


def test_zone_root_markers_empty_by_default():
    """OSS ships no zone-root markers; downstream distributions extend the
    seam. An empty seam means _find_zone_root always returns None."""
    assert ZONE_ROOT_MARKERS == []


# ---------------------------------------------------------------------------
# compile_pipeline_file facade (pipelines.py).


def test_compile_pipeline_file_returns_result(tmp_path):
    out = tmp_path / "compiled.yaml"
    _provide_noop(out)
    result = compile_pipeline_file(FIXTURES / "pipeline.py", out)
    assert isinstance(result, CompileResult)
    assert result.pipeline_path == out.resolve()
    assert result.task_count == 1


def test_compile_pipeline_file_wraps_compile_error(tmp_path):
    """The facade translates the compiler's CompileError into the CLI's
    uniform PipelineValidationError (mirrors hydrate_pipeline_file)."""
    out = tmp_path / "compiled.yaml"
    # noop.yaml deliberately not colocated -> unresolvable local ref.
    with pytest.raises(PipelineValidationError) as exc:
        compile_pipeline_file(FIXTURES / "pipeline.py", out)
    assert "file://./noop.yaml" in str(exc.value)
    assert not out.exists()


# ---------------------------------------------------------------------------
# cyclopts `compile` command (tangle sdk pipelines compile).


def test_compile_cli_writes_output(tmp_path, capsys):
    out = tmp_path / "compiled.yaml"
    _provide_noop(out)
    app = cli.build_app()

    run_app(
        app,
        ["sdk", "pipelines", "compile", str(FIXTURES / "pipeline.py"), "-o", str(out)],
    )

    assert out.exists()
    assert yaml.safe_load(out.read_text())["name"] == "Noop Pipeline"
    assert "Compiled" in capsys.readouterr().out


def test_compile_cli_help_exits_zero(capsys):
    app = cli.build_app()
    run_app(app, ["sdk", "pipelines", "compile", "--help"])
    assert "compile" in capsys.readouterr().out.lower()


def test_compile_cli_missing_script_fails(tmp_path):
    out = tmp_path / "compiled.yaml"
    app = cli.build_app()
    with pytest.raises(SystemExit) as exc_info:
        app(
            [
                "sdk",
                "pipelines",
                "compile",
                str(tmp_path / "does_not_exist.py"),
                "-o",
                str(out),
            ]
        )
    assert exc_info.value.code != 0
    assert "not found" in str(exc_info.value)
    assert not out.exists()


def test_compile_cli_no_pipeline_fails(tmp_path):
    out = tmp_path / "compiled.yaml"
    app = cli.build_app()
    with pytest.raises(SystemExit) as exc_info:
        app(
            [
                "sdk",
                "pipelines",
                "compile",
                str(FIXTURES / "no_pipeline.py"),
                "-o",
                str(out),
            ]
        )
    assert exc_info.value.code != 0
    assert "no @pipeline" in str(exc_info.value).lower()
    assert not out.exists()


def test_compile_cli_multiple_pipelines_fails(tmp_path):
    """Without --pipeline, a multi-pipeline file errors with a message that
    mentions --pipeline and lists each candidate by function and display name."""
    out = tmp_path / "compiled.yaml"
    app = cli.build_app()
    with pytest.raises(SystemExit) as exc_info:
        app(
            [
                "sdk",
                "pipelines",
                "compile",
                str(FIXTURES / "multi_pipeline.py"),
                "-o",
                str(out),
            ]
        )
    assert exc_info.value.code != 0
    msg = str(exc_info.value)
    assert "multiple" in msg.lower()
    assert "--pipeline" in msg
    assert "first_pipeline" in msg
    assert "second_pipeline" in msg
    assert "First Pipeline" in msg
    assert "Second Pipeline" in msg
    assert not out.exists()


def test_compile_cli_select_pipeline_by_function_name(tmp_path):
    out = tmp_path / "first.yaml"
    _provide_noop(out)
    app = cli.build_app()
    run_app(
        app,
        [
            "sdk",
            "pipelines",
            "compile",
            str(FIXTURES / "multi_pipeline.py"),
            "--pipeline",
            "first_pipeline",
            "-o",
            str(out),
        ],
    )
    assert yaml.safe_load(out.read_text())["name"] == "First Pipeline"


def test_compile_cli_select_pipeline_by_display_name(tmp_path):
    out = tmp_path / "second.yaml"
    _provide_noop(out)
    app = cli.build_app()
    run_app(
        app,
        [
            "sdk",
            "pipelines",
            "compile",
            str(FIXTURES / "multi_pipeline.py"),
            "--pipeline",
            "Second Pipeline",
            "-o",
            str(out),
        ],
    )
    assert yaml.safe_load(out.read_text())["name"] == "Second Pipeline"


def test_compile_cli_unknown_pipeline_name_fails(tmp_path):
    out = tmp_path / "compiled.yaml"
    _provide_noop(out)
    app = cli.build_app()
    with pytest.raises(SystemExit) as exc_info:
        app(
            [
                "sdk",
                "pipelines",
                "compile",
                str(FIXTURES / "multi_pipeline.py"),
                "--pipeline",
                "nope",
                "-o",
                str(out),
            ]
        )
    assert exc_info.value.code != 0
    assert "not found" in str(exc_info.value).lower()
    assert "first_pipeline" in str(exc_info.value)
    assert not out.exists()


def test_compile_cli_bad_override_format_fails(tmp_path):
    out = tmp_path / "compiled.yaml"
    app = cli.build_app()
    with pytest.raises(SystemExit) as exc_info:
        app(
            [
                "sdk",
                "pipelines",
                "compile",
                str(FIXTURES / "pipeline.py"),
                "-o",
                str(out),
                "--override",
                "no_equals",
            ]
        )
    assert exc_info.value.code != 0
    assert "override" in str(exc_info.value).lower()
    assert not out.exists()


def test_compile_cli_unresolvable_local_ref_exit_nonzero(tmp_path):
    out = tmp_path / "compiled.yaml"
    app = cli.build_app()
    with pytest.raises(SystemExit) as exc_info:
        app(
            [
                "sdk",
                "pipelines",
                "compile",
                str(FIXTURES / "pipeline.py"),
                "-o",
                str(out),
            ]
        )
    assert exc_info.value.code != 0
    assert "noop.yaml" in str(exc_info.value)
    assert not out.exists()


def test_compile_cli_task_decorator_exit_zero(tmp_path):
    out = tmp_path / "compiled.yaml"
    app = cli.build_app()
    run_app(
        app,
        [
            "sdk",
            "pipelines",
            "compile",
            str(FIXTURES / "task_pipeline.py"),
            "-o",
            str(out),
        ],
    )
    assert out.exists()
    assert out.with_name("compiled.components.yaml").exists()
