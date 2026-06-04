from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="ExecutionNodeReference")


@_attrs_define
class ExecutionNodeReference:
    """
    Attributes:
        execution_node_id (str):
        pipeline_run_id (None | str):
    """

    execution_node_id: str
    pipeline_run_id: None | str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        execution_node_id = self.execution_node_id

        pipeline_run_id: None | str
        pipeline_run_id = self.pipeline_run_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "execution_node_id": execution_node_id,
                "pipeline_run_id": pipeline_run_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        execution_node_id = d.pop("execution_node_id")

        def _parse_pipeline_run_id(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        pipeline_run_id = _parse_pipeline_run_id(d.pop("pipeline_run_id"))

        execution_node_reference = cls(
            execution_node_id=execution_node_id,
            pipeline_run_id=pipeline_run_id,
        )

        execution_node_reference.additional_properties = d
        return execution_node_reference

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
