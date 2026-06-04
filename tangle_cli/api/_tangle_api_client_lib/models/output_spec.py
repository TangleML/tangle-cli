from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.output_spec_annotations_type_0 import OutputSpecAnnotationsType0
    from ..models.output_spec_type_type_1 import OutputSpecTypeType1


T = TypeVar("T", bound="OutputSpec")


@_attrs_define
class OutputSpec:
    """
    Attributes:
        name (str):
        type_ (list[Any] | None | OutputSpecTypeType1 | str | Unset):
        description (None | str | Unset):
        annotations (None | OutputSpecAnnotationsType0 | Unset):
    """

    name: str
    type_: list[Any] | None | OutputSpecTypeType1 | str | Unset = UNSET
    description: None | str | Unset = UNSET
    annotations: None | OutputSpecAnnotationsType0 | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.output_spec_annotations_type_0 import OutputSpecAnnotationsType0
        from ..models.output_spec_type_type_1 import OutputSpecTypeType1

        name = self.name

        type_: dict[str, Any] | list[Any] | None | str | Unset
        if isinstance(self.type_, Unset):
            type_ = UNSET
        elif isinstance(self.type_, OutputSpecTypeType1):
            type_ = self.type_.to_dict()
        elif isinstance(self.type_, list):
            type_ = self.type_

        else:
            type_ = self.type_

        description: None | str | Unset
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

        annotations: dict[str, Any] | None | Unset
        if isinstance(self.annotations, Unset):
            annotations = UNSET
        elif isinstance(self.annotations, OutputSpecAnnotationsType0):
            annotations = self.annotations.to_dict()
        else:
            annotations = self.annotations

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "name": name,
            }
        )
        if type_ is not UNSET:
            field_dict["type"] = type_
        if description is not UNSET:
            field_dict["description"] = description
        if annotations is not UNSET:
            field_dict["annotations"] = annotations

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.output_spec_annotations_type_0 import OutputSpecAnnotationsType0
        from ..models.output_spec_type_type_1 import OutputSpecTypeType1

        d = dict(src_dict)
        name = d.pop("name")

        def _parse_type_(data: object) -> list[Any] | None | OutputSpecTypeType1 | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                type_type_1 = OutputSpecTypeType1.from_dict(data)

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
            return cast(list[Any] | None | OutputSpecTypeType1 | str | Unset, data)

        type_ = _parse_type_(d.pop("type", UNSET))

        def _parse_description(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        description = _parse_description(d.pop("description", UNSET))

        def _parse_annotations(data: object) -> None | OutputSpecAnnotationsType0 | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                annotations_type_0 = OutputSpecAnnotationsType0.from_dict(data)

                return annotations_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | OutputSpecAnnotationsType0 | Unset, data)

        annotations = _parse_annotations(d.pop("annotations", UNSET))

        output_spec = cls(
            name=name,
            type_=type_,
            description=description,
            annotations=annotations,
        )

        output_spec.additional_properties = d
        return output_spec

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
