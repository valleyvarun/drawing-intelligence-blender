"""Sketch Mesh in the real window: lines on a drawing plane that is moved between them, then the mesh.

The brush goes down with Esc and B and comes up from the Tool Box and with B; with it down the plane is selected
and raised. Enter builds the mesh, undo brings the sketch back, and Esc with the brush down asks before cancelling.
Requires --enable-event-simulate.
"""

from __future__ import annotations

import json
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

from drawing_app.core import controller
from drawing_app.core.blender import Vector, bmesh, bpy, view3d_utils
from drawing_app.core.state import ROOT, get_state
from drawing_app.tools import pipe_edit, sketch_mesh
from drawing_app.ui import layout, overlay

CHECKS: Path = ROOT / ".checks"
REPORT: dict[str, object] = {}
STEPS: list[Callable[[], None]] = []
INDEX: int = 0


def window() -> Any:
    return bpy.context.window_manager.windows[0]


def event(kind: str, value: str = "PRESS", x: int = 0, y: int = 0, **modifiers: bool | str) -> None:
    window().event_simulate(type=kind, value=value, x=x, y=y, **modifiers)


def widgets() -> dict[str, tuple[Any, overlay.Widget]]:
    found: dict[str, tuple[Any, overlay.Widget]] = {}
    area: Any
    for area in window().screen.areas:
        widget: overlay.Widget
        for widget in overlay._WIDGETS.get(int(area.as_pointer()), []):
            found[widget.key] = (area, widget)
    return found


def click_at(x: int, y: int) -> None:
    event("MOUSEMOVE", "NOTHING", x, y)
    event("LEFTMOUSE", "PRESS", x, y)
    event("LEFTMOUSE", "RELEASE", x, y)


def click(key: str) -> None:
    area: Any
    widget: overlay.Widget
    area, widget = widgets()[key]
    region: Any = next(item for item in area.regions if item.type == "WINDOW")
    scale: float = max(1.0, float(bpy.context.preferences.system.ui_scale))
    click_at(
        round(region.x + (widget.bounds[0] + widget.bounds[2] / 2) * scale),
        round(region.y + (widget.bounds[1] + widget.bounds[3] / 2) * scale),
    )


def position(world: tuple[float, float, float]) -> tuple[int, int]:
    area: Any = layout.viewport_area(bpy.context)
    assert area is not None
    region: Any = next(item for item in area.regions if item.type == "WINDOW")
    point: Any = view3d_utils.location_3d_to_region_2d(region, area.spaces.active.region_3d, Vector(world))
    assert point is not None
    return round(point.x + region.x), round(point.y + region.y)


def drag(*points: tuple[float, float, float]) -> None:
    """One press, drag, and release through these points of the drawing plane."""
    event("MOUSEMOVE", "NOTHING", *position(points[0]))
    event("LEFTMOUSE", "PRESS", *position(points[0]))
    point: tuple[float, float, float]
    for point in points[1:]:
        event("MOUSEMOVE", "NOTHING", *position(point))
    event("LEFTMOUSE", "RELEASE", *position(points[-1]))


def key(kind: str, **modifiers: bool) -> None:
    """A key press with the pointer over the middle of the viewport."""
    spot: tuple[int, int] = position((0.0, 0.0, 0.0))
    event("MOUSEMOVE", "NOTHING", *spot)
    event(kind, "PRESS", *spot, **modifiers)
    event(kind, "RELEASE", *spot, **modifiers)


def sketch_lines() -> list[Any]:
    return [item for item in bpy.context.scene.objects if item.get("drawing_sketch")]


def meshes() -> list[Any]:
    return [item for item in bpy.context.scene.objects if item.get("drawing_kind") == "sketch_mesh"]


def plane() -> Any:
    return bpy.data.objects[get_state().active_plane]


def heights() -> list[float]:
    """The height each sketch line was drawn at, lowest first."""
    found: list[float] = []
    line: Any
    for line in sketch_lines():
        found.append(
            round(sum(sample.position[2] for sample in pipe_edit.samples(line)) / len(pipe_edit.samples(line)), 3)
        )
    return sorted(found)


def open_edges(obj: Any) -> int:
    mesh: Any = bmesh.new()
    mesh.from_mesh(obj.data)
    count: int = sum(1 for edge in mesh.edges if len(edge.link_faces) != 2)
    mesh.free()
    return count


def state_is(tool: str, sketching: bool) -> None:
    assert (get_state().tool, get_state().sketching) == (tool, sketching), (get_state().tool, get_state().sketching)


def setup() -> None:
    assert controller._ACTIVE is not None
    controller.dispatch("tool", "select")
    state: Any = get_state()
    state.cursor_active = state.cursor_placed = state.cursor_placing = False
    state.on_object = state.drawing_plane = state.axes_spokes = False
    state.brushes["sketch"].radius = 0.03
    state.sketch.closed, state.sketch.curved, state.sketch.detail = False, True, 24
    assert "tool_sketch" in widgets() and "tool_paint" in widgets()
    # A drawing plane to draw on and to raise between lines.
    controller.dispatch("plane", "create")
    assert state.drawing_plane and plane().get("drawing_plane")


def choose_tool() -> None:
    click("tool_sketch")


def verify_tool() -> None:
    shown: dict[str, tuple[Any, overlay.Widget]] = widgets()
    state_is("sketch", True)
    name: str
    for name in (
        "sketch_brush",
        "tool_move",
        "tool_rotate",
        "tool_scale",
        "sketch_open",
        "sketch_closed",
        "sketch_curved",
        "sketch_straight",
        "sketch_brush_view",
    ):
        assert name in shown, name
    # The tool chooser is replaced, lines are round and of one width, and nothing waits to be built yet.
    assert "tool_paint" not in shown and "brush_shape" not in shown and "pressure_radius" not in shown
    assert "sketch_confirm" not in shown and "sketch_confirm_view" not in shown
    REPORT["tool_box_shows_brush_move_tools_and_settings"] = True


def first_line() -> None:
    drag((-0.8, -0.8, 0.0), (-0.4, 0.0, 0.0), (-0.8, 0.8, 0.0))


def escape() -> None:
    key("ESC")


def verify_brush_down() -> None:
    # Esc put the brush down: the sketch and its line are still there, and nothing was asked.
    state_is("select", True)
    assert len(sketch_lines()) == 1 and overlay._QUESTION is None
    shown: dict[str, tuple[Any, overlay.Widget]] = widgets()
    assert shown["sketch_brush"][1].payload == ("brush", True) and "tool_paint" not in shown
    assert "sketch_confirm_view" in shown and "sketch_brush_view" in shown
    REPORT["escape_puts_the_brush_down_and_keeps_the_sketch"] = True
    # With the brush down a click selects instead of drawing: here, the drawing plane.
    bpy.ops.object.select_all(action="DESELECT")
    click_at(*position((0.7, 0.0, 0.0)))


def verify_click_selected() -> None:
    assert len(sketch_lines()) == 1
    assert plane().select_get() and bpy.context.view_layer.objects.active == plane()
    click("tool_move")


def raise_plane() -> None:
    state_is("move", True)
    shown: dict[str, tuple[Any, overlay.Widget]] = widgets()
    # Move's fields for the plane sit above the sketch's settings, which are all still there.
    assert "transform_location_2" in shown and "sketch_open" in shown and "slider_detail" in shown
    controller.dispatch("transform", ("location", 2, 0.8))
    assert abs(plane().location.z - 0.8) < 1e-6
    bpy.ops.screen.screenshot(filepath=str(CHECKS / "sketch-mesh-brush-down.png"))
    REPORT["plane_moves_with_the_brush_down"] = True
    click("sketch_brush")


def second_line() -> None:
    state_is("sketch", True)
    drag((0.0, -0.8, 0.8), (0.4, 0.0, 0.8), (0.0, 0.8, 0.8))


def brush_key() -> None:
    key("B")


def raise_again() -> None:
    # B put the brush down; the plane goes up once more, and B picks the brush up again.
    state_is("select", True)
    assert heights() == [0.0, 0.8], heights()
    controller.dispatch("transform", ("location", 2, 1.6))
    key("B")


def third_line() -> None:
    state_is("sketch", True)
    drag((0.8, -0.8, 1.6), (1.2, 0.0, 1.6), (0.8, 0.8, 1.6))


def verify_lines() -> None:
    assert heights() == [0.0, 0.8, 1.6], heights()
    assert sketch_mesh.count(bpy.context) == 3
    bpy.ops.screen.screenshot(filepath=str(CHECKS / "sketch-mesh-lines.png"))
    REPORT["lines_drawn_on_a_plane_moved_between_them"] = True
    # Enter builds the mesh with the brush down too.
    key("B")


def press_enter() -> None:
    state_is("select", True)
    key("RET")


def verify_mesh() -> None:
    built: list[Any] = meshes()
    assert len(built) == 1 and not sketch_lines(), (len(built), len(sketch_lines()))
    assert abs(built[0].dimensions.z - 1.6) < 0.01 and open_edges(built[0]) > 0, tuple(built[0].dimensions)
    assert "lofted" in get_state().status, get_state().status
    state_is("select", True)
    assert "sketch_confirm_view" not in widgets() and "sketch_brush_view" in widgets()
    bpy.ops.screen.screenshot(filepath=str(CHECKS / "sketch-mesh-open.png"))
    REPORT["enter_builds_the_mesh_through_lines_at_three_heights"] = True


def undo() -> None:
    key("Z", ctrl=True)


def verify_undo() -> None:
    assert len(sketch_lines()) == 3 and not meshes()
    state_is("select", True)
    assert "sketch_confirm_view" in widgets()
    REPORT["undo_brings_the_sketch_back"] = True


def verify_question() -> None:
    # Esc with the brush down asks; nothing has changed yet.
    assert overlay._QUESTION is not None and overlay._QUESTION.title == "Do you want to cancel?"
    assert "question_yes" in widgets() and "question_no" in widgets()
    assert len(sketch_lines()) == 3
    state_is("select", True)
    bpy.ops.screen.screenshot(filepath=str(CHECKS / "sketch-mesh-question.png"))
    click("question_no")


def verify_no() -> None:
    assert overlay._QUESTION is None and len(sketch_lines()) == 3
    state_is("select", True)
    REPORT["no_keeps_the_sketch"] = True
    key("ESC")


def answer_enter() -> None:
    assert overlay._QUESTION is not None
    key("RET")


def verify_yes() -> None:
    # Enter said yes: the lines are gone, the sketch is closed, and the tool chooser is back.
    assert overlay._QUESTION is None and not sketch_lines() and not meshes()
    state_is("select", False)
    assert get_state().status == "Sketch cancelled"
    assert "tool_paint" in widgets() and "sketch_brush_view" not in widgets()
    REPORT["enter_answers_yes_and_cancels_the_sketch"] = True
    REPORT["passed"] = True


def advance() -> float | None:
    global INDEX
    try:
        if INDEX < len(STEPS):
            STEPS[INDEX]()
            INDEX += 1
            return 0.4
    except Exception:
        REPORT["failed_step"] = STEPS[INDEX].__name__
        REPORT["error"] = traceback.format_exc()
        bpy.ops.screen.screenshot(filepath=str(CHECKS / "sketch-mesh-failure.png"))
    (CHECKS / "sketch-mesh-ui-report.json").write_text(json.dumps(REPORT, indent=2), encoding="utf-8")
    bpy.ops.wm.quit_blender()
    return None


STEPS.extend(
    (
        setup,
        choose_tool,
        verify_tool,
        first_line,
        escape,
        verify_brush_down,
        verify_click_selected,
        raise_plane,
        second_line,
        brush_key,
        raise_again,
        third_line,
        verify_lines,
        press_enter,
        verify_mesh,
        undo,
        verify_undo,
        escape,
        verify_question,
        verify_no,
        answer_enter,
        verify_yes,
    )
)
bpy.app.timers.register(advance, first_interval=4.0)
