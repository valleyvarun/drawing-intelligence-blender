"""Drawing tools: strokes, the active tool, colours, brushes, groups, and drawing planes."""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any, Literal

from ..core.blender import Vector, bpy
from ..core.state import DRAWING_TOOLS, AppState, Brush, Vec3, get_state, save_preferences
from .common import color as parse_color
from .common import group_of, hex_color, number, position, to_units, vector, viewport
from .registry import JsonObject, ToolError, tool

# Grease Pencil strokes use Blender's own brushes in the app; this is the default for scripted lines.
GREASE_RADIUS: float = 0.01


def _stroke_points(points: list[list[float]]) -> list[Vec3]:
    if len(points) < 2:
        raise ToolError("A stroke needs at least two points")
    result: list[Vec3] = []
    point: list[float]
    for point in points:
        result.append(position(point, "points"))
    return result


def _radius(value: float | None, default: float) -> float:
    radius: float = default if value is None else number(value, "radius")
    if not 0.0 < radius <= 100.0:
        raise ToolError("radius must be between 0 and 100 metres")
    return radius


def _mesh_stroke(
    kind: Literal["pipe", "paint"], points: list[Vec3], brush: Brush, normal: Vec3, closed: bool
) -> tuple[Any, int]:
    from ..tools import drawing, layers
    from ..tools.geometry import Sample, resample

    if closed and points[0] != points[-1]:
        points = [*points, points[0]]
    radius: float = to_units(brush.radius)
    samples: list[Sample] = [Sample(points[0], radius, brush.hardness, brush.opacity, normal)]
    point: Vec3
    for point in points[1:]:
        samples.extend(resample(samples[-1], Sample(point, radius, brush.hardness, brush.opacity, normal)))
    if len(samples) < 2:
        raise ToolError("The points are too close together for a stroke of this radius")
    if not layers.can_draw(bpy.context):
        raise ToolError("The active drawing group is hidden or locked")
    obj: Any = drawing.stroke_object(bpy.context, kind, brush)
    drawing.write_mesh(obj, samples, brush, kind)
    drawing.store_samples(obj, samples)
    return obj, len(samples)


def _grease_stroke(points: list[Vec3], radius: float, stroke_color: Vec3, closed: bool) -> Any:
    from ..tools import layers, pencil

    if not layers.can_draw(bpy.context):
        raise ToolError("The active drawing group is hidden or locked")
    obj: Any = pencil.pencil_object(bpy.context)
    if layers.object_locked(bpy.context, obj) or not obj.visible_get():
        raise ToolError(f"Unlock and show {obj.name} to add Grease Pencil strokes")
    layer: Any = obj.data.layers.get(pencil.LAYER) or obj.data.layers.new(pencil.LAYER, set_active=False)
    frame: Any = layer.current_frame()
    if frame is None:
        frame = layer.frames.new(int(bpy.context.scene.frame_current))
    stroke_drawing: Any = frame.drawing
    material: int = pencil.material_slot(obj, stroke_color, False)
    inverse: Any = obj.matrix_world.inverted_safe()
    stroke_drawing.add_strokes([len(points)])
    stroke: Any = stroke_drawing.strokes[len(stroke_drawing.strokes) - 1]
    index: int
    point: Vec3
    for index, point in enumerate(points):
        target: Any = stroke.points[index]
        target.position = inverse @ Vector(point)
        target.radius = to_units(radius)
        target.opacity = 1.0
    stroke.material_index = material
    stroke.cyclic = closed
    stroke_drawing.tag_positions_changed()
    obj.data.update_tag()
    return obj


@tool(undo="Draw stroke")
def draw_stroke(
    points: list[list[float]],
    kind: Literal["pipe", "paint", "grease"] = "pipe",
    radius: float | None = None,
    color: str | None = None,
    shape: Literal["circle", "square", "triangle"] | None = None,
    closed: bool = False,
    normal: list[float] | None = None,
) -> JsonObject:
    """Draw a stroke through 3D points in the active drawing group, like one drawn by hand.

    A pipe is a tube, paint is a flat brush ribbon facing `normal`, and grease is a Grease Pencil
    line. Give enough points for curves to look smooth; pipe and paint strokes are resampled between
    points like hand-drawn ones, so straight segments need only their end points.

    Args:
        points: The path as [x, y, z] points in metres; at least two.
        kind: "pipe", "paint", or "grease".
        radius: Half the stroke width in metres; defaults to the tool's brush size (1 cm for grease).
        color: Hex colour such as "#FF8000" or a preset name; defaults to the tool's colour.
        shape: Cross-section of pipe and paint strokes; defaults to the tool's brush shape.
        closed: Connect the last point back to the first.
        normal: Direction a paint stroke faces, as [x, y, z]; defaults to up.
    """
    if kind not in ("pipe", "paint", "grease"):
        raise ToolError("kind must be pipe, paint, or grease")
    if shape not in (None, "circle", "square", "triangle"):
        raise ToolError("shape must be circle, square, or triangle")
    path: list[Vec3] = _stroke_points(points)
    state: AppState = get_state()
    brush: Brush = deepcopy(state.brushes[kind])
    if color is not None:
        brush.color = parse_color(color)
    if kind == "grease":
        obj: Any = _grease_stroke(path, _radius(radius, GREASE_RADIUS), brush.color, closed)
        return {"name": str(obj.name), "kind": kind, "group": group_of(obj), "points": len(path)}
    brush.radius = _radius(radius, brush.radius)
    if shape is not None:
        brush.shape = shape
    facing: Vec3 = (0.0, 0.0, 1.0)
    if normal is not None:
        direction: Any = Vector(vector(normal, "normal"))
        if direction.length < 1e-9:
            raise ToolError("normal must not be zero")
        direction.normalize()
        facing = (float(direction.x), float(direction.y), float(direction.z))
    count: int
    stroke: Any
    stroke, count = _mesh_stroke(kind, path, brush, facing, closed)
    return {
        "name": str(stroke.name),
        "kind": kind,
        "group": group_of(stroke),
        "samples": count,
        "radius": brush.radius,
        "color": hex_color(brush.color),
    }


@tool()
def set_tool(tool: Literal["select", "move", "rotate", "scale", "paint", "pipe", "printer", "grease"]) -> JsonObject:
    """Switch the app's active tool, as clicking it in the Tool Box does.

    Args:
        tool: "select", "move", "rotate", "scale", "paint", "pipe", "printer" (3D Printer, which pours
            material that settles into a mesh), or "grease" (Grease Pencil Draw).
    """
    from ..core import controller

    controller.dispatch("tool", tool)
    return {"tool": get_state().tool}


def _colors() -> JsonObject:
    state: AppState = get_state()
    result: JsonObject = {"toolbox": hex_color(state.color)}
    name: str
    for name in DRAWING_TOOLS:
        result[name] = hex_color(state.brushes[name].color)
    return result


@tool()
def set_color(color: str, tool: Literal["all", "paint", "pipe", "printer", "grease"] = "all") -> JsonObject:
    """Set the drawing colour for every drawing tool, like the Tool Box picker, or for one tool.

    Args:
        color: Hex colour such as "#FF8000" or a preset name.
        tool: "all" for every drawing tool, or the one tool to recolour.
    """
    from ..core import controller

    controller.dispatch("color", ("toolbox" if tool == "all" else tool, "rgb", parse_color(color)))
    return _colors()


@tool()
def set_brush(
    tool: Literal["paint", "pipe"],
    radius: float | None = None,
    shape: Literal["circle", "square", "triangle"] | None = None,
    hardness: float | None = None,
    opacity: float | None = None,
) -> JsonObject:
    """Change the brush of the paint or pipe tool; omitted settings are kept.

    Args:
        tool: "paint" or "pipe".
        radius: Brush radius in metres.
        shape: Cross-section: "circle", "square", or "triangle".
        hardness: Edge hardness from 0 to 1 (paint).
        opacity: Opacity from 0 to 1.
    """
    from ..core import controller

    brush: Brush = get_state().brushes[tool]
    if radius is not None:
        brush.radius = _radius(radius, brush.radius)
    if shape is not None:
        brush.shape = shape
    if hardness is not None:
        brush.hardness = max(0.0, min(1.0, number(hardness, "hardness")))
    if opacity is not None:
        brush.opacity = max(0.0, min(1.0, number(opacity, "opacity")))
    save_preferences()
    controller.redraw()
    return {
        "tool": tool,
        "radius": brush.radius,
        "shape": brush.shape,
        "hardness": brush.hardness,
        "opacity": brush.opacity,
        "color": hex_color(brush.color),
    }


@tool(undo="Create drawing group")
def create_group(name: str, activate: bool = True) -> JsonObject:
    """Create a drawing group; new strokes go to the active group.

    Args:
        name: Group name; Blender adds a number if it is taken.
        activate: Make the new group the active one.
    """
    from ..core import controller
    from ..tools import layers

    previous: str = get_state().active_group
    controller.leave_pencil()
    key: str = layers.create_group(bpy.context, name)
    if not activate:
        get_state().active_group = previous
    return {"name": key.removeprefix("collection:"), "active_group": get_state().active_group}


@tool()
def set_active_group(name: str) -> JsonObject:
    """Make a drawing group active, so new strokes go into it.

    Args:
        name: Group name, as listed by scene_info.
    """
    from ..core import controller

    collection: Any = bpy.data.collections.get(name)
    if collection is None or not collection.get("drawing_group"):
        raise ToolError(f"No drawing group named {name!r}")
    # The layer command records its own undo step and moves Grease Pencil Draw mode along.
    controller.dispatch("layer", ("active", f"collection:{name}"))
    return {"active_group": get_state().active_group}


@tool(undo="Create drawing plane")
def create_drawing_plane(
    location: list[float] | None = None, rotation: list[float] | None = None, size: float = 2.0
) -> JsonObject:
    """Add a drawing plane and draw on it: strokes drawn by hand then land on this plane.

    Args:
        location: Centre as [x, y, z] in metres; defaults to the 3D cursor when placed, else the origin.
        rotation: Orientation as [x, y, z] Euler angles in degrees; the default lies flat (facing +Z).
        size: Edge length in metres.
    """
    from ..core import controller
    from ..tools import layers, pencil

    edge: float = number(size, "size")
    if edge <= 0.0:
        raise ToolError("size must be a positive number of metres")
    controller.leave_pencil()
    plane: Any = layers.create_plane(bpy.context)
    if location is not None:
        plane.location = position(location, "location")
    if rotation is not None:
        angles: Vec3 = vector(rotation, "rotation")
        plane.rotation_euler = (math.radians(angles[0]), math.radians(angles[1]), math.radians(angles[2]))
    half: float = to_units(edge) / 2.0
    plane.scale = (half, half, 1.0)
    if get_state().tool == "grease":
        context: Any
        with viewport() as context:
            pencil.refresh(context)
    return {"name": str(plane.name), "group": group_of(plane), "active_plane": get_state().active_plane}
