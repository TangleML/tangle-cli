"""Import guards for the compatibility surface tangle-deploy consumes."""

from __future__ import annotations

import importlib.util


def test_tangle_deploy_required_import_surface_includes_static_client() -> None:
    import tangle_cli
    from tangle_cli import TangleDynamicDiscoveryClient, utils as utils_module
    from tangle_cli.dynamic_discovery_client import TangleDynamicDiscoveryClient as DynamicDiscoveryClient
    from tangle_cli.client import TangleApiClient as StaticClient
    from tangle_cli.component_inspector import (
        get_standard_library,
        inspect_by_digest,
        inspect_by_name,
        search_components,
        transparency_check,
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
    assert callable(get_standard_library)
    assert callable(inspect_by_digest)
    assert callable(inspect_by_name)
    assert callable(search_components)
    assert callable(transparency_check)
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


def test_ci_var_globals_are_mutable_for_tangle_deploy_shopify_overrides() -> None:
    from tangle_cli import utils as u

    original_git_root = u._CI_GIT_ROOT_VARS
    original_sha = u._CI_SHA_VARS
    original_branch = u._CI_BRANCH_VARS
    original_repo = u._CI_REPO_URL_VARS
    try:
        u._CI_GIT_ROOT_VARS = ("APPLICATION_ROOT", *u._CI_GIT_ROOT_VARS)
        u._CI_SHA_VARS = ("SHOPIFY_BUILD_COMMIT", *u._CI_SHA_VARS)
        u._CI_BRANCH_VARS = ("SHOPIFY_BUILD_BRANCH", *u._CI_BRANCH_VARS)
        u._CI_REPO_URL_VARS = ("SHOPIFY_BUILD_REPO", *u._CI_REPO_URL_VARS)

        assert u._CI_GIT_ROOT_VARS[0] == "APPLICATION_ROOT"
        assert u._CI_SHA_VARS[0] == "SHOPIFY_BUILD_COMMIT"
        assert u._CI_BRANCH_VARS[0] == "SHOPIFY_BUILD_BRANCH"
        assert u._CI_REPO_URL_VARS[0] == "SHOPIFY_BUILD_REPO"
    finally:
        u._CI_GIT_ROOT_VARS = original_git_root
        u._CI_SHA_VARS = original_sha
        u._CI_BRANCH_VARS = original_branch
        u._CI_REPO_URL_VARS = original_repo
