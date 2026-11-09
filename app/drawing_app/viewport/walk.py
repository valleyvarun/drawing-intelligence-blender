"""Walk navigation: hold the right mouse button, look with the mouse, and move with W/A/S/D or the arrows.

The eye stays at the eye level above the first surface straight below it, or above the ground plane
(height 0) where there is none. There is no gravity or jumping. Releasing the button keeps the view;
Esc returns to where the walk started.
"""

from __future__ import annotations

import math
import time
from typing import Any

from ..core.blender import Vector, bpy, mathutils
from ..core.state import AppState, get_state

# Movement keys: (sideways, forward) steps.
KEYS: dict[str, tuple[float, float]] = {
    "W": (0.0, 1.0),
    "UP_ARROW": (0.0, 1.0),
    "S": (0.0, -1.0),
    "DOWN_ARROW": (0.0, -1.0),
    "A": (-1.0, 0.0),
    "LEFT_ARROW": (-1.0, 0.0),
    "D": (1.0, 0.0),
    "RIGHT_ARROW": (1.0, 0.0),
}
LOOK: float = 0.004  # radians per pixel of mouse movement
PITCH_LIMIT: float = math.radians(85.0)
SETTLE: float = 12.0  # the eye eases to its new height at this rate per second, stepping onto objects


def floor_height(context: Any, eye: Any) -> float:
    """Height of the first surface straight below the eye, or the ground plane (0) where there is none."""
    from ..tools.canvas import object_hit

    found: Any = object_hit(context, eye, Vector((0.0, 0.0, -1.0)))
    return float(found.position[2]) if found is not None else 0.0


def orientation(yaw: float, pitch: float) -> Any:
    """View rotation looking along heading `yaw` (0 is +Y) and `pitch` above the horizon."""
    return mathutils.Euler((math.pi / 2.0 + pitch, 0.0, yaw), "XYZ").to_quaternion()


class DRAWING_OT_walk(bpy.types.Operator):
    bl_idname: str = "drawing.walk"
    bl_label: str = "Walk"
    area: Any
    start: tuple[Any, Any, float, str]
    eye: Any
    yaw: float
    pitch: float
    distance: float
    center: tuple[int, int]
    pointer: tuple[int, int]
    keys: set[str]
    last: float
    timer: Any

    @classmethod
    def poll(cls, context: Any) -> bool:
        return context.area is not None and context.area.type == "VIEW_3D" and context.region is not None

    def invoke(self, context: Any, event: Any) -> set[str]:
        from .guides import eye_position, navigation_distance, update_clipping

        self.area = context.area
        view: Any = self.area.spaces.active.region_3d
        self.start = (
            view.view_location.copy(),
            view.view_rotation.copy(),
            float(view.view_distance),
            str(view.view_perspective),
        )
        forward: Any = view.view_rotation @ Vector((0.0, 0.0, -1.0))
        self.yaw = math.atan2(-float(forward.x), float(forward.y))
        self.pitch = max(-PITCH_LIMIT, min(PITCH_LIMIT, math.asin(max(-1.0, min(1.0, float(forward.z))))))
        # The orbit pivot stays this far ahead, so orbiting after a walk turns around what is in front.
        self.distance = navigation_distance(self.area)
        self.eye = eye_position(view)
        self.keys = set()
        self.last = time.monotonic()
        view.view_perspective = "PERSP"
        region: Any = context.region
        self.center = (int(region.x + region.width / 2), int(region.y + region.height / 2))
        self.pointer = (int(event.mouse_x), int(event.mouse_y))
        context.window.cursor_modal_set("NONE")
        context.window.cursor_warp(*self.center)
        self.eye.z = floor_height(context, self.eye) + self.eye_level(context)
        self.apply()
        update_clipping(self.area)
        self.timer = context.window_manager.event_timer_add(1.0 / 60.0, window=context.window)
        context.window_manager.modal_handler_add(self)
        get_state().status = "Walk: W A S D or arrows, Shift runs, Ctrl slows; Esc returns"
        return {"RUNNING_MODAL"}

    @staticmethod
    def eye_level(context: Any) -> float:
        """Eye level in Blender units; the setting is in metres."""
        return get_state().eye_level / max(float(context.scene.unit_settings.scale_length), 1e-9)

    def apply(self) -> None:
        view: Any = self.area.spaces.active.region_3d
        rotation: Any = orientation(self.yaw, self.pitch)
        view.view_rotation = rotation
        view.view_distance = self.distance
        view.view_location = self.eye - rotation @ Vector((0.0, 0.0, self.distance))
        self.area.tag_redraw()

    def step(self, context: Any, event: Any) -> None:
        state: AppState = get_state()
        now: float = time.monotonic()
        elapsed: float = min(0.1, now - self.last)
        self.last = now
        sideways: float = 0.0
        ahead: float = 0.0
        key: str
        for key in self.keys:
            sideways += KEYS[key][0]
            ahead += KEYS[key][1]
        length: float = math.hypot(sideways, ahead)
        if length > 0.0:
            scale: float = max(float(context.scene.unit_settings.scale_length), 1e-9)
            speed: float = state.walk_speed / scale * (2.0 if event.shift else 0.5 if event.ctrl else 1.0)
            forward: Any = Vector((-math.sin(self.yaw), math.cos(self.yaw), 0.0))
            right: Any = Vector((math.cos(self.yaw), math.sin(self.yaw), 0.0))
            self.eye += (forward * ahead + right * sideways) * (speed * elapsed / length)
        target: float = floor_height(context, self.eye) + self.eye_level(context)
        self.eye.z += (target - float(self.eye.z)) * min(1.0, elapsed * SETTLE)
        self.apply()

    def finish(self, context: Any) -> None:
        context.window_manager.event_timer_remove(self.timer)
        context.window.cursor_modal_restore()
        context.window.cursor_warp(*self.pointer)
        get_state().status = ""
        self.area.tag_redraw()

    def modal(self, context: Any, event: Any) -> set[str]:
        kind: str = str(event.type)
        if kind in ("MOUSEMOVE", "INBETWEEN_MOUSEMOVE"):
            dx: int = int(event.mouse_x) - self.center[0]
            dy: int = int(event.mouse_y) - self.center[1]
            if dx or dy:
                self.yaw -= dx * LOOK
                self.pitch = max(-PITCH_LIMIT, min(PITCH_LIMIT, self.pitch + dy * LOOK))
                context.window.cursor_warp(*self.center)
                self.apply()
            return {"RUNNING_MODAL"}
        if kind in KEYS:
            if event.value == "PRESS":
                self.keys.add(kind)
            elif event.value == "RELEASE":
                self.keys.discard(kind)
            return {"RUNNING_MODAL"}
        if kind == "TIMER":
            self.step(context, event)
            # Other timers, such as the drawing session's, keep running.
            return {"PASS_THROUGH"}
        if (kind == "RIGHTMOUSE" and event.value == "RELEASE") or kind == "WINDOW_DEACTIVATE":
            self.finish(context)
            return {"FINISHED"}
        if kind == "ESC" and event.value == "PRESS":
            view: Any = self.area.spaces.active.region_3d
            view.view_location, view.view_rotation, view.view_distance, view.view_perspective = self.start
            self.finish(context)
            return {"CANCELLED"}
        return {"RUNNING_MODAL"}
