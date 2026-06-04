from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.get_execution_info_response_child_task_execution_ids import (
        GetExecutionInfoResponseChildTaskExecutionIds,
    )
    from ..models.get_execution_info_response_input_artifacts_type_0 import GetExecutionInfoResponseInputArtifactsType0
    from ..models.get_execution_info_response_output_artifacts_type_0 import (
        GetExecutionInfoResponseOutputArtifactsType0,
    )
    from ..models.task_spec import TaskSpec


T = TypeVar("T", bound="GetExecutionInfoResponse")


@_attrs_define
class GetExecutionInfoResponse:
    """
    Attributes:
        id (str):
        task_spec (TaskSpec):
        child_task_execution_ids (GetExecutionInfoResponseChildTaskExecutionIds):
        parent_execution_id (None | str | Unset):
        pipeline_run_id (None | str | Unset):
        input_artifacts (GetExecutionInfoResponseInputArtifactsType0 | None | Unset):
        output_artifacts (GetExecutionInfoResponseOutputArtifactsType0 | None | Unset):
    """

    id: str
    task_spec: TaskSpec
    child_task_execution_ids: GetExecutionInfoResponseChildTaskExecutionIds
    parent_execution_id: None | str | Unset = UNSET
    pipeline_run_id: None | str | Unset = UNSET
    input_artifacts: GetExecutionInfoResponseInputArtifactsType0 | None | Unset = UNSET
    output_artifacts: GetExecutionInfoResponseOutputArtifactsType0 | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.get_execution_info_response_input_artifacts_type_0 import (
            GetExecutionInfoResponseInputArtifactsType0,
        )
        from ..models.get_execution_info_response_output_artifacts_type_0 import (
            GetExecutionInfoResponseOutputArtifactsType0,
        )

        id = self.id

        task_spec = self.task_spec.to_dict()

        child_task_execution_ids = self.child_task_execution_ids.to_dict()

        parent_execution_id: None | str | Unset
        if isinstance(self.parent_execution_id, Unset):
            parent_execution_id = UNSET
        else:
            parent_execution_id = self.parent_execution_id

        pipeline_run_id: None | str | Unset
        if isinstance(self.pipeline_run_id, Unset):
            pipeline_run_id = UNSET
        else:
            pipeline_run_id = self.pipeline_run_id

        input_artifacts: dict[str, Any] | None | Unset
        if isinstance(self.input_artifacts, Unset):
            input_artifacts = UNSET
        elif isinstance(self.input_artifacts, GetExecutionInfoResponseInputArtifactsType0):
            input_artifacts = self.input_artifacts.to_dict()
        else:
            input_artifacts = self.input_artifacts

        output_artifacts: dict[str, Any] | None | Unset
        if isinstance(self.output_artifacts, Unset):
            output_artifacts = UNSET
        elif isinstance(self.output_artifacts, GetExecutionInfoResponseOutputArtifactsType0):
            output_artifacts = self.output_artifacts.to_dict()
        else:
            output_artifacts = self.output_artifacts

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "task_spec": task_spec,
                "child_task_execution_ids": child_task_execution_ids,
            }
        )
        if parent_execution_id is not UNSET:
            field_dict["parent_execution_id"] = parent_execution_id
        if pipeline_run_id is not UNSET:
            field_dict["pipeline_run_id"] = pipeline_run_id
        if input_artifacts is not UNSET:
            field_dict["input_artifacts"] = input_artifacts
        if output_artifacts is not UNSET:
            field_dict["output_artifacts"] = output_artifacts

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.get_execution_info_response_child_task_execution_ids import (
            GetExecutionInfoResponseChildTaskExecutionIds,
        )
        from ..models.get_execution_info_response_input_artifacts_type_0 import (
            GetExecutionInfoResponseInputArtifactsType0,
        )
        from ..models.get_execution_info_response_output_artifacts_type_0 import (
            GetExecutionInfoResponseOutputArtifactsType0,
        )
        from ..models.task_spec import TaskSpec

        d = dict(src_dict)
        id = d.pop("id")

        task_spec = TaskSpec.from_dict(d.pop("task_spec"))

        child_task_execution_ids = GetExecutionInfoResponseChildTaskExecutionIds.from_dict(
            d.pop("child_task_execution_ids")
        )

        def _parse_parent_execution_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        parent_execution_id = _parse_parent_execution_id(d.pop("parent_execution_id", UNSET))

        def _parse_pipeline_run_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        pipeline_run_id = _parse_pipeline_run_id(d.pop("pipeline_run_id", UNSET))

        def _parse_input_artifacts(data: object) -> GetExecutionInfoResponseInputArtifactsType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                input_artifacts_type_0 = GetExecutionInfoResponseInputArtifactsType0.from_dict(data)

                return input_artifacts_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(GetExecutionInfoResponseInputArtifactsType0 | None | Unset, data)

        input_artifacts = _parse_input_artifacts(d.pop("input_artifacts", UNSET))

        def _parse_output_artifacts(data: object) -> GetExecutionInfoResponseOutputArtifactsType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                output_artifacts_type_0 = GetExecutionInfoResponseOutputArtifactsType0.from_dict(data)

                return output_artifacts_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(GetExecutionInfoResponseOutputArtifactsType0 | None | Unset, data)

        output_artifacts = _parse_output_artifacts(d.pop("output_artifacts", UNSET))

        get_execution_info_response = cls(
            id=id,
            task_spec=task_spec,
            child_task_execution_ids=child_task_execution_ids,
            parent_execution_id=parent_execution_id,
            pipeline_run_id=pipeline_run_id,
            input_artifacts=input_artifacts,
            output_artifacts=output_artifacts,
        )

        get_execution_info_response.additional_properties = d
        return get_execution_info_response

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
