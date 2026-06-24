"""Pipeline dehydration helpers for hydrated Tangle pipeline specs.

The dehydrator is the inverse companion to :mod:`tangle_cli.pipeline_hydrator`:
it replaces full ``componentRef.spec`` blocks with portable digest/name/url/file
references, and can export a hydrated pipeline into a Jinja2 template + config
pair.  The code is intentionally native-free; downstream packages can provide a
client for component-library existence checks and URI reader/writer hooks for
schemes such as ``gs://`` without this module importing those SDKs.
"""

from __future__ import annotations

import copy
import json
import os
import re
import textwrap
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from . import utils
from .api_transport import DEFAULT_API_URL
from .handler import TangleCliHandler
from .logger import Logger, get_default_logger
from .pipeline_hydrator import PipelineHydrator, ResolverContext, UriReader, UriWriter

PATH_SEPARATOR = "|"  # Use | as separator since task names can contain dots.


@dataclass(frozen=True)
class Jinja2ExportResult:
    """Result of exporting a pipeline to Jinja2 templates."""

    main_template_path: Path
    config_file_path: Path
    subtemplates_count: int
    top_level_params_count: int
    subtemplate_paths: list[Path]


class DehydrateChoice:
    """Constants for dehydration choices.

    Lowercase values apply to the current component.  Downstream interactive
    callers may use uppercase values to remember a choice for the same digest.
    """

    DIGEST = "d"
    NAME = "n"
    URL = "u"
    FILE = "f"
    KEEP = "k"
    AUTO = "a"


class PipelineDehydrator(TangleCliHandler):
    """Dehydrate pipeline YAML by replacing full component specs with refs.

    Supported choices:
    - ``DIGEST``: replace with ``componentRef.digest``
    - ``NAME``: replace with ``componentRef.name``
    - ``URL``: replace with ``componentRef.url`` when a canonical URL exists
    - ``FILE``: extract the component spec and reference it by URL
    - ``KEEP``: preserve the full spec
    - ``AUTO``: URL if canonical, else digest when the optional client can find
      the component in the library, else file extraction

    URI I/O is delegated through the same native-free hooks as the hydrator.
    OSS registers no cloud schemes by default; downstream packages can pass or
    register URI hooks for ``gs://`` or other backends.
    """

    def __init__(
        self,
        remembered_choices: Mapping[str, str] | None = None,
        components_dir: Path | str | None = None,
        output_file: Path | str | None = None,
        client: Any = None,
        interactive: bool = False,
        logger: Logger | None = None,
        component_extension: str | None = None,
        *,
        base_url: str | None = None,
        uri_readers: Mapping[str, UriReader] | None = None,
        uri_writers: Mapping[str, UriWriter] | None = None,
    ) -> None:
        super().__init__(
            client=client,
            logger=logger,
            base_url=base_url or DEFAULT_API_URL,
        )
        self.remembered_choices = dict(remembered_choices or {})
        self.output_file = output_file
        self.component_extension = component_extension or ".yaml"

        self._components_dir_explicit = components_dir is not None
        if components_dir is not None:
            self.components_dir: Path | str = components_dir
        elif output_file is not None:
            self.components_dir = self._join_destination(self._destination_parent(output_file), "components")
        else:
            self.components_dir = Path("components")

        self.interactive = interactive
        self._saved_components: dict[str, Path | str] = {}
        self._current_reference_file: Path | str | None = output_file
        self._io = PipelineHydrator(
            enable_resolution=False,
            logger=self.log,
            base_url=self.base_url,
            uri_readers=uri_readers,
            uri_writers=uri_writers,
        )

    def _is_auto_mode(self) -> bool:
        """Return True when any remembered choice asks for auto mode."""

        return DehydrateChoice.AUTO in self.remembered_choices.values()

    @staticmethod
    def _uri_scheme(value: Path | str | None) -> str | None:
        if value is None:
            return None
        return PipelineHydrator._uri_scheme(str(value))

    @classmethod
    def _is_local_destination(cls, value: Path | str | None) -> bool:
        scheme = cls._uri_scheme(value)
        return scheme is None or scheme == "file"

    @classmethod
    def _destination_parent(cls, value: Path | str) -> Path | str:
        value_str = str(value)
        scheme = cls._uri_scheme(value)
        if scheme and scheme != "file":
            return value_str.rsplit("/", 1)[0] if "/" in value_str else value_str
        path = Path(value_str[7:] if value_str.startswith("file://") else value_str)
        return path.parent

    @classmethod
    def _join_destination(cls, parent: Path | str, filename: str) -> Path | str:
        if cls._uri_scheme(parent) and cls._uri_scheme(parent) != "file":
            return f"{str(parent).rstrip('/')}/{filename}"
        return Path(parent) / filename

    def _resolver_context(self, uri: str, kind: str) -> ResolverContext:
        return self._io.make_resolver_context(self._uri_scheme(uri) or kind, uri, kind, None)

    def _read_text(self, source: Path | str, *, kind: str = "pipeline") -> str:
        return self._io._read_uri_text(str(source), kind, self._resolver_context(str(source), kind)) or ""

    def _write_text(self, destination: Path | str, content: str, *, kind: str = "output") -> None:
        self._io._write_uri_text(str(destination), content, self._resolver_context(str(destination), kind))

    def load_file(self, input_file: Path | str) -> dict[str, Any]:
        """Read a local or URI pipeline YAML file through the registered hooks."""

        data = yaml.safe_load(self._read_text(input_file, kind="pipeline"))
        return data or {}

    def write_file(self, data: dict[str, Any], output_file: Path | str | None = None) -> None:
        """Write pipeline YAML to a local path or URI through registered hooks."""

        destination = output_file or self.output_file
        if destination is None:
            raise ValueError("output_file is required")
        self._write_text(destination, utils.dump_yaml(data), kind="output")

    def dehydrate_file(
        self,
        input_file: Path | str,
        output_file: Path | str | None = None,
    ) -> dict[str, Any]:
        """Read, dehydrate, and write a pipeline YAML file.

        Both input and output support local paths and any URI schemes provided
        by registered/passed hydrator URI hooks.
        """

        previous_output = self.output_file
        previous_reference = self._current_reference_file
        previous_components_dir = self.components_dir
        if output_file is not None:
            self.output_file = output_file
            self._current_reference_file = output_file
            if not self._components_dir_explicit:
                self.components_dir = self._join_destination(self._destination_parent(output_file), "components")
        try:
            data = self.load_file(input_file)
            output = self.dehydrate(data)
            self.write_file(output, output_file)
            return output
        finally:
            self.output_file = previous_output
            self._current_reference_file = previous_reference
            self.components_dir = previous_components_dir

    def _auto_dehydrate_choice(
        self,
        canonical_url: str | None,
        resolved_digest: str,
        name: str,
        _spec: dict[str, Any],
        path: str,
    ) -> str:
        """Determine Auto mode outcome: ``url``, ``digest``, or ``file``."""

        self.log.info(f"   Auto: '{name}' at {path} (digest: {resolved_digest[:16]}...)")
        if canonical_url:
            self.log.info("   Auto: has canonical URL -> url ref")
            return "url"
        if not resolved_digest or resolved_digest == "unknown":
            self.log.info("   Auto: no digest -> file")
            return "file"
        try:
            client = self._get_client()
        except (Exception, SystemExit):
            self.log.info("   Auto: no API client available -> file")
            return "file"
        if client is None:
            self.log.info("   Auto: no API client provided -> file")
            return "file"
        try:
            client.get_component_spec(resolved_digest)
            self.log.info(f"   Auto: digest {resolved_digest[:16]} found in library -> digest ref")
            return "digest"
        except Exception:
            self.log.info(f"   Auto: digest {resolved_digest[:16]} not in library -> file")
            return "file"

    def _prompt_choice(self, name: str, digest: str, canonical_url: str | None, path: str) -> str:
        self.log.info(f"\n📦 Found componentRef at: {path}")
        self.log.info(f"   Name: {name}")
        self.log.info(f"   Digest: {digest[:16]}...")
        if canonical_url:
            self.log.info(f"   URL: {canonical_url}")
        self.log.info("   Options:")
        self.log.info(f"     [{DehydrateChoice.DIGEST}] Replace with componentRef.digest")
        self.log.info(f"     [{DehydrateChoice.NAME}] Replace with componentRef.name")
        if canonical_url:
            self.log.info(f"     [{DehydrateChoice.URL}] Replace with componentRef.url")
        self.log.info(f"     [{DehydrateChoice.FILE}] Extract to file and use file:// URL")
        self.log.info(f"     [{DehydrateChoice.AUTO}] Auto: URL if present, else digest if in library, else file")
        self.log.info(f"     [{DehydrateChoice.KEEP}] Leave as is (keep full spec)")
        self.log.info(f"     [{DehydrateChoice.DIGEST.upper()}] Always replace this component with digest")
        self.log.info(f"     [{DehydrateChoice.NAME.upper()}] Always replace this component with name")
        if canonical_url:
            self.log.info(f"     [{DehydrateChoice.URL.upper()}] Always replace this component with URL")
        self.log.info(f"     [{DehydrateChoice.FILE.upper()}] Always extract to file")
        choice = input(f"   Choice [{DehydrateChoice.AUTO}]: ").strip() or DehydrateChoice.AUTO
        return choice

    def _process_task(
        self,
        task_name: str,
        task_data: dict[str, Any],
        path: str,
        base_dir: Path | None = None,
        _recursive_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Dehydrate a single non-subgraph task's componentRef."""

        del task_name, base_dir, _recursive_params
        if not isinstance(task_data, dict) or "componentRef" not in task_data:
            return task_data

        component_ref = task_data["componentRef"]
        if not isinstance(component_ref, dict) or "spec" not in component_ref:
            return task_data

        name, digest = utils.get_component_ref_info(component_ref)
        spec = component_ref.get("spec", {})
        if not isinstance(spec, dict):
            return task_data

        canonical_url = spec.get("metadata", {}).get("annotations", {}).get("canonical_location")
        resolved_digest = component_ref.get("digest") or utils.compute_spec_digest(spec)
        choice = (
            self.remembered_choices.get(resolved_digest)
            or self.remembered_choices.get(digest)
            or self.remembered_choices.get("")
        )
        if choice:
            if choice == DehydrateChoice.URL and not canonical_url:
                choice = DehydrateChoice.DIGEST
            if choice != DehydrateChoice.AUTO:
                self.log.info(f"   Using remembered choice: {choice}")
        elif self.interactive:
            choice = self._prompt_choice(name, digest, canonical_url, path)
            if choice == DehydrateChoice.DIGEST.upper():
                self.remembered_choices[resolved_digest] = DehydrateChoice.DIGEST
                choice = DehydrateChoice.DIGEST
            elif choice == DehydrateChoice.NAME.upper():
                self.remembered_choices[resolved_digest] = DehydrateChoice.NAME
                choice = DehydrateChoice.NAME
            elif choice == DehydrateChoice.URL.upper() and canonical_url:
                self.remembered_choices[resolved_digest] = DehydrateChoice.URL
                choice = DehydrateChoice.URL
            elif choice == DehydrateChoice.FILE.upper():
                self.remembered_choices[resolved_digest] = DehydrateChoice.FILE
                choice = DehydrateChoice.FILE
        else:
            choice = DehydrateChoice.AUTO

        new_task = {k: v for k, v in task_data.items() if k != "componentRef"}

        if choice == DehydrateChoice.AUTO:
            effective = self._auto_dehydrate_choice(canonical_url, resolved_digest, name, spec, path)
            if effective == "url":
                new_task["componentRef"] = {"url": canonical_url}
                self.log.info("   → Auto: Replaced with componentRef.url")
            elif effective == "digest":
                new_task["componentRef"] = {"digest": resolved_digest}
                self.log.info("   → Auto: Replaced with componentRef.digest (found in library)")
            else:
                file_url = self._save_component_to_file(name, resolved_digest, spec)
                new_task["componentRef"] = {"url": file_url}
                self.log.info("   → Auto: Extracted to file (no URL, not in library or no client)")
        elif choice == DehydrateChoice.DIGEST:
            new_task["componentRef"] = {"digest": resolved_digest}
            self.log.info("   → Replaced with componentRef.digest")
        elif choice == DehydrateChoice.NAME:
            new_task["componentRef"] = {"name": name}
            self.log.info("   → Replaced with componentRef.name")
        elif choice == DehydrateChoice.URL and canonical_url:
            new_task["componentRef"] = {"url": canonical_url}
            self.log.info("   → Replaced with componentRef.url")
        elif choice == DehydrateChoice.FILE:
            file_url = self._save_component_to_file(name, resolved_digest, spec)
            new_task["componentRef"] = {"url": file_url}
            self.log.info(f"   → Extracted to {file_url}")
        else:
            new_task["componentRef"] = component_ref
            self.log.info("   → Kept as componentRef (full spec)")

        return new_task

    def _safe_filename(self, name: str, fallback: str = "component") -> str:
        safe_name = name.lower().replace(" ", "_").replace("-", "_")
        safe_name = "".join(c for c in safe_name if c.isalnum() or c == "_")
        return safe_name or fallback

    def _save_component_to_file(self, name: str, digest: str, spec: dict[str, Any]) -> str:
        """Save a component spec once and return a reference URL for this file."""

        if digest not in self._saved_components:
            filename = f"{self._safe_filename(name)}{self.component_extension}"
            destination = self._join_destination(self.components_dir, filename)
            self._write_text(destination, utils.dump_yaml(spec), kind="component")
            if self._is_local_destination(destination):
                destination_text = str(destination)
                if destination_text.startswith("file://"):
                    destination_text = destination_text[7:]
                destination = Path(destination_text).resolve()
            self._saved_components[digest] = destination
        return self._make_ref_url(self._saved_components[digest])

    def _make_ref_url(self, target: Path | str) -> str:
        """Create a componentRef URL for a saved target."""

        if not self._is_local_destination(target):
            return str(target)
        return self._make_file_url(Path(str(target)[7:] if str(target).startswith("file://") else str(target)))

    def _make_file_url(self, target_path: Path) -> str:
        """Create a file:// URL relative to the current reference file."""

        ref_file = self._current_reference_file or self.output_file
        if ref_file and self._is_local_destination(ref_file):
            ref_str = str(ref_file)
            ref_path = Path(ref_str[7:] if ref_str.startswith("file://") else ref_str)
            ref_dir = ref_path.parent.resolve()
            rel = os.path.relpath(target_path.resolve(), ref_dir)
            return f"file://./{rel}"
        return f"file://{target_path.resolve()}"

    @staticmethod
    def _relativize_file_urls(spec: dict[str, Any], reference_dir: Path) -> None:
        """Convert absolute file:// URLs in a spec's tasks relative to reference_dir."""

        tasks = spec.get("implementation", {}).get("graph", {}).get("tasks", {})
        resolved_ref_dir = reference_dir.resolve()
        for task_data in tasks.values():
            if not isinstance(task_data, dict):
                continue
            component_ref = task_data.get("componentRef")
            if not isinstance(component_ref, dict) or "url" not in component_ref:
                continue
            url = component_ref["url"]
            if not isinstance(url, str) or not url.startswith("file:///"):
                continue
            abs_path = Path(url[7:])
            rel = os.path.relpath(abs_path, resolved_ref_dir)
            component_ref["url"] = f"file://./{rel}"

    def _subgraph_destination(self, filename: str) -> Path | str:
        if self.output_file is not None:
            subgraph_dir = self._join_destination(self._destination_parent(self.output_file), "subgraphs")
        else:
            subgraph_dir = self._join_destination(self.components_dir, "subgraphs")
        return self._join_destination(subgraph_dir, filename)

    def _extract_subgraphs_to_files(self, data: dict[str, Any]) -> dict[str, Any]:
        """Extract subgraph specs to YAML files and replace them with URL refs."""

        queue = _build_subgraph_processing_queue(data)
        subgraph_counter = 0

        for depth, path in queue:
            if depth == 0:
                continue

            result = _get_subgraph_by_path(data, path)
            if not result:
                continue
            component_ref, spec = result

            spec_name = spec.get("name", "subgraph")
            filename = f"{self._safe_filename(str(spec_name), 'subgraph')}_{subgraph_counter}{self.component_extension}"
            subgraph_counter += 1
            destination = self._subgraph_destination(filename)

            original_ref = self._current_reference_file
            self._current_reference_file = destination
            try:
                spec_to_write = utils.traverse_pipeline_tasks(copy.deepcopy(spec), str(spec_name), self._process_task)
            finally:
                self._current_reference_file = original_ref

            if self._is_local_destination(destination):
                destination_text = str(destination)
                if destination_text.startswith("file://"):
                    destination_text = destination_text[7:]
                destination_path = Path(destination_text)
                self._relativize_file_urls(spec_to_write, destination_path.parent)
                self._write_text(destination_path, utils.dump_yaml(spec_to_write) + "\n", kind="subgraph")
                component_url = f"file://{destination_path.resolve()}"
            else:
                self._write_text(destination, utils.dump_yaml(spec_to_write) + "\n", kind="subgraph")
                component_url = str(destination)

            self.log.info(f"   📦 Extracted subgraph '{spec_name}' -> {filename}")
            component_ref.clear()
            component_ref["url"] = component_url

        if self.output_file and self._is_local_destination(self.output_file):
            output_file_text = str(self.output_file)
            if output_file_text.startswith("file://"):
                output_file_text = output_file_text[7:]
            output_path = Path(output_file_text)
            self._relativize_file_urls(data, output_path.parent)

        return data

    def dehydrate(self, data: dict[str, Any]) -> dict[str, Any]:
        """Return a dehydrated copy of *data* according to configured choices."""

        working = copy.deepcopy(data)
        if self.remembered_choices.get("") == DehydrateChoice.AUTO:
            self._extract_subgraphs_to_files(working)

        pipeline_name = working.get("name", "pipeline")
        return utils.traverse_pipeline_tasks(working, str(pipeline_name), self._process_task)

    def export_to_jinja2(
        self,
        data: dict[str, Any],
        output_file: Path,
        jinja2_path: Path,
    ) -> Jinja2ExportResult:
        """Dehydrate a pipeline and export it to Jinja2 template files."""

        previous_output = self.output_file
        previous_reference = self._current_reference_file
        previous_components_dir = self.components_dir
        self.output_file = output_file
        self._current_reference_file = output_file
        if not self._components_dir_explicit:
            self.components_dir = self._join_destination(self._destination_parent(output_file), "components")
        try:
            output_yaml = self.dehydrate(data)
        finally:
            self.output_file = previous_output
            self._current_reference_file = previous_reference
            self.components_dir = previous_components_dir

        jinja2_path.parent.mkdir(parents=True, exist_ok=True)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        base_name = jinja2_path.stem
        if base_name.endswith(".yaml"):
            base_name = base_name[:-5]

        top_level_defaults = _extract_input_defaults(output_yaml)
        modified_data, subtemplates = _process_subgraphs_to_subtemplates(output_yaml, self.log)
        template_data = _replace_input_defaults_with_placeholders(modified_data)

        subtemplate_paths: list[Path] = []
        for subtemplate_id, subtemplate_info in subtemplates.items():
            subtemplate_file = jinja2_path.parent / f"{base_name}_{subtemplate_id}.yaml.j2"
            subtemplate_yaml = utils.dump_yaml(subtemplate_info["spec"])

            path_depth = subtemplate_info["path"].count(PATH_SEPARATOR) // 2
            indent = " " * (12 * path_depth)
            subtemplate_yaml = textwrap.indent(subtemplate_yaml, indent)
            subtemplate_yaml = _convert_templateid_to_includes(subtemplate_yaml, subtemplates, base_name)

            subtemplate_file.write_text(subtemplate_yaml, encoding="utf-8")
            subtemplate_paths.append(subtemplate_file)
            self.log.info(f"   📄 Wrote {subtemplate_file.name}")

        main_yaml = utils.dump_yaml(template_data)
        main_yaml = _convert_templateid_to_includes(main_yaml, subtemplates, base_name)
        jinja2_path.write_text(main_yaml, encoding="utf-8")

        try:
            rel_template_path = jinja2_path.relative_to(output_file.parent)
        except ValueError:
            rel_template_path = jinja2_path

        config_data: dict[str, Any] = {"template_file": str(rel_template_path), **top_level_defaults}
        output_file.write_text(utils.dump_yaml(config_data), encoding="utf-8")

        return Jinja2ExportResult(
            main_template_path=jinja2_path,
            config_file_path=output_file,
            subtemplates_count=len(subtemplates),
            top_level_params_count=len(top_level_defaults),
            subtemplate_paths=subtemplate_paths,
        )


def _extract_input_defaults(data: dict[str, Any]) -> dict[str, Any]:
    """Extract default values from top-level inputs."""

    defaults: dict[str, Any] = {}
    inputs = data.get("inputs", [])
    if isinstance(inputs, list):
        for input_spec in inputs:
            if isinstance(input_spec, dict) and "name" in input_spec and "default" in input_spec:
                defaults[_sanitize_variable_name(str(input_spec["name"]))] = input_spec["default"]
    elif isinstance(inputs, dict):
        for name, input_def in inputs.items():
            if isinstance(input_def, dict) and "default" in input_def:
                defaults[_sanitize_variable_name(str(name))] = input_def["default"]
    return defaults


def _replace_input_defaults_with_placeholders(data: dict[str, Any]) -> dict[str, Any]:
    """Replace top-level input defaults with Jinja2 placeholders."""

    modified = copy.deepcopy(data)
    inputs = modified.get("inputs", [])
    if isinstance(inputs, list):
        for input_spec in inputs:
            if isinstance(input_spec, dict) and "name" in input_spec and "default" in input_spec:
                var_name = _sanitize_variable_name(str(input_spec["name"]))
                input_spec["default"] = "{{ " + var_name + " }}"
    elif isinstance(inputs, dict):
        for name, input_def in inputs.items():
            if isinstance(input_def, dict) and "default" in input_def:
                var_name = _sanitize_variable_name(str(name))
                input_def["default"] = "{{ " + var_name + " }}"
    return modified


def _sanitize_variable_name(name: str) -> str:
    """Convert a name to a valid Jinja2 variable name."""

    sanitized = re.sub(r"[^\w]", "_", name.lower())
    sanitized = re.sub(r"_+", "_", sanitized)
    return sanitized.strip("_")


def _convert_templateid_to_includes(
    yaml_text: str,
    subtemplates: Mapping[str, Mapping[str, Any]],
    base_name: str,
) -> str:
    """Convert templateId markers in YAML to Jinja2 include syntax."""

    def replace_with_include(match: re.Match[str], template_file: str) -> str:
        name_value = match.group(1).strip()
        if not (name_value.startswith("'") or name_value.startswith('"')):
            name_value = f"'{name_value}'"
        return f"{{% with _subgraph_name = {name_value} %}}{{% include '{template_file}' %}}{{% endwith %}}"

    for subtemplate_id in subtemplates:
        template_filename = f"{base_name}_{subtemplate_id}.yaml.j2"
        yaml_text = re.sub(
            rf"^\s*templateId:\s*{re.escape(subtemplate_id)}\s*\n\s*_subgraph_name:\s*(.+?)\s*$",
            lambda m: replace_with_include(m, template_filename),
            yaml_text,
            flags=re.MULTILINE,
        )
    return yaml_text


def _build_subgraph_processing_queue(data: dict[str, Any]) -> list[tuple[int, str]]:
    """Build subgraph paths ordered deepest-first."""

    results: list[tuple[int, str]] = []
    stack: list[tuple[dict[str, Any], str, int]] = [(data, "", 0)]

    while stack:
        spec, current_path, depth = stack.pop()
        spec_name = spec.get("name", "unnamed")
        path = f"{current_path}{PATH_SEPARATOR}{spec_name}" if current_path else str(spec_name)
        results.append((depth, path))

        tasks = spec.get("implementation", {}).get("graph", {}).get("tasks", {})
        for task_name, task_data in tasks.items():
            if not isinstance(task_data, dict):
                continue
            component_ref = task_data.get("componentRef")
            if not isinstance(component_ref, dict):
                continue
            nested_spec = component_ref.get("spec", {})
            if utils.is_subgraph_spec(nested_spec):
                stack.append((nested_spec, f"{path}{PATH_SEPARATOR}{task_name}", depth + 1))

    return sorted(results, key=lambda item: (-item[0], item[1]))


def _get_task_component_ref(spec: dict[str, Any], task_name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return ``(componentRef, nested_spec)`` for a task in a spec graph."""

    tasks = spec.get("implementation", {}).get("graph", {}).get("tasks", {})
    task_data = tasks.get(task_name, {})
    component_ref = task_data.get("componentRef", {})
    nested_spec = component_ref.get("spec", {}) if isinstance(component_ref, dict) else {}
    return component_ref, nested_spec


def _get_subgraph_by_path(data: dict[str, Any], path: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Resolve a subgraph's componentRef and spec by queue path."""

    path_parts = path.split(PATH_SEPARATOR)
    if len(path_parts) < 3:
        return None
    current_spec = data
    for i in range(1, len(path_parts) - 2, 2):
        task_name = path_parts[i]
        _, current_spec = _get_task_component_ref(current_spec, task_name)

    parent_task_name = path_parts[-2]
    component_ref, spec = _get_task_component_ref(current_spec, parent_task_name)
    if not spec:
        return None
    return component_ref, spec


def _spec_hash(spec: dict[str, Any]) -> str:
    """Compute a hash key for a spec dictionary, ignoring top-level name."""

    spec_for_hash = {k: v for k, v in spec.items() if k != "name"}
    return json.dumps(spec_for_hash, sort_keys=True)


def _process_subgraphs_to_subtemplates(
    data: dict[str, Any],
    logger: Logger | None = None,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Extract subgraph specs into reusable subtemplate records."""

    log = logger or get_default_logger()
    working = copy.deepcopy(data)
    queue = _build_subgraph_processing_queue(working)
    subtemplates_by_hash: dict[str, dict[str, Any]] = {}
    subtemplate_counter = 0

    for depth, path in queue:
        if depth == 0:
            continue

        result = _get_subgraph_by_path(working, path)
        if not result:
            continue
        component_ref, spec = result

        spec_key = _spec_hash(spec)
        spec_name = spec.get("name", "unnamed")
        if spec_key in subtemplates_by_hash:
            subtemplate_id = subtemplates_by_hash[spec_key]["id"]
            log.info(f"   ♻️  Reusing {subtemplate_id} for '{spec_name}'")
        else:
            subtemplate_id = f"subtemplate_{subtemplate_counter}"
            subtemplate_counter += 1
            spec_copy = copy.deepcopy(spec)
            if "name" in spec_copy:
                spec_copy["name"] = "{{ _subgraph_name }}"
            subtemplates_by_hash[spec_key] = {"id": subtemplate_id, "spec": spec_copy, "path": path}
            log.info(f"   📦 Created {subtemplate_id} for '{spec_name}'")

        component_ref["spec"] = {"templateId": subtemplate_id, "_subgraph_name": spec_name}

    subtemplates = {
        info["id"]: {"spec": info["spec"], "path": info["path"]}
        for info in subtemplates_by_hash.values()
    }
    return working, subtemplates


__all__ = [
    "DehydrateChoice",
    "Jinja2ExportResult",
    "PipelineDehydrator",
    "PATH_SEPARATOR",
    "_build_subgraph_processing_queue",
    "_convert_templateid_to_includes",
    "_extract_input_defaults",
    "_get_subgraph_by_path",
    "_process_subgraphs_to_subtemplates",
    "_replace_input_defaults_with_placeholders",
    "_sanitize_variable_name",
]
