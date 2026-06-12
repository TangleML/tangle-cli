"""Handwritten extensions mixed into generated Tangle API models."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, cast

import yaml

import tangle_cli.utils as utils


def _strip_text_from_graph(implementation: dict[str, Any]) -> None:
    """Recursively remove raw component text from graph component references."""

    graph = implementation.get("graph", {})
    for task_data in graph.get("tasks", {}).values():
        ref = task_data.get("componentRef")
        if not ref:
            continue
        ref.pop("text", None)
        spec = ref.get("spec", {})
        nested_impl = spec.get("implementation")
        if nested_impl and "graph" in nested_impl:
            _strip_text_from_graph(nested_impl)


def _add_official_prefix(name: str) -> str:
    """Return the official component name variant used by registry searches."""

    if name and not name.startswith("[Official]"):
        return f"[Official] {name}"
    return name


class ComponentSpecExtensions:
    """Legacy YAML-domain conveniences for the generated ComponentSpec model."""

    _STRIP_ANNOTATION_KEYS = {"python_original_code", "python_dependencies"}

    def _extra_get(self, key: str, default: Any = None) -> Any:
        extra = getattr(self, "__pydantic_extra__", None)
        if isinstance(extra, dict) and key in extra:
            return extra[key]
        return getattr(self, "__dict__", {}).get(key, default)

    def _extra_set(self, key: str, value: Any) -> None:
        extra = getattr(self, "__pydantic_extra__", None)
        if isinstance(extra, dict):
            extra[key] = value
        else:  # pragma: no cover - pydantic v1 fallback
            self.__dict__[key] = value

    @property
    def data(self) -> dict[str, Any]:
        data = self._extra_get("data")
        if isinstance(data, dict):
            return data
        result: dict[str, Any] = {}
        if self.name:
            result["name"] = self.name
        if self.description is not None:
            result["description"] = self.description
        if self.metadata:
            result["metadata"] = self.metadata
        if self.inputs:
            result["inputs"] = self.inputs
        if self.outputs:
            result["outputs"] = self.outputs
        if self.implementation:
            result["implementation"] = self.implementation
        return result

    @data.setter
    def data(self, value: dict[str, Any]) -> None:
        self._extra_set("data", value)

    @property
    def digest(self) -> str:
        return str(self._extra_get("digest", "") or "")

    @digest.setter
    def digest(self, value: str) -> None:
        self._extra_set("digest", value)

    @property
    def text(self) -> str | None:
        return self._extra_get("text")

    @text.setter
    def text(self, value: str | None) -> None:
        self._extra_set("text", value)

    @property
    def version(self) -> str | None:
        return self._extra_get("version")

    @version.setter
    def version(self, value: str | None) -> None:
        self._extra_set("version", value)

    @property
    def annotations(self) -> dict[str, str]:
        annotations = self._extra_get("annotations")
        if isinstance(annotations, dict):
            return annotations
        return (self.metadata or {}).get("annotations", {})

    @annotations.setter
    def annotations(self, value: dict[str, str]) -> None:
        self._extra_set("annotations", value)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Any:
        """Create from a raw component API response.

        ``/api/components/{digest}`` responses carry raw YAML in ``text`` and
        may carry the parsed YAML in ``spec``. The generated model stores the
        schema fields while extra fields preserve legacy helpers such as
        ``data``, ``digest``, ``text``, ``version``, and ``annotations``.
        """

        spec = data.get("spec")
        text = data.get("text")
        if spec is None and text:
            spec = yaml.safe_load(text)
        spec = spec or {}
        annotations = spec.get("metadata", {}).get("annotations", {})
        return cls(
            digest=data.get("digest", ""),
            data=spec,
            text=text,
            name=spec.get("name", ""),
            version=annotations.get("version"),
            description=spec.get("description"),
            annotations=annotations,
            inputs=spec.get("inputs", []),
            outputs=spec.get("outputs", []),
            implementation=spec.get("implementation"),
            metadata=spec.get("metadata"),
        )

    @classmethod
    def from_yaml_file(cls, yaml_path: str) -> Any:
        """Load and parse a component YAML file."""

        with open(yaml_path) as f:
            yaml_content = f.read()
        return cls.from_yaml(yaml_content)

    @classmethod
    def from_yaml(
        cls,
        yaml_content: str,
        annotations: dict[str, str] | None = None,
    ) -> Any:
        """Create from YAML text, optionally merging annotations first."""

        data = utils.parse_yaml_string(yaml_content)
        if not data:
            raise ValueError("Unable to parse YAML content")

        if annotations:
            data.setdefault("metadata", {}).setdefault("annotations", {}).update(annotations)

        name = data.get("name")
        if not name:
            raise ValueError("Component name is required but not found in YAML")

        version = utils.get_version_from_data(data) or None
        return cls(
            data=data,
            version=version,
            name=name,
            description=data.get("description"),
            text=yaml_content,
            annotations=data.get("metadata", {}).get("annotations", {}),
            inputs=data.get("inputs", []),
            outputs=data.get("outputs", []),
            implementation=data.get("implementation"),
            metadata=data.get("metadata"),
        )

    @classmethod
    def from_spec(cls, spec: dict[str, Any]) -> Any:
        """Create from an inline component spec dict."""

        annotations = spec.get("metadata", {}).get("annotations", {})
        return cls(
            data=spec,
            name=spec.get("name", ""),
            description=spec.get("description"),
            annotations=annotations,
            inputs=spec.get("inputs", []),
            outputs=spec.get("outputs", []),
            implementation=spec.get("implementation"),
            metadata=spec.get("metadata"),
        )

    def __bool__(self) -> bool:
        return bool(getattr(self, "data", None))

    @property
    def search_names(self) -> list[str]:
        """Names to use for searching, including the official-name variant."""

        name = getattr(self, "name", "") or ""
        return [name, _add_official_prefix(name)]

    @property
    def stripped_spec(self) -> dict[str, Any] | None:
        """Component data with bulky annotations and implementation removed."""

        data = getattr(self, "data", None)
        if not data:
            return None
        result = dict(data)
        result.pop("implementation", None)
        annotations = result.get("metadata", {}).get("annotations", {})
        if annotations:
            result["metadata"] = dict(result["metadata"])
            result["metadata"]["annotations"] = {
                key: value
                for key, value in annotations.items()
                if key not in self._STRIP_ANNOTATION_KEYS
            }
        return result

    def strip_implementation(self, *, keep_graph: bool = False) -> None:
        """Remove implementation details in-place."""

        self.text = None
        if keep_graph:
            if self.implementation:
                _strip_text_from_graph(self.implementation)
        else:
            self.implementation = None
            self.data.pop("implementation", None)

    def to_yaml(self) -> str:
        """Convert component data back to YAML."""

        return utils.dump_yaml(self.data)

    def save_to_file(self, file_path: str) -> None:
        """Write component data to a YAML file."""

        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w") as f:
            f.write(self.to_yaml())

    def update_fields(
        self,
        git_remote_sha: str | None = None,
        git_remote_branch: str | None = None,
        git_remote_url: str | None = None,
        image: str | None = None,
        component_yaml_path: str | None = None,
    ) -> Any:
        """Update publishing metadata in-place and return ``self``."""

        self.data.setdefault("metadata", {}).setdefault("annotations", {})
        annotations = self.data["metadata"]["annotations"]
        annotations["published_at"] = datetime.now(timezone.utc).isoformat()

        if git_remote_sha:
            annotations.setdefault("git_remote_sha", git_remote_sha)
        if git_remote_branch:
            annotations.setdefault("git_remote_branch", git_remote_branch)
        if git_remote_url:
            annotations.setdefault("git_remote_url", git_remote_url)
        if component_yaml_path:
            utils.set_component_yaml_path(component_yaml_path, annotations, overwrite=False)

        if "version" in self.data:
            annotations["version"] = str(self.data.pop("version"))
        if "updated_at" in self.data:
            annotations["updated_at"] = str(self.data.pop("updated_at"))

        if image:
            self.data.setdefault("implementation", {}).setdefault("container", {})["image"] = image
            self.implementation = self.data["implementation"]
        self.metadata = self.data.get("metadata")
        self.annotations = annotations
        return self

    def fetch_from_url(self, url: str, timeout: int = 10) -> bool:
        """Fetch and parse component YAML from a URL into this model."""

        import httpx

        try:
            response = httpx.get(url, timeout=timeout)
            response.raise_for_status()
            self.text = response.text
            self.data = yaml.safe_load(response.text)
            self.name = self.data.get("name", "")
            self.description = self.data.get("description")
            self.metadata = self.data.get("metadata")
            self.annotations = self.data.get("metadata", {}).get("annotations", {})
            self.inputs = self.data.get("inputs", [])
            self.outputs = self.data.get("outputs", [])
            self.implementation = self.data.get("implementation")
            self.version = self.annotations.get("version")
            return True
        except Exception:
            return False

    def ensure_digest(self) -> str | None:
        """Compute and store a digest if one is not already present."""

        if getattr(self, "digest", None):
            return self.digest
        from tangle_cli.utils import compute_spec_digest, compute_text_digest

        if getattr(self, "text", None):
            self.digest = compute_text_digest(self.text)
        elif getattr(self, "data", None):
            self.digest = compute_spec_digest(self.data)
        return self.digest or None



class GetGraphExecutionStateResponseExtensions:
    """Convenience properties for graph execution state responses."""

    @property
    def per_execution(self) -> dict[str, dict[str, int]]:
        return cast(
            dict[str, dict[str, int]],
            getattr(self, "child_execution_status_stats", None) or {},
        )

    @property
    def status_totals(self) -> dict[str, int]:
        totals: dict[str, int] = {}
        for status_counts in self.per_execution.values():
            for status, count in status_counts.items():
                totals[status] = totals.get(status, 0) + count
        return totals

    @property
    def failed_execution_ids(self) -> list[str]:
        return [
            execution_id
            for execution_id, status_counts in self.per_execution.items()
            if status_counts.get("FAILED", 0) > 0
            or status_counts.get("SYSTEM_ERROR", 0) > 0
        ]


MODEL_EXTENSIONS = {
    "ComponentSpec": "ComponentSpecExtensions",
    "GetGraphExecutionStateResponse": "GetGraphExecutionStateResponseExtensions",
}
