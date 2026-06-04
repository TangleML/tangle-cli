from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.artifact_data import ArtifactData


T = TypeVar("T", bound="GetArtifactInfoResponse")


@_attrs_define
class GetArtifactInfoResponse:
    """
    Attributes:
        id (str):
        artifact_data (ArtifactData | None | Unset):
    """

    id: str
    artifact_data: ArtifactData | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.artifact_data import ArtifactData

        id = self.id

        artifact_data: dict[str, Any] | None | Unset
        if isinstance(self.artifact_data, Unset):
            artifact_data = UNSET
        elif isinstance(self.artifact_data, ArtifactData):
            artifact_data = self.artifact_data.to_dict()
        else:
            artifact_data = self.artifact_data

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
            }
        )
        if artifact_data is not UNSET:
            field_dict["artifact_data"] = artifact_data

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.artifact_data import ArtifactData

        d = dict(src_dict)
        id = d.pop("id")

        def _parse_artifact_data(data: object) -> ArtifactData | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                artifact_data_type_0 = ArtifactData.from_dict(data)

                return artifact_data_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ArtifactData | None | Unset, data)

        artifact_data = _parse_artifact_data(d.pop("artifact_data", UNSET))

        get_artifact_info_response = cls(
            id=id,
            artifact_data=artifact_data,
        )

        get_artifact_info_response.additional_properties = d
        return get_artifact_info_response

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
