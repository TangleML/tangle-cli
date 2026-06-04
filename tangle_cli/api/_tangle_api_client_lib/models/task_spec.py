from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.component_reference import ComponentReference
    from ..models.dynamic_data_argument import DynamicDataArgument
    from ..models.execution_options_spec import ExecutionOptionsSpec
    from ..models.graph_input_argument import GraphInputArgument
    from ..models.task_output_argument import TaskOutputArgument
    from ..models.task_spec_annotations_type_0 import TaskSpecAnnotationsType0
    from ..models.task_spec_arguments_type_0 import TaskSpecArgumentsType0


T = TypeVar("T", bound="TaskSpec")


@_attrs_define
class TaskSpec:
    """
    Attributes:
        component_ref (ComponentReference):
        arguments (None | TaskSpecArgumentsType0 | Unset):
        is_enabled (DynamicDataArgument | GraphInputArgument | None | str | TaskOutputArgument | Unset):
        execution_options (ExecutionOptionsSpec | None | Unset):
        annotations (None | TaskSpecAnnotationsType0 | Unset):
    """

    component_ref: ComponentReference
    arguments: None | TaskSpecArgumentsType0 | Unset = UNSET
    is_enabled: DynamicDataArgument | GraphInputArgument | None | str | TaskOutputArgument | Unset = UNSET
    execution_options: ExecutionOptionsSpec | None | Unset = UNSET
    annotations: None | TaskSpecAnnotationsType0 | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.dynamic_data_argument import DynamicDataArgument
        from ..models.execution_options_spec import ExecutionOptionsSpec
        from ..models.graph_input_argument import GraphInputArgument
        from ..models.task_output_argument import TaskOutputArgument
        from ..models.task_spec_annotations_type_0 import TaskSpecAnnotationsType0
        from ..models.task_spec_arguments_type_0 import TaskSpecArgumentsType0

        component_ref = self.component_ref.to_dict()

        arguments: dict[str, Any] | None | Unset
        if isinstance(self.arguments, Unset):
            arguments = UNSET
        elif isinstance(self.arguments, TaskSpecArgumentsType0):
            arguments = self.arguments.to_dict()
        else:
            arguments = self.arguments

        is_enabled: dict[str, Any] | None | str | Unset
        if isinstance(self.is_enabled, Unset):
            is_enabled = UNSET
        elif isinstance(self.is_enabled, GraphInputArgument):
            is_enabled = self.is_enabled.to_dict()
        elif isinstance(self.is_enabled, TaskOutputArgument):
            is_enabled = self.is_enabled.to_dict()
        elif isinstance(self.is_enabled, DynamicDataArgument):
            is_enabled = self.is_enabled.to_dict()
        else:
            is_enabled = self.is_enabled

        execution_options: dict[str, Any] | None | Unset
        if isinstance(self.execution_options, Unset):
            execution_options = UNSET
        elif isinstance(self.execution_options, ExecutionOptionsSpec):
            execution_options = self.execution_options.to_dict()
        else:
            execution_options = self.execution_options

        annotations: dict[str, Any] | None | Unset
        if isinstance(self.annotations, Unset):
            annotations = UNSET
        elif isinstance(self.annotations, TaskSpecAnnotationsType0):
            annotations = self.annotations.to_dict()
        else:
            annotations = self.annotations

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "componentRef": component_ref,
            }
        )
        if arguments is not UNSET:
            field_dict["arguments"] = arguments
        if is_enabled is not UNSET:
            field_dict["isEnabled"] = is_enabled
        if execution_options is not UNSET:
            field_dict["executionOptions"] = execution_options
        if annotations is not UNSET:
            field_dict["annotations"] = annotations

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.component_reference import ComponentReference
        from ..models.dynamic_data_argument import DynamicDataArgument
        from ..models.execution_options_spec import ExecutionOptionsSpec
        from ..models.graph_input_argument import GraphInputArgument
        from ..models.task_output_argument import TaskOutputArgument
        from ..models.task_spec_annotations_type_0 import TaskSpecAnnotationsType0
        from ..models.task_spec_arguments_type_0 import TaskSpecArgumentsType0

        d = dict(src_dict)
        component_ref = ComponentReference.from_dict(d.pop("componentRef"))

        def _parse_arguments(data: object) -> None | TaskSpecArgumentsType0 | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                arguments_type_0 = TaskSpecArgumentsType0.from_dict(data)

                return arguments_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | TaskSpecArgumentsType0 | Unset, data)

        arguments = _parse_arguments(d.pop("arguments", UNSET))

        def _parse_is_enabled(
            data: object,
        ) -> DynamicDataArgument | GraphInputArgument | None | str | TaskOutputArgument | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                is_enabled_type_1 = GraphInputArgument.from_dict(data)

                return is_enabled_type_1
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                is_enabled_type_2 = TaskOutputArgument.from_dict(data)

                return is_enabled_type_2
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                is_enabled_type_3 = DynamicDataArgument.from_dict(data)

                return is_enabled_type_3
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(DynamicDataArgument | GraphInputArgument | None | str | TaskOutputArgument | Unset, data)

        is_enabled = _parse_is_enabled(d.pop("isEnabled", UNSET))

        def _parse_execution_options(data: object) -> ExecutionOptionsSpec | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                execution_options_type_0 = ExecutionOptionsSpec.from_dict(data)

                return execution_options_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ExecutionOptionsSpec | None | Unset, data)

        execution_options = _parse_execution_options(d.pop("executionOptions", UNSET))

        def _parse_annotations(data: object) -> None | TaskSpecAnnotationsType0 | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                annotations_type_0 = TaskSpecAnnotationsType0.from_dict(data)

                return annotations_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | TaskSpecAnnotationsType0 | Unset, data)

        annotations = _parse_annotations(d.pop("annotations", UNSET))

        task_spec = cls(
            component_ref=component_ref,
            arguments=arguments,
            is_enabled=is_enabled,
            execution_options=execution_options,
            annotations=annotations,
        )

        task_spec.additional_properties = d
        return task_spec

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
