"""Round-trip tests for the API-contract dataclasses in :mod:`tangle_cli.models`."""

from __future__ import annotations

from tangle_cli.generated.models import (
    ComponentSpec as GeneratedComponentSpec,
    GetExecutionInfoResponse,
)
from tangle_cli.models import (
    ComponentInfo,
    ComponentSpec,
    ContainerState,
    GraphExecutionState,
    PipelineRun,
    SecretInfo,
    UserInfo,
    add_official_prefix,
)


class TestPipelineRun:
    def test_from_dict_preserves_raw(self):
        data = {"id": "run-1", "root_execution_id": "exec-1", "created_by": "alice"}
        run = PipelineRun.from_dict(data)
        assert run.id == "run-1"
        assert run.root_execution_id == "exec-1"
        assert run.created_by == "alice"
        assert run.raw == data

    def test_from_dict_with_missing_optionals(self):
        run = PipelineRun.from_dict({"id": "run-1"})
        assert run.id == "run-1"
        assert run.root_execution_id is None
        assert run.created_by is None


class TestGraphExecutionState:
    def test_status_totals_aggregates(self):
        state = GraphExecutionState.from_dict({
            "child_execution_status_stats": {
                "exec-a": {"SUCCEEDED": 1},
                "exec-b": {"SUCCEEDED": 2, "RUNNING": 1, "FAILED": 1},
            }
        })
        assert state.status_totals == {"SUCCEEDED": 3, "RUNNING": 1, "FAILED": 1}

    def test_failed_execution_ids(self):
        state = GraphExecutionState.from_dict({
            "child_execution_status_stats": {
                "exec-a": {"SUCCEEDED": 1},
                "exec-b": {"FAILED": 1},
                "exec-c": {"SYSTEM_ERROR": 1},
                "exec-d": {"RUNNING": 1},
            }
        })
        assert set(state.failed_execution_ids) == {"exec-b", "exec-c"}


class TestComponentSpec:
    def test_component_spec_is_generated_model_with_extensions(self):
        assert ComponentSpec is GeneratedComponentSpec
        assert ComponentSpec.__mro__[1].__name__ == "ComponentSpecExtensions"

    def test_from_yaml_basic(self):
        yaml_text = """\
name: my-component
description: a test component
metadata:
  annotations:
    version: 1.2.3
inputs:
  - {name: in1, type: String}
outputs:
  - {name: out1, type: String}
implementation:
  container:
    image: alpine:3.18
"""
        spec = ComponentSpec.from_yaml(yaml_text)
        assert spec.name == "my-component"
        assert spec.version == "1.2.3"
        assert spec.description == "a test component"
        assert spec.inputs == [{"name": "in1", "type": "String"}]
        assert spec.outputs == [{"name": "out1", "type": "String"}]
        assert spec.implementation == {"container": {"image": "alpine:3.18"}}
        assert spec.text == yaml_text

    def test_from_dict_parses_text_when_spec_missing(self):
        api_response = {
            "digest": "abc123",
            "text": "name: x\nmetadata:\n  annotations:\n    version: \"0.1\"\n",
        }
        spec = ComponentSpec.from_dict(api_response)
        assert spec.digest == "abc123"
        assert spec.name == "x"
        assert spec.version == "0.1"

    def test_strip_implementation_removes_container(self):
        spec = ComponentSpec.from_dict({
            "digest": "d",
            "text": "",
            "spec": {
                "name": "c",
                "implementation": {"container": {"image": "alpine"}},
            },
        })
        assert spec.implementation == {"container": {"image": "alpine"}}
        spec.strip_implementation()
        assert spec.implementation is None
        assert "implementation" not in spec.data

    def test_ensure_digest_from_text(self):
        spec = ComponentSpec(text="name: c\n")
        digest = spec.ensure_digest()
        assert digest
        assert spec.digest == digest
        # Calling again is idempotent.
        assert spec.ensure_digest() == digest

    def test_roundtrip_via_yaml(self):
        yaml_text = "name: roundtrip\nmetadata:\n  annotations:\n    version: '2.0'\n"
        spec = ComponentSpec.from_yaml(yaml_text)
        re_dumped = spec.to_yaml()
        # Re-parsing the dumped YAML should yield the same name/version.
        spec2 = ComponentSpec.from_yaml(re_dumped)
        assert spec2.name == "roundtrip"
        assert spec2.version == "2.0"


class TestComponentInfo:
    def test_from_dict_to_dict_minimal(self):
        info = ComponentInfo.from_dict({
            "name": "[Official] foo",
            "digest": "abc",
            "version": "1.0",
            "published_by": "alice@example.com",
            "deprecated": False,
        })
        out = info.to_dict()
        assert out == {
            "digest": "abc",
            "version": "1.0",
            "published_by": "alice@example.com",
            "deprecated": False,
        }


class TestContainerState:
    def test_resolves_pod_name_from_kubernetes_debug_info(self):
        state = ContainerState.from_dict({
            "status": "RUNNING",
            "debug_info": {
                "kubernetes": {"pod_name": "pod-xyz", "namespace": "ns-1"},
            },
        })
        assert state.status == "RUNNING"
        assert state.pod_name == "pod-xyz"
        assert state.namespace == "ns-1"

    def test_falls_back_to_kubernetes_job_name(self):
        state = ContainerState.from_dict({
            "status": "SUCCEEDED",
            "debug_info": {
                "kubernetes_job": {"job_name": "job-abc"},
            },
        })
        assert state.pod_name == "job-abc"


class TestUserAndSecret:
    def test_user_info_minimal(self):
        u = UserInfo(id="u-1", permissions=["read"])
        assert u.id == "u-1"
        assert u.permissions == ["read"]

    def test_secret_info_from_dict(self):
        s = SecretInfo.from_dict({
            "secret_name": "mysecret",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-02T00:00:00Z",
            "description": "test",
        })
        assert s.secret_name == "mysecret"
        assert s.description == "test"
        assert s.expires_at is None


class TestHelpers:
    def test_add_official_prefix_idempotent(self):
        assert add_official_prefix("foo") == "[Official] foo"
        assert add_official_prefix("[Official] foo") == "[Official] foo"
        assert add_official_prefix(None) is None
        assert add_official_prefix("") == ""


class TestGetExecutionInfoResponse:
    def test_execution_details_generated_model_has_extensions(self):
        assert GetExecutionInfoResponse.__mro__[1].__name__ == "GetExecutionInfoResponseExtensions"

    def test_from_dict_parses_artifacts(self):
        ed = GetExecutionInfoResponse.from_dict({
            "id": "exec-1",
            "task_spec": {"componentRef": {"spec": {"name": "task"}}},
            "input_artifacts": {"in1": {"id": "art-1"}},
            "output_artifacts": {"out1": {"id": "art-2"}, "noisy": {}},
        })
        assert ed.id == "exec-1"
        assert ed.input_artifacts == {"in1": "art-1"}
        # Entries without an "id" key are dropped.
        assert ed.output_artifacts == {"out1": "art-2"}
        assert ed.raw["id"] == "exec-1"
        assert ed.child_executions == {}
