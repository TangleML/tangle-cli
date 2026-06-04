from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.component_library_folder_annotations_type_0 import ComponentLibraryFolderAnnotationsType0
    from ..models.component_reference import ComponentReference


T = TypeVar("T", bound="ComponentLibraryFolder")


@_attrs_define
class ComponentLibraryFolder:
    """
    Attributes:
        name (str):
        folders (list[ComponentLibraryFolder] | None | Unset):
        components (list[ComponentReference] | None | Unset):
        annotations (ComponentLibraryFolderAnnotationsType0 | None | Unset):
    """

    name: str
    folders: list[ComponentLibraryFolder] | None | Unset = UNSET
    components: list[ComponentReference] | None | Unset = UNSET
    annotations: ComponentLibraryFolderAnnotationsType0 | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.component_library_folder_annotations_type_0 import ComponentLibraryFolderAnnotationsType0

        name = self.name

        folders: list[dict[str, Any]] | None | Unset
        if isinstance(self.folders, Unset):
            folders = UNSET
        elif isinstance(self.folders, list):
            folders = []
            for folders_type_0_item_data in self.folders:
                folders_type_0_item = folders_type_0_item_data.to_dict()
                folders.append(folders_type_0_item)

        else:
            folders = self.folders

        components: list[dict[str, Any]] | None | Unset
        if isinstance(self.components, Unset):
            components = UNSET
        elif isinstance(self.components, list):
            components = []
            for components_type_0_item_data in self.components:
                components_type_0_item = components_type_0_item_data.to_dict()
                components.append(components_type_0_item)

        else:
            components = self.components

        annotations: dict[str, Any] | None | Unset
        if isinstance(self.annotations, Unset):
            annotations = UNSET
        elif isinstance(self.annotations, ComponentLibraryFolderAnnotationsType0):
            annotations = self.annotations.to_dict()
        else:
            annotations = self.annotations

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "name": name,
            }
        )
        if folders is not UNSET:
            field_dict["folders"] = folders
        if components is not UNSET:
            field_dict["components"] = components
        if annotations is not UNSET:
            field_dict["annotations"] = annotations

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.component_library_folder_annotations_type_0 import ComponentLibraryFolderAnnotationsType0
        from ..models.component_reference import ComponentReference

        d = dict(src_dict)
        name = d.pop("name")

        def _parse_folders(data: object) -> list[ComponentLibraryFolder] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                folders_type_0 = []
                _folders_type_0 = data
                for folders_type_0_item_data in _folders_type_0:
                    folders_type_0_item = ComponentLibraryFolder.from_dict(folders_type_0_item_data)

                    folders_type_0.append(folders_type_0_item)

                return folders_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[ComponentLibraryFolder] | None | Unset, data)

        folders = _parse_folders(d.pop("folders", UNSET))

        def _parse_components(data: object) -> list[ComponentReference] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                components_type_0 = []
                _components_type_0 = data
                for components_type_0_item_data in _components_type_0:
                    components_type_0_item = ComponentReference.from_dict(components_type_0_item_data)

                    components_type_0.append(components_type_0_item)

                return components_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[ComponentReference] | None | Unset, data)

        components = _parse_components(d.pop("components", UNSET))

        def _parse_annotations(data: object) -> ComponentLibraryFolderAnnotationsType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                annotations_type_0 = ComponentLibraryFolderAnnotationsType0.from_dict(data)

                return annotations_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ComponentLibraryFolderAnnotationsType0 | None | Unset, data)

        annotations = _parse_annotations(d.pop("annotations", UNSET))

        component_library_folder = cls(
            name=name,
            folders=folders,
            components=components,
            annotations=annotations,
        )

        component_library_folder.additional_properties = d
        return component_library_folder

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
