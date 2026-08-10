"""Static public Tangle API client.

``TangleApiClient`` is the stable wrapper class consumed by downstream tools.
Endpoint methods are generated offline into :mod:`tangle_api.generated.operations`
from the checked-in OpenAPI snapshot; handwritten methods in this file keep the
higher-level semantic helpers that downstream callers use.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping
from dataclasses import asdict, is_dataclass
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote, urljoin, urlparse

import requests

from .api_transport import (
    DEFAULT_TIMEOUT_SECONDS,
    _join_operation_url,
    _normalize_base_url,
    _request_headers,
    default_base_url,
    format_transport_error,
    log_http_exchange,
    tangle_verbose_enabled,
)
from tangle_api.generated.operations import GeneratedTangleApiOperations
from . import models as _cli_models
from .logger import Logger, _null_logger, get_default_logger
from .models import (
    ComponentInfo,
    ComponentSpec,
    GetExecutionInfoResponse,
    GraphExecutionState,
    PipelineRun,
    RunDetails,
    TaskSpec,
)


class TangleApiTransportError(requests.exceptions.RequestException):
    """A connection/timeout/TLS/proxy/stream failure carrying no HTTP response.

    The message is a single, credential-safe line. Subclassing
    ``requests.exceptions.RequestException`` keeps callers that already handle
    requests errors working unchanged, while the originating exception is chained
    as ``__cause__`` so programmatic hooks can still inspect the low-level cause.
    """


class TangleApiClient(GeneratedTangleApiOperations):
    """Single public API wrapper for Tangle backends.

    The constructor keeps the historical ``tangle-deploy`` shape while also
    accepting the auth/header knobs used by the dynamic-discovery client. No
    OpenAPI schema is loaded at runtime; all endpoint wrappers are checked in.
    """

    _REDIRECT_STATUSES = {301, 302, 303, 307, 308}
    _MAX_REDIRECTS = 5
    _MAX_RATE_LIMIT_RETRIES = 3
    _RATE_LIMIT_BACKOFF_SECONDS = 1.0
    _MAX_RETRY_AFTER_SECONDS = 60.0

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
        include_env_credentials: bool = True,
    ) -> None:
        self.base_url = _normalize_base_url(base_url or default_base_url())
        env_verbose = tangle_verbose_enabled()
        self.verbose = verbose or env_verbose
        self.logger = logger or (get_default_logger() if self.verbose else _null_logger)
        self.headers = dict(headers or {})
        self.token = token
        self.auth_header = auth_header
        self.header = header
        self.timeout = timeout
        self.session = session or requests.Session()
        self.include_env_credentials = include_env_credentials

    def _response_model(self, model_name: str, default: Any) -> Any:
        """Use CLI-composed models for generated operation deserialization."""

        return getattr(_cli_models, model_name, default)

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
        url = self._url(path)
        clean_params = self._clean_mapping(params)
        request_method = method.upper()

        self._refresh_auth()
        response = self._request_with_rate_limit_retries(
            request_method,
            url,
            params=clean_params,
            json_data=json_data,
            extra_headers=extra_headers,
            timeout=timeout,
            request_kwargs=kwargs,
        )
        if response.status_code == 401:
            self._refresh_auth()
            response = self._request_with_rate_limit_retries(
                request_method,
                url,
                params=clean_params,
                json_data=json_data,
                extra_headers=extra_headers,
                timeout=timeout,
                request_kwargs=kwargs,
            )
        return response

    def _send_request(
        self,
        method: str,
        path: str,
        params: Mapping[str, Any] | None = None,
        json_data: Any = None,
        **kwargs: Any,
    ) -> requests.Response:
        """Issue a request, converting an unhandled transport failure to a clean error.

        ``_make_request`` and every retry/redirect layer beneath it re-raise the
        original ``requests`` exception subtypes, so callers that manage their own
        retries -- and the transient-retry layer -- can classify and retry them.
        This public boundary is where a failure that survived all of those layers
        becomes a credential-safe :class:`TangleApiTransportError`. HTTP status
        errors carry a response and stay the caller's responsibility, so they
        propagate unchanged.
        """

        try:
            return self._make_request(method, path, params=params, json_data=json_data, **kwargs)
        except requests.exceptions.RequestException as exc:
            if getattr(exc, "response", None) is not None:
                raise
            raise TangleApiTransportError(
                format_transport_error(exc, method=method.upper(), url=self._safe_error_url(path)),
                request=getattr(exc, "request", None),
            ) from exc

    def _request_with_rate_limit_retries(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None,
        json_data: Any,
        extra_headers: Mapping[str, str] | None,
        timeout: float,
        request_kwargs: Mapping[str, Any],
    ) -> requests.Response:
        response: requests.Response | None = None
        for attempt in range(self._MAX_RATE_LIMIT_RETRIES + 1):
            response = self._request_with_same_origin_redirects(
                method,
                url,
                params=params,
                json_data=json_data,
                extra_headers=extra_headers,
                timeout=timeout,
                request_kwargs=request_kwargs,
            )
            if response.status_code != 429 or attempt == self._MAX_RATE_LIMIT_RETRIES:
                return response
            self._sleep_for_rate_limit(response, attempt)
        return response

    def _sleep_for_rate_limit(self, response: requests.Response, attempt: int) -> None:
        retry_after = response.headers.get("Retry-After")
        delay = self._retry_after_delay(retry_after)
        if delay is None:
            delay = self._RATE_LIMIT_BACKOFF_SECONDS * (2 ** attempt)
        delay = min(delay, self._MAX_RETRY_AFTER_SECONDS)
        if self.verbose:
            self.logger.info(f"429 rate limited; retrying in {delay:.1f}s")
        time.sleep(delay)

    @staticmethod
    def _retry_after_delay(value: str | None) -> float | None:
        if not value:
            return None
        try:
            return max(0.0, float(value))
        except ValueError:
            pass
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if retry_at.tzinfo is None:
            return None
        return max(0.0, retry_at.timestamp() - time.time())

    def _request_with_same_origin_redirects(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None,
        json_data: Any,
        extra_headers: Mapping[str, str] | None,
        timeout: float,
        request_kwargs: Mapping[str, Any],
    ) -> requests.Response:
        """Send one request, following only same-origin redirects.

        The client may carry custom auth headers/cookies in ``session.headers``.
        ``requests`` does not strip those custom credentials on cross-origin
        redirects, so redirects are handled manually and constrained to the
        original origin.
        """

        current_method = method
        current_url = url
        current_params = params
        current_json = json_data
        response: requests.Response | None = None

        for _ in range(self._MAX_REDIRECTS + 1):
            request_headers = self._headers(extra_headers)
            # Transport failures raised here propagate untouched so the enclosing
            # retry/rate-limit layers can classify them; conversion to a clean
            # TangleApiTransportError happens once, at the _send_request boundary.
            response = self.session.request(
                current_method,
                current_url,
                params=current_params,
                json=current_json,
                headers=request_headers,
                timeout=timeout,
                allow_redirects=False,
                **request_kwargs,
            )
            if self.verbose:
                log_http_exchange(
                    self.logger,
                    method=current_method,
                    url=current_url,
                    request_headers=request_headers,
                    request_body=current_json,
                    response_status=response.status_code,
                    response_headers=dict(response.headers),
                    response_body=response.text,
                )
            if response.status_code not in self._REDIRECT_STATUSES:
                return response

            location = response.headers.get("Location")
            if not location:
                return response

            next_url = urljoin(response.url, location)
            if not self._same_origin(response.url, next_url):
                raise requests.HTTPError(
                    f"Refusing to follow cross-origin redirect from {response.url} to {next_url}",
                    response=response,
                )

            try:
                response.close()
            except Exception:
                pass
            if response.status_code == 303 or (
                response.status_code in {301, 302} and current_method not in {"GET", "HEAD"}
            ):
                current_method = "GET"
                current_json = None
            current_url = next_url
            current_params = None

        raise requests.TooManyRedirects(
            f"Exceeded {self._MAX_REDIRECTS} redirects for {url}",
            response=response,
        )

    @staticmethod
    def _same_origin(left: str, right: str) -> bool:
        left_parts = urlparse(left)
        right_parts = urlparse(right)
        return (
            left_parts.scheme.lower() == right_parts.scheme.lower()
            and left_parts.netloc.lower() == right_parts.netloc.lower()
        )

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
        response = self._send_request(method, formatted_path, params=params, json_data=json_data)
        response.raise_for_status()
        data = self._decode_response(response)
        if response_model is not None and isinstance(data, dict):
            return response_model.from_dict(data)
        if response_model is not None and isinstance(data, list):
            return [
                response_model.from_dict(item) if isinstance(item, dict) else item
                for item in data
            ]
        return data

    def _headers(self, extra_headers: Mapping[str, str] | None = None) -> dict[str, str]:
        headers = dict(self.headers)
        if extra_headers:
            headers.update({name: str(value) for name, value in extra_headers.items()})
        return _request_headers(
            self.token,
            self.header,
            self.auth_header,
            headers,
            include_env_credentials=self.include_env_credentials,
        )

    def _url(self, path: str) -> str:
        """Build the absolute request URL, mapping a malformed base to a transport error.

        ``_join_operation_url`` accesses the authority while joining, so a malformed
        base URL (an unterminated IPv6 literal such as ``http://[::1``) raises a bare
        ``ValueError`` during URL construction, before any request is issued.
        Re-raising it as ``requests.exceptions.InvalidURL`` -- which carries no HTTP
        response -- routes it through the same ``_send_request`` boundary as other
        transport failures, so it surfaces as a credential-safe
        :class:`TangleApiTransportError` instead of an unhandled ``ValueError``.
        """

        try:
            return _join_operation_url(self.base_url, path)
        except ValueError as exc:
            raise requests.exceptions.InvalidURL(str(exc)) from exc

    def _safe_error_url(self, path: str) -> str | None:
        """Best-effort destination for an error message; a malformed base yields none."""

        try:
            return self._url(path)
        except requests.exceptions.RequestException:
            return None

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

    # ---- Handwritten semantic helpers consumed by tangle-deploy ----------

    def get_execution_details(self, execution_id: str) -> GetExecutionInfoResponse:
        details = self.executions_details(execution_id)
        self._enrich_execution_tree(details)
        return details

    def stream_execution_container_log(self, execution_id: str) -> requests.Response:
        response = self._send_request(
            "GET",
            self._format_path(
                "/api/executions/{id}/stream_container_log",
                {"id": execution_id},
            ),
            stream=True,
        )
        response.raise_for_status()
        return response

    def get_component_spec(self, digest: str) -> ComponentSpec:
        """Return a parsed domain component spec from the generated component endpoint."""

        return ComponentSpec.from_dict(_to_plain(self.components_get(digest)))

    def resolve_digest(self, digest: str) -> str:
        """Resolve a component digest/name, following deprecation successors."""

        current = digest
        seen: set[str] = set()

        while current not in seen:
            seen.add(current)
            matches = self._published_component_rows(include_deprecated=True, digest=current)
            if not matches:
                matches = self._published_component_rows(
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

    def _published_component_rows(
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
            for component in self._published_component_rows(
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
        components: Iterable[ComponentSpec | Mapping[str, Any] | str] | None = None,
        *,
        names: Iterable[str] | None = None,
        digests: Iterable[str] | None = None,
        include_deprecated: bool = False,
        published_by: str | None = None,
        published_by_substring: str | None = None,
        verbose: bool = False,
    ) -> list[ComponentInfo]:
        """Find published components matching component specs, names, or digests.

        ``components`` may contain domain component specs, mapping-like component
        references, or plain component names. Results are de-duplicated by digest
        when available, falling back to name.
        """

        search_names = set(names or [])
        search_digests = set(digests or [])
        for component in components or []:
            data = _to_plain(component)
            if isinstance(component, str):
                search_names.add(component)
            elif isinstance(component, ComponentSpec):
                search_names.update(name for name in component.search_names if name)
                if component.digest:
                    search_digests.add(component.digest)
            elif isinstance(data, Mapping):
                if data.get("name"):
                    search_names.add(str(data["name"]))
                if data.get("digest"):
                    search_digests.add(str(data["digest"]))

        publisher_filter = published_by_substring or published_by
        found: dict[str, ComponentInfo] = {}

        def add(info: ComponentInfo) -> None:
            key = info.digest or info.name
            if not key:
                return
            found[key] = info
            if verbose:
                self.logger.info(f"   Found existing component: {info.name} ({key[:16]}...)")

        for digest in search_digests:
            for info in self.list_published_component_infos(
                include_deprecated=include_deprecated,
                published_by_substring=publisher_filter,
                digest=digest,
            ):
                add(info)
        for name in search_names:
            for info in self.list_published_component_infos(
                include_deprecated=include_deprecated,
                published_by_substring=publisher_filter,
                name_substring=name,
            ):
                if info.name.lower() == name.lower():
                    add(info)
        return list(found.values())

    def get_run_details(
        self,
        run_id: str,
        include_implementations: bool = False,
        include_annotations: bool = False,
        include_execution_state: bool = False,
        execution_id: str | None = None,
    ) -> RunDetails:
        annotations_run_id: str | None = run_id
        try:
            run = PipelineRun.from_dict(_to_plain(self.pipeline_runs_get(run_id)))
            root_execution_id = execution_id or run.root_execution_id
        except requests.HTTPError as exc:
            if exc.response is None or exc.response.status_code != 404 or execution_id is not None:
                raise
            root_execution_id = run_id
            annotations_run_id = None
            run = PipelineRun(
                id=run_id,
                root_execution_id=root_execution_id,
                raw={"id": run_id, "root_execution_id": root_execution_id},
            )

        execution = self.get_execution_details(root_execution_id) if root_execution_id else None
        if execution and not include_implementations:
            self._strip_execution_raw_tasks_for_run_details(execution)
            execution.strip_implementations()
        raw_annotations = (
            self.pipeline_runs_annotations(annotations_run_id)
            if include_annotations and annotations_run_id
            else None
        )
        annotations = raw_annotations if isinstance(raw_annotations, dict) else None
        execution_state = (
            GraphExecutionState.from_dict(
                _to_plain(self.executions_graph_execution_state(root_execution_id))
            )
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
        try:
            run = self.pipeline_runs_get(run_id)
            root_execution_id = getattr(run, "root_execution_id", None)
            if root_execution_id is None and isinstance(run, dict):
                root_execution_id = run.get("root_execution_id")
        except requests.HTTPError as exc:
            if exc.response is None or exc.response.status_code != 404:
                raise
            root_execution_id = run_id

        if not root_execution_id:
            return None
        execution = self.executions_details(root_execution_id)
        return execution.task_spec

    def _enrich_execution_tree(self, execution: GetExecutionInfoResponse) -> None:
        child_ids = execution.raw.get("child_task_execution_ids") or {}
        if not isinstance(child_ids, dict):
            return

        raw_tasks = self._execution_graph_tasks(execution)
        for task_name, child_execution_id in child_ids.items():
            if not child_execution_id:
                continue
            child = self.executions_details(child_execution_id)
            self._enrich_execution_tree(child)
            execution.child_executions[task_name] = child

            task = execution.task_spec.graph_tasks.get(task_name)
            raw_task = raw_tasks.get(task_name) if isinstance(raw_tasks, dict) else None
            if raw_task is None and task is not None:
                raw_task = task.raw

            context = {
                "execution_id": child.id,
                "input_artifacts": child.input_artifacts,
                "output_artifacts": child.output_artifacts,
            }
            if child.raw.get("state") is not None:
                context["state"] = child.raw["state"]

            if task is not None:
                task.raw.update(context)
            if isinstance(raw_task, dict):
                raw_task.update(context)
                child_impl = (
                    child.task_spec.component_spec.implementation
                    if child.task_spec.component_spec
                    else None
                )
                raw_spec = raw_task.get("componentRef", {}).get("spec")
                if isinstance(raw_spec, dict) and child_impl:
                    raw_spec["implementation"] = child_impl

    @staticmethod
    def _execution_graph_tasks(execution: GetExecutionInfoResponse) -> dict[str, Any]:
        implementation = (
            execution.task_spec.component_spec.implementation
            if execution.task_spec.component_spec
            else None
        )
        if not isinstance(implementation, dict):
            return {}
        graph = implementation.get("graph")
        if not isinstance(graph, dict):
            return {}
        tasks = graph.get("tasks")
        return tasks if isinstance(tasks, dict) else {}

    def _strip_execution_raw_tasks_for_run_details(
        self,
        execution: GetExecutionInfoResponse,
    ) -> None:
        for raw_task in self._execution_graph_tasks(execution).values():
            if isinstance(raw_task, dict):
                self._strip_raw_task_for_run_details(raw_task)
        for child in execution.child_executions.values():
            self._strip_execution_raw_tasks_for_run_details(child)

    def _strip_raw_task_for_run_details(self, task: dict[str, Any]) -> None:
        component_ref = task.get("componentRef")
        if not isinstance(component_ref, dict):
            return
        component_ref.pop("text", None)
        spec = component_ref.get("spec")
        if not isinstance(spec, dict):
            return

        annotations = spec.get("metadata", {}).get("annotations")
        if isinstance(annotations, dict):
            for key in ComponentSpec._STRIP_ANNOTATION_KEYS:
                annotations.pop(key, None)

        implementation = spec.get("implementation")
        if not isinstance(implementation, dict):
            return
        graph = implementation.get("graph")
        if isinstance(graph, dict) and isinstance(graph.get("tasks"), dict):
            for child_task in graph["tasks"].values():
                if isinstance(child_task, dict):
                    self._strip_raw_task_for_run_details(child_task)
        else:
            spec.pop("implementation", None)


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


__all__ = ["TangleApiClient"]
