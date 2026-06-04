from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.published_component_response import PublishedComponentResponse


T = TypeVar("T", bound="ListPublishedComponentsResponse")


@_attrs_define
class ListPublishedComponentsResponse:
    """
    Attributes:
        published_components (list[PublishedComponentResponse]):
    """

    published_components: list[PublishedComponentResponse]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        published_components = []
        for published_components_item_data in self.published_components:
            published_components_item = published_components_item_data.to_dict()
            published_components.append(published_components_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "published_components": published_components,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.published_component_response import PublishedComponentResponse

        d = dict(src_dict)
        published_components = []
        _published_components = d.pop("published_components")
        for published_components_item_data in _published_components:
            published_components_item = PublishedComponentResponse.from_dict(published_components_item_data)

            published_components.append(published_components_item)

        list_published_components_response = cls(
            published_components=published_components,
        )

        list_published_components_response.additional_properties = d
        return list_published_components_response

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
