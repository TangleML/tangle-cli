from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.execution_status_summary import ExecutionStatusSummary
    from ..models.pipeline_run_response_annotations_type_0 import PipelineRunResponseAnnotationsType0
    from ..models.pipeline_run_response_execution_status_stats_type_0 import (
        PipelineRunResponseExecutionStatusStatsType0,
    )


T = TypeVar("T", bound="PipelineRunResponse")


@_attrs_define
class PipelineRunResponse:
    """
    Attributes:
        id (str):
        root_execution_id (str):
        annotations (None | PipelineRunResponseAnnotationsType0 | Unset):
        created_by (None | str | Unset):
        created_at (datetime.datetime | None | Unset):
        pipeline_name (None | str | Unset):
        execution_status_stats (None | PipelineRunResponseExecutionStatusStatsType0 | Unset):
        execution_summary (ExecutionStatusSummary | None | Unset):
    """

    id: str
    root_execution_id: str
    annotations: None | PipelineRunResponseAnnotationsType0 | Unset = UNSET
    created_by: None | str | Unset = UNSET
    created_at: datetime.datetime | None | Unset = UNSET
    pipeline_name: None | str | Unset = UNSET
    execution_status_stats: None | PipelineRunResponseExecutionStatusStatsType0 | Unset = UNSET
    execution_summary: ExecutionStatusSummary | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.execution_status_summary import ExecutionStatusSummary
        from ..models.pipeline_run_response_annotations_type_0 import PipelineRunResponseAnnotationsType0
        from ..models.pipeline_run_response_execution_status_stats_type_0 import (
            PipelineRunResponseExecutionStatusStatsType0,
        )

        id = self.id

        root_execution_id = self.root_execution_id

        annotations: dict[str, Any] | None | Unset
        if isinstance(self.annotations, Unset):
            annotations = UNSET
        elif isinstance(self.annotations, PipelineRunResponseAnnotationsType0):
            annotations = self.annotations.to_dict()
        else:
            annotations = self.annotations

        created_by: None | str | Unset
        if isinstance(self.created_by, Unset):
            created_by = UNSET
        else:
            created_by = self.created_by

        created_at: None | str | Unset
        if isinstance(self.created_at, Unset):
            created_at = UNSET
        elif isinstance(self.created_at, datetime.datetime):
            created_at = self.created_at.isoformat()
        else:
            created_at = self.created_at

        pipeline_name: None | str | Unset
        if isinstance(self.pipeline_name, Unset):
            pipeline_name = UNSET
        else:
            pipeline_name = self.pipeline_name

        execution_status_stats: dict[str, Any] | None | Unset
        if isinstance(self.execution_status_stats, Unset):
            execution_status_stats = UNSET
        elif isinstance(self.execution_status_stats, PipelineRunResponseExecutionStatusStatsType0):
            execution_status_stats = self.execution_status_stats.to_dict()
        else:
            execution_status_stats = self.execution_status_stats

        execution_summary: dict[str, Any] | None | Unset
        if isinstance(self.execution_summary, Unset):
            execution_summary = UNSET
        elif isinstance(self.execution_summary, ExecutionStatusSummary):
            execution_summary = self.execution_summary.to_dict()
        else:
            execution_summary = self.execution_summary

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "root_execution_id": root_execution_id,
            }
        )
        if annotations is not UNSET:
            field_dict["annotations"] = annotations
        if created_by is not UNSET:
            field_dict["created_by"] = created_by
        if created_at is not UNSET:
            field_dict["created_at"] = created_at
        if pipeline_name is not UNSET:
            field_dict["pipeline_name"] = pipeline_name
        if execution_status_stats is not UNSET:
            field_dict["execution_status_stats"] = execution_status_stats
        if execution_summary is not UNSET:
            field_dict["execution_summary"] = execution_summary

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.execution_status_summary import ExecutionStatusSummary
        from ..models.pipeline_run_response_annotations_type_0 import PipelineRunResponseAnnotationsType0
        from ..models.pipeline_run_response_execution_status_stats_type_0 import (
            PipelineRunResponseExecutionStatusStatsType0,
        )

        d = dict(src_dict)
        id = d.pop("id")

        root_execution_id = d.pop("root_execution_id")

        def _parse_annotations(data: object) -> None | PipelineRunResponseAnnotationsType0 | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                annotations_type_0 = PipelineRunResponseAnnotationsType0.from_dict(data)

                return annotations_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | PipelineRunResponseAnnotationsType0 | Unset, data)

        annotations = _parse_annotations(d.pop("annotations", UNSET))

        def _parse_created_by(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        created_by = _parse_created_by(d.pop("created_by", UNSET))

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

        def _parse_pipeline_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        pipeline_name = _parse_pipeline_name(d.pop("pipeline_name", UNSET))

        def _parse_execution_status_stats(data: object) -> None | PipelineRunResponseExecutionStatusStatsType0 | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                execution_status_stats_type_0 = PipelineRunResponseExecutionStatusStatsType0.from_dict(data)

                return execution_status_stats_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | PipelineRunResponseExecutionStatusStatsType0 | Unset, data)

        execution_status_stats = _parse_execution_status_stats(d.pop("execution_status_stats", UNSET))

        def _parse_execution_summary(data: object) -> ExecutionStatusSummary | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                execution_summary_type_0 = ExecutionStatusSummary.from_dict(data)

                return execution_summary_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ExecutionStatusSummary | None | Unset, data)

        execution_summary = _parse_execution_summary(d.pop("execution_summary", UNSET))

        pipeline_run_response = cls(
            id=id,
            root_execution_id=root_execution_id,
            annotations=annotations,
            created_by=created_by,
            created_at=created_at,
            pipeline_name=pipeline_name,
            execution_status_stats=execution_status_stats,
            execution_summary=execution_summary,
        )

        pipeline_run_response.additional_properties = d
        return pipeline_run_response

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
