from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.dynamic_data_argument import DynamicDataArgument
    from ..models.graph_input_argument import GraphInputArgument
    from ..models.task_output_argument import TaskOutputArgument


T = TypeVar("T", bound="GraphSpecOutputValuesType0")


@_attrs_define
class GraphSpecOutputValuesType0:
    """ """

    additional_properties: dict[str, DynamicDataArgument | GraphInputArgument | str | TaskOutputArgument] = (
        _attrs_field(init=False, factory=dict)
    )

    def to_dict(self) -> dict[str, Any]:
        from ..models.dynamic_data_argument import DynamicDataArgument
        from ..models.graph_input_argument import GraphInputArgument
        from ..models.task_output_argument import TaskOutputArgument

        field_dict: dict[str, Any] = {}
        for prop_name, prop in self.additional_properties.items():
            if isinstance(prop, GraphInputArgument):
                field_dict[prop_name] = prop.to_dict()
            elif isinstance(prop, TaskOutputArgument):
                field_dict[prop_name] = prop.to_dict()
            elif isinstance(prop, DynamicDataArgument):
                field_dict[prop_name] = prop.to_dict()
            else:
                field_dict[prop_name] = prop

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.dynamic_data_argument import DynamicDataArgument
        from ..models.graph_input_argument import GraphInputArgument
        from ..models.task_output_argument import TaskOutputArgument

        d = dict(src_dict)
        graph_spec_output_values_type_0 = cls()

        additional_properties = {}
        for prop_name, prop_dict in d.items():

            def _parse_additional_property(
                data: object,
            ) -> DynamicDataArgument | GraphInputArgument | str | TaskOutputArgument:
                try:
                    if not isinstance(data, dict):
                        raise TypeError()
                    additional_property_type_1 = GraphInputArgument.from_dict(data)

                    return additional_property_type_1
                except (TypeError, ValueError, AttributeError, KeyError):
                    pass
                try:
                    if not isinstance(data, dict):
                        raise TypeError()
                    additional_property_type_2 = TaskOutputArgument.from_dict(data)

                    return additional_property_type_2
                except (TypeError, ValueError, AttributeError, KeyError):
                    pass
                try:
                    if not isinstance(data, dict):
                        raise TypeError()
                    additional_property_type_3 = DynamicDataArgument.from_dict(data)

                    return additional_property_type_3
                except (TypeError, ValueError, AttributeError, KeyError):
                    pass
                return cast(DynamicDataArgument | GraphInputArgument | str | TaskOutputArgument, data)

            additional_property = _parse_additional_property(prop_dict)

            additional_properties[prop_name] = additional_property

        graph_spec_output_values_type_0.additional_properties = additional_properties
        return graph_spec_output_values_type_0

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> DynamicDataArgument | GraphInputArgument | str | TaskOutputArgument:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: DynamicDataArgument | GraphInputArgument | str | TaskOutputArgument) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
