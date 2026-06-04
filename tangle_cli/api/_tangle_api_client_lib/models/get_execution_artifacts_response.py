from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.get_execution_artifacts_response_input_artifacts_type_0 import (
        GetExecutionArtifactsResponseInputArtifactsType0,
    )
    from ..models.get_execution_artifacts_response_output_artifacts_type_0 import (
        GetExecutionArtifactsResponseOutputArtifactsType0,
    )


T = TypeVar("T", bound="GetExecutionArtifactsResponse")


@_attrs_define
class GetExecutionArtifactsResponse:
    """
    Attributes:
        input_artifacts (GetExecutionArtifactsResponseInputArtifactsType0 | None | Unset):
        output_artifacts (GetExecutionArtifactsResponseOutputArtifactsType0 | None | Unset):
    """

    input_artifacts: GetExecutionArtifactsResponseInputArtifactsType0 | None | Unset = UNSET
    output_artifacts: GetExecutionArtifactsResponseOutputArtifactsType0 | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.get_execution_artifacts_response_input_artifacts_type_0 import (
            GetExecutionArtifactsResponseInputArtifactsType0,
        )
        from ..models.get_execution_artifacts_response_output_artifacts_type_0 import (
            GetExecutionArtifactsResponseOutputArtifactsType0,
        )

        input_artifacts: dict[str, Any] | None | Unset
        if isinstance(self.input_artifacts, Unset):
            input_artifacts = UNSET
        elif isinstance(self.input_artifacts, GetExecutionArtifactsResponseInputArtifactsType0):
            input_artifacts = self.input_artifacts.to_dict()
        else:
            input_artifacts = self.input_artifacts

        output_artifacts: dict[str, Any] | None | Unset
        if isinstance(self.output_artifacts, Unset):
            output_artifacts = UNSET
        elif isinstance(self.output_artifacts, GetExecutionArtifactsResponseOutputArtifactsType0):
            output_artifacts = self.output_artifacts.to_dict()
        else:
            output_artifacts = self.output_artifacts

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if input_artifacts is not UNSET:
            field_dict["input_artifacts"] = input_artifacts
        if output_artifacts is not UNSET:
            field_dict["output_artifacts"] = output_artifacts

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.get_execution_artifacts_response_input_artifacts_type_0 import (
            GetExecutionArtifactsResponseInputArtifactsType0,
        )
        from ..models.get_execution_artifacts_response_output_artifacts_type_0 import (
            GetExecutionArtifactsResponseOutputArtifactsType0,
        )

        d = dict(src_dict)

        def _parse_input_artifacts(data: object) -> GetExecutionArtifactsResponseInputArtifactsType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                input_artifacts_type_0 = GetExecutionArtifactsResponseInputArtifactsType0.from_dict(data)

                return input_artifacts_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(GetExecutionArtifactsResponseInputArtifactsType0 | None | Unset, data)

        input_artifacts = _parse_input_artifacts(d.pop("input_artifacts", UNSET))

        def _parse_output_artifacts(data: object) -> GetExecutionArtifactsResponseOutputArtifactsType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                output_artifacts_type_0 = GetExecutionArtifactsResponseOutputArtifactsType0.from_dict(data)

                return output_artifacts_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(GetExecutionArtifactsResponseOutputArtifactsType0 | None | Unset, data)

        output_artifacts = _parse_output_artifacts(d.pop("output_artifacts", UNSET))

        get_execution_artifacts_response = cls(
            input_artifacts=input_artifacts,
            output_artifacts=output_artifacts,
        )

        get_execution_artifacts_response.additional_properties = d
        return get_execution_artifacts_response

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
