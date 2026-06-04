from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.input_spec_annotations_type_0 import InputSpecAnnotationsType0
    from ..models.input_spec_type_type_1 import InputSpecTypeType1


T = TypeVar("T", bound="InputSpec")


@_attrs_define
class InputSpec:
    """
    Attributes:
        name (str):
        type_ (InputSpecTypeType1 | list[Any] | None | str | Unset):
        description (None | str | Unset):
        default (None | str | Unset):
        optional (bool | None | Unset):  Default: False.
        annotations (InputSpecAnnotationsType0 | None | Unset):
    """

    name: str
    type_: InputSpecTypeType1 | list[Any] | None | str | Unset = UNSET
    description: None | str | Unset = UNSET
    default: None | str | Unset = UNSET
    optional: bool | None | Unset = False
    annotations: InputSpecAnnotationsType0 | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.input_spec_annotations_type_0 import InputSpecAnnotationsType0
        from ..models.input_spec_type_type_1 import InputSpecTypeType1

        name = self.name

        type_: dict[str, Any] | list[Any] | None | str | Unset
        if isinstance(self.type_, Unset):
            type_ = UNSET
        elif isinstance(self.type_, InputSpecTypeType1):
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

        default: None | str | Unset
        if isinstance(self.default, Unset):
            default = UNSET
        else:
            default = self.default

        optional: bool | None | Unset
        if isinstance(self.optional, Unset):
            optional = UNSET
        else:
            optional = self.optional

        annotations: dict[str, Any] | None | Unset
        if isinstance(self.annotations, Unset):
            annotations = UNSET
        elif isinstance(self.annotations, InputSpecAnnotationsType0):
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
        if default is not UNSET:
            field_dict["default"] = default
        if optional is not UNSET:
            field_dict["optional"] = optional
        if annotations is not UNSET:
            field_dict["annotations"] = annotations

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.input_spec_annotations_type_0 import InputSpecAnnotationsType0
        from ..models.input_spec_type_type_1 import InputSpecTypeType1

        d = dict(src_dict)
        name = d.pop("name")

        def _parse_type_(data: object) -> InputSpecTypeType1 | list[Any] | None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                type_type_1 = InputSpecTypeType1.from_dict(data)

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
            return cast(InputSpecTypeType1 | list[Any] | None | str | Unset, data)

        type_ = _parse_type_(d.pop("type", UNSET))

        def _parse_description(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        description = _parse_description(d.pop("description", UNSET))

        def _parse_default(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        default = _parse_default(d.pop("default", UNSET))

        def _parse_optional(data: object) -> bool | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | None | Unset, data)

        optional = _parse_optional(d.pop("optional", UNSET))

        def _parse_annotations(data: object) -> InputSpecAnnotationsType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                annotations_type_0 = InputSpecAnnotationsType0.from_dict(data)

                return annotations_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(InputSpecAnnotationsType0 | None | Unset, data)

        annotations = _parse_annotations(d.pop("annotations", UNSET))

        input_spec = cls(
            name=name,
            type_=type_,
            description=description,
            default=default,
            optional=optional,
            annotations=annotations,
        )

        input_spec.additional_properties = d
        return input_spec

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
