"""Generate Tangle component YAML files from local Python functions."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml

# Pin the default runtime image by digest so generated component YAML is reproducible.
# The tag documents the Python line; the digest pins the linux/amd64 image
# used by Tangle execution. Authors can still pass --image to choose a
# different runtime explicitly.
DEFAULT_CONTAINER_IMAGE = "python:3.12@sha256:b8163b64b37051de76577219aa4d5e9b95dc12a2e6c8cb438793c7adb3026016"


class ComponentGenerator:
    """Generic Python-function component generation orchestration.

    The heavy Python-function introspection and YAML construction lives in
    :mod:`tangle_cli.component_from_func`. This class owns the surrounding
    authoring workflow: dependency discovery, output-path derivation, existing
    image reuse, partial-output cleanup, and logging. Downstreams should
    subclass or compose this class rather than wrapping module globals.
    """

    default_container_image = DEFAULT_CONTAINER_IMAGE

    def __init__(
        self,
        *,
        logger: Any | None = None,
        verbose: bool = False,
        default_container_image: str | None = None,
    ) -> None:
        self.logger = logger
        self.verbose = verbose
        if default_container_image is not None:
            self.default_container_image = default_container_image

    def _log(self, message: str, *, err: bool = False) -> None:
        if self.logger is not None:
            log_method = getattr(self.logger, "error", None) if err else getattr(self.logger, "info", None)
            if log_method is not None:
                log_method(message)
                return
        if self.verbose:
            print(message)

    def find_dependencies_file(self, python_file: Path) -> Path | None:
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
        self,
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

    def extract_image_from_yaml(self, yaml_path: Path) -> str | None:
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

    def generate_component_yaml(
        self,
        *,
        file_path: Path,
        output_path: Path,
        container_image: str,
        function_name: str | None = None,
        dependencies_from: Path | None = None,
        mode: Literal["inline", "bundle"] = "inline",
        custom_name: str | None = None,
        custom_annotations: dict[str, str] | None = None,
        strip_code: bool = False,
        strip_source_path: bool = False,
        resolve_root: Path | None = None,
        emit_generation_annotations: bool = True,
    ) -> bool:
        """Generate component YAML from a Python function source file."""

        from tangle_cli.component_from_func import generate_component_yaml

        return generate_component_yaml(
            file_path=file_path,
            output_path=output_path,
            container_image=container_image,
            function_name=function_name,
            dependencies_from=dependencies_from,
            mode=mode,
            custom_name=custom_name,
            custom_annotations=custom_annotations,
            strip_code=strip_code,
            strip_source_path=strip_source_path,
            resolve_root=resolve_root,
            emit_generation_annotations=emit_generation_annotations,
        )

    def regenerate_yaml(
        self,
        python_file: Path,
        output_path: Path | None = None,
        function_name: str | None = None,
        custom_name: str | None = None,
        image: str | None = None,
        dependencies_from: Path | None = None,
        strip_code: bool = False,
        strip_source_path: bool = False,
        mode: str = "inline",
        resolve_root: Path | None = None,
        emit_generation_annotations: bool = True,
    ) -> bool:
        """Regenerate a YAML component from a Python function source file."""

        if not python_file.exists():
            self._log(f"  ❌ File not found: {python_file}", err=True)
            return False

        final_output = output_path or self.determine_output_path(python_file)
        resolved_image = image or self.extract_image_from_yaml(final_output) or self.default_container_image
        deps_file = dependencies_from or self.find_dependencies_file(python_file)
        if deps_file:
            self._log(f"  Found dependencies: {deps_file}")

        final_output.parent.mkdir(parents=True, exist_ok=True)
        return self.run_generation(
            python_file=python_file,
            final_output=final_output,
            image=resolved_image,
            func_name=function_name,
            deps_file=deps_file,
            custom_name=custom_name,
            strip_code=strip_code,
            strip_source_path=strip_source_path,
            mode=mode,
            resolve_root=resolve_root,
            emit_generation_annotations=emit_generation_annotations,
        )

    def run_generation(
        self,
        *,
        python_file: Path,
        final_output: Path,
        image: str,
        func_name: str | None,
        deps_file: Path | None,
        custom_name: str | None,
        strip_code: bool,
        strip_source_path: bool,
        mode: str = "inline",
        resolve_root: Path | None = None,
        emit_generation_annotations: bool = True,
    ) -> bool:
        """Execute component generation and clean up partial output on failure."""

        try:
            function_detail = f" function {func_name!r}" if func_name else ""
            self._log(f"  Generating component from {python_file.name}{function_detail}...")
            success = self.generate_component_yaml(
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
                emit_generation_annotations=emit_generation_annotations,
            )
            if not success:
                self._log("  ❌ Failed to generate component", err=True)
                return False
            self._log(f"  ✅ Generated: {final_output}")
            return True
        except Exception as exc:
            if exc.__class__.__name__ == "AuthoringStripError":
                if final_output.exists():
                    final_output.unlink()
                raise
            self._log(f"  ❌ Error: {exc}", err=True)
            if final_output.exists():
                final_output.unlink()
            return False


def find_dependencies_file(python_file: Path) -> Path | None:
    """Find a dependency file for a Python component source file."""

    return ComponentGenerator().find_dependencies_file(python_file)


def determine_output_path(
    input_file: Path,
    output: Path | None = None,
    output_is_dir: bool = False,
    use_legacy_naming: bool = False,
) -> Path:
    """Determine the YAML output path for a generated component."""

    return ComponentGenerator().determine_output_path(
        input_file,
        output,
        output_is_dir,
        use_legacy_naming,
    )


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
    logger: Any | None = None,
) -> bool:
    """Regenerate a YAML component from a Python function source file."""

    return ComponentGenerator(logger=logger, verbose=verbose).regenerate_yaml(
        python_file=python_file,
        output_path=output_path,
        function_name=function_name,
        custom_name=custom_name,
        image=image,
        dependencies_from=dependencies_from,
        strip_code=strip_code,
        strip_source_path=strip_source_path,
        mode=mode,
        resolve_root=resolve_root,
    )


__all__ = [
    "ComponentGenerator",
    "DEFAULT_CONTAINER_IMAGE",
    "determine_output_path",
    "find_dependencies_file",
    "regenerate_yaml",
]
