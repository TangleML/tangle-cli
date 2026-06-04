from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="CachingStrategySpec")


@_attrs_define
class CachingStrategySpec:
    """
    Attributes:
        max_cache_staleness (None | str | Unset):
    """

    max_cache_staleness: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        max_cache_staleness: None | str | Unset
        if isinstance(self.max_cache_staleness, Unset):
            max_cache_staleness = UNSET
        else:
            max_cache_staleness = self.max_cache_staleness

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if max_cache_staleness is not UNSET:
            field_dict["maxCacheStaleness"] = max_cache_staleness

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_max_cache_staleness(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        max_cache_staleness = _parse_max_cache_staleness(d.pop("maxCacheStaleness", UNSET))

        caching_strategy_spec = cls(
            max_cache_staleness=max_cache_staleness,
        )

        caching_strategy_spec.additional_properties = d
        return caching_strategy_spec

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
