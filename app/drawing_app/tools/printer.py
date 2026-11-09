"""3D Printer: pour particles that fall, land, and merge into a printed mesh over time.

While the button is held, particles leave a nozzle above the canvas point under the pointer, inside the
nozzle's circle. They fall under gravity and land on the print, on another object, or on the ground
(height 0). Each landing adds its volume to a column of the print's height field (print_field.py),
which slumps to its angle of repose and becomes the object's mesh a few times a second. A timer runs
this on its own, so the print keeps forming after the stroke; one undo step is pushed when it settles.

A pour joins the active group's print of the same colour when it starts on or near it. Its columns only
grow, so pouring with the pointer below an earlier print still adds height on top of it. Another colour
makes a new print object, released above every print under the nozzle so that it lands on top of them.

Particles fall in drops: each drop releases `per_drop` particles at once across the nozzle's circle, and
drops follow each other so that `rate` particles fall per second. A particle is a block `size` wide (at
most the nozzle's width) that spreads over the columns under it when it lands, so it adds about its own
height there: larger particles build height faster.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any

from ..core.blender import Vector, batch_for_shader, bpy, gpu, view3d_utils
from ..core.state import AppState, Brush, Printer, Vec3, color_hex, get_state
from . import layers
from .canvas import Hit
from .print_field import HeightField, np

GRAVITY: float = 9.81  # metres per second squared
# A new print's column width as a share of the nozzle radius: four columns across the radius.
COLUMN_SHARE: float = 0.25
TICK: float = 1.0 / 60.0
SETTLE_TICK: float = 1.0 / 30.0
MESH_INTERVAL: float = 0.12
RELAX_STEPS: int = 3
# Slumping runs in batches of RELAX_STEPS until it is still or this much time has passed in a tick.
RELAX_BUDGET: float = 0.004
FLOOR_CASTS: int = 160  # floor look-ups per tick; the rest wait for the next one
SPAWN_LIMIT: int = 4000  # particles released per tick, at most
QUIET_TICKS: int = 20  # ticks without movement before the print counts as settled
SETTLE_TIMEOUT: float = 8.0  # seconds after the last landing
FALL_LIMIT: float = 6.0  # seconds a particle may fall before it is dropped
KIND: str = "print"
DOWN: Any = Vector((0.0, 0.0, -1.0))


@dataclass
class Job:
    """A print forming: its object and field, the nozzle, and the particles still falling."""

    obj: Any
    field: HeightField
    window: Any
    area: Any
    region: Any
    color: Vec3
    created: bool
    radius: float = 0.0  # nozzle radius, in Blender units
    drop: float = 0.0  # release height above the surface below, in Blender units
    rate: float = 0.0  # particles per second
    per_drop: int = 1  # particles released together
    size: float = 0.0  # a particle's width, in Blender units
    particle_volume: float = 0.0  # Blender units cubed
    gravity: float = GRAVITY
    nozzle: Vec3 | None = None
    pouring: bool = False
    positions: Any = None  # (N, 3) world positions
    speeds: Any = None  # (N,) downward speeds
    debt: float = 0.0  # drops owed since the last release
    last: float = 0.0
    last_mesh: float = 0.0
    last_landing: float = 0.0
    changed: bool = False
    quiet: int = 0


_JOB: Job | None = None
# Print objects' fields, by object pointer; cleared when undo or loading replaces the data.
_FIELDS: dict[int, HeightField] = {}
_HANDLER: Any = None
_SHADER: Any = None
_RANDOM: Any = np.random.default_rng()
_LAST_ERROR: str = ""


def _units(context: Any) -> float:
    """Metres per Blender unit."""
    return max(float(context.scene.unit_settings.scale_length), 1e-12)


def is_print(obj: Any) -> bool:
    return obj is not None and obj.type == "MESH" and obj.get("drawing_kind") == KIND


def field_of(obj: Any) -> HeightField | None:
    """The print object's height field, read from its properties the first time."""
    key: int = int(obj.as_pointer())
    cached: HeightField | None = _FIELDS.get(key)
    if cached is not None:
        return cached
    grid: Any = obj.get("drawing_print_grid")
    height: Any = obj.get("drawing_print_height")
    base: Any = obj.get("drawing_print_base")
    if grid is None or height is None or base is None:
        return None
    values: list[float] = []
    value: Any
    for value in grid:
        values.append(float(value))
    field: HeightField | None = HeightField.restore(
        values, np.asarray(height, dtype=np.float64), np.asarray(base, dtype=np.float64)
    )
    if field is not None:
        _FIELDS[key] = field
    return field


def _save(obj: Any, field: HeightField) -> None:
    obj["drawing_print_grid"] = field.grid()
    obj["drawing_print_height"] = field.height.ravel().tolist()
    obj["drawing_print_base"] = field.base.ravel().tolist()


def _write_mesh(job: Job) -> None:
    vertices: Any
    loops: Any
    starts: Any
    vertices, loops, starts = job.field.mesh()
    mesh: Any = job.obj.data
    mesh.clear_geometry()
    if len(starts):
        mesh.vertices.add(len(vertices))
        mesh.vertices.foreach_set("co", vertices.ravel())
        mesh.loops.add(len(loops))
        mesh.loops.foreach_set("vertex_index", loops)
        mesh.polygons.add(len(starts))
        mesh.polygons.foreach_set("loop_start", starts)
        mesh.update(calc_edges=True)
        mesh.shade_smooth()
    else:
        mesh.update()
    job.changed = False
    job.last_mesh = time.monotonic()


def _material(color: Vec3) -> Any:
    material: Any = bpy.data.materials.new("3D Print")
    material.diffuse_color = (*color, 1.0)
    node: Any
    for node in material.node_tree.nodes:
        if node.type == "BSDF_PRINCIPLED":
            node.inputs["Base Color"].default_value = (*color, 1.0)
            node.inputs["Roughness"].default_value = 0.8
            break
    return material


def _columns(context: Any, radius: float, size: float) -> float:
    """Column width for a nozzle of `radius` pouring particles `size` wide (Blender units).

    Four columns across the nozzle radius, and no narrower than half a particle: large particles then
    land on a few columns each, so slumping keeps up with them instead of leaving a spike to collapse.
    """
    return max(radius * COLUMN_SHARE, size / 2.0, 0.001 / _units(context))


def _new_print(context: Any, point: Vec3, color: Vec3, cell: float) -> tuple[Any, HeightField]:
    mesh: Any = bpy.data.meshes.new("3D Print")
    obj: Any = bpy.data.objects.new("3D Print", mesh)
    layers.link_stroke(obj, context)
    obj["drawing_kind"] = KIND
    obj["drawing_print_color"] = color_hex(color)
    obj.color = (*color, 1.0)
    mesh.materials.append(_material(color))
    field: HeightField = HeightField.empty((point[0], point[1]), cell)
    _FIELDS[int(obj.as_pointer())] = field
    _save(obj, field)
    return obj, field


def _find_print(context: Any, point: Vec3, color: Vec3, radius: float, columns: float) -> Any:
    """The active group's print of this colour and a similar scale that the pour starts on or near, or None.

    A print whose columns are over twice as wide or narrow as this pour's (`columns`) is left alone: the pour
    starts its own print, which stacks on it, instead of coarsening its detail or landing large particles on
    columns too narrow for slumping to keep up.
    """
    label: str = color_hex(color)
    best: Any = None
    best_gap: float = math.inf
    obj: Any
    for obj in layers.active_collection(context, strokes=True).objects:
        if not is_print(obj) or obj.get("drawing_print_color") != label:
            continue
        if layers.object_locked(context, obj) or not obj.visible_get():
            continue
        field: HeightField | None = field_of(obj)
        extent: tuple[float, float, float, float] | None = field.extent() if field is not None else None
        if field is None or extent is None or not columns / 2.0 <= field.cell <= columns * 2.0:
            continue
        local: Any = obj.matrix_world.inverted() @ Vector(point)
        gap: float = math.hypot(
            max(extent[0] - float(local.x), 0.0, float(local.x) - extent[2]),
            max(extent[1] - float(local.y), 0.0, float(local.y) - extent[3]),
        )
        if gap <= max(4.0 * radius, 8.0 * field.cell) and gap < best_gap:
            best, best_gap = obj, gap
    return best


def _configure(job: Job, context: Any) -> None:
    """Take the current nozzle size, particle size, particles per second and per drop, and drop height."""
    state: AppState = get_state()
    brush: Brush = state.brushes["printer"]
    settings: Printer = state.printer
    units: float = _units(context)
    job.radius = max(1e-5, brush.radius / units)
    job.drop = settings.drop / units
    job.gravity = GRAVITY / units
    job.rate = max(1.0, settings.rate)
    job.per_drop = max(1, int(settings.per_drop))
    job.size = max(1e-6, settings.particle(brush.radius) / units)
    # A cube of material: it adds its own width in height over its footprint.
    job.particle_volume = job.size**3


def start(context: Any, area: Any, region: Any, hit: Hit) -> bool:
    """Start pouring at a canvas point; a print still forming elsewhere settles at once."""
    global _JOB
    brush: Brush = get_state().brushes["printer"]
    units: float = _units(context)
    radius: float = max(1e-5, brush.radius / units)
    columns: float = _columns(context, radius, get_state().printer.particle(brush.radius) / units)
    color: Vec3 = brush.color
    obj: Any = _find_print(context, hit.position, color, radius, columns)
    if _JOB is not None and (obj is None or _JOB.obj != obj):
        finish_now()
    job: Job | None = _JOB
    if job is None or obj is None:
        created: bool = obj is None
        field: HeightField
        if obj is None:
            obj, field = _new_print(context, hit.position, color, columns)
        else:
            found: HeightField | None = field_of(obj)
            if found is None:
                return False
            field = found
        job = Job(obj, field, context.window, area, region, color, created)
        job.positions = np.zeros((0, 3), dtype=np.float64)
        job.speeds = np.zeros(0, dtype=np.float64)
        job.last = job.last_landing = time.monotonic()
    _configure(job, context)
    job.window, job.area, job.region = context.window, area, region
    job.nozzle = hit.position
    job.pouring = True
    job.quiet = 0
    # The first drop falls as soon as the button is pressed.
    job.debt = 1.0
    _JOB = job
    _install()
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=0.0)
    get_state().status = "3D Printer: pouring"
    return True


def aim(hit: Hit | None) -> None:
    if _JOB is not None and _JOB.pouring:
        _JOB.nozzle = hit.position if hit is not None else None


def release() -> None:
    if _JOB is not None:
        _JOB.pouring = False
        _JOB.nozzle = None
        get_state().status = "3D print forming ..."


def busy() -> bool:
    return _JOB is not None


def _local(obj: Any, points: Any) -> Any:
    inverse: Any = np.array(obj.matrix_world.inverted(), dtype=np.float64)
    return points @ inverse[:3, :3].T + inverse[:3, 3]


def _world_z(obj: Any, x: float, y: float, z: float) -> float:
    return float((obj.matrix_world @ Vector((x, y, z))).z)


def _floor_below(context: Any, depsgraph: Any, start: Any, skip: str) -> float | None:
    """Height of the first surface below `start`, ignoring the print itself and drawing helpers."""
    origin: Any = start.copy()
    _attempt: int
    for _attempt in range(16):
        result: Any = context.scene.ray_cast(depsgraph, origin, DOWN)
        if not result[0]:
            return None
        obj: Any = result[4]
        if obj.name == skip or obj.get("drawing_plane") or obj.get("drawing_preview") or obj.get("drawing_helper"):
            origin = result[1] + DOWN * 1e-5
            continue
        return float(result[1].z)
    return None


def _find_floors(job: Job, context: Any, i: Any, j: Any, start: Any) -> None:
    """Set the floor of each listed column: the first surface below `start` (local), or the ground."""
    if not len(i):
        return
    depsgraph: Any = context.evaluated_depsgraph_get()
    matrix: Any = job.obj.matrix_world
    inverse: Any = matrix.inverted()
    x: Any
    y: Any
    x, y = job.field.center(i, j)
    index: int
    for index in range(len(i)):
        point: Any = matrix @ Vector((float(x[index]), float(y[index]), float(start[index])))
        candidates: list[float] = []
        found: float | None = _floor_below(context, depsgraph, point, job.obj.name)
        if found is not None:
            candidates.append(found)
        # The ground plane at height 0 catches everything poured above it.
        if float(point.z) >= 0.0:
            candidates.append(0.0)
        floor: float = max(candidates) if candidates else float(point.z)
        local: Any = inverse @ Vector((float(point.x), float(point.y), floor))
        job.field.base[int(i[index]), int(j[index])] = float(local.z)


def _release_height(job: Job, context: Any, nozzle: Vec3) -> float:
    """World height particles leave from: the drop height above the highest print under the nozzle."""
    top: float = nozzle[2]
    reach: float = job.radius
    center: Any = _local(job.obj, np.array([nozzle], dtype=np.float64))[0]
    found: float | None = job.field.top(
        float(center[0]) - reach, float(center[1]) - reach, float(center[0]) + reach, float(center[1]) + reach
    )
    if found is not None:
        top = max(top, _world_z(job.obj, float(center[0]), float(center[1]), found))
    obj: Any
    for obj in context.scene.objects:
        if obj == job.obj or not is_print(obj) or not obj.visible_get():
            continue
        corners: list[Any] = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
        if (
            min(float(c.x) for c in corners) <= nozzle[0] + reach
            and max(float(c.x) for c in corners) >= nozzle[0] - reach
            and min(float(c.y) for c in corners) <= nozzle[1] + reach
            and max(float(c.y) for c in corners) >= nozzle[1] - reach
        ):
            top = max(top, max(float(c.z) for c in corners))
    return top + job.drop


def _emit(job: Job, context: Any, elapsed: float) -> None:
    if not job.pouring or job.nozzle is None:
        job.debt = 0.0
        return
    # Drops follow each other so that `rate` particles fall per second, `per_drop` at a time.
    wanted: float = job.debt + job.rate / job.per_drop * elapsed
    drops: int = int(wanted)
    job.debt = wanted - drops
    count: int = min(drops * job.per_drop, SPAWN_LIMIT)
    if count <= 0:
        return
    nozzle: Vec3 = job.nozzle
    height: float = _release_height(job, context, nozzle)
    angle: Any = _RANDOM.uniform(0.0, math.tau, count)
    distance: Any = job.radius * np.sqrt(_RANDOM.uniform(0.0, 1.0, count))
    points: Any = np.stack(
        (nozzle[0] + np.cos(angle) * distance, nozzle[1] + np.sin(angle) * distance, np.full(count, height)), axis=1
    )
    local: Any = _local(job.obj, points)
    field: HeightField = job.field
    # Room for every particle's footprint, which reaches half its width around its centre.
    reach: float = job.size / 2.0
    field.cover(
        float(local[:, 0].min()) - reach,
        float(local[:, 1].min()) - reach,
        float(local[:, 0].max()) + reach,
        float(local[:, 1].max()) + reach,
    )
    i: Any
    j: Any
    _side: int
    i, j, _side = field.footprint(local[:, 0], local[:, 1], job.size)
    inside: Any = field.inside(i, j)
    i, j = i[inside], j[inside]
    # Columns nothing has landed in yet: look down from the release height for their floor.
    unknown: Any = ~field.known()[i, j]
    if unknown.any():
        cells: Any = np.unique(np.stack((i[unknown], j[unknown]), axis=1), axis=0)
        _find_floors(job, context, cells[:, 0], cells[:, 1], np.full(len(cells), float(local[0, 2])))
    job.positions = np.concatenate((job.positions, points))
    job.speeds = np.concatenate((job.speeds, np.zeros(count)))


def _fall(job: Job, elapsed: float, now: float) -> None:
    if not len(job.positions):
        return
    job.speeds += job.gravity * elapsed
    job.positions[:, 2] -= job.speeds * elapsed
    field: HeightField = job.field
    local: Any = _local(job.obj, job.positions)
    i: Any
    j: Any
    i, j = field.index(local[:, 0], local[:, 1])
    inside: Any = field.inside(i, j)
    i = np.clip(i, 0, field.shape[0] - 1)
    j = np.clip(j, 0, field.shape[1] - 1)
    known: Any = field.known()[i, j]
    surface: Any = np.where(known, field.base[i, j] + field.height[i, j], -np.inf)
    landed: Any = inside & (local[:, 2] <= surface)
    if landed.any():
        field.land(local[landed, 0], local[landed, 1], job.size, job.particle_volume)
        job.changed = True
        job.last_landing = now
    lost: Any = ~inside | (job.speeds > job.gravity * FALL_LIMIT)
    keep: Any = ~(landed | lost)
    job.positions = job.positions[keep]
    job.speeds = job.speeds[keep]


def _engaged(job: Job) -> bool:
    """Another stroke or modal tool is running; the undo step waits so it does not split it."""
    from ..core import controller

    session: Any = controller._ACTIVE
    if session is not None and session.drawing.is_drawing:
        return True
    operator: Any
    for operator in job.window.modal_operators:
        if operator.bl_idname != "DRAWING_OT_session":
            return True
    return False


def _undo_push(job: Job) -> None:
    from ..core.project import persist_scene

    if bpy.app.background:
        return
    with bpy.context.temp_override(window=job.window, area=job.area, region=job.region):
        if bpy.ops.ed.undo_push.poll():
            persist_scene()
            bpy.ops.ed.undo_push(message="3D print")


def _complete(job: Job, *, push: bool = True) -> None:
    """Write the final mesh and field; an empty new print is removed."""
    global _JOB
    _JOB = None
    if job.created and job.field.volume() <= 0.0:
        mesh: Any = job.obj.data
        materials: list[Any] = list(mesh.materials)
        _FIELDS.pop(int(job.obj.as_pointer()), None)
        bpy.data.objects.remove(job.obj, do_unlink=True)
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
        material: Any
        for material in materials:
            if material is not None and material.users == 0:
                bpy.data.materials.remove(material)
        return
    _write_mesh(job)
    _save(job.obj, job.field)
    cubic_metres: float = job.field.volume() * _units(bpy.context) ** 3
    amount: str = f"{cubic_metres:.2f} m³" if cubic_metres >= 1.0 else f"{cubic_metres * 1.0e6:.1f} cm³"
    get_state().status = f"3D print formed ({amount})"
    if push:
        _undo_push(job)
    job.area.tag_redraw()


def finish_now() -> None:
    """Land every falling particle at once and finish the print (before another one starts)."""
    job: Job | None = _JOB
    if job is None:
        return
    job.pouring = False
    if len(job.positions):
        field: HeightField = job.field
        local: Any = _local(job.obj, job.positions)
        i: Any
        j: Any
        i, j = field.index(local[:, 0], local[:, 1])
        inside: Any = field.inside(i, j)
        inside &= field.known()[np.clip(i, 0, field.shape[0] - 1), np.clip(j, 0, field.shape[1] - 1)]
        field.land(local[inside, 0], local[inside, 1], job.size, job.particle_volume)
        job.positions = job.positions[:0]
        job.speeds = job.speeds[:0]
    _step: int
    for _step in range(20):
        job.field.relax(RELAX_STEPS)
    _complete(job)


def _slump(field: HeightField) -> float:
    """Slump in batches until the material is still or this tick's time is spent; returns the thickness moved."""
    started: float = time.perf_counter()
    moved: float = 0.0
    while True:
        batch: float = field.relax(RELAX_STEPS)
        moved += batch
        if batch < field.cell * 1e-3 or time.perf_counter() - started >= RELAX_BUDGET:
            return moved


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
            _emit(job, context, elapsed)
            _fall(job, elapsed, now)
            job.field.keep_margin()
            i: Any
            j: Any
            start: Any
            i, j, start = job.field.floors_needed(FLOOR_CASTS)
            _find_floors(job, context, i, j, start)
            moved: float = _slump(job.field)
            if moved > 0.0:
                job.changed = True
            if job.changed and now - job.last_mesh >= MESH_INTERVAL:
                _write_mesh(job)
            falling: bool = job.pouring or bool(len(job.positions))
            still: bool = moved < job.field.cell * 1e-3
            job.quiet = 0 if falling or not still else job.quiet + 1
            timed_out: bool = not falling and now - job.last_landing > SETTLE_TIMEOUT
            if (job.quiet >= QUIET_TICKS or timed_out) and not _engaged(job):
                _complete(job)
                return None
        job.area.tag_redraw()
        return TICK if falling else SETTLE_TICK
    except (ReferenceError, RuntimeError, ValueError, AttributeError) as error:
        # The print was deleted, or its file or scene replaced.
        if str(error) != _LAST_ERROR:
            _LAST_ERROR = str(error)
            print(f"3D Printer: {error}")
        reset()
        return None


def _point_size(job: Job) -> float:
    """Pixels across one particle where the particles are falling, so large particles look large."""
    scale: float = float(bpy.context.preferences.system.ui_scale)
    region: Any = bpy.context.region
    view: Any = bpy.context.region_data
    middle: Any = Vector(job.positions.mean(axis=0).tolist())
    right: Any = view.view_rotation @ Vector((1.0, 0.0, 0.0))
    near: Any = view3d_utils.location_3d_to_region_2d(region, view, middle)
    far: Any = view3d_utils.location_3d_to_region_2d(region, view, middle + right * job.size)
    if near is None or far is None:
        return 5.0 * scale
    return max(3.0 * scale, min(48.0, float((far - near).length)))


def _draw() -> None:
    """The falling particles, as dots in the print's colour, at their size on screen."""
    global _SHADER, _LAST_ERROR
    job: Job | None = _JOB
    if job is None or job.positions is None or not len(job.positions) or bpy.context.area != job.area:
        return
    depth: str = str(gpu.state.depth_test_get())
    blend: str = str(gpu.state.blend_get())
    error: RuntimeError | ValueError | TypeError | AttributeError
    try:
        if _SHADER is None:
            _SHADER = gpu.shader.from_builtin("POINT_UNIFORM_COLOR")
        batch: Any = batch_for_shader(_SHADER, "POINTS", {"pos": job.positions.astype(np.float32)})
        gpu.state.depth_test_set("LESS_EQUAL")
        gpu.state.blend_set("ALPHA")
        gpu.state.point_size_set(_point_size(job))
        _SHADER.bind()
        _SHADER.uniform_float("color", (*job.color, 1.0))
        batch.draw(_SHADER)
    except (RuntimeError, ValueError, TypeError, AttributeError) as error:
        if str(error) != _LAST_ERROR:
            _LAST_ERROR = str(error)
            print(f"3D Printer particles: {error}")
    finally:
        gpu.state.point_size_set(1.0)
        gpu.state.depth_test_set(depth)
        gpu.state.blend_set(blend)


def _install() -> None:
    global _HANDLER
    if _HANDLER is None and not bpy.app.background:
        _HANDLER = bpy.types.SpaceView3D.draw_handler_add(_draw, (), "WINDOW", "POST_VIEW")


def flush() -> None:
    """Before saving: write the forming print's mesh and field so the file holds them."""
    job: Job | None = _JOB
    if job is not None:
        try:
            _write_mesh(job)
            _save(job.obj, job.field)
        except ReferenceError:
            reset()


def reset() -> None:
    """Undo, redo, or loading replaced the data: drop the forming print and every cached field."""
    global _JOB
    _JOB = None
    _FIELDS.clear()
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)


def shutdown() -> None:
    global _HANDLER
    reset()
    if _HANDLER is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_HANDLER, "WINDOW")
        _HANDLER = None
