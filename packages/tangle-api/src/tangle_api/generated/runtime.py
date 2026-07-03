"""Runtime helpers for generated Tangle API model packages."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

try:
    from pydantic import ConfigDict
except ImportError:  # pragma: no cover - pydantic v1 fallback
    ConfigDict = None  # type: ignore[assignment]


class TangleGeneratedModel(BaseModel):
    """Base for generated response models with dict-like conveniences."""

    if ConfigDict is not None:
        model_config = ConfigDict(extra="allow", populate_by_name=True)
    else:  # pragma: no cover - pydantic v1 fallback
        class Config:
            extra = "allow"
            allow_population_by_field_name = True

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def to_dict(self) -> dict[str, Any]:
        if hasattr(self, "model_dump"):
            return self.model_dump(by_alias=True)
        return self.dict(by_alias=True)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Any:
        if hasattr(cls, "model_validate"):
            return cls.model_validate(data)
        return cls.parse_obj(data)


__all__ = ["TangleGeneratedModel"]
