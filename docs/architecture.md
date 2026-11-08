# Architecture

`launch.cmd` starts the application's own Windows launcher. `launcher/Launcher.cs` starts only `engine/Drawing Intelligence.exe`, isolates its environment, restores the window, and keeps a startup screen visible until the app signals readiness. `run.ps1` provides the equivalent development/test path. `app/bootstrap.py` imports `drawing_app`, whose lifecycle registers commands, restores state, prepares the drawing scene, and starts the input router. No Kit or Omniverse modules are imported.

| Module | Responsibility |
| --- | --- |
| `core/blender.py` | Explicit boundary for dynamically typed Blender SDK modules. |
| `core/desktop.py`, `identity.py` | App window titles, taskbar grouping, relaunch identity, and startup-ready signaling. |
| `core/state.py` | Typed application/brush state and local preferences. Physical brush dimensions are metres. |
| `core/controller.py` | Command dispatch, input capture, shortcuts, native transforms, and lifecycle-safe routing. |
| `core/project.py` | Native project dialogs, scene metadata, and Blender USD operators. |
| `tools/canvas.py` | View rays, plane/object/cursor intersections, grid snapping, and axis constraints. |
| `tools/geometry.py` | Pressure samples, resampling, pipe meshes, and sparse paint fields. |
| `tools/surface.py` | Cached native BVH queries and non-overlapping paint patches clipped to source triangles. |
| `tools/drawing.py` | Paint/pipe gestures, mesh data, material alpha, cursor placement, commit/cancel, and native undo. |
| `tools/pencil.py` | Native Grease Pencil tools, brush assets, materials, placement, and group ownership. Viewport strokes go directly to Blender. |
| `tools/pipe_geometry.py` | Shared planar face graphs, world-space physical contacts, and trim intervals. |
| `tools/pipe_edit.py` | Mesh-confirmed Trim contacts, pipe reconstruction, Trim gestures, and Fill dispatch. |
| `tools/curve_fill.py` | Curve-only contacts, lazily built planar regions, cached picking, and scanline triangulation. |
| `tools/pipe_fill.py` | Sliced curve snapshots, asynchronous Fill lifecycle, progress text, stale-result validation, and main-thread mesh commit. |
| `tools/fill_process.py`, `fill_worker.py` | One hidden plain-Python worker, bounded request encoding, cached curve geometry, and nonblocking cancellation. Temporary files stay in `runtime/fill`. No Blender API is used in the worker. |
| `tools/layers.py` | Drawing collections, nested ownership, active groups, planes, and inherited visibility/locks. |
| `tools/preview.py` | Transient brush footprint, axis guides, and completed-pipe recognition. |
| `viewport/navigation.py`, `keymap.py` | Native orbit/pan/zoom/walk bindings, touch navigation, speed controls, and orientation interaction. |
| `viewport/touch.py` | Windows touch input bridge; pen events remain native Blender input. |
| `viewport/guides.py` | Native Workbench configuration, ruler depth passes, labels, outlines, and orientation drawing. |
| `ui/layout.py` | Three real, resizable editor areas, prepared in stages as Blender initializes them. |
| `ui/overlay.py`, `paint.py`, `menus.py` | Tool/layer/object controls, field editing, menus, icon drawing, and input capture. |

The center is a real Blender `VIEW_3D`, not a rendered image embedded in another toolkit. Side panels use native editor regions with application-owned GPU controls. They therefore do not alter the viewport's projection or picking coordinates. Native transforms, drawing geometry, and guides share Blender's depth buffer.

Workbench remains responsible for mesh shading, the infinite adaptive grid, antialiasing, and hardware handling. The custom ruler uses a depth-tested pass plus a faint occluded pass. The application uses native mesh wire overlays for all visible polygon edges, excluding strokes and helper geometry. Grease Pencil uses native Draw mode and brush operators, including native stroke undo; the previous custom stroke sampler is no longer used for it.

`app/template/startup/bl_app_templates_system/drawing_intelligence/startup.blend` contains only the drawing workspace and its three areas. The launcher selects this template before initialization. `scripts/build_startup.py` regenerates it through the app's layout code. `launcher/EngineBrand.cs` applies app icons, version resources, startup title, taskbar ID, and Windows GUI subsystem to the private runtime. It is shared with the installer. This is a dedicated distribution of Blender's engine, not a separately implemented viewport.

The viewport uses a stable 0.01-unit near plane and a far plane of at least 1000 units. The far range expands for distant views and stays expanded when zooming in; reducing orbit distance must not clip away the scene or collapse depth precision for surface paint. Native mouse navigation uses the surface under the pointer as its depth reference. Paint fields cache affected cells and source patches so drawing updates rebuild the changed portion of a stroke.

Construction coordinates are Blender units internally. UI positions and brush dimensions convert through the scene's `scale_length`; loading a non-metre project preserves its unit scale. Ruler units are display preferences. The app records canvas/group/brush metadata in scene custom properties and keeps viewport preferences in JSON.

Grease Pencil tools are directly selectable in the Tool Box. Draw exposes Blender's native brush asset shelf; input over shelf regions passes through to Blender and its brush selection is synchronized into application preferences. Number-row and numpad 1/2/3 toggle orthographic axis sides, and 0 restores perspective without recentering the view target.

Fill and Trim also dispatch to Pipe geometry before native Grease Pencil input. Fill uses only world-space centerlines, fits nearby curves to a shared plane, and tolerates small deviations relative to each local boundary's size. It projects copies of accepted curves for the fill graph, preserving the original strokes and numerical precision for small details. Regions and holes are computed lazily in the separate fill worker. Trim confirms contacts against actual mesh intersections, raycasts pipe surfaces, removes the picked interval, and rebuilds capped remnants with their original transform, profile, materials and pressure samples. Input without pipe targets falls back to the native Grease Pencil tools.

Clayers lists current-frame Grease Pencil strokes directly in their owning Strokes collection, at the same depth as paint and pipe strokes. The native Grease Pencil container is omitted from both panels. Row keys identify the object, layer, frame and stroke index; rows are rebuilt from Blender data, so drawing, deletion and undo need no parallel stroke registry. Clicking a stroke enters native Edit mode with the STROKE selection domain; Shift/Ctrl-click toggles that stroke. Selection highlights read the native stroke flags. Transform tools preserve Edit mode, and edit commands dispatch to Grease Pencil operators. Proportional and multi-frame editing are disabled on entry. Selecting another object leaves stroke editing.

Selection has one default application state, `select`, and no toolbox entry. `drawing.select` owns clicks and rectangle drags in the 3D View and Tweak/fallback keymaps. A click uses Blender's object picker to find a Grease Pencil container, enters stroke Edit mode, and runs the native picker at the same pixel. A drag draws a rectangle and applies native box selection on release. Grease Pencil containers hit by the box are refined with native stroke box selection, even when a paint or pipe object was active. Shift-drag preserves previous stroke flags, and mesh selections remain alongside selected strokes. Transform tools preserve native stroke Edit mode; Esc returns to the same default selection state.

Mesh stroke previews upload only when new samples exist, at up to 60 Hz (respecting a lower application limit). A timer flushes the last pending samples if motion stops. Pipe geometry caches completed rings and only revises the previous endpoint as the stroke grows; its end caps are rebuilt separately. Paint retains its sparse field and surface patch caches. Mesh upload and shading remain native Blender work and can still become expensive for large strokes.

The router preserves captured gestures across editor boundaries, releases them on deactivation, and separates field editing from application shortcuts. File operations finish a live stroke before saving or replacing the scene. Load/undo/redo handlers invalidate graphics resources whose Blender datablocks may have been replaced. Quit cleanup removes timers, Win32 hooks, and draw handlers.

## Blender source references

`vendor/blender/` contains the source matching the running engine: Blender 5.2.2, commit `d13f752e3b9c4f8c261cda552b1021f8bcc0382c`. `scripts/setup-source.ps1` recreates it from the pin in `vendor/blender-source.json`; its optional `-Repository` argument exports an existing checkout without modifying that checkout. All paths below are relative to this app-local source tree.

- `source/blender/draw/engines/workbench/`: native solid rendering and material alpha behavior.
- `source/blender/editors/grease_pencil/` and `source/blender/editors/sculpt_paint/`: native pencil tools and brush strokes.
- `source/blender/draw/engines/overlay/overlay_grid.hh` and `shaders/overlay_grid_*.glsl`: native adaptive grid and depth handling.
- `source/blender/makesrna/intern/rna_userdef.cc`: viewport preferences, axis colors, and isolated asset indexing.
- `source/blender/editors/screen/`: editor layout and region visibility transitions.
- `source/blender/python/intern/bpy_app_handlers.cc`: file, history, and exit lifecycle hooks.
- `build_files/cmake/config/blender_lite.cmake`: base for the optional focused engine build.

The executable currently uses the verified upstream compiled engine with app identity changes. The source build is available but needs the matching Windows library bundle; it has not been compiled here. Large upstream test fixtures stay as LFS pointers unless requested when preparing the source. Application builds and dependencies stay within this folder.
