"""Bundle schema models and JSON helpers."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class SchemaError(ValueError):
    """Raised when bundle/view JSON does not match expected schema."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SchemaError(message)


def _coerce_float(value: Any, field_name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise SchemaError(f"Field '{field_name}' must be a number; got {value!r}.") from exc
    if not math.isfinite(parsed):
        raise SchemaError(f"Field '{field_name}' must be finite; got {value!r}.")
    return parsed


def _coerce_int(value: Any, field_name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise SchemaError(f"Field '{field_name}' must be an integer; got {value!r}.") from exc
    return parsed


@dataclass
class BundleMeta:
    created_at: str
    source_pse: str
    exported_objects: list[str] = field(default_factory=list)
    format: str = "pdb"
    state_mode: str = "current"
    selection: str = "all"

    def __post_init__(self) -> None:
        _require(bool(self.created_at), "Field 'created_at' must be a non-empty string.")
        _require(bool(self.source_pse), "Field 'source_pse' must be a non-empty string.")
        _require(
            isinstance(self.exported_objects, list) and all(
                isinstance(item, str) and item for item in self.exported_objects
            ),
            "Field 'exported_objects' must be a list of non-empty strings.",
        )
        _require(self.format in {"pdb", "cif"}, "Field 'format' must be one of: pdb, cif.")
        _require(
            self.state_mode in {"current", "1", "all"},
            "Field 'state_mode' must be one of: current, 1, all.",
        )
        _require(isinstance(self.selection, str), "Field 'selection' must be a string.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "created_at": self.created_at,
            "source_pse": self.source_pse,
            "exported_objects": list(self.exported_objects),
            "format": self.format,
            "state_mode": self.state_mode,
            "selection": self.selection,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BundleMeta":
        if not isinstance(data, dict):
            raise SchemaError("Bundle metadata must be a JSON object.")
        return cls(
            created_at=str(data.get("created_at", "")),
            source_pse=str(data.get("source_pse", "")),
            exported_objects=list(data.get("exported_objects", [])),
            format=str(data.get("format", "pdb")),
            state_mode=str(data.get("state_mode", "current")),
            selection=str(data.get("selection", "all")),
        )


@dataclass
class ViewData:
    pymol_view: list[float]
    fov_deg: float | None = None
    orthoscopic: bool | None = None
    viewport_px: tuple[int, int] | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        _require(
            isinstance(self.pymol_view, list) and len(self.pymol_view) == 18,
            "Field 'pymol_view' must be a list of 18 floats.",
        )
        self.pymol_view = [
            _coerce_float(value, f"pymol_view[{idx}]")
            for idx, value in enumerate(self.pymol_view)
        ]
        if self.fov_deg is not None:
            self.fov_deg = _coerce_float(self.fov_deg, "fov_deg")
        if self.orthoscopic is not None:
            _require(isinstance(self.orthoscopic, bool), "Field 'orthoscopic' must be boolean.")
        if self.viewport_px is not None:
            _require(
                isinstance(self.viewport_px, (tuple, list)) and len(self.viewport_px) == 2,
                "Field 'viewport_px' must be [width, height].",
            )
            w = _coerce_int(self.viewport_px[0], "viewport_px[0]")
            h = _coerce_int(self.viewport_px[1], "viewport_px[1]")
            _require(w > 0 and h > 0, "Field 'viewport_px' values must be positive.")
            self.viewport_px = (w, h)
        _require(isinstance(self.notes, str), "Field 'notes' must be a string.")

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "pymol_view": list(self.pymol_view),
            "fov_deg": self.fov_deg,
            "orthoscopic": self.orthoscopic,
            "notes": self.notes,
        }
        if self.viewport_px is not None:
            payload["viewport_px"] = [self.viewport_px[0], self.viewport_px[1]]
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ViewData":
        if not isinstance(data, dict):
            raise SchemaError("View data must be a JSON object.")
        viewport = data.get("viewport_px")
        if viewport is not None and isinstance(viewport, list):
            viewport = tuple(viewport)
        return cls(
            pymol_view=list(data.get("pymol_view", [])),
            fov_deg=data.get("fov_deg"),
            orthoscopic=data.get("orthoscopic"),
            viewport_px=viewport,  # type: ignore[arg-type]
            notes=str(data.get("notes", "")),
        )


def _read_json(path: Path | str) -> dict[str, Any]:
    payload_path = Path(path)
    try:
        raw = payload_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SchemaError(f"Could not read JSON file at '{payload_path}': {exc}") from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SchemaError(f"Invalid JSON in '{payload_path}': {exc}") from exc
    if not isinstance(parsed, dict):
        raise SchemaError(f"Expected JSON object in '{payload_path}'.")
    return parsed


def _write_json(path: Path | str, payload: dict[str, Any]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        raise SchemaError(f"Could not write JSON file at '{output_path}': {exc}") from exc


def read_bundle_meta(path: Path | str) -> BundleMeta:
    return BundleMeta.from_dict(_read_json(path))


def write_bundle_meta(path: Path | str, data: BundleMeta) -> None:
    _write_json(path, data.to_dict())


def read_view_data(path: Path | str) -> ViewData:
    return ViewData.from_dict(_read_json(path))


def write_view_data(path: Path | str, data: ViewData) -> None:
    _write_json(path, data.to_dict())
