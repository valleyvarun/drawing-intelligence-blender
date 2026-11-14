"""Sketch Mesh: lines drawn with the tool wait as a sketch; confirming builds a mesh through them.

The lines are ordinary round pipes marked as sketch lines, so axis spokes, Erase, Trim, and undo work on them as
on any pipe. The sketch is whatever marked lines the scene holds, which keeps it right across undo, redo, and
saved drawings. Confirming replaces the lines with the mesh (tools/sketch_surface.py makes it); cancelling removes
them. Both are one undo step.
"""

from __future__ import annotations

import math
import time
from importlib import import_module
from typing import Any

from ..core.blender import bpy
from ..core.state import AppState, Vec3, get_state
from . import drawing, layers, pipe_edit
from .drawing import SKETCH
from .geometry import Sample
from .sketch_surface import SketchError, Surface, build

# numpy ships with Blender's Python; editors without that interpreter see it as a dynamic module.
np: Any = import_module("numpy")

# The built mesh's kind among drawn objects.
KIND: str = "sketch_mesh"
# Edges sharper than this stay crisp on a smooth-shaded mesh, such as a loft's caps and a solid's base.
SHARP: float = math.radians(40.0)
METHODS: dict[str, str] = {
    "loft": "lofted through",
    "patch": "spanning",
    "solid": "a solid under",
    "inflate": "inflated from",
    "shell": "wrapped around",
}


def lines(context: Any) -> list[Any]:
    """The sketch: finished Sketch Mesh lines that are visible and not locked."""
    result: list[Any] = []
    obj: Any
    for obj in context.scene.objects:
        if obj.get(SKETCH) and not obj.get("drawing_preview") and obj.visible_get():
            if not layers.object_locked(context, obj):
                result.append(obj)
    return result


# The last count: the lines drawn by then, when it expires, and the count.
_COUNTED: tuple[int, float, int] = (-1, 0.0, 0)


def count(context: Any) -> int:
    """How many lines the sketch has, for the panels.

    They ask on every redraw, and looking through a large drawing takes a moment, so the answer is kept for a
    quarter of a second unless a line was drawn or the sketch was built or cancelled.
    """
    global _COUNTED
    drawn: int = drawing.sketch_lines_drawn()
    now: float = time.monotonic()
    if _COUNTED[0] != drawn or now >= _COUNTED[1]:
        _COUNTED = (drawn, now + 0.25, len(lines(context)))
    return _COUNTED[2]


def reset() -> None:
    """Count the sketch afresh, as after undo or redo."""
    global _COUNTED
    _COUNTED = (-1, 0.0, 0)


def _remove(objects: list[Any]) -> None:
    """Delete stroke objects with the meshes and materials only they used."""
    obj: Any
    for obj in objects:
        data: Any = obj.data
        materials: list[Any] = [item for item in data.materials if item is not None]
        bpy.data.objects.remove(obj, do_unlink=True)
        if data.users == 0:
            bpy.data.meshes.remove(data)
        item: Any
        for item in materials:
            if item.users == 0:
                bpy.data.materials.remove(item)


def create_object(context: Any, surface: Surface, color: Vec3) -> Any:
    """The surface as a mesh object among the active drawing group's strokes, placed at the middle of its points.

    Like 3D prints and fills it is drawn work, so it is shown shaded, without the outlines of added shapes.
    """
    points: Any = np.asarray(surface.points, dtype=np.float64)
    centre: Any = (points.min(axis=0) + points.max(axis=0)) * 0.5
    mesh: Any = bpy.data.meshes.new("Sketch Mesh")
    mesh.from_pydata((points - centre).tolist(), [], surface.faces)
    if surface.smooth:
        mesh.polygons.foreach_set("use_smooth", [True] * len(mesh.polygons))
        mesh.set_sharp_from_angle(angle=SHARP)
    mesh.update()
    obj: Any = bpy.data.objects.new("Sketch Mesh", mesh)
    layers.link_stroke(obj, context)
    obj["drawing_kind"] = KIND
    obj.location = (float(centre[0]), float(centre[1]), float(centre[2]))
    obj.color = (*color, 1.0)
    obj.show_wire = False
    material: Any = bpy.data.materials.new("Sketch Mesh")
    material.diffuse_color = (*color, 1.0)
    shader: Any = material.node_tree.nodes.get("Principled BSDF")
    if shader is not None:
        shader.inputs["Base Color"].default_value = (*color, 1.0)
        shader.inputs["Roughness"].default_value = 0.8
    mesh.materials.append(material)
    return obj


def confirm(context: Any) -> Any:
    """Build the mesh through the sketch's lines and remove them; returns the mesh object, or None with a status."""
    state: AppState = get_state()
    strokes: list[Any] = lines(context)
    if not strokes:
        state.status = "Sketch Mesh: draw the lines of the surface first"
        return None
    layers.ensure_scene(context)
    if not layers.can_draw(context):
        state.status = "The active drawing group is hidden or locked"
        return None
    paths: list[list[Vec3]] = []
    radii: list[float] = []
    stroke: Any
    for stroke in strokes:
        samples: list[Sample] = pipe_edit.samples(stroke)
        if len(samples) >= 2:
            paths.append([sample.position for sample in samples])
            radii.append(max(sample.radius for sample in samples))
    error: SketchError
    try:
        surface: Surface = build(
            paths, max(radii, default=0.0), state.sketch.closed, state.sketch.curved, state.sketch.detail
        )
    except SketchError as error:
        state.status = "Sketch Mesh: " + str(error)
        return None
    obj: Any = create_object(context, surface, state.brushes["sketch"].color)
    _remove(strokes)
    reset()
    state.status = f"Sketch Mesh: {len(surface.faces)} faces {METHODS.get(surface.method, 'through')} {len(paths)} " + (
        "line" if len(paths) == 1 else "lines"
    )
    return obj


def cancel(context: Any) -> bool:
    """Remove the sketch's lines; False when there were none."""
    strokes: list[Any] = lines(context)
    if not strokes:
        return False
    _remove(strokes)
    reset()
    get_state().status = "Sketch cancelled"
    return True
