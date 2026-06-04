from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.component_library_response import ComponentLibraryResponse


T = TypeVar("T", bound="ListComponentLibrariesResponse")


@_attrs_define
class ListComponentLibrariesResponse:
    """
    Attributes:
        component_libraries (list[ComponentLibraryResponse]):
    """

    component_libraries: list[ComponentLibraryResponse]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        component_libraries = []
        for component_libraries_item_data in self.component_libraries:
            component_libraries_item = component_libraries_item_data.to_dict()
            component_libraries.append(component_libraries_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "component_libraries": component_libraries,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.component_library_response import ComponentLibraryResponse

        d = dict(src_dict)
        component_libraries = []
        _component_libraries = d.pop("component_libraries")
        for component_libraries_item_data in _component_libraries:
            component_libraries_item = ComponentLibraryResponse.from_dict(component_libraries_item_data)

            component_libraries.append(component_libraries_item)

        list_component_libraries_response = cls(
            component_libraries=component_libraries,
        )

        list_component_libraries_response.additional_properties = d
        return list_component_libraries_response

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
