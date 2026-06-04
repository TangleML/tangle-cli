from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.pipeline_run_response import PipelineRunResponse


T = TypeVar("T", bound="ListPipelineJobsResponse")


@_attrs_define
class ListPipelineJobsResponse:
    """
    Attributes:
        pipeline_runs (list[PipelineRunResponse]):
        next_page_token (None | str | Unset):
    """

    pipeline_runs: list[PipelineRunResponse]
    next_page_token: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        pipeline_runs = []
        for pipeline_runs_item_data in self.pipeline_runs:
            pipeline_runs_item = pipeline_runs_item_data.to_dict()
            pipeline_runs.append(pipeline_runs_item)

        next_page_token: None | str | Unset
        if isinstance(self.next_page_token, Unset):
            next_page_token = UNSET
        else:
            next_page_token = self.next_page_token

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "pipeline_runs": pipeline_runs,
            }
        )
        if next_page_token is not UNSET:
            field_dict["next_page_token"] = next_page_token

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.pipeline_run_response import PipelineRunResponse

        d = dict(src_dict)
        pipeline_runs = []
        _pipeline_runs = d.pop("pipeline_runs")
        for pipeline_runs_item_data in _pipeline_runs:
            pipeline_runs_item = PipelineRunResponse.from_dict(pipeline_runs_item_data)

            pipeline_runs.append(pipeline_runs_item)

        def _parse_next_page_token(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        next_page_token = _parse_next_page_token(d.pop("next_page_token", UNSET))

        list_pipeline_jobs_response = cls(
            pipeline_runs=pipeline_runs,
            next_page_token=next_page_token,
        )

        list_pipeline_jobs_response.additional_properties = d
        return list_pipeline_jobs_response

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
