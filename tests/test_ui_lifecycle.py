"""Native widget input, non-meter units, and workspace/project lifecycle regression.

Run with run.ps1 -EnableEventSimulation -Script tests/test_ui_lifecycle.py.
The test writes its project, report, and captures only beneath .checks.
"""

from __future__ import annotations

import json
import math
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from drawing_app.core import controller
from drawing_app.core.blender import bpy
from drawing_app.core.state import ROOT, get_state
from drawing_app.tools import layers
from drawing_app.ui import layout, overlay, paint

CHECKS: Path = ROOT / ".checks"
PROJECT: Path = CHECKS / "ui-lifecycle.blend"
REPORT: dict[str, object] = {"passed": False}
INDEX: int = 0
MOUSE: tuple[int, int] = (0, 0)
GROUP_KEY: str = ""
GROUP_NAME: str = ""
OBJECT_NAME: str = "LifecycleCube"
SESSION_BEFORE: Any = None
SIDEBAR_WIDTHS: tuple[int, int] = (0, 0)
KEYS: dict[str, str] = {
    "0": "ZERO",
    "1": "ONE",
    "2": "TWO",
    "3": "THREE",
    "4": "FOUR",
    "5": "FIVE",
    "6": "SIX",
    "7": "SEVEN",
    "8": "EIGHT",
    "9": "NINE",
    ".": "PERIOD",
    "-": "MINUS",
    "+": "EQUAL",
    "e": "E",
}


@dataclass(frozen=True)
class Step:
    name: str
    action: Callable[[], None]
    delay: float = 0.35


def window() -> Any:
    return bpy.context.window_manager.windows[0]


def window_region(area: Any) -> Any:
    region: Any
    for region in area.regions:
        if region.type == "WINDOW":
            return region
    raise AssertionError("Editor has no WINDOW region")


def event(kind: str, value: str = "PRESS", *, unicode: str = "", ctrl: bool = False) -> None:
    if unicode:
        window().event_simulate(type=kind, value=value, x=MOUSE[0], y=MOUSE[1], unicode=unicode, ctrl=ctrl)
    else:
        window().event_simulate(type=kind, value=value, x=MOUSE[0], y=MOUSE[1], ctrl=ctrl)


def click_at(x: int, y: int) -> None:
    global MOUSE
    MOUSE = (x, y)
    event("MOUSEMOVE", "NOTHING")
    event("LEFTMOUSE")
    event("LEFTMOUSE", "RELEASE")


def widget(key: str) -> tuple[Any, overlay.Widget]:
    area: Any
    item: overlay.Widget
    for area in window().screen.areas:
        for item in overlay._WIDGETS.get(int(area.as_pointer()), []):
            if item.key == key:
                return area, item
    raise AssertionError("UI widget not found: " + key)


def click(key: str) -> None:
    area: Any
    item: overlay.Widget
    area, item = widget(key)
    region: Any = window_region(area)
    scale: float = max(1.0, float(bpy.context.preferences.system.ui_scale))
    x: int = round(region.x + (item.bounds[0] + item.bounds[2] * 0.5) * scale)
    y: int = round(region.y + (item.bounds[1] + item.bounds[3] * 0.5) * scale)
    assert region.x <= x < region.x + region.width and region.y <= y < region.y + region.height, (key, x, y)
    click_at(x, y)


def click_step(key: str) -> Callable[[], None]:
    def execute() -> None:
        click(key)

    return execute


def rename_item(key: str) -> None:
    """Use native F2 because Blender's simulator disables double-click synthesis."""
    click(key)
    event("F2")
    event("F2", "RELEASE")


def native_text(value: str) -> Callable[[], None]:
    def execute() -> None:
        event("A", ctrl=True)
        event("A", "RELEASE", ctrl=True)
        character: str
        for character in value:
            key: str = KEYS.get(character, character.upper())
            event(key, unicode=character)
            event(key, "RELEASE")
        event("RET")
        event("RET", "RELEASE")

    return execute


def confirm_dialog() -> None:
    event("RET")
    event("RET", "RELEASE")


def type_number(key: str, value: str, commit: bool = True) -> None:
    click(key)
    event("A", ctrl=True)
    event("A", "RELEASE", ctrl=True)
    character: str
    for character in value:
        event(KEYS[character], unicode=character)
        event(KEYS[character], "RELEASE")
    if commit:
        event("RET")
        event("RET", "RELEASE")


def entry_step(key: str, value: str, commit: bool = True) -> Callable[[], None]:
    def execute() -> None:
        type_number(key, value, commit)

    return execute


def close(actual: float, expected: float, tolerance: float = 1e-4) -> None:
    assert math.isclose(actual, expected, abs_tol=tolerance, rel_tol=0), {"actual": actual, "expected": expected}


def cube() -> Any:
    result: Any = bpy.data.objects.get(OBJECT_NAME)
    assert result is not None
    return result


def assert_layout() -> None:
    assert controller._ACTIVE is not None and not controller._ACTIVE.closed
    assert bpy.context.preferences.apps.show_edge_resize
    ordered: list[tuple[int, str]] = []
    area: Any
    for area in window().screen.areas:
        ordered.append((int(area.x), layout.role(area)))
        prop: str
        for prop in ("show_region_header", "show_region_tool_header", "show_region_toolbar", "show_region_ui"):
            assert not getattr(area.spaces.active, prop), (layout.role(area), prop)
    ordered.sort()
    assert ordered[0][1] == "toolbox" and ordered[1][1] == "viewport" and ordered[2][1] == "layers", ordered
    assert len(ordered) == 3 and len(overlay._HANDLES) == 2


def setup() -> None:
    assert_layout()
    get_state().cursor_active = False
    get_state().cursor_placing = False
    get_state().units = "m"
    bpy.context.scene.unit_settings.scale_length = 0.01
    controller.dispatch("create", "cube")
    obj: Any = bpy.context.view_layer.objects.active
    obj.name = "LifecycleCube"
    obj.location = (100, 0, 50)
    controller.dispatch("frame")
    controller.redraw()
    REPORT["initial_layout"] = True


def verify_meters_display() -> None:
    close(widget("transform_location_0")[1].value, 1.0, 1e-6)


def verify_move_entry() -> None:
    close(float(cube().location.x), 250.0)
    assert not overlay.is_capturing()


def verify_move_plus() -> None:
    close(float(cube().location.x), 251.0)


def verify_move_minus() -> None:
    close(float(cube().location.x), 250.0)
    REPORT["move_xyz_native_entry_and_steps_meters"] = True
    controller.dispatch("option", ("cursor_active", True))
    get_state().cursor_placed = True
    get_state().cursor_placing = False
    bpy.context.scene.cursor.location = (50, 25, 0)
    controller.redraw()


def verify_cursor_display() -> None:
    close(widget("cursor_location_0")[1].value, 0.5, 1e-6)
    close(widget("cursor_location_1")[1].value, 0.25, 1e-6)


def verify_cursor_entry() -> None:
    close(float(bpy.context.scene.cursor.location.y), 75.0)


def verify_cursor_plus() -> None:
    close(float(bpy.context.scene.cursor.location.y), 76.0)


def verify_cursor_minus() -> None:
    close(float(bpy.context.scene.cursor.location.y), 75.0)
    REPORT["cursor_xyz_native_entry_and_steps_meters"] = True


def select_mm() -> None:
    assert overlay._POPUP is not None
    click("popup_0")


def verify_mm_display() -> None:
    assert get_state().units == "mm"
    close(widget("transform_location_0")[1].value, 2500.0, 1e-3)
    close(widget("cursor_location_1")[1].value, 750.0, 1e-3)


def verify_mm_entry() -> None:
    close(float(cube().location.x), 125.0)


def verify_mm_plus() -> None:
    close(float(cube().location.x), 125.1)


def verify_mm_minus() -> None:
    close(float(cube().location.x), 125.0)
    REPORT["millimeter_fields_on_centimeter_project"] = True


def escape() -> None:
    event("ESC")
    event("ESC", "RELEASE")


def verify_cancel_entry() -> None:
    assert overlay._EDIT is None and not overlay.is_capturing()
    close(float(cube().location.x), 125.0)
    REPORT["escape_discards_uncommitted_field"] = True


def escape_popup_outside_area() -> None:
    global MOUSE
    assert overlay._POPUP is not None and overlay.is_capturing()
    MOUSE = (10, int(window().height) - 10)
    event("MOUSEMOVE", "NOTHING")
    escape()


def verify_popup_escape() -> None:
    assert overlay._POPUP is None and not overlay.is_capturing()
    REPORT["popup_escape_over_native_header"] = True


def click_native_header() -> None:
    assert overlay._POPUP is not None
    click_at(30, int(window().height) - 20)


def verify_native_header_dismiss() -> None:
    assert overlay._POPUP is None and not overlay.is_capturing()
    escape()
    REPORT["popup_dismiss_passes_native_header_click"] = True


def remember_group() -> None:
    global GROUP_KEY, GROUP_NAME
    row: layers.LayerRow
    for row in layers.layer_rows():
        if row.kind == "Group" and row.name != "Default Drawing":
            GROUP_KEY, GROUP_NAME = row.key, row.name
            break
    assert GROUP_KEY


def open_group_rename() -> None:
    rename_item("layer_select_" + GROUP_KEY)


def verify_group_rename() -> None:
    global GROUP_KEY, GROUP_NAME
    assert bpy.data.collections.get("LifecycleGroup") is not None
    assert bpy.data.collections.get(GROUP_NAME) is None
    GROUP_NAME = "LifecycleGroup"
    GROUP_KEY = "collection:" + GROUP_NAME
    REPORT["native_layer_rename_dialog"] = True


def open_object_rename() -> None:
    # Objects are listed in the layers tree; the panel below it is the Claude Code chat.
    rename_item("layer_select_object:" + OBJECT_NAME)


def verify_object_rename() -> None:
    global OBJECT_NAME
    assert bpy.data.objects.get("LifecycleCubeRenamed") is not None
    assert bpy.data.objects.get(OBJECT_NAME) is None
    OBJECT_NAME = "LifecycleCubeRenamed"
    REPORT["native_object_rename_dialog"] = True
    click("layer_lockobject:" + OBJECT_NAME)


def verify_object_locked() -> None:
    assert cube().hide_select and not cube().select_get()
    click("layer_lockobject:" + OBJECT_NAME)


def verify_object_unlocked() -> None:
    assert not cube().hide_select
    click("layer_select_object:" + OBJECT_NAME)
    REPORT["native_layer_object_lock"] = True


def layer_click(operation: str) -> Callable[[], None]:
    def execute() -> None:
        click("layer_" + operation + GROUP_KEY)

    return execute


def verify_hidden() -> None:
    assert bpy.data.collections[GROUP_NAME].hide_viewport


def verify_visible() -> None:
    assert not bpy.data.collections[GROUP_NAME].hide_viewport


def verify_locked() -> None:
    assert bpy.data.collections[GROUP_NAME].hide_select


def verify_unlocked() -> None:
    assert not bpy.data.collections[GROUP_NAME].hide_select
    click("layer_activecollection:Default Drawing")


def verify_default_active() -> None:
    assert get_state().active_group == "Default Drawing"


def verify_group_active() -> None:
    assert get_state().active_group == GROUP_NAME
    REPORT["native_layer_visibility_lock_activation"] = True
    controller.dispatch("tool", "select")
    controller.dispatch("option", ("cursor_active", False))
    controller.dispatch("edit", "undo")


def verify_undo_images() -> None:
    controller.dispatch("tool", "select")
    assert paint._IMAGES
    cached: Any
    for cached in paint._IMAGES.values():
        assert bpy.data.images.get(cached.name) == cached
    REPORT["undo_image_cache_valid"] = True
    bpy.ops.screen.screenshot(filepath=str(CHECKS / "ui-lifecycle-before-reset.png"))


def reset_workspace() -> None:
    global SESSION_BEFORE
    SESSION_BEFORE = controller._ACTIVE
    controller.dispatch("layout_reset")


def verify_reset() -> None:
    global SIDEBAR_WIDTHS, SESSION_BEFORE
    assert_layout()
    assert controller._ACTIVE is not SESSION_BEFORE
    assert paint._IMAGES
    close(float(bpy.context.scene.unit_settings.scale_length), 0.01, 1e-7)
    REPORT["reset_layout_recreates_session_and_ui"] = True
    measured: dict[str, int] = {}
    area: Any
    for area in window().screen.areas:
        measured[layout.role(area)] = int(area.width)
    SIDEBAR_WIDTHS = (measured["toolbox"], measured["layers"])
    assert (get_state().left_width, get_state().right_width) == (220, 280)
    SESSION_BEFORE = controller._ACTIVE
    controller.dispatch("layout_reset")


def verify_second_reset() -> None:
    assert_layout()
    assert controller._ACTIVE is not SESSION_BEFORE
    measured: dict[str, int] = {}
    area: Any
    for area in window().screen.areas:
        measured[layout.role(area)] = int(area.width)
    assert abs(measured["toolbox"] - SIDEBAR_WIDTHS[0]) <= 1
    assert abs(measured["layers"] - SIDEBAR_WIDTHS[1]) <= 1
    assert (get_state().left_width, get_state().right_width) == (220, 280)
    REPORT["repeated_layout_reset_has_no_width_drift"] = True
    click("tool_move")


def save_project() -> None:
    assert get_state().tool == "move"
    obj: Any = cube()
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.wm.save_as_mainfile(filepath=str(PROJECT))
    assert PROJECT.is_file()
    REPORT["project_saved"] = str(PROJECT.relative_to(ROOT))


def reload_project() -> None:
    global SESSION_BEFORE
    SESSION_BEFORE = controller._ACTIVE
    cube().location.x = -900
    bpy.ops.wm.open_mainfile(filepath=str(PROJECT), load_ui=False)


def verify_reopen() -> None:
    assert_layout()
    assert controller._ACTIVE is not SESSION_BEFORE
    close(float(cube().location.x), 125.0)
    close(float(bpy.context.scene.unit_settings.scale_length), 0.01, 1e-7)
    assert get_state().units == "mm"
    close(widget("transform_location_0")[1].value, 1250.0, 1e-3)
    REPORT["file_reopen_preserves_units_and_reinitializes_ui"] = True


def verify_post_load_input() -> None:
    close(float(cube().location.x), 125.1)
    REPORT["native_input_after_file_load"] = True
    bpy.ops.screen.screenshot(filepath=str(CHECKS / "ui-lifecycle-after-reopen.png"))
    REPORT["passed"] = True


STEPS: list[Step] = [
    Step("setup", setup),
    Step("meters display", verify_meters_display),
    Step("move typed entry", entry_step("transform_location_0", "2.5")),
    Step("verify move entry", verify_move_entry),
    Step("move plus", click_step("transform_location_0_plus")),
    Step("verify move plus", verify_move_plus),
    Step("move minus", click_step("transform_location_0_minus")),
    Step("verify move minus", verify_move_minus),
    Step("cursor display", verify_cursor_display),
    Step("cursor typed entry", entry_step("cursor_location_1", "0.75")),
    Step("verify cursor entry", verify_cursor_entry),
    Step("cursor plus", click_step("cursor_location_1_plus")),
    Step("verify cursor plus", verify_cursor_plus),
    Step("cursor minus", click_step("cursor_location_1_minus")),
    Step("verify cursor minus", verify_cursor_minus),
    Step("units dropdown", click_step("units")),
    Step("choose mm", select_mm),
    Step("verify mm display", verify_mm_display),
    Step("mm typed entry", entry_step("transform_location_0", "1250")),
    Step("verify mm entry", verify_mm_entry),
    Step("mm plus", click_step("transform_location_0_plus")),
    Step("verify mm plus", verify_mm_plus),
    Step("mm minus", click_step("transform_location_0_minus")),
    Step("verify mm minus", verify_mm_minus),
    Step("uncommitted entry", entry_step("transform_location_0", "999", False)),
    Step("cancel entry", escape),
    Step("verify cancel", verify_cancel_entry),
    Step("theme dropdown", click_step("theme")),
    Step("escape popup outside area", escape_popup_outside_area),
    Step("verify popup escape", verify_popup_escape),
    Step("theme dropdown again", click_step("theme")),
    Step("native header click", click_native_header),
    Step("verify native header dismissal", verify_native_header_dismiss),
    Step("add drawing group", click_step("layer_add")),
    Step("remember group", remember_group),
    Step("open group rename", open_group_rename),
    Step("type group name", native_text("LifecycleGroup")),
    Step("confirm group rename", confirm_dialog),
    Step("verify group rename", verify_group_rename),
    Step("open object rename", open_object_rename),
    Step("type object name", native_text("LifecycleCubeRenamed")),
    Step("confirm object rename", confirm_dialog),
    Step("verify object rename and lock", verify_object_rename),
    Step("verify object lock", verify_object_locked),
    Step("verify object unlock", verify_object_unlocked),
    Step("hide layer", layer_click("visible")),
    Step("verify hidden", verify_hidden),
    Step("show layer", layer_click("visible")),
    Step("verify visible", verify_visible),
    Step("lock layer", layer_click("lock")),
    Step("verify locked", verify_locked),
    Step("unlock layer", layer_click("lock")),
    Step("verify unlocked", verify_unlocked),
    Step("verify default active", verify_default_active),
    Step("activate group", layer_click("active")),
    Step("verify active and undo", verify_group_active),
    Step("verify undo image cache", verify_undo_images),
    Step("reset layout", reset_workspace, 1.5),
    Step("verify reset", verify_reset, 1.5),
    Step("verify second reset", verify_second_reset),
    Step("save project", save_project),
    Step("reopen project", reload_project, 1.5),
    Step("verify reopen", verify_reopen),
    Step("post-load increment", click_step("transform_location_0_plus")),
    Step("verify post-load input", verify_post_load_input),
]


def advance() -> float | None:
    global INDEX
    try:
        if INDEX < len(STEPS):
            current: Step = STEPS[INDEX]
            current.action()
            INDEX += 1
            return current.delay
    except Exception:
        REPORT["failed_step"] = STEPS[INDEX].name
        REPORT["error"] = traceback.format_exc()
        bpy.ops.screen.screenshot(filepath=str(CHECKS / "ui-lifecycle-failure.png"))
    (CHECKS / "ui-lifecycle-report.json").write_text(json.dumps(REPORT, indent=2), encoding="utf-8")
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(advance, first_interval=4.0, persistent=True)
