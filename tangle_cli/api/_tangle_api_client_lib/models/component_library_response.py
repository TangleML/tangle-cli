from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.component_library_folder import ComponentLibraryFolder
    from ..models.component_library_response_annotations_type_0 import ComponentLibraryResponseAnnotationsType0


T = TypeVar("T", bound="ComponentLibraryResponse")


@_attrs_define
class ComponentLibraryResponse:
    """
    Attributes:
        id (str):
        name (str):
        created_at (datetime.datetime):
        updated_at (datetime.datetime):
        root_folder (ComponentLibraryFolder | None | Unset):
        published_by (None | str | Unset):
        hide_from_search (bool | Unset):  Default: False.
        annotations (ComponentLibraryResponseAnnotationsType0 | None | Unset):
        component_count (int | Unset):  Default: 0.
    """

    id: str
    name: str
    created_at: datetime.datetime
    updated_at: datetime.datetime
    root_folder: ComponentLibraryFolder | None | Unset = UNSET
    published_by: None | str | Unset = UNSET
    hide_from_search: bool | Unset = False
    annotations: ComponentLibraryResponseAnnotationsType0 | None | Unset = UNSET
    component_count: int | Unset = 0
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.component_library_folder import ComponentLibraryFolder
        from ..models.component_library_response_annotations_type_0 import ComponentLibraryResponseAnnotationsType0

        id = self.id

        name = self.name

        created_at = self.created_at.isoformat()

        updated_at = self.updated_at.isoformat()

        root_folder: dict[str, Any] | None | Unset
        if isinstance(self.root_folder, Unset):
            root_folder = UNSET
        elif isinstance(self.root_folder, ComponentLibraryFolder):
            root_folder = self.root_folder.to_dict()
        else:
            root_folder = self.root_folder

        published_by: None | str | Unset
        if isinstance(self.published_by, Unset):
            published_by = UNSET
        else:
            published_by = self.published_by

        hide_from_search = self.hide_from_search

        annotations: dict[str, Any] | None | Unset
        if isinstance(self.annotations, Unset):
            annotations = UNSET
        elif isinstance(self.annotations, ComponentLibraryResponseAnnotationsType0):
            annotations = self.annotations.to_dict()
        else:
            annotations = self.annotations

        component_count = self.component_count

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "name": name,
                "created_at": created_at,
                "updated_at": updated_at,
            }
        )
        if root_folder is not UNSET:
            field_dict["root_folder"] = root_folder
        if published_by is not UNSET:
            field_dict["published_by"] = published_by
        if hide_from_search is not UNSET:
            field_dict["hide_from_search"] = hide_from_search
        if annotations is not UNSET:
            field_dict["annotations"] = annotations
        if component_count is not UNSET:
            field_dict["component_count"] = component_count

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.component_library_folder import ComponentLibraryFolder
        from ..models.component_library_response_annotations_type_0 import ComponentLibraryResponseAnnotationsType0

        d = dict(src_dict)
        id = d.pop("id")

        name = d.pop("name")

        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        updated_at = datetime.datetime.fromisoformat(d.pop("updated_at"))

        def _parse_root_folder(data: object) -> ComponentLibraryFolder | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                root_folder_type_0 = ComponentLibraryFolder.from_dict(data)

                return root_folder_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ComponentLibraryFolder | None | Unset, data)

        root_folder = _parse_root_folder(d.pop("root_folder", UNSET))

        def _parse_published_by(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        published_by = _parse_published_by(d.pop("published_by", UNSET))

        hide_from_search = d.pop("hide_from_search", UNSET)

        def _parse_annotations(data: object) -> ComponentLibraryResponseAnnotationsType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                annotations_type_0 = ComponentLibraryResponseAnnotationsType0.from_dict(data)

                return annotations_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ComponentLibraryResponseAnnotationsType0 | None | Unset, data)

        annotations = _parse_annotations(d.pop("annotations", UNSET))

        component_count = d.pop("component_count", UNSET)

        component_library_response = cls(
            id=id,
            name=name,
            created_at=created_at,
            updated_at=updated_at,
            root_folder=root_folder,
            published_by=published_by,
            hide_from_search=hide_from_search,
            annotations=annotations,
            component_count=component_count,
        )

        component_library_response.additional_properties = d
        return component_library_response

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
