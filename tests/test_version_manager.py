#!/usr/bin/env python3
"""Tests for the version manager."""

import tempfile
from pathlib import Path

import yaml

from tangle_cli.component_from_func import generate_component_yaml
from tangle_cli.version_manager import bump_version


def test_bump_plain_yaml_no_prior_version():
    """Test bump_version on a YAML file with no version field."""
    yaml_content = '''name: test-component
metadata:
  annotations:
    description: A test component
'''
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        f.write(yaml_content)
        f.flush()
        temp_path = Path(f.name)

    try:
        result = bump_version(temp_path)

        assert result["status"] == "success"

        with open(temp_path) as f:
            data = yaml.safe_load(f)
        assert data["metadata"]["annotations"]["version"] == "0.1"
    finally:
        temp_path.unlink()


def test_bump_plain_yaml_major_minor():
    """Test bump_version on a plain YAML file with major.minor version."""
    yaml_content = '''name: test-component
version: "2.3"
metadata:
  annotations:
    description: A test component
spec:
  inputs: []
'''
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        f.write(yaml_content)
        f.flush()
        temp_path = Path(f.name)

    try:
        result = bump_version(temp_path)

        assert result["status"] == "success"

        with open(temp_path) as f:
            data = yaml.safe_load(f)
        # Version migrated to metadata.annotations, top-level removed
        assert "version" not in data
        assert data["metadata"]["annotations"]["version"] == "2.4"
    finally:
        temp_path.unlink()


def test_bump_plain_yaml_major_minor_patch():
    """Test bump_version on a plain YAML file with major.minor.patch version."""
    yaml_content = '''name: test-component
version: "1.2.3"
metadata:
  annotations:
    description: A test component
'''
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        f.write(yaml_content)
        f.flush()
        temp_path = Path(f.name)

    try:
        result = bump_version(temp_path)

        assert result["status"] == "success"

        with open(temp_path) as f:
            data = yaml.safe_load(f)
        # Version migrated to metadata.annotations, top-level removed
        assert "version" not in data
        assert data["metadata"]["annotations"]["version"] == "1.2.4"
    finally:
        temp_path.unlink()


def test_bump_plain_yaml_with_timestamp():
    """Test bump_version with timestamp update."""
    yaml_content = '''name: test-component
version: "1.5"
updated_at: "2024-01-01T00:00:00Z"
'''
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        f.write(yaml_content)
        f.flush()
        temp_path = Path(f.name)

    try:
        result = bump_version(temp_path, update_timestamp=True)

        assert result["status"] == "success"

        with open(temp_path) as f:
            data = yaml.safe_load(f)
        # Version and timestamp migrated to metadata.annotations, top-level removed
        assert "version" not in data
        assert "updated_at" not in data
        assert data["metadata"]["annotations"]["version"] == "1.6"
        assert data["metadata"]["annotations"]["updated_at"] != "2024-01-01T00:00:00Z"
        assert "T" in data["metadata"]["annotations"]["updated_at"]  # ISO format
    finally:
        temp_path.unlink()


def test_bump_yaml_with_python_source():
    """Test bump_version when YAML has a Python source file."""
    python_content = '''"""Test component."""


def test_component(input_value: str) -> str:
    """A test component function.

    Metadata:
        version: 1.5
        updated_at: 2024-01-01T00:00:00Z

    Args:
        input_value: The input value.

    Returns:
        The output value.
    """
    return input_value
'''
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        sources_dir = temp_path / "sources"
        sources_dir.mkdir()

        # Create Python source file
        python_file = sources_dir / "test_component.py"
        python_file.write_text(python_content)

        # Create YAML file pointing to Python source
        yaml_content = f'''name: test-component
version: "1.5"
updated_at: "2024-01-01T00:00:00Z"
metadata:
  annotations:
    python_original_code_path: {python_file.name}
implementation:
  container:
    image: us-docker.pkg.dev/test/image:latest
'''
        yaml_file = temp_path / "test-component.yaml"
        yaml_file.write_text(yaml_content)

        # Bump version with timestamp
        result = bump_version(yaml_file, update_timestamp=True)

        assert result["status"] == "success"

        # Verify Python file was updated
        updated_python = python_file.read_text()
        assert "version: 1.6" in updated_python
        assert "2024-01-01T00:00:00Z" not in updated_python

        # Verify YAML was regenerated with new version
        with open(yaml_file) as f:
            data = yaml.safe_load(f)
        assert data["metadata"]["annotations"]["version"] == "1.6"


def test_bump_yaml_with_python_source_in_separate_output_dir(tmp_path: Path):
    """Resolve source paths relative to component_yaml_path/common root."""

    src_dir = tmp_path / "src"
    out_dir = tmp_path / "generated"
    src_dir.mkdir()
    out_dir.mkdir()
    python_file = src_dir / "test_component.py"
    python_file.write_text('''"""Test component."""


def test_component(input_value: str) -> str:
    """A test component function.

    Metadata:
        version: 1.5

    Args:
        input_value: The input value.

    Returns:
        The output value.
    """
    return input_value
''')
    yaml_file = out_dir / "test-component.yaml"
    yaml_file.write_text('''name: test-component
metadata:
  annotations:
    version: '1.5'
    python_original_code_path: src/test_component.py
    component_yaml_path: generated/test-component.yaml
implementation:
  container:
    image: python:3.12
''')

    result = bump_version(yaml_file)

    assert result["status"] == "success"
    assert result["old_version"] == "1.5"
    assert result["new_version"] == "1.6"
    assert "version: 1.6" in python_file.read_text()
    data = yaml.safe_load(yaml_file.read_text())
    assert data["metadata"]["annotations"]["version"] == "1.6"
    assert data["metadata"]["annotations"]["python_original_code_path"] == "src/test_component.py"
    assert data["metadata"]["annotations"]["component_yaml_path"] == "generated/test-component.yaml"


def test_bump_yaml_with_missing_python_source_fails_without_yaml_fallback(tmp_path: Path):
    """Do not rewrite embedded python_original_code when annotated source is missing."""

    yaml_file = tmp_path / "test-component.yaml"
    original = '''name: test-component
metadata:
  annotations:
    version: '1.5'
    python_original_code_path: missing/test_component.py
    python_original_code: |
      def test_component(input_value: str) -> str:
          """A test component function.

          Metadata:
              version: 9.9
          """
          return input_value
implementation:
  container:
    image: python:3.12
'''
    yaml_file.write_text(original)

    result = bump_version(yaml_file)

    assert result["status"] == "failed"
    assert "Python source not found" in str(result["error"])
    assert yaml_file.read_text() == original


def test_bump_generated_yaml_preserves_selected_function(tmp_path: Path):
    python_file = tmp_path / "multi.py"
    yaml_file = tmp_path / "out.yaml"
    python_file.write_text('''def helper(value: str) -> str:
    """Helper function.

    Metadata:
        name: Helper
        version: 8.0

    Args:
        value: Value.

    Returns:
        Value.
    """
    return value


def target(value: str) -> str:
    """Target function.

    Metadata:
        name: Target
        version: 1.0

    Args:
        value: Value.

    Returns:
        Value.
    """
    return helper(value)
''')
    assert generate_component_yaml(
        python_file,
        yaml_file,
        container_image="python:3.12",
        function_name="target",
    )

    result = bump_version(yaml_file)

    assert result["status"] == "success"
    assert result["old_version"] == "1.0"
    assert result["new_version"] == "1.1"
    updated_source = python_file.read_text()
    assert "name: Helper\n        version: 8.0" in updated_source
    assert "def target" in updated_source
    assert "version: 1.1" in updated_source
    data = yaml.safe_load(yaml_file.read_text())
    assert data["name"] == "Target"
    assert data["metadata"]["annotations"]["version"] == "1.1"
    assert data["metadata"]["annotations"]["tangle_cli_generation_function_name"] == "target"


def test_bump_generated_yaml_fails_when_persisted_function_is_missing(tmp_path: Path):
    python_file = tmp_path / "multi.py"
    yaml_file = tmp_path / "out.yaml"
    initial_source = '''def helper(value: str) -> str:
    """Helper function.

    Metadata:
        name: Helper
        version: 8.0

    Args:
        value: Value.

    Returns:
        Value.
    """
    return value


def target(value: str) -> str:
    """Target function.

    Metadata:
        name: Target
        version: 1.0

    Args:
        value: Value.

    Returns:
        Value.
    """
    return helper(value)
'''
    python_file.write_text(initial_source)
    assert generate_component_yaml(
        python_file,
        yaml_file,
        container_image="python:3.12",
        function_name="target",
    )
    generated_yaml = yaml_file.read_text()

    source_without_target = '''def helper(value: str) -> str:
    """Helper function.

    Metadata:
        name: Helper
        version: 8.0

    Args:
        value: Value.

    Returns:
        Value.
    """
    return value
'''
    python_file.write_text(source_without_target)

    result = bump_version(yaml_file)

    assert result["status"] == "failed"
    assert python_file.read_text() == source_without_target
    assert yaml_file.read_text() == generated_yaml


def test_bump_generated_yaml_preserves_custom_name(tmp_path: Path):
    python_file = tmp_path / "component.py"
    yaml_file = tmp_path / "component.yaml"
    python_file.write_text('''def component(value: str) -> str:
    """Component function.

    Metadata:
        name: Inferred Name
        version: 1.0

    Args:
        value: Value.

    Returns:
        Value.
    """
    return value
''')
    assert generate_component_yaml(
        python_file,
        yaml_file,
        container_image="python:3.12",
        custom_name="Custom Name",
    )

    result = bump_version(yaml_file)

    assert result["status"] == "success"
    assert result["new_version"] == "1.1"
    data = yaml.safe_load(yaml_file.read_text())
    assert data["name"] == "Custom Name"
    assert data["metadata"]["annotations"]["version"] == "1.1"


def test_bump_generated_yaml_preserves_bundle_mode(tmp_path: Path):
    helpers_dir = tmp_path / "helpers"
    helpers_dir.mkdir()
    (helpers_dir / "__init__.py").write_text("", encoding="utf-8")
    (helpers_dir / "utils.py").write_text("def clean(text):\n    return text.strip().lower()\n", encoding="utf-8")
    python_file = tmp_path / "component.py"
    yaml_file = tmp_path / "component.yaml"
    python_file.write_text('''from helpers.utils import clean


def component(value: str) -> str:
    """Component function.

    Metadata:
        version: 1.0

    Args:
        value: Value.

    Returns:
        Value.
    """
    return clean(value)
''')
    assert generate_component_yaml(
        python_file,
        yaml_file,
        container_image="python:3.12",
        function_name="component",
        mode="bundle",
    )

    result = bump_version(yaml_file)

    assert result["status"] == "success"
    assert result["new_version"] == "1.1"
    data = yaml.safe_load(yaml_file.read_text())
    annotations = data["metadata"]["annotations"]
    command = data["implementation"]["container"]["command"][-1]
    assert annotations["tangle_cli_generation_mode"] == "bundle"
    assert "helpers.utils" in annotations["bundled_modules"]
    assert "_EMBEDDED_MODULES" in command
    assert "helpers.utils" in command


def test_bump_yaml_with_python_source_no_prior_version():
    """Test bump_version when YAML has Python source with no prior version."""
    python_content = '''"""Test component."""


def test_component(input_value: str) -> str:
    """A test component function.

    Metadata:

    Args:
        input_value: The input value.

    Returns:
        The output value.
    """
    return input_value
'''
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        sources_dir = temp_path / "sources"
        sources_dir.mkdir()

        # Create Python source file
        python_file = sources_dir / "test_component.py"
        python_file.write_text(python_content)

        # Create YAML file pointing to Python source (no version)
        yaml_content = f'''name: test-component
metadata:
  annotations:
    python_original_code_path: {python_file.name}
implementation:
  container:
    image: us-docker.pkg.dev/test/image:latest
'''
        yaml_file = temp_path / "test-component.yaml"
        yaml_file.write_text(yaml_content)

        # Bump version
        result = bump_version(yaml_file)

        assert result["status"] == "success"

        # Verify Python file was updated with initial version
        updated_python = python_file.read_text()
        assert "version: 0.1" in updated_python

        # Verify YAML was regenerated with new version
        with open(yaml_file) as f:
            data = yaml.safe_load(f)
        assert data["metadata"]["annotations"]["version"] == "0.1"
