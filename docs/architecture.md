# Architecture

`run.ps1` selects the Blender executable and isolates its environment. `app/bootstrap.py` imports `drawing_app`, whose lifecycle registers commands, restores state, prepares the drawing scene, builds the workspace, and starts one modal input router. No Kit or Omniverse modules are imported.

| Module | Responsibility |
| --- | --- |
| `core/blender.py` | Explicit boundary for dynamically typed Blender SDK modules. |
| `core/state.py` | Typed application/brush state and local preferences. Physical brush dimensions are metres. |
| `core/controller.py` | Command dispatch, input capture, shortcuts, native transforms, and lifecycle-safe routing. |
| `core/project.py` | Native project dialogs, scene metadata, and Blender USD operators. |
| `tools/canvas.py` | View rays, plane/object/cursor intersections, grid snapping, and axis constraints. |
| `tools/geometry.py` | Pressure samples, resampling, pipe meshes, and sparse paint fields. |
| `tools/surface.py` | Cached native BVH queries and non-overlapping paint patches clipped to source triangles. |
| `tools/drawing.py` | Live gestures, mesh/Grease Pencil data, material alpha, commit/cancel, and native undo. |
| `tools/layers.py` | Drawing collections, nested ownership, active groups, planes, and inherited visibility/locks. |
| `tools/preview.py` | Transient brush footprint, axis guides, and completed-pipe recognition. |
| `viewport/navigation.py` | Orbit/fly camera math, cursor zoom, pan, and orientation interaction. |
| `viewport/touch.py` | Windows touch input bridge; pen events remain native Blender input. |
| `viewport/guides.py` | Native Workbench configuration, ruler depth passes, labels, outlines, and orientation drawing. |
| `ui/layout.py` | Three real, resizable editor areas, prepared in stages as Blender initializes them. |
| `ui/overlay.py`, `paint.py`, `menus.py` | Tool/layer/object controls, field editing, menus, icon drawing, and input capture. |

The center is a real Blender `VIEW_3D`, not a rendered image embedded in another toolkit. Side panels use native editor regions with application-owned GPU controls. They therefore do not alter the viewport's projection or picking coordinates. Native transforms, drawing geometry, and guides share Blender's depth buffer.

Workbench remains responsible for mesh shading, the infinite adaptive grid, antialiasing, and hardware handling. The custom ruler uses a depth-tested pass plus a faint occluded pass. The application uses native mesh wire overlays for all visible polygon edges, excluding strokes and helper geometry. Grease Pencil is native Blender data.

Clip distances follow the local view scale to preserve depth precision for paint on surfaces, including orthographic views. They do not grow merely because the camera travels far from the world origin in XY. Paint fields cache affected cells and source patches so drawing updates rebuild the changed portion of a stroke.

Construction coordinates are Blender units internally. UI positions and brush dimensions convert through the scene's `scale_length`; loading a non-metre project preserves its unit scale. Ruler units are display preferences. The app records canvas/group/brush metadata in scene custom properties and keeps viewport preferences in JSON.

The router preserves captured gestures across editor boundaries, releases them on deactivation, and separates field editing from application shortcuts. File operations finish a live stroke before saving or replacing the scene. Load/undo/redo handlers invalidate graphics resources whose Blender datablocks may have been replaced. Quit cleanup removes timers, Win32 hooks, and draw handlers.

## Blender source references

The implementation was checked against the local `C:\blender` checkout and the installed Blender 5.2.2 runtime. The checkout was read-only throughout.

- `source/blender/draw/engines/workbench/`: native solid rendering and material alpha behavior.
- `source/blender/draw/engines/overlay/overlay_grid.hh` and `shaders/overlay_grid_*.glsl`: native adaptive grid and depth handling.
- `source/blender/makesrna/intern/rna_userdef.cc`: viewport preferences, axis colors, and isolated asset indexing.
- `source/blender/editors/screen/`: editor layout and region visibility transitions.
- `source/blender/python/intern/bpy_app_handlers.cc`: file, history, and exit lifecycle hooks.
- `build_files/cmake/config/blender_lite.cmake`: base for the optional focused engine build.

The native engine is reused through its supported application API. This avoids maintaining a separate renderer or copying Blender's GPU implementation into Python.
