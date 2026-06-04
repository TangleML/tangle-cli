from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.component_library_annotations_type_0 import ComponentLibraryAnnotationsType0
    from ..models.component_library_folder import ComponentLibraryFolder


T = TypeVar("T", bound="ComponentLibrary")


@_attrs_define
class ComponentLibrary:
    """
    Attributes:
        name (str):
        root_folder (ComponentLibraryFolder):
        annotations (ComponentLibraryAnnotationsType0 | None | Unset):
    """

    name: str
    root_folder: ComponentLibraryFolder
    annotations: ComponentLibraryAnnotationsType0 | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.component_library_annotations_type_0 import ComponentLibraryAnnotationsType0

        name = self.name

        root_folder = self.root_folder.to_dict()

        annotations: dict[str, Any] | None | Unset
        if isinstance(self.annotations, Unset):
            annotations = UNSET
        elif isinstance(self.annotations, ComponentLibraryAnnotationsType0):
            annotations = self.annotations.to_dict()
        else:
            annotations = self.annotations

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "name": name,
                "root_folder": root_folder,
            }
        )
        if annotations is not UNSET:
            field_dict["annotations"] = annotations

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.component_library_annotations_type_0 import ComponentLibraryAnnotationsType0
        from ..models.component_library_folder import ComponentLibraryFolder

        d = dict(src_dict)
        name = d.pop("name")

        root_folder = ComponentLibraryFolder.from_dict(d.pop("root_folder"))

        def _parse_annotations(data: object) -> ComponentLibraryAnnotationsType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                annotations_type_0 = ComponentLibraryAnnotationsType0.from_dict(data)

                return annotations_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ComponentLibraryAnnotationsType0 | None | Unset, data)

        annotations = _parse_annotations(d.pop("annotations", UNSET))

        component_library = cls(
            name=name,
            root_folder=root_folder,
            annotations=annotations,
        )

        component_library.additional_properties = d
        return component_library

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
