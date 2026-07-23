"""Tests for ``tangle sdk pipelines compile`` (the PipelineCompiler).

The generic compile behavior — free functions, dehydrated single-file output,
runnable argument emission, ``@task`` sidecars, subpipeline child sidecars,
``@registered`` resolution, and user-facing failure modes — is exercised
directly against ``tangle_cli.pipeline_compiler``. CLI coverage targets the
cyclopts ``compile`` command (``tangle sdk pipelines compile``) and the
``compile_pipeline_file`` facade.
"""
import shutil
import sys
from pathlib import Path

import pytest
import yaml

from tangle_cli import cli
from tangle_cli.pipeline_compiler import (
    IMAGE_IDS,
    ZONE_ROOT_MARKERS,
    CompileResult,
    PipelineCompiler,
    compile_pipeline,
    register_image_id,
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


def test_compile_task_decorator_emits_unwrapped_input_schema_and_flat_edges(tmp_path):
    project = tmp_path / "project"
    src = project / "src"
    pipeline_path = src / "pipeline.py"
    src.mkdir(parents=True)
    pipeline_path.write_text(
        "from tangle_cli.python_pipeline import Out, pipeline, task\n\n"
        "@task(image='python:3.12')\n"
        "def produce() -> str:\n"
        "    return 'ok'\n\n"
        "@task(image='python:3.12', unwrap='run_data')\n"
        "def combine(run_data: dict[str, str]) -> str:\n"
        "    return run_data['shop'] + run_data['catalog']\n\n"
        "@pipeline('Unwrap Pipeline')\n"
        "def unwrap_pipeline() -> Out[str]:\n"
        "    shop = produce.named('shop')()\n"
        "    catalog = produce.named('catalog')()\n"
        "    combined = combine.named('combine')(run_data={'shop': shop, 'catalog': catalog})\n"
        "    return combined\n",
        encoding="utf-8",
    )

    out = project / "compiled.yaml"
    result = compile_pipeline(pipeline_path, out)

    compiled = yaml.safe_load(out.read_text())
    combine_task = compiled["implementation"]["graph"]["tasks"]["combine"]
    assert combine_task["componentRef"]["url"].startswith(
        "resolve://./compiled.components.yaml#combine--"
    )
    assert set(combine_task["arguments"]) == {"run_data__shop", "run_data__catalog"}
    assert combine_task["arguments"]["run_data__shop"]["taskOutput"]["taskId"] == "shop"
    assert combine_task["arguments"]["run_data__catalog"]["taskOutput"]["taskId"] == "catalog"

    sidecar = yaml.safe_load(result.components_path.read_text())
    fragments = [name for name in sidecar if name.startswith("combine--")]
    assert len(fragments) == 1
    local_from_python = sidecar[fragments[0]]["local_from_python"]
    schema = local_from_python["unwrapped_inputs"]["run_data"]
    assert schema["value_type"] == "String"
    assert schema["keys"] == [
        {"key": "shop", "input_name": "run_data__shop", "type": "String", "optional": False},
        {
            "key": "catalog",
            "input_name": "run_data__catalog",
            "type": "String",
            "optional": False,
        },
    ]


def test_compile_task_decorator_uses_schema_hash_for_unwrapped_fragment_collisions(tmp_path):
    project = tmp_path / "project"
    src = project / "src"
    pipeline_path = src / "pipeline.py"
    src.mkdir(parents=True)
    pipeline_path.write_text(
        "from tangle_cli.python_pipeline import Out, pipeline, task\n\n"
        "@task(image='python:3.12')\n"
        "def produce() -> str:\n"
        "    return 'ok'\n\n"
        "@task(image='python:3.12', unwrap='run_data')\n"
        "def combine(run_data: dict[str, str]) -> str:\n"
        "    return ','.join(sorted(run_data))\n\n"
        "@pipeline('Unwrap Hash Pipeline')\n"
        "def unwrap_hash_pipeline() -> Out[str]:\n"
        "    first = produce.named('first')()\n"
        "    second = produce.named('second')()\n"
        "    a = combine.named('combine_a')(run_data={'first': first})\n"
        "    b = combine.named('combine_b')(run_data={'second': second})\n"
        "    return combine.named('combine_c')(run_data={'a': a, 'b': b})\n",
        encoding="utf-8",
    )

    result = compile_pipeline(pipeline_path, project / "compiled.yaml")
    sidecar = yaml.safe_load(result.components_path.read_text())

    fragments = sorted(name for name in sidecar if name.startswith("combine--"))
    assert len(fragments) == 3
    key_sets = {
        tuple(key["key"] for key in sidecar[name]["local_from_python"]["unwrapped_inputs"]["run_data"]["keys"])
        for name in fragments
    }
    assert key_sets == {("first",), ("second",), ("a", "b")}


def test_compile_task_decorator_rejects_empty_unwrapped_dict(tmp_path):
    project = tmp_path / "project"
    src = project / "src"
    pipeline_path = src / "pipeline.py"
    src.mkdir(parents=True)
    pipeline_path.write_text(
        "from tangle_cli.python_pipeline import Out, pipeline, task\n\n"
        "@task(image='python:3.12', unwrap='run_data')\n"
        "def combine(run_data: dict[str, str]) -> str:\n"
        "    return 'ok'\n\n"
        "@pipeline('Bad Unwrap Pipeline')\n"
        "def bad_unwrap_pipeline() -> Out[str]:\n"
        "    return combine.named('combine')(run_data={})\n",
        encoding="utf-8",
    )

    with pytest.raises(CompileError, match="cannot be an empty dict"):
        compile_pipeline(pipeline_path, project / "compiled.yaml")


def test_compile_task_decorator_rejects_unwrap_on_non_dict_annotation(tmp_path):
    project = tmp_path / "project"
    src = project / "src"
    pipeline_path = src / "pipeline.py"
    src.mkdir(parents=True)
    pipeline_path.write_text(
        "from tangle_cli.python_pipeline import Out, pipeline, task\n\n"
        "@task(image='python:3.12', unwrap='run_data')\n"
        "def combine(run_data: str) -> str:\n"
        "    return 'ok'\n\n"
        "@pipeline('Bad Annotation Pipeline')\n"
        "def bad_annotation_pipeline() -> Out[str]:\n"
        "    return combine.named('combine')(run_data={'x': 'y'})\n",
        encoding="utf-8",
    )

    with pytest.raises(CompileError, match="must be annotated as dict"):
        compile_pipeline(pipeline_path, project / "compiled.yaml")


def test_compile_task_decorator_emits_bundle_mode_and_resolve_root(tmp_path):
    """@task(mode="bundle") is carried into the auto-emitted sidecar."""
    project = tmp_path / "project"
    src = project / "src"
    pipeline_path = src / "pipeline.py"
    src.mkdir(parents=True)
    (src / "helpers.py").write_text("MESSAGE = 'hello'\n", encoding="utf-8")
    pipeline_path.write_text(
        "from tangle_cli.python_pipeline import Out, pipeline, task\n"
        "from helpers import MESSAGE\n\n"
        "@task(image='python:3.12', mode='bundle', resolve_root='.')\n"
        "def bundled_task() -> str:\n"
        "    return MESSAGE\n\n"
        "@pipeline('Bundle Pipeline')\n"
        "def bundle_pipeline() -> Out[str]:\n"
        "    bundled = bundled_task()\n"
        "    return bundled\n",
        encoding="utf-8",
    )

    out = project / "compiled.yaml"
    result = compile_pipeline(pipeline_path, out)

    sidecar = yaml.safe_load(result.components_path.read_text())
    local_from_python = sidecar["bundled-task"]["local_from_python"]
    assert local_from_python["mode"] == "bundle"
    assert local_from_python["resolve_root"] == "./src"
    assert local_from_python["file"] == "./src/pipeline.py"
    assert local_from_python["function"] == "bundled_task"


def _write_image_id_pipeline(project: Path, decorator: str) -> Path:
    src = project / "src"
    src.mkdir(parents=True)
    pipeline_path = src / "pipeline.py"
    pipeline_path.write_text(
        "from tangle_cli.python_pipeline import Out, pipeline, task\n\n"
        f"@task({decorator})\n"
        "def image_task() -> str:\n"
        "    return 'ok'\n\n"
        "@pipeline('Image Pipeline')\n"
        "def image_pipeline() -> Out[str]:\n"
        "    result = image_task()\n"
        "    return result\n",
        encoding="utf-8",
    )
    return pipeline_path


def test_compile_task_image_id_uses_registered_default(tmp_path):
    original = dict(IMAGE_IDS)
    try:
        IMAGE_IDS.clear()
        register_image_id("eval-slim", "registry.example/eval-slim:latest")
        pipeline_path = _write_image_id_pipeline(tmp_path / "project", "image_id='eval-slim'")

        result = compile_pipeline(pipeline_path, tmp_path / "project" / "compiled.yaml")

        sidecar = yaml.safe_load(result.components_path.read_text())
        local_from_python = sidecar["image-task"]["local_from_python"]
        assert local_from_python["image"] == "registry.example/eval-slim:latest"
    finally:
        IMAGE_IDS.clear()
        IMAGE_IDS.update(original)


def test_compile_task_image_id_uses_compile_time_override(tmp_path):
    pipeline_path = _write_image_id_pipeline(tmp_path / "project", "image_id='eval-slim'")

    result = compile_pipeline(
        pipeline_path,
        tmp_path / "project" / "compiled.yaml",
        image_overrides={"eval-slim": "registry.example/eval-slim@sha256:abc"},
    )

    sidecar = yaml.safe_load(result.components_path.read_text())
    local_from_python = sidecar["image-task"]["local_from_python"]
    assert local_from_python["image"] == "registry.example/eval-slim@sha256:abc"


def test_compile_task_explicit_image_precedes_image_id_override(tmp_path):
    pipeline_path = _write_image_id_pipeline(
        tmp_path / "project",
        "image='python:3.12', image_id='eval-slim'",
    )

    result = compile_pipeline(
        pipeline_path,
        tmp_path / "project" / "compiled.yaml",
        image_overrides={"eval-slim": "registry.example/eval-slim@sha256:abc"},
    )

    sidecar = yaml.safe_load(result.components_path.read_text())
    local_from_python = sidecar["image-task"]["local_from_python"]
    assert local_from_python["image"] == "python:3.12"


def test_compile_task_image_id_without_default_or_override_fails(tmp_path):
    original = dict(IMAGE_IDS)
    try:
        IMAGE_IDS.clear()
        pipeline_path = _write_image_id_pipeline(tmp_path / "project", "image_id='eval-slim'")

        with pytest.raises(CompileError, match="image_id='eval-slim'.*did not resolve"):
            compile_pipeline(pipeline_path, tmp_path / "project" / "compiled.yaml")
    finally:
        IMAGE_IDS.clear()
        IMAGE_IDS.update(original)


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


def test_compile_cli_image_override_writes_resolved_image(tmp_path):
    project = tmp_path / "project"
    out = project / "compiled.yaml"
    pipeline_path = _write_image_id_pipeline(project, "image_id='eval-slim'")
    app = cli.build_app()

    run_app(
        app,
        [
            "sdk",
            "pipelines",
            "compile",
            str(pipeline_path),
            "-o",
            str(out),
            "--image",
            "eval-slim=registry.example/eval-slim@sha256:abc",
        ],
    )

    sidecar = yaml.safe_load(out.with_name("compiled.components.yaml").read_text())
    assert sidecar["image-task"]["local_from_python"]["image"] == "registry.example/eval-slim@sha256:abc"


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


# ---------------------------------------------------------------------------
# Subpipeline composition: a child @pipeline embedded via subpipeline(...) is
# emitted as its own sidecar under <stem>.subgraphs/ and the parent task's
# componentRef is rewritten from the subpipeline://pending sentinel to a
# file:// reference at that child.


def test_compile_subpipeline_emits_child_sidecar(tmp_path):
    """A subpipeline child compiles to a sidecar under ``<stem>.subgraphs/``
    and the parent task's componentRef is rewritten to a ``file://`` URL at
    that child — the ``subpipeline://pending`` sentinel never survives."""
    out = tmp_path / "compiled.yaml"
    result = compile_pipeline(
        FIXTURES / "subpipeline_pipeline.py", out, pipeline_name="Parent Pipeline"
    )

    # Exactly one child sidecar, named after the child pipeline (compile-key
    # hash suffix), living under the parent's <stem>.subgraphs/ directory.
    assert len(result.subgraph_paths) == 1
    child = result.subgraph_paths[0]
    assert child.exists()
    assert child.parent.name == "compiled.subgraphs"
    assert child.name.startswith("child-pipeline-")
    assert child.name.endswith(".yaml")

    # Parent has a single task whose componentRef points at the child sidecar.
    parent = yaml.safe_load(out.read_text())
    tasks = parent["implementation"]["graph"]["tasks"]
    assert list(tasks) == ["Run Child"]
    assert (
        tasks["Run Child"]["componentRef"]["url"]
        == f"file://./compiled.subgraphs/{child.name}"
    )
    assert "subpipeline://pending" not in out.read_text()

    # Both parent and child are valid dehydrated pipelines.
    validate_dehydrated_data(parent)
    validate_dehydrated_data(yaml.safe_load(child.read_text()))


def test_compile_subpipeline_override_config_wins(tmp_path):
    """``.override_config`` on a subpipeline edge sets the child's COMPILE-TIME
    cfg, and the overridden value wins over the child's own config.yaml. The
    child emits ``cfg.message`` as a task-argument constant, so the override is
    observable in the child sidecar (the value can only come from the edge
    override: it is neither the ``@task`` default nor the config.yaml value)."""
    out = tmp_path / "compiled.yaml"
    result = compile_pipeline(
        FIXTURES / "subpipeline_config_pipeline.py", out, pipeline_name="Config Parent"
    )
    child = yaml.safe_load(result.subgraph_paths[0].read_text())
    tasks = child["implementation"]["graph"]["tasks"]
    (task,) = tasks.values()
    assert task["arguments"]["message"] == "from-override"


# ---------------------------------------------------------------------------
# propagate_config broadcast. A pipeline with propagate_config=True broadcasts
# its own config.yaml deep into the subtree by key name; the broadcast must
# flow PAST config-less intermediate subpipelines to reach descendants that
# declare the key.

_BROADCAST_ZONE = FIXTURES / "broadcast_zone"


def test_compile_propagate_config_broadcasts_through_configless_intermediate(tmp_path):
    """``propagate_config=True`` broadcasts a config key deep into the subtree,
    flowing PAST a config-less intermediate (no ``cfg``/``config=``, so no
    config.yaml on disk) to a config-declaring grandchild, where the root's
    broadcast value wins over the grandchild's own config.yaml (same key).

    Regression guard: before the broadcast path tolerated a missing
    intermediate config.yaml, this compile hard-failed with a spurious
    ``config file not found`` for the config-less ``Broadcast Middle``. The
    grandchild leaf emits ``cfg.shared_key`` as a task-argument constant, so
    the broadcast value is observable and can only be the root's — it is
    neither the ``@task`` default (``from-task-default``) nor the grandchild's
    own config value (``from-grandchild``)."""
    out = tmp_path / "compiled.yaml"
    result = compile_pipeline(
        _BROADCAST_ZONE / "broadcast_pipeline.py", out, pipeline_name="Broadcast Root"
    )
    # Two child sidecars: the config-less Middle and the Grandchild leaf.
    assert len(result.subgraph_paths) == 2
    slugs = sorted(p.name.rsplit("-", 1)[0] for p in result.subgraph_paths)
    assert slugs == ["broadcast-grandchild", "broadcast-middle"]
    # The grandchild leaf task argument carries the root's broadcast value,
    # proving the key flowed through the config-less intermediate and won over
    # the grandchild's own config.yaml.
    gc_path = next(
        p for p in result.subgraph_paths if p.name.startswith("broadcast-grandchild-")
    )
    gc = yaml.safe_load(gc_path.read_text())
    (leaf,) = gc["implementation"]["graph"]["tasks"].values()
    assert leaf["arguments"]["shared_key"] == "from-root-broadcast"
    # Parent + both child sidecars are structurally valid dehydrated pipelines.
    validate_dehydrated_data(yaml.safe_load(out.read_text()))
    for p in result.subgraph_paths:
        validate_dehydrated_data(yaml.safe_load(p.read_text()))


# ---------------------------------------------------------------------------
# Cycle detection. A subpipeline that (transitively) reaches back to itself is
# a recursive Python pipeline, which cannot be compiled to a finite graph. It
# must be rejected with a precise "cycle detected" diagnostic — NOT allowed to
# recurse until the max-depth guard trips, whose "reduce nesting depth" advice
# is misleading for what is actually a cycle.

_LOOP_SRC = (
    "from tangle_cli.python_pipeline import In, Out, pipeline, subpipeline\n"
    "\n"
    "@pipeline('Loop')\n"
    "def loop(seed: In[str]) -> Out[str]:\n"
    "    # Self-reference => a 1-node cycle.\n"
    "    return subpipeline(loop).named('Recurse')(seed=seed)\n"
)


def test_compile_detects_self_referencing_cycle(tmp_path):
    """A self-referencing subpipeline is reported as a cycle, precisely."""
    src = tmp_path / "loop_pipeline.py"
    src.write_text(_LOOP_SRC)

    with pytest.raises(CompileError) as exc_info:
        compile_pipeline(src, tmp_path / "compiled.yaml", pipeline_name="loop")

    msg = str(exc_info.value)
    assert "nested pipeline cycle detected" in msg
    assert "Loop" in msg
    assert "max depth" not in msg  # must not degrade to the depth guard


def test_compile_detects_cycle_under_propagate_config_passthrough(tmp_path):
    """A cycle must still be caught precisely when the cyclic pipeline is
    reached under a ``propagate_config`` ancestor that broadcasts a key the
    cyclic pipeline does not declare (an ambient PASS-THROUGH key).

    Regression guard: the cycle-check key is built by the parent WITH the
    ambient pass-through folded in, while a child used to push its OWN key
    (ambient-less) onto ``active_stack``. Under a pass-through broadcast the two
    keys diverged, so ``child_key in active_stack`` never matched and the SAME
    cycle that is caught in isolation instead degraded to the max-depth guard
    after 32 redundant levels. The fix threads the parent-computed key into the
    child compile so the stacked, registry, and cycle-check keys are identical.
    """
    src = tmp_path / "loop_pipeline.py"
    src.write_text(
        _LOOP_SRC
        + "\n"
        "@pipeline('Broadcast Root', config='root_config.yaml', propagate_config=True)\n"
        "def broadcast_root(parent_wait_token: In[str], cfg) -> Out[str]:\n"
        "    # Broadcasts shared_key; `loop` doesn't declare it, so it flows\n"
        "    # PAST loop as ambient context at every self-edge.\n"
        "    return subpipeline(loop).named('Run Loop')(seed=parent_wait_token)\n"
    )
    (tmp_path / "root_config.yaml").write_text("shared_key: broadcast-value\n")

    with pytest.raises(CompileError) as exc_info:
        compile_pipeline(
            src, tmp_path / "compiled.yaml", pipeline_name="broadcast_root"
        )

    msg = str(exc_info.value)
    assert "nested pipeline cycle detected" in msg
    assert "Broadcast Root" in msg and "Loop" in msg
    assert "max depth" not in msg  # the bug: cycle degraded to the depth guard


# ---------------------------------------------------------------------------
# @registered gen_config resolution. @registered references an EXISTING
# gen_config.yaml and generates no sidecar of its own; the task's componentRef
# is rewritten from the registered://pending sentinel to a resolve:// URL.

_REGISTERED_ZONE = FIXTURES / "registered_zone"


def test_compile_registered_omitted_gen_config_uses_nearest(tmp_path):
    """With ``gen_config`` omitted, resolution falls back to the nearest
    ancestor ``gen_config.yaml`` — the default OSS path, needing no zone-root
    marker. The emitted URL is a ``resolve://`` reference at that file with the
    verbatim ``#fragment``, and @registered generates no sidecar."""
    out = tmp_path / "compiled.yaml"
    result = compile_pipeline(
        _REGISTERED_ZONE / "registered_op_pipeline.py",
        out,
        pipeline_name="Registered Pipeline",
    )
    assert result.components_path is None
    assert result.subgraph_paths == []

    data = yaml.safe_load(out.read_text())
    (task,) = data["implementation"]["graph"]["tasks"].values()
    url = task["componentRef"]["url"]
    # The relpath from the tmp output dir to the fixture is environment
    # dependent; assert the scheme and the target/fragment tail only.
    assert url.startswith("resolve://")
    assert url.endswith("registered_zone/gen_config.yaml#run-query")
    assert "registered://pending" not in out.read_text()


def test_compile_registered_relative_gen_config_rejected_without_marker(tmp_path):
    """A relative ``gen_config`` is rejected in the default OSS build because
    ``ZONE_ROOT_MARKERS`` is empty, so no zone root can be located."""
    assert ZONE_ROOT_MARKERS == []  # guard: no marker leaked in from another test
    with pytest.raises(CompileError) as exc_info:
        compile_pipeline(
            _REGISTERED_ZONE / "registered_op_relative_pipeline.py",
            tmp_path / "compiled.yaml",
            pipeline_name="Registered Relative Pipeline",
        )
    assert "zone-root markers" in str(exc_info.value)


def test_compile_registered_relative_gen_config_resolved_with_marker(tmp_path):
    """A downstream distribution appends its marker filename to the
    ``ZONE_ROOT_MARKERS`` seam (mutating the list in place) to restore
    zone-root resolution; a relative ``gen_config`` then resolves against the
    nearest ancestor holding that marker."""
    ZONE_ROOT_MARKERS.append("zone_root.marker")
    try:
        out = tmp_path / "compiled.yaml"
        compile_pipeline(
            _REGISTERED_ZONE / "registered_op_relative_pipeline.py",
            out,
            pipeline_name="Registered Relative Pipeline",
        )
        data = yaml.safe_load(out.read_text())
        (task,) = data["implementation"]["graph"]["tasks"].values()
        url = task["componentRef"]["url"]
        assert url.startswith("resolve://")
        assert url.endswith("registered_zone/gen_config.yaml#run-query")
    finally:
        # Restore the empty OSS default so ordering never leaks a marker into
        # test_compile_registered_relative_gen_config_rejected_without_marker
        # or test_zone_root_markers_empty_by_default.
        ZONE_ROOT_MARKERS.remove("zone_root.marker")
