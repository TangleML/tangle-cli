from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.graph_spec_output_values_type_0 import GraphSpecOutputValuesType0
    from ..models.graph_spec_tasks import GraphSpecTasks


T = TypeVar("T", bound="GraphSpec")


@_attrs_define
class GraphSpec:
    """
    Attributes:
        tasks (GraphSpecTasks):
        output_values (GraphSpecOutputValuesType0 | None | Unset):
    """

    tasks: GraphSpecTasks
    output_values: GraphSpecOutputValuesType0 | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.graph_spec_output_values_type_0 import GraphSpecOutputValuesType0

        tasks = self.tasks.to_dict()

        output_values: dict[str, Any] | None | Unset
        if isinstance(self.output_values, Unset):
            output_values = UNSET
        elif isinstance(self.output_values, GraphSpecOutputValuesType0):
            output_values = self.output_values.to_dict()
        else:
            output_values = self.output_values

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "tasks": tasks,
            }
        )
        if output_values is not UNSET:
            field_dict["outputValues"] = output_values

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.graph_spec_output_values_type_0 import GraphSpecOutputValuesType0
        from ..models.graph_spec_tasks import GraphSpecTasks

        d = dict(src_dict)
        tasks = GraphSpecTasks.from_dict(d.pop("tasks"))

        def _parse_output_values(data: object) -> GraphSpecOutputValuesType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                output_values_type_0 = GraphSpecOutputValuesType0.from_dict(data)

                return output_values_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(GraphSpecOutputValuesType0 | None | Unset, data)

        output_values = _parse_output_values(d.pop("outputValues", UNSET))

        graph_spec = cls(
            tasks=tasks,
            output_values=output_values,
        )

        graph_spec.additional_properties = d
        return graph_spec

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
