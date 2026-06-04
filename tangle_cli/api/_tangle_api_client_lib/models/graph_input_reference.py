from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.graph_input_reference_type_type_1 import GraphInputReferenceTypeType1


T = TypeVar("T", bound="GraphInputReference")


@_attrs_define
class GraphInputReference:
    """
    Attributes:
        input_name (str):
        type_ (GraphInputReferenceTypeType1 | list[Any] | None | str | Unset):
    """

    input_name: str
    type_: GraphInputReferenceTypeType1 | list[Any] | None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.graph_input_reference_type_type_1 import GraphInputReferenceTypeType1

        input_name = self.input_name

        type_: dict[str, Any] | list[Any] | None | str | Unset
        if isinstance(self.type_, Unset):
            type_ = UNSET
        elif isinstance(self.type_, GraphInputReferenceTypeType1):
            type_ = self.type_.to_dict()
        elif isinstance(self.type_, list):
            type_ = self.type_

        else:
            type_ = self.type_

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "inputName": input_name,
            }
        )
        if type_ is not UNSET:
            field_dict["type"] = type_

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.graph_input_reference_type_type_1 import GraphInputReferenceTypeType1

        d = dict(src_dict)
        input_name = d.pop("inputName")

        def _parse_type_(data: object) -> GraphInputReferenceTypeType1 | list[Any] | None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                type_type_1 = GraphInputReferenceTypeType1.from_dict(data)

                return type_type_1
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            try:
                if not isinstance(data, list):
                    raise TypeError()
                type_type_2 = cast(list[Any], data)

                return type_type_2
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(GraphInputReferenceTypeType1 | list[Any] | None | str | Unset, data)

        type_ = _parse_type_(d.pop("type", UNSET))

        graph_input_reference = cls(
            input_name=input_name,
            type_=type_,
        )

        graph_input_reference.additional_properties = d
        return graph_input_reference

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
