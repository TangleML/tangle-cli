from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="ArtifactDataResponse")


@_attrs_define
class ArtifactDataResponse:
    """
    Attributes:
        total_size (int):
        is_dir (bool):
        uri (None | str | Unset):
        value (None | str | Unset):
    """

    total_size: int
    is_dir: bool
    uri: None | str | Unset = UNSET
    value: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        total_size = self.total_size

        is_dir = self.is_dir

        uri: None | str | Unset
        if isinstance(self.uri, Unset):
            uri = UNSET
        else:
            uri = self.uri

        value: None | str | Unset
        if isinstance(self.value, Unset):
            value = UNSET
        else:
            value = self.value

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "total_size": total_size,
                "is_dir": is_dir,
            }
        )
        if uri is not UNSET:
            field_dict["uri"] = uri
        if value is not UNSET:
            field_dict["value"] = value

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        total_size = d.pop("total_size")

        is_dir = d.pop("is_dir")

        def _parse_uri(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        uri = _parse_uri(d.pop("uri", UNSET))

        def _parse_value(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        value = _parse_value(d.pop("value", UNSET))

        artifact_data_response = cls(
            total_size=total_size,
            is_dir=is_dir,
            uri=uri,
            value=value,
        )

        artifact_data_response.additional_properties = d
        return artifact_data_response

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
