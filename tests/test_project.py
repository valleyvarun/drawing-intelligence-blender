"""Real Blender project/metadata and USD round-trip integration checks."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from drawing_app import api
from drawing_app.core import controller
from drawing_app.core.blender import bpy
from drawing_app.core.project import persist_scene, restore_scene, startup_file
from drawing_app.core.state import CONFIG, ROOT, get_state, load_preferences, restore_brushes, save_preferences
from drawing_app.tools import layers


def workspaces(path: Path) -> list[str]:
    """Workspace names stored in a .blend, read from a copy since Blender will not list the open file."""
    copy: Path = path.with_name(path.stem + "-inspect.blend")
    shutil.copyfile(path, copy)
    names: list[str] = []
    data_from: Any
    _data_to: Any
    try:
        with bpy.data.libraries.load(str(copy)) as (data_from, _data_to):
            name: str
            for name in data_from.workspaces:
                names.append(name)
    finally:
        library: Any
        for library in list(bpy.data.libraries):
            if Path(bpy.path.abspath(library.filepath)) == copy:
                bpy.data.libraries.remove(library)
        copy.unlink()
    return names


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
        # Ctrl+G's command: a new group beside the active one becomes the active group.
        controller.dispatch("layer", ("sibling", ""))
        top: Any = bpy.data.collections[get_state().active_group]
        assert top.name != "Default Drawing" and top.get("drawing_group"), top.name
        assert (
            top.name in bpy.context.scene.collection.children and get_state().status == "New active group: " + top.name
        )
        nested: str = layers.create_group(bpy.context, "Nested", f"collection:{top.name}")
        assert get_state().active_group == "Nested"
        controller.dispatch("layer", ("sibling", ""))
        beside: Any = bpy.data.collections[get_state().active_group]
        assert beside.name not in ("Nested", top.name) and beside.name in top.children, beside.name
        assert beside.name not in bpy.context.scene.collection.children
        # What is made next goes into the new active group: a stroke, a shape, and a drawing plane.
        stroke: dict[str, object] = api.draw_stroke([[0, 0, 0], [1, 0, 0]])
        shape: dict[str, object] = api.add_primitive("cube")
        sheet: Any = layers.create_plane(bpy.context)
        assert stroke["group"] == beside.name and shape["group"] == beside.name, (stroke, shape)
        assert sheet.users_collection[0] == beside
        assert bpy.data.objects[str(stroke["name"])].users_collection[0] == layers.stroke_collection(beside)
        item: Any
        for item in (bpy.data.objects[str(stroke["name"])], bpy.data.objects[str(shape["name"])], sheet):
            bpy.data.objects.remove(item, do_unlink=True)
        assert nested == "collection:Nested"
        collection: Any
        for collection in [*top.children_recursive, top]:
            bpy.data.collections.remove(collection)
        layers.ensure_scene(bpy.context)
        assert get_state().active_group == "Default Drawing"
        get_state().drawing_plane = False
        get_state().active_plane = "Ground Plane"
        report["ctrl_g_group_beside_the_active_one"] = True
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
        orphan: Any = bpy.data.materials.new("Project Unused Material")
        notes: Any = bpy.data.texts.new("Project Notes")
        notes.write("kept")
        layout_path: Path = checks / "round-trip-layout.blend"
        with startup_file():
            bpy.ops.wm.save_as_mainfile(filepath=str(layout_path), copy=True)
        assert workspaces(layout_path), "The startup-file path must keep the application's screen"
        bpy.ops.wm.save_as_mainfile(filepath=str(project_path))
        assert Path(bpy.data.filepath) == project_path, bpy.data.filepath
        # Blender keeps its own layout for files without screens, as it does for library files.
        assert not workspaces(project_path), "Saved drawings must not carry the application's layout"
        report["blend_without_application_layout"] = True
        assert orphan.users == 0
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
        assert bpy.data.materials.get("Project Unused Material") is not None
        assert bpy.data.texts["Project Notes"].as_string() == "kept"
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
