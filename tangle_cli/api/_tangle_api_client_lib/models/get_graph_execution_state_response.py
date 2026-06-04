from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.execution_status_summary import ExecutionStatusSummary
    from ..models.get_graph_execution_state_response_child_execution_status_stats import (
        GetGraphExecutionStateResponseChildExecutionStatusStats,
    )


T = TypeVar("T", bound="GetGraphExecutionStateResponse")


@_attrs_define
class GetGraphExecutionStateResponse:
    """
    Attributes:
        child_execution_status_stats (GetGraphExecutionStateResponseChildExecutionStatusStats):
        child_execution_status_summary (ExecutionStatusSummary):
    """

    child_execution_status_stats: GetGraphExecutionStateResponseChildExecutionStatusStats
    child_execution_status_summary: ExecutionStatusSummary
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        child_execution_status_stats = self.child_execution_status_stats.to_dict()

        child_execution_status_summary = self.child_execution_status_summary.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "child_execution_status_stats": child_execution_status_stats,
                "child_execution_status_summary": child_execution_status_summary,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.execution_status_summary import ExecutionStatusSummary
        from ..models.get_graph_execution_state_response_child_execution_status_stats import (
            GetGraphExecutionStateResponseChildExecutionStatusStats,
        )

        d = dict(src_dict)
        child_execution_status_stats = GetGraphExecutionStateResponseChildExecutionStatusStats.from_dict(
            d.pop("child_execution_status_stats")
        )

        child_execution_status_summary = ExecutionStatusSummary.from_dict(d.pop("child_execution_status_summary"))

        get_graph_execution_state_response = cls(
            child_execution_status_stats=child_execution_status_stats,
            child_execution_status_summary=child_execution_status_summary,
        )

        get_graph_execution_state_response.additional_properties = d
        return get_graph_execution_state_response

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
