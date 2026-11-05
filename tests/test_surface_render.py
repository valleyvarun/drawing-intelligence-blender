"""Isolated native Workbench captures for curved paint, pipe ends, and GP feathers."""

from __future__ import annotations

import json
import math
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

from drawing_app.core import controller
from drawing_app.core.blender import Vector, bpy
from drawing_app.core.state import ROOT, Brush, Vec3, get_state
from drawing_app.tools import layers
from drawing_app.tools.drawing import material, write_grease, write_mesh
from drawing_app.tools.geometry import PaintCache, Sample, resample
from drawing_app.tools.surface import PaintSurface
from drawing_app.ui import layout

CHECKS: Path = ROOT / ".checks"
REPORT: dict[str, Any] = {}
INDEX: int = 0


def viewport() -> Any:
    area: Any = layout.viewport_area(bpy.context)
    assert area is not None
    return area


def ink_object(name: str, kind: str, brush: Brush) -> Any:
    data: Any = bpy.data.grease_pencils.new(name) if kind == "grease" else bpy.data.meshes.new(name)
    obj: Any = bpy.data.objects.new(name, data)
    layers.link_stroke(obj, bpy.context)
    obj["drawing_kind"] = kind
    obj["drawing_preview"] = False
    obj.show_wire = False
    ink: Any = material(brush, kind == "grease")
    if kind == "grease":
        data.materials.append(ink)
        layer: Any = data.layers.new("Ink", set_active=True)
        layer.frames.new(1)
    else:
        level: int
        for level in range(16):
            entry: Any = ink if level == 0 else ink.copy()
            entry.diffuse_color = (*brush.color, level / 15.0)
            data.materials.append(entry)
    return obj


def paint_surface(source: Any, samples: list[Sample], name: str) -> None:
    brush: Brush = Brush(radius=0.14, hardness=0.45, opacity=0.45, color=(0.025, 0.12, 0.18))
    obj: Any = ink_object(name, "paint", brush)
    cache: PaintCache = PaintCache(brush.radius * 0.125)
    surface: PaintSurface = PaintSurface(bpy.context, source, brush.radius, cache.cell)
    write_mesh(obj, samples[:-8], brush, "paint", cache, surface)
    started: float = time.perf_counter()
    write_mesh(obj, samples, brush, "paint", cache, surface)
    REPORT[name] = {
        "patches": surface.patch_count,
        "faces": len(obj.data.polygons),
        "last_8_samples_upload_ms": (time.perf_counter() - started) * 1000.0,
    }


def append_sample(samples: list[Sample], position: Vec3, normal: Vec3) -> None:
    sample: Sample = Sample(position, 0.14, 0.45, 0.45, normal)
    samples.extend(resample(samples[-1], sample) if samples else [sample])


def setup() -> None:
    obj: Any
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    controller.dispatch("theme", "light")
    controller.dispatch("tool", "select")
    get_state().ruler = False
    get_state().cursor_active = False
    get_state().cursor_placing = False
    get_state().cursor_placed = False
    get_state().axes_spokes = False
    gray: Any = bpy.data.materials.new("Surface gray")
    gray.diffuse_color = (0.72, 0.72, 0.72, 1.0)
    bpy.ops.mesh.primitive_cube_add(size=1.6, location=(-2.2, 0.0, 1.0))
    cube: Any = bpy.context.object
    cube.name = "Cube"
    cube.data.materials.append(gray)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=64, ring_count=32, radius=0.8, location=(0.0, 0.0, 1.0))
    sphere: Any = bpy.context.object
    sphere.name = "Sphere"
    sphere.data.materials.append(gray)
    bpy.ops.object.shade_smooth()
    bpy.ops.mesh.primitive_cylinder_add(vertices=96, radius=0.8, depth=1.6, location=(2.2, 0.0, 1.0))
    cylinder: Any = bpy.context.object
    cylinder.name = "Cylinder"
    cylinder.data.materials.append(gray)
    bpy.context.view_layer.update()
    samples: list[Sample] = []
    index: int
    for index in range(31):
        append_sample(samples, (-2.2, 0.1 - index * 0.03, 1.8), (0.0, 0.0, 1.0))
    for index in range(31):
        append_sample(samples, (-2.2, -0.8, 1.8 - index * 0.035), (0.0, -1.0, 0.0))
    paint_surface(cube, samples, "Cube paint")
    center: float
    source: Any
    name: str
    for center, source, name in ((0.0, sphere, "Sphere paint"), (2.2, cylinder, "Cylinder paint")):
        samples = []
        for index in range(141):
            angle: float = math.radians(-170.0 + index * 1.5)
            normal: Vec3 = (math.cos(angle), math.sin(angle), 0.0)
            append_sample(samples, (center + 0.8 * normal[0], 0.8 * normal[1], 1.0), normal)
        paint_surface(source, samples, name)
    brush: Brush = Brush(radius=0.14, color=(0.8, 0.25, 0.04))
    pipe: Any = ink_object("Rounded pipe", "pipe", brush)
    write_mesh(
        pipe, [Sample((-2.8, -1.7, 0.4), 0.14, 1.0, 1.0), Sample((-1.3, -1.7, 0.4), 0.14, 1.0, 1.0)], brush, "pipe"
    )
    dot: Any = ink_object("Pipe dot", "pipe", brush)
    write_mesh(dot, [Sample((-0.8, -1.7, 0.4), 0.14, 1.0, 1.0)], brush, "pipe")
    shape: str
    for index, shape in enumerate(("square", "triangle", "circle")):
        brush = Brush(radius=0.3, hardness=0.15, opacity=0.55, shape=shape, color=(0.3, 0.04, 0.5))
        grease: Any = ink_object(f"Feathered {shape}", "grease", brush)
        write_grease(grease, [Sample((0.0 + index * 0.9, -1.8, 0.3), 0.3, 0.15, 0.55)], brush, 1)
    brush = Brush(radius=0.3, hardness=1.0, opacity=0.55, color=(0.3, 0.04, 0.5))
    grease = ink_object("Hard circle comparison", "grease", brush)
    write_grease(grease, [Sample((2.7, -1.8, 0.3), 0.3, 1.0, 0.55)], brush, 1)
    brush = Brush(radius=0.3, hardness=1.0, opacity=1.0, shape="square", color=(0.02, 0.8, 0.05))
    grease = ink_object("Occluded GP probe", "grease", brush)
    write_grease(grease, [Sample((-2.2, -0.75, 1.0), 0.3, 1.0, 1.0, (0.0, -1.0, 0.0))], brush, 1)
    bpy.ops.object.select_all(action="DESELECT")
    area: Any = viewport()
    view: Any = area.spaces.active.region_3d
    view.view_location = (0.0, -0.2, 0.8)
    view.view_distance = 8.5
    view.view_rotation = Vector((3.0, -10.0, 7.0)).to_track_quat("Z", "Y")
    view.view_perspective = "ORTHO"
    view.update()
    area.tag_redraw()


def capture_light() -> None:
    bpy.ops.screen.screenshot(filepath=str(CHECKS / "surface-light.png"))
    controller.dispatch("theme", "dark")


def capture_dark() -> None:
    bpy.ops.screen.screenshot(filepath=str(CHECKS / "surface-dark.png"))
    area: Any = viewport()
    area.spaces.active.overlay.show_overlays = False
    view: Any = area.spaces.active.region_3d
    view.view_location = (0.8, 0.0, 1.0)
    view.view_distance = 4.3
    view.update()
    area.tag_redraw()


def capture_detail() -> None:
    bpy.ops.screen.screenshot(filepath=str(CHECKS / "surface-detail-no-overlays.png"))
    REPORT["detail_capture"] = "Native overlays hidden only for seam inspection"
    view: Any = viewport().spaces.active.region_3d
    view.view_location = (-2.2, -0.2, 1.0)
    view.view_distance = 3.0
    view.update()
    viewport().tag_redraw()


def capture_occluded() -> None:
    bpy.ops.screen.screenshot(filepath=str(CHECKS / "surface-gp-occluded.png"))
    bpy.data.objects["Occluded GP probe"].location.y = -0.5
    viewport().tag_redraw()


def capture_revealed() -> None:
    bpy.ops.screen.screenshot(filepath=str(CHECKS / "surface-gp-revealed.png"))
    REPORT["gp_occlusion_capture_pair"] = ["surface-gp-occluded.png", "surface-gp-revealed.png"]
    REPORT["passed"] = True


STEPS: list[Callable[[], None]] = [
    setup,
    capture_light,
    capture_dark,
    capture_detail,
    capture_occluded,
    capture_revealed,
]


def advance() -> float | None:
    global INDEX
    try:
        if INDEX < len(STEPS):
            STEPS[INDEX]()
            INDEX += 1
            return 1.2
    except Exception:
        REPORT["error"] = traceback.format_exc()
    (CHECKS / "surface-render-report.json").write_text(json.dumps(REPORT, indent=2), encoding="utf-8")
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(advance, first_interval=4.0)
