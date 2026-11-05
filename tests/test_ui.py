"""Real-window input and screenshot test. Requires --enable-event-simulate."""

from __future__ import annotations

import json
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

from drawing_app.core import controller
from drawing_app.core.blender import Vector, bpy, view3d_utils
from drawing_app.core.state import ROOT, get_state
from drawing_app.ui import layout, overlay
from drawing_app.viewport import grid_spacing

CHECKS: Path = ROOT / ".checks"
REPORT: dict[str, object] = {}
STEPS: list[Callable[[], None]] = []
INDEX: int = 0
CURSOR_BEFORE: float = 0.0
VIEW_BEFORE: Any = None
DISTANCE_BEFORE: float = 0.0


def window() -> Any:
    return bpy.context.window_manager.windows[0]


def event(kind: str, value: str = "PRESS", x: int = 0, y: int = 0, **modifiers: bool | str) -> None:
    window().event_simulate(type=kind, value=value, x=x, y=y, **modifiers)


def click_at(x: int, y: int) -> None:
    event("MOUSEMOVE", "NOTHING", x, y)
    event("LEFTMOUSE", "PRESS", x, y)
    event("LEFTMOUSE", "RELEASE", x, y)


def click(key: str) -> None:
    area: Any
    for area in window().screen.areas:
        widget: overlay.Widget
        for widget in overlay._WIDGETS.get(int(area.as_pointer()), []):
            if widget.key == key:
                region: Any = next(item for item in area.regions if item.type == "WINDOW")
                scale: float = max(1.0, float(bpy.context.preferences.system.ui_scale))
                x: int = round(region.x + (widget.bounds[0] + widget.bounds[2] / 2) * scale)
                y: int = round(region.y + (widget.bounds[1] + widget.bounds[3] / 2) * scale)
                click_at(x, y)
                return
    raise AssertionError("UI widget not found: " + key)


def viewport() -> tuple[Any, Any]:
    area: Any = layout.viewport_area(bpy.context)
    assert area is not None
    region: Any = next(item for item in area.regions if item.type == "WINDOW")
    return area, region


def position(world: tuple[float, float, float]) -> tuple[int, int]:
    area: Any
    region: Any
    area, region = viewport()
    point: Any = view3d_utils.location_3d_to_region_2d(region, area.spaces.active.region_3d, Vector(world))
    assert point is not None
    return round(point.x + region.x), round(point.y + region.y)


def setup() -> None:
    assert controller._ACTIVE is not None
    controller.dispatch("theme", "light")
    controller.dispatch("tool", "select")
    get_state().cursor_active = False
    get_state().cursor_placed = False
    get_state().cursor_placing = False
    get_state().on_object = False
    get_state().drawing_plane = False
    get_state().brushes["pipe"].radius = 0.04
    assert len(window().screen.areas) == 3
    area: Any
    for area in window().screen.areas:
        assert not area.spaces.active.show_region_toolbar
        assert not area.spaces.active.show_region_ui
    REPORT["native_layout"] = True


def verify_cursor_active() -> None:
    assert get_state().cursor_active and get_state().cursor_placing
    click_at(*position((0.8, 1.2, 0.0)))


def verify_cursor_snap() -> None:
    global CURSOR_BEFORE
    assert get_state().cursor_placed and not get_state().cursor_placing
    area: Any
    region: Any
    area, region = viewport()
    step: float = grid_spacing(bpy.context, area, region)
    cursor: Any = bpy.context.scene.cursor.location
    assert abs(cursor.x / step - round(cursor.x / step)) < 1e-5
    assert abs(cursor.y / step - round(cursor.y / step)) < 1e-5
    REPORT["native_cursor_click_and_grid_snap"] = True
    CURSOR_BEFORE = float(cursor.x)
    click("cursor_location_0_plus")


def verify_increment() -> None:
    assert bpy.context.scene.cursor.location.x > CURSOR_BEFORE
    REPORT["cursor_increment_button"] = True
    click("check_cursor_active")


def begin_pipe() -> None:
    assert get_state().tool == "pipe"
    event("MOUSEMOVE", "NOTHING", *position((-1.0, 0.0, 0.0)))
    event("LEFTMOUSE", "PRESS", *position((-1.0, 0.0, 0.0)))


def continue_pipe() -> None:
    assert controller._ACTIVE is not None and controller._ACTIVE.drawing.is_drawing
    event("MOUSEMOVE", "NOTHING", *position((0.4, 0.8, 0.0)))


def finish_pipe_over_panel() -> None:
    area: Any = next(item for item in window().screen.areas if layout.role(item) == "toolbox")
    event("LEFTMOUSE", "RELEASE", area.x + 8, area.y + 20)


def verify_pipe() -> None:
    assert controller._ACTIVE is not None and not controller._ACTIVE.drawing.is_drawing
    objects: list[Any] = [item for item in bpy.context.scene.objects if item.get("drawing_kind") == "pipe"]
    assert len(objects) == 1 and len(objects[0].data.vertices) > 20
    REPORT["native_pipe_drag_and_release_over_panel"] = True
    click("tool_pipe")


def undo_over_panel() -> None:
    area: Any = next(item for item in window().screen.areas if layout.role(item) == "layers")
    event("MOUSEMOVE", "NOTHING", area.x + 20, area.y + 20)
    event("Z", "PRESS", area.x + 20, area.y + 20, ctrl=True)
    event("Z", "RELEASE", area.x + 20, area.y + 20, ctrl=True)


def verify_undo_redo() -> None:
    assert not any(item.get("drawing_kind") == "pipe" for item in bpy.context.scene.objects)
    area: Any = next(item for item in window().screen.areas if layout.role(item) == "layers")
    event("Z", "PRESS", area.x + 20, area.y + 20, ctrl=True, shift=True)
    event("Z", "RELEASE", area.x + 20, area.y + 20, ctrl=True, shift=True)


def verify_redo() -> None:
    assert any(item.get("drawing_kind") == "pipe" for item in bpy.context.scene.objects)
    REPORT["native_undo_redo_over_panel_and_graphics_reload"] = True


def begin_orbit() -> None:
    global VIEW_BEFORE
    area: Any
    region: Any
    area, region = viewport()
    VIEW_BEFORE = area.spaces.active.region_3d.view_rotation.copy()
    event("RIGHTMOUSE", "PRESS", region.x + region.width // 2, region.y + region.height // 2)


def orbit_motion() -> None:
    area: Any
    region: Any
    area, region = viewport()
    event("MOUSEMOVE", "NOTHING", region.x + region.width // 2 + 50, region.y + region.height // 2 + 20)


def orbit_release() -> None:
    area: Any = next(item for item in window().screen.areas if layout.role(item) == "layers")
    event("RIGHTMOUSE", "RELEASE", area.x + 20, area.y + 20)


def verify_orbit() -> None:
    global DISTANCE_BEFORE
    assert controller._ACTIVE is not None and not controller._ACTIVE.navigation.active
    area: Any
    region: Any
    area, region = viewport()
    assert VIEW_BEFORE.rotation_difference(area.spaces.active.region_3d.view_rotation).angle > 0.01
    REPORT["native_orbit_and_release_over_panel"] = True
    DISTANCE_BEFORE = float(area.spaces.active.region_3d.view_distance)
    event("WHEELUPMOUSE", "PRESS", region.x + region.width // 2, region.y + region.height // 2)


def verify_zoom() -> None:
    area: Any
    region: Any
    area, region = viewport()
    assert area.spaces.active.region_3d.view_distance < DISTANCE_BEFORE
    REPORT["native_wheel_zoom"] = True
    controller.dispatch("create", "cube")
    bpy.context.object.location = (0.0, 0.0, 0.5)
    controller.dispatch("create", "sphere")
    bpy.context.object.location = (1.6, 0.0, 0.5)
    bpy.ops.object.select_all(action="DESELECT")
    controller.dispatch("tool", "select")
    area.spaces.active.region_3d.view_location = (0.6, 0.0, 0.3)
    area.spaces.active.region_3d.view_distance = 8.0
    area.spaces.active.region_3d.update()
    REPORT["unselected_polygon_outline_scene"] = True


def capture_light() -> None:
    bpy.ops.screen.screenshot(filepath=str(CHECKS / "verified-light.png"))
    controller.dispatch("theme", "dark")


def capture_dark() -> None:
    bpy.ops.screen.screenshot(filepath=str(CHECKS / "verified-dark.png"))
    REPORT["screenshots"] = ["verified-light.png", "verified-dark.png"]
    REPORT["passed"] = True


def advance() -> float | None:
    global INDEX
    try:
        if INDEX < len(STEPS):
            STEPS[INDEX]()
            INDEX += 1
            return 0.35
    except Exception:
        REPORT["failed_step"] = INDEX
        REPORT["error"] = traceback.format_exc()
        bpy.ops.screen.screenshot(filepath=str(CHECKS / "ui-failure.png"))
    (CHECKS / "ui-input-report.json").write_text(json.dumps(REPORT, indent=2), encoding="utf-8")
    bpy.ops.wm.quit_blender()
    return None


def enable_cursor() -> None:
    click("check_cursor_active")


def enable_pipe() -> None:
    click("tool_pipe")


STEPS.extend(
    (
        setup,
        enable_cursor,
        verify_cursor_active,
        verify_cursor_snap,
        verify_increment,
        enable_pipe,
        begin_pipe,
        continue_pipe,
        finish_pipe_over_panel,
        verify_pipe,
        undo_over_panel,
        verify_undo_redo,
        verify_redo,
        begin_orbit,
        orbit_motion,
        orbit_release,
        verify_orbit,
        verify_zoom,
        capture_light,
        capture_dark,
    )
)
bpy.app.timers.register(advance, first_interval=4.0)
