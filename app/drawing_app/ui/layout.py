"""Three real Blender editor areas with application-owned panel drawing."""

from __future__ import annotations

import time
from typing import Any

from ..core.blender import bpy
from ..core.state import get_state, save_preferences

_ROLES: dict[int, str] = {}
_PENDING: bool = False
_WIDTH_BASE: dict[str, tuple[int, float]] = {}
_WIDTH_SAVE_AT: float = 0.0


def _area_size(area: Any) -> int:
    return int(area.width) * int(area.height)


def _area_x(area: Any) -> int:
    return int(area.x)


def sidebar_widths(width: float, scale: float) -> tuple[int, int]:
    """Restore chosen widths, reducing them only when they cannot fit the window."""
    left: float = max(29.0, float(get_state().left_width)) * scale
    right: float = max(29.0, float(get_state().right_width)) * scale
    available: float = max(2.0, width - 29.0 * scale)
    factor: float = min(1.0, available / (left + right))
    return max(1, round(left * factor)), max(1, round(right * factor))


def on_divider(context: Any, x: int, y: int) -> bool:
    """Leave the native resize hit zone available to Blender's screen handler."""
    scale: float = max(1.0, float(context.preferences.system.ui_scale))
    padding: float = (float(context.preferences.view.border_width) + 3.0) * scale
    area: Any
    for area in context.window.screen.areas:
        name: str = role(area)
        if name not in ("toolbox", "layers") or not area.y <= y < area.y + area.height:
            continue
        edge: int = int(area.x + area.width) if name == "toolbox" else int(area.x)
        if abs(x - edge) <= padding:
            return True
    return False


def reset_panel_cursor(context: Any, x: int, y: int) -> None:
    """Clear stale border cursors where custom panels consume mouse movement."""
    if on_divider(context, x, y):
        return
    area: Any
    for area in context.window.screen.areas:
        if (
            role(area) in ("toolbox", "layers")
            and area.x <= x < area.x + area.width
            and area.y <= y < area.y + area.height
        ):
            context.window.cursor_set("DEFAULT")
            return


def role(area: Any) -> str:
    """Return the drawing role without claiming unrelated Blender windows."""
    return _ROLES.get(int(area.as_pointer()), "") if area is not None else ""


def viewport_area(context: Any) -> Any:
    if _PENDING:
        return None
    area: Any
    for area in context.screen.areas:
        if role(area) == "viewport":
            return area
    for area in context.screen.areas:
        if area.type == "VIEW_3D":
            return area
    return None


def _clean_space(area: Any) -> None:
    space: Any = area.spaces.active
    name: str
    for name in (
        "show_region_header",
        "show_region_tool_header",
        "show_region_toolbar",
        "show_region_ui",
        "show_region_asset_shelf",
        "show_region_hud",
    ):
        if hasattr(space, name) and not space.is_property_readonly(name):
            setattr(space, name, False)
    if area.type == "IMAGE_EDITOR":
        space.image = None
        space.show_region_ui = False
        space.show_region_toolbar = False
        # The panels are application canvases; the image editor's zoom and pan gizmos stay hidden.
        space.show_gizmo = False


def _adopt(screen: Any, width: float, scale: float) -> bool:
    """Claim the saved Tool Box | viewport | Clayers editors from the application's startup file."""
    areas: list[Any] = sorted(screen.areas, key=_area_x)
    types: list[str] = []
    area: Any
    for area in areas:
        types.append(str(area.type))
    if types != ["IMAGE_EDITOR", "VIEW_3D", "IMAGE_EDITOR"]:
        return False
    targets: tuple[int, int] = sidebar_widths(width, scale)
    tolerance: float = 4.0 * scale
    if abs(int(areas[0].width) - targets[0]) > tolerance or abs(int(areas[-1].width) - targets[1]) > tolerance:
        return False
    _ROLES.clear()
    _WIDTH_BASE.clear()
    name: str
    for area, name in zip(areas, ("toolbox", "viewport", "layers")):
        _ROLES[int(area.as_pointer())] = name
        _clean_space(area)
        area.tag_redraw()
    screen.show_statusbar = False
    _WIDTH_BASE["toolbox"] = (int(areas[0].width), float(get_state().left_width))
    _WIDTH_BASE["layers"] = (int(areas[-1].width), float(get_state().right_width))
    return True


def configure(context: Any, adopt: bool = True) -> None:
    """Use the startup file's editors, or collapse the screen and split actual resizable native areas."""
    global _PENDING
    if _PENDING:
        return
    window: Any = context.window
    screen: Any = window.screen
    scale: float = max(1.0, float(context.preferences.system.ui_scale))
    context.preferences.view.use_reduce_motion = True
    claimed: list[Any] = []
    area: Any
    for area in screen.areas:
        if role(area):
            claimed.append(area)
    if len(screen.areas) == 3 and len(claimed) == 3:
        return
    if adopt and not claimed and _adopt(screen, float(window.width), scale):
        return
    _ROLES.clear()
    _WIDTH_BASE.clear()
    _attempt: int
    for _attempt in range(24):
        areas: list[Any] = list(screen.areas)
        if len(areas) <= 1:
            break
        keeper: Any = max(areas, key=_area_size)
        candidates: list[Any] = []
        for area in areas:
            if area != keeper:
                candidates.append(area)
        candidates.sort(key=_area_size)
        closed: bool = False
        for area in candidates:
            with context.temp_override(window=window, screen=screen, area=area):
                result: set[str] = bpy.ops.screen.area_close()
            if "FINISHED" in result:
                closed = True
                break
        if not closed:
            raise RuntimeError("Unable to prepare the drawing workspace; an editor could not be closed")
    if len(screen.areas) != 1:
        raise RuntimeError("The drawing workspace requires one initial editor")
    main: Any = screen.areas[0]
    width: float = float(max(window.width, main.width, 1))
    left: int
    right: int
    left, right = sidebar_widths(width, scale)
    get_state().left_width = round(left / scale)
    get_state().right_width = round(right / scale)
    with context.temp_override(window=window, screen=screen, area=main):
        main.type = "VIEW_3D"
        bpy.ops.screen.area_split(direction="VERTICAL", factor=left / width)
    _PENDING = True
    phase: int = 0

    def finish_layout() -> float | None:
        """Let Blender initialize area coordinates and regions between changes."""
        nonlocal phase
        global _PENDING
        active: Any = bpy.context
        area: Any
        error: ReferenceError | RuntimeError | AttributeError
        try:
            ordered: list[Any] = sorted(screen.areas, key=_area_x)
            if phase == 0:
                remainder: Any = ordered[-1]
                with active.temp_override(window=window, screen=screen, area=remainder):
                    bpy.ops.screen.area_split(direction="VERTICAL", factor=1.0 - right / max(width - left, 1.0))
                phase = 1
                return 0.08
            if phase == 1:
                name: str
                for area, name in zip(ordered, ("toolbox", "viewport", "layers")):
                    with active.temp_override(window=window, screen=screen, area=area):
                        area.type = "VIEW_3D" if name == "viewport" else "IMAGE_EDITOR"
                        _ROLES[int(area.as_pointer())] = name
                phase = 2
                return 0.08
            for area in ordered:
                with active.temp_override(window=window, screen=screen, area=area):
                    _clean_space(area)
                    area.tag_redraw()
            screen.show_statusbar = False
            # Native borders reduce measured width; preserve the requested span.
            _WIDTH_BASE["toolbox"] = (int(ordered[0].width), float(get_state().left_width))
            _WIDTH_BASE["layers"] = (int(ordered[-1].width), float(get_state().right_width))
            _PENDING = False
            return None
        except (ReferenceError, RuntimeError, AttributeError) as error:
            _PENDING = False
            _ROLES.clear()
            get_state().status = "Workspace layout: " + str(error)
            print(get_state().status)
            return None

    bpy.app.timers.register(finish_layout, first_interval=0.08)


def redraw(context: Any | None = None) -> None:
    active: Any = context if context is not None else bpy.context
    window: Any
    area: Any
    for window in active.window_manager.windows:
        for area in window.screen.areas:
            if role(area):
                area.tag_redraw()


def _save_widths() -> float | None:
    """Write once after divider movement settles, outside the draw callback."""
    remaining: float = _WIDTH_SAVE_AT - time.monotonic()
    if _PENDING or remaining > 0.0:
        return max(0.05, remaining)
    save_preferences()
    return None


def capture_widths(context: Any) -> None:
    """Capture final dimensions before save or shutdown, even without a final redraw."""
    if not _PENDING and not bpy.app.background:
        window: Any = context.window
        if window is None and context.window_manager.windows:
            window = context.window_manager.windows[0]
        if window is not None:
            scale: float = max(1.0, float(context.preferences.system.ui_scale))
            area: Any
            for area in window.screen.areas:
                remember_width(area, scale, save=False)
    if bpy.app.timers.is_registered(_save_widths):
        bpy.app.timers.unregister(_save_widths)


def remember_width(area: Any, scale: float, *, save: bool = True) -> None:
    """Persist resize deltas without counting native editor-border thickness."""
    global _WIDTH_SAVE_AT
    if _PENDING:
        return
    name: str = role(area)
    baseline: tuple[int, float] | None = _WIDTH_BASE.get(name)
    if baseline is None:
        return
    width: int = round(max(29.0, baseline[1] + (int(area.width) - baseline[0]) / scale))
    if name == "toolbox":
        if get_state().left_width == width:
            return
        get_state().left_width = width
    elif name == "layers":
        if get_state().right_width == width:
            return
        get_state().right_width = width
    else:
        return
    if save and not bpy.app.background:
        _WIDTH_SAVE_AT = time.monotonic() + 0.4
        if not bpy.app.timers.is_registered(_save_widths):
            bpy.app.timers.register(_save_widths, first_interval=0.4)


def reset(context: Any) -> None:
    """Restore the initial sidebar widths and Clayers/chat split through the same native layout path."""
    get_state().left_width = 220
    get_state().right_width = 280
    get_state().layers_share = 1.0 / 3.0
    _ROLES.clear()
    # Rebuild the editors from one area, as the default widths are split exactly.
    configure(context, adopt=False)
