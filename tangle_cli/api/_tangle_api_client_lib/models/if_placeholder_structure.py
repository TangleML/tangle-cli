from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.concat_placeholder import ConcatPlaceholder
    from ..models.if_placeholder import IfPlaceholder
    from ..models.input_path_placeholder import InputPathPlaceholder
    from ..models.input_value_placeholder import InputValuePlaceholder
    from ..models.is_present_placeholder import IsPresentPlaceholder
    from ..models.output_path_placeholder import OutputPathPlaceholder


T = TypeVar("T", bound="IfPlaceholderStructure")


@_attrs_define
class IfPlaceholderStructure:
    """
    Attributes:
        cond (bool | InputValuePlaceholder | IsPresentPlaceholder | str):
        then (list[ConcatPlaceholder | IfPlaceholder | InputPathPlaceholder | InputValuePlaceholder |
            OutputPathPlaceholder | str]):
        else_ (list[ConcatPlaceholder | IfPlaceholder | InputPathPlaceholder | InputValuePlaceholder |
            OutputPathPlaceholder | str] | None | Unset):
    """

    cond: bool | InputValuePlaceholder | IsPresentPlaceholder | str
    then: list[
        ConcatPlaceholder | IfPlaceholder | InputPathPlaceholder | InputValuePlaceholder | OutputPathPlaceholder | str
    ]
    else_: (
        list[
            ConcatPlaceholder
            | IfPlaceholder
            | InputPathPlaceholder
            | InputValuePlaceholder
            | OutputPathPlaceholder
            | str
        ]
        | None
        | Unset
    ) = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.concat_placeholder import ConcatPlaceholder
        from ..models.if_placeholder import IfPlaceholder
        from ..models.input_path_placeholder import InputPathPlaceholder
        from ..models.input_value_placeholder import InputValuePlaceholder
        from ..models.is_present_placeholder import IsPresentPlaceholder
        from ..models.output_path_placeholder import OutputPathPlaceholder

        cond: bool | dict[str, Any] | str
        if isinstance(self.cond, IsPresentPlaceholder):
            cond = self.cond.to_dict()
        elif isinstance(self.cond, InputValuePlaceholder):
            cond = self.cond.to_dict()
        else:
            cond = self.cond

        then = []
        for then_item_data in self.then:
            then_item: dict[str, Any] | str
            if isinstance(then_item_data, InputValuePlaceholder):
                then_item = then_item_data.to_dict()
            elif isinstance(then_item_data, InputPathPlaceholder):
                then_item = then_item_data.to_dict()
            elif isinstance(then_item_data, OutputPathPlaceholder):
                then_item = then_item_data.to_dict()
            elif isinstance(then_item_data, ConcatPlaceholder):
                then_item = then_item_data.to_dict()
            elif isinstance(then_item_data, IfPlaceholder):
                then_item = then_item_data.to_dict()
            else:
                then_item = then_item_data
            then.append(then_item)

        else_: list[dict[str, Any] | str] | None | Unset
        if isinstance(self.else_, Unset):
            else_ = UNSET
        elif isinstance(self.else_, list):
            else_ = []
            for else_type_0_item_data in self.else_:
                else_type_0_item: dict[str, Any] | str
                if isinstance(else_type_0_item_data, InputValuePlaceholder):
                    else_type_0_item = else_type_0_item_data.to_dict()
                elif isinstance(else_type_0_item_data, InputPathPlaceholder):
                    else_type_0_item = else_type_0_item_data.to_dict()
                elif isinstance(else_type_0_item_data, OutputPathPlaceholder):
                    else_type_0_item = else_type_0_item_data.to_dict()
                elif isinstance(else_type_0_item_data, ConcatPlaceholder):
                    else_type_0_item = else_type_0_item_data.to_dict()
                elif isinstance(else_type_0_item_data, IfPlaceholder):
                    else_type_0_item = else_type_0_item_data.to_dict()
                else:
                    else_type_0_item = else_type_0_item_data
                else_.append(else_type_0_item)

        else:
            else_ = self.else_

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "cond": cond,
                "then": then,
            }
        )
        if else_ is not UNSET:
            field_dict["else"] = else_

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.concat_placeholder import ConcatPlaceholder
        from ..models.if_placeholder import IfPlaceholder
        from ..models.input_path_placeholder import InputPathPlaceholder
        from ..models.input_value_placeholder import InputValuePlaceholder
        from ..models.is_present_placeholder import IsPresentPlaceholder
        from ..models.output_path_placeholder import OutputPathPlaceholder

        d = dict(src_dict)

        def _parse_cond(data: object) -> bool | InputValuePlaceholder | IsPresentPlaceholder | str:
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                cond_type_2 = IsPresentPlaceholder.from_dict(data)

                return cond_type_2
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                cond_type_3 = InputValuePlaceholder.from_dict(data)

                return cond_type_3
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(bool | InputValuePlaceholder | IsPresentPlaceholder | str, data)

        cond = _parse_cond(d.pop("cond"))

        then = []
        _then = d.pop("then")
        for then_item_data in _then:

            def _parse_then_item(
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
                    then_item_type_1 = InputValuePlaceholder.from_dict(data)

                    return then_item_type_1
                except (TypeError, ValueError, AttributeError, KeyError):
                    pass
                try:
                    if not isinstance(data, dict):
                        raise TypeError()
                    then_item_type_2 = InputPathPlaceholder.from_dict(data)

                    return then_item_type_2
                except (TypeError, ValueError, AttributeError, KeyError):
                    pass
                try:
                    if not isinstance(data, dict):
                        raise TypeError()
                    then_item_type_3 = OutputPathPlaceholder.from_dict(data)

                    return then_item_type_3
                except (TypeError, ValueError, AttributeError, KeyError):
                    pass
                try:
                    if not isinstance(data, dict):
                        raise TypeError()
                    then_item_type_4 = ConcatPlaceholder.from_dict(data)

                    return then_item_type_4
                except (TypeError, ValueError, AttributeError, KeyError):
                    pass
                try:
                    if not isinstance(data, dict):
                        raise TypeError()
                    then_item_type_5 = IfPlaceholder.from_dict(data)

                    return then_item_type_5
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

            then_item = _parse_then_item(then_item_data)

            then.append(then_item)

        def _parse_else_(
            data: object,
        ) -> (
            list[
                ConcatPlaceholder
                | IfPlaceholder
                | InputPathPlaceholder
                | InputValuePlaceholder
                | OutputPathPlaceholder
                | str
            ]
            | None
            | Unset
        ):
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                else_type_0 = []
                _else_type_0 = data
                for else_type_0_item_data in _else_type_0:

                    def _parse_else_type_0_item(
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
                            else_type_0_item_type_1 = InputValuePlaceholder.from_dict(data)

                            return else_type_0_item_type_1
                        except (TypeError, ValueError, AttributeError, KeyError):
                            pass
                        try:
                            if not isinstance(data, dict):
                                raise TypeError()
                            else_type_0_item_type_2 = InputPathPlaceholder.from_dict(data)

                            return else_type_0_item_type_2
                        except (TypeError, ValueError, AttributeError, KeyError):
                            pass
                        try:
                            if not isinstance(data, dict):
                                raise TypeError()
                            else_type_0_item_type_3 = OutputPathPlaceholder.from_dict(data)

                            return else_type_0_item_type_3
                        except (TypeError, ValueError, AttributeError, KeyError):
                            pass
                        try:
                            if not isinstance(data, dict):
                                raise TypeError()
                            else_type_0_item_type_4 = ConcatPlaceholder.from_dict(data)

                            return else_type_0_item_type_4
                        except (TypeError, ValueError, AttributeError, KeyError):
                            pass
                        try:
                            if not isinstance(data, dict):
                                raise TypeError()
                            else_type_0_item_type_5 = IfPlaceholder.from_dict(data)

                            return else_type_0_item_type_5
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

                    else_type_0_item = _parse_else_type_0_item(else_type_0_item_data)

                    else_type_0.append(else_type_0_item)

                return else_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(
                list[
                    ConcatPlaceholder
                    | IfPlaceholder
                    | InputPathPlaceholder
                    | InputValuePlaceholder
                    | OutputPathPlaceholder
                    | str
                ]
                | None
                | Unset,
                data,
            )

        else_ = _parse_else_(d.pop("else", UNSET))

        if_placeholder_structure = cls(
            cond=cond,
            then=then,
            else_=else_,
        )

        if_placeholder_structure.additional_properties = d
        return if_placeholder_structure

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
