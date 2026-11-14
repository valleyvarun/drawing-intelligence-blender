"""Sketch Mesh inside a real Blender (background mode): sketch lines, every kind of surface, and the commands."""

from __future__ import annotations

import json
import math
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

from drawing_app import api
from drawing_app.core import controller
from drawing_app.core.blender import bmesh, bpy
from drawing_app.core.state import ROOT, Brush, Vec3, get_state
from drawing_app.tools import drawing, layers, sketch_mesh
from drawing_app.tools.geometry import Sample, resample
from drawing_app.ui import overlay

JsonObject = dict[str, object]


def circle(radius: float, height: float, count: int = 96, sweep: float = math.tau) -> list[list[float]]:
    points: list[list[float]] = []
    index: int
    for index in range(count + 1):
        angle: float = sweep * index / count
        points.append([radius * math.cos(angle), radius * math.sin(angle), height])
    return points


def arch(start: Vec3, end: Vec3, height: float, count: int = 64) -> list[list[float]]:
    points: list[list[float]] = []
    index: int
    for index in range(count + 1):
        t: float = index / count
        points.append(
            [
                start[0] + (end[0] - start[0]) * t,
                start[1] + (end[1] - start[1]) * t,
                start[2] + (end[2] - start[2]) * t + height * math.sin(math.pi * t),
            ]
        )
    return points


def polyline(*corners: Vec3) -> list[list[float]]:
    return [list(corner) for corner in corners]


def box_edges(width: float, depth: float, height: float) -> list[list[list[float]]]:
    edges: list[list[list[float]]] = []
    level: float
    for level in (0.0, height):
        edges.append(polyline((0, 0, level), (width, 0, level)))
        edges.append(polyline((width, 0, level), (width, depth, level)))
        edges.append(polyline((width, depth, level), (0, depth, level)))
        edges.append(polyline((0, depth, level), (0, 0, level)))
    corner: tuple[float, float]
    for corner in ((0.0, 0.0), (width, 0.0), (width, depth), (0.0, depth)):
        edges.append(polyline((corner[0], corner[1], 0.0), (corner[0], corner[1], height)))
    return edges


def measure(name: str) -> dict[str, float]:
    """Counts of the mesh's faces and of its open and badly shared edges, and its size."""
    obj: Any = bpy.data.objects[name]
    mesh: Any = bmesh.new()
    mesh.from_mesh(obj.data)
    open_edges: int = 0
    crowded: int = 0
    edge: Any
    for edge in mesh.edges:
        if len(edge.link_faces) == 1:
            open_edges += 1
        elif len(edge.link_faces) != 2 or not edge.is_contiguous:
            crowded += 1
    result: dict[str, float] = {
        "faces": float(len(mesh.faces)),
        "open": float(open_edges),
        "crowded": float(crowded),
        "volume": float(mesh.calc_volume(signed=True)),
        "width": float(obj.dimensions.x),
        "depth": float(obj.dimensions.y),
        "height": float(obj.dimensions.z),
    }
    mesh.free()
    return result


def nearest(name: str, point: Vec3) -> float:
    """Distance from a point to the mesh's nearest vertex."""
    obj: Any = bpy.data.objects[name]
    # A new object's world matrix is only filled in by this update.
    bpy.context.view_layer.update()
    best: float = math.inf
    vertex: Any
    for vertex in obj.data.vertices:
        position: Any = obj.matrix_world @ vertex.co
        best = min(best, math.dist((position.x, position.y, position.z), point))
    return best


def sketch_line(points: list[list[float]]) -> Any:
    """A finished Sketch Mesh line, as the tool leaves it after a stroke."""
    brush: Brush = deepcopy(get_state().brushes["sketch"])
    samples: list[Sample] = [Sample((points[0][0], points[0][1], points[0][2]), brush.radius, 1.0, 1.0)]
    point: list[float]
    for point in points[1:]:
        samples.extend(resample(samples[-1], Sample((point[0], point[1], point[2]), brush.radius, 1.0, 1.0)))
    obj: Any = drawing.stroke_object(bpy.context, "pipe", brush, sketch=True)
    drawing.write_mesh(obj, samples, brush, "pipe")
    drawing.store_samples(obj, samples)
    return obj


def main() -> None:
    checks: Path = ROOT / ".checks"
    checks.mkdir(exist_ok=True)
    report: dict[str, object] = {"blender": str(bpy.app.version_string)}
    try:
        layers.ensure_scene(bpy.context)
        state: Any = get_state()

        # Ribs that do not touch are lofted; closed, the loft is capped into a solid.
        rings: list[list[list[float]]] = [circle(0.5, 0.0), circle(0.9, 0.6), circle(0.35, 1.3), circle(0.55, 1.8)]
        vase: JsonObject = api.sketch_mesh(rings)
        assert vase["method"] == "loft" and vase["closed"] is False, vase
        counts: dict[str, float] = measure(str(vase["name"]))
        assert counts["open"] > 0 and counts["crowded"] == 0, counts
        assert abs(counts["height"] - 1.8) < 0.02 and abs(counts["width"] - 1.8) < 0.08, counts
        assert bpy.data.objects[str(vase["name"])].data.polygons[0].use_smooth
        solid: JsonObject = api.sketch_mesh(rings, closed=True, curved=False, color="#FF8000")
        counts = measure(str(solid["name"]))
        assert solid["method"] == "loft" and solid["color"] == "#FF8000", solid
        assert counts["open"] == 0 and counts["crowded"] == 0 and counts["volume"] > 1.0, counts
        assert not bpy.data.objects[str(solid["name"])].data.polygons[0].use_smooth
        report["ribs_are_lofted_and_capped"] = True

        # Touching lines seen whole from one side: a sheet over their plane, ending at the outline.
        dome_lines: list[list[list[float]]] = [
            circle(1.0, 0.0),
            arch((-1.0, 0.0, 0.0), (1.0, 0.0, 0.0), 0.8),
            arch((0.0, -1.0, 0.0), (0.0, 1.0, 0.0), 0.8),
        ]
        dome: JsonObject = api.sketch_mesh(dome_lines)
        counts = measure(str(dome["name"]))
        assert dome["method"] == "patch" and counts["open"] > 0 and counts["crowded"] == 0, (dome, counts)
        assert abs(counts["width"] - 2.0) < 0.03 and abs(counts["height"] - 0.8) < 0.03, counts
        assert nearest(str(dome["name"]), (0.0, 0.0, 0.8)) < 0.06
        straight: JsonObject = api.sketch_mesh(dome_lines, curved=False)
        assert cast(int, straight["faces"]) < cast(int, dome["faces"]), (straight, dome)
        base: JsonObject = api.sketch_mesh(dome_lines, closed=True)
        counts = measure(str(base["name"]))
        assert base["method"] == "solid" and counts["open"] == 0 and counts["crowded"] == 0, (base, counts)
        assert 0.5 < counts["volume"] < 2.0 and abs(counts["height"] - 0.8) < 0.03, counts
        # Between the arches the curved surface bulges; the straight one's flat faces hold less.
        faceted: JsonObject = api.sketch_mesh(dome_lines, closed=True, curved=False)
        flat_counts: dict[str, float] = measure(str(faceted["name"]))
        assert flat_counts["open"] == 0 and flat_counts["crowded"] == 0, flat_counts
        assert flat_counts["volume"] < counts["volume"], (flat_counts, counts)
        report["touching_lines_make_a_patch_or_a_solid"] = True

        # The surface ends at the outermost lines: overshooting ends are left off, and an L keeps its notch.
        grid: list[list[list[float]]] = []
        offset: float
        for offset in (0.0, 0.7, 1.4):
            grid.append(polyline((-0.15, offset, 0.0), (1.55, offset, 0.0)))
            grid.append(polyline((offset, -0.15, 0.0), (offset, 1.55, 0.0)))
        net: JsonObject = api.sketch_mesh(grid, curved=False)
        counts = measure(str(net["name"]))
        assert abs(counts["width"] - 1.4) < 0.02 and abs(counts["depth"] - 1.4) < 0.02, counts
        ell: list[Vec3] = [(0, 0, 0), (2, 0, 0), (2, 1, 0), (1, 1, 0), (1, 2, 0), (0, 2, 0), (0, 0, 0)]
        corner: JsonObject = api.sketch_mesh([polyline(a, b) for a, b in zip(ell[:-1], ell[1:])])
        area: float = sum(polygon.area for polygon in bpy.data.objects[str(corner["name"])].data.polygons)
        assert abs(area - 3.0) < 0.02, area
        report["surface_ends_at_the_outermost_lines"] = True

        # A flat outline, closed and curved, is inflated: a circle becomes a ball.
        ball: JsonObject = api.sketch_mesh([circle(1.0, 0.0)], closed=True)
        counts = measure(str(ball["name"]))
        assert ball["method"] == "inflate" and counts["open"] == 0 and counts["crowded"] == 0, (ball, counts)
        assert abs(counts["height"] - 2.0) < 0.15 and abs(counts["volume"] - 4.19) < 0.5, counts
        report["flat_outline_is_inflated"] = True

        # Lines around a shape: a shell. A box's edges make the box; open, one side is left open.
        edges: list[list[list[float]]] = box_edges(1.5, 1.0, 1.0)
        box: JsonObject = api.sketch_mesh(edges, closed=True, curved=False)
        counts = measure(str(box["name"]))
        assert box["method"] == "shell" and counts["open"] == 0 and counts["crowded"] == 0, (box, counts)
        assert abs(counts["volume"] - 1.5) < 0.01, counts
        opened: JsonObject = api.sketch_mesh(edges, curved=False)
        counts = measure(str(opened["name"]))
        assert opened["method"] == "shell" and counts["open"] > 0 and counts["crowded"] == 0, (opened, counts)
        area = sum(polygon.area for polygon in bpy.data.objects[str(opened["name"])].data.polygons)
        # Five of the six sides: the open one is 1.5 x 1, 1.5 x 1, or 1 x 1.
        assert 6.99 <= area <= 7.01 or 6.49 <= area <= 6.51, area
        report["cage_makes_a_shell"] = True

        try:
            api.sketch_mesh([polyline((0, 0, 0), (1, 0, 0)), polyline((1.5, 0, 0), (3, 0, 0))])
            raise AssertionError("Lines along one straight line cannot make a surface")
        except api.ToolError as error:
            assert "straight line" in str(error), str(error)
        report["impossible_sketch_is_explained"] = True

        # The tool: its lines wait as the sketch; Enter's command builds the mesh and removes them.
        obj: Any
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        controller.dispatch("tool", "sketch")
        assert state.tool == "sketch" and state.sketching and "Enter" in state.status
        controller.dispatch("color", ("tool", "rgb", (1.0, 0.0, 0.0)))
        controller.dispatch("sketch", ("closed", True))
        controller.dispatch("sketch", ("curved", False))
        controller.dispatch("sketch", ("detail", 500))
        assert (state.sketch.closed, state.sketch.curved, state.sketch.detail) == (True, False, 64)
        controller.dispatch("sketch", ("detail", 16))
        controller.dispatch("sketch", "confirm")
        assert "draw the lines" in state.status and not bpy.data.objects
        ring: list[list[float]]
        for ring in rings:
            line: Any = sketch_line(ring)
            assert line.get("drawing_kind") == "pipe" and line.get("drawing_shape") == "circle"
            assert line.name.startswith("Sketch Line")
        assert len(sketch_mesh.lines(bpy.context)) == 4
        # The brush goes down and the sketch stays: its lines wait, and its settings still answer.
        controller.dispatch("sketch", ("brush", False))
        assert (state.tool, state.sketching) == ("select", True) and "brush down" in state.status
        assert len(sketch_mesh.lines(bpy.context)) == 4
        controller.dispatch("tool", "move")
        assert (state.tool, state.sketching) == ("move", True)
        controller.dispatch("color", ("sketch", "rgb", (0.0, 0.5, 1.0)))
        controller.dispatch("sketch", ("radius", 0.035))
        assert state.brushes["sketch"].radius == 0.035 and state.brushes["paint"].radius == 0.02
        # Leaving would lose the lines, so it asks first and changes nothing.
        controller.dispatch("sketch", "leave")
        assert overlay.is_capturing() and len(sketch_mesh.lines(bpy.context)) == 4 and state.sketching
        overlay.cancel_capture()
        assert not overlay.is_capturing()
        controller.dispatch("sketch", ("brush", "toggle"))
        assert (state.tool, state.sketching) == ("sketch", True)
        report["brush_goes_down_and_up_inside_the_sketch"] = True
        materials: int = len(bpy.data.materials)
        controller.dispatch("sketch", "confirm")
        assert (state.tool, state.sketching) == ("sketch", True)
        assert len(sketch_mesh.lines(bpy.context)) == 0 and len(bpy.data.objects) == 1, list(bpy.data.objects)
        built: Any = bpy.data.objects[0]
        assert built.name == "Sketch Mesh" and built.type == "MESH" and built.get("drawing_kind") == "sketch_mesh"
        # It is drawn work: among the active group's strokes, shaded without the outlines of added shapes.
        assert built.get("drawing_stroke") and built.users_collection[0].get("drawing_strokes")
        assert api.object_info(built.name)["group"] == state.active_group and not built.show_wire
        assert tuple(round(value, 3) for value in built.data.materials[0].diffuse_color) == (0.0, 0.5, 1.0, 1.0)
        # The lines' sixteen ink materials each went with them; the mesh has one of its own.
        assert len(bpy.data.materials) == materials - 4 * 16 + 1, (len(bpy.data.materials), materials)
        counts = measure(built.name)
        assert counts["open"] == 0 and counts["crowded"] == 0 and counts["volume"] > 1.0, counts
        assert "4 lines" in state.status and "lofted" in state.status, state.status
        # The object sits at the middle of its mesh, so the gizmo is on it.
        assert abs(built.location.z - 0.9) < 0.01 and abs(built.location.x) < 0.01
        report["confirm_builds_the_mesh_and_removes_the_lines"] = True

        # Cancelling removes the lines and closes the sketch; a hidden or locked line is not part of it.
        first: Any = sketch_line(rings[0])
        second: Any = sketch_line(rings[1])
        second.hide_select = True
        assert len(sketch_mesh.lines(bpy.context)) == 1
        controller.dispatch("sketch", "cancel")
        assert state.status == "Sketch cancelled" and second.name in bpy.data.objects
        assert (state.tool, state.sketching) == ("select", False)
        assert len(bpy.data.objects) == 2 and built.name in bpy.data.objects
        try:
            assert first.name == ""
            raise AssertionError("The cancelled line was not removed")
        except ReferenceError:
            pass
        bpy.data.objects.remove(second, do_unlink=True)
        # A sketch that cannot make a surface says why, and keeps its lines.
        sketch_line(polyline((0, 0, 0), (1, 0, 0)))
        controller.dispatch("sketch", "confirm")
        assert len(sketch_mesh.lines(bpy.context)) == 1 and "one straight line" in state.status, state.status
        controller.dispatch("sketch", "cancel")
        assert len(sketch_mesh.lines(bpy.context)) == 0 and len(bpy.data.objects) == 1
        controller.dispatch("sketch", ("closed", False))
        controller.dispatch("sketch", ("curved", True))
        # A sketch without lines closes without a question; a script's other tool closes one too.
        controller.dispatch("tool", "sketch")
        controller.dispatch("sketch", ("brush", False))
        controller.dispatch("sketch", "leave")
        assert (state.tool, state.sketching) == ("select", False) and not overlay.is_capturing()
        api.set_tool("sketch")
        assert state.sketching and api.set_tool("select") == {"tool": "select"} and not state.sketching
        report["cancel_removes_only_the_sketch"] = True
        report["passed"] = True
    finally:
        (checks / "sketch-mesh-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("Sketch Mesh passed")


main()
