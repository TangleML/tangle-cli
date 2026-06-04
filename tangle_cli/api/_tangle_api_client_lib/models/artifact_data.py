from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.artifact_data_extra_data_type_0 import ArtifactDataExtraDataType0


T = TypeVar("T", bound="ArtifactData")


@_attrs_define
class ArtifactData:
    """
    Attributes:
        total_size (int):
        is_dir (bool):
        hash_ (str):
        uri (None | str | Unset):
        value (None | str | Unset):
        created_at (datetime.datetime | None | Unset):
        deleted_at (datetime.datetime | None | Unset):
        extra_data (ArtifactDataExtraDataType0 | None | Unset):
    """

    total_size: int
    is_dir: bool
    hash_: str
    uri: None | str | Unset = UNSET
    value: None | str | Unset = UNSET
    created_at: datetime.datetime | None | Unset = UNSET
    deleted_at: datetime.datetime | None | Unset = UNSET
    extra_data: ArtifactDataExtraDataType0 | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.artifact_data_extra_data_type_0 import ArtifactDataExtraDataType0

        total_size = self.total_size

        is_dir = self.is_dir

        hash_ = self.hash_

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

        created_at: None | str | Unset
        if isinstance(self.created_at, Unset):
            created_at = UNSET
        elif isinstance(self.created_at, datetime.datetime):
            created_at = self.created_at.isoformat()
        else:
            created_at = self.created_at

        deleted_at: None | str | Unset
        if isinstance(self.deleted_at, Unset):
            deleted_at = UNSET
        elif isinstance(self.deleted_at, datetime.datetime):
            deleted_at = self.deleted_at.isoformat()
        else:
            deleted_at = self.deleted_at

        extra_data: dict[str, Any] | None | Unset
        if isinstance(self.extra_data, Unset):
            extra_data = UNSET
        elif isinstance(self.extra_data, ArtifactDataExtraDataType0):
            extra_data = self.extra_data.to_dict()
        else:
            extra_data = self.extra_data

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "total_size": total_size,
                "is_dir": is_dir,
                "hash": hash_,
            }
        )
        if uri is not UNSET:
            field_dict["uri"] = uri
        if value is not UNSET:
            field_dict["value"] = value
        if created_at is not UNSET:
            field_dict["created_at"] = created_at
        if deleted_at is not UNSET:
            field_dict["deleted_at"] = deleted_at
        if extra_data is not UNSET:
            field_dict["extra_data"] = extra_data

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.artifact_data_extra_data_type_0 import ArtifactDataExtraDataType0

        d = dict(src_dict)
        total_size = d.pop("total_size")

        is_dir = d.pop("is_dir")

        hash_ = d.pop("hash")

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

        def _parse_created_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                created_at_type_0 = datetime.datetime.fromisoformat(data)

                return created_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        created_at = _parse_created_at(d.pop("created_at", UNSET))

        def _parse_deleted_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                deleted_at_type_0 = datetime.datetime.fromisoformat(data)

                return deleted_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        deleted_at = _parse_deleted_at(d.pop("deleted_at", UNSET))

        def _parse_extra_data(data: object) -> ArtifactDataExtraDataType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                extra_data_type_0 = ArtifactDataExtraDataType0.from_dict(data)

                return extra_data_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ArtifactDataExtraDataType0 | None | Unset, data)

        extra_data = _parse_extra_data(d.pop("extra_data", UNSET))

        artifact_data = cls(
            total_size=total_size,
            is_dir=is_dir,
            hash_=hash_,
            uri=uri,
            value=value,
            created_at=created_at,
            deleted_at=deleted_at,
            extra_data=extra_data,
        )

        artifact_data.additional_properties = d
        return artifact_data

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
