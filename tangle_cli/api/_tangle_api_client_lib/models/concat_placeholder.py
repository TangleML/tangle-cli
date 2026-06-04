from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.if_placeholder import IfPlaceholder
    from ..models.input_path_placeholder import InputPathPlaceholder
    from ..models.input_value_placeholder import InputValuePlaceholder
    from ..models.output_path_placeholder import OutputPathPlaceholder


T = TypeVar("T", bound="ConcatPlaceholder")


@_attrs_define
class ConcatPlaceholder:
    """
    Attributes:
        concat (list[ConcatPlaceholder | IfPlaceholder | InputPathPlaceholder | InputValuePlaceholder |
            OutputPathPlaceholder | str]):
    """

    concat: list[
        ConcatPlaceholder | IfPlaceholder | InputPathPlaceholder | InputValuePlaceholder | OutputPathPlaceholder | str
    ]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.if_placeholder import IfPlaceholder
        from ..models.input_path_placeholder import InputPathPlaceholder
        from ..models.input_value_placeholder import InputValuePlaceholder
        from ..models.output_path_placeholder import OutputPathPlaceholder

        concat = []
        for concat_item_data in self.concat:
            concat_item: dict[str, Any] | str
            if isinstance(concat_item_data, InputValuePlaceholder):
                concat_item = concat_item_data.to_dict()
            elif isinstance(concat_item_data, InputPathPlaceholder):
                concat_item = concat_item_data.to_dict()
            elif isinstance(concat_item_data, OutputPathPlaceholder):
                concat_item = concat_item_data.to_dict()
            elif isinstance(concat_item_data, ConcatPlaceholder):
                concat_item = concat_item_data.to_dict()
            elif isinstance(concat_item_data, IfPlaceholder):
                concat_item = concat_item_data.to_dict()
            else:
                concat_item = concat_item_data
            concat.append(concat_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "concat": concat,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.if_placeholder import IfPlaceholder
        from ..models.input_path_placeholder import InputPathPlaceholder
        from ..models.input_value_placeholder import InputValuePlaceholder
        from ..models.output_path_placeholder import OutputPathPlaceholder

        d = dict(src_dict)
        concat = []
        _concat = d.pop("concat")
        for concat_item_data in _concat:

            def _parse_concat_item(
                data: object,
            ) -> (
                ConcatPlaceholder
                | IfPlaceholder
                | InputPathPlaceholder
                | InputValuePlaceholder
                | OutputPathPlaceholder
                | str
            ):
                try:
                    if not isinstance(data, dict):
                        raise TypeError()
                    concat_item_type_1 = InputValuePlaceholder.from_dict(data)

                    return concat_item_type_1
                except (TypeError, ValueError, AttributeError, KeyError):
                    pass
                try:
                    if not isinstance(data, dict):
                        raise TypeError()
                    concat_item_type_2 = InputPathPlaceholder.from_dict(data)

                    return concat_item_type_2
                except (TypeError, ValueError, AttributeError, KeyError):
                    pass
                try:
                    if not isinstance(data, dict):
                        raise TypeError()
                    concat_item_type_3 = OutputPathPlaceholder.from_dict(data)

                    return concat_item_type_3
                except (TypeError, ValueError, AttributeError, KeyError):
                    pass
                try:
                    if not isinstance(data, dict):
                        raise TypeError()
                    concat_item_type_4 = ConcatPlaceholder.from_dict(data)

                    return concat_item_type_4
                except (TypeError, ValueError, AttributeError, KeyError):
                    pass
                try:
                    if not isinstance(data, dict):
                        raise TypeError()
                    concat_item_type_5 = IfPlaceholder.from_dict(data)

                    return concat_item_type_5
                except (TypeError, ValueError, AttributeError, KeyError):
                    pass
                return cast(
                    ConcatPlaceholder
                    | IfPlaceholder
                    | InputPathPlaceholder
                    | InputValuePlaceholder
                    | OutputPathPlaceholder
                    | str,
                    data,
                )

            concat_item = _parse_concat_item(concat_item_data)

            concat.append(concat_item)

        concat_placeholder = cls(
            concat=concat,
        )

        concat_placeholder.additional_properties = d
        return concat_placeholder

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
