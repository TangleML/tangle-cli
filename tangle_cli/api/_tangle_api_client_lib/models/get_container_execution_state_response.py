from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.container_execution_status import ContainerExecutionStatus
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.execution_node_reference import ExecutionNodeReference
    from ..models.get_container_execution_state_response_debug_info_type_0 import (
        GetContainerExecutionStateResponseDebugInfoType0,
    )


T = TypeVar("T", bound="GetContainerExecutionStateResponse")


@_attrs_define
class GetContainerExecutionStateResponse:
    """
    Attributes:
        status (ContainerExecutionStatus):
        exit_code (int | None | Unset):
        started_at (datetime.datetime | None | Unset):
        ended_at (datetime.datetime | None | Unset):
        debug_info (GetContainerExecutionStateResponseDebugInfoType0 | None | Unset):
        execution_nodes_linked_to_same_container_execution (list[ExecutionNodeReference] | None | Unset):
    """

    status: ContainerExecutionStatus
    exit_code: int | None | Unset = UNSET
    started_at: datetime.datetime | None | Unset = UNSET
    ended_at: datetime.datetime | None | Unset = UNSET
    debug_info: GetContainerExecutionStateResponseDebugInfoType0 | None | Unset = UNSET
    execution_nodes_linked_to_same_container_execution: list[ExecutionNodeReference] | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.get_container_execution_state_response_debug_info_type_0 import (
            GetContainerExecutionStateResponseDebugInfoType0,
        )

        status = self.status.value

        exit_code: int | None | Unset
        if isinstance(self.exit_code, Unset):
            exit_code = UNSET
        else:
            exit_code = self.exit_code

        started_at: None | str | Unset
        if isinstance(self.started_at, Unset):
            started_at = UNSET
        elif isinstance(self.started_at, datetime.datetime):
            started_at = self.started_at.isoformat()
        else:
            started_at = self.started_at

        ended_at: None | str | Unset
        if isinstance(self.ended_at, Unset):
            ended_at = UNSET
        elif isinstance(self.ended_at, datetime.datetime):
            ended_at = self.ended_at.isoformat()
        else:
            ended_at = self.ended_at

        debug_info: dict[str, Any] | None | Unset
        if isinstance(self.debug_info, Unset):
            debug_info = UNSET
        elif isinstance(self.debug_info, GetContainerExecutionStateResponseDebugInfoType0):
            debug_info = self.debug_info.to_dict()
        else:
            debug_info = self.debug_info

        execution_nodes_linked_to_same_container_execution: list[dict[str, Any]] | None | Unset
        if isinstance(self.execution_nodes_linked_to_same_container_execution, Unset):
            execution_nodes_linked_to_same_container_execution = UNSET
        elif isinstance(self.execution_nodes_linked_to_same_container_execution, list):
            execution_nodes_linked_to_same_container_execution = []
            for (
                execution_nodes_linked_to_same_container_execution_type_0_item_data
            ) in self.execution_nodes_linked_to_same_container_execution:
                execution_nodes_linked_to_same_container_execution_type_0_item = (
                    execution_nodes_linked_to_same_container_execution_type_0_item_data.to_dict()
                )
                execution_nodes_linked_to_same_container_execution.append(
                    execution_nodes_linked_to_same_container_execution_type_0_item
                )

        else:
            execution_nodes_linked_to_same_container_execution = self.execution_nodes_linked_to_same_container_execution

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "status": status,
            }
        )
        if exit_code is not UNSET:
            field_dict["exit_code"] = exit_code
        if started_at is not UNSET:
            field_dict["started_at"] = started_at
        if ended_at is not UNSET:
            field_dict["ended_at"] = ended_at
        if debug_info is not UNSET:
            field_dict["debug_info"] = debug_info
        if execution_nodes_linked_to_same_container_execution is not UNSET:
            field_dict["execution_nodes_linked_to_same_container_execution"] = (
                execution_nodes_linked_to_same_container_execution
            )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.execution_node_reference import ExecutionNodeReference
        from ..models.get_container_execution_state_response_debug_info_type_0 import (
            GetContainerExecutionStateResponseDebugInfoType0,
        )

        d = dict(src_dict)
        status = ContainerExecutionStatus(d.pop("status"))

        def _parse_exit_code(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        exit_code = _parse_exit_code(d.pop("exit_code", UNSET))

        def _parse_started_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                started_at_type_0 = datetime.datetime.fromisoformat(data)

                return started_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        started_at = _parse_started_at(d.pop("started_at", UNSET))

        def _parse_ended_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                ended_at_type_0 = datetime.datetime.fromisoformat(data)

                return ended_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        ended_at = _parse_ended_at(d.pop("ended_at", UNSET))

        def _parse_debug_info(data: object) -> GetContainerExecutionStateResponseDebugInfoType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                debug_info_type_0 = GetContainerExecutionStateResponseDebugInfoType0.from_dict(data)

                return debug_info_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(GetContainerExecutionStateResponseDebugInfoType0 | None | Unset, data)

        debug_info = _parse_debug_info(d.pop("debug_info", UNSET))

        def _parse_execution_nodes_linked_to_same_container_execution(
            data: object,
        ) -> list[ExecutionNodeReference] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                execution_nodes_linked_to_same_container_execution_type_0 = []
                _execution_nodes_linked_to_same_container_execution_type_0 = data
                for (
                    execution_nodes_linked_to_same_container_execution_type_0_item_data
                ) in _execution_nodes_linked_to_same_container_execution_type_0:
                    execution_nodes_linked_to_same_container_execution_type_0_item = ExecutionNodeReference.from_dict(
                        execution_nodes_linked_to_same_container_execution_type_0_item_data
                    )

                    execution_nodes_linked_to_same_container_execution_type_0.append(
                        execution_nodes_linked_to_same_container_execution_type_0_item
                    )

                return execution_nodes_linked_to_same_container_execution_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[ExecutionNodeReference] | None | Unset, data)

        execution_nodes_linked_to_same_container_execution = _parse_execution_nodes_linked_to_same_container_execution(
            d.pop("execution_nodes_linked_to_same_container_execution", UNSET)
        )

        get_container_execution_state_response = cls(
            status=status,
            exit_code=exit_code,
            started_at=started_at,
            ended_at=ended_at,
            debug_info=debug_info,
            execution_nodes_linked_to_same_container_execution=execution_nodes_linked_to_same_container_execution,
        )

        get_container_execution_state_response.additional_properties = d
        return get_container_execution_state_response

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
