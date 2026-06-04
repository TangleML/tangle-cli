from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.container_implementation import ContainerImplementation
    from ..models.graph_implementation import GraphImplementation
    from ..models.input_spec import InputSpec
    from ..models.metadata_spec import MetadataSpec
    from ..models.output_spec import OutputSpec


T = TypeVar("T", bound="ComponentSpec")


@_attrs_define
class ComponentSpec:
    """
    Attributes:
        name (None | str | Unset):
        description (None | str | Unset):
        metadata (MetadataSpec | None | Unset):
        inputs (list[InputSpec] | None | Unset):
        outputs (list[OutputSpec] | None | Unset):
        implementation (ContainerImplementation | GraphImplementation | None | Unset):
    """

    name: None | str | Unset = UNSET
    description: None | str | Unset = UNSET
    metadata: MetadataSpec | None | Unset = UNSET
    inputs: list[InputSpec] | None | Unset = UNSET
    outputs: list[OutputSpec] | None | Unset = UNSET
    implementation: ContainerImplementation | GraphImplementation | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.container_implementation import ContainerImplementation
        from ..models.graph_implementation import GraphImplementation
        from ..models.metadata_spec import MetadataSpec

        name: None | str | Unset
        if isinstance(self.name, Unset):
            name = UNSET
        else:
            name = self.name

        description: None | str | Unset
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

        metadata: dict[str, Any] | None | Unset
        if isinstance(self.metadata, Unset):
            metadata = UNSET
        elif isinstance(self.metadata, MetadataSpec):
            metadata = self.metadata.to_dict()
        else:
            metadata = self.metadata

        inputs: list[dict[str, Any]] | None | Unset
        if isinstance(self.inputs, Unset):
            inputs = UNSET
        elif isinstance(self.inputs, list):
            inputs = []
            for inputs_type_0_item_data in self.inputs:
                inputs_type_0_item = inputs_type_0_item_data.to_dict()
                inputs.append(inputs_type_0_item)

        else:
            inputs = self.inputs

        outputs: list[dict[str, Any]] | None | Unset
        if isinstance(self.outputs, Unset):
            outputs = UNSET
        elif isinstance(self.outputs, list):
            outputs = []
            for outputs_type_0_item_data in self.outputs:
                outputs_type_0_item = outputs_type_0_item_data.to_dict()
                outputs.append(outputs_type_0_item)

        else:
            outputs = self.outputs

        implementation: dict[str, Any] | None | Unset
        if isinstance(self.implementation, Unset):
            implementation = UNSET
        elif isinstance(self.implementation, ContainerImplementation):
            implementation = self.implementation.to_dict()
        elif isinstance(self.implementation, GraphImplementation):
            implementation = self.implementation.to_dict()
        else:
            implementation = self.implementation

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if name is not UNSET:
            field_dict["name"] = name
        if description is not UNSET:
            field_dict["description"] = description
        if metadata is not UNSET:
            field_dict["metadata"] = metadata
        if inputs is not UNSET:
            field_dict["inputs"] = inputs
        if outputs is not UNSET:
            field_dict["outputs"] = outputs
        if implementation is not UNSET:
            field_dict["implementation"] = implementation

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.container_implementation import ContainerImplementation
        from ..models.graph_implementation import GraphImplementation
        from ..models.input_spec import InputSpec
        from ..models.metadata_spec import MetadataSpec
        from ..models.output_spec import OutputSpec

        d = dict(src_dict)

        def _parse_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        name = _parse_name(d.pop("name", UNSET))

        def _parse_description(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        description = _parse_description(d.pop("description", UNSET))

        def _parse_metadata(data: object) -> MetadataSpec | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                metadata_type_0 = MetadataSpec.from_dict(data)

                return metadata_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(MetadataSpec | None | Unset, data)

        metadata = _parse_metadata(d.pop("metadata", UNSET))

        def _parse_inputs(data: object) -> list[InputSpec] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                inputs_type_0 = []
                _inputs_type_0 = data
                for inputs_type_0_item_data in _inputs_type_0:
                    inputs_type_0_item = InputSpec.from_dict(inputs_type_0_item_data)

                    inputs_type_0.append(inputs_type_0_item)

                return inputs_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[InputSpec] | None | Unset, data)

        inputs = _parse_inputs(d.pop("inputs", UNSET))

        def _parse_outputs(data: object) -> list[OutputSpec] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                outputs_type_0 = []
                _outputs_type_0 = data
                for outputs_type_0_item_data in _outputs_type_0:
                    outputs_type_0_item = OutputSpec.from_dict(outputs_type_0_item_data)

                    outputs_type_0.append(outputs_type_0_item)

                return outputs_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[OutputSpec] | None | Unset, data)

        outputs = _parse_outputs(d.pop("outputs", UNSET))

        def _parse_implementation(data: object) -> ContainerImplementation | GraphImplementation | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                implementation_type_0 = ContainerImplementation.from_dict(data)

                return implementation_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                implementation_type_1 = GraphImplementation.from_dict(data)

                return implementation_type_1
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ContainerImplementation | GraphImplementation | None | Unset, data)

        implementation = _parse_implementation(d.pop("implementation", UNSET))

        component_spec = cls(
            name=name,
            description=description,
            metadata=metadata,
            inputs=inputs,
            outputs=outputs,
            implementation=implementation,
        )

        component_spec.additional_properties = d
        return component_spec

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
