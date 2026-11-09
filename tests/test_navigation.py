"""Native viewport settings, native-navigation routing, and mathutils-backed camera regressions."""

from __future__ import annotations

import json
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import drawing_app.viewport.guides as guides
import drawing_app.viewport.navigation as navigation
from drawing_app.core.blender import Matrix, Vector, bpy, view3d_utils
from drawing_app.core.state import ROOT, get_state
from drawing_app.viewport import configure_viewport, grid_spacing, install_guides, keymap, remove_guides, ruler_origin
from drawing_app.viewport.guides import Label, _labels, label_interval
from drawing_app.viewport.navigation import (
    NavigationController,
    align_axis,
    axis_handles,
    orbit_view,
    pan_view,
    zoom_view,
)

REPORT: dict[str, Any] = {"blender": bpy.app.version_string}


class RegionView:
    """Camera stub; projection calculations use the installed Blender mathutils."""

    def __init__(self) -> None:
        self.view_location: Any = Vector((2.0, -1.0, 0.5))
        self.view_rotation: Any = Vector((8.0, -10.0, 7.0)).to_track_quat("Z", "Y")
        self.view_distance: float = 12.0
        self.view_perspective: str = "PERSP"
        self.view_matrix: Any = Matrix.Identity(4)
        self.window_matrix: Any = Matrix.Identity(4)
        self.perspective_matrix: Any = Matrix.Identity(4)
        self.update()

    @property
    def is_perspective(self) -> bool:
        return self.view_perspective == "PERSP"

    def update(self) -> None:
        world: Any = self.view_rotation.to_matrix().to_4x4()
        world.translation = self.view_location + self.view_rotation @ Vector((0.0, 0.0, self.view_distance))
        self.view_matrix = world.inverted()
        if self.is_perspective:
            self.window_matrix = Matrix(
                ((1.5, 0.0, 0.0, 0.0), (0.0, 2.0, 0.0, 0.0), (0.0, 0.0, -1.00002, -0.0200002), (0.0, 0.0, -1.0, 0.0))
            )
        else:
            self.window_matrix = Matrix(
                (
                    (1.5 / self.view_distance, 0.0, 0.0, 0.0),
                    (0.0, 2.0 / self.view_distance, 0.0, 0.0),
                    (0.0, 0.0, -0.001, 0.0),
                    (0.0, 0.0, 0.0, 1.0),
                )
            )
        self.perspective_matrix = self.window_matrix @ self.view_matrix


def project(rv3d: RegionView, point: Any) -> tuple[float, float]:
    clip: Any = rv3d.perspective_matrix @ point.to_4d()
    return float(clip.x / clip.w), float(clip.y / clip.w)


def event(kind: str, value: str = "PRESS", x: float = 500.0, y: float = 400.0, shift: bool = False) -> Any:
    return SimpleNamespace(type=kind, value=value, mouse_x=x, mouse_y=y, shift=shift, ctrl=False)


def redraw() -> None:
    pass


def area_pointer() -> int:
    return 1


def guide_undo_lifecycle() -> None:
    """Dispatch installed handlers after real Image ID removal without a GPU."""
    handlers: list[Any]
    for handlers in (bpy.app.handlers.undo_post, bpy.app.handlers.redo_post):
        assert handlers.count(guides._invalidate_text_resources) == 1
        image: Any = bpy.data.images.new("Navigation Undo Atlas", width=8, height=8)
        guides._IMAGE = image
        guides._TEXTURE = object()
        guides._TEXT_SHADER = object()
        bpy.data.images.remove(image)
        invalid: bool = False
        try:
            image.as_pointer()
        except ReferenceError:
            invalid = True
        assert invalid
        guides._invalidate_text_resources(None)
        assert guides._IMAGE is None and guides._TEXTURE is None and guides._TEXT_SHADER is None
    REPORT["registered_undo_redo_invalidates_removed_image_resources"] = True


def controller_checks(region: Any) -> None:
    rv3d: RegionView = RegionView()
    area: Any = SimpleNamespace(
        type="VIEW_3D",
        spaces=SimpleNamespace(active=SimpleNamespace(region_3d=rv3d)),
        tag_redraw=redraw,
        as_pointer=area_pointer,
    )
    area.spaces.active.clip_start = 0.001
    area.spaces.active.clip_end = 100000.0
    guides.update_clipping(area)
    assert area.spaces.active.clip_end >= 1000.0
    assert area.spaces.active.clip_start > 0.001
    local_far: float = area.spaces.active.clip_end
    rv3d.view_location.x += 1000000.0
    rv3d.view_location.y -= 1000000.0
    rv3d.update()
    guides.update_clipping(area)
    assert abs(area.spaces.active.clip_end - local_far) < 1e-4
    rv3d.view_location = Vector((2.0, -1.0, 0.5))
    rv3d.update()
    REPORT["stable_depth_range_independent_of_xy_travel"] = True
    controller: NavigationController = NavigationController()
    saved_preferences: Callable[[], None] = navigation.save_preferences
    navigation.save_preferences = redraw
    try:
        get_state().navigation = "orbit"
        get_state().zoom_speed = 1.0
        # At the default speed the wheel belongs to Blender's view3d.zoom.
        assert not controller.handle(bpy.context, event("WHEELUPMOUSE"), area, region)
        assert rv3d.view_distance == 12.0
        get_state().zoom_speed = 2.0
        assert controller.handle(bpy.context, event("WHEELUPMOUSE"), area, region)
        assert abs(rv3d.view_distance / 12.0 - math.pow(navigation.WHEEL_STEP, -2.0)) < 1e-8
        pivot: Any = rv3d.view_location.copy()
        distance: float = rv3d.view_distance
        assert controller.handle(bpy.context, event("WHEELUPMOUSE", shift=True), area, region)
        assert get_state().zoom_speed == 2.5 and rv3d.view_distance == distance
        assert (rv3d.view_location - pivot).length < 1e-8
        REPORT["native_wheel_step_and_adjustable_speed"] = True
        kind: str
        for kind in ("RIGHTMOUSE", "MIDDLEMOUSE"):
            assert not controller.handle(bpy.context, event(kind), area, region)
            assert not controller.active
        get_state().navigation = "fly"
        rv3d.view_distance = 50.0
        assert not controller.handle(bpy.context, event("RIGHTMOUSE"), area, region)
        assert abs(float(bpy.context.preferences.inputs.walk_navigation.walk_speed) - 20.0) < 1e-4
        REPORT["navigation_drags_pass_to_native_operators"] = True
        REPORT["fly_speed_follows_view_scale"] = True
        assert controller.handle(bpy.context, event("LEFTMOUSE", x=76.0, y=76.0), area, region)
        assert controller.viewport_context == (area, region)
        assert controller.handle(bpy.context, event("MOUSEMOVE", "NOTHING", x=96.0, y=86.0), area, region)
        assert controller.handle(bpy.context, event("LEFTMOUSE", "RELEASE", x=96.0, y=86.0), area, region)
        assert not controller.active and controller.viewport_context is None
        REPORT["captured_gizmo_navigation_release"] = True
        keymap.install()
        get_state().navigation = "orbit"
        keymap.update()
        bindings: list[str] = keymap.active_bindings()
        assert "3D View:RIGHTMOUSE:view3d.rotate" in bindings and "3D View:MIDDLEMOUSE:view3d.move" in bindings
        assert "Object Mode:RIGHTMOUSE:view3d.rotate" in bindings
        assert "Grease Pencil Draw Mode:RIGHTMOUSE:view3d.rotate" in bindings
        assert not any(binding.endswith("view3d.walk") for binding in bindings)
        get_state().navigation = "fly"
        keymap.update()
        assert "3D View:RIGHTMOUSE:view3d.walk" in keymap.active_bindings()
        REPORT["native_navigation_keymap_bindings"] = True
        get_state().tool = "grease"
        assert not controller.handle(bpy.context, event("F"), area, region)
        REPORT["grease_pencil_native_radius_shortcut"] = True
    finally:
        navigation.save_preferences = saved_preferences
        controller.destroy()
        get_state().navigation = "orbit"
        get_state().zoom_speed = 1.0
        get_state().tool = "select"


def main() -> None:
    area: Any
    for area in bpy.context.screen.areas:
        if area.type == "VIEW_3D":
            configure_viewport(area)
            get_state().cursor_active = True
            configure_viewport(area)
            assert area.spaces.active.overlay.show_cursor
            get_state().theme = "dark"
            configure_viewport(area)
            assert area.spaces.active.shading.type == "SOLID"
            get_state().theme = "light"
            get_state().cursor_active = False
            configure_viewport(area)
    REPORT["native_workbench_properties"] = True
    bpy.ops.mesh.primitive_cube_add(size=1.0)
    cube: Any = bpy.context.active_object
    cube.name = "Navigation Probe"
    install_guides()
    assert cube.show_wire and cube.show_all_edges
    cube["drawing_stroke"] = True
    install_guides()
    assert not cube.show_wire and not cube.show_all_edges
    bpy.data.objects.remove(cube, do_unlink=True)
    guide_undo_lifecycle()
    remove_guides()
    assert guides._invalidate_text_resources not in bpy.app.handlers.undo_post
    assert guides._invalidate_text_resources not in bpy.app.handlers.redo_post
    remove_guides()
    REPORT["guide_lifecycle_and_native_wire"] = True
    region: Any = SimpleNamespace(width=1000, height=800, x=0, y=0)
    rv3d: RegionView = RegionView()
    original_location: Any = rv3d.view_location.copy()
    orbit_view(rv3d, 100.0, 60.0, 1000.0, 800.0)
    assert (rv3d.view_location - original_location).length < 1e-6
    assert abs(rv3d.view_distance - 12.0) < 1e-8
    REPORT["orbit_preserves_pivot_distance"] = True
    perspective: str
    for perspective in ("PERSP", "ORTHO"):
        rv3d.view_perspective = perspective
        rv3d.update()
        anchor: Any = view3d_utils.region_2d_to_location_3d(region, rv3d, (730.0, 315.0), rv3d.view_location)
        before: tuple[float, float] = project(rv3d, anchor)
        zoom_view(region, rv3d, (730.0, 315.0), 0.88)
        after: tuple[float, float] = project(rv3d, anchor)
        assert math.dist(before, after) < 2e-5, (perspective, before, after)
        location: Any = rv3d.view_location.copy()
        pan_view(region, rv3d, (500.0, 400.0), (550.0, 430.0))
        assert (rv3d.view_location - location).length > 0.01
    REPORT["cursor_zoom_anchor_perspective_and_orthographic"] = True
    REPORT["pan"] = True
    controller_checks(region)
    axis: int
    sign: int
    for axis in range(3):
        for sign in (-1, 1):
            align_axis(rv3d, axis, sign)
            backward: Any = rv3d.view_rotation @ Vector((0.0, 0.0, 1.0))
            assert abs(backward[axis] - sign) < 1e-5
            assert rv3d.view_location.length < 1e-6
    assert len(axis_handles(rv3d)) == 6
    REPORT["six_origin_axis_views"] = True
    rv3d.view_perspective = "ORTHO"
    rv3d.view_distance = 12.0
    align_axis(rv3d, 2, 1)
    interval: float | None = label_interval(rv3d.perspective_matrix, (0.0, 0.0, 0.0), 0, 1.0, 1000.0, 800.0)
    assert interval is not None and 1.0 <= interval <= 2.0
    assert label_interval(rv3d.perspective_matrix, (0.0, 0.0, 0.0), 2, 1.0, 1000.0, 800.0) is None
    REPORT["projected_ruler_labels_and_end_on_axis"] = True
    context: Any = SimpleNamespace(
        scene=SimpleNamespace(
            unit_settings=SimpleNamespace(scale_length=1.0), cursor=SimpleNamespace(location=Vector((8.0, 9.0, 3.0)))
        )
    )
    fake_area: Any = SimpleNamespace(
        spaces=SimpleNamespace(active=SimpleNamespace(region_3d=rv3d)), as_pointer=area_pointer
    )
    labels: list[Label] = _labels(context, fake_area, region, (0.0, 0.0, 0.0), 1.0)
    negative: bool = False
    label: Label
    for label in labels:
        negative = negative or label.text.startswith("-")
        assert label.text != "0"
    assert negative
    # Away from world 0, the zero coordinate is labelled; only the ruler centre is skipped.
    offset_origin: tuple[float, float, float] = (0.3, 0.3, 0.0)
    offset_labels: list[Label] = _labels(context, fake_area, region, offset_origin, 1.0)
    offset_label: Label
    assert any(offset_label.text == "0" for offset_label in offset_labels)
    for offset_label in offset_labels:
        assert offset_label.position != offset_origin
    REPORT["world_signed_labels_without_origin_zero"] = True
    rv3d.view_perspective = "PERSP"
    rv3d.view_rotation = Vector((1.0, -1.0, 1.0)).to_track_quat("Z", "Y")
    rv3d.view_distance = 500.0
    rv3d.view_location = Vector((100.0, 200.0, 0.0))
    rv3d.update()
    origin: tuple[float, float, float] = ruler_origin(context, fake_area, region)
    camera: Any = rv3d.view_matrix.inverted().translation
    assert origin[2] == 0.0 and math.hypot(origin[0] - camera.x, origin[1] - camera.y) <= 20.0001
    step: float = grid_spacing(context, fake_area, region)
    orbit_view(rv3d, 110.0, -65.0, 1000.0, 800.0)
    assert grid_spacing(context, fake_area, region) == step
    get_state().cursor_active = get_state().cursor_placed = True
    assert ruler_origin(context, fake_area, region) == (8.0, 9.0, 3.0)
    get_state().cursor_active = get_state().cursor_placed = False
    REPORT["planar_twenty_meter_cap_and_cursor_override"] = True
    REPORT["orbit_stable_grid_spacing"] = True
    controller: NavigationController = NavigationController()
    controller.destroy()
    REPORT["controller_touch_lifecycle"] = True
    REPORT["passed"] = True
    report_path: Path = ROOT / ".checks/navigation-report.json"
    report_path.write_text(json.dumps(REPORT, indent=2), encoding="utf-8")
    print(json.dumps(REPORT, indent=2))


main()
