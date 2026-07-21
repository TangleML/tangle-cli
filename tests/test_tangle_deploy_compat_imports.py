"""Import guards for the compatibility surface tangle-deploy consumes."""

from __future__ import annotations

import importlib.util


def test_tangle_deploy_required_import_surface_includes_static_client() -> None:
    import tangle_cli
    from tangle_cli import TangleDynamicDiscoveryClient, utils as utils_module
    from tangle_cli.dynamic_discovery_client import TangleDynamicDiscoveryClient as DynamicDiscoveryClient
    from tangle_cli.client import TangleApiClient as StaticClient
    from tangle_cli.args_container import ArgsContainer, ConfigFileError
    from tangle_cli.component_from_func import generate_component_yaml
    from tangle_cli.component_generator import regenerate_yaml
    from tangle_cli.component_publisher import (
        ComponentPublishContext,
        ComponentPublisher,
        ProcessingOutcome,
        ProcessingResult,
        deprecate_component,
        deprecate_old_components,
        perform_version_check,
        prepare_component_for_publish,
        publish_component,
        publish_component_to_tangle,
    )
    from tangle_cli.artifacts import ArtifactManager
    from tangle_cli.module_bundler import ModuleBundler
    from tangle_cli.secrets import SecretsManager
    from tangle_cli.version_manager import bump_version
    from tangle_cli.pipeline_run_details import PipelineRunDetails
    from tangle_cli.pipeline_run_search import PipelineRunSearch
    from tangle_cli.component_inspector import ComponentInspector
    from tangle_cli.pipeline_dehydrator import (
        DehydrateChoice,
        Jinja2ExportResult,
        PipelineDehydrator,
    )
    from tangle_cli.logger import (
        CaptureLogger,
        CliLogType,
        ConsoleLogger,
        Logger,
        NullLogger,
        _null_logger,
        _print_result,
        get_default_logger,
        run_with_logging,
    )
    from tangle_cli.models import (
        ArtifactComponentQuery,
        ArtifactInfo,
        ComponentInfo,
        ComponentSpec,
        ContainerState,
        DebugInfo,
        GraphExecutionState,
        KubernetesDebugInfo,
        KubernetesJobInfo,
        PageChunk,
        PipelineRun,
        RunDetails,
        SecretInfo,
        TaskSpec,
        UserInfo,
    )
    from tangle_cli.utils import (
        _CI_BRANCH_VARS,
        _CI_GIT_ROOT_VARS,
        _CI_REPO_URL_VARS,
        _CI_SHA_VARS,
        OrderedDict,
        TaskProcessor,
        _strip_text_from_graph,
        add_official_prefix,
        UnsetVarError,
        _extract_recursive_params,
        _extract_source_dir,
        _fill_from_ci_env,
        _literal_str_representer,
        _LiteralBlockDumper,
        _normalize_git_url,
        _strip_internal_annotations,
        apply_defaults,
        check_versions,
        clamp,
        compare_versions,
        compute_spec_digest,
        compute_text_digest,
        dump_yaml,
        expand_vars,
        find_documentation_path_for_yaml,
        get_component_ref_info,
        get_git_info,
        get_git_root,
        get_version_component,
        get_version_from_data,
        is_graph_task,
        is_subgraph_spec,
        normalize_annotation_paths,
        parse_yaml_string,
        resolve_input_path,
        set_component_yaml_path,
        tangle_verbose_enabled,
        traverse_pipeline_tasks,
    )

    assert TangleDynamicDiscoveryClient.__name__ == DynamicDiscoveryClient.__name__ == "TangleDynamicDiscoveryClient"
    assert StaticClient.__name__ == "TangleApiClient"
    assert not hasattr(tangle_cli, "TangleApiClient")
    assert importlib.util.find_spec("tangle_cli.client") is not None
    assert callable(StaticClient("https://api.test").set_verbose)
    assert ComponentSpec.__name__ == "ComponentSpec"
    assert PipelineRun.__name__ == "PipelineRun"
    assert ArgsContainer and ConfigFileError
    assert callable(generate_component_yaml)
    assert callable(regenerate_yaml)
    assert ComponentPublisher is not None
    assert ComponentPublishContext is not None
    assert ProcessingOutcome.SUCCESS.value == "success"
    assert ProcessingResult is not None
    assert callable(perform_version_check)
    assert callable(deprecate_old_components)
    assert callable(prepare_component_for_publish)
    assert callable(publish_component)
    assert callable(publish_component_to_tangle)
    assert callable(deprecate_component)
    assert callable(bump_version)
    assert ModuleBundler is not None
    assert ArtifactManager is not None
    assert callable(ArtifactManager.serialize_artifacts)
    assert SecretsManager is not None
    assert PipelineRunDetails is not None
    assert PipelineRunSearch is not None
    assert callable(SecretsManager.resolve_secret_value)
    assert callable(ComponentInspector.get_standard_library)
    assert callable(ComponentInspector.inspect_by_digest)
    assert callable(ComponentInspector.inspect_by_name)
    assert callable(ComponentInspector.search_components)
    assert callable(ComponentInspector.transparency_check)
    assert DehydrateChoice.AUTO == "a"
    assert Jinja2ExportResult is not None
    assert PipelineDehydrator is not None
    assert callable(get_default_logger)
    assert callable(run_with_logging)
    assert ConsoleLogger and CaptureLogger and NullLogger and Logger and CliLogType
    assert _null_logger is not None
    assert callable(_print_result)
    assert ArtifactComponentQuery and ArtifactInfo and ComponentInfo
    assert ContainerState and DebugInfo and GraphExecutionState
    assert KubernetesDebugInfo and KubernetesJobInfo and PageChunk and RunDetails
    assert SecretInfo and TaskSpec and UserInfo
    assert callable(_strip_text_from_graph)
    assert add_official_prefix("demo") == "[Official] demo"
    assert OrderedDict is not None and TaskProcessor is not None
    assert UnsetVarError is not None and _LiteralBlockDumper is not None
    assert callable(_extract_recursive_params)
    assert callable(_extract_source_dir)
    assert callable(_fill_from_ci_env)
    assert callable(_literal_str_representer)
    assert callable(_normalize_git_url)
    assert callable(_strip_internal_annotations)
    assert callable(apply_defaults)
    assert callable(check_versions)
    assert callable(clamp)
    assert callable(compare_versions)
    assert callable(compute_spec_digest)
    assert callable(compute_text_digest)
    assert callable(dump_yaml)
    assert callable(expand_vars)
    assert callable(find_documentation_path_for_yaml)
    assert callable(get_component_ref_info)
    assert callable(get_git_info)
    assert callable(get_git_root)
    assert callable(get_version_component)
    assert callable(get_version_from_data)
    assert callable(is_graph_task)
    assert callable(is_subgraph_spec)
    assert callable(normalize_annotation_paths)
    assert callable(parse_yaml_string)
    assert callable(resolve_input_path)
    assert callable(set_component_yaml_path)
    assert callable(tangle_verbose_enabled)
    assert callable(traverse_pipeline_tasks)
    assert _CI_GIT_ROOT_VARS and _CI_SHA_VARS and _CI_BRANCH_VARS and _CI_REPO_URL_VARS
    assert utils_module is not None


def test_tangle_deploy_pipeline_compile_import_surface() -> None:
    """Guards the pipeline-compile surface tangle-deploy consumes from tangle_cli.

    The internal ``tangle-deploy pipeline compile from-python`` command delegates
    to the OSS compile driver and only augments the Shopify zone-root seam. These
    are the exact ``tangle_cli.*`` imports the delegating ``tangle_deploy``
    modules make (the discovery originals imported the same names from
    ``tangle_deploy.*`` before the driver was migrated OSS).
    """
    from tangle_cli.python_pipeline.cfg import Cfg, _coerce_override, load_cfg
    from tangle_cli.python_pipeline.compiler_context import (
        BroadcastLayer,
        CompileContext,
        PipelineCompileKey,
        canonical_repo_path,
        overrides_fingerprint,
    )
    from tangle_cli.python_pipeline.emit import _TASK_URL_PLACEHOLDER, emit_pipeline
    from tangle_cli.python_pipeline.errors import CompileError
    from tangle_cli.python_pipeline.pipeline import PipelineFn
    from tangle_cli.python_pipeline.ref import CallableRef
    from tangle_cli.python_pipeline.registered import _REGISTERED_URL_PLACEHOLDER
    from tangle_cli.python_pipeline.subpipeline import (
        SubpipelineRef,
        _SUBPIPELINE_URL_PLACEHOLDER,
    )
    from tangle_cli.python_pipeline.trace import trace_pipeline
    from tangle_cli.python_pipeline.types import In
    from tangle_cli.schema_validation import (
        SchemaValidationError,
        validate_dehydrated_pipeline,
    )
    from tangle_cli.pipeline_compiler import (
        IMAGE_IDS,
        CompileResult,
        PipelineCompiler,
        ZONE_ROOT_MARKERS,
        compile_pipeline,
        get_image_id,
        register_image_id,
        resolve_image_id,
    )
    from tangle_cli.handler import TangleCliHandler

    # python_pipeline authoring / compile DSL.
    assert Cfg is not None
    assert callable(load_cfg) and callable(_coerce_override)
    assert BroadcastLayer and CompileContext and PipelineCompileKey
    assert callable(canonical_repo_path) and callable(overrides_fingerprint)
    assert callable(emit_pipeline)
    assert _TASK_URL_PLACEHOLDER is not None
    assert issubclass(CompileError, Exception)
    assert PipelineFn is not None and CallableRef is not None
    assert _REGISTERED_URL_PLACEHOLDER is not None
    assert SubpipelineRef is not None and _SUBPIPELINE_URL_PLACEHOLDER is not None
    assert callable(trace_pipeline)
    assert In is not None
    # schema validation.
    assert issubclass(SchemaValidationError, Exception)
    assert callable(validate_dehydrated_pipeline)
    # compile driver + object-oriented handler the command delegates to.
    assert callable(compile_pipeline)
    assert CompileResult is not None
    assert issubclass(PipelineCompiler, TangleCliHandler)
    assert callable(PipelineCompiler.compile_file)
    assert isinstance(ZONE_ROOT_MARKERS, list)
    assert isinstance(IMAGE_IDS, dict)
    assert callable(register_image_id)
    assert callable(get_image_id)
    assert callable(resolve_image_id)


def test_image_id_registry_is_empty_and_mutable_for_downstream() -> None:
    from tangle_cli import pipeline_compiler as pc

    assert pc.IMAGE_IDS == {}
    original = dict(pc.IMAGE_IDS)
    try:
        pc.register_image_id("eval-slim", "registry.example/eval-slim:latest")
        assert pc.get_image_id("eval-slim") == "registry.example/eval-slim:latest"
        assert pc.resolve_image_id("eval-slim", {"eval-slim": "override"}) == "override"
    finally:
        pc.IMAGE_IDS.clear()
        pc.IMAGE_IDS.update(original)
    assert pc.IMAGE_IDS == {}



def test_zone_root_markers_seam_is_empty_and_mutable_for_downstream() -> None:
    """OSS ships an EMPTY zone-root marker list; downstream distributions
    (tangle-deploy) append their own marker (e.g. the oasis component-root
    marker) to re-enable zone-root resolution. Mirrors the mutable-CI-vars
    provider-override contract above.
    """
    from tangle_cli import pipeline_compiler as pc

    assert pc.ZONE_ROOT_MARKERS == []  # empty in OSS by default
    original = list(pc.ZONE_ROOT_MARKERS)
    try:
        pc.ZONE_ROOT_MARKERS.append("oasis.pipeline_component_root.yaml")
        assert "oasis.pipeline_component_root.yaml" in pc.ZONE_ROOT_MARKERS
    finally:
        pc.ZONE_ROOT_MARKERS[:] = original
    assert pc.ZONE_ROOT_MARKERS == []


def test_ci_var_globals_are_mutable_for_downstream_provider_overrides() -> None:
    from tangle_cli import utils as u

    original_git_root = u._CI_GIT_ROOT_VARS
    original_sha = u._CI_SHA_VARS
    original_branch = u._CI_BRANCH_VARS
    original_repo = u._CI_REPO_URL_VARS
    try:
        u._CI_GIT_ROOT_VARS = ("APPLICATION_ROOT", *u._CI_GIT_ROOT_VARS)
        u._CI_SHA_VARS = ("PROVIDER_BUILD_COMMIT", *u._CI_SHA_VARS)
        u._CI_BRANCH_VARS = ("PROVIDER_BUILD_BRANCH", *u._CI_BRANCH_VARS)
        u._CI_REPO_URL_VARS = ("PROVIDER_BUILD_REPO", *u._CI_REPO_URL_VARS)

        assert u._CI_GIT_ROOT_VARS[0] == "APPLICATION_ROOT"
        assert u._CI_SHA_VARS[0] == "PROVIDER_BUILD_COMMIT"
        assert u._CI_BRANCH_VARS[0] == "PROVIDER_BUILD_BRANCH"
        assert u._CI_REPO_URL_VARS[0] == "PROVIDER_BUILD_REPO"
    finally:
        u._CI_GIT_ROOT_VARS = original_git_root
        u._CI_SHA_VARS = original_sha
        u._CI_BRANCH_VARS = original_branch
        u._CI_REPO_URL_VARS = original_repo
