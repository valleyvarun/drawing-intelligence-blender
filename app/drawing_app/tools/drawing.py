"""Modal drawing gestures with Blender geometry, pressure, and native undo."""

from __future__ import annotations

import time
from copy import deepcopy
from typing import Any

from ..core.blender import Vector, bpy
from ..core.state import AppState, Brush, get_state
from . import canvas, layers
from .canvas import Hit, place_cursor
from .geometry import (
    Face,
    PaintCache,
    Sample,
    add,
    basis,
    fill_bands,
    grease_softness,
    mul,
    paint_mesh,
    pipe_mesh,
    profile,
    resample,
)
from .preview import BrushPreview
from .surface import PaintSurface


def material(brush: Brush, grease: bool = False) -> Any:
    result: Any = bpy.data.materials.new("Drawing Ink")
    result.diffuse_color = (*brush.color, brush.opacity)
    if grease:
        bpy.data.materials.create_gpencil_data(result)
        result.grease_pencil.color = (*brush.color, 1.0)
        result.grease_pencil.fill_color = (*brush.color, 1.0)
        result.grease_pencil.show_fill = brush.shape != "circle"
        result.grease_pencil.show_stroke = brush.shape == "circle"
        return result
    result.use_nodes = True
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
) -> None:
    points: list[tuple[float, float, float]]
    faces: list[Face]
    alphas: list[float]
    if surface is not None:
        points, faces, alphas = surface.paint(samples, brush.shape)
    else:
        points, faces, alphas = (
            paint_mesh(samples, brush.shape, cache) if kind == "paint" else pipe_mesh(samples, brush.shape)
        )
    mesh: Any = obj.data
    mesh.clear_geometry()
    mesh.from_pydata(points, [], faces)
    mesh.update()
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


def write_grease(obj: Any, samples: list[Sample], brush: Brush, frame: int) -> None:
    layer: Any = obj.data.layers[0]
    drawing: Any = layer.frames[0].drawing
    drawing.remove_strokes()
    if brush.shape == "circle":
        drawing.add_strokes([len(samples)])
        stroke: Any = drawing.strokes[0]
        hardness: float = 0.0
        sample: Sample
        for sample in samples:
            hardness += sample.hardness
        stroke.softness = grease_softness(hardness / max(1, len(samples)))
        point: Any
        for point, sample in zip(stroke.points, samples):
            point.position = sample.position
            point.radius = sample.radius
            point.opacity = sample.opacity
            point.vertex_color = (*brush.color, 1.0)
    else:
        outline: list[tuple[float, float]] = profile(brush.shape)
        fills: list[tuple[Sample, float, float]] = []
        radius: float
        alpha: float
        for sample in samples:
            for radius, alpha in fill_bands(sample):
                fills.append((sample, radius, alpha))
        drawing.add_strokes([len(outline)] * len(fills))
        index: int
        for index, (sample, radius, alpha) in enumerate(fills):
            stroke = drawing.strokes[index]
            stroke.cyclic = True
            stroke.fill_id = index + 1
            stroke.hide_stroke = True
            stroke.fill_opacity = alpha
            right: tuple[float, float, float]
            up: tuple[float, float, float]
            right, up = basis(sample.normal)
            xy: tuple[float, float]
            for point, xy in zip(stroke.points, outline):
                point.position = add(
                    sample.position, mul(add(mul(right, xy[0]), mul(up, xy[1])), sample.radius * radius)
                )
                point.radius = sample.radius * 0.02
                point.opacity = sample.opacity
    drawing.tag_positions_changed()
    obj.data.update_tag()


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
        self._last_upload: float = 0.0
        self._undo_started: bool = False
        self._preview: BrushPreview = BrushPreview()
        self._last_hover: float = 0.0
        self._paint_cache: PaintCache | None = None
        self._paint_surface: PaintSurface | None = None

    @property
    def is_drawing(self) -> bool:
        return self._object is not None

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
        if state.axes_spokes and not state.cursor_placing:
            pipe: Hit | None = canvas.pipe_hit(context, event, area, region, radius)
            if pipe is not None:
                location, recognized = pipe, pipe.source
        self._preview.update(area, region, location, radius, recognized)
        return location

    def begin(self, context: Any, event: Any, area: Any, region: Any) -> bool:
        if self.is_drawing or get_state().tool not in ("paint", "pipe", "grease"):
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
        self._brush = deepcopy(get_state().brush)
        self._kind = get_state().tool
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
        source: Any = bpy.data.objects.get(location.source)
        self._paint_surface = (
            PaintSurface(context, source, self._samples[0].radius, self._paint_cache.cell)
            if self._paint_cache is not None and source is not None and not get_state().axes_spokes
            else None
        )
        data: Any
        if self._kind == "grease":
            data = bpy.data.grease_pencils.new("Grease Stroke")
            layer: Any = data.layers.new("Ink", set_active=True)
            layer.frames.new(context.scene.frame_current)
        else:
            data = bpy.data.meshes.new("Paint Stroke" if self._kind == "paint" else "Pipe Stroke")
        self._object = bpy.data.objects.new(data.name, data)
        layers.link_stroke(self._object, context)
        self._object["drawing_preview"] = True
        self._object["drawing_kind"] = self._kind
        self._object["drawing_shape"] = self._brush.shape
        self._object.color = (*self._brush.color, self._brush.opacity)
        ink: Any = material(self._brush, self._kind == "grease")
        if self._kind == "grease":
            self._object.data.materials.append(ink)
        else:
            level: int
            for level in range(16):
                entry: Any = ink if level == 0 else ink.copy()
                entry.name = f"Drawing Ink {level:02d}"
                entry.diffuse_color = (*self._brush.color, level / 15.0)
                self._object.data.materials.append(entry)
        self._object.show_wire = False
        self._upload()
        return True

    def _upload(self) -> None:
        if self._object is None:
            return
        if self._kind == "grease":
            write_grease(self._object, self._samples, self._brush, self._scene.frame_current)
        else:
            write_mesh(self._object, self._samples, self._brush, self._kind, self._paint_cache, self._paint_surface)
        self._last_upload = time.monotonic()
        self._area.tag_redraw()

    def handle(self, context: Any, event: Any, area: Any, region: Any) -> bool:
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
                if self._source in ("ground", "plane", "cursor"):
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
                    self._source not in ("ground", "plane", "cursor")
                    and location is not None
                    and location.source != self._source
                ):
                    location = None
                if get_state().axes_spokes:
                    if self._axis is None:
                        self._axis = canvas.choose_axis(self._samples[0].position, event, area, region)
                    if self._axis is not None:
                        position: tuple[float, float, float] | None = canvas.axis_position(
                            self._samples[0].position, self._axis, event, area, region
                        )
                        if position is not None:
                            location = Hit(position, self._samples[0].normal, "axis")
                if location is not None:
                    self._samples.extend(resample(self._samples[-1], self._sample(context, event, location)))
                    self._preview.update(area, region, location, self._samples[-1].radius)
                    if time.monotonic() - self._last_upload >= 1.0 / 30.0:
                        self._upload()
                return True
            return event.type in ("LEFTMOUSE", "RIGHTMOUSE")
        state: AppState = get_state()
        if state.cursor_placing and event.type == "ESC" and event.value == "PRESS":
            state.cursor_placing = False
            state.status = "Cursor placement cancelled"
            self._preview.clear()
            return True
        if event.type in ("MOUSEMOVE", "INBETWEEN_MOUSEMOVE") and (
            state.tool in ("paint", "pipe", "grease") or state.cursor_placing
        ):
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
        if self.is_drawing:
            self._finish(context)

    def _finish(self, context: Any) -> None:
        from ..core.project import persist_scene

        self._upload()
        obj: Any = self._object
        if obj is None:
            return
        obj["drawing_preview"] = False
        values: list[float] = []
        sample: Sample
        for sample in self._samples:
            values.extend((*sample.position, sample.radius, sample.hardness, sample.opacity, *sample.normal))
        obj["drawing_samples"] = values
        previous: Any
        for previous in tuple(context.selected_objects):
            previous.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        get_state().status = f"{self._kind.title()} stroke · {len(self._samples)} samples"
        self._object = None
        self._samples = []
        self._paint_cache = None
        self._paint_surface = None
        if bpy.ops.ed.undo_push.poll():
            persist_scene()
            bpy.ops.ed.undo_push(message=f"3D {self._kind} stroke")

    def cancel(self) -> None:
        obj: Any = self._object
        if obj is not None:
            data: Any = obj.data
            materials: list[Any] = list(data.materials)
            bpy.data.objects.remove(obj, do_unlink=True)
            if data.users == 0:
                (bpy.data.grease_pencils if self._kind == "grease" else bpy.data.meshes).remove(data)
            item: Any
            for item in materials:
                if item.users == 0:
                    bpy.data.materials.remove(item)
        self._object = None
        self._samples = []
        self._paint_cache = None
        self._paint_surface = None
        self._axis = None
        self._preview.clear()
        get_state().status = "Stroke cancelled"

    def clear_hover(self) -> None:
        self._preview.clear()

    def destroy(self) -> None:
        self.cancel()
        self._preview.destroy()
