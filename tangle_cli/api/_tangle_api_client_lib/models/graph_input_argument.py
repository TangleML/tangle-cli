from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.graph_input_reference import GraphInputReference


T = TypeVar("T", bound="GraphInputArgument")


@_attrs_define
class GraphInputArgument:
    """
    Attributes:
        graph_input (GraphInputReference):
    """

    graph_input: GraphInputReference
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        graph_input = self.graph_input.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "graphInput": graph_input,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.graph_input_reference import GraphInputReference

        d = dict(src_dict)
        graph_input = GraphInputReference.from_dict(d.pop("graphInput"))

        graph_input_argument = cls(
            graph_input=graph_input,
        )

        graph_input_argument.additional_properties = d
        return graph_input_argument

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
