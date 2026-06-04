from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.metadata_spec_annotations_type_0 import MetadataSpecAnnotationsType0
    from ..models.metadata_spec_labels_type_0 import MetadataSpecLabelsType0


T = TypeVar("T", bound="MetadataSpec")


@_attrs_define
class MetadataSpec:
    """
    Attributes:
        annotations (MetadataSpecAnnotationsType0 | None | Unset):
        labels (MetadataSpecLabelsType0 | None | Unset):
    """

    annotations: MetadataSpecAnnotationsType0 | None | Unset = UNSET
    labels: MetadataSpecLabelsType0 | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.metadata_spec_annotations_type_0 import MetadataSpecAnnotationsType0
        from ..models.metadata_spec_labels_type_0 import MetadataSpecLabelsType0

        annotations: dict[str, Any] | None | Unset
        if isinstance(self.annotations, Unset):
            annotations = UNSET
        elif isinstance(self.annotations, MetadataSpecAnnotationsType0):
            annotations = self.annotations.to_dict()
        else:
            annotations = self.annotations

        labels: dict[str, Any] | None | Unset
        if isinstance(self.labels, Unset):
            labels = UNSET
        elif isinstance(self.labels, MetadataSpecLabelsType0):
            labels = self.labels.to_dict()
        else:
            labels = self.labels

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if annotations is not UNSET:
            field_dict["annotations"] = annotations
        if labels is not UNSET:
            field_dict["labels"] = labels

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.metadata_spec_annotations_type_0 import MetadataSpecAnnotationsType0
        from ..models.metadata_spec_labels_type_0 import MetadataSpecLabelsType0

        d = dict(src_dict)

        def _parse_annotations(data: object) -> MetadataSpecAnnotationsType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                annotations_type_0 = MetadataSpecAnnotationsType0.from_dict(data)

                return annotations_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(MetadataSpecAnnotationsType0 | None | Unset, data)

        annotations = _parse_annotations(d.pop("annotations", UNSET))

        def _parse_labels(data: object) -> MetadataSpecLabelsType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                labels_type_0 = MetadataSpecLabelsType0.from_dict(data)

                return labels_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(MetadataSpecLabelsType0 | None | Unset, data)

        labels = _parse_labels(d.pop("labels", UNSET))

        metadata_spec = cls(
            annotations=annotations,
            labels=labels,
        )

        metadata_spec.additional_properties = d
        return metadata_spec

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
