from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.artifact_data_response import ArtifactDataResponse
    from ..models.artifact_node_response_type_properties_type_0 import ArtifactNodeResponseTypePropertiesType0


T = TypeVar("T", bound="ArtifactNodeResponse")


@_attrs_define
class ArtifactNodeResponse:
    """
    Attributes:
        id (str):
        type_name (None | str | Unset):
        type_properties (ArtifactNodeResponseTypePropertiesType0 | None | Unset):
        producer_execution_id (None | str | Unset):
        producer_output_name (None | str | Unset):
        artifact_data (ArtifactDataResponse | None | Unset):
    """

    id: str
    type_name: None | str | Unset = UNSET
    type_properties: ArtifactNodeResponseTypePropertiesType0 | None | Unset = UNSET
    producer_execution_id: None | str | Unset = UNSET
    producer_output_name: None | str | Unset = UNSET
    artifact_data: ArtifactDataResponse | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.artifact_data_response import ArtifactDataResponse
        from ..models.artifact_node_response_type_properties_type_0 import ArtifactNodeResponseTypePropertiesType0

        id = self.id

        type_name: None | str | Unset
        if isinstance(self.type_name, Unset):
            type_name = UNSET
        else:
            type_name = self.type_name

        type_properties: dict[str, Any] | None | Unset
        if isinstance(self.type_properties, Unset):
            type_properties = UNSET
        elif isinstance(self.type_properties, ArtifactNodeResponseTypePropertiesType0):
            type_properties = self.type_properties.to_dict()
        else:
            type_properties = self.type_properties

        producer_execution_id: None | str | Unset
        if isinstance(self.producer_execution_id, Unset):
            producer_execution_id = UNSET
        else:
            producer_execution_id = self.producer_execution_id

        producer_output_name: None | str | Unset
        if isinstance(self.producer_output_name, Unset):
            producer_output_name = UNSET
        else:
            producer_output_name = self.producer_output_name

        artifact_data: dict[str, Any] | None | Unset
        if isinstance(self.artifact_data, Unset):
            artifact_data = UNSET
        elif isinstance(self.artifact_data, ArtifactDataResponse):
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
        if type_name is not UNSET:
            field_dict["type_name"] = type_name
        if type_properties is not UNSET:
            field_dict["type_properties"] = type_properties
        if producer_execution_id is not UNSET:
            field_dict["producer_execution_id"] = producer_execution_id
        if producer_output_name is not UNSET:
            field_dict["producer_output_name"] = producer_output_name
        if artifact_data is not UNSET:
            field_dict["artifact_data"] = artifact_data

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.artifact_data_response import ArtifactDataResponse
        from ..models.artifact_node_response_type_properties_type_0 import ArtifactNodeResponseTypePropertiesType0

        d = dict(src_dict)
        id = d.pop("id")

        def _parse_type_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        type_name = _parse_type_name(d.pop("type_name", UNSET))

        def _parse_type_properties(data: object) -> ArtifactNodeResponseTypePropertiesType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                type_properties_type_0 = ArtifactNodeResponseTypePropertiesType0.from_dict(data)

                return type_properties_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ArtifactNodeResponseTypePropertiesType0 | None | Unset, data)

        type_properties = _parse_type_properties(d.pop("type_properties", UNSET))

        def _parse_producer_execution_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        producer_execution_id = _parse_producer_execution_id(d.pop("producer_execution_id", UNSET))

        def _parse_producer_output_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        producer_output_name = _parse_producer_output_name(d.pop("producer_output_name", UNSET))

        def _parse_artifact_data(data: object) -> ArtifactDataResponse | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                artifact_data_type_0 = ArtifactDataResponse.from_dict(data)

                return artifact_data_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ArtifactDataResponse | None | Unset, data)

        artifact_data = _parse_artifact_data(d.pop("artifact_data", UNSET))

        artifact_node_response = cls(
            id=id,
            type_name=type_name,
            type_properties=type_properties,
            producer_execution_id=producer_execution_id,
            producer_output_name=producer_output_name,
            artifact_data=artifact_data,
        )

        artifact_node_response.additional_properties = d
        return artifact_node_response

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
