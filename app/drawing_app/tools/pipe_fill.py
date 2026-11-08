"""Asynchronous curve fill; Blender data is read and committed only on the UI thread."""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Literal

from ..core.blender import bpy
from ..core.state import ROOT, Vec3, get_state
from ..viewport import fills
from . import canvas, layers, pencil
from .fill_process import FillProcess, FillResult
from .pipe_edit import pipe_objects


def _empty_steps() -> Iterator[None]:
    return iter(())


@dataclass
class Source:
    name: str
    pointer: int
    matrix: tuple[float, ...]
    points: list[Vec3] = field(default_factory=list)


@dataclass
class Job:
    window: Any
    area: Any
    region: Any
    scene: int
    group: str
    sources: list[Source]
    origin: Vec3
    direction: Vec3
    color: tuple[float, float, float, float]
    started: float = field(default_factory=time.monotonic)
    paths: list[list[Vec3]] = field(default_factory=list)
    stage: Literal["snapshot", "compute", "validate"] = "snapshot"
    steps: Iterator[None] = field(default_factory=_empty_steps)
    result: FillResult | None = None


_JOB: Job | None = None
_PROCESS: FillProcess | None = None


def _matrix(obj: Any) -> tuple[float, ...]:
    values: list[float] = []
    row: Any
    value: float
    for row in obj.matrix_world:
        for value in row:
            values.append(float(value))
    return tuple(values)


def _snapshot(job: Job, *, validate: bool = False) -> Iterator[None]:
    source: Source
    for source in job.sources:
        obj: Any = bpy.data.objects.get(source.name)
        if obj is None or int(obj.as_pointer()) != source.pointer or _matrix(obj) != source.matrix:
            raise ValueError("The pipe curves changed; click Fill again")
        values: Any = obj.get("drawing_samples", ())
        count: int = len(values)
        if count % 9:
            raise ValueError("This pipe has no valid curve data")
        if validate and len(source.points) != count // 9:
            raise ValueError("The pipe curves changed; click Fill again")
        path: list[Vec3] = []
        offset: int
        index: int
        for offset in range(0, count, 256 * 9):
            obj = bpy.data.objects.get(source.name)
            if obj is None or int(obj.as_pointer()) != source.pointer:
                raise ValueError("The pipe curves changed; click Fill again")
            values = obj.get("drawing_samples", ())
            if len(values) != count:
                raise ValueError("The pipe curves changed; click Fill again")
            for index in range(offset, min(count, offset + 256 * 9), 9):
                point: Vec3 = (float(values[index]), float(values[index + 1]), float(values[index + 2]))
                if validate:
                    if point != source.points[index // 9]:
                        raise ValueError("The pipe curves changed; click Fill again")
                else:
                    source.points.append(point)
                    m: tuple[float, ...] = source.matrix
                    x: float
                    y: float
                    z: float
                    x, y, z = point
                    path.append(
                        (
                            m[0] * x + m[1] * y + m[2] * z + m[3],
                            m[4] * x + m[5] * y + m[6] * z + m[7],
                            m[8] * x + m[9] * y + m[10] * z + m[11],
                        )
                    )
            yield None
        if not validate:
            job.paths.append(path)


def busy() -> bool:
    return _JOB is not None


def message() -> str:
    return "Fill processing ..." if _JOB is not None and time.monotonic() - _JOB.started >= 0.2 else ""


def cancel(*, close: bool = False) -> None:
    global _JOB, _PROCESS
    _JOB = None
    if _PROCESS is not None:
        if close:
            _PROCESS.close()
            _PROCESS = None
        elif _PROCESS.busy:
            _PROCESS.cancel()
    if bpy.app.timers.is_registered(tick):
        bpy.app.timers.unregister(tick)


def _valid(context: Any, job: Job) -> bool:
    if (
        int(context.scene.as_pointer()) != job.scene
        or get_state().active_group != job.group
        or get_state().tool != "grease"
        or get_state().pencil.tool != "builtin_brush.Fill"
    ):
        return False
    objects: list[Any] = pipe_objects(context)
    if len(objects) != len(job.sources):
        return False
    obj: Any
    source: Source
    for obj, source in zip(objects, job.sources, strict=True):
        if obj.name != source.name or int(obj.as_pointer()) != source.pointer or _matrix(obj) != source.matrix:
            return False
    return True


def _native_fill(context: Any) -> bool:
    native: Any = context.view_layer.objects.active
    if pencil.active(context):
        layer: Any
        for layer in native.data.layers:
            frame: Any = layer.current_frame()
            if frame is not None and len(frame.drawing.strokes) and not layer.hide:
                bpy.ops.grease_pencil.fill("INVOKE_DEFAULT")
                return True
    return False


def _apply(context: Any, job: Job, result: FillResult) -> None:
    if not result.faces:
        if _native_fill(context):
            return
        get_state().status = "No fillable area on the curve's plane at the pointer"
        return
    if not layers.can_draw(context):
        get_state().status = "Unlock the drawing group to fill"
        return
    mesh: Any = bpy.data.meshes.new("Pipe Fill")
    mesh.from_pydata(result.points, [], result.faces)
    obj: Any = bpy.data.objects.new("Pipe Fill", mesh)
    layers.link_stroke(obj, context)
    obj["drawing_kind"] = "fill"
    obj.color = job.color
    material: Any = bpy.data.materials.new("Pipe Fill Ink")
    material.diffuse_color = job.color
    material.surface_render_method = "DITHERED"
    shader: Any
    for shader in material.node_tree.nodes:
        if shader.type == "BSDF_PRINCIPLED":
            shader.inputs["Base Color"].default_value = job.color
            shader.inputs["Alpha"].default_value = job.color[3]
            shader.inputs["Roughness"].default_value = 1.0
            break
    mesh.materials.append(material)
    fills.prepare(obj)
    get_state().status = "Filled the pipe curves on their plane"
    pencil._undo_push("Fill pipe boundary")


def tick() -> float | None:
    """Poll a separate process and keep snapshot work within a short UI time slice."""
    global _JOB
    job: Job | None = _JOB
    if job is None:
        return None
    error: OSError | RuntimeError | ValueError | ReferenceError
    try:
        if job.window not in tuple(bpy.context.window_manager.windows) or job.area not in tuple(
            job.window.screen.areas
        ):
            cancel()
            return None
        with bpy.context.temp_override(window=job.window, area=job.area, region=job.region):
            if not _valid(bpy.context, job):
                cancel()
                return None
            assert _PROCESS is not None
            if job.stage == "compute":
                result: FillResult | None = _PROCESS.poll()
                if result is None:
                    job.area.tag_redraw()
                    return 0.02
                if result.error:
                    raise RuntimeError(result.error)
                job.result = result
                job.stage = "validate"
                job.steps = _snapshot(job, validate=True)
            deadline: float = time.monotonic() + 0.004
            while time.monotonic() < deadline:
                try:
                    next(job.steps)
                except StopIteration:
                    if job.stage == "snapshot":
                        if not _PROCESS.submit(job.paths, job.origin, job.direction):
                            raise RuntimeError("The fill worker is unavailable") from None
                        job.stage = "compute"
                    else:
                        assert job.result is not None
                        _JOB = None
                        _apply(bpy.context, job, job.result)
                        job.area.tag_redraw()
                        return None
                    break
            job.area.tag_redraw()
            return 0.02
    except (OSError, RuntimeError, ValueError, ReferenceError) as error:
        cancel()
        get_state().status = "Fill: " + str(error)
        print(f"Pipe fill: {error}")
        return None


def start(context: Any, event: Any, area: Any, region: Any) -> bool:
    global _JOB, _PROCESS
    if _JOB is not None:
        return True
    objects: list[Any] = pipe_objects(context)
    if not objects:
        return False
    if not layers.can_draw(context):
        get_state().status = "Unlock the drawing group to fill"
        return True
    origin: Any
    direction: Any
    origin, direction = canvas.ray(event, area, region)
    color: Vec3 = (0.0, 0.0, 0.0)
    name: str
    value: Vec3
    for name, value in pencil.COLORS:
        if name == get_state().pencil.material:
            color = value
            break
    rgba: tuple[float, float, float, float] = (*color, 1.0)
    native: Any = context.view_layer.objects.active
    if native is not None and native.type == "GREASEPENCIL" and native.active_material is not None:
        style: Any = native.active_material.grease_pencil
        if style is not None:
            selected: Any = style.fill_color if style.fill_color[3] > 0.0 else style.color
            rgba = (float(selected[0]), float(selected[1]), float(selected[2]), float(selected[3]))
    rgba = (rgba[0], rgba[1], rgba[2], get_state().pencil.fill_opacity)
    obj: Any
    sources: list[Source] = []
    for obj in objects:
        sources.append(Source(obj.name, int(obj.as_pointer()), _matrix(obj)))
    job: Job = Job(
        context.window,
        area,
        region,
        int(context.scene.as_pointer()),
        get_state().active_group,
        sources,
        canvas.vector_tuple(origin),
        canvas.vector_tuple(direction),
        rgba,
    )
    job.steps = _snapshot(job)
    if _PROCESS is None:
        _PROCESS = FillProcess(ROOT)
    _JOB = job
    if not bpy.app.timers.is_registered(tick):
        bpy.app.timers.register(tick, first_interval=0.0)
    return True
