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
Tool: TypeAlias = Literal["select", "move", "rotate", "scale", "paint", "pipe", "printer", "grease"]
TOOLS: tuple[str, ...] = ("select", "move", "rotate", "scale", "paint", "pipe", "printer", "grease")
# Tools with their own brush and colour.
DRAWING_TOOLS: tuple[str, ...] = ("paint", "pipe", "printer", "grease")
ROOT: Path = Path(__file__).resolve().parents[3]
CONFIG: Path = (ROOT / os.environ.get("DRAWING_APP_CONFIG", "runtime/config")).resolve()
if not CONFIG.is_relative_to(ROOT):
    CONFIG = ROOT / "runtime/config"


# Drawing colours are stored linear, like Blender materials; pickers show sRGB.
def srgb_to_linear(color: Vec3) -> Vec3:
    channels: list[float] = []
    value: float
    for value in color:
        value = max(0.0, min(1.0, value))
        channels.append(value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4)
    return channels[0], channels[1], channels[2]


def linear_to_srgb(color: Vec3) -> Vec3:
    channels: list[float] = []
    value: float
    for value in color:
        value = max(0.0, min(1.0, value))
        channels.append(value * 12.92 if value <= 0.0031308 else 1.055 * value ** (1.0 / 2.4) - 0.055)
    return channels[0], channels[1], channels[2]


def color_hex(color: Vec3) -> str:
    """Six-digit sRGB hex of a linear colour, without '#'."""
    text: str = ""
    value: float
    for value in linear_to_srgb(color):
        text += f"{round(value * 255):02X}"
    return text


def parse_hex(text: str) -> Vec3 | None:
    """Linear colour from 'RRGGBB' or 'RGB' sRGB hex (an optional '#' is ignored)."""
    digits: str = text.strip().removeprefix("#")
    character: str
    if len(digits) == 3:
        doubled: str = ""
        for character in digits:
            doubled += character * 2
        digits = doubled
    if len(digits) != 6:
        return None
    for character in digits:
        if character not in "0123456789abcdefABCDEF":
            return None
    channels: list[float] = []
    index: int
    for index in (0, 2, 4):
        channels.append(int(digits[index : index + 2], 16) / 255.0)
    return srgb_to_linear((channels[0], channels[1], channels[2]))


def clean_color(value: object) -> Vec3 | None:
    """A finite three-channel colour clamped to 0..1, or None."""
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    channels: list[float] = []
    component: object
    for component in value:
        if not isinstance(component, (float, int)) or isinstance(component, bool) or not math.isfinite(component):
            return None
        channels.append(max(0.0, min(1.0, float(component))))
    return channels[0], channels[1], channels[2]


NAVIGATION_MODES: tuple[str, ...] = ("orbit", "fly", "walk")
# Walk settings' ranges in metres: walk_speed per second, eye_level above the surface below.
WALK_LIMITS: dict[str, tuple[float, float]] = {"walk_speed": (0.1, 20.0), "eye_level": (0.1, 10.0)}

# Purple #70197C (sRGB 112, 25, 124): every drawing tool's colour when the app opens.
DEFAULT_COLOR: Vec3 = srgb_to_linear((0x70 / 255, 0x19 / 255, 0x7C / 255))


@dataclass
class Brush:
    radius: float = 0.02
    hardness: float = 1.0
    opacity: float = 1.0
    shape: str = "circle"
    color: Vec3 = DEFAULT_COLOR
    pressure_size: bool = False
    pressure_hardness: bool = False
    pressure_opacity: bool = False


def _brushes() -> dict[str, Brush]:
    # The 3D Printer's radius is its nozzle's: particles fall within that circle.
    return {"paint": Brush(), "pipe": Brush(), "printer": Brush(radius=0.03), "grease": Brush()}


# 3D Printer settings' ranges: particles per second and per drop, particle size and drop height in metres.
# A particle is never wider than the nozzle, so its size is also capped at the nozzle's diameter.
PRINTER_LIMITS: dict[str, tuple[float, float]] = {
    "rate": (1.0, 2000.0),
    "per_drop": (1.0, 500.0),
    "size": (0.001, 20.0),
    "drop": (0.0, 2.0),
}
# The nozzle's radius in metres; the largest pours over a 20 m circle.
NOZZLE_LIMITS: tuple[float, float] = (0.002, 10.0)


@dataclass
class Printer:
    """3D Printer pouring: particles per second, how many fall at once, and the release height above the surface.

    `rate` replaced an earlier `flow` share; saved preferences that only have `flow` start from these defaults.
    """

    rate: float = 200.0
    per_drop: int = 1
    # Each particle is a block this wide; it adds about its own height where it lands.
    size: float = 0.02
    drop: float = 0.15

    def particle(self, radius: float) -> float:
        """The particle size in use with a nozzle of this radius: never wider than the nozzle."""
        return min(self.size, 2.0 * radius)


@dataclass
class Pencil:
    """Native Grease Pencil choices; radius and strength stay on Blender's brush assets."""

    tool: str = "builtin.brush"
    # The Shape tool's last choice: Blender's box or circle primitive.
    shape: str = "builtin.box"
    brush: str = "Pencil"
    eraser: str = "Eraser Hard"
    # Label of the last applied colour material: a preset name or "#RRGGBB".
    material: str = "#" + color_hex(DEFAULT_COLOR)
    fill_opacity: float = 1.0
    # Blender's defaults: strokes at the object origin, on a plane facing the view.
    placement: str = "ORIGIN"
    plane: str = "VIEW"


@dataclass
class AppState:
    tool: Tool = "select"
    theme: str = "light"
    units: str = "m"
    # Right-drag navigation: "orbit", "fly", or "walk".
    navigation: str = "orbit"
    zoom_speed: float = 1.0
    # Walk navigation, in metres: speed per second and eye height above the surface below.
    walk_speed: float = 1.5
    eye_level: float = 1.6
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
    # Tool Box colour; setting it recolours every drawing tool.
    color: Vec3 = DEFAULT_COLOR
    brushes: dict[str, Brush] = field(default_factory=_brushes)
    pencil: Pencil = field(default_factory=Pencil)
    printer: Printer = field(default_factory=Printer)
    status: str = ""
    left_width: int = 220
    right_width: int = 280
    # Window position and size in Blender's coordinates (x, y, width, height); maximized by default.
    window_geometry: tuple[int, int, int, int] | None = None
    window_maximized: bool = True
    # Claude Code executable chosen in the chat panel; empty finds it automatically.
    agent_cli: str = ""
    # Chat model alias, permission mode, and effort; empty values follow Claude Code's own settings.
    agent_model: str = ""
    agent_mode: str = ""
    agent_effort: str = ""
    # Clayers' share of the right panel's height; the chat below gets the rest.
    layers_share: float = 1.0 / 3.0

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
            "navigation": NAVIGATION_MODES,
        }
        for name in choices:
            value: object = data.get(name)
            if isinstance(value, str) and value in choices[name]:
                setattr(_STATE, name, value)
        speed: object = data.get("zoom_speed")
        if isinstance(speed, (float, int)) and math.isfinite(speed):
            _STATE.zoom_speed = max(0.1, min(10.0, float(speed)))
        limits: tuple[float, float]
        for name, limits in WALK_LIMITS.items():
            value = data.get(name)
            if isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value):
                setattr(_STATE, name, max(limits[0], min(limits[1], float(value))))
        for name in ("ruler", "hud"):
            value = data.get(name)
            if isinstance(value, bool):
                setattr(_STATE, name, value)
        for name in ("left_width", "right_width"):
            value = data.get(name)
            if isinstance(value, int) and not isinstance(value, bool):
                default: int = 220 if name == "left_width" else 280
                setattr(_STATE, name, value if 29 <= value <= 100000 else default)
        limit: object = data.get("fps_limit")
        if isinstance(limit, int) and limit in (0, 30, 60, 120):
            _STATE.fps_limit = limit
        cli: object = data.get("agent_cli")
        if isinstance(cli, str) and len(cli) < 1024:
            _STATE.agent_cli = cli
        for name in ("agent_model", "agent_mode", "agent_effort"):
            value = data.get(name)
            if isinstance(value, str) and len(value) <= 64 and value.replace("-", "").replace("_", "").isalnum():
                setattr(_STATE, name, value)
        share: object = data.get("layers_share")
        if isinstance(share, (float, int)) and not isinstance(share, bool) and 0.1 <= share <= 0.9:
            _STATE.layers_share = float(share)
        # Colours start at DEFAULT_COLOR on every launch; saved drawings restore their own.
        restore_brushes(data.get("brushes"), colors=False)
        restore_pencil(data.get("pencil"))
        restore_printer(data.get("printer"))
        restore_window(data.get("window_geometry"), data.get("window_maximized"))
    except (OSError, ValueError, TypeError):
        _STATE.status = "Saved preferences could not be read; using defaults."


def restore_window(geometry: object, maximized: object) -> None:
    if isinstance(maximized, bool):
        _STATE.window_maximized = maximized
    if not isinstance(geometry, (list, tuple)) or len(geometry) != 4:
        return
    values: list[int] = []
    item: object
    for item in geometry:
        if not isinstance(item, int) or isinstance(item, bool) or abs(item) > 100000:
            return
        values.append(item)
    if values[2] >= 240 and values[3] >= 240:
        _STATE.window_geometry = (values[0], values[1], values[2], values[3])


def restore_printer(payload: object) -> None:
    if not isinstance(payload, dict):
        return
    data: dict[str, object] = cast(dict[str, object], payload)
    name: str
    limits: tuple[float, float]
    for name, limits in PRINTER_LIMITS.items():
        value: object = data.get(name)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            bounded: float = max(limits[0], min(limits[1], float(value)))
            setattr(_STATE.printer, name, round(bounded) if name == "per_drop" else bounded)


def restore_pencil(payload: object) -> None:
    if not isinstance(payload, dict):
        return
    data: dict[str, object] = cast(dict[str, object], payload)
    pencil: Pencil = _STATE.pencil
    name: str
    for name in ("tool", "brush", "eraser", "material"):
        value: object = data.get(name)
        if isinstance(value, str) and 0 < len(value) <= 64:
            setattr(pencil, name, value)
    shape: object = data.get("shape")
    if shape in ("builtin.box", "builtin.circle"):
        pencil.shape = str(shape)
    opacity: object = data.get("fill_opacity")
    if isinstance(opacity, (int, float)) and math.isfinite(opacity):
        pencil.fill_opacity = max(0.0, min(1.0, float(opacity)))
    choice: object = data.get("placement")
    if choice in ("ORIGIN", "CURSOR", "SURFACE", "STROKE"):
        pencil.placement = str(choice)
    choice = data.get("plane")
    if choice in ("VIEW", "AXIS_X", "AXIS_Y", "AXIS_Z", "CURSOR"):
        pencil.plane = str(choice)


def restore_brushes(payload: object, *, colors: bool = True) -> None:
    """Brush settings from preferences or a drawing; `colors=False` keeps the current colours."""
    if not isinstance(payload, dict):
        return
    brushes: dict[str, object] = cast(dict[str, object], payload)
    kind: str
    for kind in DRAWING_TOOLS:
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
        color: Vec3 | None = clean_color(data.get("color")) if colors else None
        if color is not None:
            brush.color = color
