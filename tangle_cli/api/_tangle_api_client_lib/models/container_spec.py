from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.concat_placeholder import ConcatPlaceholder
    from ..models.container_spec_env_type_0 import ContainerSpecEnvType0
    from ..models.if_placeholder import IfPlaceholder
    from ..models.input_path_placeholder import InputPathPlaceholder
    from ..models.input_value_placeholder import InputValuePlaceholder
    from ..models.output_path_placeholder import OutputPathPlaceholder


T = TypeVar("T", bound="ContainerSpec")


@_attrs_define
class ContainerSpec:
    """
    Attributes:
        image (str):
        command (list[ConcatPlaceholder | IfPlaceholder | InputPathPlaceholder | InputValuePlaceholder |
            OutputPathPlaceholder | str] | None | Unset):
        args (list[ConcatPlaceholder | IfPlaceholder | InputPathPlaceholder | InputValuePlaceholder |
            OutputPathPlaceholder | str] | None | Unset):
        env (ContainerSpecEnvType0 | None | Unset):
    """

    image: str
    command: (
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
    args: (
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
    env: ContainerSpecEnvType0 | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.concat_placeholder import ConcatPlaceholder
        from ..models.container_spec_env_type_0 import ContainerSpecEnvType0
        from ..models.if_placeholder import IfPlaceholder
        from ..models.input_path_placeholder import InputPathPlaceholder
        from ..models.input_value_placeholder import InputValuePlaceholder
        from ..models.output_path_placeholder import OutputPathPlaceholder

        image = self.image

        command: list[dict[str, Any] | str] | None | Unset
        if isinstance(self.command, Unset):
            command = UNSET
        elif isinstance(self.command, list):
            command = []
            for command_type_0_item_data in self.command:
                command_type_0_item: dict[str, Any] | str
                if isinstance(command_type_0_item_data, InputValuePlaceholder):
                    command_type_0_item = command_type_0_item_data.to_dict()
                elif isinstance(command_type_0_item_data, InputPathPlaceholder):
                    command_type_0_item = command_type_0_item_data.to_dict()
                elif isinstance(command_type_0_item_data, OutputPathPlaceholder):
                    command_type_0_item = command_type_0_item_data.to_dict()
                elif isinstance(command_type_0_item_data, ConcatPlaceholder):
                    command_type_0_item = command_type_0_item_data.to_dict()
                elif isinstance(command_type_0_item_data, IfPlaceholder):
                    command_type_0_item = command_type_0_item_data.to_dict()
                else:
                    command_type_0_item = command_type_0_item_data
                command.append(command_type_0_item)

        else:
            command = self.command

        args: list[dict[str, Any] | str] | None | Unset
        if isinstance(self.args, Unset):
            args = UNSET
        elif isinstance(self.args, list):
            args = []
            for args_type_0_item_data in self.args:
                args_type_0_item: dict[str, Any] | str
                if isinstance(args_type_0_item_data, InputValuePlaceholder):
                    args_type_0_item = args_type_0_item_data.to_dict()
                elif isinstance(args_type_0_item_data, InputPathPlaceholder):
                    args_type_0_item = args_type_0_item_data.to_dict()
                elif isinstance(args_type_0_item_data, OutputPathPlaceholder):
                    args_type_0_item = args_type_0_item_data.to_dict()
                elif isinstance(args_type_0_item_data, ConcatPlaceholder):
                    args_type_0_item = args_type_0_item_data.to_dict()
                elif isinstance(args_type_0_item_data, IfPlaceholder):
                    args_type_0_item = args_type_0_item_data.to_dict()
                else:
                    args_type_0_item = args_type_0_item_data
                args.append(args_type_0_item)

        else:
            args = self.args

        env: dict[str, Any] | None | Unset
        if isinstance(self.env, Unset):
            env = UNSET
        elif isinstance(self.env, ContainerSpecEnvType0):
            env = self.env.to_dict()
        else:
            env = self.env

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "image": image,
            }
        )
        if command is not UNSET:
            field_dict["command"] = command
        if args is not UNSET:
            field_dict["args"] = args
        if env is not UNSET:
            field_dict["env"] = env

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.concat_placeholder import ConcatPlaceholder
        from ..models.container_spec_env_type_0 import ContainerSpecEnvType0
        from ..models.if_placeholder import IfPlaceholder
        from ..models.input_path_placeholder import InputPathPlaceholder
        from ..models.input_value_placeholder import InputValuePlaceholder
        from ..models.output_path_placeholder import OutputPathPlaceholder

        d = dict(src_dict)
        image = d.pop("image")

        def _parse_command(
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
                command_type_0 = []
                _command_type_0 = data
                for command_type_0_item_data in _command_type_0:

                    def _parse_command_type_0_item(
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
                            command_type_0_item_type_1 = InputValuePlaceholder.from_dict(data)

                            return command_type_0_item_type_1
                        except (TypeError, ValueError, AttributeError, KeyError):
                            pass
                        try:
                            if not isinstance(data, dict):
                                raise TypeError()
                            command_type_0_item_type_2 = InputPathPlaceholder.from_dict(data)

                            return command_type_0_item_type_2
                        except (TypeError, ValueError, AttributeError, KeyError):
                            pass
                        try:
                            if not isinstance(data, dict):
                                raise TypeError()
                            command_type_0_item_type_3 = OutputPathPlaceholder.from_dict(data)

                            return command_type_0_item_type_3
                        except (TypeError, ValueError, AttributeError, KeyError):
                            pass
                        try:
                            if not isinstance(data, dict):
                                raise TypeError()
                            command_type_0_item_type_4 = ConcatPlaceholder.from_dict(data)

                            return command_type_0_item_type_4
                        except (TypeError, ValueError, AttributeError, KeyError):
                            pass
                        try:
                            if not isinstance(data, dict):
                                raise TypeError()
                            command_type_0_item_type_5 = IfPlaceholder.from_dict(data)

                            return command_type_0_item_type_5
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

                    command_type_0_item = _parse_command_type_0_item(command_type_0_item_data)

                    command_type_0.append(command_type_0_item)

                return command_type_0
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

        command = _parse_command(d.pop("command", UNSET))

        def _parse_args(
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
                args_type_0 = []
                _args_type_0 = data
                for args_type_0_item_data in _args_type_0:

                    def _parse_args_type_0_item(
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
                            args_type_0_item_type_1 = InputValuePlaceholder.from_dict(data)

                            return args_type_0_item_type_1
                        except (TypeError, ValueError, AttributeError, KeyError):
                            pass
                        try:
                            if not isinstance(data, dict):
                                raise TypeError()
                            args_type_0_item_type_2 = InputPathPlaceholder.from_dict(data)

                            return args_type_0_item_type_2
                        except (TypeError, ValueError, AttributeError, KeyError):
                            pass
                        try:
                            if not isinstance(data, dict):
                                raise TypeError()
                            args_type_0_item_type_3 = OutputPathPlaceholder.from_dict(data)

                            return args_type_0_item_type_3
                        except (TypeError, ValueError, AttributeError, KeyError):
                            pass
                        try:
                            if not isinstance(data, dict):
                                raise TypeError()
                            args_type_0_item_type_4 = ConcatPlaceholder.from_dict(data)

                            return args_type_0_item_type_4
                        except (TypeError, ValueError, AttributeError, KeyError):
                            pass
                        try:
                            if not isinstance(data, dict):
                                raise TypeError()
                            args_type_0_item_type_5 = IfPlaceholder.from_dict(data)

                            return args_type_0_item_type_5
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

                    args_type_0_item = _parse_args_type_0_item(args_type_0_item_data)

                    args_type_0.append(args_type_0_item)

                return args_type_0
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

        args = _parse_args(d.pop("args", UNSET))

        def _parse_env(data: object) -> ContainerSpecEnvType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                env_type_0 = ContainerSpecEnvType0.from_dict(data)

                return env_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ContainerSpecEnvType0 | None | Unset, data)

        env = _parse_env(d.pop("env", UNSET))

        container_spec = cls(
            image=image,
            command=command,
            args=args,
            env=env,
        )

        container_spec.additional_properties = d
        return container_spec

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
