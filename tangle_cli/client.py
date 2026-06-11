"""Static public Tangle API client.

``TangleApiClient`` is the stable wrapper class consumed by downstream tools.
Endpoint methods are generated offline into :mod:`tangle_cli.generated.operations`
from the checked-in OpenAPI snapshot; handwritten methods in this file keep the
higher-level compatibility helpers that downstream callers use.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, is_dataclass
from typing import Any
from urllib.parse import quote, urljoin

import requests

from .api_transport import (
    DEFAULT_TIMEOUT_SECONDS,
    _normalize_base_url,
    _request_headers,
    default_base_url,
    default_token,
)
from .generated.operations import GeneratedOperationsMixin
from .logger import Logger, _null_logger
from .models import (
    ArtifactInfo,
    ComponentInfo,
    ComponentSpec,
    ContainerState,
    ExecutionDetails,
    GraphExecutionState,
    PipelineRun,
    RunDetails,
    SecretInfo,
    TaskSpec,
    UserInfo,
)


class TangleApiClient(GeneratedOperationsMixin):
    """Single public API wrapper for Tangle backends.

    The constructor keeps the historical ``tangle-deploy`` shape while also
    accepting the auth/header knobs used by the dynamic OpenAPI client. No
    OpenAPI schema is loaded at runtime; all endpoint wrappers are checked in.
    """

    def __init__(
        self,
        base_url: str | None = None,
        *,
        logger: Logger | None = None,
        verbose: bool = False,
        headers: Mapping[str, str] | None = None,
        token: str | None = None,
        auth_header: str | None = None,
        header: list[str] | str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = _normalize_base_url(base_url or default_base_url())
        self.logger = logger or _null_logger
        self.verbose = verbose
        self.headers = dict(headers or {})
        self.token = token
        self.auth_header = auth_header
        self.header = header
        self.timeout = timeout
        self.session = session or requests.Session()

    def set_verbose(self, enabled: bool) -> None:
        """Enable or disable request logging."""

        self.verbose = enabled

    def _refresh_auth(self) -> None:
        """Hook for subclasses to refresh auth before/retry after a request.

        Subclasses commonly mutate ``self.headers`` or session state here. The
        base implementation intentionally does nothing.
        """

    def _make_request(
        self,
        method: str,
        path: str,
        params: Mapping[str, Any] | None = None,
        json_data: Any = None,
        **kwargs: Any,
    ) -> requests.Response:
        """Issue an HTTP request and return the raw ``requests.Response``.

        This method preserves the subclass extension point used by
        ``tangle-deploy``: auth can be refreshed by overriding
        :meth:`_refresh_auth`, and callers that need streaming can pass standard
        ``requests`` keyword arguments such as ``stream=True``.
        """

        if "json" in kwargs and json_data is None:
            json_data = kwargs.pop("json")
        timeout = kwargs.pop("timeout", self.timeout)
        extra_headers = kwargs.pop("headers", None)
        request_headers = self._headers(extra_headers)
        url = self._url(path)
        clean_params = self._clean_mapping(params)

        self._refresh_auth()
        # Refresh may mutate headers/session state, so build headers again.
        request_headers = self._headers(extra_headers)
        if self.verbose:
            self.logger.info(f"{method.upper()} {url}")
        response = self.session.request(
            method.upper(),
            url,
            params=clean_params,
            json=json_data,
            headers=request_headers,
            timeout=timeout,
            **kwargs,
        )
        if response.status_code == 401:
            self._refresh_auth()
            request_headers = self._headers(extra_headers)
            response = self.session.request(
                method.upper(),
                url,
                params=clean_params,
                json=json_data,
                headers=request_headers,
                timeout=timeout,
                **kwargs,
            )
        return response

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        path_params: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
        json_data: Any = None,
        response_model: Any = None,
    ) -> Any:
        formatted_path = self._format_path(path, path_params)
        response = self._make_request(method, formatted_path, params=params, json_data=json_data)
        response.raise_for_status()
        data = self._decode_response(response)
        if response_model is not None and isinstance(data, dict):
            return response_model.from_dict(data)
        return data

    def _headers(self, extra_headers: Mapping[str, str] | None = None) -> dict[str, str]:
        headers = dict(self.headers)
        if extra_headers:
            headers.update({name: str(value) for name, value in extra_headers.items()})
        return _request_headers(
            self.token or default_token(),
            self.header,
            self.auth_header,
            headers,
        )

    def _url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return urljoin(self.base_url.rstrip("/") + "/", path.lstrip("/"))

    @staticmethod
    def _format_path(path: str, path_params: Mapping[str, Any] | None = None) -> str:
        if not path_params:
            return path
        for name, value in path_params.items():
            path = path.replace("{" + name + "}", quote(str(value), safe=""))
        return path

    @staticmethod
    def _clean_mapping(values: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if not values:
            return None
        cleaned = {key: value for key, value in values.items() if value is not None}
        return cleaned or None

    @staticmethod
    def _decode_response(response: requests.Response) -> Any:
        if response.status_code == 204 or not response.content:
            return None
        content_type = response.headers.get("Content-Type", "")
        if "json" in content_type.lower():
            return response.json()
        try:
            return response.json()
        except ValueError:
            return response.text

    # ---- Compatibility helpers consumed by tangle-deploy -----------------

    def get_artifact(self, artifact_id: str) -> ArtifactInfo:
        return ArtifactInfo.from_dict(_to_plain(self.artifacts_get(artifact_id)))

    def get_artifact_signed_url(self, artifact_id: str) -> str | dict[str, Any] | None:
        data = _to_plain(self.artifacts_signed_artifact_url(artifact_id))
        return data.get("signed_url") if isinstance(data, dict) else data

    def get_execution_details(self, execution_id: str) -> ExecutionDetails:
        details = ExecutionDetails.from_dict(_to_plain(self.executions_details(execution_id)))
        self._enrich_execution_tree(details)
        return details

    def get_execution_graph_state(self, execution_id: str) -> GraphExecutionState:
        return GraphExecutionState.from_dict(_to_plain(self.executions_graph_execution_state(execution_id)))

    def get_execution_graph_state_alt(self, execution_id: str) -> GraphExecutionState:
        return GraphExecutionState.from_dict(_to_plain(self.executions_state(execution_id)))

    def get_execution_container_state(self, execution_id: str) -> ContainerState:
        return ContainerState.from_dict(_to_plain(self.executions_container_state(execution_id)))

    def get_execution_artifacts(self, execution_id: str) -> dict[str, Any]:
        return _to_plain(self.executions_artifacts(execution_id))

    def get_execution_container_log(self, execution_id: str) -> str | dict[str, Any] | None:
        data = _to_plain(self.executions_container_log(execution_id))
        if isinstance(data, dict) and "log_text" in data:
            return data.get("log_text")
        return data

    def stream_execution_container_log(self, execution_id: str) -> requests.Response:
        response = self._make_request(
            "GET",
            self._format_path(
                "/api/executions/{id}/stream_container_log",
                {"id": execution_id},
            ),
            stream=True,
        )
        response.raise_for_status()
        return response

    def list_pipeline_runs(
        self,
        page_token: str | None = None,
        filter: str | None = None,
        filter_query: str | None = None,
        include_pipeline_names: bool = False,
        include_execution_stats: bool = False,
    ) -> dict[str, Any]:
        return _to_plain(
            self.pipeline_runs_list(
                page_token=page_token,
                filter=filter,
                filter_query=filter_query,
                include_pipeline_names=include_pipeline_names,
                include_execution_stats=include_execution_stats,
            )
        )

    def create_pipeline_run(
        self,
        root_task: Any,
        components: list[Any] | None = None,
        annotations: dict[str, Any] | None = None,
    ) -> PipelineRun:
        body = {
            "root_task": _to_plain(root_task),
            "components": _to_plain(components),
            "annotations": annotations,
        }
        return PipelineRun.from_dict(
            self._request_json("POST", "/api/pipeline_runs/", json_data=self._clean_mapping(body))
        )

    def get_pipeline_run(self, run_id: str) -> PipelineRun:
        return PipelineRun.from_dict(_to_plain(self.pipeline_runs_get(run_id)))

    def cancel_pipeline_run(self, run_id: str) -> None:
        self.pipeline_runs_cancel(run_id)
        return None

    def list_pipeline_run_annotations(self, run_id: str) -> dict[str, str | None]:
        data = self.pipeline_runs_annotations(run_id)
        return data if isinstance(data, dict) else {}

    def set_pipeline_run_annotation(
        self,
        run_id: str,
        key: str,
        value: str | None = None,
    ) -> None:
        self.pipeline_runs_put_annotations(run_id, key, value=value)
        return None

    def delete_pipeline_run_annotation(self, run_id: str, key: str) -> None:
        self.pipeline_runs_delete_annotations(run_id, key)
        return None

    def get_current_user(self) -> UserInfo | None:
        data = _to_plain(self.users_me())
        if data is None:
            return None
        return UserInfo(id=data.get("id"), permissions=data.get("permissions", []))

    def get_component(self, digest: str) -> ComponentSpec:
        return ComponentSpec.from_dict(_to_plain(self.components_get(digest)))

    def get_component_spec(self, digest: str) -> ComponentSpec:
        return self.get_component(digest)

    def resolve_digest(self, digest: str) -> str:
        """Resolve a component digest/name, following deprecation successors."""

        current = digest
        seen: set[str] = set()

        while current not in seen:
            seen.add(current)
            matches = self.list_published_components(include_deprecated=True, digest=current)
            if not matches:
                matches = self.list_published_components(
                    include_deprecated=True,
                    name_substring=current,
                )
            if len(matches) != 1:
                return current

            component = matches[0]
            resolved = str(component.get("digest") or current)
            successor = component.get("superseded_by")
            if component.get("deprecated") and successor:
                current = str(successor)
                continue
            return resolved

        return current

    def list_published_components(
        self,
        include_deprecated: bool = False,
        name_substring: str | None = None,
        published_by_substring: str | None = None,
        digest: str | None = None,
    ) -> list[dict[str, Any]]:
        data = _to_plain(
            self.published_components_list(
                include_deprecated=include_deprecated,
                name_substring=name_substring,
                published_by_substring=published_by_substring,
                digest=digest,
            )
        )
        if isinstance(data, dict):
            return list(data.get("published_components") or [])
        return list(data or [])

    def list_published_component_infos(
        self,
        include_deprecated: bool = False,
        name_substring: str | None = None,
        published_by_substring: str | None = None,
        digest: str | None = None,
        *,
        fetch_specs: bool = False,
    ) -> list[ComponentInfo]:
        infos = [
            ComponentInfo.from_dict(component)
            for component in self.list_published_components(
                include_deprecated=include_deprecated,
                name_substring=name_substring,
                published_by_substring=published_by_substring,
                digest=digest,
            )
        ]
        if fetch_specs:
            for info in infos:
                if not info.digest:
                    continue
                try:
                    info.component_spec = self.get_component_spec(info.digest)
                except Exception as exc:  # pragma: no cover - best-effort enrichment
                    info.spec_error = str(exc)
        return infos

    def find_existing_components(
        self,
        components: Iterable[ComponentSpec | Mapping[str, Any]] | None = None,
        *,
        names: Iterable[str] | None = None,
        digests: Iterable[str] | None = None,
        include_deprecated: bool = False,
    ) -> dict[str, ComponentInfo]:
        """Find published components matching provided component specs/names/digests.

        The result is keyed by every useful identifier we can infer (digest and
        name), which keeps the helper compatible with callers that check either.
        """

        search_names = set(names or [])
        search_digests = set(digests or [])
        for component in components or []:
            data = _to_plain(component)
            if isinstance(component, ComponentSpec):
                search_names.update(name for name in component.search_names if name)
                if component.digest:
                    search_digests.add(component.digest)
            elif isinstance(data, dict):
                if data.get("name"):
                    search_names.add(str(data["name"]))
                if data.get("digest"):
                    search_digests.add(str(data["digest"]))

        found: dict[str, ComponentInfo] = {}
        for digest in search_digests:
            for info in self.list_published_component_infos(
                include_deprecated=include_deprecated,
                digest=digest,
            ):
                _index_component_info(found, info)
        for name in search_names:
            for info in self.list_published_component_infos(
                include_deprecated=include_deprecated,
                name_substring=name,
            ):
                if info.name == name:
                    _index_component_info(found, info)
        return found

    def publish_component(self, component_reference: dict[str, Any]) -> dict[str, Any]:
        return _to_plain(
            self._request_json(
                "POST",
                "/api/published_components/",
                json_data=_to_plain(component_reference),
            )
        )

    def update_published_component(
        self,
        digest: str,
        deprecated: bool | None = None,
        superseded_by: str | None = None,
    ) -> dict[str, Any]:
        return _to_plain(
            self.published_components_update(
                digest,
                deprecated=deprecated,
                superseded_by=superseded_by,
            )
        )

    def get_component_search_schema(self) -> dict[str, Any]:
        return _to_plain(
            self._request_json(
                "GET",
                "/api/published_components/experimental/search/schema",
            )
        )

    def search_components_v2(self, *, body: dict[str, Any]) -> dict[str, Any]:
        return _to_plain(
            self._request_json(
                "POST",
                "/api/published_components/experimental/search",
                json_data=body,
            )
        )

    def get_run_details(
        self,
        run_id: str,
        include_implementations: bool = False,
        include_annotations: bool = False,
        include_execution_state: bool = False,
        execution_id: str | None = None,
    ) -> RunDetails:
        run = self.get_pipeline_run(run_id)
        root_execution_id = execution_id or run.root_execution_id
        execution = self.get_execution_details(root_execution_id) if root_execution_id else None
        if execution and not include_implementations:
            execution.strip_implementations()
        annotations = self.list_pipeline_run_annotations(run_id) if include_annotations else None
        execution_state = (
            self.get_execution_graph_state(root_execution_id)
            if include_execution_state and root_execution_id
            else None
        )
        return RunDetails(
            run=run,
            execution=execution,
            annotations=annotations,
            execution_state=execution_state,
        )

    def get_run_pipeline_spec(self, run_id: str) -> TaskSpec | None:
        details = self.get_run_details(run_id, include_implementations=True)
        return details.execution.task_spec if details.execution else None

    def list_secrets(self) -> list[SecretInfo]:
        data = _to_plain(self.secrets_list())
        rows = data.get("secrets", []) if isinstance(data, dict) else data or []
        return [SecretInfo.from_dict(row) for row in rows]

    def create_secret(
        self,
        secret_name: str,
        secret_value: str,
        description: str | None = None,
        expires_at: str | None = None,
    ) -> SecretInfo:
        return SecretInfo.from_dict(
            _to_plain(
                self.secrets_create(
                    secret_name=secret_name,
                    secret_value=secret_value,
                    description=description,
                    expires_at=expires_at,
                )
            )
        )

    def update_secret(
        self,
        secret_name: str,
        secret_value: str,
        description: str | None = None,
        expires_at: str | None = None,
    ) -> SecretInfo:
        return SecretInfo.from_dict(
            _to_plain(
                self.secrets_update(
                    secret_name=secret_name,
                    secret_value=secret_value,
                    description=description,
                    expires_at=expires_at,
                )
            )
        )

    def delete_secret(self, secret_name: str) -> None:
        self.secrets_delete(secret_name)
        return None

    def _enrich_execution_tree(self, execution: ExecutionDetails) -> None:
        child_ids = execution.raw.get("child_task_execution_ids") or {}
        if not isinstance(child_ids, dict):
            return
        for task_name, child_execution_id in child_ids.items():
            if not child_execution_id:
                continue
            child = ExecutionDetails.from_dict(_to_plain(self.executions_details(child_execution_id)))
            self._enrich_execution_tree(child)
            execution.child_executions[task_name] = child
            task = execution.task_spec.graph_tasks.get(task_name)
            if task is not None:
                task.raw["execution_id"] = child.id
                task.raw["input_artifacts"] = child.input_artifacts
                task.raw["output_artifacts"] = child.output_artifacts


def _to_plain(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return value.to_dict()
    if hasattr(value, "model_dump") and callable(value.model_dump):
        return value.model_dump(by_alias=True)
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, list):
        return [_to_plain(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_to_plain(item) for item in value)
    if isinstance(value, dict):
        return {key: _to_plain(item) for key, item in value.items()}
    return value


def _index_component_info(index: dict[str, ComponentInfo], info: ComponentInfo) -> None:
    if info.digest:
        index[info.digest] = info
    if info.name:
        index[info.name] = info


__all__ = ["TangleApiClient"]
