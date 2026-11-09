"""3D Extruder: squeeze out a tube, like toothpaste, that falls from a nozzle and lands where it falls.

While the button is held, the tube comes out of a nozzle `drop` above the surface under the pointer at
`flow` metres per second. It lands on the ground (height 0), on other objects, or on its own earlier
passes, so going over a path again lays a layer on top of it. A nozzle moving slower than the flow coils the
spare tube around the point below it (extrusion.py). A timer lays the tube; after release, the strand still
hanging from the nozzle falls and lays down too, and then one undo step is pushed. Each stroke is its own
mesh object, which later pours and tubes land on.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..core.blender import Vector, bpy
from ..core.state import AppState, Brush, Extruder, Vec3, get_state
from . import layers
from .canvas import Hit
from .extrusion import COIL, Point, Tube, coil_radius, spacing_for
from .printer import GRAVITY, engaged, fill_mesh, floor_below, ignored, material, printed_top, undo_push, units

KIND: str = "extrusion"
TICK: float = 1.0 / 60.0
MESH_INTERVAL: float = 1.0 / 30.0
# Seconds over which the nozzle's speed is averaged; pointer events do not arrive every tick.
SPEED_SMOOTHING: float = 0.15
UP: Any = Vector((0.0, 0.0, 1.0))
# A floor is found by looking down at the centre and four points this share of the radius around it.
FLOOR_OFFSETS: tuple[tuple[float, float], ...] = ((0.0, 0.0), (0.7, 0.0), (-0.7, 0.0), (0.0, 0.7), (0.0, -0.7))


@dataclass
class Job:
    """A tube being laid: its object, the nozzle, and the strand still falling after release."""

    obj: Any
    tube: Tube
    window: Any
    area: Any
    region: Any
    radius: float  # tube radius, in Blender units
    flow: float  # tube length per second, in Blender units
    drop: float  # nozzle height above the surface below, in Blender units
    gravity: float
    nozzle: Hit | None = None  # canvas point under the pointer
    footprint: tuple[float, float] | None = None  # the point under the nozzle when the tube was last laid
    height: float = 0.0  # the nozzle's height
    canvas: float = 0.0  # the canvas point's height, where the tube lies when nothing is below it
    pressing: bool = False
    speed: float = 0.0  # the nozzle's averaged speed
    hanging: float = 0.0  # strand length still falling after release
    falling: float = 0.0  # the strand's falling speed
    last: float = 0.0
    last_mesh: float = 0.0
    changed: bool = False
    # Centre heights other objects allow, by tube.spacing cell: (height, height it was looked up from).
    floors: dict[tuple[int, int], tuple[float, float]] = field(default_factory=dict)


_JOB: Job | None = None
_LAST_ERROR: str = ""


def is_extrusion(obj: Any) -> bool:
    return obj is not None and obj.type == "MESH" and obj.get("drawing_kind") == KIND


def busy() -> bool:
    return _JOB is not None


def _floor(job: Job, context: Any) -> Callable[[float, float], float]:
    """The lowest centre height other objects and the ground allow at (x, y), looked up once per cell."""
    depsgraph: list[Any] = []
    cell: float = job.tube.spacing
    radius: float = job.radius

    def floor(x: float, y: float) -> float:
        key: tuple[int, int] = (math.floor(x / cell), math.floor(y / cell))
        origin: float = job.height + radius
        cached: tuple[float, float] | None = job.floors.get(key)
        # A nozzle that has risen since may be above something the earlier look started inside.
        if cached is not None and cached[1] + radius >= origin:
            return cached[0]
        if not depsgraph:
            depsgraph.append(context.evaluated_depsgraph_get())
        best: float = -math.inf
        across: float
        along: float
        for across, along in FLOOR_OFFSETS:
            start: Any = Vector(
                ((key[0] + 0.5) * cell + across * radius, (key[1] + 0.5) * cell + along * radius, origin)
            )
            surfaces: list[float] = []
            found: float | None = floor_below(context, depsgraph[0], start, job.obj.name)
            if found is not None:
                surfaces.append(found)
            if origin >= 0.0:
                surfaces.append(0.0)
            if surfaces:
                # A ball of the tube's radius resting on the surface touches it this far below its centre.
                best = max(best, max(surfaces) + radius * math.sqrt(1.0 - across * across - along * along))
        height: float = best if best > -math.inf else job.canvas + radius
        job.floors[key] = (height, origin)
        return height

    return floor


def _out_of_solids(job: Job, context: Any, x: float, y: float, z: float) -> float:
    """Where the column above a canvas point leaves the solids it starts inside, or the point's own height.

    Drawing on the ground across a box puts the canvas point inside the box, so the nozzle rises above its
    top. Under a table the point is in open space: the first thing above faces down, and the nozzle stays.
    """
    depsgraph: Any = context.evaluated_depsgraph_get()
    # Just above the point, clear of a face resting on the canvas.
    origin: Any = Vector((x, y, z + 0.05 * job.radius))
    height: float = z
    _attempt: int
    for _attempt in range(16):
        result: Any = context.scene.ray_cast(depsgraph, origin, UP)
        if not result[0]:
            return height
        if not ignored(result[4], job.obj.name):
            # A face turned away from the point is the underside of something in open space above it.
            if float(result[2].z) <= 0.0:
                return height
            height = float(result[1].z)
        origin = result[1] + UP * 1e-5
    return height


def _nozzle_height(job: Job, context: Any, x: float, y: float, canvas: float) -> float:
    """The drop height above the highest of the canvas point (out of any solid it is in), this tube, and prints
    and tubes under the nozzle."""
    reach: float = (COIL + 2.0) * job.radius
    top: float = _out_of_solids(job, context, x, y, canvas)
    own: float | None = job.tube.top(x, y, reach)
    if own is not None:
        top = max(top, own)
    others: float | None = printed_top(context, x, y, reach, job.obj)
    if others is not None:
        top = max(top, others)
    return top + job.drop


def _settings(context: Any) -> tuple[float, float, float]:
    """Tube radius, flow per second, and drop height, in Blender units."""
    state: AppState = get_state()
    brush: Brush = state.brushes["extruder"]
    settings: Extruder = state.extruder
    scale: float = units(context)
    return max(1e-5, brush.radius / scale), max(1e-5, settings.flow / scale), settings.drop / scale


def _new_object(context: Any, color: Vec3) -> Any:
    mesh: Any = bpy.data.meshes.new("3D Extrusion")
    obj: Any = bpy.data.objects.new("3D Extrusion", mesh)
    layers.link_stroke(obj, context)
    obj["drawing_kind"] = KIND
    obj.color = (*color, 1.0)
    # A little sheen, like a paste.
    mesh.materials.append(material("3D Extrusion", color, 0.45))
    return obj


def start(context: Any, area: Any, region: Any, hit: Hit) -> bool:
    """Start squeezing out a new tube at a canvas point; a tube still settling elsewhere finishes at once."""
    global _JOB
    finish_now()
    radius: float
    flow: float
    drop: float
    radius, flow, drop = _settings(context)
    color: Vec3 = get_state().brushes["extruder"].color
    obj: Any = _new_object(context, color)
    job: Job = Job(obj, Tube(radius, spacing_for(radius, flow)), context.window, area, region, radius, flow, drop, 0.0)
    job.gravity = GRAVITY / units(context)
    job.nozzle = hit
    job.pressing = True
    job.last = time.monotonic()
    x: float = hit.position[0]
    y: float = hit.position[1]
    job.canvas = hit.position[2]
    job.height = _nozzle_height(job, context, x, y, job.canvas)
    # The tube touches down under the nozzle as soon as the button is pressed.
    job.tube.lay((x, y), (x, y), 0.0, coil_radius(radius, 0.0, flow), _floor(job, context))
    job.footprint = (x, y)
    job.changed = True
    _JOB = job
    _write_mesh(job)
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=0.0)
    get_state().status = "3D Extruder: squeezing out a tube"
    return True


def aim(hit: Hit | None) -> None:
    if _JOB is not None and _JOB.pressing:
        _JOB.nozzle = hit


def release() -> None:
    """Stop squeezing; the strand hanging from the nozzle falls and lays down."""
    job: Job | None = _JOB
    if job is None or not job.pressing:
        return
    job.pressing = False
    job.nozzle = None
    if job.tube.points:
        job.hanging = max(0.0, job.height - job.tube.points[-1][2])
    get_state().status = "3D extrusion settling ..."


def _tip(job: Job) -> Point | None:
    """The top of the hanging strand: the nozzle while squeezing, then the strand's falling end."""
    if job.footprint is None or not job.tube.points:
        return None
    if job.pressing:
        return job.footprint[0], job.footprint[1], job.height
    if job.hanging <= 0.0:
        return None
    return job.footprint[0], job.footprint[1], job.tube.points[-1][2] + job.hanging


def _write_mesh(job: Job) -> None:
    vertices: Any
    loops: Any
    starts: Any
    vertices, loops, starts = job.tube.mesh(_tip(job))
    fill_mesh(job.obj.data, vertices, loops, starts)
    job.changed = False
    job.last_mesh = time.monotonic()


def _squeeze(job: Job, context: Any, elapsed: float) -> None:
    """Lay the tube that came out this tick while the nozzle moved to the point under the pointer."""
    nozzle: Hit | None = job.nozzle
    blend: float = min(1.0, elapsed / SPEED_SMOOTHING)
    if nozzle is None or job.footprint is None:
        # Off the canvas the flow pauses until the pointer returns.
        job.speed -= job.speed * blend
        return
    target: tuple[float, float] = (nozzle.position[0], nozzle.position[1])
    moved: float = math.hypot(target[0] - job.footprint[0], target[1] - job.footprint[1])
    job.speed += (moved / max(elapsed, 1e-6) - job.speed) * blend
    job.canvas = nozzle.position[2]
    height: float = _nozzle_height(job, context, target[0], target[1], job.canvas)
    coil: float = coil_radius(job.radius, job.speed, job.flow)
    laid: int = job.tube.lay(job.footprint, target, job.flow * elapsed, coil, _floor(job, context))
    job.changed = job.changed or bool(laid) or moved > 0.0 or height != job.height
    job.footprint = target
    job.height = height


def _settle(job: Job, context: Any, elapsed: float) -> None:
    """The released strand falls under gravity, laying its length where it lands."""
    if job.footprint is None:
        job.hanging = 0.0
        return
    job.falling += job.gravity * elapsed
    length: float = min(job.hanging, job.falling * elapsed)
    job.hanging -= length
    job.speed -= job.speed * min(1.0, elapsed / SPEED_SMOOTHING)
    coil: float = coil_radius(job.radius, job.speed, job.flow)
    job.tube.lay(job.footprint, job.footprint, length, coil, _floor(job, context))
    job.changed = True


def _complete(job: Job, *, push: bool = True) -> None:
    """Write the final mesh; a tube too short to have a body is removed."""
    global _JOB
    _JOB = None
    job.pressing = False
    job.hanging = 0.0
    if len(job.tube.points) < 2:
        mesh: Any = job.obj.data
        materials: list[Any] = list(mesh.materials)
        bpy.data.objects.remove(job.obj, do_unlink=True)
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
        item: Any
        for item in materials:
            if item is not None and item.users == 0:
                bpy.data.materials.remove(item)
        return
    _write_mesh(job)
    metres: float = job.tube.length * units(bpy.context)
    amount: str = f"{metres:.2f} m" if metres >= 1.0 else f"{metres * 100.0:.1f} cm"
    get_state().status = f"3D extrusion: {amount} of tube"
    if push:
        undo_push(job.window, job.area, job.region, "3D extrusion")
    job.area.tag_redraw()


def finish_now() -> None:
    """Lay the falling strand at once and finish the tube (before another one starts)."""
    job: Job | None = _JOB
    if job is None:
        return
    if job.pressing:
        release()
    error: ReferenceError | RuntimeError
    try:
        if job.hanging > 0.0 and job.footprint is not None:
            with bpy.context.temp_override(window=job.window, area=job.area, region=job.region):
                coil: float = coil_radius(job.radius, 0.0, job.flow)
                job.tube.lay(job.footprint, job.footprint, job.hanging, coil, _floor(job, bpy.context))
        _complete(job)
    except (ReferenceError, RuntimeError) as error:
        # The tube's object or window is gone.
        print(f"3D Extruder: {error}")
        reset()


def _tick() -> float | None:
    global _LAST_ERROR
    job: Job | None = _JOB
    if job is None:
        return None
    error: ReferenceError | RuntimeError | ValueError | AttributeError
    try:
        if job.window not in tuple(bpy.context.window_manager.windows) or job.area not in tuple(
            job.window.screen.areas
        ):
            reset()
            return None
        now: float = time.monotonic()
        elapsed: float = min(0.05, now - job.last)
        job.last = now
        with bpy.context.temp_override(window=job.window, area=job.area, region=job.region):
            context: Any = bpy.context
            if job.pressing:
                _squeeze(job, context, elapsed)
            elif job.hanging > 0.0:
                _settle(job, context, elapsed)
            if job.changed and now - job.last_mesh >= MESH_INTERVAL:
                _write_mesh(job)
            if not job.pressing and job.hanging <= 0.0 and not engaged(job.window):
                _complete(job)
                return None
        job.area.tag_redraw()
        return TICK
    except (ReferenceError, RuntimeError, ValueError, AttributeError) as error:
        # The tube was deleted, or its file or scene replaced.
        if str(error) != _LAST_ERROR:
            _LAST_ERROR = str(error)
            print(f"3D Extruder: {error}")
        reset()
        return None


def flush() -> None:
    """Before saving: write the tube being laid as it is now, so the file holds it."""
    job: Job | None = _JOB
    if job is not None:
        try:
            _write_mesh(job)
        except ReferenceError:
            reset()


def reset() -> None:
    """Undo, redo, or loading replaced the data: drop the tube being laid."""
    global _JOB
    _JOB = None
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)


def shutdown() -> None:
    reset()
