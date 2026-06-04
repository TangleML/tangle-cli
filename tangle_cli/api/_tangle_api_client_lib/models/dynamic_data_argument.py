from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.dynamic_data_argument_dynamic_data_type_1 import DynamicDataArgumentDynamicDataType1


T = TypeVar("T", bound="DynamicDataArgument")


@_attrs_define
class DynamicDataArgument:
    """
    Attributes:
        dynamic_data (DynamicDataArgumentDynamicDataType1 | str):
    """

    dynamic_data: DynamicDataArgumentDynamicDataType1 | str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.dynamic_data_argument_dynamic_data_type_1 import DynamicDataArgumentDynamicDataType1

        dynamic_data: dict[str, Any] | str
        if isinstance(self.dynamic_data, DynamicDataArgumentDynamicDataType1):
            dynamic_data = self.dynamic_data.to_dict()
        else:
            dynamic_data = self.dynamic_data

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "dynamicData": dynamic_data,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.dynamic_data_argument_dynamic_data_type_1 import DynamicDataArgumentDynamicDataType1

        d = dict(src_dict)

        def _parse_dynamic_data(data: object) -> DynamicDataArgumentDynamicDataType1 | str:
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                dynamic_data_type_1 = DynamicDataArgumentDynamicDataType1.from_dict(data)

                return dynamic_data_type_1
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(DynamicDataArgumentDynamicDataType1 | str, data)

        dynamic_data = _parse_dynamic_data(d.pop("dynamicData"))

        dynamic_data_argument = cls(
            dynamic_data=dynamic_data,
        )

        dynamic_data_argument.additional_properties = d
        return dynamic_data_argument

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
