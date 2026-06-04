from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.if_placeholder_structure import IfPlaceholderStructure


T = TypeVar("T", bound="IfPlaceholder")


@_attrs_define
class IfPlaceholder:
    """
    Attributes:
        if_ (IfPlaceholderStructure):
    """

    if_: IfPlaceholderStructure
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        if_ = self.if_.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "if": if_,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.if_placeholder_structure import IfPlaceholderStructure

        d = dict(src_dict)
        if_ = IfPlaceholderStructure.from_dict(d.pop("if"))

        if_placeholder = cls(
            if_=if_,
        )

        if_placeholder.additional_properties = d
        return if_placeholder

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
