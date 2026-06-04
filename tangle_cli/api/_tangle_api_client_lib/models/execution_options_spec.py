from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.caching_strategy_spec import CachingStrategySpec
    from ..models.retry_strategy_spec import RetryStrategySpec


T = TypeVar("T", bound="ExecutionOptionsSpec")


@_attrs_define
class ExecutionOptionsSpec:
    """
    Attributes:
        retry_strategy (None | RetryStrategySpec | Unset):
        caching_strategy (CachingStrategySpec | None | Unset):
    """

    retry_strategy: None | RetryStrategySpec | Unset = UNSET
    caching_strategy: CachingStrategySpec | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.caching_strategy_spec import CachingStrategySpec
        from ..models.retry_strategy_spec import RetryStrategySpec

        retry_strategy: dict[str, Any] | None | Unset
        if isinstance(self.retry_strategy, Unset):
            retry_strategy = UNSET
        elif isinstance(self.retry_strategy, RetryStrategySpec):
            retry_strategy = self.retry_strategy.to_dict()
        else:
            retry_strategy = self.retry_strategy

        caching_strategy: dict[str, Any] | None | Unset
        if isinstance(self.caching_strategy, Unset):
            caching_strategy = UNSET
        elif isinstance(self.caching_strategy, CachingStrategySpec):
            caching_strategy = self.caching_strategy.to_dict()
        else:
            caching_strategy = self.caching_strategy

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if retry_strategy is not UNSET:
            field_dict["retryStrategy"] = retry_strategy
        if caching_strategy is not UNSET:
            field_dict["cachingStrategy"] = caching_strategy

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.caching_strategy_spec import CachingStrategySpec
        from ..models.retry_strategy_spec import RetryStrategySpec

        d = dict(src_dict)

        def _parse_retry_strategy(data: object) -> None | RetryStrategySpec | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                retry_strategy_type_0 = RetryStrategySpec.from_dict(data)

                return retry_strategy_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | RetryStrategySpec | Unset, data)

        retry_strategy = _parse_retry_strategy(d.pop("retryStrategy", UNSET))

        def _parse_caching_strategy(data: object) -> CachingStrategySpec | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                caching_strategy_type_0 = CachingStrategySpec.from_dict(data)

                return caching_strategy_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(CachingStrategySpec | None | Unset, data)

        caching_strategy = _parse_caching_strategy(d.pop("cachingStrategy", UNSET))

        execution_options_spec = cls(
            retry_strategy=retry_strategy,
            caching_strategy=caching_strategy,
        )

        execution_options_spec.additional_properties = d
        return execution_options_spec

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
