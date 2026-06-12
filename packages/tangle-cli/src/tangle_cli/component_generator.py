"""Generate Tangle component YAML files from local Python functions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONTAINER_IMAGE = "python:3.12"


def find_dependencies_file(python_file: Path) -> Path | None:
    """Find a dependency file for a Python component source file.

    Looks for a component-specific TOML file next to the Python file, then a
    ``pyproject.toml`` in the file's directory or up to three parent directories.
    """

    file_dir = python_file.parent
    file_base = python_file.stem
    toml_variations = [
        file_dir / f"{file_base.replace('_', '-')}.toml",
        file_dir / f"{file_base}.toml",
    ]
    for toml_file in toml_variations:
        if toml_file.exists():
            return toml_file

    search_dirs = [
        file_dir,
        file_dir.parent,
        file_dir.parent.parent,
        file_dir.parent.parent.parent,
    ]
    for search_dir in search_dirs:
        pyproject = search_dir / "pyproject.toml"
        if pyproject.exists():
            return pyproject
    return None


def determine_output_path(
    input_file: Path,
    output: Path | None = None,
    output_is_dir: bool = False,
    use_legacy_naming: bool = False,
) -> Path:
    """Determine the YAML output path for a generated component."""

    base_name = input_file.stem.replace("_", "-")
    if output:
        output_name = base_name + ".yaml"
        if output.is_dir() or output_is_dir or (not output.suffix and not output.exists()):
            return output / output_name
        return output

    if use_legacy_naming:
        legacy_name = input_file.stem + ".component.yaml"
        output_dir = input_file.parent / "generated"
        return output_dir / legacy_name

    return input_file.parent / (base_name + ".yaml")


def _extract_image_from_yaml(yaml_path: Path) -> str | None:
    """Extract an existing component container image, if any."""

    if not yaml_path.exists():
        return None
    try:
        with yaml_path.open(encoding="utf-8") as f:
            existing_yaml = yaml.safe_load(f)
        impl = existing_yaml.get("implementation", {}) if isinstance(existing_yaml, dict) else {}
        return impl.get("container", {}).get("image")
    except Exception:
        return None


def regenerate_yaml(
    python_file: Path,
    output_path: Path | None = None,
    function_name: str | None = None,
    custom_name: str | None = None,
    image: str | None = None,
    dependencies_from: Path | None = None,
    strip_code: bool = False,
    strip_source_path: bool = False,
    verbose: bool = False,
    mode: str = "inline",
    resolve_root: Path | None = None,
) -> bool:
    """Regenerate a YAML component from a Python function source file."""

    log = print if verbose else lambda *args, **kwargs: None
    if not python_file.exists():
        log(f"  ❌ File not found: {python_file}")
        return False

    final_output = output_path or determine_output_path(python_file)
    image = image or _extract_image_from_yaml(final_output) or DEFAULT_CONTAINER_IMAGE
    deps_file = dependencies_from or find_dependencies_file(python_file)
    if deps_file:
        log(f"  Found dependencies: {deps_file}")

    final_output.parent.mkdir(parents=True, exist_ok=True)
    return _run_generation(
        python_file=python_file,
        final_output=final_output,
        image=image,
        func_name=function_name,
        deps_file=deps_file,
        custom_name=custom_name,
        strip_code=strip_code,
        strip_source_path=strip_source_path,
        log=log,
        mode=mode,
        resolve_root=resolve_root,
    )


def _run_generation(
    *,
    python_file: Path,
    final_output: Path,
    image: str,
    func_name: str | None,
    deps_file: Path | None,
    custom_name: str | None,
    strip_code: bool,
    strip_source_path: bool,
    log: Any,
    mode: str = "inline",
    resolve_root: Path | None = None,
) -> bool:
    """Execute component generation and clean up partial output on failure."""

    try:
        from tangle_cli.component_from_func import generate_component_yaml

        log(f"  Generating component from {python_file.name}{f' function {func_name!r}' if func_name else ''}...")
        success = generate_component_yaml(
            file_path=python_file,
            output_path=final_output,
            container_image=image,
            function_name=func_name,
            dependencies_from=deps_file,
            mode=mode,  # type: ignore[arg-type]
            custom_name=custom_name,
            strip_code=strip_code,
            strip_source_path=strip_source_path,
            resolve_root=resolve_root,
        )
        if not success:
            log("  ❌ Failed to generate component")
            return False
        log(f"  ✅ Generated: {final_output}")
        return True
    except Exception as exc:
        log(f"  ❌ Error: {exc}")
        if final_output.exists():
            final_output.unlink()
        return False


__all__ = [
    "DEFAULT_CONTAINER_IMAGE",
    "determine_output_path",
    "find_dependencies_file",
    "regenerate_yaml",
]
