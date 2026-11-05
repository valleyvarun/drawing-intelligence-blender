"""Small GPU/BLF drawing helpers for the custom workspace controls."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, TypeAlias

from ..core.blender import batch_for_shader, blf, bpy, gpu

Color: TypeAlias = tuple[float, float, float, float]
Point: TypeAlias = tuple[float, float]
TEXT: Color = (0.80, 0.80, 0.80, 1.0)
MUTED: Color = (0.63, 0.63, 0.63, 1.0)
PANEL: Color = (0.265, 0.265, 0.265, 1.0)
FIELD: Color = (0.125, 0.135, 0.14, 1.0)
BUTTON: Color = (0.16, 0.16, 0.16, 1.0)
SELECTED: Color = (0.16, 0.37, 0.55, 1.0)
_SHADER: Any = None
_IMAGES: dict[str, Any] = {}
_FONT: int = -1
_FONT_PATH: str = ""
_TEXT_SCALE: float = 1.0


def set_scale(scale: float) -> None:
    global _TEXT_SCALE
    _TEXT_SCALE = max(1.0, scale)


def _font() -> int:
    global _FONT, _FONT_PATH
    if _FONT < 0:
        folder: str = f"{bpy.app.version[0]}.{bpy.app.version[1]}"
        path: Path = Path(bpy.app.binary_path).parent / folder / "datafiles/fonts/Inter.woff2"
        _FONT_PATH = str(path) if path.is_file() else ""
        _FONT = int(blf.load(_FONT_PATH)) if _FONT_PATH else 0
    return _FONT


def measure(label: str, size: float = 13.0) -> float:
    font: int = _font()
    blf.size(font, size * _TEXT_SCALE)
    return float(blf.dimensions(font, label)[0]) / _TEXT_SCALE


def _shader() -> Any:
    global _SHADER
    if _SHADER is None:
        _SHADER = gpu.shader.from_builtin("UNIFORM_COLOR")
    return _SHADER


def polygon(points: list[Point], color: Color) -> None:
    if len(points) < 3:
        return
    indices: list[tuple[int, int, int]] = []
    index: int
    for index in range(1, len(points) - 1):
        indices.append((0, index, index + 1))
    shader: Any = _shader()
    batch: Any = batch_for_shader(shader, "TRIS", {"pos": points}, indices=indices)
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def rect(x: float, y: float, w: float, h: float, color: Color, radius: float = 0.0) -> None:
    if radius <= 0.0:
        polygon([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], color)
        return
    r: float = min(radius, w * 0.5, h * 0.5)
    points: list[Point] = []
    cx: float
    cy: float
    start: float
    for cx, cy, start in (
        (x + w - r, y + r, -90.0),
        (x + w - r, y + h - r, 0.0),
        (x + r, y + h - r, 90.0),
        (x + r, y + r, 180.0),
    ):
        index: int
        for index in range(6):
            angle: float = math.radians(start + index * 18.0)
            points.append((cx + math.cos(angle) * r, cy + math.sin(angle) * r))
    polygon(points, color)


def line(points: list[Point], color: Color = TEXT, width: float = 1.3, closed: bool = False) -> None:
    if len(points) < 2:
        return
    shader: Any = _shader()
    vertices: list[Point] = points + [points[0]] if closed else points
    batch: Any = batch_for_shader(shader, "LINE_STRIP", {"pos": vertices})
    gpu.state.line_width_set(width)
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)
    gpu.state.line_width_set(1.0)


def circle(x: float, y: float, radius: float, color: Color = TEXT, fill: bool = False) -> None:
    points: list[Point] = []
    index: int
    for index in range(33):
        angle: float = index * math.tau / 32
        points.append((x + radius * math.cos(angle), y + radius * math.sin(angle)))
    if fill:
        polygon(points, color)
    else:
        line(points, color)


def text(label: str, x: float, y: float, size: float = 13.0, color: Color = TEXT, max_width: float = 0.0) -> None:
    font: int = _font()
    blf.size(font, size * _TEXT_SCALE)
    blf.disable(font, blf.SHADOW)
    blf.disable(font, blf.ROTATION)
    blf.disable(font, blf.CLIPPING)
    value: str = label
    if max_width > 0.0 and blf.dimensions(font, value)[0] / _TEXT_SCALE > max_width:
        while value and blf.dimensions(font, value + "...")[0] / _TEXT_SCALE > max_width:
            value = value[:-1]
        value += "..."
    blf.position(font, x * _TEXT_SCALE, y * _TEXT_SCALE, 0)
    blf.color(font, *color)
    gpu.matrix.push()
    try:
        gpu.matrix.scale((1.0 / _TEXT_SCALE, 1.0 / _TEXT_SCALE, 1.0))
        blf.draw(font, value)
    finally:
        gpu.matrix.pop()


def image(name: str, x: float, y: float, size: float) -> bool:
    path: Path = Path(__file__).resolve().parents[1] / "assets" / name
    if not path.is_file():
        return False
    cached: Any = _IMAGES.get(name)
    if cached is not None:
        try:
            if bpy.data.images.get(cached.name) != cached:
                _IMAGES.pop(name, None)
        except ReferenceError:
            _IMAGES.pop(name, None)
    if name not in _IMAGES:
        _IMAGES[name] = bpy.data.images.load(str(path), check_existing=True)
    texture: Any = gpu.texture.from_image(_IMAGES[name])
    shader: Any = gpu.shader.from_builtin("IMAGE")
    batch: Any = batch_for_shader(
        shader,
        "TRI_FAN",
        {
            "pos": [(x, y), (x + size, y), (x + size, y + size), (x, y + size)],
            "texCoord": [(0, 0), (1, 0), (1, 1), (0, 1)],
        },
    )
    shader.bind()
    shader.uniform_sampler("image", texture)
    batch.draw(shader)
    return True


def icon(name: str, x: float, y: float, size: float, color: Color = TEXT) -> None:
    cx: float = x + size * 0.5
    cy: float = y + size * 0.5
    r: float = size * 0.30
    if name in ("paint", "pipe", "plane") and image(
        {"paint": "brush_oval.png", "pipe": "brush_sphere.png", "plane": "drawing_plane.png"}[name], x, y, size
    ):
        return
    if name == "move":
        line([(cx - r, cy), (cx + r, cy)], color)
        line([(cx, cy - r), (cx, cy + r)], color)
        line([(cx - r * 0.6, cy + r * 0.6), (cx, cy + r), (cx + r * 0.6, cy + r * 0.6)], color)
        line([(cx - r * 0.6, cy - r * 0.6), (cx, cy - r), (cx + r * 0.6, cy - r * 0.6)], color)
        line([(cx - r * 0.6, cy - r * 0.6), (cx - r, cy), (cx - r * 0.6, cy + r * 0.6)], color)
        line([(cx + r * 0.6, cy - r * 0.6), (cx + r, cy), (cx + r * 0.6, cy + r * 0.6)], color)
    elif name == "rotate":
        circle(cx, cy, r, color)
        line([(cx + r, cy + r), (cx + r, cy), (cx, cy)], color, 2)
    elif name == "scale":
        line([(cx - r, cy - r), (cx + r, cy + r)], color)
        line([(cx - r, cy), (cx - r, cy - r), (cx, cy - r)], color)
        line([(cx, cy + r), (cx + r, cy + r), (cx + r, cy)], color)
    elif name in ("cursor", "cursor_active"):
        circle(cx, cy, r * 0.75, color)
        line([(cx - r * 1.3, cy), (cx + r * 1.3, cy)], (0.75, 0.35, 0.35, 1))
        line([(cx, cy - r * 1.3), (cx, cy + r * 1.3)], (0.4, 0.7, 0.45, 1))
        circle(cx, cy, 2, color, True)
    elif name in ("eye", "visible"):
        line(
            [
                (cx - r, cy),
                (cx - r * 0.5, cy + r * 0.55),
                (cx + r * 0.5, cy + r * 0.55),
                (cx + r, cy),
                (cx + r * 0.5, cy - r * 0.55),
                (cx - r * 0.5, cy - r * 0.55),
            ],
            color,
            closed=True,
        )
        circle(cx, cy, r * 0.4, color)
    elif name in ("lock", "locked"):
        rect(cx - r * 0.65, cy - r * 0.7, r * 1.3, r, color, 1)
        line(
            [
                (cx - r * 0.5, cy + r * 0.2),
                (cx - r * 0.5, cy + r * 0.8),
                (cx + r * 0.5, cy + r * 0.8),
                (cx + r * 0.5, cy + r * 0.2),
            ],
            color,
        )
    elif name == "active":
        line([(cx - r, cy - r), (cx + r, cy - r), (cx + r, cy + r), (cx - r, cy + r)], color, closed=True)
        rect(cx - r * 0.45, cy - r * 0.45, r * 0.9, r * 0.9, color)
    elif name == "ruler":
        line([(cx - r, cy - r * 0.6), (cx + r, cy + r * 0.6)], color, 3)
        line([(cx - r * 0.5, cy), (cx - r * 0.3, cy - r * 0.35)], color)
        line([(cx, cy + r * 0.3), (cx + r * 0.2, cy - r * 0.05)], color)
    elif name == "on_object":
        polygon(
            [
                (cx - r, cy - r * 0.5),
                (cx, cy - r),
                (cx + r, cy - r * 0.5),
                (cx + r, cy + r * 0.6),
                (cx, cy + r),
                (cx - r, cy + r * 0.6),
            ],
            (0.22, 0.65, 0.8, 1),
        )
        line([(cx - r, cy + r * 0.5), (cx, cy), (cx + r, cy + r * 0.5)], color)
        line([(cx, cy), (cx, cy - r)], color)
    else:
        text(
            {"grease": "GP", "group": "+", "ungroup": "-", "pressure": "P", "add": "+"}.get(name, name[:1].upper()),
            x + size * 0.25,
            y + size * 0.25,
            size * 0.48,
            color,
        )


def invalidate_images() -> None:
    _IMAGES.clear()


def release() -> None:
    global _SHADER, _FONT, _FONT_PATH
    _SHADER = None
    _IMAGES.clear()
    if _FONT_PATH:
        blf.unload(_FONT_PATH)
    _FONT = -1
    _FONT_PATH = ""
