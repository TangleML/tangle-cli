from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="GetContainerExecutionLogResponse")


@_attrs_define
class GetContainerExecutionLogResponse:
    """
    Attributes:
        log_text (None | str | Unset):
        system_error_exception_full (None | str | Unset):
        orchestration_error_message (None | str | Unset):
    """

    log_text: None | str | Unset = UNSET
    system_error_exception_full: None | str | Unset = UNSET
    orchestration_error_message: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        log_text: None | str | Unset
        if isinstance(self.log_text, Unset):
            log_text = UNSET
        else:
            log_text = self.log_text

        system_error_exception_full: None | str | Unset
        if isinstance(self.system_error_exception_full, Unset):
            system_error_exception_full = UNSET
        else:
            system_error_exception_full = self.system_error_exception_full

        orchestration_error_message: None | str | Unset
        if isinstance(self.orchestration_error_message, Unset):
            orchestration_error_message = UNSET
        else:
            orchestration_error_message = self.orchestration_error_message

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if log_text is not UNSET:
            field_dict["log_text"] = log_text
        if system_error_exception_full is not UNSET:
            field_dict["system_error_exception_full"] = system_error_exception_full
        if orchestration_error_message is not UNSET:
            field_dict["orchestration_error_message"] = orchestration_error_message

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_log_text(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        log_text = _parse_log_text(d.pop("log_text", UNSET))

        def _parse_system_error_exception_full(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        system_error_exception_full = _parse_system_error_exception_full(d.pop("system_error_exception_full", UNSET))

        def _parse_orchestration_error_message(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        orchestration_error_message = _parse_orchestration_error_message(d.pop("orchestration_error_message", UNSET))

        get_container_execution_log_response = cls(
            log_text=log_text,
            system_error_exception_full=system_error_exception_full,
            orchestration_error_message=orchestration_error_message,
        )

        get_container_execution_log_response.additional_properties = d
        return get_container_execution_log_response

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
