"""GPU workspace panels with mouse, keyboard, slider and text-field interaction."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeAlias

from ..core.blender import bpy, gpu
from ..core.state import AppState, Brush, get_state
from . import layout, menus, paint

Rect: TypeAlias = tuple[float, float, float, float]
MenuItem: TypeAlias = tuple[str, str, object]


@dataclass
class Widget:
    key: str
    bounds: Rect
    action: str = ""
    payload: object = None
    kind: str = "button"
    value: float = 0.0
    minimum: float = -1e9
    maximum: float = 1e9
    scale: float = 1.0
    prefix: tuple[object, ...] = ()
    tooltip: str = ""


@dataclass
class Edit:
    widget: Widget
    text: str
    caret: int
    selected: bool = True


@dataclass
class Popup:
    area: int
    x: float
    top: float
    items: list[MenuItem]


_DISPATCH: Callable[[str, object], None] | None = None
_HANDLES: list[tuple[Any, Any]] = []
_WIDGETS: dict[int, list[Widget]] = {}
_SCROLL: dict[str, float] = {"toolbox": 0.0, "layers": 0.0, "stage": 0.0}
_LIMITS: dict[str, float] = {}
_COLLAPSED: set[str] = set()
_SELECTED_LAYER: str = ""
_SEARCH: str = ""
_EDIT: Edit | None = None
_DRAG: Widget | None = None
_DRAG_ORIGIN: float = 0.0
_POPUP: Popup | None = None
_HOVER: tuple[int, float, float] = (0, 0.0, 0.0)
_HOVER_TIME: float = 0.0
_CURRENT: list[Widget] = []
_AREA: int = 0
_SCALE: float = 1.0


def _show_tooltip() -> float | None:
    remaining: float = 0.65 - (time.monotonic() - _HOVER_TIME)
    if remaining > 0:
        return remaining
    layout.redraw()
    return None


def _emit(action: str, payload: object = None) -> None:
    if _DISPATCH is not None:
        _DISPATCH(action, payload)
    layout.redraw()


def _hit(bounds: Rect, x: float, y: float) -> bool:
    return bounds[0] <= x <= bounds[0] + bounds[2] and bounds[1] <= y <= bounds[1] + bounds[3]


def _widget_at(widgets: list[Widget], x: float, y: float, tooltip: bool = False) -> Widget | None:
    widget: Widget
    for widget in reversed(widgets):
        if (not tooltip or widget.tooltip) and _hit(widget.bounds, x, y):
            return widget
    return None


def _button(
    key: str,
    label: str,
    x: float,
    y: float,
    w: float,
    action: str,
    payload: object = None,
    *,
    selected: bool = False,
    icon: str = "",
    tooltip: str = "",
    height: float = 26.0,
) -> None:
    hovered: bool = _HOVER[0] == _AREA and _hit((x, y, w, height), _HOVER[1], _HOVER[2])
    background: paint.Color = paint.SELECTED if selected else ((0.22, 0.22, 0.22, 1) if hovered else paint.BUTTON)
    paint.rect(x, y, w, height, (0.10, 0.10, 0.10, 1), 5)
    paint.rect(x + 1, y + 1, w - 2, height - 2, background, 4)
    if icon:
        paint.icon(icon, x + (w - height + 6) / 2, y + 3, height - 6, (0.90, 0.90, 0.90, 1))
    else:
        tw: float = paint.measure(label)
        paint.text(label, x + max(4, (w - tw) * 0.5), y + (height - 13) * 0.5 + 2, max_width=w - 8)
    _CURRENT.append(Widget(key, (x, y, w, height), action, payload, tooltip=tooltip or label))


def _field(
    key: str,
    x: float,
    y: float,
    w: float,
    value: float,
    action: str,
    prefix: tuple[object, ...],
    *,
    scale: float = 1.0,
    minimum: float = -1e9,
    maximum: float = 1e9,
    tooltip: str = "",
) -> None:
    focused: bool = _EDIT is not None and _EDIT.widget.key == key
    paint.rect(x, y, w, 24, paint.SELECTED if focused else (0.11, 0.11, 0.11, 1), 3)
    paint.rect(x + 1, y + 1, w - 2, 22, paint.FIELD, 2)
    label: str = _EDIT.text if focused and _EDIT is not None else f"{value:.6g}"
    paint.text(label, x + 6, y + 6, max_width=w - 12)
    if focused and _EDIT is not None:
        if _EDIT.selected:
            paint.rect(x + 4, y + 3, min(w - 8, paint.measure(label) + 4), 18, (0.25, 0.45, 0.65, 0.45))
        else:
            cursor_x: float = min(x + w - 5, x + 6 + paint.measure(label[: _EDIT.caret]))
            paint.line([(cursor_x, y + 4), (cursor_x, y + 20)])
    _CURRENT.append(
        Widget(
            key,
            (x, y, w, 24),
            action,
            kind="field",
            value=value,
            minimum=minimum,
            maximum=maximum,
            scale=scale,
            prefix=prefix,
            tooltip=tooltip,
        )
    )


def _checkbox(key: str, label: str, x: float, y: float, value: bool, action: str, payload: object) -> None:
    paint.rect(x, y + 3, 16, 16, (0.72, 0.72, 0.72, 1), 3)
    if value:
        paint.rect(x + 2, y + 5, 12, 12, paint.SELECTED, 2)
        paint.line([(x + 4, y + 10), (x + 7, y + 7), (x + 13, y + 14)], (1, 1, 1, 1), 1.5)
    paint.text(label, x + 24, y + 6)
    _CURRENT.append(Widget(key, (x, y, max(24.0, paint.measure(label) + 28), 24), action, payload, tooltip=label))


def _units() -> tuple[float, float]:
    units: str = get_state().units
    scale: float = {"mm": 1000.0, "cm": 100.0, "m": 1.0, "in": 39.3700787402, "ft": 3.280839895}.get(units, 1.0)
    return scale, {"mm": 1.0, "cm": 0.1, "m": 0.01, "in": 0.1, "ft": 0.01}.get(units, 0.01)


def _xyz(
    label: str,
    values: tuple[float, float, float],
    x: float,
    y: float,
    w: float,
    action: str,
    prefix: tuple[object, ...],
    kind: str = "location",
) -> float:
    paint.text(label, x, y)
    y -= 32
    factor: float
    step: float
    factor, step = _units() if kind == "location" else (1.0, 1.0 if kind == "rotation" else 0.1)
    colors: tuple[paint.Color, ...] = ((0.9, 0.36, 0.36, 1), (0.36, 0.85, 0.4, 1), (0.32, 0.55, 0.92, 1))
    axis: int
    name: str
    for axis, name in enumerate(("X", "Y", "Z")):
        value: float = values[axis] * factor
        paint.text(name, x, y + 6, color=colors[axis])
        _button(
            f"{action}_{kind}_{axis}_minus",
            "-",
            x + 20,
            y,
            20,
            action,
            (*prefix, axis, (value - step) / factor),
            height=24,
        )
        _field(
            f"{action}_{kind}_{axis}",
            x + 43,
            y,
            max(36, w - 68),
            value,
            action,
            (*prefix, axis),
            scale=1.0 / factor,
            tooltip=f"{label}: {name}",
        )
        _button(
            f"{action}_{kind}_{axis}_plus",
            "+",
            x + w - 22,
            y,
            22,
            action,
            (*prefix, axis, (value + step) / factor),
            height=24,
        )
        y -= 30
    return y - 8


def _slider(
    name: str,
    label: str,
    value: float,
    x: float,
    y: float,
    w: float,
    minimum: float,
    maximum: float,
    scale: float,
    pressure: str,
) -> float:
    paint.text(label, x, y)
    y -= 31
    _field(
        "brush_" + name, x, y, 48, value, "brush", (name,), minimum=minimum, maximum=maximum, scale=scale, tooltip=label
    )
    left: float = x + 55
    width: float = max(30, w - 86)
    paint.rect(left, y + 9, width, 6, (0.12, 0.12, 0.12, 1), 3)
    fraction: float = max(0.0, min(1.0, (value - minimum) / (maximum - minimum)))
    paint.rect(left, y + 9, max(1, width * fraction), 6, (0.39, 0.46, 0.53, 1), 3)
    paint.circle(left + width * fraction, y + 12, 6, (0.70, 0.73, 0.76, 1), True)
    _CURRENT.append(
        Widget(
            "slider_" + name,
            (left, y, width, 24),
            "brush",
            kind="slider",
            value=value,
            minimum=minimum,
            maximum=maximum,
            scale=scale,
            prefix=(name,),
            tooltip=label,
        )
    )
    pressed: bool = bool(getattr(get_state().brush, pressure))
    _button(
        "pressure_" + name,
        "",
        x + w - 25,
        y,
        25,
        "brush",
        (pressure, not pressed),
        icon="pressure",
        selected=pressed,
        tooltip="Stylus pressure controls " + label.lower(),
        height=24,
    )
    return y - 24


def _toolbox(context: Any, w: float, h: float) -> None:
    state: AppState = get_state()
    unit_scale: float = float(context.scene.unit_settings.scale_length)
    paint.rect(0, 0, w, h, paint.PANEL)
    y: float = h - 28 + _SCROLL["toolbox"]
    x: float = 10.0
    width: float = w - 20
    groups: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
        ("Transform", (("move", "Move"), ("rotate", "Rotate"), ("scale", "Scale"))),
        ("Drawing", (("paint", "3D Paint"), ("pipe", "3D Pipe"), ("grease", "Grease Pencil"))),
    )
    title: str
    tools: tuple[tuple[str, str], ...]
    for title, tools in groups:
        contains_active: bool = False
        descriptor: tuple[str, str]
        for descriptor in tools:
            contains_active = contains_active or descriptor[0] == state.tool
        if state.tool != "select" and not contains_active:
            continue
        if state.tool == "select":
            paint.text(title, x, y, 14)
            y -= 47
        else:
            y -= 20
        index: int = 0
        name: str
        label: str
        for name, label in tools:
            if state.tool != "select" and name != state.tool:
                continue
            _button(
                "tool_" + name,
                "",
                x + index * 46,
                y,
                38,
                "tool",
                "select" if state.tool == name else name,
                selected=state.tool == name,
                icon=name,
                tooltip=label,
                height=38,
            )
            index += 1
        y -= 30
    paint.text("Canvas", x, y, 14)
    y -= 47
    options: tuple[tuple[str, str, str], ...] = (
        ("drawing_plane", "plane", "Drawing Plane"),
        ("on_object", "on_object", "Draw on object"),
        ("cursor_active", "cursor", "3D cursor"),
    )
    index: int
    option: str
    icon: str
    label: str
    for index, (option, icon, label) in enumerate(options):
        enabled: bool = bool(getattr(state, option))
        action: str = (
            "plane" if option == "drawing_plane" else "cursor_place" if option == "cursor_active" else "option"
        )
        payload: object = (
            "create" if option == "drawing_plane" else None if option == "cursor_active" else (option, not enabled)
        )
        _button(
            "canvas_" + option,
            "",
            x + index * 46,
            y,
            38,
            action,
            payload,
            icon=icon,
            selected=enabled,
            tooltip=label,
            height=38,
        )
        _checkbox("check_" + option, "", x + index * 46 + 11, y - 30, enabled, "option", (option, not enabled))
    y -= 59
    if state.drawing_plane:
        paint.text("Plane", x, y)
        y -= 31
        plane_items: list[MenuItem] = [("Ground", "plane", "ground")]
        obj: Any
        for obj in context.scene.objects:
            if obj.get("drawing_plane") or obj.get("drawing_app_kind") == "plane":
                plane_items.append((str(obj.name), "plane", "object:" + str(obj.name)))
        plane_items.append(("+ New drawing plane", "plane", "create"))
        _button("plane_picker", state.active_plane + "  v", x, y, width, "popup", plane_items)
        y -= 28
    if state.cursor_active:
        cursor: Any = context.scene.cursor.location
        values: tuple[float, float, float] = (
            float(cursor[0]) * unit_scale,
            float(cursor[1]) * unit_scale,
            float(cursor[2]) * unit_scale,
        )
        y = _xyz(f"3D cursor ({state.units})", values, x, y, width, "cursor", ())
        _button(
            "cursor_place",
            "Place cursor",
            x,
            y,
            width,
            "cursor_place",
            tooltip="Click a point in the viewport to place the 3D cursor",
        )
        y -= 32
        index = 0
        axis: str
        for axis in ("X", "Y", "Z", "PERSPECTIVE"):
            _button(
                "cursor_axis_" + axis,
                "0" if axis == "PERSPECTIVE" else axis,
                x + index * (width / 4),
                y,
                width / 4 - 3,
                "cursor_view",
                axis,
            )
            index += 1
        y -= 30
    if state.tool in ("paint", "pipe", "grease"):
        brush: Brush = state.brush
        factor: float
        step: float
        factor, step = _units()
        y = _slider(
            "radius",
            f"Brush Size ({state.units})",
            brush.radius * factor,
            x,
            y,
            width,
            0.001 * factor,
            0.2 * factor,
            1 / factor,
            "pressure_size",
        )
        if state.tool in ("paint", "grease"):
            y = _slider("hardness", "Hardness", brush.hardness * 100, x, y, width, 0, 100, 0.01, "pressure_hardness")
            y = _slider("opacity", "Opacity", brush.opacity * 100, x, y, width, 0, 100, 0.01, "pressure_opacity")
        paint.text("Shape", x, y)
        y -= 31
        shape_items: list[MenuItem] = []
        shape: str
        for shape in ("circle", "square", "triangle"):
            shape_items.append((shape.title(), "brush", ("shape", shape)))
        _button(
            "brush_shape",
            brush.shape.title() + "  v",
            x,
            y,
            width,
            "popup",
            shape_items,
        )
        y -= 28
        paint.text("Color", x, y)
        y -= 29
        colors: tuple[tuple[float, float, float], ...] = (
            (0.015, 0.015, 0.015),
            (0.8, 0.8, 0.8),
            (0.65, 0.06, 0.04),
            (0.05, 0.32, 0.7),
            (0.06, 0.45, 0.13),
            (0.65, 0.36, 0.015),
        )
        color: tuple[float, float, float]
        for index, color in enumerate(colors):
            sw: float = width / 6 - 3
            paint.rect(x + index * (width / 6), y, sw, 22, (*color, 1), 3)
            _CURRENT.append(
                Widget(
                    "brush_color_" + str(index),
                    (x + index * (width / 6), y, sw, 22),
                    "brush",
                    ("color", color),
                    tooltip="Brush color",
                )
            )
        y -= 34
        paint.text("Method", x, y)
        y -= 30
        _checkbox(
            "axes_spokes", "Axes spokes", x, y, state.axes_spokes, "option", ("axes_spokes", not state.axes_spokes)
        )
        y -= 28
    elif state.tool in ("move", "rotate", "scale"):
        obj = context.view_layer.objects.active
        if obj is None or not obj.select_get():
            paint.text("Select an object", x, y, color=paint.MUTED, max_width=width)
        else:
            kind: str = {"move": "location", "rotate": "rotation", "scale": "scale"}[state.tool]
            vector: Any = obj.rotation_euler if state.tool == "rotate" else getattr(obj, kind)
            conversion: float = (
                180.0 / math.pi if state.tool == "rotate" else unit_scale if state.tool == "move" else 1.0
            )
            values = (float(vector[0]) * conversion, float(vector[1]) * conversion, float(vector[2]) * conversion)
            y = _xyz(
                kind.title() + (f" ({state.units})" if kind == "location" else ""),
                values,
                x,
                y,
                width,
                "transform",
                (kind,),
                kind,
            )
    _LIMITS["toolbox"] = max(0.0, h - y + _SCROLL["toolbox"] - h + 15)
    if _LIMITS["toolbox"] > 0:
        thumb: float = max(25, h * h / (h + _LIMITS["toolbox"]))
        paint.rect(
            w - 4,
            h - thumb - _SCROLL["toolbox"] / max(1, _LIMITS["toolbox"]) * (h - thumb),
            3,
            thumb,
            (0.50, 0.50, 0.50, 0.7),
            1,
        )


def _layers(context: Any, w: float, h: float) -> None:
    from ..tools.layers import LayerRow, layer_rows

    paint.rect(0, 0, w, h, paint.PANEL)
    split: float = h * 0.49
    top: float = h - 30
    paint.rect(5, top, w - 10, 28, paint.BUTTON)
    control: float = 25
    type_x: float = max(90, w - 148)
    paint.text("Name", 28, top + 8)
    paint.text("Type", type_x, top + 8)
    index: int
    label: str
    for index, label in enumerate(("V", "L", "A")):
        paint.text(label, w - 78 + index * control, top + 8)
    rows: list[LayerRow] = layer_rows(context)
    hidden_depth: int | None = None
    visible_rows: list[LayerRow] = []
    row: LayerRow
    for row in rows:
        if hidden_depth is not None and int(row.depth) > hidden_depth:
            continue
        hidden_depth = None
        visible_rows.append(row)
        if str(row.key) in _COLLAPSED:
            hidden_depth = int(row.depth)
    y: float = top - 28 + _SCROLL["layers"]
    for row in visible_rows:
        if split + 32 <= y <= top - 28:
            key: str = str(row.key)
            if row.active:
                paint.rect(5, y, w - 10, 28, (0.12, 0.36, 0.12, 1) if row.kind == "Group" else (0.27, 0.17, 0.23, 1))
            elif _SELECTED_LAYER == key:
                paint.rect(5, y, w - 10, 28, (0.20, 0.29, 0.38, 1))
            left: float = 10 + int(row.depth) * 12
            if row.kind in ("Group", "Stroke Layer"):
                paint.text("+" if key in _COLLAPSED else "-", left, y + 8)
                _CURRENT.append(
                    Widget("expand_" + key, (left - 2, y, 18, 28), "expand", key, tooltip="Expand or collapse")
                )
            paint.text(str(row.name), left + 18, y + 8, max_width=max(20, type_x - left - 22))
            _CURRENT.append(
                Widget(
                    "layer_select_" + key,
                    (left + 16, y, max(20, type_x - left - 18), 28),
                    "layer",
                    ("select", key),
                    tooltip=str(row.name),
                )
            )
            paint.text(str(row.kind), type_x, y + 8, max_width=max(20, w - 87 - type_x))
            operation: str
            for index, operation in enumerate(("visible", "lock", "active")):
                if key == "ground" and operation != "active":
                    continue
                if operation == "active" and row.kind not in ("Plane", "Group"):
                    continue
                value: bool = bool(
                    row.visible if operation == "visible" else row.locked if operation == "lock" else row.active
                )
                color: paint.Color = paint.TEXT if value else (0.43, 0.43, 0.43, 1)
                px: float = w - 85 + index * control
                paint.icon("eye" if operation == "visible" else operation, px, y + 3, 22, color)
                _CURRENT.append(
                    Widget(
                        "layer_" + operation + key,
                        (px, y, 24, 28),
                        "layer",
                        (operation, key),
                        tooltip=operation.title() + " " + str(row.name),
                    )
                )
        y -= 28
    _LIMITS["layers"] = max(0, len(visible_rows) * 28 - (top - split - 60))
    paint.rect(5, split + 3, w - 10, 28, paint.BUTTON)
    _button("layer_add", "+", 10, split + 4, 30, "layer", ("add", ""), tooltip="Add drawing group")
    _button("layer_group", "G", 46, split + 4, 30, "layer", ("group", ""), tooltip="Group selected")
    _button("layer_ungroup", "-", 82, split + 4, 30, "layer", ("ungroup", _SELECTED_LAYER), tooltip="Ungroup selected")
    paint.rect(0, split - 2, w, 3, (0.12, 0.12, 0.12, 1))
    _stage(context, w, split - 7)


def _stage(context: Any, w: float, top: float) -> None:
    paint.rect(5, 5, w - 10, top - 5, (0.13, 0.14, 0.145, 1))
    paint.rect(9, top - 34, w - 18, 28, paint.FIELD, 2)
    search_text: str = _EDIT.text if _EDIT is not None and _EDIT.widget.key == "stage_search" else _SEARCH
    paint.text(
        search_text or "Search", 20, top - 25, color=paint.TEXT if search_text else paint.MUTED, max_width=w - 45
    )
    _CURRENT.append(
        Widget("stage_search", (9, top - 34, w - 18, 28), "search", kind="search", tooltip="Search stage objects")
    )
    paint.rect(8, top - 59, w - 16, 22, (0.21, 0.21, 0.21, 1))
    paint.text("Name (Old to New)", 15, top - 53, 11)
    paint.text("Type", w - 70, top - 53, 11)
    objects: list[tuple[Any, int]] = []

    def visit(obj: Any, depth: int) -> None:
        if not _SEARCH or _SEARCH.lower() in str(obj.name).lower():
            objects.append((obj, depth))
        if "obj:" + str(obj.name) not in _COLLAPSED or _SEARCH:
            child: Any
            for child in obj.children:
                visit(child, depth + 1)

    obj: Any
    for obj in context.scene.objects:
        if obj.parent is None:
            visit(obj, 0)
    y: float = top - 87 + _SCROLL["stage"]
    depth: int
    for obj, depth in objects:
        if 5 <= y <= top - 87:
            name: str = str(obj.name)
            if obj.select_get():
                paint.rect(8, y, w - 16, 27, (0.18, 0.28, 0.37, 1))
            left: float = 12 + depth * 12
            if len(obj.children):
                paint.text("+" if "obj:" + name in _COLLAPSED else "-", left, y + 8)
                _CURRENT.append(Widget("stage_expand_" + name, (left - 2, y, 16, 27), "expand", "obj:" + name))
            paint.icon("move" if obj.type == "EMPTY" else "on_object", left + 15, y + 3, 20, (0.48, 0.60, 0.63, 1))
            paint.text(name, left + 38, y + 8, color=(0.55, 0.65, 0.68, 1), max_width=max(15, w - left - 160))
            _CURRENT.append(
                Widget(
                    "object_" + name,
                    (left + 15, y, max(20, w - left - 140), 27),
                    "object",
                    ("select", name),
                    tooltip=name,
                )
            )
            paint.icon("eye", w - 126, y + 3, 21, paint.MUTED if not obj.hide_get() else (0.35, 0.35, 0.35, 1))
            _CURRENT.append(
                Widget(
                    "object_visible_" + name,
                    (w - 128, y, 26, 27),
                    "object",
                    ("visible", name),
                    tooltip="Toggle object visibility",
                )
            )
            paint.icon("lock", w - 101, y + 3, 21, paint.TEXT if obj.hide_select else (0.35, 0.35, 0.35, 1))
            _CURRENT.append(
                Widget(
                    "object_lock_" + name, (w - 103, y, 26, 27), "object", ("lock", name), tooltip="Toggle object lock"
                )
            )
            paint.text(str(obj.type).title(), w - 72, y + 8, 11, max_width=66)
        y -= 27
    _LIMITS["stage"] = max(0, len(objects) * 27 - (top - 90))


def _viewport(context: Any, w: float, h: float) -> None:
    state: AppState = get_state()
    y: float = h - 34
    _button("frame", "Frame", 8, y, 50, "frame", tooltip="Frame selected objects")
    widths: tuple[float, ...] = (142, 30, 78, 42, 32)
    x: float = max(64, w - sum(widths) - 33)
    if x >= 170:
        _button(
            "projection",
            "Perspective" if context.space_data.region_3d.view_perspective == "PERSP" else "Orthographic",
            64,
            y,
            98,
            "popup",
            [
                ("Perspective", "view_axis", "PERSPECTIVE"),
                ("X axis", "view_axis", "X"),
                ("Y axis", "view_axis", "Y"),
                ("Z axis", "view_axis", "Z"),
            ],
        )
    _button(
        "navigation",
        f"Mouse: {state.navigation.title()} ({state.zoom_speed:g}x)",
        x,
        y,
        widths[0],
        "popup",
        [
            ("Orbit", "navigation", "orbit"),
            ("Fly", "navigation", "fly"),
            ("Zoom speed 0.5x", "zoom_speed", 0.5),
            ("Zoom speed 1x", "zoom_speed", 1.0),
            ("Zoom speed 2x", "zoom_speed", 2.0),
        ],
    )
    x += widths[0] + 5
    _button(
        "ruler", "", x, y, 30, "option", ("ruler", not state.ruler), icon="ruler", selected=state.ruler, tooltip="Ruler"
    )
    x += 35
    _button("units", "Units: " + state.units, x, y, 78, "popup", [("mm", "units", "mm"), ("m", "units", "m")])
    x += 83
    _button(
        "fps_limit",
        str(state.fps_limit) if state.fps_limit else "Max",
        x,
        y,
        42,
        "popup",
        [
            ("30 updates/s", "fps_limit", 30),
            ("60 updates/s", "fps_limit", 60),
            ("120 updates/s", "fps_limit", 120),
            ("Unlimited", "fps_limit", 0),
        ],
        tooltip="Navigation update limit",
    )
    x += 47
    _button(
        "theme",
        "O",
        x,
        y,
        32,
        "popup",
        [("Light mode", "theme", "light"), ("Dark mode", "theme", "dark")],
        tooltip="Lighting",
    )
    if state.status:
        paint.text(state.status, 155, 14, 12, color=(0.7, 0.72, 0.75, 1), max_width=w - 180)


def _popup(w: float, h: float) -> None:
    if _POPUP is None or _POPUP.area != _AREA:
        return
    width: float = 190
    x: float = max(2, min(w - width - 2, _POPUP.x))
    top: float = min(h - 2, _POPUP.top)
    height: float = len(_POPUP.items) * 28 + 8
    top = max(height + 2, top)
    paint.rect(x - 1, top - height - 2, width + 2, height + 3, (0.06, 0.06, 0.06, 1), 4)
    paint.rect(x, top - height, width, height, (0.16, 0.17, 0.18, 1), 4)
    index: int
    item: MenuItem
    for index, item in enumerate(_POPUP.items):
        y: float = top - 32 - index * 28
        hovered: bool = _HOVER[0] == _AREA and _hit((x + 3, y, width - 6, 28), _HOVER[1], _HOVER[2])
        if hovered:
            paint.rect(x + 3, y, width - 6, 28, paint.SELECTED, 2)
        paint.text(item[0], x + 12, y + 8)
        _CURRENT.append(Widget("popup_" + str(index), (x + 3, y, width - 6, 28), item[1], item[2], kind="menu"))


def _draw() -> None:
    global _CURRENT, _AREA, _SCALE
    context: Any = bpy.context
    area: Any = context.area
    name: str = layout.role(area)
    if not name or context.region.type != "WINDOW":
        return
    _AREA = int(area.as_pointer())
    _CURRENT = []
    _SCALE = max(1.0, float(context.preferences.system.ui_scale))
    paint.set_scale(_SCALE)
    w: float = float(context.region.width) / _SCALE
    h: float = float(context.region.height) / _SCALE
    gpu.state.blend_set("ALPHA")
    gpu.state.depth_test_set("NONE")
    gpu.matrix.push()
    gpu.matrix.scale((_SCALE, _SCALE, 1.0))
    try:
        if name == "toolbox":
            layout.remember_width(area, _SCALE)
            _toolbox(context, w, h)
        elif name == "layers":
            layout.remember_width(area, _SCALE)
            _layers(context, w, h)
        else:
            _viewport(context, w, h)
        _popup(w, h)
        _WIDGETS[_AREA] = _CURRENT
        if time.monotonic() - _HOVER_TIME > 0.6 and _HOVER[0] == _AREA:
            widget: Widget | None = _widget_at(_CURRENT, _HOVER[1], _HOVER[2], tooltip=True)
            if widget is not None:
                tw: float = min(w - 12, paint.measure(widget.tooltip, 12) + 14)
                tx: float = min(w - tw - 5, max(5, _HOVER[1] + 10))
                ty: float = max(5, _HOVER[2] - 32)
                paint.rect(tx, ty, tw, 25, (0.07, 0.07, 0.07, 0.98), 3)
                paint.text(widget.tooltip, tx + 7, ty + 7, 12, max_width=tw - 14)
    finally:
        gpu.matrix.pop()
        gpu.state.blend_set("NONE")


def _number(widget: Widget, value: float) -> None:
    if math.isfinite(value):
        bounded: float = max(widget.minimum, min(widget.maximum, value)) * widget.scale
        payload: object = (*widget.prefix, bounded) if widget.prefix else bounded
        _emit(widget.action, payload)


def _commit() -> None:
    global _EDIT, _SEARCH
    if _EDIT is None:
        return
    edit: Edit = _EDIT
    _EDIT = None
    if edit.widget.kind == "search":
        _SEARCH = edit.text
        _SCROLL["stage"] = 0
    else:
        try:
            _number(edit.widget, float(edit.text))
        except ValueError:
            get_state().status = "Enter a valid number"
    layout.redraw()


def _editing(context: Any, event: Any) -> bool:
    global _EDIT, _SEARCH
    if _EDIT is None or event.value != "PRESS":
        return _EDIT is not None and event.type not in (
            "MOUSEMOVE",
            "LEFTMOUSE",
            "RIGHTMOUSE",
            "WHEELUPMOUSE",
            "WHEELDOWNMOUSE",
            "TIMER",
        )
    if event.type in ("RET", "NUMPAD_ENTER", "TAB"):
        _commit()
        return True
    if event.type == "ESC":
        _EDIT = None
        layout.redraw()
        return True
    if event.type in ("LEFTMOUSE", "RIGHTMOUSE", "MIDDLEMOUSE"):
        return False
    edit: Edit = _EDIT
    if event.ctrl and event.type == "A":
        edit.selected = True
    elif event.ctrl and event.type == "C":
        context.window_manager.clipboard = edit.text
    elif event.ctrl and event.type == "V":
        value: str = str(context.window_manager.clipboard)
        edit.text = value if edit.selected else edit.text[: edit.caret] + value + edit.text[edit.caret :]
        edit.caret = len(edit.text) if edit.selected else edit.caret + len(value)
        edit.selected = False
    elif event.type == "BACK_SPACE":
        if edit.selected:
            edit.text = ""
            edit.caret = 0
        elif edit.caret:
            edit.text = edit.text[: edit.caret - 1] + edit.text[edit.caret :]
            edit.caret -= 1
        edit.selected = False
    elif event.type == "DEL":
        edit.text = "" if edit.selected else edit.text[: edit.caret] + edit.text[edit.caret + 1 :]
        edit.selected = False
        edit.caret = min(edit.caret, len(edit.text))
    elif event.type in ("LEFT_ARROW", "RIGHT_ARROW", "HOME", "END"):
        edit.caret = (
            0
            if event.type == "HOME"
            else (
                len(edit.text)
                if event.type == "END"
                else max(0, min(len(edit.text), edit.caret + (-1 if event.type == "LEFT_ARROW" else 1)))
            )
        )
        edit.selected = False
    elif event.unicode:
        character: str = str(event.unicode)
        if edit.widget.kind == "search" or character in "0123456789.+-eE":
            edit.text = character if edit.selected else edit.text[: edit.caret] + character + edit.text[edit.caret :]
            edit.caret = len(character) if edit.selected else edit.caret + len(character)
            edit.selected = False
    if edit.widget.kind == "search":
        _SEARCH = edit.text
        _SCROLL["stage"] = 0
    layout.redraw()
    return True


def handle_event(context: Any, event: Any) -> bool:
    """Consume panel events; leave ordinary viewport events to native tools."""
    global _EDIT, _DRAG, _DRAG_ORIGIN, _POPUP, _HOVER, _HOVER_TIME, _SELECTED_LAYER
    if str(event.type).startswith("TIMER"):
        return False
    if event.type == "ESC" and event.value == "PRESS" and is_capturing():
        cancel_capture()
        return True
    if _EDIT is not None and _editing(context, event):
        return True
    if _EDIT is not None and event.type == "LEFTMOUSE" and event.value == "PRESS":
        _commit()
    scale: float = max(1.0, float(context.preferences.system.ui_scale))
    if _DRAG is not None:
        if event.type == "MOUSEMOVE":
            drag_x: float = event.mouse_x / scale - _DRAG_ORIGIN
            fraction: float = max(0, min(1, (drag_x - _DRAG.bounds[0]) / _DRAG.bounds[2]))
            _number(_DRAG, _DRAG.minimum + fraction * (_DRAG.maximum - _DRAG.minimum))
        elif event.type == "LEFTMOUSE" and event.value == "RELEASE":
            _DRAG = None
        return True
    area: Any = None
    candidate: Any
    for candidate in context.window.screen.areas:
        if (
            candidate.x <= event.mouse_x < candidate.x + candidate.width
            and candidate.y <= event.mouse_y < candidate.y + candidate.height
        ):
            area = candidate
            break
    if area is None or not layout.role(area):
        if _POPUP is not None and event.type == "LEFTMOUSE" and event.value == "PRESS":
            _POPUP = None
            layout.redraw(context)
        return False
    region: Any = None
    for candidate in area.regions:
        if candidate.type == "WINDOW":
            region = candidate
            break
    if region is None:
        return False
    x: float = (event.mouse_x - region.x) / scale
    y: float = (event.mouse_y - region.y) / scale
    pointer: int = int(area.as_pointer())
    if _HOVER != (pointer, x, y):
        _HOVER = (pointer, x, y)
        _HOVER_TIME = time.monotonic()
        layout.redraw(context)
        if not bpy.app.timers.is_registered(_show_tooltip):
            bpy.app.timers.register(_show_tooltip, first_interval=0.65)
    name: str = layout.role(area)
    panel: bool = name in ("toolbox", "layers")
    widgets: list[Widget] = _WIDGETS.get(pointer, [])
    widget: Widget | None = _widget_at(widgets, x, y)
    rename_requested: bool = (event.type == "LEFTMOUSE" and event.value == "DOUBLE_CLICK") or (
        event.type == "F2" and event.value == "PRESS"
    )
    if rename_requested and widget is not None:
        if widget.action in ("layer", "object") and isinstance(widget.payload, tuple) and widget.payload[0] == "select":
            key: str = str(widget.payload[1])
            if key != "ground":
                cancel_capture()
                menus.rename(widget.action, key, widget.tooltip)
                return True
    if event.type in ("WHEELUPMOUSE", "WHEELDOWNMOUSE") and panel:
        section: str = "toolbox" if name == "toolbox" else "layers" if y > region.height / scale * 0.49 else "stage"
        _SCROLL[section] = max(
            0, min(_LIMITS.get(section, 0), _SCROLL[section] + (-42 if event.type == "WHEELUPMOUSE" else 42))
        )
        layout.redraw(context)
        return True
    if event.type == "LEFTMOUSE" and event.value == "PRESS":
        if _EDIT is not None:
            _commit()
        if _POPUP is not None:
            _POPUP = None
            if widget is None or widget.kind != "menu":
                layout.redraw(context)
                return True
        if widget is not None:
            if widget.kind in ("field", "search"):
                initial: str = _SEARCH if widget.kind == "search" else f"{widget.value:.9g}"
                _EDIT = Edit(widget, initial, len(initial))
            elif widget.kind == "slider":
                _DRAG = widget
                _DRAG_ORIGIN = region.x / scale
                _number(
                    widget,
                    widget.minimum
                    + max(0, min(1, (x - widget.bounds[0]) / widget.bounds[2])) * (widget.maximum - widget.minimum),
                )
            elif widget.action == "popup":
                items: list[MenuItem] = list(widget.payload) if isinstance(widget.payload, list) else []
                _POPUP = Popup(pointer, widget.bounds[0], widget.bounds[1] - 2, items)
            elif widget.action == "expand":
                key: str = str(widget.payload)
                _COLLAPSED.symmetric_difference_update({key})
            else:
                if widget.action == "layer" and isinstance(widget.payload, tuple) and widget.payload[0] == "select":
                    _SELECTED_LAYER = str(widget.payload[1])
                _emit(widget.action, widget.payload)
            layout.redraw(context)
            return True
    return panel or widget is not None or (name == "viewport" and y >= region.height / scale - 38)


def is_capturing() -> bool:
    """Keep an active field or drag alive when the pointer leaves its area."""
    return _EDIT is not None or _DRAG is not None or _POPUP is not None


def cancel_capture() -> None:
    """Discard unfinished text and release pointer capture without committing."""
    global _EDIT, _DRAG, _POPUP
    _EDIT = None
    _DRAG = None
    _POPUP = None
    layout.redraw()


def invalidate_images() -> None:
    """Drop image proxies replaced by Blender undo without rebuilding widgets."""
    paint.invalidate_images()
    layout.redraw()


def install(dispatch: Callable[[str, object], None]) -> None:
    global _DISPATCH
    if _HANDLES:
        return
    _DISPATCH = dispatch
    menus.install(dispatch)
    space: Any
    for space in (bpy.types.SpaceImageEditor, bpy.types.SpaceView3D):
        handle: Any = space.draw_handler_add(_draw, (), "WINDOW", "POST_PIXEL")
        _HANDLES.append((space, handle))


def uninstall() -> None:
    global _DISPATCH, _EDIT, _DRAG, _POPUP
    space: Any
    handle: Any
    for space, handle in _HANDLES:
        space.draw_handler_remove(handle, "WINDOW")
    _HANDLES.clear()
    _WIDGETS.clear()
    _EDIT = None
    _DRAG = None
    _POPUP = None
    _DISPATCH = None
    if bpy.app.timers.is_registered(_show_tooltip):
        bpy.app.timers.unregister(_show_tooltip)
    menus.uninstall()
    paint.release()
