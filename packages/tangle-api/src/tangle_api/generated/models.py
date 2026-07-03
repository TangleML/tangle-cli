"""Generated Pydantic models for the checked-in Tangle OpenAPI schema.

Do not edit by hand; run ``uv run python -m tangle_cli.openapi.codegen``.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from tangle_api.generated.runtime import TangleGeneratedModel

class ArtifactData(TangleGeneratedModel):
    created_at: Any = None
    deleted_at: Any = None
    extra_data: Any = None
    hash: Any = None
    is_dir: Any = None
    total_size: Any = None
    uri: Any = None
    value: Any = None

class ArtifactDataResponse(TangleGeneratedModel):
    is_dir: Any = None
    total_size: Any = None
    uri: Any = None
    value: Any = None

class ArtifactNodeIdResponse(TangleGeneratedModel):
    id: Any = None

class ArtifactNodeResponse(TangleGeneratedModel):
    artifact_data: Any = None
    id: Any = None
    producer_execution_id: Any = None
    producer_output_name: Any = None
    type_name: Any = None
    type_properties: Any = None

class BodyCreateApiPipelineRunsPost(TangleGeneratedModel):
    annotations: Any = None
    components: Any = None
    root_task: Any = None

class BodyCreateSecretApiSecretsPost(TangleGeneratedModel):
    secret_value: Any = None

class BodySetSettingsApiUsersMeSettingsPatch(TangleGeneratedModel):
    settings: Any = None

class BodyUpdateSecretApiSecretsSecretNamePut(TangleGeneratedModel):
    secret_value: Any = None

class CachingStrategySpec(TangleGeneratedModel):
    maxcachestaleness: Any = Field(default=None, alias='maxCacheStaleness')

class ComponentLibrary(TangleGeneratedModel):
    annotations: Any = None
    name: Any = None
    root_folder: Any = None

class ComponentLibraryFolder(TangleGeneratedModel):
    annotations: Any = None
    components: Any = None
    folders: Any = None
    name: Any = None

class ComponentLibraryResponse(TangleGeneratedModel):
    annotations: Any = None
    component_count: Any = None
    created_at: Any = None
    hide_from_search: Any = None
    id: Any = None
    name: Any = None
    published_by: Any = None
    root_folder: Any = None
    updated_at: Any = None

class ComponentReference(TangleGeneratedModel):
    digest: Any = None
    name: Any = None
    spec: Any = None
    tag: Any = None
    text: Any = None
    url: Any = None

class ComponentResponse(TangleGeneratedModel):
    digest: Any = None
    text: Any = None

class ComponentSpec(TangleGeneratedModel):
    description: Any = None
    implementation: Any = None
    inputs: Any = None
    metadata: Any = None
    name: Any = None
    outputs: Any = None

class ConcatPlaceholder(TangleGeneratedModel):
    concat: Any = None

ContainerExecutionStatus = Any

class ContainerImplementation(TangleGeneratedModel):
    container: Any = None

class ContainerSpec(TangleGeneratedModel):
    args: Any = None
    command: Any = None
    env: Any = None
    image: Any = None

class DynamicDataArgument(TangleGeneratedModel):
    dynamicdata: Any = Field(default=None, alias='dynamicData')

class ExecutionNodeReference(TangleGeneratedModel):
    execution_node_id: Any = None
    pipeline_run_id: Any = None

class ExecutionOptionsSpec(TangleGeneratedModel):
    cachingstrategy: Any = Field(default=None, alias='cachingStrategy')
    retrystrategy: Any = Field(default=None, alias='retryStrategy')

class ExecutionStatusSummary(TangleGeneratedModel):
    ended_executions: Any = None
    has_ended: Any = None
    total_executions: Any = None

class GetArtifactInfoResponse(TangleGeneratedModel):
    artifact_data: Any = None
    id: Any = None

class GetArtifactSignedUrlResponse(TangleGeneratedModel):
    signed_url: Any = None

class GetContainerExecutionLogResponse(TangleGeneratedModel):
    log_text: Any = None
    orchestration_error_message: Any = None
    system_error_exception_full: Any = None

class GetContainerExecutionStateResponse(TangleGeneratedModel):
    debug_info: Any = None
    ended_at: Any = None
    execution_nodes_linked_to_same_container_execution: Any = None
    exit_code: Any = None
    started_at: Any = None
    status: Any = None

class GetExecutionArtifactsResponse(TangleGeneratedModel):
    input_artifacts: Any = None
    output_artifacts: Any = None

class GetExecutionInfoResponse(TangleGeneratedModel):
    child_task_execution_ids: Any = None
    id: Any = None
    input_artifacts: Any = None
    output_artifacts: Any = None
    parent_execution_id: Any = None
    pipeline_run_id: Any = None
    task_spec: Any = None

class GetGraphExecutionStateResponse(TangleGeneratedModel):
    child_execution_status_stats: Any = None
    child_execution_status_summary: Any = None

class GetUserResponse(TangleGeneratedModel):
    id: Any = None
    permissions: Any = None

class GraphImplementation(TangleGeneratedModel):
    graph: Any = None

class GraphInputArgument(TangleGeneratedModel):
    graphinput: Any = Field(default=None, alias='graphInput')

class GraphInputReference(TangleGeneratedModel):
    inputname: Any = Field(default=None, alias='inputName')
    type: Any = None

class GraphSpec(TangleGeneratedModel):
    outputvalues: Any = Field(default=None, alias='outputValues')
    tasks: Any = None

class HTTPValidationError(TangleGeneratedModel):
    detail: Any = None

class IfPlaceholder(TangleGeneratedModel):
    if_: Any = Field(default=None, alias='if')

class IfPlaceholderStructure(TangleGeneratedModel):
    cond: Any = None
    else_: Any = Field(default=None, alias='else')
    then: Any = None

class InputPathPlaceholder(TangleGeneratedModel):
    inputpath: Any = Field(default=None, alias='inputPath')

class InputSpec(TangleGeneratedModel):
    annotations: Any = None
    default: Any = None
    description: Any = None
    name: Any = None
    optional: Any = None
    type: Any = None

class InputValuePlaceholder(TangleGeneratedModel):
    inputvalue: Any = Field(default=None, alias='inputValue')

class IsPresentPlaceholder(TangleGeneratedModel):
    ispresent: Any = Field(default=None, alias='isPresent')

class ListComponentLibrariesResponse(TangleGeneratedModel):
    component_libraries: Any = None

class ListPipelineJobsResponse(TangleGeneratedModel):
    next_page_token: Any = None
    pipeline_runs: Any = None

class ListPublishedComponentsResponse(TangleGeneratedModel):
    published_components: Any = None

class ListSecretsResponse(TangleGeneratedModel):
    secrets: Any = None

class MetadataSpec(TangleGeneratedModel):
    annotations: Any = None
    labels: Any = None

class OutputPathPlaceholder(TangleGeneratedModel):
    outputpath: Any = Field(default=None, alias='outputPath')

class OutputSpec(TangleGeneratedModel):
    annotations: Any = None
    description: Any = None
    name: Any = None
    type: Any = None

class PipelineRunResponse(TangleGeneratedModel):
    annotations: Any = None
    created_at: Any = None
    created_by: Any = None
    execution_status_stats: Any = None
    execution_summary: Any = None
    id: Any = None
    pipeline_name: Any = None
    root_execution_id: Any = None

class PublishedComponentResponse(TangleGeneratedModel):
    deprecated: Any = None
    digest: Any = None
    name: Any = None
    published_by: Any = None
    superseded_by: Any = None
    url: Any = None

class RetryStrategySpec(TangleGeneratedModel):
    maxretries: Any = Field(default=None, alias='maxRetries')

class SecretInfoResponse(TangleGeneratedModel):
    created_at: Any = None
    description: Any = None
    expires_at: Any = None
    secret_name: Any = None
    updated_at: Any = None

class TaskOutputArgument(TangleGeneratedModel):
    taskoutput: Any = Field(default=None, alias='taskOutput')

class TaskOutputReference(TangleGeneratedModel):
    outputname: Any = Field(default=None, alias='outputName')
    taskid: Any = Field(default=None, alias='taskId')

class TaskSpec(TangleGeneratedModel):
    annotations: Any = None
    arguments: Any = None
    componentref: Any = Field(default=None, alias='componentRef')
    executionoptions: Any = Field(default=None, alias='executionOptions')
    isenabled: Any = Field(default=None, alias='isEnabled')

class UserComponentLibraryPinsResponse(TangleGeneratedModel):
    component_library_ids: Any = None

class UserSettingsResponse(TangleGeneratedModel):
    settings: Any = None

class ValidationError(TangleGeneratedModel):
    ctx: Any = None
    input: Any = None
    loc: Any = None
    msg: Any = None
    type: Any = None

__all__ = ['ArtifactData', 'ArtifactDataResponse', 'ArtifactNodeIdResponse', 'ArtifactNodeResponse', 'BodyCreateApiPipelineRunsPost', 'BodyCreateSecretApiSecretsPost', 'BodySetSettingsApiUsersMeSettingsPatch', 'BodyUpdateSecretApiSecretsSecretNamePut', 'CachingStrategySpec', 'ComponentLibrary', 'ComponentLibraryFolder', 'ComponentLibraryResponse', 'ComponentReference', 'ComponentResponse', 'ComponentSpec', 'ConcatPlaceholder', 'ContainerExecutionStatus', 'ContainerImplementation', 'ContainerSpec', 'DynamicDataArgument', 'ExecutionNodeReference', 'ExecutionOptionsSpec', 'ExecutionStatusSummary', 'GetArtifactInfoResponse', 'GetArtifactSignedUrlResponse', 'GetContainerExecutionLogResponse', 'GetContainerExecutionStateResponse', 'GetExecutionArtifactsResponse', 'GetExecutionInfoResponse', 'GetGraphExecutionStateResponse', 'GetUserResponse', 'GraphImplementation', 'GraphInputArgument', 'GraphInputReference', 'GraphSpec', 'HTTPValidationError', 'IfPlaceholder', 'IfPlaceholderStructure', 'InputPathPlaceholder', 'InputSpec', 'InputValuePlaceholder', 'IsPresentPlaceholder', 'ListComponentLibrariesResponse', 'ListPipelineJobsResponse', 'ListPublishedComponentsResponse', 'ListSecretsResponse', 'MetadataSpec', 'OutputPathPlaceholder', 'OutputSpec', 'PipelineRunResponse', 'PublishedComponentResponse', 'RetryStrategySpec', 'SecretInfoResponse', 'TaskOutputArgument', 'TaskOutputReference', 'TaskSpec', 'UserComponentLibraryPinsResponse', 'UserSettingsResponse', 'ValidationError']
