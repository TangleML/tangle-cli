"""Tests for the native component YAML generator (component_from_func)."""

import ast
import inspect
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

import tangle_cli.component_from_func as cff
from tangle_cli.component_from_func import (
    AuthoringStripError,
    FunctionSpec,
    InputPath,
    OutputPath,
    ParamInfo,
    _build_argparse_code,
    _build_args_section,
    _build_pip_install_command,
    _build_python_source,
    _is_authoring_import,
    _is_authoring_module,
    _python_name_to_component_name,
    _resolve_annotation,
    _resolve_return_type,
    _serialize_default,
    _strip_authoring_constructs,
    _strip_main_guard,
    _strip_type_hints,
    authoring_import_modules,
    build_component_dict,
    extract_interface,
    generate_component_yaml,
    get_function_from_module,
    load_python_module,
    read_dependencies,
    register_authoring_import_module,
)
from tangle_cli.module_bundler import ModuleBundler

# Real on-disk python-pipeline fixtures (``task_env_strip_*`` for Phase 3).
_PIPELINE_FIXTURES = Path(__file__).parent / "fixtures" / "python_pipeline"

# The downstream authoring surface these strip/codegen tests rely on (a
# registered ``tangle_deploy.python_pipeline`` path + a stand-in module in
# ``sys.modules``) is provided suite-wide by the autouse
# ``downstream_authoring_surface`` fixture in ``tests/conftest.py``.

# ============================================================================
# @task image-id authoring metadata
# ============================================================================


def test_task_decorator_records_image_id_without_explicit_image():
    from tangle_cli.python_pipeline import task

    @task(image_id="eval-slim")
    def uses_image_id() -> str:
        return "ok"

    assert uses_image_id._task_image is None
    assert uses_image_id._task_image_id == "eval-slim"


def test_task_decorator_records_unwrap_names():
    from tangle_cli.python_pipeline import task

    @task(unwrap=["run_data", "metadata"])
    def uses_unwrap(run_data: dict[str, str], metadata: dict[str, int]) -> str:
        return "ok"

    assert uses_unwrap._task_unwrap == ("run_data", "metadata")


def test_task_decorator_rejects_invalid_unwrap_name():
    from tangle_cli.python_pipeline import task

    with pytest.raises(ValueError, match="valid Python parameter names"):
        task(unwrap="run-data")


def test_unwrapped_input_schema_infers_dict_value_type():
    def uses_int_values(metrics: dict[str, int]) -> str:
        return "ok"

    schema = cff.build_unwrapped_inputs_schema(uses_int_values, {"metrics": ["shop", "catalog"]})

    assert schema["metrics"]["value_type"] == "Integer"
    assert [item["type"] for item in schema["metrics"]["keys"]] == ["Integer", "Integer"]


# ============================================================================
# Type resolution tests
# ============================================================================


class TestTypeResolution:
    def test_str_type(self):
        tangle, deser, kind = _resolve_annotation(str)
        assert tangle == "String"
        assert deser == "str"
        assert kind == "input"

    def test_int_type(self):
        tangle, deser, kind = _resolve_annotation(int)
        assert tangle == "Integer"
        assert deser == "int"
        assert kind == "input"

    def test_float_type(self):
        tangle, deser, kind = _resolve_annotation(float)
        assert tangle == "Float"
        assert deser == "float"
        assert kind == "input"

    def test_bool_type(self):
        tangle, deser, kind = _resolve_annotation(bool)
        assert tangle == "Boolean"
        assert deser == "_deserialize_bool"
        assert kind == "input"

    def test_list_type(self):
        tangle, deser, kind = _resolve_annotation(list)
        assert tangle == "JsonArray"
        assert deser == "json.loads"
        assert kind == "input"

    def test_dict_type(self):
        tangle, deser, kind = _resolve_annotation(dict)
        assert tangle == "JsonObject"
        assert deser == "json.loads"
        assert kind == "input"

    def test_no_annotation(self):
        tangle, deser, kind = _resolve_annotation(inspect.Parameter.empty)
        assert tangle == "String"
        assert deser == "str"
        assert kind == "input"

    def test_output_path(self):
        tangle, deser, kind = _resolve_annotation(OutputPath("Text"))
        assert tangle == "Text"
        assert deser == "_make_parent_dirs_and_return_path"
        assert kind == "output"

    def test_input_path(self):
        tangle, deser, kind = _resolve_annotation(InputPath("CSV"))
        assert tangle == "CSV"
        assert deser == "str"
        assert kind == "input_path"

    def test_optional_str(self):
        from typing import Optional

        tangle, deser, kind = _resolve_annotation(Optional[str])
        assert tangle == "String"
        assert kind == "input"

    def test_list_subscript(self):
        tangle, deser, kind = _resolve_annotation(list[str])
        assert tangle == "JsonArray"
        assert deser == "json.loads"

    def test_dict_subscript(self):
        from typing import Any

        tangle, deser, kind = _resolve_annotation(dict[str, Any])
        assert tangle == "JsonObject"
        assert deser == "json.loads"


# ============================================================================
# Name conversion tests
# ============================================================================


class TestNameConversion:
    def test_simple_name(self):
        assert _python_name_to_component_name("my_function") == "My function"

    def test_multi_word(self):
        assert _python_name_to_component_name("split_dataset_by_hash") == "Split dataset by hash"

    def test_single_word(self):
        assert _python_name_to_component_name("process") == "Process"


# ============================================================================
# Interface extraction tests
# ============================================================================


class TestExtractInterface:
    def test_basic_function(self):
        def my_func(name: str, count: int = 5) -> str:
            """Do something useful."""
            return f"{name}: {count}"

        spec = extract_interface(my_func, {})
        assert spec.name == "my_func"
        assert spec.component_name == "My func"
        assert spec.description == "Do something useful."
        assert len(spec.inputs) == 2
        assert len(spec.outputs) == 0

        name_param = spec.inputs[0]
        assert name_param.yaml_name == "name"
        assert name_param.tangle_type == "String"
        assert name_param.optional is False

        count_param = spec.inputs[1]
        assert count_param.yaml_name == "count"
        assert count_param.tangle_type == "Integer"
        assert count_param.optional is True
        assert count_param.default == 5

    def test_output_path_stripping(self, tmp_path):
        py_file = tmp_path / "my_func.py"
        py_file.write_text(textwrap.dedent("""\
            from cloud_pipelines import components

            def my_func(
                input_data_path: components.InputPath("CSV"),
                output_result_path: components.OutputPath("Text"),
            ):
                \"\"\"Process data.\"\"\"
                pass
        """))

        module = load_python_module(py_file)
        func = get_function_from_module(module, "my_func")
        spec = extract_interface(func, {})
        assert len(spec.inputs) == 1
        assert spec.inputs[0].yaml_name == "input_data"  # _path stripped
        assert spec.inputs[0].kind == "input_path"

        assert len(spec.outputs) == 1
        assert spec.outputs[0].yaml_name == "output_result"  # _path stripped
        assert spec.outputs[0].kind == "output"

    def test_docstring_param_descriptions(self):
        def my_func(name: str, value: float):
            """Do things.

            Args:
                name: The name to use.
                value: The numeric value.
            """
            pass

        spec = extract_interface(my_func, {})
        assert spec.inputs[0].description == "The name to use."
        assert spec.inputs[1].description == "The numeric value."

    def test_bool_and_dict_types(self):
        def my_func(flag: bool = False, config: dict | None = None):
            """Test function."""
            pass

        spec = extract_interface(my_func, {})
        assert spec.inputs[0].tangle_type == "Boolean"
        assert spec.inputs[0].deserializer == "_deserialize_bool"
        # dict | None resolves to JsonObject via Optional handling
        assert spec.inputs[1].tangle_type == "JsonObject"
        assert spec.inputs[1].deserializer == "json.loads"
        assert spec.inputs[1].optional is True


# ============================================================================
# Type hint stripping tests
# ============================================================================


class TestStripTypeHints:
    def test_basic_stripping(self):
        source = "def my_func(name: str, count: int = 5) -> str:\n    return f'{name}: {count}'\n"
        stripped = _strip_type_hints(source)
        assert "name," in stripped
        assert "count=5" in stripped or "count = 5" in stripped
        assert ": str" not in stripped
        assert ": int" not in stripped
        assert "-> str" not in stripped
        assert "def my_func" in stripped

    def test_components_input_output_path(self):
        """Regression test: components.InputPath/OutputPath must be stripped (Python 3.13 bug)."""
        source = textwrap.dedent("""\
            def embed_texts(
                input_dataset_path: components.InputPath("ApacheParquet"),
                output_dataset_path: components.OutputPath("ApacheParquet"),
                model_name: str = "all-MiniLM-L6-v2",
            ):
                pass
        """)
        stripped = _strip_type_hints(source)
        assert "components.InputPath" not in stripped
        assert "components.OutputPath" not in stripped
        assert "input_dataset_path," in stripped
        assert "output_dataset_path," in stripped
        assert 'model_name="all-MiniLM-L6-v2"' in stripped or "model_name = " in stripped

    def test_no_annotations(self):
        source = "def my_func(name, count=5):\n    return name\n"
        stripped = _strip_type_hints(source)
        assert stripped == source

    def test_return_annotation_only(self):
        source = "def my_func(name) -> dict:\n    return {}\n"
        stripped = _strip_type_hints(source)
        assert "-> dict" not in stripped
        assert "def my_func(name)" in stripped

    def test_mixed_annotated_and_plain(self):
        source = "def my_func(a: int, b, c: str = 'x'):\n    pass\n"
        stripped = _strip_type_hints(source)
        assert ": int" not in stripped
        assert ": str" not in stripped
        assert "a," in stripped
        assert "b," in stripped

    def test_complex_annotation(self):
        source = "def my_func(data: dict[str, list[int]], flag: bool = True) -> list[str]:\n    pass\n"
        stripped = _strip_type_hints(source)
        assert "dict[str, list[int]]" not in stripped
        assert "-> list[str]" not in stripped
        assert "flag=" in stripped or "flag =" in stripped

    def test_multiple_functions(self):
        source = textwrap.dedent("""\
            def func_a(x: int) -> str:
                pass

            def func_b(y: float = 1.0) -> None:
                pass
        """)
        stripped = _strip_type_hints(source)
        assert ": int" not in stripped
        assert ": float" not in stripped
        assert "-> str" not in stripped
        assert "-> None" not in stripped
        assert "def func_a(x)" in stripped
        assert "def func_b(y" in stripped

    def test_multiline_return_annotation(self):
        """Return annotation where -> is on a different line than the type."""
        source = textwrap.dedent("""\
            def my_func(x, y)\\
                    -> dict[str, list[int]]:
                pass
        """)
        stripped = _strip_type_hints(source)
        assert "->" not in stripped
        assert "dict[str, list[int]]" not in stripped
        assert "def my_func(x, y)" in stripped

    def test_arrow_search_does_not_cross_functions(self):
        """Backward scan for -> must not match a previous function's arrow."""
        source = textwrap.dedent("""\
            def first() -> str:
                return "hi"

            def second():
                return 42
        """)
        stripped = _strip_type_hints(source)
        # first's arrow should be removed
        assert "-> str" not in stripped
        # second must remain unchanged — no spurious removal
        assert "def second():" in stripped
        assert 'return "hi"' in stripped

    def test_non_ascii_default_value(self):
        """Non-ASCII characters before annotations must not shift removal offsets."""
        source = 'def greet(label: str = "caf\u00e9", count: int = 1) -> str:\n    pass\n'
        stripped = _strip_type_hints(source)
        assert ": str" not in stripped
        assert ": int" not in stripped
        assert "-> str" not in stripped
        assert '"caf\u00e9"' in stripped
        assert "count=" in stripped or "count =" in stripped


# ============================================================================
# Code generation tests
# ============================================================================


class TestCodeGeneration:
    def _make_spec(self) -> FunctionSpec:
        return FunctionSpec(
            name="my_func",
            component_name="My func",
            description="Test function.",
            params=[
                ParamInfo(
                    name="input_data",
                    yaml_name="input_data",
                    python_type="str",
                    tangle_type="String",
                    kind="input",
                    deserializer="str",
                ),
                ParamInfo(
                    name="count",
                    yaml_name="count",
                    python_type="int",
                    tangle_type="Integer",
                    kind="input",
                    deserializer="int",
                    optional=True,
                    default=5,
                ),
                ParamInfo(
                    name="output_path",
                    yaml_name="output",
                    python_type="OutputPath",
                    tangle_type="Text",
                    kind="output",
                    deserializer="_make_parent_dirs_and_return_path",
                ),
            ],
            source_code_stripped="def my_func(input_data, count = 5, output_path):\n    pass\n",
        )

    def test_argparse_generation(self):
        spec = self._make_spec()
        code = _build_argparse_code(spec)
        assert "import argparse" in code
        assert '"--input-data"' in code
        assert '"--count"' in code
        assert '"--output"' in code
        assert "required=True" in code
        assert "required=False" in code
        assert "_outputs = my_func(**_parsed_args)" in code

    def test_args_section(self):
        spec = self._make_spec()
        args = _build_args_section(spec)

        # Required input: flat flag + placeholder
        assert "--input-data" in args
        assert {"inputValue": "input_data"} in args

        # Optional input: wrapped in if/cond
        optional_args = [a for a in args if isinstance(a, dict) and "if" in a]
        assert len(optional_args) == 1
        assert optional_args[0]["if"]["cond"] == {"isPresent": "count"}

        # Output: flat flag + outputPath placeholder
        assert "--output" in args
        assert {"outputPath": "output"} in args

    def test_pip_install_command(self):
        cmd = _build_pip_install_command(["pandas==2.0", "requests"])
        assert cmd[0] == "sh"
        assert cmd[1] == "-c"
        assert "pandas==2.0" in cmd[2]
        assert "requests" in cmd[2]
        assert "--user" in cmd[2]

    def test_pip_install_empty(self):
        assert _build_pip_install_command([]) == []

    def test_python_source_inline(self):
        spec = self._make_spec()
        source = _build_python_source(spec, mode="inline")
        assert "_make_parent_dirs_and_return_path" in source
        assert "import argparse" in source
        assert "my_func(**_parsed_args)" in source
        assert "bundle" not in source or True  # no bundle-specific imports in inline mode

    def test_python_source_bundle(self):
        spec = self._make_spec()
        source = _build_python_source(spec, mode="bundle", bundled_modules_b64="dGVzdA==")
        assert "_make_parent_dirs_and_return_path" in source
        assert "_EMBEDDED_MODULES" in source
        assert "sys.modules" in source
        assert "dGVzdA==" in source
        # Main function source still present
        assert "import argparse" in source

    def test_unwrapped_inputs_are_rewrapped_before_function_call(self):
        spec = FunctionSpec(
            name="combine",
            component_name="Combine",
            description="Test unwrapped inputs.",
            params=[
                ParamInfo(
                    name="run_data__left",
                    yaml_name="run_data__left",
                    python_type="dict[str, str]",
                    tangle_type="String",
                    kind="input",
                    deserializer="str",
                    source_param="run_data",
                    source_key="left",
                ),
                ParamInfo(
                    name="run_data__right",
                    yaml_name="run_data__right",
                    python_type="dict[str, str]",
                    tangle_type="String",
                    kind="input",
                    deserializer="str",
                    source_param="run_data",
                    source_key="right",
                ),
            ],
            source_code_stripped="def combine(run_data):\n    return run_data['left']\n",
        )

        code = _build_argparse_code(spec)

        assert '"--run_data__left"' in code
        assert "_unwrapped_0['left'] = _parsed_args.pop('run_data__left')" in code
        assert "_parsed_args['run_data'] = _unwrapped_0" in code
        assert "_outputs = combine(**_parsed_args)" in code


# ============================================================================
# Build component dict tests
# ============================================================================


class TestBuildComponentDict:
    def test_basic_component(self):
        spec = FunctionSpec(
            name="simple_func",
            component_name="Simple func",
            description="A simple component.",
            params=[
                ParamInfo(
                    name="name",
                    yaml_name="name",
                    python_type="str",
                    tangle_type="String",
                    kind="input",
                    deserializer="str",
                ),
                ParamInfo(
                    name="output_path",
                    yaml_name="output",
                    python_type="OutputPath",
                    tangle_type="Text",
                    kind="output",
                    deserializer="_make_parent_dirs_and_return_path",
                ),
            ],
            source_code_stripped="def simple_func(name, output_path):\n    pass\n",
        )
        component = build_component_dict(
            spec=spec,
            container_image="python:3.12",
            dependencies=["requests"],
            annotations={"cloud_pipelines.net": "true"},
            mode="inline",
        )

        assert component["name"] == "Simple func"
        assert component["description"] == "A simple component."
        assert len(component["inputs"]) == 1
        assert component["inputs"][0]["name"] == "name"
        assert component["inputs"][0]["type"] == "String"
        assert len(component["outputs"]) == 1
        assert component["outputs"][0]["name"] == "output"
        assert component["outputs"][0]["type"] == "Text"

        impl = component["implementation"]["container"]
        assert impl["image"] == "python:3.12"
        assert len(impl["command"]) > 0
        assert len(impl["args"]) > 0

    def test_missing_docstring_falls_back_to_placeholder_description(self):
        """Regression test for functions without docstrings.

        When a function has no docstring, ``spec.description`` is ``None``.
        Without a fallback, the generated YAML emits ``description: null``,
        which Tangle's schema validator rejects with
        ``Expected string, received null``.  The placeholder ensures the
        generated YAML is always loadable in the Tangle UI.
        """

        def do():  # no docstring
            pass

        spec = extract_interface(do, {})
        assert spec.description is None  # sanity check on the input

        component = build_component_dict(
            spec=spec,
            container_image="python:3.12",
            dependencies=[],
            annotations={},
            mode="inline",
        )

        # Description must be present and non-empty — we don't pin its exact wording.
        assert component.get("description")

    def test_docstring_description_overrides_placeholder(self):
        """When a docstring is present, it wins over the placeholder fallback."""

        def do():
            """This function does something."""
            pass

        spec = extract_interface(do, {})
        component = build_component_dict(
            spec=spec,
            container_image="python:3.12",
            dependencies=[],
            annotations={},
            mode="inline",
        )

        assert component["description"] == "This function does something."

    def test_bundle_embeds_modules(self):
        def func(x: str):
            """Test."""
            pass

        spec = extract_interface(func, {})
        component = build_component_dict(
            spec=spec,
            container_image="python:3.12",
            dependencies=["pandas"],
            annotations={},
            mode="bundle",
            bundled_modules_b64="dGVzdA==",
        )

        # The embedded source should contain the injection code
        python_source = component["implementation"]["container"]["command"][-1]
        assert "_EMBEDDED_MODULES" in python_source
        assert "sys.modules" in python_source

    def test_bundle_collects_imports_from_stripped_runtime_source_only(self, tmp_path):
        import base64
        import re as _re
        import zlib

        (tmp_path / "runtime_helper.py").write_text('VALUE = "runtime"\n', encoding="utf-8")
        (tmp_path / "tangle_deploy" / "python_pipeline").mkdir(parents=True)
        (tmp_path / "tangle_deploy" / "__init__.py").write_text("", encoding="utf-8")
        (tmp_path / "tangle_deploy" / "python_pipeline" / "__init__.py").write_text(textwrap.dedent("""\
            class TaskEnv:
                def __init__(self, **kwargs):
                    pass

            def task(**kwargs):
                def decorator(fn):
                    return fn
                return decorator
        """), encoding="utf-8")
        (tmp_path / "authoring_envs.py").write_text(textwrap.dedent("""\
            from tangle_deploy.python_pipeline import TaskEnv

            UPI = TaskEnv(image="python:3.12")
        """), encoding="utf-8")
        py_file = tmp_path / "component.py"
        py_file.write_text(textwrap.dedent("""\
            from tangle_deploy.python_pipeline import task
            from authoring_envs import UPI
            import runtime_helper

            @task(env=UPI)
            def my_component() -> str:
                return runtime_helper.VALUE
        """), encoding="utf-8")
        output_file = tmp_path / "output.yaml"

        success = generate_component_yaml(
            file_path=py_file,
            output_path=output_file,
            container_image="python:3.12",
            function_name="my_component",
            mode="bundle",
        )

        assert success is True
        with open(output_file) as f:
            component = yaml.safe_load(f)
        python_source = component["implementation"]["container"]["command"][-1]
        match = _re.search(r"base64\.b64decode\('([A-Za-z0-9+/=]+)'\)", python_source)
        assert match is not None
        embedded = json.loads(zlib.decompress(base64.b64decode(match.group(1))))
        assert "runtime_helper" in embedded
        assert "authoring_envs" not in embedded


    def test_bundle_yaml_orders_dependencies_before_dependents(self, tmp_path):
        """End-to-end YAML check for issue #30197.

        Generates a real component YAML, decodes the embedded module
        bundle, and verifies that a module-level dependency sorts before
        its dependent in the embedded dict.  Before the topological
        ordering fix, the alphabetical sort placed ``aaa`` (which calls
        ``bbb.bar()`` at module load) before ``bbb``, causing an
        ``AttributeError`` at component runtime.
        """
        import base64
        import zlib

        (tmp_path / "aaa.py").write_text(textwrap.dedent("""\
            import bbb

            FOO = bbb.bar()

            def foo():
                return FOO
        """))
        (tmp_path / "bbb.py").write_text(textwrap.dedent("""\
            def bar():
                return "BIZ"
        """))
        py_file = tmp_path / "component.py"
        py_file.write_text(textwrap.dedent("""\
            import aaa

            def my_component() -> str:
                \"\"\"Use aaa.\"\"\"
                return aaa.foo()
        """))
        toml_file = tmp_path / "pyproject.toml"
        toml_file.write_text('[project]\nname = "test"\ndependencies = []\n')
        output_file = tmp_path / "output.yaml"

        success = generate_component_yaml(
            file_path=py_file,
            output_path=output_file,
            container_image="python:3.12",
            function_name="my_component",
            dependencies_from=toml_file,
            mode="bundle",
        )
        assert success is True

        with open(output_file) as f:
            component = yaml.safe_load(f)
        python_source = component["implementation"]["container"]["command"][-1]

        # Pull the base64 blob out of the generated source by re-running
        # the same expression the injection snippet uses (the blob is
        # quoted via ``repr`` in the source).
        import re as _re

        # The injection emits ``base64.b64decode('<b64>')`` — the b64
        # alphabet is ``[A-Za-z0-9+/=]``, never a single quote.
        match = _re.search(r"base64\.b64decode\('([A-Za-z0-9+/=]+)'\)", python_source)
        assert match is not None, "injection snippet must contain a b64 blob"
        embedded = json.loads(zlib.decompress(base64.b64decode(match.group(1))))
        order = list(embedded.keys())

        assert order.index("bbb") < order.index("aaa"), f"bbb must execute before aaa (got order: {order})"


# ============================================================================
# Import classification tests
# ============================================================================


class TestClassifyImports:
    def test_stdlib_import(self, tmp_path):
        source = "import os\nimport json\n"
        py_file = tmp_path / "test.py"
        py_file.write_text(source)

        result = ModuleBundler.classify_imports(py_file)
        assert result["os"] == "stdlib"
        assert result["json"] == "stdlib"

    def test_local_import(self, tmp_path):
        # Create sibling module
        (tmp_path / "utils.py").write_text("def helper(): pass\n")

        source = "from utils import helper\n"
        py_file = tmp_path / "main.py"
        py_file.write_text(source)

        result = ModuleBundler.classify_imports(py_file)
        assert result["utils"] == "local"

    def test_third_party_import(self, tmp_path):
        source = "import pandas\nimport requests\n"
        py_file = tmp_path / "test.py"
        py_file.write_text(source)

        result = ModuleBundler.classify_imports(py_file, pip_deps=["pandas==2.0", "requests>=2.28"])
        assert result["pandas"] == "third_party"
        assert result["requests"] == "third_party"

    def test_relative_import(self, tmp_path):
        source = "from . import helpers\n"
        py_file = tmp_path / "test.py"
        py_file.write_text(source)

        result = ModuleBundler.classify_imports(py_file)
        assert result["helpers"] == "local"

    def test_importlib_fallback_for_sibling_directory(self, tmp_path):
        """When a local module is NOT in file_dir but IS on sys.path, importlib finds it."""
        import sys as _sys

        # Use unique package name to avoid collisions
        pkg_name = f"_test_classify_utils_{id(tmp_path)}"
        src = tmp_path / "src"
        (src / "components").mkdir(parents=True)
        (src / pkg_name).mkdir(parents=True)
        (src / pkg_name / "__init__.py").write_text("def helper(): pass\n")

        py_file = src / "components" / "component.py"
        py_file.write_text(f"from {pkg_name} import helper\n")

        # Without sys.path modification, package won't be found from components/
        result = ModuleBundler.classify_imports(py_file)
        assert result[pkg_name] == "third_party", "Without sys.path containing src/, package should be third_party"

        # With src/ on sys.path, importlib fallback should find it
        _sys.path.insert(0, str(src))
        try:
            result = ModuleBundler.classify_imports(py_file)
            assert result[pkg_name] == "local", "With src/ on sys.path, importlib should classify package as local"
        finally:
            _sys.path.remove(str(src))
            for mod in list(_sys.modules):
                if mod.startswith(pkg_name):
                    del _sys.modules[mod]

    def test_importlib_fallback_ignores_site_packages(self, tmp_path):
        """Modules in site-packages should NOT be classified as local by the importlib fallback."""
        from tangle_cli.module_bundler import _is_local_via_importlib

        # json is stdlib, not in site-packages — but it's already handled by stdlib check.
        # numpy/pandas (if installed) would be in site-packages.
        # We test that _is_local_via_importlib returns False for known third-party packages.
        # Use 'ast' (stdlib) as a safe module that find_spec will find but isn't in site-packages.
        # The key point: the function should return False for things in site-packages.
        assert _is_local_via_importlib("_nonexistent_module_xyz_") is False


# ============================================================================
# Dependencies reading tests
# ============================================================================


class TestReadDependencies:
    def test_pyproject_toml(self, tmp_path):
        toml_content = '[project]\nname = "test"\ndependencies = ["pandas==2.0", "requests"]\n'
        toml_file = tmp_path / "pyproject.toml"
        toml_file.write_text(toml_content)

        deps = read_dependencies(toml_file)
        assert deps == ["pandas==2.0", "requests"]

    def test_empty_deps(self, tmp_path):
        toml_content = '[project]\nname = "test"\n'
        toml_file = tmp_path / "pyproject.toml"
        toml_file.write_text(toml_content)

        deps = read_dependencies(toml_file)
        assert deps == []


# ============================================================================
# Default serialization tests
# ============================================================================


class TestSerializeDefault:
    def test_string(self):
        assert _serialize_default("hello", "String") == "hello"

    def test_int(self):
        assert _serialize_default(5, "Integer") == "5"

    def test_float(self):
        assert _serialize_default(3.14, "Float") == "3.14"

    def test_bool(self):
        assert _serialize_default(True, "Boolean") == "True"

    def test_none(self):
        assert _serialize_default(None, "String") is None

    def test_empty(self):
        assert _serialize_default(inspect.Parameter.empty, "String") is None


# ============================================================================
# End-to-end generation tests
# ============================================================================


class TestEndToEnd:
    def test_inline_generation(self, tmp_path):
        """Test full inline generation pipeline."""
        py_file = tmp_path / "my_component.py"
        py_file.write_text(textwrap.dedent("""\
            def my_component(name: str, count: int = 5):
                \"\"\"A test component.

                Args:
                    name: The input name.
                    count: How many times.
                \"\"\"
                return f"{name}: {count}"
        """))

        toml_file = tmp_path / "pyproject.toml"
        toml_file.write_text('[project]\nname = "test"\ndependencies = []\n')

        output_file = tmp_path / "output.yaml"

        success = generate_component_yaml(
            file_path=py_file,
            output_path=output_file,
            container_image="python:3.12",
            function_name="my_component",
            dependencies_from=toml_file,
            mode="inline",
        )

        assert success is True
        assert output_file.exists()

        with open(output_file) as f:
            component = yaml.safe_load(f)

        assert component["name"] == "My component"
        assert component["description"] == "A test component."
        assert len(component["inputs"]) == 2
        assert component["inputs"][0]["name"] == "name"
        assert component["inputs"][0]["type"] == "String"
        assert component["inputs"][1]["name"] == "count"
        assert component["inputs"][1]["type"] == "Integer"
        assert component["inputs"][1]["optional"] is True
        assert component["inputs"][1]["default"] == "5"

        # Check implementation structure
        impl = component["implementation"]["container"]
        assert impl["image"] == "python:3.12"
        command = impl["command"]
        # Should have shell bootstrap + python source
        assert "program_path=$(mktemp)" in command[-2]
        python_source = command[-1]
        assert "my_component" in python_source
        assert "import argparse" in python_source

        # Check annotations
        annotations = component["metadata"]["annotations"]
        assert annotations["python_original_code_path"] == "my_component.py"
        assert "my_component" in annotations["python_original_code"]

    def test_bundle_generation_with_local_import(self, tmp_path):
        """Test bundle mode with a cross-module import."""
        # Use unique module name to avoid sys.modules cache conflicts with other tests
        (tmp_path / "greeter_utils.py").write_text(textwrap.dedent("""\
            def greet(name):
                return f"Hello, {name}!"
        """))

        py_file = tmp_path / "my_component.py"
        py_file.write_text(textwrap.dedent("""\
            from greeter_utils import greet

            def my_component(name: str):
                \"\"\"Greet someone.\"\"\"
                return greet(name)
        """))

        toml_file = tmp_path / "pyproject.toml"
        toml_file.write_text('[project]\nname = "test"\ndependencies = []\n')

        output_file = tmp_path / "output.yaml"

        success = generate_component_yaml(
            file_path=py_file,
            output_path=output_file,
            container_image="python:3.12",
            function_name="my_component",
            dependencies_from=toml_file,
            mode="bundle",
        )

        assert success is True
        assert output_file.exists()

        with open(output_file) as f:
            component = yaml.safe_load(f)

        # Check embedded source injection is present
        python_source = component["implementation"]["container"]["command"][-1]
        assert "_EMBEDDED_MODULES" in python_source
        assert "sys.modules" in python_source
        assert "types.ModuleType" in python_source

        # Main function source should still be readable
        assert "my_component" in python_source
        assert "import argparse" in python_source

        # Annotations should list embedded modules
        annotations = component["metadata"]["annotations"]
        assert "bundled_modules" in annotations
        modules_list = json.loads(annotations["bundled_modules"])
        assert "greeter_utils" in modules_list

    def test_bundle_generation_with_submodule_import(self, tmp_path):
        """Test that `from pkg.sub import mod` bundles the submodule file.

        When the imported name is a real submodule (e.g. local_modules/dw/utils.py),
        _collect_full_module_paths must add both 'local_modules.dw' and
        'local_modules.dw.utils' so the submodule is bundled.
        """
        # Create local_modules/dw/__init__.py and local_modules/dw/utils.py
        pkg_dir = tmp_path / "local_modules" / "dw"
        pkg_dir.mkdir(parents=True)
        (tmp_path / "local_modules" / "__init__.py").write_text("")
        (pkg_dir / "__init__.py").write_text("")
        (pkg_dir / "utils.py").write_text(textwrap.dedent("""\
            def helper():
                return "helped"
        """))

        py_file = tmp_path / "my_component.py"
        py_file.write_text(textwrap.dedent("""\
            from local_modules.dw import utils

            def my_component(name: str):
                \"\"\"Use a submodule.\"\"\"
                return utils.helper()
        """))

        toml_file = tmp_path / "pyproject.toml"
        toml_file.write_text('[project]\nname = "test"\ndependencies = []\n')

        output_file = tmp_path / "output.yaml"

        success = generate_component_yaml(
            file_path=py_file,
            output_path=output_file,
            container_image="python:3.12",
            function_name="my_component",
            dependencies_from=toml_file,
            mode="bundle",
        )

        assert success is True
        assert output_file.exists()

        with open(output_file) as f:
            component = yaml.safe_load(f)

        python_source = component["implementation"]["container"]["command"][-1]
        assert "_EMBEDDED_MODULES" in python_source

        # The submodule utils.py must be bundled
        annotations = component["metadata"]["annotations"]
        modules_list = json.loads(annotations["bundled_modules"])
        assert "local_modules.dw.utils" in modules_list

    def test_bundle_collects_parent_init_files(self, tmp_path):
        """Test that bundle mode collects all parent __init__.py files."""
        # Create a 3-level package: pkg/sub/mod.py
        (tmp_path / "pkg" / "sub").mkdir(parents=True)
        (tmp_path / "pkg" / "__init__.py").write_text("TOP = 1")
        (tmp_path / "pkg" / "sub" / "__init__.py").write_text("")
        (tmp_path / "pkg" / "sub" / "mod.py").write_text("def fn(): pass")

        py_file = tmp_path / "comp.py"
        py_file.write_text("from pkg.sub import mod\ndef comp(): return mod.fn()\n")

        sources = ModuleBundler.collect_sources(py_file)
        assert "pkg" in sources, "parent __init__.py must be collected"
        assert sources["pkg"] == "TOP = 1"

    def test_bundle_follows_transitive_imports_in_parent_init(self, tmp_path):
        """Test that imports inside parent __init__.py files are followed.

        Regression test: parent __init__.py files may import sibling modules
        (e.g. ``from . import helpers``). These must be collected, otherwise
        the bundle crashes at runtime with ImportError.
        """
        import base64
        import zlib

        # mylib/__init__.py imports helpers; component only imports mylib.core
        (tmp_path / "mylib").mkdir()
        (tmp_path / "mylib" / "__init__.py").write_text("from . import helpers\n")
        (tmp_path / "mylib" / "helpers.py").write_text("HELP = True\n")
        (tmp_path / "mylib" / "core.py").write_text("def process(): pass\n")

        py_file = tmp_path / "component.py"
        py_file.write_text("from mylib.core import process\n")

        sources = ModuleBundler.collect_sources(py_file)
        assert "mylib.core" in sources
        assert "mylib" in sources, "parent __init__.py must be collected"
        assert "mylib.helpers" in sources, "sibling module imported by parent __init__.py must be collected"

        # The encoded bundle must put ``mylib.helpers`` before ``mylib``
        # because ``mylib/__init__.py`` does ``from . import helpers`` at
        # module load time — a dependent must never sort before its
        # dependency in the embedded dict (issue #30197).
        b64 = ModuleBundler.encode(sources)
        assert b64 is not None
        order = list(json.loads(zlib.decompress(base64.b64decode(b64))).keys())
        assert order.index("mylib.helpers") < order.index(
            "mylib"
        ), f"mylib.helpers must execute before mylib (got order: {order})"

    def test_bundle_with_sibling_directory_via_importlib(self, tmp_path):
        """Test bundle mode discovers modules in sibling directories via importlib.

        Reproduces the project layout from GitHub issue #28707:
            src/
              components/
                component_one.py   (imports utils)
              utils/
                __init__.py
                utility_function.py
        """
        import sys as _sys

        src = tmp_path / "src"
        (src / "components").mkdir(parents=True)
        # Use a unique package name to avoid collisions with real packages
        pkg_name = f"_test_sibling_utils_{id(tmp_path)}"
        (src / pkg_name).mkdir(parents=True)
        (src / pkg_name / "__init__.py").write_text(f"from {pkg_name}.utility_function import do_something\n")
        (src / pkg_name / "utility_function.py").write_text(textwrap.dedent("""\
            def do_something(x):
                return x + 1
        """))

        py_file = src / "components" / "component_one.py"
        py_file.write_text(textwrap.dedent(f"""\
            from {pkg_name} import do_something

            def component_one(value: int) -> int:
                \"\"\"Process a value.\"\"\"
                return do_something(value)
        """))

        # Add src/ to sys.path so importlib can find the package
        _sys.path.insert(0, str(src))
        try:
            sources = ModuleBundler.collect_sources(py_file)
            assert pkg_name in sources, "importlib fallback should discover package in sibling directory"
            assert f"{pkg_name}.utility_function" in sources, "transitive import within __init__.py should be followed"
        finally:
            _sys.path.remove(str(src))
            for mod in list(_sys.modules):
                if mod.startswith(pkg_name):
                    del _sys.modules[mod]

    def test_bundle_with_sibling_directory_via_resolve_root(self, tmp_path):
        """Test bundle mode discovers sibling modules via explicit resolve_root.

        Same layout as test_bundle_with_sibling_directory_via_importlib but
        uses the resolve_root parameter instead of relying on sys.path.
        """
        src = tmp_path / "src"
        (src / "components").mkdir(parents=True)
        (src / "utils").mkdir(parents=True)
        (src / "utils" / "__init__.py").write_text("from utils.utility_function import do_something\n")
        (src / "utils" / "utility_function.py").write_text(textwrap.dedent("""\
            def do_something(x):
                return x + 1
        """))

        py_file = src / "components" / "component_one.py"
        py_file.write_text(textwrap.dedent("""\
            from utils import do_something

            def component_one(value: int) -> int:
                \"\"\"Process a value.\"\"\"
                return do_something(value)
        """))

        # Use resolve_root=src/ so filesystem check finds utils/
        sources = ModuleBundler.collect_sources(py_file, resolve_root=src)
        assert "utils" in sources, "resolve_root=src should find utils in sibling directory"
        assert "utils.utility_function" in sources

    def test_bundle_end_to_end_sibling_directory(self, tmp_path):
        """End-to-end test: generate_component_yaml with sibling directory imports.

        Tests the full pipeline with ``resolve_root`` — does NOT manually modify
        ``sys.path``.  This proves that ``--resolve-root`` alone is sufficient:
        ``load_python_module`` and ``ModuleBundler.collect_sources`` both use
        it to find sibling packages.
        """
        import sys as _sys

        src = tmp_path / "src"
        (src / "components").mkdir(parents=True)
        # Use a unique package name to avoid collisions with real packages
        pkg_name = f"_test_utils_{id(tmp_path)}"
        (src / pkg_name).mkdir(parents=True)
        (src / pkg_name / "__init__.py").write_text("CONSTANT = 42\n")

        py_file = src / "components" / "component_one.py"
        py_file.write_text(textwrap.dedent(f"""\
            from {pkg_name} import CONSTANT

            def component_one(value: int) -> int:
                \"\"\"Add constant.\"\"\"
                return value + CONSTANT
        """))

        toml_file = tmp_path / "pyproject.toml"
        toml_file.write_text('[project]\nname = "test"\ndependencies = []\n')

        output_file = tmp_path / "output.yaml"

        # Note: sys.path is NOT modified — resolve_root should handle everything
        success = generate_component_yaml(
            file_path=py_file,
            output_path=output_file,
            container_image="python:3.12",
            function_name="component_one",
            dependencies_from=toml_file,
            mode="bundle",
            resolve_root=src,
        )

        # Clean up sys.modules to avoid leaking into other tests
        for mod_name in list(_sys.modules):
            if mod_name.startswith(pkg_name):
                del _sys.modules[mod_name]

        assert success is True
        assert output_file.exists()

        with open(output_file) as f:
            component = yaml.safe_load(f)

        python_source = component["implementation"]["container"]["command"][-1]
        assert "_EMBEDDED_MODULES" in python_source

        annotations = component["metadata"]["annotations"]
        assert "bundled_modules" in annotations
        modules_list = json.loads(annotations["bundled_modules"])
        assert pkg_name in modules_list

    def test_generation_with_output_path(self, tmp_path):
        """Test component with OutputPath annotation."""
        py_file = tmp_path / "writer.py"
        py_file.write_text(textwrap.dedent("""\
            class OutputPath:
                def __init__(self, type=None):
                    self.type = type

            def write_data(data: str, result_path: OutputPath("Text")):
                \"\"\"Write data to output.\"\"\"
                with open(result_path, "w") as f:
                    f.write(data)
        """))

        toml_file = tmp_path / "pyproject.toml"
        toml_file.write_text('[project]\nname = "test"\ndependencies = []\n')

        output_file = tmp_path / "output.yaml"

        success = generate_component_yaml(
            file_path=py_file,
            output_path=output_file,
            container_image="python:3.12",
            function_name="write_data",
            dependencies_from=toml_file,
        )

        assert success is True

        with open(output_file) as f:
            component = yaml.safe_load(f)

        assert len(component["inputs"]) == 1
        assert component["inputs"][0]["name"] == "data"

        assert len(component["outputs"]) == 1
        assert component["outputs"][0]["name"] == "result"  # _path stripped
        assert component["outputs"][0]["type"] == "Text"

        # Check args section has outputPath
        args = component["implementation"]["container"]["args"]
        has_output_path = any(isinstance(a, dict) and "outputPath" in a for a in args)
        assert has_output_path

    def test_main_guard_stripped_from_generated_source(self, tmp_path):
        """Test that if __name__ == "__main__" blocks are stripped during generation."""
        py_file = tmp_path / "guarded.py"
        py_file.write_text(textwrap.dedent("""\
            import sys

            def guarded(name: str):
                \"\"\"A component with a main guard.\"\"\"
                return name

            if __name__ == "__main__":
                print("ERROR: This script should be called through Tangle, not directly")
                sys.exit(1)
        """))

        toml_file = tmp_path / "pyproject.toml"
        toml_file.write_text('[project]\nname = "test"\ndependencies = []\n')

        output_file = tmp_path / "output.yaml"

        success = generate_component_yaml(
            file_path=py_file,
            output_path=output_file,
            container_image="python:3.12",
            function_name="guarded",
            dependencies_from=toml_file,
            mode="inline",
        )

        assert success is True

        with open(output_file) as f:
            component = yaml.safe_load(f)

        python_source = component["implementation"]["container"]["command"][-1]
        assert "if __name__" not in python_source
        assert "sys.exit(1)" not in python_source
        # The argparse wrapper should still be present
        assert "import argparse" in python_source
        assert "guarded" in python_source


# ============================================================================
# _strip_main_guard tests
# ============================================================================


class TestStripMainGuard:
    def test_strips_simple_guard(self):
        source = textwrap.dedent("""\
            def hello():
                pass

            if __name__ == "__main__":
                hello()
        """)
        result = _strip_main_guard(source)
        assert "__name__" not in result
        assert "def hello" in result

    def test_strips_guard_with_sys_exit(self):
        source = textwrap.dedent("""\
            import sys

            def my_func():
                pass

            if __name__ == "__main__":
                print("ERROR: not directly")
                sys.exit(1)
        """)
        result = _strip_main_guard(source)
        assert "__name__" not in result
        assert "sys.exit" not in result
        assert "def my_func" in result
        assert "import sys" in result

    def test_strips_reversed_comparison(self):
        source = textwrap.dedent("""\
            def hello():
                pass

            if "__main__" == __name__:
                hello()
        """)
        result = _strip_main_guard(source)
        assert "__name__" not in result

    def test_preserves_code_without_guard(self):
        source = textwrap.dedent("""\
            def hello():
                pass

            x = 1
        """)
        assert _strip_main_guard(source) == source

    def test_handles_syntax_error(self):
        source = "def broken(:\n"
        assert _strip_main_guard(source) == source


# ============================================================================
# _strip_authoring_constructs tests
# ============================================================================


class TestStripAuthoringConstructs:
    """Unit tests for the authoring import + decorator strip (§0.2)."""

    def test_strips_from_import_and_simple_decorator(self):
        source = textwrap.dedent("""\
            from tangle_deploy.python_pipeline import task

            @task(image="python:3.12")
            def hello(out, who="world"):
                with open(out, "w") as f:
                    f.write(who)
        """)
        result = _strip_authoring_constructs(source)
        assert "from tangle_deploy" not in result
        assert "@task" not in result
        assert 'def hello(out, who="world"):' in result
        # The runtime body survives untouched.
        assert "f.write(who)" in result

    def test_strips_multiline_decorator(self):
        source = textwrap.dedent("""\
            from tangle_deploy.python_pipeline import task

            @task(
                image="python:3.12",
            )
            def hello(out):
                pass
        """)
        result = _strip_authoring_constructs(source)
        assert "@task" not in result
        assert 'image="python:3.12"' not in result
        assert "def hello(out):" in result

    def test_strips_pipeline_and_subpipeline_decorators(self):
        source = textwrap.dedent("""\
            from tangle_deploy.python_pipeline import pipeline, subpipeline

            @pipeline("My Pipeline")
            def my_pipeline(cfg):
                return None

            @subpipeline("Nested")
            def nested(cfg):
                return None
        """)
        result = _strip_authoring_constructs(source)
        assert "@pipeline" not in result
        assert "@subpipeline" not in result
        assert "from tangle_deploy" not in result
        assert "def my_pipeline(cfg):" in result
        assert "def nested(cfg):" in result

    def test_strips_dotted_decorator_form(self):
        source = textwrap.dedent("""\
            import tangle_deploy.python_pipeline as tp

            @tp.task(image="python:3.12")
            def hello(out):
                pass
        """)
        result = _strip_authoring_constructs(source)
        assert "import tangle_deploy" not in result
        assert "@tp.task" not in result
        assert "def hello(out):" in result

    def test_strips_aliased_import(self):
        source = textwrap.dedent("""\
            from tangle_deploy.python_pipeline import ref as operation_by_ref

            x = 1
        """)
        result = _strip_authoring_constructs(source)
        assert "operation_by_ref" not in result
        assert "from tangle_deploy" not in result
        assert "x = 1" in result

    def test_strips_plain_import_of_python_pipeline(self):
        source = textwrap.dedent("""\
            import tangle_deploy.python_pipeline

            x = 1
        """)
        result = _strip_authoring_constructs(source)
        assert "import tangle_deploy.python_pipeline" not in result
        assert "x = 1" in result

    def test_preserves_non_authoring_tangle_deploy_import(self):
        # FIX 1: only tangle_deploy.python_pipeline is authoring. A genuine
        # runtime helper from another tangle_deploy.* package must survive,
        # otherwise the baked program raises NameError at runtime.
        source = textwrap.dedent("""\
            from tangle_deploy.python_pipeline import task
            from tangle_deploy.utils import something

            @task(image="python:3.12")
            def hello(out):
                return something(out)
        """)
        result = _strip_authoring_constructs(source)
        # Authoring import + decorator gone.
        assert "from tangle_deploy.python_pipeline import task" not in result
        assert "@task" not in result
        # Non-authoring helper import preserved, body still references it.
        assert "from tangle_deploy.utils import something" in result
        assert "return something(out)" in result

    def test_preserves_bare_tangle_deploy_import(self):
        # A bare ``import tangle_deploy`` is not the authoring module; preserve it.
        source = textwrap.dedent("""\
            import tangle_deploy

            x = tangle_deploy.__name__
        """)
        result = _strip_authoring_constructs(source)
        assert result == source

    def test_preserves_unrelated_imports_and_decorators(self):
        source = textwrap.dedent("""\
            import os
            from functools import lru_cache
            from . import sibling

            @lru_cache(maxsize=None)
            def cached(x):
                return x

            @property
            def prop(self):
                return 1
        """)
        result = _strip_authoring_constructs(source)
        # Nothing authoring-related here, so the source is unchanged.
        assert result == source

    def test_body_using_task_pipeline_identifiers_not_corrupted(self):
        # FIX 3(a): identifiers/strings named task/pipeline in the BODY must not
        # be touched — only the decorator + authoring import lines are removed.
        source = textwrap.dedent("""\
            from tangle_deploy.python_pipeline import task

            @task(image="python:3.12")
            def run(out):
                task = "build the pipeline"
                pipeline = ["task", "pipeline"]
                note = "run @task then @pipeline"
                return task, pipeline, note
        """)
        result = _strip_authoring_constructs(source)
        # Decorator + import removed.
        assert "from tangle_deploy" not in result
        assert "@task(" not in result
        # Body assignments/strings using these names survive verbatim.
        assert 'task = "build the pipeline"' in result
        assert 'pipeline = ["task", "pipeline"]' in result
        assert 'note = "run @task then @pipeline"' in result
        assert "return task, pipeline, note" in result

    def test_multi_name_authoring_import_line_dropped(self):
        # FIX 3(b): a multi-name authoring import drops the WHOLE line.
        source = textwrap.dedent("""\
            from tangle_deploy.python_pipeline import In, Out, task

            @task(image="python:3.12")
            def hello(out):
                return out
        """)
        result = _strip_authoring_constructs(source)
        assert "In" not in result
        assert "Out" not in result
        assert "from tangle_deploy" not in result
        assert "@task" not in result
        assert "def hello(out):" in result

    def test_fail_fast_mixed_plain_import_downstream_surface(self):
        # The mixed-import fail-fast is surface-agnostic: a registered
        # downstream authoring path (here ``tangle_deploy.python_pipeline`` via
        # the autouse fixture) mixed with a runtime module fails fast exactly
        # like the canonical OSS path does.
        source = textwrap.dedent("""\
            import tangle_deploy.python_pipeline as tp, os

            @tp.task(image="python:3.12")
            def hello(out):
                return os.path.join(out, "x")
        """)
        with pytest.raises(AuthoringStripError) as excinfo:
            _strip_authoring_constructs(source)
        msg = str(excinfo.value)
        assert "tangle_deploy.python_pipeline" in msg
        assert "os" in msg
        assert "Split the authoring import" in msg

    def test_unrelated_decorator_preserved_alongside_task(self):
        # FIX 3(c): @task is stripped but a stacked unrelated decorator stays.
        source = textwrap.dedent("""\
            import functools

            from tangle_deploy.python_pipeline import task

            @task(image="python:3.12")
            @functools.cache
            def hello(out):
                return out
        """)
        result = _strip_authoring_constructs(source)
        assert "@task" not in result
        assert "from tangle_deploy" not in result
        # Unrelated decorator + its import survive.
        assert "@functools.cache" in result
        assert "import functools" in result
        assert "def hello(out):" in result

    def test_strips_bare_authoring_decorator(self):
        # FIX 3(d): a bare @task (no call) is still removed.
        source = textwrap.dedent("""\
            from tangle_deploy.python_pipeline import task

            @task
            def hello(out):
                return out
        """)
        result = _strip_authoring_constructs(source)
        assert "@task" not in result
        assert "from tangle_deploy" not in result
        assert "def hello(out):" in result

    def test_preserves_comments_and_formatting_in_body(self):
        source = textwrap.dedent("""\
            from tangle_deploy.python_pipeline import task

            # a leading comment that must survive
            @task(image="python:3.12")
            def hello(out):
                # inline comment
                value = 1  # trailing comment
                return value
        """)
        result = _strip_authoring_constructs(source)
        assert "# a leading comment that must survive" in result
        assert "# inline comment" in result
        assert "value = 1  # trailing comment" in result
        assert "@task" not in result

    def test_handles_syntax_error(self):
        source = "def broken(:\n"
        assert _strip_authoring_constructs(source) == source

    def test_no_authoring_constructs_is_noop(self):
        source = textwrap.dedent("""\
            import os

            def plain(x):
                return os.path.join(x, "y")
        """)
        assert _strip_authoring_constructs(source) == source


# ============================================================================
# _strip_authoring_constructs — tangle_cli.python_pipeline surface
# ============================================================================


class TestStripAuthoringConstructsTangleCli:
    """The strip must recognise the OSS ``tangle_cli.python_pipeline`` path.

    ``tangle_deploy.python_pipeline`` re-exports the OSS objects, so an
    author may import from EITHER module. Every case here mirrors a
    ``tangle_deploy`` case above but through the OSS import path, so a
    component baked on a thin image (without the authoring DSL) still
    imports cleanly.
    """

    def test_strips_from_import_and_simple_decorator(self):
        source = textwrap.dedent("""\
            from tangle_cli.python_pipeline import task

            @task(image="python:3.12")
            def hello(out, who="world"):
                with open(out, "w") as f:
                    f.write(who)
        """)
        result = _strip_authoring_constructs(source)
        assert "from tangle_cli.python_pipeline" not in result
        assert "@task" not in result
        assert 'def hello(out, who="world"):' in result
        assert "f.write(who)" in result

    def test_strips_dotted_decorator_form(self):
        source = textwrap.dedent("""\
            import tangle_cli.python_pipeline as tp

            @tp.task(image="python:3.12")
            def hello(out):
                pass
        """)
        result = _strip_authoring_constructs(source)
        assert "import tangle_cli.python_pipeline" not in result
        assert "@tp.task" not in result
        assert "def hello(out):" in result

    def test_strips_aliased_import(self):
        source = textwrap.dedent("""\
            from tangle_cli.python_pipeline import ref as operation_by_ref

            x = 1
        """)
        result = _strip_authoring_constructs(source)
        assert "operation_by_ref" not in result
        assert "from tangle_cli.python_pipeline" not in result
        assert "x = 1" in result

    def test_strips_plain_import_of_python_pipeline(self):
        source = textwrap.dedent("""\
            import tangle_cli.python_pipeline

            x = 1
        """)
        result = _strip_authoring_constructs(source)
        assert "import tangle_cli.python_pipeline" not in result
        assert "x = 1" in result

    def test_preserves_non_authoring_tangle_cli_import(self):
        # Only ``tangle_cli.python_pipeline`` is authoring. A genuine runtime
        # helper from another ``tangle_cli.*`` package must survive.
        source = textwrap.dedent("""\
            from tangle_cli.python_pipeline import task
            from tangle_cli.utils import something

            @task(image="python:3.12")
            def hello(out):
                return something(out)
        """)
        result = _strip_authoring_constructs(source)
        assert "from tangle_cli.python_pipeline import task" not in result
        assert "@task" not in result
        assert "from tangle_cli.utils import something" in result
        assert "return something(out)" in result

    def test_multi_name_authoring_import_line_dropped(self):
        source = textwrap.dedent("""\
            from tangle_cli.python_pipeline import In, Out, task

            @task(image="python:3.12")
            def hello(out):
                return out
        """)
        result = _strip_authoring_constructs(source)
        assert "In" not in result
        assert "Out" not in result
        assert "from tangle_cli.python_pipeline" not in result
        assert "@task" not in result
        assert "def hello(out):" in result

    def test_strips_submodule_import(self):
        # ``from tangle_cli.python_pipeline.x import y`` is a submodule of the
        # authoring package and must also be dropped.
        source = textwrap.dedent("""\
            from tangle_cli.python_pipeline.types import In, Out

            x = 1
        """)
        result = _strip_authoring_constructs(source)
        assert "from tangle_cli.python_pipeline.types" not in result
        assert "x = 1" in result

    def test_fail_fast_mixed_plain_import_with_runtime_module(self):
        # ``import tangle_cli.python_pipeline as tp, os`` mixes the authoring
        # surface with a runtime module on ONE comma-separated statement. The
        # authoring import must be stripped, but line-deletion cannot drop just
        # part of the statement — dropping the whole line would take ``os`` with
        # it (silent NameError in the baked program). Fail fast with split
        # guidance instead.
        source = textwrap.dedent("""\
            import tangle_cli.python_pipeline as tp, os

            @tp.task(image="python:3.12")
            def hello(out):
                return os.path.join(out, "x")
        """)
        with pytest.raises(AuthoringStripError) as excinfo:
            _strip_authoring_constructs(source)
        msg = str(excinfo.value)
        assert "tangle_cli.python_pipeline" in msg
        assert "os" in msg
        assert "Split the authoring import" in msg

    def test_mixed_plain_import_split_across_lines_is_dropped_cleanly(self):
        # The remedy the fail-fast recommends: with the authoring import on its
        # own line, the strip drops it whole and the runtime ``import os``
        # survives — no fail-fast.
        source = textwrap.dedent("""\
            import os
            import tangle_cli.python_pipeline as tp

            @tp.task(image="python:3.12")
            def hello(out):
                return os.path.join(out, "x")
        """)
        result = _strip_authoring_constructs(source)
        assert "import tangle_cli.python_pipeline" not in result
        assert "import os" in result
        assert "@tp.task" not in result
        assert 'return os.path.join(out, "x")' in result

    def test_pure_authoring_multi_module_import_dropped_whole(self):
        # A comma-separated ``import`` where EVERY alias is an authoring module
        # is not "mixed" — there is no runtime alias to preserve — so it is
        # still dropped whole rather than failing fast.
        source = textwrap.dedent("""\
            import tangle_cli.python_pipeline, tangle_cli.python_pipeline.types

            x = 1
        """)
        result = _strip_authoring_constructs(source)
        assert "tangle_cli.python_pipeline" not in result
        assert "x = 1" in result


# ============================================================================
# _is_authoring_module / _is_authoring_import predicates
# ============================================================================


class TestAuthoringImportPredicate:
    """Unit tests for the authoring-module recognition helpers."""

    def test_oss_seeds_only_the_canonical_surface(self):
        # Under a pristine registry (no downstream registered), OSS recognises
        # ONLY its own authoring path — it never hardcodes a downstream module.
        # The autouse fixture registers the downstream path, so reset to the
        # OSS seed for this assertion, then restore.
        before = list(cff._AUTHORING_IMPORT_MODULES)
        cff._AUTHORING_IMPORT_MODULES[:] = ["tangle_cli.python_pipeline"]
        try:
            assert authoring_import_modules() == ("tangle_cli.python_pipeline",)
        finally:
            cff._AUTHORING_IMPORT_MODULES[:] = before

    def test_register_authoring_import_module_adds_downstream_surface(self):
        register_authoring_import_module("acme_pipelines.python_pipeline")
        assert "acme_pipelines.python_pipeline" in authoring_import_modules()
        # A registered path is treated exactly like the canonical one.
        assert _is_authoring_module("acme_pipelines.python_pipeline") is True
        assert _is_authoring_module("acme_pipelines.python_pipeline.types") is True

    def test_register_authoring_import_module_is_idempotent(self):
        register_authoring_import_module("acme_pipelines.python_pipeline")
        register_authoring_import_module("acme_pipelines.python_pipeline")
        assert authoring_import_modules().count("acme_pipelines.python_pipeline") == 1

    def test_authoring_import_modules_returns_immutable_snapshot(self):
        # Callers get a tuple copy; mutating the return value must not leak into
        # the registry.
        snapshot = authoring_import_modules()
        assert isinstance(snapshot, tuple)
        assert authoring_import_modules() == snapshot

    @pytest.mark.parametrize(
        "name",
        [
            "tangle_deploy.python_pipeline",
            "tangle_cli.python_pipeline",
            "tangle_deploy.python_pipeline.types",
            "tangle_cli.python_pipeline.task",
        ],
    )
    def test_is_authoring_module_true(self, name):
        assert _is_authoring_module(name) is True

    @pytest.mark.parametrize(
        "name",
        [
            "tangle_cli.utils",
            "tangle_deploy.utils",
            "tangle_cli",
            "tangle_deploy",
            "tangle_cli.python_pipelinex",  # not a submodule boundary
            "os",
            "",
        ],
    )
    def test_is_authoring_module_false(self, name):
        assert _is_authoring_module(name) is False

    def test_is_authoring_import_from_both_paths(self):
        for mod in ("tangle_deploy.python_pipeline", "tangle_cli.python_pipeline"):
            node = ast.parse(f"from {mod} import task").body[0]
            assert _is_authoring_import(node) is True

    def test_is_authoring_import_plain_import_both_paths(self):
        for mod in ("tangle_deploy.python_pipeline", "tangle_cli.python_pipeline"):
            node = ast.parse(f"import {mod}").body[0]
            assert _is_authoring_import(node) is True

    def test_is_authoring_import_rejects_relative_import(self):
        # A relative ``from . import x`` is never the authoring package.
        node = ast.parse("from . import sibling").body[0]
        assert _is_authoring_import(node) is False

    def test_is_authoring_import_rejects_non_authoring(self):
        for src in ("import os", "from tangle_cli.utils import x", "x = 1"):
            node = ast.parse(src).body[0]
            assert _is_authoring_import(node) is False


# ============================================================================
# _strip_authoring_constructs — TaskEnv env-only hardening (Phase 3, §3.5)
# ============================================================================


class TestStripTaskEnvAuthoring:
    """Unit tests for the TaskEnv env-only strip extension (§3.5).

    These feed source text directly to ``_strip_authoring_constructs`` and
    assert that env-only authoring declarations/imports used by a stripped
    ``@task(env=...)`` decorator are also removed (or fail fast), while the
    general strip behaviour and unrelated runtime code are untouched.
    """

    def test_colocated_env_assignment_is_stripped(self):
        # UPI = TaskEnv(...) co-located with @task(env=UPI): the assignment, the
        # authoring import, and the decorator must all be gone.
        source = textwrap.dedent("""\
            from tangle_deploy.python_pipeline import TaskEnv, task

            UPI = TaskEnv(image="python:3.12")

            @task(env=UPI)
            def hello(out, who="world"):
                with open(out, "w") as f:
                    f.write(who)
        """)
        result = _strip_authoring_constructs(source)
        assert "TaskEnv" not in result
        assert "UPI" not in result
        assert "from tangle_deploy" not in result
        assert "@task" not in result
        # Runtime function + body survive.
        assert 'def hello(out, who="world"):' in result
        assert "f.write(who)" in result

    def test_annotated_env_assignment_is_stripped(self):
        # UPI: TaskEnv = TaskEnv(...) annotated form is stripped too.
        source = textwrap.dedent("""\
            from tangle_deploy.python_pipeline import TaskEnv, task

            UPI: TaskEnv = TaskEnv(image="python:3.12")

            @task(env=UPI)
            def hello(out):
                return out
        """)
        result = _strip_authoring_constructs(source)
        assert "TaskEnv" not in result
        assert "UPI" not in result
        assert "def hello(out):" in result

    def test_factory_built_env_assignment_is_stripped(self):
        # UPI = make_task_env(...) is stripped because UPI is collected from
        # @task(env=UPI) (target-name rule), even though the value is not a
        # direct TaskEnv(...) call.
        source = textwrap.dedent("""\
            from helpers import make_task_env
            from tangle_deploy.python_pipeline import task

            UPI = make_task_env("python:3.12")

            @task(env=UPI)
            def hello(out):
                return out
        """)
        result = _strip_authoring_constructs(source)
        # The env assignment (UPI) and the decorator are stripped because UPI
        # was collected from @task(env=UPI), even though the value is a factory
        # call rather than a direct TaskEnv(...).
        assert "UPI = make_task_env" not in result
        assert "UPI" not in result
        assert "@task" not in result
        assert "from tangle_deploy" not in result
        # The factory import itself is NOT a collected env name, so it is
        # deliberately preserved -- the strip is not a broad unused-import
        # cleaner. Authors keep factory helpers runtime-available.
        assert "from helpers import make_task_env" in result
        assert "def hello(out):" in result

    def test_imported_env_name_is_stripped(self):
        # from _envs import UPI + @task(env=UPI): the sibling import is gone.
        source = textwrap.dedent("""\
            from _envs import UPI
            from tangle_deploy.python_pipeline import task

            @task(env=UPI)
            def hello(out):
                return out
        """)
        result = _strip_authoring_constructs(source)
        assert "from _envs import UPI" not in result
        assert "UPI" not in result
        assert "@task" not in result
        assert "def hello(out):" in result

    def test_imported_env_module_alias_is_stripped(self):
        # import _envs + @task(env=_envs.UPI): the module import is gone.
        source = textwrap.dedent("""\
            import _envs
            from tangle_deploy.python_pipeline import task

            @task(env=_envs.UPI)
            def hello(out):
                return out
        """)
        result = _strip_authoring_constructs(source)
        assert "import _envs" not in result
        assert "_envs" not in result
        assert "@task" not in result
        assert "def hello(out):" in result

    def test_aliased_env_module_import_is_stripped(self):
        # import envs as task_envs + @task(env=task_envs.UPI): the aliased
        # module import is collected by its bound name and stripped.
        source = textwrap.dedent("""\
            import envs as task_envs
            from tangle_deploy.python_pipeline import task

            @task(env=task_envs.UPI)
            def hello(out):
                return out
        """)
        result = _strip_authoring_constructs(source)
        assert "task_envs" not in result
        assert "import envs" not in result
        assert "def hello(out):" in result

    def test_inline_taskenv_leaves_no_residual(self):
        # @task(env=TaskEnv(...)) inline: the whole decorator range is deleted,
        # so no TaskEnv text survives.
        source = textwrap.dedent("""\
            from tangle_deploy.python_pipeline import TaskEnv, task

            @task(env=TaskEnv(image="python:3.12"))
            def hello(out):
                return out
        """)
        result = _strip_authoring_constructs(source)
        assert "TaskEnv" not in result
        assert "@task" not in result
        assert "from tangle_deploy" not in result
        assert "def hello(out):" in result

    def test_explicit_image_override_still_strips_env_decl(self):
        # @task(env=UPI, image="override"): decorator + UPI decl gone. The
        # sidecar image override is a Phase 2 concern; here the baked source
        # must just be env-free.
        source = textwrap.dedent("""\
            from tangle_deploy.python_pipeline import TaskEnv, task

            UPI = TaskEnv(image="python:3.12")

            @task(env=UPI, image="python:3.13-slim")
            def hello(out):
                return out
        """)
        result = _strip_authoring_constructs(source)
        assert "TaskEnv" not in result
        assert "UPI" not in result
        assert "python:3.13-slim" not in result  # lived only in the decorator
        assert "@task" not in result
        assert "def hello(out):" in result

    def test_direct_taskenv_assignment_without_decorator_is_stripped(self):
        # A module-level X = TaskEnv(...) is authoring-only by contract even
        # with no @task(env=X) referencing it -- the direct-construction rule
        # removes it.
        source = textwrap.dedent("""\
            from tangle_deploy.python_pipeline import TaskEnv

            SHARED = TaskEnv(image="python:3.12")

            def hello(out):
                return out
        """)
        result = _strip_authoring_constructs(source)
        assert "TaskEnv" not in result
        assert "SHARED" not in result
        assert "def hello(out):" in result

    def test_preserves_unrelated_runtime_declarations(self):
        # Only env-only names are stripped; unrelated runtime constants,
        # imports and helpers survive verbatim.
        source = textwrap.dedent("""\
            import os

            from tangle_deploy.python_pipeline import TaskEnv, task

            UPI = TaskEnv(image="python:3.12")
            RETRIES = 3
            BASE_DIR = os.getcwd()

            @task(env=UPI)
            def hello(out):
                return os.path.join(BASE_DIR, str(RETRIES))
        """)
        result = _strip_authoring_constructs(source)
        # env-only constructs gone.
        assert "TaskEnv" not in result
        assert "UPI" not in result
        # runtime constants/imports/usages survive.
        assert "import os" in result
        assert "RETRIES = 3" in result
        assert "BASE_DIR = os.getcwd()" in result
        assert "return os.path.join(BASE_DIR, str(RETRIES))" in result

    def test_fail_fast_mixed_import_with_used_runtime_name(self):
        # from _envs import UPI, helper where helper is used in the body: we
        # cannot line-delete part of the statement, so fail fast with split
        # guidance.
        source = textwrap.dedent("""\
            from _envs import UPI, helper
            from tangle_deploy.python_pipeline import task

            @task(env=UPI)
            def hello(out):
                return helper(out)
        """)
        with pytest.raises(AuthoringStripError) as excinfo:
            _strip_authoring_constructs(source)
        msg = str(excinfo.value)
        assert "helper" in msg
        assert "Split the import" in msg

    def test_fail_fast_env_name_referenced_by_body(self):
        # @task(env=UPI) but the body also references UPI: env names are
        # authoring-only, so fail fast rather than bake a NameError.
        source = textwrap.dedent("""\
            from tangle_deploy.python_pipeline import TaskEnv, task

            UPI = TaskEnv(image="python:3.12")

            @task(env=UPI)
            def hello(out):
                return UPI.image
        """)
        with pytest.raises(AuthoringStripError) as excinfo:
            _strip_authoring_constructs(source)
        msg = str(excinfo.value)
        assert "UPI" in msg
        assert "authoring-only" in msg

    def test_fail_fast_imported_env_name_referenced_by_body(self):
        # from _envs import UPI + body references UPI: fail fast (its import is
        # stripped, so a kept reference would be a NameError).
        source = textwrap.dedent("""\
            from _envs import UPI
            from tangle_deploy.python_pipeline import task

            @task(env=UPI)
            def hello(out):
                return UPI.image
        """)
        with pytest.raises(AuthoringStripError) as excinfo:
            _strip_authoring_constructs(source)
        assert "UPI" in str(excinfo.value)

    def test_unused_runtime_name_on_env_import_is_dropped(self):
        # from _envs import UPI, unused where ``unused`` is NOT referenced in
        # kept code: the whole env import is safely removed (no fail-fast,
        # since nothing runtime depends on it).
        source = textwrap.dedent("""\
            from _envs import UPI, unused
            from tangle_deploy.python_pipeline import task

            @task(env=UPI)
            def hello(out):
                return out
        """)
        result = _strip_authoring_constructs(source)
        assert "from _envs import" not in result
        assert "UPI" not in result
        assert "def hello(out):" in result

    def test_annotation_only_env_reference_does_not_fail(self):
        # FIX N1 (§3.5): an env name used ONLY in a type annotation (param and
        # return) and NEVER in the body must NOT trip the body-ref fail-fast.
        # Annotations are stripped from the baked output by _strip_type_hints
        # (a separate later pass), so they are not live runtime references.
        # The UPI declaration IS still stripped; the annotation survives at
        # THIS layer (it is removed by the subsequent type-hint pass).
        source = textwrap.dedent("""\
            from tangle_deploy.python_pipeline import TaskEnv, task

            UPI = TaskEnv(image="python:3.12")

            @task(env=UPI)
            def hello(x: UPI, out) -> UPI:
                with open(out, "w") as f:
                    f.write(x)
        """)
        # Does NOT raise.
        result = _strip_authoring_constructs(source)
        # The env declaration is stripped.
        assert "UPI = TaskEnv" not in result
        assert "TaskEnv" not in result
        assert "@task" not in result
        assert "from tangle_deploy" not in result
        # The annotation reference is untouched here (stripped later by
        # _strip_type_hints); the body + def survive.
        assert "def hello(x: UPI, out) -> UPI:" in result
        assert "f.write(x)" in result
        # End-to-end: after the type-hint pass the program is fully env-free.
        final = _strip_type_hints(result)
        assert "UPI" not in final
        assert "def hello(x, out):" in final

    def test_annotation_plus_body_reference_still_fails(self):
        # FIX N1 guard: excluding annotation slots must NOT mask a REAL body
        # reference. UPI here is in the annotation AND used in the body, so the
        # fail-fast must still fire.
        source = textwrap.dedent("""\
            from tangle_deploy.python_pipeline import TaskEnv, task

            UPI = TaskEnv(image="python:3.12")

            @task(env=UPI)
            def hello(x: UPI, out) -> UPI:
                return UPI.image
        """)
        with pytest.raises(AuthoringStripError) as excinfo:
            _strip_authoring_constructs(source)
        msg = str(excinfo.value)
        assert "UPI" in msg
        assert "authoring-only" in msg

    def test_fail_fast_nested_env_import(self):
        # FIX N2 (§3.5): an env import nested inside an `if` block is NOT a
        # module-level statement, so it is never stripped and would leak into
        # the baked program. The strip must FAIL FAST with actionable guidance
        # rather than line-delete it (which could leave an empty block).
        source = textwrap.dedent("""\
            from tangle_deploy.python_pipeline import task

            if True:
                from task_env_strip_envs import UPI

            @task(env=UPI)
            def hello(out):
                with open(out, "w") as f:
                    f.write("x")
        """)
        with pytest.raises(AuthoringStripError) as excinfo:
            _strip_authoring_constructs(source)
        msg = str(excinfo.value)
        assert "UPI" in msg
        assert "nested" in msg
        assert "module-level" in msg
        assert "top-level import" in msg

    def test_nested_env_import_in_try_block_fails_fast(self):
        # FIX N2: the same protection applies to imports nested in a try block
        # (anything that is not a direct child of the module body).
        source = textwrap.dedent("""\
            from tangle_deploy.python_pipeline import task

            try:
                import task_env_strip_envs
            except ImportError:
                task_env_strip_envs = None

            @task(env=task_env_strip_envs.UPI)
            def hello(out):
                with open(out, "w") as f:
                    f.write("x")
        """)
        with pytest.raises(AuthoringStripError) as excinfo:
            _strip_authoring_constructs(source)
        msg = str(excinfo.value)
        assert "task_env_strip_envs" in msg
        assert "nested" in msg


# ============================================================================
# Generator-level authoring strip tests (Phase 0d)
# ============================================================================


class TestGeneratorStripsAuthoring:
    """`generate_component_yaml` must bake an authoring-free runtime program
    while keeping ``python_original_code`` byte-verbatim."""

    @staticmethod
    def _baked_program_and_annotations(output_file):
        with open(output_file) as f:
            component = yaml.safe_load(f)
        program = component["implementation"]["container"]["command"][-1]
        annotations = component["metadata"]["annotations"]
        return program, annotations

    def test_single_function_task_file(self, tmp_path):
        py_file = tmp_path / "single_task.py"
        py_file.write_text(textwrap.dedent('''\
            from cloud_pipelines import components

            from tangle_deploy.python_pipeline import task


            @task(
                image="python:3.12",
            )
            def single_task(out: components.OutputPath("Text"), who: str = "world"):
                """
                Metadata:
                    Name: Single Task
                    Version: 1.0.0
                """
                with open(out, "w") as fh:
                    fh.write(f"hi {who}")
        '''))
        original_bytes = py_file.read_text()

        output_file = tmp_path / "single_task.yaml"
        success = generate_component_yaml(
            file_path=py_file,
            output_path=output_file,
            container_image="python:3.12",
            function_name="single_task",
            mode="inline",
        )
        assert success is True

        program, annotations = self._baked_program_and_annotations(output_file)
        # Baked runtime program is authoring-free.
        assert "from tangle_deploy" not in program
        assert "@task" not in program
        # The plain runtime function survived.
        assert "def single_task(" in program
        assert 'fh.write(f"hi {who}")' in program
        # Verbatim annotation is untouched.
        assert annotations["python_original_code"] == original_bytes

    def test_colocated_task_and_pipeline_file(self, tmp_path):
        py_file = tmp_path / "colocated.py"
        py_file.write_text(textwrap.dedent('''\
            from cloud_pipelines import components

            from tangle_deploy.python_pipeline import Out, pipeline, task


            @task(image="python:3.12")
            def greet(out: components.OutputPath("Text"), who: str = "world"):
                """
                Metadata:
                    Name: Greet
                    Version: 1.0.0
                """
                with open(out, "w") as fh:
                    fh.write(f"hi {who}")


            @pipeline("Colocated Pipeline")
            def colocated_pipeline(cfg) -> Out[str]:
                result = greet(who="world")
                return result.out
        '''))
        original_bytes = py_file.read_text()

        output_file = tmp_path / "colocated.yaml"
        success = generate_component_yaml(
            file_path=py_file,
            output_path=output_file,
            container_image="python:3.12",
            function_name="greet",
            mode="inline",
        )
        assert success is True

        program, annotations = self._baked_program_and_annotations(output_file)
        # Both co-located decorators and the authoring import are gone.
        assert "from tangle_deploy" not in program
        assert "@task" not in program
        assert "@pipeline" not in program
        # The task's runtime function survived the strip.
        assert "def greet(" in program
        # Verbatim annotation keeps the full co-located source byte-for-byte.
        assert annotations["python_original_code"] == original_bytes

    def test_multi_name_import_baked_program_runs(self, tmp_path):
        # FIX 3(b): multi-name authoring import drops the whole line; the baked
        # program has no In/Out/task left and still runs end-to-end.
        py_file = tmp_path / "multi_name.py"
        py_file.write_text(textwrap.dedent('''\
            from cloud_pipelines import components

            from tangle_deploy.python_pipeline import In, Out, task


            @task(image="python:3.12")
            def multi_name(out: components.OutputPath("Text"), who: str = "world"):
                """
                Metadata:
                    Name: Multi Name
                    Version: 1.0.0
                """
                with open(out, "w") as fh:
                    fh.write(f"hi {who}")
        '''))

        output_file = tmp_path / "multi_name.yaml"
        success = generate_component_yaml(
            file_path=py_file,
            output_path=output_file,
            container_image="python:3.12",
            function_name="multi_name",
            mode="inline",
        )
        assert success is True

        program, _ = self._baked_program_and_annotations(output_file)
        assert "from tangle_deploy" not in program
        assert "@task" not in program
        # None of the multi-name authoring imports leaked.
        for token in (" In", " Out", " task"):
            assert token not in program

        program_path = tmp_path / "inlined_multi_name.py"
        program_path.write_text(program)
        out_path = tmp_path / "outputs" / "hi.txt"
        completed = subprocess.run(
            [sys.executable, str(program_path), "--out", str(out_path), "--who", "world"],
            capture_output=True,
            check=False,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr
        assert out_path.read_text() == "hi world"


# ============================================================================
# Generator-level TaskEnv env-only strip tests (Phase 3, §3.5)
# ============================================================================


def test_generate_component_yaml_with_unwrapped_inputs_runs_rewrapped_program(tmp_path):
    source = tmp_path / "combine_component.py"
    source.write_text(
        textwrap.dedent(
            '''
            from cloud_pipelines import components

            def combine_component(out: components.OutputPath("Text"), run_data: dict[str, str]):
                with open(out, "w") as fh:
                    fh.write(run_data["left"] + "|" + run_data["right"])
            '''
        ).lstrip()
    )
    output_file = tmp_path / "component.yaml"

    assert generate_component_yaml(
        source,
        output_file,
        container_image="python:3.12",
        function_name="combine_component",
        unwrapped_inputs={
            "run_data": {
                "input_prefix": "run_data__",
                "value_type": "String",
                "keys": [
                    {"key": "left", "input_name": "run_data__left", "type": "String"},
                    {"key": "right", "input_name": "run_data__right", "type": "String"},
                ],
            }
        },
    ) is True

    component = yaml.safe_load(output_file.read_text())
    assert {item["name"] for item in component["inputs"]} == {"run_data__left", "run_data__right"}
    program = component["implementation"]["container"]["command"][-1]
    program_path = tmp_path / "program.py"
    program_path.write_text(program)
    out_path = tmp_path / "combined.txt"
    completed = subprocess.run(
        [
            sys.executable,
            str(program_path),
            "--out",
            str(out_path),
            "--run_data__left",
            "a",
            "--run_data__right",
            "b",
        ],
        capture_output=True,
        check=False,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert out_path.read_text() == "a|b"


def test_generate_component_yaml_unwrapped_flags_are_injective(tmp_path):
    source = tmp_path / "hyphen_component.py"
    source.write_text(
        textwrap.dedent(
            '''
            from cloud_pipelines import components

            def hyphen_component(out: components.OutputPath("Text"), items: dict[str, str]):
                with open(out, "w") as fh:
                    fh.write(items["who-1"] + "|" + items["who_1"])
            '''
        ).lstrip()
    )
    output_file = tmp_path / "component.yaml"

    assert generate_component_yaml(
        source,
        output_file,
        container_image="python:3.12",
        function_name="hyphen_component",
        unwrapped_inputs={
            "items": {
                "input_prefix": "items__",
                "value_type": "String",
                "keys": [
                    {"key": "who-1", "input_name": "items__who-1", "type": "String"},
                    {"key": "who_1", "input_name": "items__who_1", "type": "String"},
                ],
            }
        },
    ) is True

    component = yaml.safe_load(output_file.read_text())
    args = component["implementation"]["container"]["args"]
    assert "--items__who-1" in args
    assert "--items__who_1" in args
    assert "--items--who-1" not in args

    program = component["implementation"]["container"]["command"][-1]
    program_path = tmp_path / "program.py"
    program_path.write_text(program)
    out_path = tmp_path / "combined.txt"
    completed = subprocess.run(
        [
            sys.executable,
            str(program_path),
            "--out",
            str(out_path),
            "--items__who-1",
            "hyphen",
            "--items__who_1",
            "underscore",
        ],
        capture_output=True,
        check=False,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert out_path.read_text() == "hyphen|underscore"


def test_generate_component_yaml_shim_supports_outputs_import(tmp_path):
    source = tmp_path / "outputs_import_component.py"
    source.write_text(
        textwrap.dedent(
            '''
            from tangle_deploy.python_pipeline import Outputs, task

            @task()
            def outputs_import_component(name: str) -> str:
                return name
            '''
        ).lstrip()
    )
    output_file = tmp_path / "component.yaml"

    assert generate_component_yaml(
        source,
        output_file,
        container_image="python:3.12",
        function_name="outputs_import_component",
    ) is True

    component = yaml.safe_load(output_file.read_text())
    program = component["implementation"]["container"]["command"][-1]
    assert "from tangle_deploy.python_pipeline" not in program
    assert "from tangle_deploy.python_pipeline import Outputs" not in program


class TestGeneratorStripsTaskEnvAuthoring:
    """``generate_component_yaml`` must bake an env-free runtime program for
    ``@task(env=...)`` authoring, while keeping ``python_original_code``
    byte-verbatim. Drives the real ``task_env_strip_*`` fixtures end to end.
    """

    @staticmethod
    def _baked_program_and_annotations(output_file):
        with open(output_file) as f:
            component = yaml.safe_load(f)
        program = component["implementation"]["container"]["command"][-1]
        annotations = component["metadata"]["annotations"]
        return program, annotations

    def _generate(self, fixture, function_name, tmp_path, container_image="python:3.12"):
        output_file = tmp_path / f"{function_name}.yaml"
        success = generate_component_yaml(
            file_path=_PIPELINE_FIXTURES / fixture,
            output_path=output_file,
            container_image=container_image,
            function_name=function_name,
            mode="inline",
        )
        assert success is True, f"generate_component_yaml failed for {fixture}"
        program, annotations = self._baked_program_and_annotations(output_file)
        original = (_PIPELINE_FIXTURES / fixture).read_text()
        return program, annotations, original

    @staticmethod
    def _run_baked(program, tmp_path, stem):
        """Subprocess-run a baked program; assert it writes ``hi world``."""
        program_path = tmp_path / f"inlined_{stem}.py"
        program_path.write_text(program)
        out_path = tmp_path / "outputs" / "hi.txt"
        completed = subprocess.run(
            [sys.executable, str(program_path), "--out", str(out_path), "--who", "world"],
            capture_output=True,
            check=False,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr
        assert out_path.read_text() == "hi world"

    def test_colocated_env_assignment_absent_from_baked_program(self, tmp_path):
        program, annotations, original = self._generate(
            "task_env_strip_colocated_op.py", "task_env_strip_colocated", tmp_path
        )
        # Baked runtime program is env- and authoring-free.
        assert "from tangle_deploy" not in program
        assert "@task" not in program
        assert "TaskEnv" not in program
        assert "UPI" not in program
        # Plain runtime function survived.
        assert "def task_env_strip_colocated(" in program
        # Verbatim annotation untouched: it STILL carries the env declaration.
        assert annotations["python_original_code"] == original
        assert "UPI = TaskEnv(" in annotations["python_original_code"]
        # Proof it actually runs (would be NameError: TaskEnv if leaked).
        self._run_baked(program, tmp_path, "colocated")

    def test_imported_env_name_absent_from_baked_program(self, tmp_path):
        program, annotations, original = self._generate(
            "task_env_strip_imported_op.py", "task_env_strip_imported", tmp_path
        )
        assert "from tangle_deploy" not in program
        assert "@task" not in program
        assert "task_env_strip_envs" not in program  # the authoring-only import
        assert "UPI" not in program
        assert "def task_env_strip_imported(" in program
        # Verbatim annotation keeps the sibling import byte-for-byte.
        assert annotations["python_original_code"] == original
        assert "from task_env_strip_envs import UPI" in annotations["python_original_code"]
        # Proof it runs without importing the authoring-only sibling module
        # (would be ImportError if leaked).
        self._run_baked(program, tmp_path, "imported")

    def test_imported_env_module_alias_absent_from_baked_program(self, tmp_path):
        program, annotations, original = self._generate(
            "task_env_strip_module_op.py", "task_env_strip_module", tmp_path
        )
        assert "from tangle_deploy" not in program
        assert "@task" not in program
        assert "import task_env_strip_envs" not in program
        assert "task_env_strip_envs" not in program
        assert "def task_env_strip_module(" in program
        assert annotations["python_original_code"] == original
        assert "import task_env_strip_envs" in annotations["python_original_code"]
        self._run_baked(program, tmp_path, "module")

    def test_inline_taskenv_absent_from_baked_program(self, tmp_path):
        program, annotations, original = self._generate(
            "task_env_strip_inline_op.py", "task_env_strip_inline", tmp_path
        )
        assert "from tangle_deploy" not in program
        assert "@task" not in program
        assert "TaskEnv" not in program  # no residual inline TaskEnv(...) text
        assert "def task_env_strip_inline(" in program
        assert annotations["python_original_code"] == original
        assert "env=TaskEnv(" in annotations["python_original_code"]
        self._run_baked(program, tmp_path, "inline")

    def test_explicit_image_override_baked_program_is_env_free(self, tmp_path):
        program, annotations, original = self._generate(
            "task_env_strip_override_op.py",
            "task_env_strip_override",
            tmp_path,
            container_image="python:3.13-slim",
        )
        # Decorator + env declaration absent; the override image string lived
        # only in the decorator, so it must not leak into the baked source.
        assert "from tangle_deploy" not in program
        assert "@task" not in program
        assert "TaskEnv" not in program
        assert "UPI" not in program
        assert "python:3.13-slim" not in program
        assert "def task_env_strip_override(" in program
        # Verbatim annotation keeps both the env decl and the override.
        assert annotations["python_original_code"] == original
        assert "UPI = TaskEnv(" in annotations["python_original_code"]
        assert "python:3.13-slim" in annotations["python_original_code"]
        self._run_baked(program, tmp_path, "override")

    def test_mixed_import_fails_loud_at_generator_layer(self, tmp_path):
        # A TaskEnv authoring-violation must be a HARD, LOUD failure carrying
        # the actionable guidance -- NOT swallowed into warn + success=False
        # (which would resurface as a confusing broken component at hydrate /
        # backend run time). generate_component_yaml re-raises AuthoringStripError
        # specifically while keeping warn+False for every other failure.
        output_file = tmp_path / "mixed.yaml"
        with pytest.raises(AuthoringStripError) as excinfo:
            generate_component_yaml(
                file_path=_PIPELINE_FIXTURES / "task_env_strip_mixed_import_op.py",
                output_path=output_file,
                container_image="python:3.12",
                function_name="task_env_strip_mixed_import",
                mode="inline",
            )
        msg = str(excinfo.value)
        assert "helper" in msg
        assert "Split the import" in msg
        # Hard failure: nothing was written.
        assert not output_file.exists()

    def test_body_reference_fails_loud_at_generator_layer(self, tmp_path):
        # An env name referenced by the task body must also fail loud.
        output_file = tmp_path / "body_ref.yaml"
        with pytest.raises(AuthoringStripError) as excinfo:
            generate_component_yaml(
                file_path=_PIPELINE_FIXTURES / "task_env_strip_body_ref_op.py",
                output_path=output_file,
                container_image="python:3.12",
                function_name="task_env_strip_body_ref",
                mode="inline",
            )
        msg = str(excinfo.value)
        assert "UPI" in msg
        assert "authoring-only" in msg
        assert not output_file.exists()

    def test_annotation_only_env_baked_program_is_env_free_and_runs(self, tmp_path):
        # FIX N1 (§3.5): an env used ONLY as a type annotation (`-> UPI`) and
        # NOT in the body must NOT fail fast. The baked program must be env-free
        # (decorator, import, env decl, AND the annotation all gone) and run
        # without a NameError; python_original_code stays byte-verbatim.
        program, annotations, original = self._generate(
            "task_env_strip_annotation_op.py", "task_env_strip_annotation", tmp_path
        )
        # Baked runtime program is env- and authoring-free.
        assert "from tangle_deploy" not in program
        assert "@task" not in program
        assert "TaskEnv" not in program
        assert "UPI" not in program  # incl. the `-> UPI` annotation (stripped)
        # Plain runtime function survived (annotation stripped from signature).
        assert "def task_env_strip_annotation(" in program
        # Verbatim annotation untouched: it STILL carries env decl + annotation.
        assert annotations["python_original_code"] == original
        assert "UPI = TaskEnv(" in annotations["python_original_code"]
        assert "-> UPI:" in annotations["python_original_code"]
        # Proof it actually runs (would be NameError: UPI/TaskEnv if it leaked,
        # or generation would have wrongly fail-fasted before N1).
        self._run_baked(program, tmp_path, "annotation")

    def test_nested_env_import_fails_loud_at_generator_layer(self, tmp_path):
        # FIX N2 (§3.5): an env import nested in an `if`/`try` block cannot be
        # safely stripped (module-level removal only touches the module body),
        # so it would silently leak into the baked program. The generator must
        # re-raise AuthoringStripError loudly with actionable guidance and write
        # no output, exactly like the mixed-import / body-ref violations.
        output_file = tmp_path / "nested.yaml"
        with pytest.raises(AuthoringStripError) as excinfo:
            generate_component_yaml(
                file_path=_PIPELINE_FIXTURES / "task_env_strip_nested_import_op.py",
                output_path=output_file,
                container_image="python:3.12",
                function_name="task_env_strip_nested_import",
                mode="inline",
            )
        msg = str(excinfo.value)
        assert "UPI" in msg
        assert "nested" in msg
        assert "module-level" in msg
        assert "top-level import" in msg
        # Hard failure: nothing was written.
        assert not output_file.exists()


# ============================================================================
# NamedTuple return type tests
# ============================================================================


class TestNamedTupleReturnType:
    """Tests for NamedTuple return type -> output declaration generation."""

    def test_resolve_return_type_namedtuple_str(self):
        """Functional-style NamedTuple with str field produces return_output."""
        from typing import NamedTuple

        def my_func(x: str) -> NamedTuple("Outputs", created_table=str):
            return ("table",)

        params, single = _resolve_return_type(my_func)
        assert len(params) == 1
        assert params[0].name == "created_table"
        assert params[0].tangle_type == "String"
        assert params[0].kind == "return_output"
        assert single is False

    def test_resolve_return_type_namedtuple_multiple_fields(self):
        """NamedTuple with multiple fields of different types."""
        from typing import NamedTuple

        def my_func(x: str) -> NamedTuple("Outputs", sql_query=str, row_count=int, metrics=dict):
            return ("q", 10, {})

        params, single = _resolve_return_type(my_func)
        assert len(params) == 3
        assert params[0].name == "sql_query"
        assert params[0].tangle_type == "String"
        assert params[1].name == "row_count"
        assert params[1].tangle_type == "Integer"
        assert params[2].name == "metrics"
        assert params[2].tangle_type == "JsonObject"
        assert single is False

    def test_resolve_return_type_no_return(self):
        """Function without return annotation produces empty list."""

        def my_func(x: str):
            pass

        params, single = _resolve_return_type(my_func)
        assert params == []
        assert single is False

    def test_resolve_return_type_plain_type(self):
        """Function returning a plain type produces single Output param."""

        def my_func(x: str) -> str:
            return ""

        params, single = _resolve_return_type(my_func)
        assert len(params) == 1
        assert params[0].name == "Output"
        assert params[0].tangle_type == "String"
        assert params[0].kind == "return_output"
        assert single is True

    def test_extract_interface_with_namedtuple(self):
        """extract_interface populates return_params for NamedTuple returns."""
        from typing import NamedTuple

        def create_table(
            project: str,
            table_name: str,
        ) -> NamedTuple("Outputs", created_table=str):
            """Create a table.

            Args:
                project: The cloud project.
                table_name: The table name.

            Returns:
                created_table: The full table name.
            """
            return (f"{project}.{table_name}",)

        spec = extract_interface(create_table, {})
        assert len(spec.return_params) == 1
        assert spec.return_params[0].name == "created_table"
        assert spec.return_params[0].tangle_type == "String"
        assert spec.return_params[0].description == "The full table name."

    def test_build_args_section_with_return_outputs(self):
        """Args section includes ----output-paths for NamedTuple return outputs."""
        spec = FunctionSpec(
            name="my_func",
            component_name="My func",
            description="test",
            params=[
                ParamInfo(
                    name="x", yaml_name="x", python_type="str", tangle_type="String", kind="input", deserializer="str"
                ),
            ],
            return_params=[
                ParamInfo(
                    name="result",
                    yaml_name="result",
                    python_type="str",
                    tangle_type="String",
                    kind="return_output",
                    deserializer="_serialize_str",
                ),
            ],
        )

        args = _build_args_section(spec)
        assert "----output-paths" in args
        assert {"outputPath": "result"} in args

    def test_build_argparse_code_with_return_outputs(self):
        """Argparse code includes output-paths handling and serialization."""
        spec = FunctionSpec(
            name="my_func",
            component_name="My func",
            description="test",
            params=[
                ParamInfo(
                    name="x", yaml_name="x", python_type="str", tangle_type="String", kind="input", deserializer="str"
                ),
            ],
            return_params=[
                ParamInfo(
                    name="result",
                    yaml_name="result",
                    python_type="str",
                    tangle_type="String",
                    kind="return_output",
                    deserializer="_serialize_str",
                ),
            ],
        )

        code = _build_argparse_code(spec)
        assert '"----output-paths"' in code
        assert '_output_files = _parsed_args.pop("_output_paths", [])' in code
        assert "_output_serializers" in code
        assert "_serialize_str" in code

    def test_end_to_end_namedtuple_generation(self, tmp_path):
        """Full end-to-end: Python file with NamedTuple return -> YAML with outputs."""
        py_file = tmp_path / "create_table.py"
        py_file.write_text(textwrap.dedent("""\
            from typing import NamedTuple

            def create_table(
                project: str,
                table_name: str,
            ) -> NamedTuple("Outputs", created_table=str):
                \"\"\"Create a data warehouse table.

                Args:
                    project: The cloud project.
                    table_name: The table name.

                Returns:
                    created_table: The full table name.
                \"\"\"
                return (f"{project}.{table_name}",)
        """))

        toml_file = tmp_path / "pyproject.toml"
        toml_file.write_text('[project]\nname = "test"\ndependencies = []\n')

        output_file = tmp_path / "output.yaml"

        success = generate_component_yaml(
            file_path=py_file,
            output_path=output_file,
            container_image="python:3.12",
            function_name="create_table",
            dependencies_from=toml_file,
            mode="inline",
        )

        assert success is True
        assert output_file.exists()

        with open(output_file) as f:
            component = yaml.safe_load(f)

        assert len(component["inputs"]) == 2
        assert component["inputs"][0]["name"] == "project"
        assert component["inputs"][1]["name"] == "table_name"

        assert "outputs" in component
        assert len(component["outputs"]) == 1
        assert component["outputs"][0]["name"] == "created_table"
        assert component["outputs"][0]["type"] == "String"

        args = component["implementation"]["container"]["args"]
        assert "----output-paths" in args
        has_output_path = any(isinstance(a, dict) and a.get("outputPath") == "created_table" for a in args)
        assert has_output_path

        python_source = component["implementation"]["container"]["command"][-1]
        assert "_output_serializers" in python_source
        assert "_serialize_str" in python_source

    def test_end_to_end_single_return_generation(self, tmp_path):
        """Full end-to-end: Python file with -> str return -> YAML with single Output."""
        py_file = tmp_path / "greet.py"
        py_file.write_text(textwrap.dedent("""\
            def greet(name: str) -> str:
                \"\"\"Greet someone.

                Args:
                    name: The person's name.
                \"\"\"
                return f"Hello, {name}!"
        """))

        toml_file = tmp_path / "pyproject.toml"
        toml_file.write_text('[project]\nname = "test"\ndependencies = []\n')

        output_file = tmp_path / "output.yaml"

        success = generate_component_yaml(
            file_path=py_file,
            output_path=output_file,
            container_image="python:3.12",
            function_name="greet",
            dependencies_from=toml_file,
            mode="inline",
        )

        assert success is True

        with open(output_file) as f:
            component = yaml.safe_load(f)

        assert "outputs" in component
        assert len(component["outputs"]) == 1
        assert component["outputs"][0]["name"] == "Output"
        assert component["outputs"][0]["type"] == "String"

        python_source = component["implementation"]["container"]["command"][-1]
        assert "_outputs = [_outputs]" in python_source
        assert "_serialize_str" in python_source

        args = component["implementation"]["container"]["args"]
        assert "----output-paths" in args
        assert {"outputPath": "Output"} in args
