"""Observe a real Blender event loop while a dense curve fill runs in its helper process."""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from drawing_app.core import state as state_module
from drawing_app.core.blender import Vector, bpy
from drawing_app.core.state import AppState, get_state
from drawing_app.tools import canvas, layers, pencil, pipe_fill


class AsyncFillProbe:
    def __init__(self) -> None:
        self.window: Any = bpy.context.window
        self.area: Any = None
        self.region: Any = None
        area: Any
        region: Any
        for area in self.window.screen.areas:
            if area.type == "VIEW_3D":
                self.area = area
                break
        assert self.area is not None
        for region in self.area.regions:
            if region.type == "WINDOW":
                self.region = region
                break
        assert self.region is not None
        self.view: Any = self.area.spaces.active.region_3d
        self.original_view: tuple[Any, Any, float, str] = (
            self.view.view_rotation.copy(),
            self.view.view_location.copy(),
            float(self.view.view_distance),
            str(self.view.view_perspective),
        )
        self.original_scene: Any = self.window.scene
        self.original_active: Any = bpy.context.view_layer.objects.active
        self.original_mode: str = self.original_active.mode if self.original_active is not None else "OBJECT"
        self.original_state: AppState = get_state()
        self.original_undo: Callable[[str], None] = pencil._undo_push
        self.original_collections: set[str] = set(bpy.data.collections.keys())
        self.original_objects: set[str] = set(bpy.data.objects.keys())
        self.original_meshes: set[str] = set(bpy.data.meshes.keys())
        self.original_materials: set[str] = set(bpy.data.materials.keys())
        self.scene: Any = None
        self.started: float = 0.0
        self.previous: float = 0.0
        self.beats: int = 0
        self.max_gap: float = 0.0
        self.saw_message: bool = False
        self.report: dict[str, object] = {"passed": False}
        self.output: Path = Path(__file__).resolve().parents[1] / ".checks/pipe-fill-async-report.json"

    @staticmethod
    def no_undo(_message: str) -> None:
        return

    def start(self) -> None:
        if pipe_fill.busy():
            raise RuntimeError("A user fill is already running")
        try:
            state_module._STATE = deepcopy(self.original_state)
            pencil._undo_push = self.no_undo
            with bpy.context.temp_override(window=self.window, area=self.area, region=self.region):
                if self.original_mode != "OBJECT":
                    bpy.ops.object.mode_set(mode="OBJECT")
                self.scene = bpy.data.scenes.new("Async fill event loop validation")
                self.window.scene = self.scene
                group: Any = bpy.data.collections.new("Async fill validation")
                group[layers.GROUP] = True
                self.scene.collection.children.link(group)
                get_state().active_group = group.name
                get_state().tool = "grease"
                get_state().pencil.tool = "builtin_brush.Fill"
                get_state().pencil.fill_opacity = 1.0
                mesh: Any = bpy.data.meshes.new("Curve-only async boundary")
                obj: Any = bpy.data.objects.new(mesh.name, mesh)
                layers.link_stroke(obj, bpy.context)
                obj["drawing_kind"] = "pipe"
                samples: list[float] = []
                index: int
                for index in range(8001):
                    angle: float = math.tau * index / 8000
                    samples.extend(
                        (
                            4.0 + math.cos(angle),
                            -3.0 + math.sin(angle),
                            5.0 + 0.04 * math.sin(3.0 * angle),
                            0.0,
                            1.0,
                            1.0,
                            0.0,
                            0.0,
                            1.0,
                        )
                    )
                obj["drawing_samples"] = samples
                bpy.context.view_layer.update()
                self.started = self.previous = time.perf_counter()
                with patch.object(canvas, "ray", return_value=(Vector((4.0, -3.0, 10.0)), Vector((0, 0, -1)))):
                    assert pipe_fill.start(bpy.context, SimpleNamespace(), self.area, self.region)
                assert pipe_fill.busy()
                self.report["start_ms"] = round((time.perf_counter() - self.started) * 1000.0, 3)
                self.report["samples"] = 8001
                self.report["source_has_small_out_of_plane_deviations"] = True
                self.report["phase"] = "running"
                self.output.write_text(json.dumps(self.report, indent=2), encoding="utf-8")
                bpy.app.timers.register(self.heartbeat, first_interval=0.005)
        except Exception:
            self.cleanup()
            raise

    def heartbeat(self) -> float | None:
        now: float = time.perf_counter()
        self.max_gap = max(self.max_gap, now - self.previous)
        self.previous = now
        error: Exception
        try:
            if now - self.started > 30.0:
                raise AssertionError("Async fill exceeded the probe's 30-second limit")
            if pipe_fill.busy():
                self.beats += 1
                self.saw_message = self.saw_message or bool(pipe_fill.message())
                # A normal viewport update executes between fill work slices.
                self.view.view_distance = self.original_view[2] * (1.0 + (self.beats % 2) * 0.001)
                self.view.update()
                self.area.tag_redraw()
                return 0.005
            obj: Any
            fill: Any = None
            for obj in self.scene.objects:
                if obj.get("drawing_kind") == "fill":
                    fill = obj
                    break
            assert fill is not None, get_state().status
            fitted_origin: Any = fill.data.vertices[0].co.copy()
            fitted_direction: Any = Vector((0.0, 0.0, 0.0))
            vertex: Any
            for vertex in fill.data.vertices:
                delta: Any = vertex.co - fitted_origin
                if delta.length_squared > fitted_direction.length_squared:
                    fitted_direction = delta
            fitted_normal: Any = Vector((0.0, 0.0, 0.0))
            for vertex in fill.data.vertices:
                normal: Any = fitted_direction.cross(vertex.co - fitted_origin)
                if normal.length_squared > fitted_normal.length_squared:
                    fitted_normal = normal
            fitted_normal.normalize()
            assert fitted_normal.length > 0.99
            maximum_offset: float = 0.0
            for vertex in fill.data.vertices:
                maximum_offset = max(maximum_offset, abs(float(vertex.co.z) - 5.0))
                assert abs((vertex.co - fitted_origin).dot(fitted_normal)) < 1e-5, vertex.co
            # The simplified wavy boundary's best fit may have a small tilt.
            assert maximum_offset < 0.001, maximum_offset
            polygon: Any
            area: float = 0.0
            for polygon in fill.data.polygons:
                area += float(polygon.area)
            assert abs(area - math.pi) < 0.001, area
            assert self.beats >= 2, self.beats
            assert not fill.select_get()
            assert not pipe_fill.message()
            self.report.update(
                passed=True,
                phase="complete",
                heartbeat_ticks_while_filling=self.beats,
                viewport_updates_while_filling=self.beats,
                heartbeat_max_gap_ms=round(self.max_gap * 1000.0, 3),
                elapsed_ms=round((now - self.started) * 1000.0, 3),
                saw_processing_message=self.saw_message,
                fill_area=area,
                fill_vertices=len(fill.data.vertices),
                maximum_plane_offset=maximum_offset,
            )
        except Exception as error:
            self.report.update(phase="failed", error=repr(error), heartbeat_ticks_while_filling=self.beats)
        finally:
            if not pipe_fill.busy() or self.report.get("phase") == "failed":
                self.cleanup()
        return None

    def cleanup(self) -> None:
        pipe_fill.cancel(close=True)
        try:
            with bpy.context.temp_override(window=self.window, area=self.area, region=self.region):
                active: Any = bpy.context.view_layer.objects.active
                if active is not None and active.mode != "OBJECT":
                    bpy.ops.object.mode_set(mode="OBJECT")
                self.window.scene = self.original_scene
                (
                    self.view.view_rotation,
                    self.view.view_location,
                    self.view.view_distance,
                    self.view.view_perspective,
                ) = self.original_view
                if self.original_active is not None and self.original_mode != "OBJECT":
                    bpy.ops.object.mode_set(mode=self.original_mode)
                self.view.update()
                self.area.tag_redraw()
            obj: Any
            for obj in tuple(bpy.data.objects):
                if obj.name not in self.original_objects:
                    bpy.data.objects.remove(obj, do_unlink=True)
            mesh: Any
            for mesh in tuple(bpy.data.meshes):
                if mesh.name not in self.original_meshes:
                    bpy.data.meshes.remove(mesh)
            material: Any
            for material in tuple(bpy.data.materials):
                if material.name not in self.original_materials:
                    bpy.data.materials.remove(material)
            if self.scene is not None:
                bpy.data.scenes.remove(self.scene)
                self.scene = None
            collection: Any
            for collection in tuple(bpy.data.collections):
                if collection.name not in self.original_collections:
                    bpy.data.collections.remove(collection)
        finally:
            state_module._STATE = self.original_state
            pencil._undo_push = self.original_undo
            self.output.write_text(json.dumps(self.report, indent=2), encoding="utf-8")
            print(json.dumps(self.report, indent=2))


PROBE: AsyncFillProbe = AsyncFillProbe()
PROBE.start()
print("Async fill probe scheduled; Blender's event loop owns subsequent work.")
