"""Generated static endpoint methods for the Tangle API.

Do not edit by hand; run ``uv run python -m tangle_cli.openapi.codegen``.
"""

from __future__ import annotations

from typing import Any

from .models import ComponentLibraryResponse, ComponentResponse, GetArtifactInfoResponse, GetArtifactSignedUrlResponse, GetContainerExecutionLogResponse, GetContainerExecutionStateResponse, GetExecutionArtifactsResponse, GetExecutionInfoResponse, GetGraphExecutionStateResponse, GetUserResponse, ListComponentLibrariesResponse, ListPipelineJobsResponse, ListPublishedComponentsResponse, ListSecretsResponse, PipelineRunResponse, PublishedComponentResponse, SecretInfoResponse, UserComponentLibraryPinsResponse, UserSettingsResponse


class GeneratedOperationsMixin:
    """Mixin containing one checked-in method per OpenAPI operation."""

    def admin_execution_node_status(self, id: Any, status: Any) -> Any:
        return self._request_json(
            'PUT',
            '/api/admin/execution_node/{id}/status',
            path_params={'id': id},
            params={'status': status},
            json_data=None,
            response_model=None,
        )

    def admin_set_read_only_model(self, read_only: Any) -> Any:
        return self._request_json(
            'PUT',
            '/api/admin/set_read_only_model',
            path_params=None,
            params={'read_only': read_only},
            json_data=None,
            response_model=None,
        )

    def admin_sql_engine_connection_pool_status(self) -> Any:
        return self._request_json(
            'GET',
            '/api/admin/sql_engine_connection_pool_status',
            path_params=None,
            params=None,
            json_data=None,
            response_model=None,
        )

    def artifacts_get(self, id: Any) -> Any:
        return self._request_json(
            'GET',
            '/api/artifacts/{id}',
            path_params={'id': id},
            params=None,
            json_data=None,
            response_model=GetArtifactInfoResponse,
        )

    def artifacts_signed_artifact_url(self, id: Any) -> Any:
        return self._request_json(
            'GET',
            '/api/artifacts/{id}/signed_artifact_url',
            path_params={'id': id},
            params=None,
            json_data=None,
            response_model=GetArtifactSignedUrlResponse,
        )

    def component_libraries_list(self, name_substring: Any = None) -> Any:
        return self._request_json(
            'GET',
            '/api/component_libraries/',
            path_params=None,
            params={'name_substring': name_substring},
            json_data=None,
            response_model=ListComponentLibrariesResponse,
        )

    def component_libraries_create(self, name: Any, hide_from_search: Any = None) -> Any:
        return self._request_json(
            'POST',
            '/api/component_libraries/',
            path_params=None,
            params={'hide_from_search': hide_from_search},
            json_data={'name': name},
            response_model=ComponentLibraryResponse,
        )

    def component_libraries_get(self, id: Any, include_component_texts: Any = None) -> Any:
        return self._request_json(
            'GET',
            '/api/component_libraries/{id}',
            path_params={'id': id},
            params={'include_component_texts': include_component_texts},
            json_data=None,
            response_model=ComponentLibraryResponse,
        )

    def component_libraries_update(self, id: Any, name: Any, hide_from_search: Any = None) -> Any:
        return self._request_json(
            'PUT',
            '/api/component_libraries/{id}',
            path_params={'id': id},
            params={'hide_from_search': hide_from_search},
            json_data={'name': name},
            response_model=ComponentLibraryResponse,
        )

    def component_library_pins_me(self) -> Any:
        return self._request_json(
            'GET',
            '/api/component_library_pins/me/',
            path_params=None,
            params=None,
            json_data=None,
            response_model=UserComponentLibraryPinsResponse,
        )

    def component_library_pins_put_me(self, body: Any = None) -> Any:
        return self._request_json(
            'PUT',
            '/api/component_library_pins/me/',
            path_params=None,
            params=None,
            json_data=body,
            response_model=None,
        )

    def components_get(self, digest: Any) -> Any:
        return self._request_json(
            'GET',
            '/api/components/{digest}',
            path_params={'digest': digest},
            params=None,
            json_data=None,
            response_model=ComponentResponse,
        )

    def executions_artifacts(self, id: Any) -> Any:
        return self._request_json(
            'GET',
            '/api/executions/{id}/artifacts',
            path_params={'id': id},
            params=None,
            json_data=None,
            response_model=GetExecutionArtifactsResponse,
        )

    def executions_container_log(self, id: Any) -> Any:
        return self._request_json(
            'GET',
            '/api/executions/{id}/container_log',
            path_params={'id': id},
            params=None,
            json_data=None,
            response_model=GetContainerExecutionLogResponse,
        )

    def executions_container_state(self, id: Any, include_execution_nodes_linked_to_same_container_execution: Any = None) -> Any:
        return self._request_json(
            'GET',
            '/api/executions/{id}/container_state',
            path_params={'id': id},
            params={'include_execution_nodes_linked_to_same_container_execution': include_execution_nodes_linked_to_same_container_execution},
            json_data=None,
            response_model=GetContainerExecutionStateResponse,
        )

    def executions_details(self, id: Any) -> Any:
        return self._request_json(
            'GET',
            '/api/executions/{id}/details',
            path_params={'id': id},
            params=None,
            json_data=None,
            response_model=GetExecutionInfoResponse,
        )

    def executions_graph_execution_state(self, id: Any) -> Any:
        return self._request_json(
            'GET',
            '/api/executions/{id}/graph_execution_state',
            path_params={'id': id},
            params=None,
            json_data=None,
            response_model=GetGraphExecutionStateResponse,
        )

    def executions_state(self, id: Any) -> Any:
        return self._request_json(
            'GET',
            '/api/executions/{id}/state',
            path_params={'id': id},
            params=None,
            json_data=None,
            response_model=GetGraphExecutionStateResponse,
        )

    def pipeline_runs_list(self, page_token: Any = None, filter: Any = None, filter_query: Any = None, include_pipeline_names: Any = None, include_execution_stats: Any = None) -> Any:
        return self._request_json(
            'GET',
            '/api/pipeline_runs/',
            path_params=None,
            params={'page_token': page_token, 'filter': filter, 'filter_query': filter_query, 'include_pipeline_names': include_pipeline_names, 'include_execution_stats': include_execution_stats},
            json_data=None,
            response_model=ListPipelineJobsResponse,
        )

    def pipeline_runs_create(self, body: Any = None) -> Any:
        return self._request_json(
            'POST',
            '/api/pipeline_runs/',
            path_params=None,
            params=None,
            json_data=body,
            response_model=PipelineRunResponse,
        )

    def pipeline_runs_get(self, id: Any, include_execution_stats: Any = None) -> Any:
        return self._request_json(
            'GET',
            '/api/pipeline_runs/{id}',
            path_params={'id': id},
            params={'include_execution_stats': include_execution_stats},
            json_data=None,
            response_model=PipelineRunResponse,
        )

    def pipeline_runs_annotations(self, id: Any) -> Any:
        return self._request_json(
            'GET',
            '/api/pipeline_runs/{id}/annotations/',
            path_params={'id': id},
            params=None,
            json_data=None,
            response_model=None,
        )

    def pipeline_runs_put_annotations(self, id: Any, key: Any, value: Any = None) -> Any:
        return self._request_json(
            'PUT',
            '/api/pipeline_runs/{id}/annotations/{key}',
            path_params={'id': id, 'key': key},
            params={'value': value},
            json_data=None,
            response_model=None,
        )

    def pipeline_runs_delete_annotations(self, id: Any, key: Any) -> Any:
        return self._request_json(
            'DELETE',
            '/api/pipeline_runs/{id}/annotations/{key}',
            path_params={'id': id, 'key': key},
            params=None,
            json_data=None,
            response_model=None,
        )

    def pipeline_runs_cancel(self, id: Any) -> Any:
        return self._request_json(
            'POST',
            '/api/pipeline_runs/{id}/cancel',
            path_params={'id': id},
            params=None,
            json_data=None,
            response_model=None,
        )

    def published_components_list(self, include_deprecated: Any = None, name_substring: Any = None, published_by_substring: Any = None, digest: Any = None) -> Any:
        return self._request_json(
            'GET',
            '/api/published_components/',
            path_params=None,
            params={'include_deprecated': include_deprecated, 'name_substring': name_substring, 'published_by_substring': published_by_substring, 'digest': digest},
            json_data=None,
            response_model=ListPublishedComponentsResponse,
        )

    def published_components_create(self, digest: Any = None, name: Any = None, tag: Any = None, text: Any = None, url: Any = None) -> Any:
        return self._request_json(
            'POST',
            '/api/published_components/',
            path_params=None,
            params=None,
            json_data={'digest': digest, 'name': name, 'tag': tag, 'text': text, 'url': url},
            response_model=PublishedComponentResponse,
        )

    def published_components_update(self, digest: Any, deprecated: Any = None, superseded_by: Any = None) -> Any:
        return self._request_json(
            'PUT',
            '/api/published_components/{digest}',
            path_params={'digest': digest},
            params={'deprecated': deprecated, 'superseded_by': superseded_by},
            json_data=None,
            response_model=PublishedComponentResponse,
        )

    def secrets_list(self) -> Any:
        return self._request_json(
            'GET',
            '/api/secrets/',
            path_params=None,
            params=None,
            json_data=None,
            response_model=ListSecretsResponse,
        )

    def secrets_create(self, secret_name: Any, secret_value: Any, description: Any = None, expires_at: Any = None) -> Any:
        return self._request_json(
            'POST',
            '/api/secrets/',
            path_params=None,
            params={'secret_name': secret_name, 'description': description, 'expires_at': expires_at},
            json_data={'secret_value': secret_value},
            response_model=SecretInfoResponse,
        )

    def secrets_update(self, secret_name: Any, secret_value: Any, description: Any = None, expires_at: Any = None) -> Any:
        return self._request_json(
            'PUT',
            '/api/secrets/{secret_name}',
            path_params={'secret_name': secret_name},
            params={'description': description, 'expires_at': expires_at},
            json_data={'secret_value': secret_value},
            response_model=SecretInfoResponse,
        )

    def secrets_delete(self, secret_name: Any) -> Any:
        return self._request_json(
            'DELETE',
            '/api/secrets/{secret_name}',
            path_params={'secret_name': secret_name},
            params=None,
            json_data=None,
            response_model=None,
        )

    def users_me(self) -> Any:
        return self._request_json(
            'GET',
            '/api/users/me',
            path_params=None,
            params=None,
            json_data=None,
            response_model=GetUserResponse,
        )

    def users_me_settings(self, setting_names: Any = None) -> Any:
        return self._request_json(
            'GET',
            '/api/users/me/settings',
            path_params=None,
            params={'setting_names': setting_names},
            json_data=None,
            response_model=UserSettingsResponse,
        )

    def users_patch_me_settings(self, body: Any = None) -> Any:
        return self._request_json(
            'PATCH',
            '/api/users/me/settings',
            path_params=None,
            params=None,
            json_data=body,
            response_model=None,
        )

    def users_delete_me_settings(self, setting_names: Any) -> Any:
        return self._request_json(
            'DELETE',
            '/api/users/me/settings',
            path_params=None,
            params={'setting_names': setting_names},
            json_data=None,
            response_model=None,
        )

__all__ = ['GeneratedOperationsMixin']
