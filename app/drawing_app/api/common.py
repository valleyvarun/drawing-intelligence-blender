"""Shared conversions for the scripting API: units, vectors, colours, objects, and the viewport context."""

from __future__ import annotations

import math
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from ..core.blender import bpy
from ..core.state import Vec3, color_hex, parse_hex
from .registry import ToolError


def unit_scale() -> float:
    """Metres per Blender unit in the current scene (1.0 unless a project uses another scale)."""
    return max(float(bpy.context.scene.unit_settings.scale_length), 1e-12)


def to_units(meters: float) -> float:
    return meters / unit_scale()


def to_meters(units: float) -> float:
    return units * unit_scale()


def number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ToolError(f"{label} must be a finite number")
    return float(value)


def vector(value: object, label: str) -> Vec3:
    """A finite [x, y, z] triple."""
    if not isinstance(value, Sequence) or isinstance(value, str) or len(value) != 3:
        raise ToolError(f"{label} must be [x, y, z]")
    components: list[float] = []
    component: object
    for component in value:
        components.append(number(component, label))
    return components[0], components[1], components[2]


def position(value: object, label: str) -> Vec3:
    """A point given in metres, in Blender units."""
    point: Vec3 = vector(value, label)
    return to_units(point[0]), to_units(point[1]), to_units(point[2])


def rounded(values: Sequence[float], digits: int = 4) -> list[float]:
    result: list[float] = []
    value: float
    for value in values:
        result.append(round(float(value), digits) + 0.0)
    return result


def meters(values: Sequence[float]) -> list[float]:
    """Blender-unit coordinates as rounded metres."""
    scale: float = unit_scale()
    converted: list[float] = []
    value: float
    for value in values:
        converted.append(float(value) * scale)
    return rounded(converted)


def color(value: str) -> Vec3:
    """A linear colour from "#RRGGBB", "RRGGBB", "#RGB", or a preset name such as "red"."""
    from ..tools.pencil import COLORS

    name: str
    preset: Vec3
    for name, preset in COLORS:
        if value.strip().lower() == name.lower():
            return preset
    parsed: Vec3 | None = parse_hex(value)
    if parsed is None:
        names: list[str] = []
        entry: tuple[str, Vec3]
        for entry in COLORS:
            names.append(entry[0])
        raise ToolError(f"Colours are hex strings such as '#FF8000' or one of: {', '.join(names)}")
    return parsed


def hex_color(value: Sequence[float]) -> str:
    return "#" + color_hex((float(value[0]), float(value[1]), float(value[2])))


def find_object(name: str) -> Any:
    found: Any = bpy.data.objects.get(name)
    if found is None:
        raise ToolError(f"No object named {name!r}. Use scene_info to list objects.")
    return found


def kind(obj: Any) -> str:
    """What an object is in the app: a stroke kind, a drawing plane, or a Blender object type."""
    drawing: object = obj.get("drawing_kind")
    if obj.type == "GREASEPENCIL":
        return "grease"
    if isinstance(drawing, str) and drawing:
        return drawing
    if obj.get("drawing_plane"):
        return "drawing_plane"
    return str(obj.type).lower()


def group_of(obj: Any) -> str:
    """The drawing group that owns an object (its stroke collection's parent for strokes)."""
    collection: Any
    for collection in obj.users_collection:
        if collection.get("drawing_strokes"):
            parent: Any
            for parent in bpy.data.collections:
                if collection.name in parent.children:
                    return str(parent.name)
        return str(collection.name)
    return ""


@contextmanager
def viewport() -> Iterator[Any]:
    """The drawing viewport as context, whether called from the bridge, a timer, or the text editor.

    Without a window (background mode) the current context is used as-is.
    """
    from ..ui import layout

    if not bpy.app.background:
        window: Any
        area: Any
        region: Any
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if layout.role(area) != "viewport" or area.type != "VIEW_3D":
                    continue
                for region in area.regions:
                    if region.type == "WINDOW":
                        with bpy.context.temp_override(window=window, screen=window.screen, area=area, region=region):
                            yield bpy.context
                        return
    yield bpy.context
