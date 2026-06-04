from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.component_spec import ComponentSpec


T = TypeVar("T", bound="ComponentReference")


@_attrs_define
class ComponentReference:
    """
    Attributes:
        name (None | str | Unset):
        digest (None | str | Unset):
        tag (None | str | Unset):
        url (None | str | Unset):
        spec (ComponentSpec | None | Unset):
        text (None | str | Unset):
    """

    name: None | str | Unset = UNSET
    digest: None | str | Unset = UNSET
    tag: None | str | Unset = UNSET
    url: None | str | Unset = UNSET
    spec: ComponentSpec | None | Unset = UNSET
    text: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.component_spec import ComponentSpec

        name: None | str | Unset
        if isinstance(self.name, Unset):
            name = UNSET
        else:
            name = self.name

        digest: None | str | Unset
        if isinstance(self.digest, Unset):
            digest = UNSET
        else:
            digest = self.digest

        tag: None | str | Unset
        if isinstance(self.tag, Unset):
            tag = UNSET
        else:
            tag = self.tag

        url: None | str | Unset
        if isinstance(self.url, Unset):
            url = UNSET
        else:
            url = self.url

        spec: dict[str, Any] | None | Unset
        if isinstance(self.spec, Unset):
            spec = UNSET
        elif isinstance(self.spec, ComponentSpec):
            spec = self.spec.to_dict()
        else:
            spec = self.spec

        text: None | str | Unset
        if isinstance(self.text, Unset):
            text = UNSET
        else:
            text = self.text

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if name is not UNSET:
            field_dict["name"] = name
        if digest is not UNSET:
            field_dict["digest"] = digest
        if tag is not UNSET:
            field_dict["tag"] = tag
        if url is not UNSET:
            field_dict["url"] = url
        if spec is not UNSET:
            field_dict["spec"] = spec
        if text is not UNSET:
            field_dict["text"] = text

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.component_spec import ComponentSpec

        d = dict(src_dict)

        def _parse_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        name = _parse_name(d.pop("name", UNSET))

        def _parse_digest(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        digest = _parse_digest(d.pop("digest", UNSET))

        def _parse_tag(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        tag = _parse_tag(d.pop("tag", UNSET))

        def _parse_url(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        url = _parse_url(d.pop("url", UNSET))

        def _parse_spec(data: object) -> ComponentSpec | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                spec_type_0 = ComponentSpec.from_dict(data)

                return spec_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ComponentSpec | None | Unset, data)

        spec = _parse_spec(d.pop("spec", UNSET))

        def _parse_text(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        text = _parse_text(d.pop("text", UNSET))

        component_reference = cls(
            name=name,
            digest=digest,
            tag=tag,
            url=url,
            spec=spec,
            text=text,
        )

        component_reference.additional_properties = d
        return component_reference

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
