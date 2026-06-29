"""Generated Pydantic models for the checked-in Tangle OpenAPI schema.

Do not edit by hand; run ``uv run python -m tangle_cli.openapi.codegen``.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from tangle_cli.generated_runtime import TangleGeneratedModel

from tangle_cli.generated_model_extensions import ComponentSpecExtensions, GetExecutionInfoResponseExtensions, GetGraphExecutionStateResponseExtensions

class _ArtifactDataGenerated(TangleGeneratedModel):
    created_at: Any = None
    deleted_at: Any = None
    extra_data: Any = None
    hash: Any = None
    is_dir: Any = None
    total_size: Any = None
    uri: Any = None
    value: Any = None

class ArtifactData(_ArtifactDataGenerated):
    pass

class _ArtifactDataResponseGenerated(TangleGeneratedModel):
    is_dir: Any = None
    total_size: Any = None
    uri: Any = None
    value: Any = None

class ArtifactDataResponse(_ArtifactDataResponseGenerated):
    pass

class _ArtifactNodeIdResponseGenerated(TangleGeneratedModel):
    id: Any = None

class ArtifactNodeIdResponse(_ArtifactNodeIdResponseGenerated):
    pass

class _ArtifactNodeResponseGenerated(TangleGeneratedModel):
    artifact_data: Any = None
    id: Any = None
    producer_execution_id: Any = None
    producer_output_name: Any = None
    type_name: Any = None
    type_properties: Any = None

class ArtifactNodeResponse(_ArtifactNodeResponseGenerated):
    pass

class _BodyCreateApiPipelineRunsPostGenerated(TangleGeneratedModel):
    annotations: Any = None
    components: Any = None
    root_task: Any = None

class BodyCreateApiPipelineRunsPost(_BodyCreateApiPipelineRunsPostGenerated):
    pass

class _BodyCreateSecretApiSecretsPostGenerated(TangleGeneratedModel):
    secret_value: Any = None

class BodyCreateSecretApiSecretsPost(_BodyCreateSecretApiSecretsPostGenerated):
    pass

class _BodySetSettingsApiUsersMeSettingsPatchGenerated(TangleGeneratedModel):
    settings: Any = None

class BodySetSettingsApiUsersMeSettingsPatch(_BodySetSettingsApiUsersMeSettingsPatchGenerated):
    pass

class _BodyUpdateSecretApiSecretsSecretNamePutGenerated(TangleGeneratedModel):
    secret_value: Any = None

class BodyUpdateSecretApiSecretsSecretNamePut(_BodyUpdateSecretApiSecretsSecretNamePutGenerated):
    pass

class _CachingStrategySpecGenerated(TangleGeneratedModel):
    maxcachestaleness: Any = Field(default=None, alias='maxCacheStaleness')

class CachingStrategySpec(_CachingStrategySpecGenerated):
    pass

class _ComponentLibraryGenerated(TangleGeneratedModel):
    annotations: Any = None
    name: Any = None
    root_folder: Any = None

class ComponentLibrary(_ComponentLibraryGenerated):
    pass

class _ComponentLibraryFolderGenerated(TangleGeneratedModel):
    annotations: Any = None
    components: Any = None
    folders: Any = None
    name: Any = None

class ComponentLibraryFolder(_ComponentLibraryFolderGenerated):
    pass

class _ComponentLibraryResponseGenerated(TangleGeneratedModel):
    annotations: Any = None
    component_count: Any = None
    created_at: Any = None
    hide_from_search: Any = None
    id: Any = None
    name: Any = None
    published_by: Any = None
    root_folder: Any = None
    updated_at: Any = None

class ComponentLibraryResponse(_ComponentLibraryResponseGenerated):
    pass

class _ComponentReferenceGenerated(TangleGeneratedModel):
    digest: Any = None
    name: Any = None
    spec: Any = None
    tag: Any = None
    text: Any = None
    url: Any = None

class ComponentReference(_ComponentReferenceGenerated):
    pass

class _ComponentResponseGenerated(TangleGeneratedModel):
    digest: Any = None
    text: Any = None

class ComponentResponse(_ComponentResponseGenerated):
    pass

class _ComponentSpecGenerated(TangleGeneratedModel):
    description: Any = None
    implementation: Any = None
    inputs: Any = None
    metadata: Any = None
    name: Any = None
    outputs: Any = None

class ComponentSpec(ComponentSpecExtensions, _ComponentSpecGenerated):
    pass

class _ConcatPlaceholderGenerated(TangleGeneratedModel):
    concat: Any = None

class ConcatPlaceholder(_ConcatPlaceholderGenerated):
    pass

ContainerExecutionStatus = Any

class _ContainerImplementationGenerated(TangleGeneratedModel):
    container: Any = None

class ContainerImplementation(_ContainerImplementationGenerated):
    pass

class _ContainerSpecGenerated(TangleGeneratedModel):
    args: Any = None
    command: Any = None
    env: Any = None
    image: Any = None

class ContainerSpec(_ContainerSpecGenerated):
    pass

class _DynamicDataArgumentGenerated(TangleGeneratedModel):
    dynamicdata: Any = Field(default=None, alias='dynamicData')

class DynamicDataArgument(_DynamicDataArgumentGenerated):
    pass

class _ExecutionNodeReferenceGenerated(TangleGeneratedModel):
    execution_node_id: Any = None
    pipeline_run_id: Any = None

class ExecutionNodeReference(_ExecutionNodeReferenceGenerated):
    pass

class _ExecutionOptionsSpecGenerated(TangleGeneratedModel):
    cachingstrategy: Any = Field(default=None, alias='cachingStrategy')
    retrystrategy: Any = Field(default=None, alias='retryStrategy')

class ExecutionOptionsSpec(_ExecutionOptionsSpecGenerated):
    pass

class _ExecutionStatusSummaryGenerated(TangleGeneratedModel):
    ended_executions: Any = None
    has_ended: Any = None
    total_executions: Any = None

class ExecutionStatusSummary(_ExecutionStatusSummaryGenerated):
    pass

class _GetArtifactInfoResponseGenerated(TangleGeneratedModel):
    artifact_data: Any = None
    id: Any = None

class GetArtifactInfoResponse(_GetArtifactInfoResponseGenerated):
    pass

class _GetArtifactSignedUrlResponseGenerated(TangleGeneratedModel):
    signed_url: Any = None

class GetArtifactSignedUrlResponse(_GetArtifactSignedUrlResponseGenerated):
    pass

class _GetContainerExecutionLogResponseGenerated(TangleGeneratedModel):
    log_text: Any = None
    orchestration_error_message: Any = None
    system_error_exception_full: Any = None

class GetContainerExecutionLogResponse(_GetContainerExecutionLogResponseGenerated):
    pass

class _GetContainerExecutionStateResponseGenerated(TangleGeneratedModel):
    debug_info: Any = None
    ended_at: Any = None
    execution_nodes_linked_to_same_container_execution: Any = None
    exit_code: Any = None
    started_at: Any = None
    status: Any = None

class GetContainerExecutionStateResponse(_GetContainerExecutionStateResponseGenerated):
    pass

class _GetExecutionArtifactsResponseGenerated(TangleGeneratedModel):
    input_artifacts: Any = None
    output_artifacts: Any = None

class GetExecutionArtifactsResponse(_GetExecutionArtifactsResponseGenerated):
    pass

class _GetExecutionInfoResponseGenerated(TangleGeneratedModel):
    child_task_execution_ids: Any = None
    id: Any = None
    input_artifacts: Any = None
    output_artifacts: Any = None
    parent_execution_id: Any = None
    pipeline_run_id: Any = None
    task_spec: Any = None

class GetExecutionInfoResponse(GetExecutionInfoResponseExtensions, _GetExecutionInfoResponseGenerated):
    pass

class _GetGraphExecutionStateResponseGenerated(TangleGeneratedModel):
    child_execution_status_stats: Any = None
    child_execution_status_summary: Any = None

class GetGraphExecutionStateResponse(GetGraphExecutionStateResponseExtensions, _GetGraphExecutionStateResponseGenerated):
    pass

class _GetUserResponseGenerated(TangleGeneratedModel):
    id: Any = None
    permissions: Any = None

class GetUserResponse(_GetUserResponseGenerated):
    pass

class _GraphImplementationGenerated(TangleGeneratedModel):
    graph: Any = None

class GraphImplementation(_GraphImplementationGenerated):
    pass

class _GraphInputArgumentGenerated(TangleGeneratedModel):
    graphinput: Any = Field(default=None, alias='graphInput')

class GraphInputArgument(_GraphInputArgumentGenerated):
    pass

class _GraphInputReferenceGenerated(TangleGeneratedModel):
    inputname: Any = Field(default=None, alias='inputName')
    type: Any = None

class GraphInputReference(_GraphInputReferenceGenerated):
    pass

class _GraphSpecGenerated(TangleGeneratedModel):
    outputvalues: Any = Field(default=None, alias='outputValues')
    tasks: Any = None

class GraphSpec(_GraphSpecGenerated):
    pass

class _HTTPValidationErrorGenerated(TangleGeneratedModel):
    detail: Any = None

class HTTPValidationError(_HTTPValidationErrorGenerated):
    pass

class _IfPlaceholderGenerated(TangleGeneratedModel):
    if_: Any = Field(default=None, alias='if')

class IfPlaceholder(_IfPlaceholderGenerated):
    pass

class _IfPlaceholderStructureGenerated(TangleGeneratedModel):
    cond: Any = None
    else_: Any = Field(default=None, alias='else')
    then: Any = None

class IfPlaceholderStructure(_IfPlaceholderStructureGenerated):
    pass

class _InputPathPlaceholderGenerated(TangleGeneratedModel):
    inputpath: Any = Field(default=None, alias='inputPath')

class InputPathPlaceholder(_InputPathPlaceholderGenerated):
    pass

class _InputSpecGenerated(TangleGeneratedModel):
    annotations: Any = None
    default: Any = None
    description: Any = None
    name: Any = None
    optional: Any = None
    type: Any = None

class InputSpec(_InputSpecGenerated):
    pass

class _InputValuePlaceholderGenerated(TangleGeneratedModel):
    inputvalue: Any = Field(default=None, alias='inputValue')

class InputValuePlaceholder(_InputValuePlaceholderGenerated):
    pass

class _IsPresentPlaceholderGenerated(TangleGeneratedModel):
    ispresent: Any = Field(default=None, alias='isPresent')

class IsPresentPlaceholder(_IsPresentPlaceholderGenerated):
    pass

class _ListComponentLibrariesResponseGenerated(TangleGeneratedModel):
    component_libraries: Any = None

class ListComponentLibrariesResponse(_ListComponentLibrariesResponseGenerated):
    pass

class _ListPipelineJobsResponseGenerated(TangleGeneratedModel):
    next_page_token: Any = None
    pipeline_runs: Any = None

class ListPipelineJobsResponse(_ListPipelineJobsResponseGenerated):
    pass

class _ListPublishedComponentsResponseGenerated(TangleGeneratedModel):
    published_components: Any = None

class ListPublishedComponentsResponse(_ListPublishedComponentsResponseGenerated):
    pass

class _ListSecretsResponseGenerated(TangleGeneratedModel):
    secrets: Any = None

class ListSecretsResponse(_ListSecretsResponseGenerated):
    pass

class _MetadataSpecGenerated(TangleGeneratedModel):
    annotations: Any = None
    labels: Any = None

class MetadataSpec(_MetadataSpecGenerated):
    pass

class _OutputPathPlaceholderGenerated(TangleGeneratedModel):
    outputpath: Any = Field(default=None, alias='outputPath')

class OutputPathPlaceholder(_OutputPathPlaceholderGenerated):
    pass

class _OutputSpecGenerated(TangleGeneratedModel):
    annotations: Any = None
    description: Any = None
    name: Any = None
    type: Any = None

class OutputSpec(_OutputSpecGenerated):
    pass

class _PipelineRunResponseGenerated(TangleGeneratedModel):
    annotations: Any = None
    created_at: Any = None
    created_by: Any = None
    execution_status_stats: Any = None
    execution_summary: Any = None
    id: Any = None
    pipeline_name: Any = None
    root_execution_id: Any = None

class PipelineRunResponse(_PipelineRunResponseGenerated):
    pass

class _PublishedComponentResponseGenerated(TangleGeneratedModel):
    deprecated: Any = None
    digest: Any = None
    name: Any = None
    published_by: Any = None
    superseded_by: Any = None
    url: Any = None

class PublishedComponentResponse(_PublishedComponentResponseGenerated):
    pass

class _RetryStrategySpecGenerated(TangleGeneratedModel):
    maxretries: Any = Field(default=None, alias='maxRetries')

class RetryStrategySpec(_RetryStrategySpecGenerated):
    pass

class _SecretInfoResponseGenerated(TangleGeneratedModel):
    created_at: Any = None
    description: Any = None
    expires_at: Any = None
    secret_name: Any = None
    updated_at: Any = None

class SecretInfoResponse(_SecretInfoResponseGenerated):
    pass

class _TaskOutputArgumentGenerated(TangleGeneratedModel):
    taskoutput: Any = Field(default=None, alias='taskOutput')

class TaskOutputArgument(_TaskOutputArgumentGenerated):
    pass

class _TaskOutputReferenceGenerated(TangleGeneratedModel):
    outputname: Any = Field(default=None, alias='outputName')
    taskid: Any = Field(default=None, alias='taskId')

class TaskOutputReference(_TaskOutputReferenceGenerated):
    pass

class _TaskSpecGenerated(TangleGeneratedModel):
    annotations: Any = None
    arguments: Any = None
    componentref: Any = Field(default=None, alias='componentRef')
    executionoptions: Any = Field(default=None, alias='executionOptions')
    isenabled: Any = Field(default=None, alias='isEnabled')

class TaskSpec(_TaskSpecGenerated):
    pass

class _UserComponentLibraryPinsResponseGenerated(TangleGeneratedModel):
    component_library_ids: Any = None

class UserComponentLibraryPinsResponse(_UserComponentLibraryPinsResponseGenerated):
    pass

class _UserSettingsResponseGenerated(TangleGeneratedModel):
    settings: Any = None

class UserSettingsResponse(_UserSettingsResponseGenerated):
    pass

class _ValidationErrorGenerated(TangleGeneratedModel):
    ctx: Any = None
    input: Any = None
    loc: Any = None
    msg: Any = None
    type: Any = None

class ValidationError(_ValidationErrorGenerated):
    pass

__all__ = ['ArtifactData', 'ArtifactDataResponse', 'ArtifactNodeIdResponse', 'ArtifactNodeResponse', 'BodyCreateApiPipelineRunsPost', 'BodyCreateSecretApiSecretsPost', 'BodySetSettingsApiUsersMeSettingsPatch', 'BodyUpdateSecretApiSecretsSecretNamePut', 'CachingStrategySpec', 'ComponentLibrary', 'ComponentLibraryFolder', 'ComponentLibraryResponse', 'ComponentReference', 'ComponentResponse', 'ComponentSpec', 'ConcatPlaceholder', 'ContainerExecutionStatus', 'ContainerImplementation', 'ContainerSpec', 'DynamicDataArgument', 'ExecutionNodeReference', 'ExecutionOptionsSpec', 'ExecutionStatusSummary', 'GetArtifactInfoResponse', 'GetArtifactSignedUrlResponse', 'GetContainerExecutionLogResponse', 'GetContainerExecutionStateResponse', 'GetExecutionArtifactsResponse', 'GetExecutionInfoResponse', 'GetGraphExecutionStateResponse', 'GetUserResponse', 'GraphImplementation', 'GraphInputArgument', 'GraphInputReference', 'GraphSpec', 'HTTPValidationError', 'IfPlaceholder', 'IfPlaceholderStructure', 'InputPathPlaceholder', 'InputSpec', 'InputValuePlaceholder', 'IsPresentPlaceholder', 'ListComponentLibrariesResponse', 'ListPipelineJobsResponse', 'ListPublishedComponentsResponse', 'ListSecretsResponse', 'MetadataSpec', 'OutputPathPlaceholder', 'OutputSpec', 'PipelineRunResponse', 'PublishedComponentResponse', 'RetryStrategySpec', 'SecretInfoResponse', 'TaskOutputArgument', 'TaskOutputReference', 'TaskSpec', 'UserComponentLibraryPinsResponse', 'UserSettingsResponse', 'ValidationError']
