"""Real Blender project/metadata and USD round-trip integration checks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from drawing_app.core.blender import bpy
from drawing_app.core.project import persist_scene, restore_scene
from drawing_app.core.state import CONFIG, ROOT, get_state, load_preferences, restore_brushes, save_preferences
from drawing_app.tools import layers


def main() -> None:
    checks: Path = ROOT / ".checks"
    checks.mkdir(exist_ok=True)
    report: dict[str, bool | str] = {"blender": str(bpy.app.version_string)}
    preference_path: Path = CONFIG / "drawing-preferences.json"
    original_preferences: bytes | None = preference_path.read_bytes() if preference_path.exists() else None
    try:
        get_state().zoom_speed = 3.25
        get_state().brushes["pipe"].radius = 0.061
        get_state().brushes["pipe"].pressure_size = True
        save_preferences()
        get_state().zoom_speed = 0.5
        get_state().brushes["pipe"].radius = 0.01
        load_preferences()
        assert get_state().zoom_speed == 3.25
        assert get_state().brushes["pipe"].radius == 0.061
        assert get_state().brushes["pipe"].pressure_size
        report["preferences_round_trip"] = True
        restore_brushes({"pipe": {"radius": -1, "opacity": 9, "hardness": float("nan")}})
        assert get_state().brushes["pipe"].radius > 0.0
        assert get_state().brushes["pipe"].opacity == 1.0
        report["invalid_preferences_bounded"] = True
        group: str = layers.create_group(bpy.context, "Project Group")
        layers.activate(bpy.context, group)
        plane: Any = layers.create_plane(bpy.context)
        plane.location.z = 1.4
        get_state().cursor_active = True
        get_state().cursor_placed = True
        bpy.context.scene.cursor.location = (2.0, 3.0, 4.0)
        get_state().brushes["paint"].opacity = 0.37
        get_state().pencil.brush = "Ink Pen"
        get_state().pencil.placement = "SURFACE"
        get_state().pencil.plane = "AXIS_Z"
        bpy.context.scene.unit_settings.scale_length = 0.01
        persist_scene()
        project_path: Path = checks / "round-trip.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(project_path))
        get_state().cursor_placed = False
        get_state().brushes["paint"].opacity = 1.0
        get_state().pencil.brush = "Pencil"
        get_state().pencil.placement = "ORIGIN"
        get_state().pencil.plane = "VIEW"
        bpy.ops.wm.open_mainfile(filepath=str(project_path), load_ui=False)
        restore_scene()
        assert get_state().cursor_placed
        assert abs(get_state().brushes["paint"].opacity - 0.37) < 1e-8
        assert get_state().pencil.brush == "Ink Pen"
        assert get_state().pencil.placement == "SURFACE"
        assert get_state().pencil.plane == "AXIS_Z"
        report["native_pencil_project_settings_round_trip"] = True
        assert tuple(bpy.context.scene.cursor.location) == (2.0, 3.0, 4.0)
        assert bpy.data.collections.get("Project Group") is not None
        assert abs(layers.selected_plane().location.z - 1.4) < 1e-6
        assert abs(bpy.context.scene.unit_settings.scale_length - 0.01) < 1e-8
        report["blend_geometry_layers_canvas_metadata"] = True
        report["nonmeter_scene_units_preserved"] = True
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=(1.0, 2.0, 0.5))
        cube: Any = bpy.context.object
        cube.name = "USD Verification Cube"
        usd_path: Path = checks / "round-trip.usdc"
        bpy.ops.wm.usd_export(filepath=str(usd_path), export_materials=True)
        assert usd_path.stat().st_size > 0
        bpy.data.objects.remove(cube, do_unlink=True)
        bpy.ops.wm.usd_import(filepath=str(usd_path), import_cameras=False, import_lights=False)
        obj: Any
        imported: list[Any] = []
        for obj in bpy.context.scene.objects:
            if obj.type == "MESH" and len(obj.data.vertices) == 8:
                imported.append(obj)
        assert imported, "USD importer did not restore the verification cube"
        assert abs(imported[0].location.x - 1.0) < 1e-4
        assert abs(imported[0].dimensions.x - 1.0) < 1e-4
        report["usd_geometry_units_round_trip"] = True
        del bpy.context.scene["drawing_app_state"]
        get_state().cursor_placing = True
        restore_scene()
        assert not get_state().cursor_active and not get_state().cursor_placing
        assert not get_state().cursor_placed and get_state().active_plane == "Ground Plane"
        report["ordinary_project_resets_canvas_state"] = True
        get_state().cursor_active = True
        bpy.ops.drawing.new_project("EXEC_DEFAULT")
        assert bpy.context.scene.get("drawing_app_state"), "New-project undo state has no canvas metadata"
        get_state().cursor_active = False
        restore_scene()
        assert get_state().cursor_active and not get_state().cursor_placed
        assert get_state().active_group == "Default Drawing"
        assert bpy.context.scene.get("drawing_unsaved_project")
        report["new_project_checkpoint_preserves_cursor_and_save_as"] = True
        report["passed"] = True
    finally:
        if original_preferences is not None:
            preference_path.write_bytes(original_preferences)
        elif preference_path.exists():
            preference_path.unlink()
        (checks / "project-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("Project integration passed")


main()
