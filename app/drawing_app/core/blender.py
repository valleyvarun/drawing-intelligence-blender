"""Dynamic Blender SDK boundary; application state and geometry stay explicitly typed."""

from importlib import import_module
from typing import Any

bpy: Any = import_module("bpy")
gpu: Any = import_module("gpu")
blf: Any = import_module("blf")
mathutils: Any = import_module("mathutils")
Vector: Any = mathutils.Vector
Matrix: Any = mathutils.Matrix
Quaternion: Any = mathutils.Quaternion
batch_for_shader: Any = import_module("gpu_extras.batch").batch_for_shader
view3d_utils: Any = import_module("bpy_extras.view3d_utils")
