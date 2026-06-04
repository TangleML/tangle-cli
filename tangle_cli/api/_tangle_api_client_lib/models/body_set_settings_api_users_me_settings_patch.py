from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.body_set_settings_api_users_me_settings_patch_settings import (
        BodySetSettingsApiUsersMeSettingsPatchSettings,
    )


T = TypeVar("T", bound="BodySetSettingsApiUsersMeSettingsPatch")


@_attrs_define
class BodySetSettingsApiUsersMeSettingsPatch:
    """
    Attributes:
        settings (BodySetSettingsApiUsersMeSettingsPatchSettings):
    """

    settings: BodySetSettingsApiUsersMeSettingsPatchSettings
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        settings = self.settings.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "settings": settings,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.body_set_settings_api_users_me_settings_patch_settings import (
            BodySetSettingsApiUsersMeSettingsPatchSettings,
        )

        d = dict(src_dict)
        settings = BodySetSettingsApiUsersMeSettingsPatchSettings.from_dict(d.pop("settings"))

        body_set_settings_api_users_me_settings_patch = cls(
            settings=settings,
        )

        body_set_settings_api_users_me_settings_patch.additional_properties = d
        return body_set_settings_api_users_me_settings_patch

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
