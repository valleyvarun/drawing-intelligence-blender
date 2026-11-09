"""Modal drawing gestures with Blender geometry, pressure, and native undo."""

from __future__ import annotations

import math
import time
from copy import deepcopy
from typing import Any

from ..core.blender import Vector, bpy
from ..core.state import NOZZLE_TOOLS, STROKE_TOOLS, AppState, Brush, Vec3, get_state
from . import canvas, layers
from .canvas import Hit, place_cursor
from .geometry import Face, PaintCache, PipeCache, Sample, paint_mesh, pipe_mesh, resample
from .preview import BrushPreview
from .surface import PaintSurface


def _flat(location: Hit | None) -> Hit | None:
    """The same point, facing up: a nozzle tool's footprint is horizontal."""
    return Hit(location.position, (0.0, 0.0, 1.0), location.source) if location is not None else None


def _nozzle_start(tool: str, context: Any, area: Any, region: Any, location: Hit) -> bool:
    """Start the 3D Printer's pour or the 3D Extruder's tube at a canvas point."""
    from . import extruder, printer

    if tool == "extruder":
        return extruder.start(context, area, region, location)
    return printer.start(context, area, region, location)


def _nozzle_aim(tool: str, location: Hit | None) -> None:
    from . import extruder, printer

    if tool == "extruder":
        extruder.aim(location)
    else:
        printer.aim(location)


def _nozzle_release(tool: str) -> None:
    from . import extruder, printer

    if tool == "extruder":
        extruder.release()
    else:
        printer.release()


def material(brush: Brush) -> Any:
    result: Any = bpy.data.materials.new("Drawing Ink")
    result.diffuse_color = (*brush.color, brush.opacity)
    result.surface_render_method = "DITHERED"
    shader: Any = result.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Roughness"].default_value = 1.0
    attribute: Any = result.node_tree.nodes.new("ShaderNodeVertexColor")
    attribute.layer_name = "Ink"
    result.node_tree.links.new(attribute.outputs["Color"], shader.inputs["Base Color"])
    result.node_tree.links.new(attribute.outputs["Alpha"], shader.inputs["Alpha"])
    return result


def write_mesh(
    obj: Any,
    samples: list[Sample],
    brush: Brush,
    kind: str,
    cache: PaintCache | None = None,
    surface: PaintSurface | None = None,
    pipe_cache: PipeCache | None = None,
) -> None:
    points: list[tuple[float, float, float]]
    faces: list[Face]
    alphas: list[float]
    if surface is not None:
        points, faces, alphas = surface.paint(samples, brush.shape)
    else:
        points, faces, alphas = (
            paint_mesh(samples, brush.shape, cache) if kind == "paint" else pipe_mesh(samples, brush.shape, pipe_cache)
        )
    mesh: Any = obj.data
    mesh.clear_geometry()
    mesh.from_pydata(points, [], faces, shade_flat=kind != "pipe" or brush.shape != "circle")
    color: Any = mesh.color_attributes.get("Ink") or mesh.color_attributes.new(
        name="Ink", type="FLOAT_COLOR", domain="POINT"
    )
    data: list[float] = []
    alpha: float
    for alpha in alphas:
        data.extend((*brush.color, alpha))
    color.data.foreach_set("color", data)
    material_indices: list[int] = []
    face: Face
    for face in faces:
        total: float = 0.0
        index: int
        for index in face:
            total += alphas[index]
        average: float = total / max(1, len(face))
        material_indices.append(min(15, max(0, round(average * 15.0))))
    mesh.polygons.foreach_set("material_index", material_indices)
    mesh.polygons.foreach_set("use_smooth", [kind == "pipe" and brush.shape == "circle"] * len(faces))
    mesh.update()


def stroke_object(context: Any, kind: str, brush: Brush) -> Any:
    """A new paint or pipe stroke in the active group, with one material per opacity level."""
    data: Any = bpy.data.meshes.new("Paint Stroke" if kind == "paint" else "Pipe Stroke")
    obj: Any = bpy.data.objects.new(data.name, data)
    layers.link_stroke(obj, context)
    obj["drawing_preview"] = True
    obj["drawing_kind"] = kind
    obj["drawing_shape"] = brush.shape
    obj.color = (*brush.color, brush.opacity)
    ink: Any = material(brush)
    level: int
    for level in range(16):
        entry: Any = ink if level == 0 else ink.copy()
        entry.name = f"Drawing Ink {level:02d}"
        entry.diffuse_color = (*brush.color, level / 15.0)
        obj.data.materials.append(entry)
    obj.show_wire = False
    return obj


def store_samples(obj: Any, samples: list[Sample]) -> None:
    """Finish a stroke: keep its pressure samples for Trim, Fill, and rebuilding its mesh."""
    obj["drawing_preview"] = False
    values: list[float] = []
    sample: Sample
    for sample in samples:
        values.extend((*sample.position, sample.radius, sample.hardness, sample.opacity, *sample.normal))
    obj["drawing_samples"] = values


class DrawingController:
    def __init__(self) -> None:
        self._object: Any = None
        self._area: Any = None
        self._region: Any = None
        self._scene: Any = None
        self._samples: list[Sample] = []
        self._brush: Brush = Brush()
        self._kind: str = "paint"
        self._source: str = "ground"
        self._axis: int | None = None
        self._axis_origin: Vec3 = (0.0, 0.0, 0.0)
        self._axis_offset: float = 0.0
        self._axis_pointer: tuple[float, float] = (0.0, 0.0)
        self._axis_previous: tuple[float, float] = (0.0, 0.0)
        self._axis_tip: Sample | None = None
        self._last_upload: float = 0.0
        self._undo_started: bool = False
        self._preview: BrushPreview = BrushPreview()
        self._last_hover: float = 0.0
        self._paint_cache: PaintCache | None = None
        self._paint_surface: PaintSurface | None = None
        self._pipe_cache: PipeCache | None = None
        self._uploaded_samples: int = 0
        # 3D Printer or 3D Extruder: the button is held and material is flowing; the start is the canvas it follows.
        self._pouring: bool = False
        self._pour_tool: str = ""
        self._pour_start: Hit | None = None

    @property
    def is_drawing(self) -> bool:
        return self._object is not None or self._pouring

    @property
    def viewport_context(self) -> tuple[Any, Any] | None:
        return (self._area, self._region) if self.is_drawing else None

    def _sample(self, context: Any, event: Any, hit: Hit) -> Sample:
        brush: Brush = self._brush
        pressure: float = max(0.0, min(1.0, float(event.pressure))) if bool(getattr(event, "is_tablet", False)) else 1.0
        unit_scale: float = max(1e-12, float(context.scene.unit_settings.scale_length))
        return Sample(
            hit.position,
            max(1e-6, brush.radius / unit_scale * (pressure if brush.pressure_size else 1.0)),
            brush.hardness * (pressure if brush.pressure_hardness else 1.0),
            brush.opacity * (pressure if brush.pressure_opacity else 1.0),
            hit.normal,
        )

    def _target(self, context: Any, event: Any, area: Any, region: Any) -> Hit | None:
        state: AppState = get_state()
        radius: float = state.brush.radius / max(1e-12, float(context.scene.unit_settings.scale_length))
        location: Hit | None = canvas.hit(context, event, area, region, placing=state.cursor_placing)
        recognized: str = ""
        if state.tool in NOZZLE_TOOLS and not state.cursor_placing:
            # Material falls straight down, so the nozzle's footprint lies flat.
            self._preview.update(area, region, _flat(location), radius)
            return location
        if state.axes_spokes and not state.cursor_placing:
            pipe: Hit | None = canvas.pipe_hit(context, event, area, region, radius)
            if pipe is not None:
                location, recognized = pipe, pipe.source
        self._preview.update(area, region, location, radius, recognized)
        return location

    def begin(self, context: Any, event: Any, area: Any, region: Any) -> bool:
        if self.is_drawing or get_state().tool not in STROKE_TOOLS:
            return False
        location: Hit | None = self._target(context, event, area, region)
        if location is None:
            get_state().status = "No drawing surface under the pointer"
            return True
        layers.ensure_scene(context)
        if not layers.can_draw(context):
            get_state().status = "The active drawing group is hidden or locked"
            return True
        if not self._undo_started and bpy.ops.ed.undo_push.poll():
            bpy.ops.ed.undo_push(message="Drawing session")
            self._undo_started = True
        if get_state().tool in NOZZLE_TOOLS:
            self._area, self._region, self._scene = area, region, context.scene
            self._pour_start = location
            self._pour_tool = get_state().tool
            self._pouring = _nozzle_start(self._pour_tool, context, area, region, location)
            return True
        self._brush = deepcopy(get_state().brush)
        self._kind = get_state().tool
        self._pipe_cache = PipeCache() if self._kind == "pipe" else None
        self._uploaded_samples = 0
        self._paint_cache = (
            PaintCache(
                max(1e-6, self._brush.radius / max(1e-12, float(context.scene.unit_settings.scale_length)) * 0.125)
            )
            if self._kind == "paint"
            else None
        )
        self._area, self._region, self._scene = area, region, context.scene
        self._samples = [self._sample(context, event, location)]
        self._source = location.source
        self._axis = None
        self._axis_origin = location.position
        self._axis_offset = 0.0
        self._axis_pointer = self._axis_previous = (float(event.mouse_x), float(event.mouse_y))
        self._axis_tip = self._samples[0]
        source: Any = bpy.data.objects.get(location.source)
        self._paint_surface = (
            PaintSurface(context, source, self._samples[0].radius, self._paint_cache.cell)
            if self._paint_cache is not None and source is not None and not get_state().axes_spokes
            else None
        )
        self._object = stroke_object(context, self._kind, self._brush)
        self._upload()
        return True

    def _upload(self) -> None:
        if self._object is None or self._uploaded_samples == len(self._samples):
            return
        started: float = time.monotonic()
        write_mesh(
            self._object,
            self._samples,
            self._brush,
            self._kind,
            self._paint_cache,
            self._paint_surface,
            self._pipe_cache,
        )
        self._uploaded_samples = len(self._samples)
        self._last_upload = started
        self._area.tag_redraw()

    def tick(self) -> None:
        """Flush pending samples at display cadence, even when mouse movement stops."""
        fps: int = min(60, get_state().fps_limit or 60)
        if self.is_drawing and time.monotonic() - self._last_upload >= 1.0 / fps:
            self._upload()

    def _axis_target(self, context: Any, event: Any, area: Any, region: Any) -> Hit | None:
        """Continue a constrained segment or turn onto another axis at its current tip."""
        tip: Sample | None = self._axis_tip
        if tip is None:
            return None
        pointer: tuple[float, float] = (float(event.mouse_x), float(event.mouse_y))
        motion: tuple[float, float] = (pointer[0] - self._axis_pointer[0], pointer[1] - self._axis_pointer[1])
        threshold: float = 6.0 * max(1.0, float(context.preferences.system.ui_scale))
        chosen: int | None = canvas.choose_axis(
            tip.position, event, area, region, current=self._axis, motion=motion, minimum=threshold
        )
        reference: tuple[float, float] = self._axis_pointer if self._axis is None else self._axis_previous
        self._axis_previous = pointer
        if math.hypot(*motion) >= threshold:
            self._axis_pointer = pointer
        if chosen is not None and chosen != self._axis:
            baseline: Vec3 | None = canvas.axis_position(tip.position, chosen, event, area, region, pointer=reference)
            if baseline is not None:
                # Commit the corner even when it is shorter than normal sample spacing.
                if tip.position != self._samples[-1].position:
                    self._samples.append(tip)
                self._axis = chosen
                self._axis_origin = tip.position
                self._axis_offset = baseline[chosen] - tip.position[chosen]
        if self._axis is None:
            return None
        position: Vec3 | None = canvas.axis_position(self._axis_origin, self._axis, event, area, region)
        if position is None:
            return None
        adjusted: list[float] = list(position)
        adjusted[self._axis] -= self._axis_offset
        return Hit((adjusted[0], adjusted[1], adjusted[2]), tip.normal, "axis")

    def _pour_location(self, context: Any, event: Any) -> Hit | None:
        """Where the nozzle is now: on the canvas the pour started on, like a paint stroke."""
        start: Hit | None = self._pour_start
        if start is None:
            return None
        if start.source in ("ground", "plane", "cursor"):
            origin: Any
            direction: Any
            origin, direction = canvas.ray(event, self._area, self._region)
            return canvas.intersect(origin, direction, Vector(start.position), Vector(start.normal), start.source)
        return canvas.hit(context, event, self._area, self._region)

    def _stop_pour(self) -> None:
        """Stop the flow; material already falling still lands and the print or tube keeps forming."""
        if self._pouring:
            _nozzle_release(self._pour_tool)
        self._pouring = False
        self._pour_start = None

    def handle(self, context: Any, event: Any, area: Any, region: Any) -> bool:
        if self._pouring:
            if context.scene != self._scene or get_state().tool != self._pour_tool:
                self._stop_pour()
                return False
            if (event.type == "ESC" and event.value == "PRESS") or (
                event.type == "LEFTMOUSE" and event.value == "RELEASE"
            ):
                self._stop_pour()
                return True
            if event.type in ("MOUSEMOVE", "INBETWEEN_MOUSEMOVE"):
                location: Hit | None = self._pour_location(context, event)
                _nozzle_aim(self._pour_tool, location)
                radius: float = get_state().brush.radius / max(1e-12, float(context.scene.unit_settings.scale_length))
                self._preview.update(self._area, self._region, _flat(location), radius)
                return True
            return event.type in ("LEFTMOUSE", "RIGHTMOUSE")
        if self.is_drawing:
            if context.scene != self._scene:
                self.cancel()
                return False
            if get_state().tool != self._kind:
                self._finish(context)
                return False
            area, region = self._area, self._region
            if event.type == "ESC" and event.value == "PRESS":
                self.cancel()
                return True
            if event.type == "LEFTMOUSE" and event.value == "RELEASE":
                self._finish(context)
                return True
            if event.type in ("MOUSEMOVE", "INBETWEEN_MOUSEMOVE"):
                location: Hit | None
                if get_state().axes_spokes:
                    location = self._axis_target(context, event, area, region)
                elif self._source in ("ground", "plane", "cursor"):
                    origin: Any
                    direction: Any
                    origin, direction = canvas.ray(event, area, region)
                    location = canvas.intersect(
                        origin,
                        direction,
                        Vector(self._samples[0].position),
                        Vector(self._samples[0].normal),
                        self._source,
                    )
                else:
                    location = canvas.hit(context, event, area, region)
                if (
                    not get_state().axes_spokes
                    and self._source not in ("ground", "plane", "cursor")
                    and location is not None
                    and location.source != self._source
                ):
                    location = None
                if location is not None:
                    sample: Sample = self._sample(context, event, location)
                    if get_state().axes_spokes:
                        self._axis_tip = sample
                    self._samples.extend(resample(self._samples[-1], sample))
                    self._preview.update(area, region, location, self._samples[-1].radius)
                    self.tick()
                return True
            return event.type in ("LEFTMOUSE", "RIGHTMOUSE")
        state: AppState = get_state()
        if state.cursor_placing and event.type == "ESC" and event.value == "PRESS":
            state.cursor_placing = False
            state.status = "Cursor placement cancelled"
            self._preview.clear()
            return True
        if event.type in ("MOUSEMOVE", "INBETWEEN_MOUSEMOVE") and (state.tool in STROKE_TOOLS or state.cursor_placing):
            now: float = time.monotonic()
            if now - self._last_hover >= 1.0 / 60.0:
                self._target(context, event, area, region)
                self._last_hover = now
        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            if state.cursor_active and state.cursor_placing:
                if place_cursor(context, event, area, region) and bpy.ops.ed.undo_push.poll():
                    from ..core.project import persist_scene

                    persist_scene()
                    bpy.ops.ed.undo_push(message="Place 3D cursor")
                return True
            return self.begin(context, event, area, region)
        return False

    def finish(self, context: Any) -> None:
        if self._pouring:
            self._stop_pour()
        elif self.is_drawing:
            self._finish(context)

    def _finish(self, context: Any) -> None:
        from ..core.project import persist_scene

        if get_state().axes_spokes and self._axis_tip is not None and self._samples:
            if self._axis_tip.position != self._samples[-1].position:
                self._samples.append(self._axis_tip)
        self._upload()
        obj: Any = self._object
        if obj is None:
            return
        store_samples(obj, self._samples)
        get_state().status = f"{self._kind.title()} stroke · {len(self._samples)} samples"
        self._object = None
        self._samples = []
        self._paint_cache = None
        self._paint_surface = None
        self._pipe_cache = None
        self._axis = None
        self._axis_tip = None
        if bpy.ops.ed.undo_push.poll():
            persist_scene()
            bpy.ops.ed.undo_push(message=f"3D {self._kind} stroke")

    def cancel(self) -> None:
        self._stop_pour()
        obj: Any = self._object
        if obj is not None:
            data: Any = obj.data
            materials: list[Any] = list(data.materials)
            bpy.data.objects.remove(obj, do_unlink=True)
            if data.users == 0:
                bpy.data.meshes.remove(data)
            item: Any
            for item in materials:
                if item.users == 0:
                    bpy.data.materials.remove(item)
        self._object = None
        self._samples = []
        self._paint_cache = None
        self._paint_surface = None
        self._pipe_cache = None
        self._axis = None
        self._axis_tip = None
        self._preview.clear()
        get_state().status = "Stroke cancelled"

    def clear_hover(self) -> None:
        self._preview.clear()

    def destroy(self) -> None:
        self.cancel()
        self._preview.destroy()
