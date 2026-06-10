"""
API-contract dataclasses for the Tangle (Oasis) Cloud Pipelines API.

These dataclasses model the shapes of HTTP request/response bodies on the
Tangle API — ``PipelineRun``, ``ExecutionDetails``, ``ComponentSpec``,
etc. They are used by wrapper packages and OpenAPI-backed client helpers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import yaml

import tangle_cli.utils as utils

# ---- Helpers ---------------------------------------------------------------


def _strip_text_from_graph(implementation: dict[str, Any]) -> None:
    """Recursively remove ``text`` from ``componentRef`` objects in a graph implementation."""
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


def add_official_prefix(name):
    """
    Add the [Official] prefix to a component name if not already present.

    Args:
        name: The original component name

    Returns:
        The name with [Official] prefix
    """
    if name and not name.startswith("[Official]"):
        return f"[Official] {name}"
    return name


# ---- Execution / Run dataclasses -------------------------------------------


@dataclass
class GraphExecutionState:
    """Response from GET /api/executions/{id}/state.

    Maps each child execution ID to a dict of status -> count.
    Example::

        GraphExecutionState(child_execution_status_stats={
            "019c8b46508e751207fc": {"SUCCEEDED": 1},
            "019c8b46508e76e607fd": {"RUNNING": 2, "SUCCEEDED": 3},
        })
    """
    child_execution_status_stats: dict[str, dict[str, int]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GraphExecutionState:
        return cls(
            child_execution_status_stats=data.get("child_execution_status_stats", {}),
        )

    @property
    def status_totals(self) -> dict[str, int]:
        """Aggregate counts across all child executions."""
        totals: dict[str, int] = {}
        for status_counts in self.child_execution_status_stats.values():
            for status, count in status_counts.items():
                totals[status] = totals.get(status, 0) + count
        return totals

    @property
    def failed_execution_ids(self) -> list[str]:
        """Execution IDs that have at least one FAILED or SYSTEM_ERROR task."""
        return [
            exec_id
            for exec_id, status_counts in self.child_execution_status_stats.items()
            if status_counts.get("FAILED", 0) > 0
            or status_counts.get("SYSTEM_ERROR", 0) > 0
        ]


@dataclass
class PipelineRun:
    """Response from GET /api/pipeline_runs/{id}."""
    id: str
    root_execution_id: str | None
    created_at: str | None = None
    created_by: str | None = None
    annotations: dict[str, str] | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PipelineRun:
        return cls(
            id=data["id"],
            root_execution_id=data.get("root_execution_id"),
            created_at=data.get("created_at"),
            created_by=data.get("created_by"),
            annotations=data.get("annotations"),
            raw=data,
        )


@dataclass
class TaskSpec:
    """A task within a pipeline execution graph.

    Recursive: a graph task contains child TaskSpecs via ``graph_tasks``.
    Leaf tasks have a container implementation instead.
    """
    name: str | None = None
    component_spec: ComponentSpec | None = None
    arguments: dict[str, str] = field(default_factory=dict)
    graph_tasks: dict[str, TaskSpec] = field(default_factory=dict)
    annotations: dict[str, str] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskSpec:
        """Parse a task_spec dict (the shape returned by the API)."""
        spec = data.get("componentRef", {}).get("spec", {})
        graph = spec.get("implementation", {}).get("graph", {})
        raw_tasks = graph.get("tasks", {})

        graph_tasks: dict[str, TaskSpec] = {}
        for task_name, task_data in raw_tasks.items():
            graph_tasks[task_name] = TaskSpec.from_dict(task_data)

        return cls(
            name=spec.get("name"),
            component_spec=ComponentSpec.from_spec(spec) if spec else None,
            arguments=data.get("arguments", {}),
            graph_tasks=graph_tasks,
            annotations=data.get("annotations", {}),
            raw=data,
        )

    @property
    def digest(self) -> str | None:
        """Component digest from componentRef."""
        return self.raw.get("componentRef", {}).get("digest")

    @property
    def inputs(self) -> list[dict[str, Any]]:
        """Component inputs."""
        return self.component_spec.inputs if self.component_spec else []

    @property
    def outputs(self) -> list[dict[str, Any]]:
        """Component outputs."""
        return self.component_spec.outputs if self.component_spec else []

    @property
    def execution_id(self) -> str | None:
        """Execution ID injected by ``_enrich_execution_tree``."""
        return self.raw.get("execution_id")

    @property
    def execution_input_artifacts(self) -> dict[str, str]:
        """Input artifact IDs injected by ``_enrich_execution_tree``."""
        return self.raw.get("input_artifacts", {})

    @property
    def execution_output_artifacts(self) -> dict[str, str]:
        """Output artifact IDs injected by ``_enrich_execution_tree``."""
        return self.raw.get("output_artifacts", {})

    @property
    def is_graph(self) -> bool:
        """True if this task is a subgraph (has child tasks)."""
        return len(self.graph_tasks) > 0

    def strip_implementations(self) -> None:
        """Remove container implementation blocks recursively.

        Graph structure (tasks, arguments, connections) is preserved.
        Only leaf container/code blocks are stripped.  ``text`` fields
        (raw YAML containing full implementations) are stripped at every
        level to avoid leaking implementation details.
        """
        if self.is_graph:
            # Graph component: keep implementation dict but strip text fields
            if self.component_spec:
                self.component_spec.strip_implementation(keep_graph=True)
            for child in self.graph_tasks.values():
                child.strip_implementations()
        else:
            if self.component_spec:
                self.component_spec.strip_implementation()


@dataclass
class ExecutionDetails:
    """Response from GET /api/executions/{id}/details."""
    id: str
    task_spec: TaskSpec = field(default_factory=TaskSpec)
    child_executions: dict[str, ExecutionDetails] = field(default_factory=dict)
    pipeline_run_id: str | None = None
    input_artifacts: dict[str, str] = field(default_factory=dict)
    output_artifacts: dict[str, str] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutionDetails:
        return cls(
            id=data.get("id", ""),
            task_spec=TaskSpec.from_dict(data.get("task_spec", {})),
            pipeline_run_id=data.get("pipeline_run_id"),
            input_artifacts={k: v["id"] for k, v in data.get("input_artifacts", {}).items() if "id" in v},
            output_artifacts={k: v["id"] for k, v in data.get("output_artifacts", {}).items() if "id" in v},
            raw=data,
        )

    def strip_implementations(self) -> None:
        """Remove implementation blocks from all component specs in-place."""
        self.task_spec.strip_implementations()
        for child in self.child_executions.values():
            child.strip_implementations()

    @property
    def tasks(self) -> dict[str, TaskSpec]:
        """Shortcut to the root task_spec's graph_tasks."""
        return self.task_spec.graph_tasks


# ---- Container state -------------------------------------------------------


@dataclass
class KubernetesDebugInfo:
    """Kubernetes debug info from container state."""
    pod_name: str | None = None
    namespace: str | None = None
    log_uri: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KubernetesDebugInfo:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class KubernetesJobInfo:
    """Kubernetes job info from container state (debug_info.kubernetes_job)."""
    job_name: str | None = None
    namespace: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KubernetesJobInfo:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class DebugInfo:
    """Debug info from container state (mirrors debug_info in the API response)."""
    kubernetes: KubernetesDebugInfo | None = None
    kubernetes_job: KubernetesJobInfo | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DebugInfo:
        k8s_data = data.get("kubernetes", {})
        job_data = data.get("kubernetes_job", {})
        return cls(
            kubernetes=KubernetesDebugInfo.from_dict(k8s_data) if k8s_data else None,
            kubernetes_job=KubernetesJobInfo.from_dict(job_data) if job_data else None,
        )


@dataclass
class ContainerState:
    """Response from GET /api/executions/{id}/container_state.

    Extracts key fields for debugging; the full Kubernetes debug info
    (pod spec, status, etc.) is available via ``debug_info.kubernetes`` and ``raw``.
    """
    status: str = "UNKNOWN"
    exit_code: int | None = None
    started_at: str | None = None
    ended_at: str | None = None
    pod_name: str | None = None
    namespace: str | None = None
    debug_info: DebugInfo | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContainerState:
        debug_data = data.get("debug_info", {})
        fields = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        if debug_data:
            fields["debug_info"] = DebugInfo.from_dict(debug_data)
        fields["raw"] = data

        # Resolve pod_name: debug_info.kubernetes.pod_name > debug_info.kubernetes_job.job_name
        if not fields.get("pod_name"):
            di = fields.get("debug_info")
            k8s = di.kubernetes if di else None
            if k8s and k8s.pod_name:
                fields["pod_name"] = k8s.pod_name
                if not fields.get("namespace"):
                    fields["namespace"] = k8s.namespace
            else:
                job = di.kubernetes_job if di else None
                if job and job.job_name:
                    fields["pod_name"] = job.job_name

        return cls(**fields)


# ---- Composite -------------------------------------------------------------


@dataclass
class RunDetails:
    """Combined pipeline run + execution details from get_run_details."""
    run: PipelineRun
    execution: ExecutionDetails | None = None
    annotations: dict[str, str | None] | None = None
    execution_state: GraphExecutionState | None = None


# ---- Artifacts -------------------------------------------------------------


@dataclass
class ArtifactComponentQuery:
    """Filter for selecting artifacts by component name or digest."""
    name: str | None = None
    digest: str | None = None
    outputs: list[str] = field(default_factory=list)


@dataclass
class ArtifactInfo:
    """Resolved artifact with gs:// URI from GET /api/artifacts/{id}."""
    id: str
    uri: str
    key: str = ""
    total_size: int = 0
    is_dir: bool = False
    hash: str | None = None
    created_at: str | None = None
    error: str | None = None
    local_path: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any], key: str = "") -> ArtifactInfo:
        ad = data.get("artifact_data", {})
        return cls(
            id=data.get("id", ""),
            uri=ad.get("uri", ""),
            key=key,
            total_size=ad.get("total_size", 0),
            is_dir=ad.get("is_dir", False),
            hash=ad.get("hash"),
            created_at=ad.get("created_at"),
        )


# ---- Users / secrets -------------------------------------------------------


@dataclass
class UserInfo:
    """Current authenticated user from /api/users/me."""
    id: str
    permissions: list[str]


@dataclass
class SecretInfo:
    """Secret metadata from /api/secrets/ endpoints."""
    secret_name: str
    created_at: str
    updated_at: str
    expires_at: str | None = None
    description: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SecretInfo:
        return cls(
            secret_name=data["secret_name"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            expires_at=data.get("expires_at"),
            description=data.get("description"),
        )


# ---- Components ------------------------------------------------------------


@dataclass
class ComponentSpec:
    """Component specification extracted from YAML content or API response."""

    data: dict = field(default_factory=dict)  # The full parsed YAML data structure
    version: str | None = None
    name: str = ""
    description: str | None = None
    digest: str = ""
    text: str | None = None
    annotations: dict[str, str] = field(default_factory=dict)
    inputs: list[dict[str, Any]] = field(default_factory=list)
    outputs: list[dict[str, Any]] = field(default_factory=list)
    implementation: dict[str, Any] | None = None

    # ---- factories ----

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ComponentSpec:
        """Create from a raw API response (``/api/components/{digest}``).

        Parses the ``text`` field into ``data`` when no ``spec`` key is present.
        """
        spec = data.get("spec")
        text = data.get("text")
        if spec is None and text:
            spec = yaml.safe_load(text)
        spec = spec or {}
        ann = spec.get("metadata", {}).get("annotations", {})
        return cls(
            digest=data.get("digest", ""),
            data=spec,
            text=text,
            name=spec.get("name", ""),
            version=ann.get("version"),
            description=spec.get("description"),
            annotations=ann,
            inputs=spec.get("inputs", []),
            outputs=spec.get("outputs", []),
            implementation=spec.get("implementation"),
        )

    @staticmethod
    def from_yaml_file(yaml_path: str) -> ComponentSpec:
        """Load and extract component specification from a YAML file.

        Raises:
            FileNotFoundError: If the file doesn't exist
            ValueError: If component name is missing or file is empty
        """
        with open(yaml_path) as f:
            yaml_content = f.read()
        return ComponentSpec.from_yaml(yaml_content)

    @staticmethod
    def from_yaml(
        yaml_content: str,
        annotations: dict[str, str] | None = None,
    ) -> ComponentSpec:
        """Extract component specification from YAML content.

        Args:
            yaml_content: YAML string content
            annotations: Optional annotations to add before extracting version

        Raises:
            ValueError: If name is not found in YAML
        """
        data = utils.parse_yaml_string(yaml_content)

        if not data:
            raise ValueError("Unable to parse YAML content")

        # Apply custom annotations before extracting version
        if annotations:
            if 'metadata' not in data:
                data['metadata'] = {}
            if 'annotations' not in data['metadata']:
                data['metadata']['annotations'] = {}
            data['metadata']['annotations'].update(annotations)

        name = data.get('name')
        if not name:
            raise ValueError("Component name is required but not found in YAML")

        version = utils.get_version_from_data(data)
        if not version:
            version = None

        return ComponentSpec(
            data=data,
            version=version,
            name=name,
            description=data.get('description'),
            text=yaml_content,
            annotations=data.get('metadata', {}).get('annotations', {}),
            inputs=data.get('inputs', []),
            outputs=data.get('outputs', []),
            implementation=data.get('implementation'),
        )

    @classmethod
    def from_spec(cls, spec: dict[str, Any]) -> ComponentSpec:
        """Create from an inline component spec dict (e.g. from an execution task_spec)."""
        ann = spec.get("metadata", {}).get("annotations", {})
        return cls(
            data=spec,
            name=spec.get("name", ""),
            description=spec.get("description"),
            annotations=ann,
            inputs=spec.get("inputs", []),
            outputs=spec.get("outputs", []),
            implementation=spec.get("implementation"),
        )

    def __bool__(self) -> bool:
        return bool(self.data)

    # ---- properties ----

    @property
    def search_names(self) -> list[str]:
        """Get names to use for searching (both original and with [Official] prefix)."""
        return [self.name, add_official_prefix(self.name)]

    _STRIP_ANNOTATION_KEYS = {"python_original_code", "python_dependencies"}

    def strip_implementation(self, *, keep_graph: bool = False) -> None:
        """Remove implementation details in-place.

        Args:
            keep_graph: If True, preserve the graph structure but strip
                ``text`` from nested ``componentRef`` objects.  If False
                (default), remove the implementation block entirely.
        """
        self.text = None
        if keep_graph:
            if self.implementation:
                _strip_text_from_graph(self.implementation)
        else:
            self.implementation = None
            self.data.pop("implementation", None)

    @property
    def stripped_spec(self) -> dict[str, Any] | None:
        """Data with bulky annotations and implementation blocks removed.

        Returns a shallow copy — does not mutate ``self``.
        """
        if not self.data:
            return None
        result = dict(self.data)
        result.pop("implementation", None)
        annotations = result.get("metadata", {}).get("annotations", {})
        if annotations:
            result["metadata"] = dict(result["metadata"])
            result["metadata"]["annotations"] = {
                k: v for k, v in annotations.items() if k not in self._STRIP_ANNOTATION_KEYS
            }
        return result

    # ---- serialisation ----

    def to_yaml(self) -> str:
        """Convert the component data back to YAML string."""
        return utils.dump_yaml(self.data)

    def save_to_file(self, file_path: str) -> None:
        """Save the component spec to a YAML file."""
        import os
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, 'w') as f:
            f.write(self.to_yaml())

    # ---- mutation helpers ----

    def update_fields(
        self,
        git_remote_sha=None,
        git_remote_branch=None,
        git_remote_url=None,
        image=None,
        component_yaml_path=None,
    ):
        """Update component spec with publishing metadata (in-place).

        Returns self for method chaining.
        """
        from datetime import datetime, timezone

        if 'metadata' not in self.data:
            self.data['metadata'] = {}
        if 'annotations' not in self.data['metadata']:
            self.data['metadata']['annotations'] = {}

        self.data['metadata']['annotations']['published_at'] = datetime.now(timezone.utc).isoformat()

        annotations = self.data['metadata']['annotations']
        if git_remote_sha:
            annotations.setdefault('git_remote_sha', git_remote_sha)
        if git_remote_branch:
            annotations.setdefault('git_remote_branch', git_remote_branch)
        if git_remote_url:
            annotations.setdefault('git_remote_url', git_remote_url)
        if component_yaml_path:
            utils.set_component_yaml_path(component_yaml_path, annotations, overwrite=False)

        if 'version' in self.data:
            version = self.data.pop('version')
            self.data['metadata']['annotations']['version'] = str(version)
        if 'updated_at' in self.data:
            updated_at = self.data.pop('updated_at')
            self.data['metadata']['annotations']['updated_at'] = str(updated_at)

        if image:
            if 'implementation' not in self.data:
                self.data['implementation'] = {}
            if 'container' not in self.data['implementation']:
                self.data['implementation']['container'] = {}
            self.data['implementation']['container']['image'] = image

        return self

    def fetch_from_url(self, url: str, timeout: int = 10) -> bool:
        """Fetch spec from a URL, populating ``data`` and ``text``.

        Returns True if the fetch succeeded.
        """
        import httpx

        try:
            response = httpx.get(url, timeout=timeout)
            response.raise_for_status()
            self.text = response.text
            self.data = yaml.safe_load(response.text)
            self.name = self.data.get("name", "")
            return True
        except Exception:
            return False

    def ensure_digest(self) -> str | None:
        """Compute and set ``digest`` if missing. Returns the digest.

        Resolution order: existing digest > hash of raw text > hash of data.
        """
        if self.digest:
            return self.digest
        from tangle_cli.utils import compute_spec_digest, compute_text_digest

        if self.text:
            self.digest = compute_text_digest(self.text)
        elif self.data:
            self.digest = compute_spec_digest(self.data)
        return self.digest or None


@dataclass
class ComponentInfo:
    """Merged view of a published component: spec + publication metadata."""

    name: str = ""
    digest: str | None = None
    version: str | None = None
    published_by: str | None = None
    deprecated: bool = False
    superseded_by: str | None = None
    description: str = ""
    component_spec: ComponentSpec | None = None
    spec_error: str | None = None

    @classmethod
    def from_dict(cls, pub: dict[str, Any]) -> ComponentInfo:
        """Create from a published_components API response entry."""
        return cls(
            name=pub.get("name", ""),
            digest=pub.get("digest"),
            version=pub.get("version"),
            published_by=pub.get("published_by"),
            deprecated=pub.get("deprecated", False),
            superseded_by=pub.get("superseded_by"),
            description=pub.get("description", ""),
        )

    def to_dict(self, strip_spec: bool = True) -> dict[str, Any]:
        """Serialize to a dict, omitting None/empty optional fields.

        Args:
            strip_spec: If True (default), strip bulky annotations and
                implementation blocks from the component spec.
        """
        d: dict[str, Any] = {"digest": self.digest, "version": self.version}
        if self.published_by is not None:
            d["published_by"] = self.published_by
        d["deprecated"] = self.deprecated
        if self.superseded_by is not None:
            d["superseded_by"] = self.superseded_by
        if self.description:
            d["description"] = self.description
        if self.component_spec is not None:
            spec = self.component_spec.stripped_spec if strip_spec else self.component_spec.data
            if spec is not None:
                d["spec"] = spec
        if self.spec_error is not None:
            d["spec_error"] = self.spec_error
        return d


# ---- Pagination ------------------------------------------------------------


@dataclass
class PageChunk:
    """Metadata for a single page of search results."""

    rows: list[dict[str, Any]]
    page_token: str | None
    next_page_token: str | None
    ui_filter_url: str
    next_ui_filter_url: str | None
