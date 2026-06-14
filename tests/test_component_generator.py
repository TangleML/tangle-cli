"""Tests for Python-function component generation helpers and CLI wiring."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tangle_cli import cli
from tangle_cli.component_from_func import AuthoringStripError, generate_component_yaml
from tangle_cli.component_generator import (
    DEFAULT_CONTAINER_IMAGE,
    determine_output_path,
    find_dependencies_file,
    regenerate_yaml,
)


DUMMY_PYTHON_COMPONENT = '''#!/usr/bin/env python3
"""Module docstring."""

def test_component(input_data: str, threshold: float = 0.5) -> dict:
    """
    Processes and validates input data.

    Args:
        input_data: The data to process
        threshold: Processing threshold

    Returns:
        Processing results as a dictionary

    Metadata:
        Name: Data Processor
        Version: 2.1.0
        updated_at: 2024-11-23T10:00:00Z
    """
    return {
        "processed": input_data.upper(),
        "threshold_met": len(input_data) > threshold
    }

if __name__ == "__main__":
    print(test_component("test", 0.3))
'''


SNAPSHOTS_DIR = Path(__file__).parent / "snapshots" / "component_generator"


def run_app(app, args):
    with pytest.raises(SystemExit) as exc_info:
        app(args)
    assert exc_info.value.code == 0


def _assert_yaml_matches(actual_path: Path, expected_path: Path) -> None:
    assert actual_path.exists(), f"Generated file not found: {actual_path}"
    with actual_path.open(encoding="utf-8") as f:
        actual = yaml.safe_load(f)
    with expected_path.open(encoding="utf-8") as f:
        expected = yaml.safe_load(f)
    assert actual == expected


class TestDependenciesDiscovery:
    def test_find_component_specific_toml(self, tmp_path: Path):
        py_file = tmp_path / "my_component.py"
        py_file.write_text("def main(): pass", encoding="utf-8")
        toml_file = tmp_path / "my-component.toml"
        toml_file.write_text("[project]\nname = 'test'", encoding="utf-8")

        assert find_dependencies_file(py_file) == toml_file

    def test_find_pyproject_in_parent(self, tmp_path: Path):
        subdir = tmp_path / "components"
        subdir.mkdir()
        py_file = subdir / "component.py"
        py_file.write_text("def main(): pass", encoding="utf-8")
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("[project]\nname = 'test'", encoding="utf-8")

        assert find_dependencies_file(py_file) == pyproject

    def test_no_dependencies_file(self, tmp_path: Path):
        py_file = tmp_path / "component.py"
        py_file.write_text("def main(): pass", encoding="utf-8")

        assert find_dependencies_file(py_file) is None


class TestOutputPathDetermination:
    def test_output_same_directory(self):
        assert determine_output_path(Path("/project/components/my_component.py")) == Path(
            "/project/components/my-component.yaml"
        )

    def test_output_sources_directory_not_special_cased(self):
        assert determine_output_path(Path("/project/components/sources/my_component.py")) == Path(
            "/project/components/sources/my-component.yaml"
        )

    def test_output_custom_file(self):
        output_path = Path("/output/custom.yaml")
        assert determine_output_path(Path("/project/component.py"), output_path) == output_path

    def test_output_custom_directory_no_extension(self):
        assert determine_output_path(Path("/project/component.py"), Path("/output/dir")) == Path(
            "/output/dir/component.yaml"
        )

    def test_output_custom_directory_explicit(self):
        assert determine_output_path(
            Path("/project/component.py"), Path("/output/dir"), output_is_dir=True
        ) == Path("/output/dir/component.yaml")


@pytest.mark.parametrize("use_cli", [False, True])
def test_complete_generation_flow(monkeypatch, tmp_path: Path, use_cli: bool):
    monkeypatch.setattr("tangle_cli.utils._fill_from_ci_env", lambda info: None)
    py_file = tmp_path / "test_component.py"
    py_file.write_text(DUMMY_PYTHON_COMPONENT, encoding="utf-8")
    yaml_file = tmp_path / "test-component.yaml"

    if use_cli:
        app = cli.build_app()
        run_app(
            app,
            [
                "sdk",
                "components",
                "generate",
                "from-python",
                str(py_file),
                "--image",
                "test-image:latest",
            ],
        )
    else:
        assert regenerate_yaml(py_file, image="test-image:latest") is True

    _assert_yaml_matches(yaml_file, SNAPSHOTS_DIR / "complete_generation.expected.yaml")


def test_regenerate_yaml_default_image_is_pinned(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("tangle_cli.utils._fill_from_ci_env", lambda info: None)
    py_file = tmp_path / "test_component.py"
    py_file.write_text(DUMMY_PYTHON_COMPONENT, encoding="utf-8")

    assert regenerate_yaml(py_file) is True

    generated = yaml.safe_load((tmp_path / "test-component.yaml").read_text(encoding="utf-8"))
    assert generated["implementation"]["container"]["image"] == DEFAULT_CONTAINER_IMAGE
    assert "@sha256:" in generated["implementation"]["container"]["image"]


def test_generate_component_yaml_default_emits_generation_annotations_and_oss_paths(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setattr("tangle_cli.utils._fill_from_ci_env", lambda info: None)
    src_dir = tmp_path / "src"
    out_dir = tmp_path / "generated"
    src_dir.mkdir()
    out_dir.mkdir()
    py_file = src_dir / "component.py"
    py_file.write_text(DUMMY_PYTHON_COMPONENT, encoding="utf-8")
    yaml_file = out_dir / "component.yaml"

    assert generate_component_yaml(py_file, yaml_file, "python:3.12", function_name="test_component") is True

    generated = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
    annotations = generated["metadata"]["annotations"]
    assert annotations["python_original_code_path"] == "src/component.py"
    assert annotations["component_yaml_path"] == "generated/component.yaml"
    assert annotations["tangle_cli_generation_function_name"] == "test_component"
    assert annotations["tangle_cli_generation_mode"] == "inline"


def test_generate_component_yaml_can_disable_generation_annotations_and_use_td_legacy_paths(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setattr("tangle_cli.utils._fill_from_ci_env", lambda info: None)
    src_dir = tmp_path / "src"
    out_dir = tmp_path / "generated"
    src_dir.mkdir()
    out_dir.mkdir()
    py_file = src_dir / "component.py"
    py_file.write_text(DUMMY_PYTHON_COMPONENT, encoding="utf-8")
    yaml_file = out_dir / "component.yaml"

    assert generate_component_yaml(
        py_file,
        yaml_file,
        "python:3.12",
        function_name="test_component",
        emit_generation_annotations=False,
        path_annotation_mode="td_legacy",
    ) is True

    generated = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
    annotations = generated["metadata"]["annotations"]
    assert annotations["python_original_code_path"] == "component.py"
    assert annotations["component_yaml_path"] == "component.yaml"
    assert "tangle_cli_generation_function_name" not in annotations
    assert "tangle_cli_generation_mode" not in annotations
    assert "tangle_cli_generation_dependencies_from" not in annotations
    assert "tangle_cli_generation_resolve_root" not in annotations


def test_from_python_function_alias_and_config(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("tangle_cli.utils._fill_from_ci_env", lambda info: None)
    py_file = tmp_path / "my_component.py"
    py_file.write_text('''
def my_component(name: str) -> str:
    """Echo a name.

    Metadata:
        version: 1.0
    """
    return name
''', encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text(
        f"python_file: {py_file}\n"
        "image: python:3.12\n"
        "name: Configured Component\n",
        encoding="utf-8",
    )

    app = cli.build_app()
    run_app(app, ["sdk", "components", "generate", "from-python-function", "--config", str(config)])

    generated = yaml.safe_load((tmp_path / "my-component.yaml").read_text(encoding="utf-8"))
    assert generated["name"] == "Configured Component"
    assert generated["metadata"]["annotations"]["version"] == "1.0"


def test_regenerate_yaml_reraises_authoring_strip_errors(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("tangle_cli.utils._fill_from_ci_env", lambda info: None)
    py_file = tmp_path / "bad_authoring.py"
    py_file.write_text(
        '''from tangle_deploy.python_pipeline import TaskEnv, task

UPI = TaskEnv(image="python:3.12")

@task(env=UPI)
def bad_authoring() -> str:
    return UPI
''',
        encoding="utf-8",
    )

    with pytest.raises(AuthoringStripError):
        regenerate_yaml(py_file, image="python:3.12", function_name="bad_authoring")

    assert not (tmp_path / "bad-authoring.yaml").exists()


def test_bundle_mode_with_local_imports(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("tangle_cli.utils._fill_from_ci_env", lambda info: None)
    helpers_dir = tmp_path / "helpers"
    helpers_dir.mkdir()
    (helpers_dir / "__init__.py").write_text("", encoding="utf-8")
    (helpers_dir / "utils.py").write_text("def clean(text):\n    return text.strip().lower()\n", encoding="utf-8")
    py_file = tmp_path / "my_component.py"
    py_file.write_text('''from helpers.utils import clean

def my_component(name: str) -> str:
    """Clean a name.

    Metadata:
        version: 1.0
    """
    return clean(name)
''', encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\ndependencies = []\n', encoding="utf-8")

    assert regenerate_yaml(py_file, image="python:3.12", function_name="my_component", mode="bundle") is True

    generated = yaml.safe_load((tmp_path / "my-component.yaml").read_text(encoding="utf-8"))
    program = generated["implementation"]["container"]["command"][-1]
    assert generated["name"] == "My component"
    assert "_EMBEDDED_MODULES" in program
    assert "helpers.utils" in program


def test_bump_version_cli_uses_config(tmp_path: Path):
    yaml_file = tmp_path / "component.yaml"
    yaml_file.write_text(
        'name: demo\nmetadata:\n  annotations:\n    version: "1.2"\n',
        encoding="utf-8",
    )
    config = tmp_path / "bump.yaml"
    config.write_text(f"yaml_file: {yaml_file}\nset_version: '2.0'\n", encoding="utf-8")

    app = cli.build_app()
    run_app(app, ["sdk", "components", "bump-version", "--config", str(config)])

    updated = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
    assert updated["metadata"]["annotations"]["version"] == "2.0"
