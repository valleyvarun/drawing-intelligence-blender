"""Three real Blender editor areas with application-owned panel drawing."""

from __future__ import annotations

from typing import Any

from ..core.blender import bpy
from ..core.state import get_state

_ROLES: dict[int, str] = {}
_PENDING: bool = False
_WIDTH_BASE: dict[str, tuple[int, float]] = {}


def _area_size(area: Any) -> int:
    return int(area.width) * int(area.height)


def _area_x(area: Any) -> int:
    return int(area.x)


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


def configure(context: Any) -> None:
    """Collapse the factory workspace, then split actual resizable native areas."""
    global _PENDING
    if _PENDING:
        return
    window: Any = context.window
    screen: Any = window.screen
    context.preferences.view.use_reduce_motion = True
    claimed: list[Any] = []
    area: Any
    for area in screen.areas:
        if role(area):
            claimed.append(area)
    if len(screen.areas) == 3 and len(claimed) == 3:
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
    width: float = float(max(window.width, main.width, 800))
    scale: float = max(1.0, float(context.preferences.system.ui_scale))
    left: float = max(145.0, min(float(get_state().left_width) * scale, width * 0.28))
    right: float = max(250.0, min(float(get_state().right_width) * scale, width * 0.35))
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


def remember_width(area: Any, scale: float) -> None:
    """Persist resize deltas without counting native editor-border thickness."""
    name: str = role(area)
    baseline: tuple[int, float] | None = _WIDTH_BASE.get(name)
    if baseline is None:
        return
    width: int = round(max(80.0, baseline[1] + (int(area.width) - baseline[0]) / scale))
    if name == "toolbox":
        get_state().left_width = width
    elif name == "layers":
        get_state().right_width = width


def reset(context: Any) -> None:
    """Restore the initial sidebar widths through the same native layout path."""
    get_state().left_width = 220
    get_state().right_width = 330
    _ROLES.clear()
    configure(context)
