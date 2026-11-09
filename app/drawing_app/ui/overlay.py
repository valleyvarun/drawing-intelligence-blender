"""GPU workspace panels with mouse, keyboard, slider and text-field interaction."""

from __future__ import annotations

import colorsys
import math
import time
from collections.abc import Callable
from typing import Any, cast

from ..core.blender import bpy, gpu
from ..core.state import (
    EXTRUDER_LIMITS,
    NOZZLE_LIMITS,
    PRINTER_LIMITS,
    TUBE_LIMITS,
    WALK_LIMITS,
    AppState,
    Brush,
    Vec3,
    color_hex,
    get_state,
    linear_to_srgb,
    parse_hex,
    save_preferences,
    srgb_to_linear,
)
from . import chat, layout, menus, paint
from .widgets import Edit, MenuItem, Popup, PopupSlider, Rect, Widget, hit

__all__: list[str] = ["Edit", "MenuItem", "Popup", "Rect", "Widget"]

_DISPATCH: Callable[[str, object], None] | None = None
_HANDLES: list[tuple[Any, Any]] = []
_WIDGETS: dict[int, list[Widget]] = {}
_SCROLL: dict[str, float] = {"toolbox": 0.0, "layers": 0.0}
_LIMITS: dict[str, float] = {}
_COLLAPSED: set[str] = set()
_SELECTED_LAYER: str = ""
# The Claude Code chat below the layers list, and the smallest Clayers and chat heights in UI units.
_CHAT: chat.ChatPanel = chat.ChatPanel()
PANEL_MINIMUMS: tuple[float, float] = (96.0, 180.0)
_EDIT: Edit | None = None
_DRAG: Widget | None = None
_DRAG_ORIGIN: float = 0.0
_DRAG_ORIGIN_Y: float = 0.0
# Height of the panel whose divider is being dragged.
_DRAG_EXTENT: float = 0.0
# Colour being dragged in a picker; Grease Pencil applies it on release.
_DRAG_COLOR: Vec3 | None = None
# Picker cache key -> hue, saturation, value shown by that picker.
_HSV: dict[str, tuple[float, float, float]] = {}
_POPUP: Popup | None = None
_HOVER: tuple[int, float, float] = (0, 0.0, 0.0)
_HOVER_TIME: float = 0.0
_CURRENT: list[Widget] = []
_AREA: int = 0
_SCALE: float = 1.0
_TOOLBOX_SELECTION: tuple[str, str] | None = None


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
    return hit(bounds, x, y)


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
    dropdown: bool = False,
) -> None:
    hovered: bool = _HOVER[0] == _AREA and _hit((x, y, w, height), _HOVER[1], _HOVER[2])
    background: paint.Color = paint.SELECTED if selected else ((0.22, 0.22, 0.22, 1) if hovered else paint.BUTTON)
    paint.rect(x, y, w, height, (0.10, 0.10, 0.10, 1), 5)
    paint.rect(x + 1, y + 1, w - 2, height - 2, background, 4)
    if icon:
        paint.icon(icon, x + (w - height + 6) / 2, y + 3, height - 6, (0.90, 0.90, 0.90, 1))
    elif dropdown:
        # Square arrow box at the right end, split from the label by a dark divider.
        box: float = height
        box_x: float = x + w - box
        shade: paint.Color = (0.28, 0.28, 0.28, 1) if hovered else (0.21, 0.21, 0.21, 1)
        paint.rect(box_x, y + 1, box - 1, height - 2, shade, 4)
        paint.rect(box_x, y + 1, 4, height - 2, shade)
        paint.rect(box_x, y + 1, 1, height - 2, (0.10, 0.10, 0.10, 1))
        # Downward-pointing triangle centred in the box.
        center_x: float = box_x + box * 0.5 + 0.5
        center_y: float = y + height * 0.5
        paint.polygon(
            [(center_x - 4.5, center_y + 2.5), (center_x + 4.5, center_y + 2.5), (center_x, center_y - 3.0)],
            paint.TEXT,
        )
        # Label centred in the space left of the box.
        label_width: float = paint.measure(label)
        space: float = w - box
        paint.text(label, x + max(6, (space - label_width) * 0.5), y + (height - 13) * 0.5 + 2, max_width=space - 12)
    else:
        tw: float = paint.measure(label)
        paint.text(label, x + max(4, (w - tw) * 0.5), y + (height - 13) * 0.5 + 2, max_width=w - 8)
    _CURRENT.append(Widget(key, (x, y, w, height), action, payload, tooltip=tooltip or label))
    if _POPUP is not None and _POPUP.key == key and _POPUP.area == _AREA and isinstance(payload, list):
        # The open dropdown shows the slider values as they change.
        _POPUP.sliders = _popup_sliders(cast(list[object], payload))


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
    text: str | None = None,
    compact: bool = False,
) -> None:
    """Numeric field; with `text` it is a hex field whose text is edited instead of `value`.

    Compact fields centre their text with less padding and shrink it before truncating.
    """
    focused: bool = _EDIT is not None and _EDIT.widget.key == key
    paint.rect(x, y, w, 24, paint.SELECTED if focused else (0.11, 0.11, 0.11, 1), 3)
    paint.rect(x + 1, y + 1, w - 2, 22, paint.FIELD, 2)
    shown: str = text if text is not None else f"{value:.6g}"
    label: str = _EDIT.text if focused and _EDIT is not None else shown
    padding: float = 2.0 if compact else 6.0
    room: float = w - 2 * padding
    size: float = 13.0
    while compact and size > 10.0 and paint.measure(label, size) > room:
        size -= 1.0
    text_x: float = x + padding
    if compact:
        text_x = x + max(padding, (w - paint.measure(label, size)) / 2)
    paint.text(label, text_x, y + 6 + (13.0 - size) / 2, size, max_width=room)
    if focused and _EDIT is not None:
        if _EDIT.selected:
            highlight: float = min(x + w - text_x - 2, paint.measure(label, size) + 4)
            paint.rect(text_x - 2, y + 3, highlight, 18, (0.25, 0.45, 0.65, 0.45))
        else:
            cursor_x: float = min(x + w - 5, text_x + paint.measure(label[: _EDIT.caret], size))
            paint.line([(cursor_x, y + 4), (cursor_x, y + 20)])
    _CURRENT.append(
        Widget(
            key,
            (x, y, w, 24),
            action,
            text,
            kind="field" if text is None else "hex",
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


def slider_fraction(value: float, minimum: float, maximum: float, log: bool) -> float:
    """Where `value` sits along a slider's track, from 0 to 1."""
    if log and minimum > 0.0 and maximum > minimum:
        return max(0.0, min(1.0, math.log(max(value, minimum) / minimum) / math.log(maximum / minimum)))
    return max(0.0, min(1.0, (value - minimum) / max(maximum - minimum, 1e-12)))


def slider_value(widget: Widget, fraction: float) -> float:
    """The value at a point along a slider's track, from 0 to 1.

    A log slider's value keeps three significant figures, such as 0.0123, so its field can show it.
    """
    share: float = max(0.0, min(1.0, fraction))
    if widget.log and widget.minimum > 0.0 and widget.maximum > widget.minimum:
        value: float = float(f"{widget.minimum * (widget.maximum / widget.minimum) ** share:.3g}")
        return max(widget.minimum, min(widget.maximum, value))
    return widget.minimum + share * (widget.maximum - widget.minimum)


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
    *,
    action: str = "brush",
    pressed: bool | None = None,
    compact: bool = False,
    log: bool = False,
) -> float:
    """Numeric field, slider, and optional stylus-pressure toggle; empty `pressure` hides the toggle.

    A `log` slider spaces its values by ratio, for ranges such as millimetres to metres.
    """
    paint.text(label, x, y)
    y -= 31
    _field(
        "brush_" + name,
        x,
        y,
        48,
        value,
        action,
        (name,),
        minimum=minimum,
        maximum=maximum,
        scale=scale,
        tooltip=label,
        # Log values such as 0.0123 shrink to fit rather than lose digits.
        compact=compact or log,
    )
    left: float = x + 55
    width: float = max(30, w - (86 if pressure else 58))
    paint.rect(left, y + 9, width, 6, (0.12, 0.12, 0.12, 1), 3)
    fraction: float = slider_fraction(value, minimum, maximum, log)
    paint.rect(left, y + 9, max(1, width * fraction), 6, (0.39, 0.46, 0.53, 1), 3)
    paint.circle(left + width * fraction, y + 12, 6, (0.70, 0.73, 0.76, 1), True)
    _CURRENT.append(
        Widget(
            "slider_" + name,
            (left, y, width, 24),
            action,
            kind="slider",
            value=value,
            minimum=minimum,
            maximum=maximum,
            scale=scale,
            prefix=(name,),
            tooltip=label,
            log=log,
        )
    )
    if pressure:
        enabled: bool = pressed if pressed is not None else bool(getattr(get_state().brush, pressure))
        _button(
            "pressure_" + name,
            "",
            x + w - 25,
            y,
            25,
            action,
            (pressure, not enabled),
            icon="pressure",
            selected=enabled,
            tooltip="Stylus pressure controls " + label.lower(),
            height=24,
        )
    return y - 24


SWATCHES: tuple[tuple[float, float, float], ...] = (
    (0.015, 0.015, 0.015),
    (0.8, 0.8, 0.8),
    (0.65, 0.06, 0.04),
    (0.05, 0.32, 0.7),
    (0.06, 0.45, 0.13),
    (0.65, 0.36, 0.015),
)


def _same_color(a: Vec3, b: Vec3) -> bool:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]), abs(a[2] - b[2])) < 1e-4


def _swatches(x: float, y: float, width: float, colors: tuple[Vec3, ...], current: Vec3, tooltip: str) -> float:
    """Preset colours for the active tool; the current one is outlined."""
    count: int = len(colors)
    index: int
    color: Vec3
    for index, color in enumerate(colors):
        sw: float = width / count - 3
        left: float = x + index * (width / count)
        if _same_color(color, current):
            paint.rect(left - 2, y - 2, sw + 4, 26, paint.SELECTED, 4)
        paint.rect(left, y, sw, 22, (*linear_to_srgb(color), 1), 3)
        _CURRENT.append(
            Widget(f"tool_color_preset_{index}", (left, y, sw, 22), "color", ("tool", "rgb", color), tooltip=tooltip)
        )
    return y - 34


def _picker_hsv(cache: str, srgb: Vec3) -> tuple[float, float, float]:
    """The picker's hue, saturation, and value; kept while they are undefined (greys and black)."""
    cached: tuple[float, float, float] | None = _HSV.get(cache)
    if cached is not None:
        shown: tuple[float, float, float] = colorsys.hsv_to_rgb(*cached)
        if max(abs(shown[0] - srgb[0]), abs(shown[1] - srgb[1]), abs(shown[2] - srgb[2])) < 0.5 / 255:
            return cached
    hue: float
    saturation: float
    value: float
    hue, saturation, value = colorsys.rgb_to_hsv(*srgb)
    if cached is not None and value <= 0.0:
        hue, saturation = cached[0], cached[1]
    elif cached is not None and saturation <= 0.0:
        hue = cached[0]
    _HSV[cache] = (hue, saturation, value)
    return _HSV[cache]


def _color_picker(cache: str, target: str, color: Vec3, x: float, y: float, w: float, tooltip: str) -> float:
    """Saturation/value square, hue bar, colour chip, hex field, and R/G/B fields. Returns the next row's y."""
    srgb: Vec3 = linear_to_srgb(color)
    hue: float
    saturation: float
    value: float
    hue, saturation, value = _picker_hsv(cache, srgb)

    # --- 1) SATURATION/VALUE SQUARE AND HUE BAR ---

    bar: float = 12.0
    size: float = max(60.0, w - bar - 14.0)
    height: float = round(min(size * 0.75, 150.0))
    top: float = y + 24.0
    bottom: float = top - height
    pure: tuple[float, float, float] = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
    # white-to-hue across, then black rising from the bottom
    paint.gradient(
        [(x, bottom), (x + size, bottom), (x + size, top), (x, top)],
        [(1, 1, 1, 1), (*pure, 1), (*pure, 1), (1, 1, 1, 1)],
    )
    paint.gradient(
        [(x, bottom), (x + size, bottom), (x + size, top), (x, top)],
        [(0, 0, 0, 1), (0, 0, 0, 1), (0, 0, 0, 0), (0, 0, 0, 0)],
    )
    marker_x: float = x + saturation * size
    marker_y: float = bottom + value * height
    paint.circle(marker_x, marker_y, 6.0, (0.05, 0.05, 0.05, 1))
    paint.circle(marker_x, marker_y, 5.0, (1, 1, 1, 1))
    # dragging reads the cache key from the payload and the target from the prefix
    square: Rect = (x, bottom, size, height)
    _CURRENT.append(Widget(f"{cache}_sv", square, "color", cache, kind="color_sv", prefix=(target,), tooltip=tooltip))
    bar_x: float = x + size + 8.0
    step: int
    for step in range(6):
        low: tuple[float, float, float] = colorsys.hsv_to_rgb(step / 6.0, 1.0, 1.0)
        high: tuple[float, float, float] = colorsys.hsv_to_rgb((step + 1) / 6.0, 1.0, 1.0)
        y0: float = bottom + height * step / 6.0
        y1: float = bottom + height * (step + 1) / 6.0
        paint.gradient(
            [(bar_x, y0), (bar_x + bar, y0), (bar_x + bar, y1), (bar_x, y1)],
            [(*low, 1), (*low, 1), (*high, 1), (*high, 1)],
        )
    # arrow beside the bar at the current hue
    hue_y: float = bottom + hue * height
    paint.polygon([(bar_x + bar + 1, hue_y), (bar_x + bar + 7, hue_y + 5), (bar_x + bar + 7, hue_y - 5)], paint.TEXT)
    paint.rect(bar_x, hue_y - 0.75, bar, 1.5, (1, 1, 1, 0.9))
    strip: Rect = (bar_x, bottom, bar + 8, height)
    _CURRENT.append(Widget(f"{cache}_hue", strip, "color", cache, kind="color_hue", prefix=(target,), tooltip=tooltip))

    # --- 2) COLOUR CHIP AND HEX FIELD ---

    y = bottom - 32
    paint.rect(x, y, 24, 24, (0.10, 0.10, 0.10, 1), 4)
    paint.rect(x + 2, y + 2, 20, 20, (*srgb, 1), 3)
    paint.text("#", x + 32, y + 6, color=paint.MUTED)
    hex_text: str = color_hex(color)
    _field(f"{cache}_hex", x + 44, y, w - 44, 0.0, "color", (target,), tooltip="Hex color (sRGB)", text=hex_text)

    # --- 3) R, G, AND B FIELDS (0-255) ---

    y -= 30
    gap: float = 6.0
    # Labels take only their letter's width so narrow panels leave room for "255".
    label_width: float = 0.0
    letter: str
    for letter in "RGB":
        label_width = max(label_width, paint.measure(letter) + 3.0)
    width: float = max(1.0, (w - 3 * label_width - 2 * gap) / 3)
    channel: int
    for channel in range(3):
        left: float = x + channel * (label_width + width + gap)
        paint.text("RGB"[channel], left, y + 6, color=paint.MUTED)
        _field(
            f"{cache}_channel_{channel}",
            left + label_width,
            y,
            width,
            round(srgb[channel] * 255),
            "color",
            (target, channel),
            minimum=0,
            maximum=255,
            tooltip="RGB"[channel] + " (0-255)",
            compact=True,
        )
    return y - 38


def _pencil(context: Any, x: float, y: float, width: float) -> float:
    """Blender's Grease Pencil Draw-mode tools, the active brush asset, materials, and placement."""
    from ..tools import pencil

    view: pencil.PencilView | None = pencil.view(context)
    label: str
    paint.text("Grease Pencil settings", x, y)
    y -= 31
    if view is None:
        paint.text(get_state().status or "Draw mode is unavailable", x, y, color=paint.MUTED, max_width=width)
        return y - 28
    if pencil.is_shape(view.tool):
        # One Shape tool; choosing a shape switches Blender's primitive tool.
        paint.text("Shape", x, y)
        y -= 31
        shapes: list[MenuItem] = []
        shape_label: str = ""
        code: str
        for code, label in pencil.SHAPES:
            shapes.append((label, "pencil", ("tool", code)))
            if code == view.tool:
                shape_label = label
        _button("pencil_shape", shape_label, x, y, width, "popup", shapes, tooltip="Shape to draw", dropdown=True)
        y -= 32
    if view.brushes and view.tool != "builtin.brush":
        paint.text("Brush", x, y)
        y -= 31
        items: list[MenuItem] = []
        name: str
        for name in view.brushes:
            items.append((name, "pencil", ("brush", name)))
        _button("pencil_brush", view.brush, x, y, width, "popup", items, tooltip="Blender brush asset", dropdown=True)
        y -= 32
    if view.brush:
        if view.tool != "builtin_brush.Fill":
            factor: float = _units()[0] if view.size_scene else 1.0
            unit: str = get_state().units if view.size_scene else "px"
            y = _slider(
                "size",
                f"Radius ({unit})",
                view.size * factor,
                x,
                y,
                width,
                view.size_range[0] * factor,
                view.size_range[1] * factor,
                1.0 / factor,
                "pressure_size",
                action="pencil",
                pressed=view.pressure_size,
            )
            y = _slider(
                "strength",
                "Strength",
                view.strength * 100,
                x,
                y,
                width,
                0,
                100,
                0.01,
                "pressure_strength",
                action="pencil",
                pressed=view.pressure_strength,
            )
        if view.tool in pencil.DRAW_TOOLS:
            y = _slider("hardness", "Hardness", view.hardness * 100, x, y, width, 0, 100, 0.01, "", action="pencil")
    if view.tool == "builtin_brush.Fill":
        y = _slider(
            "fill_opacity",
            "Opacity",
            get_state().pencil.fill_opacity * 100,
            x,
            y,
            width,
            0,
            100,
            0.01,
            "",
            action="pencil",
        )
    if view.material and (view.tool in pencil.DRAW_TOOLS or view.tool == "builtin_brush.Fill"):
        # One colour shared by every Grease Pencil tool; Fill uses it as a fill.
        tool_color: Vec3 = get_state().brushes["grease"].color
        paint.text("Color", x, y)
        y -= 31
        y = _color_picker("tool_grease", "tool", tool_color, x, y, width, "Color for Grease Pencil tools only")
        presets: list[Vec3] = []
        preset: tuple[str, Vec3]
        for preset in pencil.COLORS:
            presets.append(preset[1])
        y = _swatches(x, y, width, tuple(presets), tool_color, "Preset color for Grease Pencil tools")
    if view.tool in pencil.DRAW_TOOLS:
        _checkbox(
            "pencil_stabilize", "Stabilize stroke", x, y, view.stabilize, "pencil", ("stabilize", not view.stabilize)
        )
        y -= 30
    paint.text("Stroke placement", x, y)
    y -= 31
    options: list[MenuItem] = []
    code: str
    for code, label in pencil.PLACEMENTS:
        options.append((label, "pencil", ("placement", code)))
    _button("pencil_placement", pencil.placement_text(view.placement), x, y, width, "popup", options, dropdown=True)
    y -= 32
    paint.text("Drawing plane", x, y)
    y -= 31
    planes: list[MenuItem] = []
    for code, label in pencil.PLANES:
        planes.append((label, "pencil", ("plane", code)))
    _button("pencil_plane", pencil.plane_text(view.plane), x, y, width, "popup", planes, dropdown=True)
    y -= 30
    if view.canvas:
        paint.text("Canvas: " + view.canvas, x, y, 11, color=paint.MUTED, max_width=width)
        y -= 22
    return y


def _printer(state: AppState, x: float, y: float, width: float) -> float:
    """3D Printer settings: nozzle and particle size, stickiness, flow, particles per drop, drop height, and colour.

    Returns the next row's y. Both sizes span millimetres to metres, so their sliders are logarithmic.
    """
    brush: Brush = state.brushes["printer"]
    factor: float = _units()[0]
    y = _slider(
        "radius",
        f"Nozzle size ({state.units})",
        brush.radius * factor,
        x,
        y,
        width,
        NOZZLE_LIMITS[0] * factor,
        NOZZLE_LIMITS[1] * factor,
        1 / factor,
        "",
        log=True,
    )
    # Larger particles add more height where they land; none is wider than the nozzle.
    size: tuple[float, float] = PRINTER_LIMITS["size"]
    y = _slider(
        "size",
        f"Particle size ({state.units})",
        state.printer.particle(brush.radius) * factor,
        x,
        y,
        width,
        size[0] * factor,
        min(size[1], 2.0 * brush.radius) * factor,
        1 / factor,
        "",
        action="printer",
        log=True,
    )
    # 0% slumps like sand; 100% stands almost upright and builds up over the surface below.
    y = _slider("sticky", "Sticky", state.printer.sticky * 100, x, y, width, 0, 100, 0.01, "", action="printer")
    rate: tuple[float, float] = PRINTER_LIMITS["rate"]
    y = _slider(
        "rate", "Flow (particles/s)", state.printer.rate, x, y, width, rate[0], rate[1], 1.0, "", action="printer"
    )
    per_drop: tuple[float, float] = PRINTER_LIMITS["per_drop"]
    y = _slider(
        "per_drop",
        "Particles per drop",
        float(state.printer.per_drop),
        x,
        y,
        width,
        per_drop[0],
        per_drop[1],
        1.0,
        "",
        action="printer",
    )
    drop: tuple[float, float] = PRINTER_LIMITS["drop"]
    y = _slider(
        "drop",
        f"Drop height ({state.units})",
        state.printer.drop * factor,
        x,
        y,
        width,
        drop[0] * factor,
        drop[1] * factor,
        1 / factor,
        "",
        action="printer",
    )
    # This tool's colour only; a new colour starts a new print object.
    paint.text("Color", x, y)
    y -= 31
    y = _color_picker("tool_printer", "tool", brush.color, x, y, width, "Color for this tool only")
    return _swatches(x, y, width, SWATCHES, brush.color, "Preset color for this tool")


def _extruder(state: AppState, x: float, y: float, width: float) -> float:
    """3D Extruder settings: tube size, flow, drop height, and colour. Returns the next row's y.

    Tube size spans millimetres to metres and flow centimetres to metres per second, so both are logarithmic.
    """
    brush: Brush = state.brushes["extruder"]
    factor: float = _units()[0]
    y = _slider(
        "radius",
        f"Tube size ({state.units})",
        brush.radius * factor,
        x,
        y,
        width,
        TUBE_LIMITS[0] * factor,
        TUBE_LIMITS[1] * factor,
        1 / factor,
        "",
        log=True,
    )
    # Moving slower than the flow coils the spare tube; holding still piles it up.
    flow: tuple[float, float] = EXTRUDER_LIMITS["flow"]
    y = _slider(
        "flow",
        f"Flow ({state.units}/s)",
        state.extruder.flow * factor,
        x,
        y,
        width,
        flow[0] * factor,
        flow[1] * factor,
        1 / factor,
        "",
        action="extruder",
        log=True,
    )
    drop: tuple[float, float] = EXTRUDER_LIMITS["drop"]
    y = _slider(
        "drop",
        f"Drop height ({state.units})",
        state.extruder.drop * factor,
        x,
        y,
        width,
        drop[0] * factor,
        drop[1] * factor,
        1 / factor,
        "",
        action="extruder",
    )
    # This tool's colour only.
    paint.text("Color", x, y)
    y -= 31
    y = _color_picker("tool_extruder", "tool", brush.color, x, y, width, "Color for this tool only")
    return _swatches(x, y, width, SWATCHES, brush.color, "Preset color for this tool")


def _toolbox(context: Any, w: float, h: float) -> None:
    global _TOOLBOX_SELECTION
    from ..tools import pencil

    state: AppState = get_state()
    selecting: bool = state.tool == "select"
    selection: tuple[str, str] = (state.tool, state.pencil.tool if state.tool == "grease" else "")
    if selection != _TOOLBOX_SELECTION:
        _SCROLL["toolbox"] = 0.0
        _TOOLBOX_SELECTION = selection
    unit_scale: float = float(context.scene.unit_settings.scale_length)
    paint.rect(0, 0, w, h, paint.PANEL)
    y: float = h - 28 + _SCROLL["toolbox"]
    x: float = 10.0
    width: float = w - 20
    groups: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
        ("Transform", (("move", "Move"), ("rotate", "Rotate"), ("scale", "Scale"))),
        (
            "Drawing",
            (
                ("paint", "3D Paint"),
                ("pipe", "3D Pipe"),
                ("printer", "3D Printer"),
                ("extruder", "3D Extruder"),
                *pencil.toolbox_tools(),
            ),
        ),
        ("Erase", pencil.ERASING_TOOLS),
    )
    active_tool: str = state.pencil.tool if state.tool == "grease" else state.tool
    columns: int = max(1, min(4, int((width + 8) // 46)))
    title: str
    tools: tuple[tuple[str, str], ...]
    for title, tools in groups:
        if not selecting and active_tool not in dict(tools):
            continue
        if selecting:
            paint.text(title, x, y, 14)
            y -= 47
        else:
            y -= 20
        index: int = 0
        name: str
        label: str
        for name, label in tools:
            if not selecting and name != active_tool:
                continue
            native: bool = name.startswith("builtin")
            selected: bool = name == active_tool
            _button(
                "tool_grease" if name == "builtin.brush" else "pencil_tool_" + name if native else "tool_" + name,
                "",
                x + (index % columns) * 46,
                y - (index // columns) * 46,
                38,
                "pencil" if native and not selected else "tool",
                "select" if selected else ("tool", name) if native else name,
                selected=selected,
                icon="grease" if name == "builtin.brush" else "shape" if pencil.is_shape(name) else name,
                tooltip=label,
                height=38,
            )
            index += 1
        y -= max(0, (index + columns - 1) // columns - 1) * 46 + 30
    # Tool Box colour, shown before a tool is chosen: setting it recolours every drawing tool.
    # A chosen tool shows only its own colour, in its settings below.
    if selecting:
        paint.text("Color", x, y, 14)
        y -= 31
        y = _color_picker("toolbox", "toolbox", state.color, x, y, width, "Color for all drawing tools")
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
        _button("plane_picker", state.active_plane, x, y, width, "popup", plane_items, dropdown=True)
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
    if state.tool == "grease":
        y = _pencil(context, x, y, width)
    elif state.tool == "printer":
        y = _printer(state, x, y, width)
    elif state.tool == "extruder":
        y = _extruder(state, x, y, width)
    elif state.tool in ("paint", "pipe"):
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
        if state.tool == "paint":
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
            brush.shape.title(),
            x,
            y,
            width,
            "popup",
            shape_items,
            dropdown=True,
        )
        y -= 28
        # This tool's colour only.
        paint.text("Color", x, y)
        y -= 31
        y = _color_picker("tool_" + state.tool, "tool", brush.color, x, y, width, "Color for this tool only")
        y = _swatches(x, y, width, SWATCHES, brush.color, "Preset color for this tool")
        paint.text("Method", x, y)
        y -= 30
        _checkbox(
            "axes_spokes", "Axes spokes", x, y, state.axes_spokes, "option", ("axes_spokes", not state.axes_spokes)
        )
        y -= 28
    elif state.tool in ("move", "rotate", "scale"):
        obj = context.view_layer.objects.active
        if pencil.editing(context):
            paint.text("Selected strokes", x, y, 14)
            y -= 24
            paint.text("Use the gizmo or G / R / S", x, y, 11, max_width=width)
            y -= 24
            paint.text("Esc: finish transforming", x, y, 11, max_width=width)
        elif obj is None or not obj.select_get():
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


def _selection_outline(x: float, y: float, w: float, h: float) -> None:
    paint.line(
        [(x, y), (x + w, y), (x + w, y + h), (x, y + h)],
        color=(1.0, 0.45, 0.05, 1.0),
        width=1.5,
        closed=True,
    )


def _layers(context: Any, w: float, h: float) -> None:
    from ..tools.layers import LayerRow, layer_rows

    paint.rect(0, 0, w, h, paint.PANEL)
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
    # Clayers keeps its share of the panel's height at the top; the Claude Code chat gets the rest.
    split: float = _chat_height(h)
    y: float = top - 28 + _SCROLL["layers"]
    for row in visible_rows:
        if split + 32 <= y <= top - 28:
            key: str = str(row.key)
            if row.active:
                paint.rect(5, y, w - 10, 28, (0.12, 0.36, 0.12, 1) if row.kind == "Group" else (0.27, 0.17, 0.23, 1))
            if row.selected:
                _selection_outline(6, y + 1, w - 12, 26)
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
                if key.startswith("stroke:"):
                    continue
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
    hover: tuple[float, float] | None = (_HOVER[1], _HOVER[2]) if _HOVER[0] == _AREA else None
    _CURRENT.extend(_CHAT.draw(5, 5, w - 10, split - 12, _EDIT, hover))
    # The divider between Clayers and the chat drags up and down, like the native editor borders.
    divider: Rect = (0, split - 5, w, 8)
    active: bool = (_DRAG is not None and _DRAG.kind == "divider") or (hover is not None and _hit(divider, *hover))
    paint.rect(0, split - 2, w, 3, (0.30, 0.42, 0.58, 1) if active else (0.12, 0.12, 0.12, 1))
    _CURRENT.append(Widget("panel_divider", divider, kind="divider"))


def _chat_height(h: float) -> float:
    """Height left for the chat once Clayers takes its saved share of the panel."""
    layers: float = max(PANEL_MINIMUMS[0], min(h - PANEL_MINIMUMS[1], h * get_state().layers_share))
    return max(0.0, h - layers)


def _resize_panels(y: float) -> None:
    """Move the Clayers/chat divider to `y` (UI units above the panel's bottom edge)."""
    height: float = max(1.0, _DRAG_EXTENT)
    layers: float = max(PANEL_MINIMUMS[0], min(height - PANEL_MINIMUMS[1], height - y))
    get_state().layers_share = max(0.1, min(0.9, layers / height))
    layout.redraw()


def _viewport(context: Any, w: float, h: float) -> None:
    from ..tools.pipe_fill import message

    state: AppState = get_state()
    y: float = h - 34
    widths: tuple[float, ...] = (142, 30, 78, 42, 32)
    x: float = max(8, w - sum(widths) - 33)
    if x >= 114:
        _button(
            "projection",
            "Perspective" if context.space_data.region_3d.view_perspective == "PERSP" else "Orthographic",
            8,
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
    factor: float = _units()[0]
    unit: str = state.units
    navigation: list[object] = [
        ("Orbit", "navigation", "orbit"),
        ("Fly", "navigation", "fly"),
        ("Walk", "navigation", "walk"),
        PopupSlider(
            "walk_speed",
            f"Walking speed ({unit}/s)",
            "walk",
            state.walk_speed * factor,
            WALK_LIMITS["walk_speed"][0] * factor,
            WALK_LIMITS["walk_speed"][1] * factor,
            1.0 / factor,
        ),
        PopupSlider(
            "eye_level",
            f"Eye level ({unit})",
            "walk",
            state.eye_level * factor,
            WALK_LIMITS["eye_level"][0] * factor,
            WALK_LIMITS["eye_level"][1] * factor,
            1.0 / factor,
        ),
    ]
    _button(
        "navigation",
        f"Mouse: {state.navigation.title()}",
        x,
        y,
        widths[0],
        "popup",
        navigation,
        tooltip="Right-drag navigation (N switches); walking speed and eye level",
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
    status: str = message() or state.status
    if status:
        paint.text(status, 155, 14, 12, color=(0.7, 0.72, 0.75, 1), max_width=w - 180)


POPUP_SLIDER_ROW: float = 58.0


def _popup_items(payload: list[object]) -> list[MenuItem]:
    items: list[MenuItem] = []
    entry: object
    for entry in payload:
        if isinstance(entry, tuple):
            items.append(cast(MenuItem, entry))
    return items


def _popup_sliders(payload: list[object]) -> list[PopupSlider]:
    sliders: list[PopupSlider] = []
    entry: object
    for entry in payload:
        if isinstance(entry, PopupSlider):
            sliders.append(entry)
    return sliders


def _popup_height(items: int, sliders: int) -> float:
    return items * 28 + 8 + (6 + sliders * POPUP_SLIDER_ROW if sliders else 0)


def _popup_keys() -> set[str]:
    """Field and slider widgets inside the open dropdown, which keep it open when used."""
    keys: set[str] = set()
    slider: PopupSlider
    for slider in _POPUP.sliders if _POPUP is not None else []:
        keys.update(("brush_" + slider.name, "slider_" + slider.name))
    return keys


def _popup(w: float, h: float) -> None:
    if _POPUP is None or _POPUP.area != _AREA:
        return
    # Wide enough for the longest item (such as an email address), within the panel.
    width: float = 230 if _POPUP.sliders else 190
    entry: MenuItem
    for entry in _POPUP.items:
        width = max(width, paint.measure(entry[0]) + 30)
    width = min(width, max(120.0, w - 4))
    x: float = max(2, min(w - width - 2, _POPUP.x))
    top: float = min(h - 2, _POPUP.top)
    height: float = _popup_height(len(_POPUP.items), len(_POPUP.sliders))
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
        paint.text(item[0], x + 12, y + 8, max_width=width - 20)
        _CURRENT.append(Widget("popup_" + str(index), (x + 3, y, width - 6, 28), item[1], item[2], kind="menu"))
    if not _POPUP.sliders:
        return
    # Sliders below a separator, each with a label, a typed field, and a drag track.
    row_top: float = top - 4 - len(_POPUP.items) * 28 - 6
    paint.rect(x + 8, row_top + 3, width - 16, 1, (0.30, 0.30, 0.30, 1))
    slider: PopupSlider
    for slider in _POPUP.sliders:
        _slider(
            slider.name,
            slider.label,
            slider.value,
            x + 10,
            row_top - 18,
            width - 20,
            slider.minimum,
            slider.maximum,
            slider.scale,
            "",
            action=slider.action,
            compact=True,
        )
        row_top -= POPUP_SLIDER_ROW


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
        # Tooltips would cover an open menu.
        if time.monotonic() - _HOVER_TIME > 0.6 and _HOVER[0] == _AREA and _POPUP is None:
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


def _send_chat() -> None:
    from .. import agent

    message: str = _CHAT.take_draft().strip()
    # Files and context can go on their own.
    if message or agent.session().attachments:
        _emit("agent", ("send", message))


def _open_popup(area: int, bounds: Rect, entries: list[object], key: str) -> None:
    """Open a dropdown below a button, or above it when there is no room below (as at the panel's bottom)."""
    global _POPUP
    items: list[MenuItem] = _popup_items(entries)
    sliders: list[PopupSlider] = _popup_sliders(entries)
    menu_height: float = _popup_height(len(items), len(sliders))
    below: float = bounds[1] - 2
    above: float = bounds[1] + bounds[3] + 2 + menu_height
    _POPUP = Popup(area, bounds[0], below if below - menu_height >= 2 else above, items, key, sliders)


def _chat_command(payload: object, area: int) -> None:
    """Chat actions handled by the panel itself: tool details, example prompts, sending, and the context menu."""
    if not isinstance(payload, tuple) or not payload:
        return
    command: object = payload[0]
    argument: object = payload[1] if len(payload) > 1 else None
    if command == "toggle" and isinstance(argument, int):
        _CHAT.toggle(argument)
    elif command == "example" and isinstance(argument, str):
        _CHAT.draft = argument
    elif command == "send":
        _send_chat()
    elif command == "context_menu":
        # The + menu's Add context opens its own menu in the same place.
        _open_popup(area, _CHAT.add_bounds, list(_CHAT.context_menu()), "chat_context")


def _commit() -> None:
    """Leave the field being edited and apply it; the chat box keeps its text as an unsent draft."""
    global _EDIT
    if _EDIT is None:
        return
    edit: Edit = _EDIT
    _EDIT = None
    if edit.widget.kind == "chat":
        _CHAT.draft = edit.text
    elif edit.widget.kind == "hex":
        color: Vec3 | None = parse_hex(edit.text)
        if color is None:
            get_state().status = "Enter a hex color such as FF8000"
        else:
            _emit(edit.widget.action, (*edit.widget.prefix, "rgb", color))
    else:
        try:
            _number(edit.widget, float(edit.text))
        except ValueError:
            get_state().status = "Enter a valid number"
    layout.redraw()


def _insert(edit: Edit, value: str) -> None:
    edit.text = value if edit.selected else edit.text[: edit.caret] + value + edit.text[edit.caret :]
    edit.caret = len(value) if edit.selected else edit.caret + len(value)
    edit.selected = False


def _editing(context: Any, event: Any) -> bool:
    global _EDIT
    if _EDIT is None or event.value != "PRESS":
        return _EDIT is not None and event.type not in (
            "MOUSEMOVE",
            "LEFTMOUSE",
            "RIGHTMOUSE",
            "WHEELUPMOUSE",
            "WHEELDOWNMOUSE",
            "TIMER",
        )
    chatting: bool = _EDIT.widget.kind == "chat"
    if chatting and event.type in ("RET", "NUMPAD_ENTER"):
        # Enter sends and keeps the box focused; Shift+Enter starts a new line.
        if event.shift:
            _insert(_EDIT, "\n")
            _CHAT.draft = _EDIT.text
        else:
            _CHAT.draft = _EDIT.text
            _send_chat()
            _EDIT.text = ""
            _EDIT.caret = 0
            _EDIT.selected = False
        layout.redraw()
        return True
    if chatting and event.type in ("TAB", "UP_ARROW", "DOWN_ARROW"):
        return True
    if event.type in ("RET", "NUMPAD_ENTER", "TAB"):
        _commit()
        return True
    if event.type == "ESC":
        if chatting:
            _CHAT.draft = _EDIT.text
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
        _insert(edit, str(context.window_manager.clipboard))
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
        allowed: str = "#0123456789abcdefABCDEF" if edit.widget.kind == "hex" else "0123456789.+-eE"
        if chatting or character in allowed:
            _insert(edit, character)
    if chatting:
        _CHAT.draft = edit.text
    layout.redraw()
    return True


def _pick(widget: Widget, x: float, y: float) -> None:
    """Colour from a point on a picker's square or hue bar; a drag previews until release."""
    global _DRAG_COLOR
    left: float
    bottom: float
    width: float
    height: float
    left, bottom, width, height = widget.bounds
    across: float = max(0.0, min(1.0, (x - left) / max(width, 1e-6)))
    up: float = max(0.0, min(1.0, (y - bottom) / max(height, 1e-6)))
    cache: str = str(widget.payload)
    hue: float
    saturation: float
    value: float
    hue, saturation, value = _HSV.get(cache, (0.0, 0.0, 0.0))
    if widget.kind == "color_sv":
        saturation, value = across, up
    else:
        hue = up
    _HSV[cache] = (hue, saturation, value)
    red: float
    green: float
    blue: float
    red, green, blue = colorsys.hsv_to_rgb(hue, saturation, value)
    _DRAG_COLOR = srgb_to_linear((red, green, blue))
    _emit(widget.action, (*widget.prefix, "preview", _DRAG_COLOR))


def handle_event(context: Any, event: Any) -> bool:
    """Consume panel events; leave ordinary viewport events to native tools."""
    global _EDIT, _DRAG, _DRAG_ORIGIN, _DRAG_ORIGIN_Y, _DRAG_EXTENT, _DRAG_COLOR, _POPUP, _HOVER, _HOVER_TIME
    global _SELECTED_LAYER
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
    if _DRAG is not None and _DRAG.kind == "divider":
        if event.type == "MOUSEMOVE":
            context.window.cursor_set("MOVE_Y")
            _resize_panels(event.mouse_y / scale - _DRAG_ORIGIN_Y)
        elif event.type == "LEFTMOUSE" and event.value == "RELEASE":
            _DRAG = None
            save_preferences()
            layout.redraw()
        return True
    if _DRAG is not None:
        picking: bool = _DRAG.kind in ("color_sv", "color_hue")
        if event.type == "MOUSEMOVE":
            drag_x: float = event.mouse_x / scale - _DRAG_ORIGIN
            if picking:
                _pick(_DRAG, drag_x, event.mouse_y / scale - _DRAG_ORIGIN_Y)
            else:
                _number(_DRAG, slider_value(_DRAG, (drag_x - _DRAG.bounds[0]) / _DRAG.bounds[2]))
        elif event.type == "LEFTMOUSE" and event.value == "RELEASE":
            # The final colour is applied everywhere, including Grease Pencil materials.
            if picking and _DRAG_COLOR is not None:
                _emit(_DRAG.action, (*_DRAG.prefix, "rgb", _DRAG_COLOR))
            _DRAG = None
            _DRAG_COLOR = None
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
    if event.type == "MOUSEMOVE" and widget is not None and widget.kind in ("divider", "chat"):
        # Resize and text cursors, as over Blender's own borders and text fields.
        context.window.cursor_set("MOVE_Y" if widget.kind == "divider" else "TEXT")
    rename_requested: bool = (event.type == "LEFTMOUSE" and event.value == "DOUBLE_CLICK") or (
        event.type == "F2" and event.value == "PRESS"
    )
    if rename_requested and widget is not None:
        if widget.action in ("layer", "object") and isinstance(widget.payload, tuple) and widget.payload[0] == "select":
            key: str = str(widget.payload[1])
            if key != "ground" and not key.startswith("stroke:"):
                cancel_capture()
                menus.rename(widget.action, key, widget.tooltip)
                return True
    if event.type in ("WHEELUPMOUSE", "WHEELDOWNMOUSE") and panel:
        if name == "layers" and _hit(_CHAT.bounds, x, y):
            # The chat scrolls back through older messages.
            _CHAT.scroll(42 if event.type == "WHEELUPMOUSE" else -42)
        else:
            section: str = "toolbox" if name == "toolbox" else "layers"
            _SCROLL[section] = max(
                0, min(_LIMITS.get(section, 0), _SCROLL[section] + (-42 if event.type == "WHEELUPMOUSE" else 42))
            )
        layout.redraw(context)
        return True
    if event.type == "LEFTMOUSE" and event.value == "PRESS":
        if _EDIT is not None:
            _commit()
        # A dropdown's own sliders and fields keep it open; any other click closes it.
        if _POPUP is not None and (widget is None or widget.key not in _popup_keys()):
            _POPUP = None
            if widget is None or widget.kind != "menu":
                layout.redraw(context)
                return True
        if widget is not None:
            if widget.kind in ("field", "hex"):
                initial: str = str(widget.payload) if widget.kind == "hex" else f"{widget.value:.9g}"
                _EDIT = Edit(widget, initial, len(initial))
            elif widget.kind == "chat":
                _EDIT = Edit(widget, _CHAT.draft, len(_CHAT.draft), selected=False)
            elif widget.action == "chat":
                _chat_command(widget.payload, pointer)
            elif widget.kind == "divider":
                _DRAG = widget
                _DRAG_ORIGIN_Y = region.y / scale
                _DRAG_EXTENT = region.height / scale
                context.window.cursor_set("MOVE_Y")
            elif widget.kind in ("color_sv", "color_hue"):
                _DRAG = widget
                _DRAG_ORIGIN = region.x / scale
                _DRAG_ORIGIN_Y = region.y / scale
                _pick(widget, x, y)
            elif widget.kind == "slider":
                _DRAG = widget
                _DRAG_ORIGIN = region.x / scale
                _number(widget, slider_value(widget, (x - widget.bounds[0]) / widget.bounds[2]))
            elif widget.action == "popup":
                entries: list[object] = cast(list[object], widget.payload) if isinstance(widget.payload, list) else []
                _open_popup(pointer, widget.bounds, entries, widget.key)
            elif widget.action == "expand":
                key: str = str(widget.payload)
                _COLLAPSED.symmetric_difference_update({key})
            else:
                payload: object = widget.payload
                if widget.action == "layer" and isinstance(widget.payload, tuple) and widget.payload[0] == "select":
                    _SELECTED_LAYER = str(widget.payload[1])
                    if _SELECTED_LAYER.startswith("stroke:") and (event.shift or event.ctrl):
                        payload = ("select_extend", _SELECTED_LAYER)
                _emit(widget.action, payload)
            layout.redraw(context)
            return True
    return panel or widget is not None or (name == "viewport" and y >= region.height / scale - 38)


def is_capturing() -> bool:
    """Keep an active field or drag alive when the pointer leaves its area."""
    return _EDIT is not None or _DRAG is not None or _POPUP is not None


def cancel_capture() -> None:
    """Discard unfinished field text and release pointer capture; a chat message stays as a draft."""
    global _EDIT, _DRAG, _DRAG_COLOR, _POPUP
    if _EDIT is not None and _EDIT.widget.kind == "chat":
        _CHAT.draft = _EDIT.text
    _EDIT = None
    _DRAG = None
    _DRAG_COLOR = None
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
    global _DISPATCH, _EDIT, _DRAG, _DRAG_COLOR, _POPUP
    space: Any
    handle: Any
    for space, handle in _HANDLES:
        space.draw_handler_remove(handle, "WINDOW")
    _HANDLES.clear()
    _WIDGETS.clear()
    _HSV.clear()
    _EDIT = None
    _DRAG = None
    _DRAG_COLOR = None
    _POPUP = None
    _DISPATCH = None
    if bpy.app.timers.is_registered(_show_tooltip):
        bpy.app.timers.unregister(_show_tooltip)
    menus.uninstall()
    paint.release()
