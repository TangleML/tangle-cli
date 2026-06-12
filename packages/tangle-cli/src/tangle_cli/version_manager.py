"""Version bumping for Tangle component YAML and Python source files."""

from __future__ import annotations

import ast
import re
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import yaml

from tangle_cli import utils
from tangle_cli.component_from_func import extract_file_metadata, find_function_in_source
from tangle_cli.component_generator import regenerate_yaml
from tangle_cli.logger import Logger, get_default_logger

ReferenceContentGetter = Callable[[str], str | None]


class VersionManager:
    """Manage version updates for component YAML and Python source files."""

    def __init__(self, logger: Logger | None = None) -> None:
        self.log = logger or get_default_logger()

    def parse_version(self, version_str: str) -> tuple[int, ...]:
        """Parse a major/minor[/patch] version string into integer parts."""

        parts = str(version_str).strip().strip("\"'").split(".")
        if len(parts) == 1:
            return (int(parts[0]), 0)
        if len(parts) == 2:
            return (int(parts[0]), int(parts[1]))
        return (int(parts[0]), int(parts[1]), int(parts[2]))

    def increment_version(self, version_str: str) -> str:
        """Increment patch for x.y.z versions, otherwise increment minor."""

        parts = self.parse_version(version_str)
        if len(parts) == 3:
            return f"{parts[0]}.{parts[1]}.{parts[2] + 1}"
        return f"{parts[0]}.{parts[1] + 1}"

    def _get_yaml_version(self, content: str) -> str | None:
        try:
            data = yaml.safe_load(content)
            return utils.get_version_from_data(data)
        except Exception:
            return None

    def update_yaml_file(
        self,
        file_path: str,
        new_version: str | None = None,
        reference_content_getter: ReferenceContentGetter | None = None,
        update_timestamp: bool = False,
    ) -> bool:
        """Update version metadata in a YAML component file."""

        with open(file_path, encoding="utf-8") as f:
            content = f.read()
        data = yaml.safe_load(content) or {}
        old_version = utils.get_version_from_data(data)

        if new_version is None:
            ref_version = None
            if reference_content_getter:
                ref_content = reference_content_getter(file_path)
                if ref_content:
                    ref_version = self._get_yaml_version(ref_content)
                    if ref_version:
                        new_version = self.increment_version(ref_version)
                        self.log.info(f"   📊 Reference version: {ref_version} → bumping to {new_version}")
            if new_version is None:
                if old_version:
                    new_version = self.increment_version(old_version)
                    self.log.info(f"   📊 Local version: {old_version} → bumping to {new_version}")
                else:
                    new_version = "0.1"
                    self.log.info("   📝 No existing version - using 0.1")
        else:
            parts = self.parse_version(new_version)
            new_version = ".".join(str(part) for part in parts)

        self.log.info(f"   {Path(file_path).name}:")
        self.log.info(f"     Current version: {old_version or 'none'}")
        self.log.info(f"     New version:     {new_version}")

        if not isinstance(data, dict):
            self.log.warn("     ⚠️  Could not update YAML - root value is not a mapping")
            return False

        if "metadata" not in data or data["metadata"] is None:
            data["metadata"] = {}
        if not isinstance(data["metadata"], dict):
            self.log.warn("     ⚠️  Could not update YAML - metadata is not a mapping")
            return False
        if "annotations" not in data["metadata"] or data["metadata"]["annotations"] is None:
            data["metadata"]["annotations"] = {}
        if not isinstance(data["metadata"]["annotations"], dict):
            self.log.warn("     ⚠️  Could not update YAML - metadata.annotations is not a mapping")
            return False

        annotations = data["metadata"]["annotations"]
        annotations["version"] = new_version
        data.pop("version", None)
        if update_timestamp:
            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            self.log.info(f"     Timestamp:       {timestamp}")
            annotations["updated_at"] = timestamp
        else:
            existing_timestamp = data.get("updated_at") or annotations.get("updated_at")
            if existing_timestamp:
                annotations["updated_at"] = existing_timestamp
        data.pop("updated_at", None)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(utils.dump_yaml(data))

        self.log.info("     ✅ Updated")
        return True

    def update_python_file(
        self,
        python_file: str,
        new_version: str | None = None,
        reference_content_getter: ReferenceContentGetter | None = None,
        update_timestamp: bool = False,
        function_name: str | None = None,
    ) -> bool:
        """Update a Python component function docstring Metadata section."""

        python_path = Path(python_file)
        if function_name and not _has_exact_public_function(python_path, function_name):
            self.log.warn(f"   ⚠️  Function '{function_name}' not found in {python_path.name}")
            return False
        metadata, actual_func_name = extract_file_metadata(python_path, function_name)
        if not actual_func_name:
            self.log.warn(f"   ⚠️  No function found in {python_path.name}")
            return False

        current_version = metadata.get("version")
        if new_version:
            final_version = new_version
        else:
            ref_version = None
            if reference_content_getter:
                ref_content = reference_content_getter(python_file)
                if ref_content:
                    import tempfile

                    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as tmp:
                        tmp.write(ref_content)
                        tmp_path = Path(tmp.name)
                    try:
                        ref_metadata, _ = extract_file_metadata(tmp_path, actual_func_name)
                        ref_version = ref_metadata.get("version")
                    finally:
                        tmp_path.unlink()
            if ref_version:
                final_version = self.increment_version(ref_version)
                self.log.info(f"   📊 Reference version: {ref_version} → bumping to {final_version}")
            elif current_version:
                final_version = self.increment_version(current_version)
                self.log.info(f"   📊 Local version: {current_version} → bumping to {final_version}")
            else:
                final_version = "0.1"
                self.log.info("   📝 No existing version - using 0.1")

        current_timestamp = (
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            if update_timestamp
            else None
        )
        self.log.info(f"   {python_path.name}:")
        self.log.info(f"     Current version: {current_version or 'none'}")
        self.log.info(f"     New version:     {final_version}")
        if current_timestamp:
            self.log.info(f"     Timestamp:       {current_timestamp}")

        with open(python_file, encoding="utf-8") as f:
            content = f.read()
        new_content = self._update_function_docstring_metadata(
            python_path,
            content,
            actual_func_name,
            final_version,
            current_timestamp,
        )
        if new_content == content:
            self.log.warn("     ⚠️  Could not update docstring - no Metadata section found")
            return False
        with open(python_file, "w", encoding="utf-8") as f:
            f.write(new_content)
        self.log.info("     ✅ Updated")
        return True

    def _update_function_docstring_metadata(
        self,
        python_path: Path,
        content: str,
        function_name: str,
        version: str,
        timestamp: str | None = None,
    ) -> str:
        _, func_node = find_function_in_source(python_path, function_name)
        if not func_node or not func_node.body:
            return content
        doc_node = func_node.body[0]
        value = getattr(doc_node, "value", None)
        if not (
            getattr(doc_node, "lineno", None)
            and getattr(doc_node, "end_lineno", None)
            and isinstance(getattr(value, "value", None), str)
        ):
            return content

        lines = content.splitlines(keepends=True)
        start = doc_node.lineno - 1
        end = doc_node.end_lineno
        docstring_source = "".join(lines[start:end])
        updated_docstring = self._update_docstring_metadata(docstring_source, version, timestamp)
        if updated_docstring == docstring_source:
            return content
        return "".join([*lines[:start], updated_docstring, *lines[end:]])

    def _update_docstring_metadata(
        self,
        content: str,
        version: str,
        timestamp: str | None = None,
    ) -> str:
        metadata_pattern = re.compile(
            r"(Metadata:\s*\n)"
            r"(\s+)"
            r"(?:.*?\n)*?"
            r"(?=\s*(?:Args:|Returns:|Raises:|Yields:|Note:|Example:|\"\"\"|\'\'\')|\Z)",
            re.IGNORECASE | re.MULTILINE,
        )

        def replace_metadata(match: re.Match) -> str:
            header = match.group(1)
            indent = match.group(2)
            result = f"{header}{indent}version: {version}\n"
            if timestamp:
                result += f"{indent}updated_at: {timestamp}\n"
            return result

        return metadata_pattern.sub(replace_metadata, content, count=1)


def _has_exact_public_function(python_path: Path, function_name: str) -> bool:
    """Return whether *python_path* defines exactly this public function."""

    try:
        tree = ast.parse(python_path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return False
    return any(
        isinstance(node, ast.FunctionDef) and node.name == function_name and not node.name.startswith("_")
        for node in ast.iter_child_nodes(tree)
    )


def _common_generation_dir(yaml_path: Path, annotations: dict[str, str]) -> Path | None:
    component_yaml_path = annotations.get("component_yaml_path")
    if not component_yaml_path:
        return None
    yaml_rel = Path(component_yaml_path)
    if yaml_rel.is_absolute():
        return None
    common_dir = yaml_path.resolve().parent
    for part in yaml_rel.parent.parts:
        if part not in ("", "."):
            common_dir = common_dir.parent
    return common_dir


def _resolve_annotated_path(yaml_path: Path, annotations: dict[str, str], annotation_key: str) -> Path | None:
    raw_path = annotations.get(annotation_key)
    if not raw_path:
        return None
    annotated_path = Path(raw_path)
    if annotated_path.is_absolute():
        return annotated_path if annotated_path.exists() else None

    candidates: list[Path] = []
    common_dir = _common_generation_dir(yaml_path, annotations)
    if common_dir:
        candidates.append(common_dir / annotated_path)
    candidates.append(yaml_path.parent / annotated_path)

    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.exists():
            return resolved
    return None


def _resolve_python_source_path(yaml_path: Path, annotations: dict[str, str]) -> Path | None:
    """Resolve a component YAML's annotated Python source path.

    New generated YAML records both ``python_original_code_path`` and
    ``component_yaml_path`` relative to a common ancestor. Older YAML may store
    only the source basename, sometimes beside the YAML or under a sibling
    ``sources`` directory. Try the structured common-ancestor form first, then
    legacy locations.
    """

    raw_python_path = annotations.get("python_original_code_path")
    if not raw_python_path:
        return None

    python_path = Path(raw_python_path)
    if python_path.is_absolute():
        return python_path if python_path.exists() else None

    candidates: list[Path] = []
    common_dir = _common_generation_dir(yaml_path, annotations)
    if common_dir:
        candidates.append(common_dir / python_path)

    yaml_dir = yaml_path.parent
    candidates.extend(
        [
            yaml_dir / python_path,
            yaml_dir / "sources" / python_path.name,
            yaml_dir / python_path.name,
        ]
    )

    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.exists():
            return resolved
    return None


def bump_version(
    yaml_file: str | Path,
    set_version: str | None = None,
    reference_content_getter: ReferenceContentGetter | None = None,
    update_timestamp: bool = False,
    logger: Logger | None = None,
) -> dict[str, str | None]:
    """Bump component version in a YAML file.

    If the YAML references a local Python source via
    ``metadata.annotations.python_original_code_path``, updates that source and
    regenerates the YAML. Otherwise updates YAML metadata directly.
    """

    log = logger or get_default_logger()
    yaml_path = Path(yaml_file)
    if not yaml_path.exists():
        log.error(f"❌ File not found: {yaml_file}")
        return {"status": "failed", "error": f"File not found: {yaml_file}"}
    if yaml_path.suffix not in [".yaml", ".yml"]:
        log.error(f"❌ Not a YAML file: {yaml_file}")
        return {"status": "failed", "error": f"Not a YAML file: {yaml_file}"}

    version_manager = VersionManager(logger=log)
    with open(yaml_path, encoding="utf-8") as f:
        yaml_content = yaml.safe_load(f) or {}
    old_version = utils.get_version_from_data(yaml_content)

    annotations: dict[str, str] = {}
    metadata = yaml_content.get("metadata") if isinstance(yaml_content, dict) else None
    if isinstance(metadata, dict) and isinstance(metadata.get("annotations"), dict):
        annotations = metadata["annotations"]
    python_path = annotations.get("python_original_code_path")
    has_original_code = "python_original_code" in annotations
    generation_function_name = annotations.get("tangle_cli_generation_function_name")
    generation_mode = annotations.get("tangle_cli_generation_mode") or (
        "bundle" if annotations.get("bundled_modules") else "inline"
    )
    if generation_mode not in {"inline", "bundle"}:
        error = f"Unsupported generation mode: {generation_mode}"
        log.error(f"❌ {error}")
        return {"status": "failed", "yaml_file": str(yaml_path), "error": error}
    custom_name = (
        yaml_content.get("name")
        if isinstance(yaml_content, dict) and isinstance(yaml_content.get("name"), str)
        else None
    )

    dependencies_from = None
    if annotations.get("tangle_cli_generation_dependencies_from"):
        dependencies_from = _resolve_annotated_path(
            yaml_path,
            annotations,
            "tangle_cli_generation_dependencies_from",
        )
        if dependencies_from is None:
            error = f"Dependency file not found: {annotations['tangle_cli_generation_dependencies_from']}"
            log.error(f"❌ {error}")
            return {"status": "failed", "yaml_file": str(yaml_path), "error": error}

    resolve_root = None
    if annotations.get("tangle_cli_generation_resolve_root"):
        resolve_root = _resolve_annotated_path(
            yaml_path,
            annotations,
            "tangle_cli_generation_resolve_root",
        )
        if resolve_root is None:
            error = f"Resolve root not found: {annotations['tangle_cli_generation_resolve_root']}"
            log.error(f"❌ {error}")
            return {"status": "failed", "yaml_file": str(yaml_path), "error": error}

    if python_path:
        python_full_path = _resolve_python_source_path(yaml_path, annotations)
        if python_full_path:
            log.info(f"   📍 Found Python source: {python_full_path.name}")
            success = version_manager.update_python_file(
                str(python_full_path),
                new_version=set_version,
                reference_content_getter=reference_content_getter,
                update_timestamp=update_timestamp,
                function_name=generation_function_name,
            )
            if success:
                log.info("   🔄 Regenerating YAML...")
                success = regenerate_yaml(
                    python_full_path,
                    output_path=yaml_path,
                    function_name=generation_function_name,
                    custom_name=custom_name,
                    dependencies_from=dependencies_from,
                    strip_code=not has_original_code,
                    mode=generation_mode,
                    resolve_root=resolve_root,
                )
        else:
            log.error(f"❌ Python source not found: {python_path}")
            return {
                "status": "failed",
                "yaml_file": str(yaml_path),
                "error": f"Python source not found: {python_path}",
            }
    else:
        success = version_manager.update_yaml_file(
            str(yaml_path),
            new_version=set_version,
            reference_content_getter=reference_content_getter,
            update_timestamp=update_timestamp,
        )

    if not success:
        return {"status": "failed", "yaml_file": str(yaml_path), "error": "Version update failed"}

    with open(yaml_path, encoding="utf-8") as f:
        new_version = utils.get_version_from_data(yaml.safe_load(f))
    return {
        "status": "success",
        "yaml_file": str(yaml_path),
        "old_version": old_version,
        "new_version": new_version,
    }


__all__ = ["ReferenceContentGetter", "VersionManager", "bump_version"]
