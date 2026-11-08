"""Real-window input and screenshot test. Requires --enable-event-simulate."""

from __future__ import annotations

import json
import math
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

from drawing_app.core import controller
from drawing_app.core.blender import Vector, bpy, view3d_utils
from drawing_app.core.state import ROOT, get_state
from drawing_app.ui import layout, overlay
from drawing_app.viewport import grid_spacing, keymap
from drawing_app.viewport.guides import ruler_extent

CHECKS: Path = ROOT / ".checks"
REPORT: dict[str, object] = {}
STEPS: list[Callable[[], None]] = []
INDEX: int = 0
CURSOR_BEFORE: float = 0.0
VIEW_BEFORE: Any = None
DISTANCE_BEFORE: float = 0.0
SESSION: dict[str, Any] = {}


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


def verify_keymap_lifecycle() -> None:
    walk: Any = bpy.context.window_manager.keyconfigs.user.keymaps["View3D Walk Modal"]
    count: int = len(walk.keymap_items)
    keymap.uninstall()
    assert not keymap.walk_ready() and not keymap.active_bindings()
    assert len(walk.keymap_items) == count - len(keymap.WALK_ITEMS), (count, len(walk.keymap_items))
    item: Any
    for item in walk.keymap_items:
        if item.propvalue == "CANCEL" and item.type == "RIGHTMOUSE":
            assert item.active
    keymap.install()
    assert keymap.install_walk()
    assert len(walk.keymap_items) == count
    assert keymap.install_walk() and len(walk.keymap_items) == count
    REPORT["native_navigation_unregister_and_register"] = True


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


def viewport_center() -> tuple[int, int]:
    area: Any
    region: Any
    area, region = viewport()
    return region.x + region.width // 2, region.y + region.height // 2


def grease_strokes() -> int:
    obj: Any = bpy.data.objects[str(SESSION["grease"])]
    return len(obj.data.layers.active.frames[0].drawing.strokes)


def begin_grease() -> None:
    click("tool_grease")


def grease_ready() -> None:
    """Draw exposes Blender's native thumbnail shelf below the viewport."""
    area: Any
    region: Any
    area, region = viewport()
    assert area.spaces.active.show_region_asset_shelf
    REPORT["grease_pencil_draw_brush_shelf_visible"] = True


def grease_tools() -> None:
    from drawing_app.tools import pencil

    area: Any
    region: Any
    area, region = viewport()
    tool: str
    label: str
    with bpy.context.temp_override(area=area, region=region):
        for tool, label in pencil.TOOLS:
            controller.dispatch("pencil", ("tool", "builtin_brush.Erase"))
            controller.dispatch("pencil", ("tool", tool))
            assert pencil.active_tool(bpy.context) == tool, label
            if tool in pencil.DRAW_TOOLS:
                brush: Any = bpy.context.scene.tool_settings.gpencil_paint.brush
                assert brush is not None, label
                assert brush.gpencil_brush_type == "DRAW", (label, brush.name)
        controller.dispatch("pencil", ("tool", "builtin.brush"))
    REPORT["grease_pencil_native_tools_after_eraser"] = True


def grease_resize_begin() -> None:
    from drawing_app.tools import pencil

    view: pencil.PencilView | None = pencil.view(bpy.context)
    assert view is not None
    SESSION["radius"] = view.size
    area: Any
    region: Any
    area, region = viewport()
    SESSION["radius_view"] = float(area.spaces.active.region_3d.view_distance)
    event("MOUSEMOVE", "NOTHING", *viewport_center())
    event("F", "PRESS", *viewport_center())
    event("F", "RELEASE", *viewport_center())


def grease_resize_motion() -> None:
    x: int
    y: int
    x, y = viewport_center()
    event("MOUSEMOVE", "NOTHING", x + 110, y)


def grease_resize_confirm() -> None:
    x: int
    y: int
    x, y = viewport_center()
    click_at(x + 110, y)


def verify_grease_resize() -> None:
    from drawing_app.tools import pencil

    view: pencil.PencilView | None = pencil.view(bpy.context)
    assert view is not None and abs(view.size - float(SESSION["radius"])) > 1e-5
    area: Any
    region: Any
    area, region = viewport()
    assert abs(float(area.spaces.active.region_3d.view_distance) - float(SESSION["radius_view"])) < 1e-5
    controller.dispatch("pencil", ("size", SESSION["radius"]))
    SESSION["grease"] = bpy.context.view_layer.objects.active.name
    SESSION["strokes"] = grease_strokes()
    REPORT["grease_pencil_native_radius_shortcut"] = True
    click("canvas_cursor_active")


def grease_cursor_place() -> None:
    assert get_state().cursor_placing
    click_at(*position((-1.2, -0.8, 0.0)))


def verify_grease_cursor_place() -> None:
    assert get_state().cursor_placed and not get_state().cursor_placing
    assert grease_strokes() == int(SESSION["strokes"])
    REPORT["grease_pencil_cursor_placement_without_stroke"] = True
    click("cursor_place")


def grease_cursor_cancel() -> None:
    assert get_state().cursor_placing
    event("ESC", "PRESS", *viewport_center())
    event("ESC", "RELEASE", *viewport_center())


def verify_grease_cursor_cancel() -> None:
    assert not get_state().cursor_placing and get_state().tool == "grease"
    assert grease_strokes() == int(SESSION["strokes"])
    REPORT["grease_pencil_cursor_placement_cancel"] = True
    click("check_cursor_active")


def grease_press() -> None:
    from drawing_app.tools import pencil

    assert get_state().tool == "grease"
    area: Any
    region: Any
    area, region = viewport()
    with bpy.context.temp_override(area=area, region=region):
        assert pencil.active(bpy.context) and pencil.active_tool(bpy.context) == "builtin.brush"
    SESSION["grease"] = bpy.context.view_layer.objects.active.name
    SESSION["strokes"] = grease_strokes()
    event("MOUSEMOVE", "NOTHING", *position((-0.6, -0.4, 0.0)))
    event("LEFTMOUSE", "PRESS", *position((-0.6, -0.4, 0.0)))


def grease_motion() -> None:
    # A curved path: Blender's brush simplifies nearly straight input to its end points.
    x: int
    y: int
    x, y = position((-0.6, -0.4, 0.0))
    step: int
    for step in range(1, 17):
        event("MOUSEMOVE", "NOTHING", x + step * 14, y + round(40 * math.sin(step * 0.7)))


def grease_release() -> None:
    x: int
    y: int
    x, y = position((-0.6, -0.4, 0.0))
    event("LEFTMOUSE", "RELEASE", x + 16 * 14, y + round(40 * math.sin(16 * 0.7)))


def verify_grease() -> None:
    obj: Any = bpy.data.objects[str(SESSION["grease"])]
    drawing: Any = obj.data.layers.active.frames[0].drawing
    operators: list[str] = []
    operator: Any
    for operator in bpy.context.window_manager.operators:
        operators.append(str(operator.bl_idname))
    REPORT["grease_diagnostics"] = {
        "strokes": grease_strokes(),
        "before": SESSION["strokes"],
        "mode": obj.mode,
        "layer": obj.data.layers.active.name,
        "frames": len(obj.data.layers.active.frames),
        "operators": operators[-6:],
        "points": len(drawing.strokes[-1].points) if len(drawing.strokes) else 0,
        "press": position((-0.6, -0.4, 0.0)),
        "status": get_state().status,
    }
    assert grease_strokes() == int(SESSION["strokes"]) + 1 and len(drawing.strokes[-1].points) > 5
    REPORT["native_grease_pencil_brush_stroke"] = True
    x: int
    y: int
    x, y = viewport_center()
    event("Z", "PRESS", x, y, ctrl=True)
    event("Z", "RELEASE", x, y, ctrl=True)


def verify_grease_undo() -> None:
    assert grease_strokes() == int(SESSION["strokes"])
    REPORT["native_grease_pencil_undo"] = True
    click("tool_grease")


def verify_grease_exit() -> None:
    assert get_state().tool == "select" and bpy.context.view_layer.objects.active is None
    assert not bpy.data.objects[str(SESSION["grease"])].select_get()
    REPORT["grease_pencil_returns_to_object_mode"] = True


def eye() -> Any:
    area: Any
    region: Any
    area, region = viewport()
    return area.spaces.active.region_3d.view_matrix.inverted().translation.copy()


def begin_fly() -> None:
    area: Any
    region: Any
    area, region = viewport()
    view: Any = area.spaces.active.region_3d
    SESSION["view"] = (view.view_location.copy(), view.view_rotation.copy(), float(view.view_distance))
    controller.dispatch("navigation", "fly")
    SESSION["eye"] = eye()
    SESSION["fly_grid"] = grid_spacing(bpy.context, area, region)
    SESSION["fly_extent"] = ruler_extent(area, SESSION["fly_grid"])
    event("MOUSEMOVE", "NOTHING", *viewport_center())
    event("RIGHTMOUSE", "PRESS", *viewport_center())


def fly_look() -> None:
    x: int
    y: int
    x, y = viewport_center()
    event("MOUSEMOVE", "NOTHING", x + 70, y + 25)


def verify_fly_look() -> None:
    area: Any
    region: Any
    area, region = viewport()
    view: Any = area.spaces.active.region_3d
    assert float(view.view_distance) == 0.0
    assert (eye() - SESSION["eye"]).length < 1e-4
    assert view.view_rotation.rotation_difference(SESSION["view"][1]).angle > 1e-4
    step: float = grid_spacing(bpy.context, area, region)
    assert step == SESSION["fly_grid"]
    assert abs(ruler_extent(area, step) - SESSION["fly_extent"]) < 1e-4
    REPORT["fly_look_keeps_eye_and_ruler_scale"] = True


def fly_forward() -> None:
    event("W", "PRESS", *viewport_center())


def fly_stop() -> None:
    event("W", "RELEASE", *viewport_center())


def end_fly() -> None:
    event("RIGHTMOUSE", "RELEASE", *viewport_center())


def verify_fly() -> None:
    assert (eye() - SESSION["eye"]).length > 0.01
    REPORT["native_walk_fly_held_on_right_button"] = True
    controller.dispatch("navigation", "orbit")
    area: Any
    region: Any
    area, region = viewport()
    view: Any = area.spaces.active.region_3d
    location: Any
    rotation: Any
    distance: float
    location, rotation, distance = SESSION["view"]
    view.view_location = location
    view.view_rotation = rotation
    view.view_distance = distance
    view.update()


def verify_identity() -> None:
    import ctypes

    from drawing_app.core.desktop import identity

    hwnd: int = identity().main_window()
    assert hwnd
    title: Any = ctypes.create_unicode_buffer(512)
    ctypes.windll.user32.GetWindowTextW(hwnd, title, 512)
    assert title.value.endswith(" - Drawing Intelligence") and "Blender" not in title.value, title.value
    REPORT["window_title"] = title.value


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
        verify_keymap_lifecycle,
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
        begin_grease,
        grease_ready,
        grease_tools,
        grease_resize_begin,
        grease_resize_motion,
        grease_resize_confirm,
        verify_grease_resize,
        grease_cursor_place,
        verify_grease_cursor_place,
        grease_cursor_cancel,
        verify_grease_cursor_cancel,
        grease_press,
        grease_motion,
        grease_release,
        verify_grease,
        verify_grease_undo,
        verify_grease_exit,
        begin_fly,
        fly_look,
        verify_fly_look,
        fly_forward,
        fly_stop,
        end_fly,
        verify_fly,
        verify_identity,
        capture_light,
        capture_dark,
    )
)
bpy.app.timers.register(advance, first_interval=4.0)
