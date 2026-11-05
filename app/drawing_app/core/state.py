"""Application-owned state shared by the Blender adapters and custom workspace."""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal, TypeAlias, cast

Vec3: TypeAlias = tuple[float, float, float]
Tool: TypeAlias = Literal["select", "move", "rotate", "scale", "paint", "pipe", "grease"]
ROOT: Path = Path(__file__).resolve().parents[3]
CONFIG: Path = Path(os.environ.get("DRAWING_APP_CONFIG", str(ROOT / "runtime/config")))


@dataclass
class Brush:
    radius: float = 0.02
    hardness: float = 1.0
    opacity: float = 1.0
    shape: str = "circle"
    color: Vec3 = (0.015, 0.015, 0.015)
    pressure_size: bool = False
    pressure_hardness: bool = False
    pressure_opacity: bool = False


def _brushes() -> dict[str, Brush]:
    return {"paint": Brush(), "pipe": Brush(), "grease": Brush()}


@dataclass
class AppState:
    tool: Tool = "select"
    theme: str = "light"
    units: str = "m"
    navigation: str = "orbit"
    zoom_speed: float = 1.0
    ruler: bool = True
    hud: bool = False
    fps_limit: int = 60
    drawing_plane: bool = False
    on_object: bool = False
    axes_spokes: bool = False
    cursor_active: bool = False
    cursor_placed: bool = False
    cursor_placing: bool = False
    cursor_axis: int = 1
    cursor_side: int = 1
    active_plane: str = "Ground Plane"
    active_group: str = "Default Drawing"
    brushes: dict[str, Brush] = field(default_factory=_brushes)
    status: str = ""
    left_width: int = 220
    right_width: int = 330

    @property
    def brush(self) -> Brush:
        return self.brushes.get(self.tool, self.brushes["paint"])


_STATE: AppState = AppState()


def get_state() -> AppState:
    return _STATE


def save_preferences() -> None:
    folder: Path = CONFIG
    folder.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = asdict(_STATE)
    payload["tool"] = "select"
    payload["cursor_placing"] = False
    temporary: Path = folder / f"drawing-preferences-{os.getpid()}.tmp"
    destination: Path = folder / "drawing-preferences.json"
    encoded: str = json.dumps(payload, indent=2)
    error: OSError
    try:
        temporary.write_text(encoded, encoding="utf-8")
        attempt: int
        for attempt in range(3):
            try:
                temporary.replace(destination)
                return
            except PermissionError:
                if attempt < 2:
                    time.sleep(0.015)
        # Windows scanners can briefly deny rename while permitting file writes.
        destination.write_text(encoded, encoding="utf-8")
    except OSError as error:
        _STATE.status = "Settings could not be saved: " + str(error)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def load_preferences() -> None:
    path: Path = CONFIG / "drawing-preferences.json"
    if not path.is_file():
        return
    try:
        decoded: object = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(decoded, dict):
            raise ValueError("Preferences must be a JSON object")
        data: dict[str, object] = cast(dict[str, object], decoded)
        name: str
        choices: dict[str, tuple[str, ...]] = {
            "theme": ("light", "dark"),
            "units": ("m", "mm"),
            "navigation": ("orbit", "fly"),
        }
        for name in choices:
            value: object = data.get(name)
            if isinstance(value, str) and value in choices[name]:
                setattr(_STATE, name, value)
        speed: object = data.get("zoom_speed")
        if isinstance(speed, (float, int)) and math.isfinite(speed):
            _STATE.zoom_speed = max(0.1, min(10.0, float(speed)))
        for name in ("ruler", "hud"):
            value = data.get(name)
            if isinstance(value, bool):
                setattr(_STATE, name, value)
        for name in ("left_width", "right_width"):
            value = data.get(name)
            if isinstance(value, int):
                setattr(_STATE, name, max(180, min(600, value)))
        limit: object = data.get("fps_limit")
        if isinstance(limit, int) and limit in (0, 30, 60, 120):
            _STATE.fps_limit = limit
        restore_brushes(data.get("brushes"))
    except (OSError, ValueError, TypeError):
        _STATE.status = "Saved preferences could not be read; using defaults."


def restore_brushes(payload: object) -> None:
    if not isinstance(payload, dict):
        return
    brushes: dict[str, object] = cast(dict[str, object], payload)
    kind: str
    for kind in ("paint", "pipe", "grease"):
        raw: object = brushes.get(kind)
        if not isinstance(raw, dict):
            continue
        data: dict[str, object] = cast(dict[str, object], raw)
        brush: Brush = _STATE.brushes[kind]
        name: str
        for name in ("radius", "hardness", "opacity"):
            value: object = data.get(name)
            if isinstance(value, (float, int)) and math.isfinite(value):
                minimum: float = 0.00001 if name == "radius" else 0.0
                maximum: float = 100.0 if name == "radius" else 1.0
                setattr(brush, name, max(minimum, min(maximum, float(value))))
        for name in ("pressure_size", "pressure_hardness", "pressure_opacity"):
            value = data.get(name)
            if isinstance(value, bool):
                setattr(brush, name, value)
        shape: object = data.get("shape")
        if shape in ("circle", "square", "triangle"):
            brush.shape = str(shape)
        color: object = data.get("color")
        if isinstance(color, (list, tuple)) and len(color) == 3:
            values: list[object] = list(color)
            normalized: list[float] = []
            component: object
            for component in values:
                if not isinstance(component, (float, int)) or not math.isfinite(component):
                    break
                normalized.append(max(0.0, min(1.0, float(component))))
            if len(normalized) == 3:
                brush.color = (normalized[0], normalized[1], normalized[2])
