from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="PublishedComponentResponse")


@_attrs_define
class PublishedComponentResponse:
    """
    Attributes:
        digest (str):
        published_by (str):
        deprecated (bool | Unset):  Default: False.
        superseded_by (None | str | Unset):
        url (None | str | Unset):
        name (None | str | Unset):
    """

    digest: str
    published_by: str
    deprecated: bool | Unset = False
    superseded_by: None | str | Unset = UNSET
    url: None | str | Unset = UNSET
    name: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        digest = self.digest

        published_by = self.published_by

        deprecated = self.deprecated

        superseded_by: None | str | Unset
        if isinstance(self.superseded_by, Unset):
            superseded_by = UNSET
        else:
            superseded_by = self.superseded_by

        url: None | str | Unset
        if isinstance(self.url, Unset):
            url = UNSET
        else:
            url = self.url

        name: None | str | Unset
        if isinstance(self.name, Unset):
            name = UNSET
        else:
            name = self.name

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "digest": digest,
                "published_by": published_by,
            }
        )
        if deprecated is not UNSET:
            field_dict["deprecated"] = deprecated
        if superseded_by is not UNSET:
            field_dict["superseded_by"] = superseded_by
        if url is not UNSET:
            field_dict["url"] = url
        if name is not UNSET:
            field_dict["name"] = name

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        digest = d.pop("digest")

        published_by = d.pop("published_by")

        deprecated = d.pop("deprecated", UNSET)

        def _parse_superseded_by(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        superseded_by = _parse_superseded_by(d.pop("superseded_by", UNSET))

        def _parse_url(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        url = _parse_url(d.pop("url", UNSET))

        def _parse_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        name = _parse_name(d.pop("name", UNSET))

        published_component_response = cls(
            digest=digest,
            published_by=published_by,
            deprecated=deprecated,
            superseded_by=superseded_by,
            url=url,
            name=name,
        )

        published_component_response.additional_properties = d
        return published_component_response

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
