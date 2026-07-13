"""Pipeline-run annotation helpers."""

from __future__ import annotations

from typing import Any

from .handler import TangleCliHandler
from .pipeline_run_manager import PipelineRunError


class AnnotationManager(TangleCliHandler):
    """Manage annotations on Tangle pipeline runs."""

    _required_client_error_type = PipelineRunError

    @staticmethod
    def to_plain(value: Any) -> Any:
        if hasattr(value, "to_dict"):
            return value.to_dict()
        if hasattr(value, "model_dump"):
            return value.model_dump(by_alias=True)
        return value

    def list_annotations(self, run_id: str) -> dict[str, Any]:
        with self._surface_http_errors():
            annotations = self.to_plain(self._require_client().pipeline_runs_annotations(run_id)) or {}
        if not isinstance(annotations, dict):
            annotations = dict(annotations)
        return {
            "status": "success",
            "run_id": run_id,
            "count": len(annotations),
            "annotations": annotations,
        }

    def set_annotation(self, run_id: str, key: str, value: Any = None) -> dict[str, Any]:
        with self._surface_http_errors():
            self._require_client().pipeline_runs_put_annotations(run_id, key, value=value)
        return {"status": "success", "run_id": run_id, "key": key, "value": value}

    def delete_annotation(self, run_id: str, key: str) -> dict[str, Any]:
        with self._surface_http_errors():
            self._require_client().pipeline_runs_delete_annotations(run_id, key)
        return {"status": "success", "run_id": run_id, "key": key}


__all__ = ["AnnotationManager"]
