from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="ExecutionStatusSummary")


@_attrs_define
class ExecutionStatusSummary:
    """
    Attributes:
        total_executions (int):
        ended_executions (int):
        has_ended (bool):
    """

    total_executions: int
    ended_executions: int
    has_ended: bool
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        total_executions = self.total_executions

        ended_executions = self.ended_executions

        has_ended = self.has_ended

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "total_executions": total_executions,
                "ended_executions": ended_executions,
                "has_ended": has_ended,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        total_executions = d.pop("total_executions")

        ended_executions = d.pop("ended_executions")

        has_ended = d.pop("has_ended")

        execution_status_summary = cls(
            total_executions=total_executions,
            ended_executions=ended_executions,
            has_ended=has_ended,
        )

        execution_status_summary.additional_properties = d
        return execution_status_summary

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
