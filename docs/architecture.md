# Architecture

`launch.cmd` starts the application's own Windows launcher. `launcher/Launcher.cs` starts only `engine/Drawing Intelligence.exe`, isolates its environment, restores the window, and keeps a startup screen visible until the app signals readiness. `run.ps1` provides the equivalent development/test path. `app/bootstrap.py` imports `drawing_app`, whose lifecycle registers commands, restores state, prepares the drawing scene, and starts the input router. No Kit or Omniverse modules are imported.

| Module | Responsibility |
| --- | --- |
| `core/blender.py` | Explicit boundary for dynamically typed Blender SDK modules. |
| `core/desktop.py`, `identity.py` | App window titles, taskbar grouping, relaunch identity, and startup-ready signaling. |
| `core/state.py` | Typed application/brush state and local preferences. Physical brush dimensions are metres. |
| `core/controller.py` | Command dispatch, input capture, native transforms, and lifecycle-safe routing. |
| `core/shortcuts.py` | The keyboard shortcut table (key and modifiers to a `dispatch` command), the Blender keys the app blocks, and a reference list of context-only keys. Add a row to add a shortcut. |
| `core/project.py` | Native project dialogs, scene metadata, and Blender USD operators. |
| `tools/canvas.py` | View rays, plane/object/cursor intersections, grid snapping, and axis constraints. |
| `tools/geometry.py` | Pressure samples, resampling, pipe meshes, and sparse paint fields. |
| `tools/surface.py` | Cached native BVH queries and non-overlapping paint patches clipped to source triangles. |
| `tools/drawing.py` | Paint/pipe gestures, mesh data, material alpha, cursor placement, commit/cancel, and native undo; it also starts and aims 3D Printer pours. |
| `tools/printer.py` | 3D Printer: particles released above the canvas point, falling and landing on a timer, floors found by casting down, the print object's mesh rebuilt as it forms, and one undo step once it settles. |
| `tools/print_field.py` | The print's height field in numpy: deposits per column, slumping to the angle of repose, growth and coarsening, the floors to look up, and the mesh with its sloped skirt. Saved on the object as custom properties. |
| `tools/pencil.py` | Native Grease Pencil tools, brush assets, materials, placement, and group ownership. Viewport strokes go directly to Blender. |
| `tools/pipe_geometry.py` | Shared planar face graphs, world-space physical contacts, and trim intervals. |
| `tools/pipe_edit.py` | Mesh-confirmed Trim contacts, pipe reconstruction, Trim gestures, and Fill dispatch. |
| `tools/curve_fill.py` | Curve-only contacts, lazily built planar regions, cached picking, and scanline triangulation. |
| `tools/pipe_fill.py` | Sliced curve snapshots, asynchronous Fill lifecycle, progress text, stale-result validation, and main-thread mesh commit. |
| `tools/fill_process.py`, `fill_worker.py` | One hidden plain-Python worker, bounded request encoding, cached curve geometry, and nonblocking cancellation. Temporary files stay in `runtime/fill`. No Blender API is used in the worker. |
| `tools/layers.py` | Drawing collections, nested ownership, active groups, planes, and inherited visibility/locks. |
| `tools/preview.py` | Transient brush footprint, axis guides, and completed-pipe recognition. |
| `viewport/navigation.py`, `keymap.py` | Native orbit/pan/zoom/walk bindings, touch navigation, speed controls, and orientation interaction. |
| `viewport/walk.py` | Walk navigation (`drawing.walk`, held on RMB): mouse look and WASD at eye level above the surface below, or the ground plane. Fly stays Blender's native walk operator. |
| `viewport/touch.py` | Windows touch input bridge; pen events remain native Blender input. |
| `viewport/guides.py` | Native Workbench configuration, ruler depth passes, labels, outlines, and orientation drawing. |
| `ui/layout.py` | Three real, resizable editor areas, prepared in stages as Blender initializes them. |
| `ui/overlay.py`, `paint.py`, `menus.py` | Tool/layer/object controls, field editing, menus, icon drawing, and input capture. |
| `ui/widgets.py` | Widget, edit, and popup records shared by the overlay and the panels it draws. |
| `ui/chat.py` | Claude Code chat panel below Clayers: transcript layout, approval cards, the message box, the mode/model/effort menus, and the account menu. Input and the Clayers/chat divider are routed by `overlay.py`. |
| `ui/scripting.py` | Windows > Scripting: a window with Blender's text editor and Python console, and the example script. |
| `api/` | Scripting API shared by Python and agents. `registry.py` turns typed, documented functions into tools with JSON schemas; `scene.py`, `drawing.py`, `view.py`, and `code.py` hold the tools; `common.py` converts units, colours, and context. |
| `agent/session.py`, `transcript.py`, `cli.py` | The chat: Claude Code run headless over stream-json, permission prompts answered from the panel, and the transcript model. |
| `agent/bridge.py`, `runner.py`, `mcp_server.py` | Blender MCP: a localhost, token-protected bridge that runs tool calls on Blender's main thread, and the standard-library MCP server process that relays to it. |

The center is a real Blender `VIEW_3D`, not a rendered image embedded in another toolkit. Side panels use native editor regions with application-owned GPU controls. They therefore do not alter the viewport's projection or picking coordinates. Native transforms, drawing geometry, and guides share Blender's depth buffer.

Workbench remains responsible for mesh shading, the infinite adaptive grid, antialiasing, and hardware handling. The custom ruler uses a depth-tested pass plus a faint occluded pass. The application uses native mesh wire overlays for all visible polygon edges, excluding strokes and helper geometry. Grease Pencil uses native Draw mode and brush operators, including native stroke undo; the previous custom stroke sampler is no longer used for it.

`app/template/startup/bl_app_templates_system/drawing_intelligence/startup.blend` contains only the drawing workspace and its three areas. The launcher selects this template before initialization. `scripts/build_startup.py` regenerates it through the app's layout code. `launcher/EngineBrand.cs` applies app icons, version resources, startup title, taskbar ID, and Windows GUI subsystem to the private runtime. It is shared with the installer. This is a dedicated distribution of Blender's engine, not a separately implemented viewport.

The viewport uses a stable 0.01-unit near plane and a far plane of at least 1000 units. The far range expands for distant views and stays expanded when zooming in; reducing orbit distance must not clip away the scene or collapse depth precision for surface paint. Native mouse navigation uses the surface under the pointer as its depth reference. Paint fields cache affected cells and source patches so drawing updates rebuild the changed portion of a stroke.

Construction coordinates are Blender units internally. UI positions and brush dimensions convert through the scene's `scale_length`; loading a non-metre project preserves its unit scale. Ruler units are display preferences. The app records canvas/group/brush metadata in scene custom properties and keeps viewport preferences in JSON.

Saved drawings contain data only. After each native save, a `save_post` handler in `core/project.py` rewrites the file without the window manager, workspaces, and screens. Blender therefore opens it with its own layout, as it does a library file, and the app reopens projects with `load_ui=False`. The native save still sets the file path, backup, and relative paths. `scripts/build_startup.py` saves inside `project.startup_file()`, which keeps the application's screen and leaves out drawing state. Every launch therefore starts from default tool colours.

Grease Pencil tools are directly selectable in the Tool Box. Blender's box and circle primitives share one Shape button, which opens the last-used shape; its settings choose Rectangle or Ellipse. Draw exposes Blender's native brush asset shelf; input over shelf regions passes through to Blender and its brush selection is synchronized into application preferences. Number-row and numpad 1/2/3 toggle orthographic axis sides, and 0 restores perspective without recentering the view target.

Fill and Trim also dispatch to Pipe geometry before native Grease Pencil input. Fill uses only world-space centerlines, fits nearby curves to a shared plane, and tolerates small deviations relative to each local boundary's size. It projects copies of accepted curves for the fill graph, preserving the original strokes and numerical precision for small details. Regions and holes are computed lazily in the separate fill worker. Trim confirms contacts against actual mesh intersections, raycasts pipe surfaces, removes the picked interval, and rebuilds capped remnants with their original transform, profile, materials and pressure samples. Input without pipe targets falls back to the native Grease Pencil tools.

Clayers lists current-frame Grease Pencil strokes directly in their owning Strokes collection, at the same depth as paint and pipe strokes. The native Grease Pencil container is omitted from both panels. Row keys identify the object, layer, frame and stroke index; rows are rebuilt from Blender data, so drawing, deletion and undo need no parallel stroke registry. Clicking a stroke enters native Edit mode with the STROKE selection domain; Shift/Ctrl-click toggles that stroke. Selection highlights read the native stroke flags. Transform tools preserve Edit mode, and edit commands dispatch to Grease Pencil operators. Proportional and multi-frame editing are disabled on entry. Selecting another object leaves stroke editing.

Selection has one default application state, `select`, and no toolbox entry. `drawing.select` owns clicks and rectangle drags in the 3D View and Tweak/fallback keymaps. A click uses Blender's object picker to find a Grease Pencil container, enters stroke Edit mode, and runs the native picker at the same pixel. A drag draws a rectangle and applies native box selection on release. Grease Pencil containers hit by the box are refined with native stroke box selection, even when a paint or pipe object was active. Shift-drag preserves previous stroke flags, and mesh selections remain alongside selected strokes. Transform tools preserve native stroke Edit mode; Esc returns to the same default selection state.

Mesh stroke previews upload only when new samples exist, at up to 60 Hz (respecting a lower application limit). A timer flushes the last pending samples if motion stops. Pipe geometry caches completed rings and only revises the previous endpoint as the stroke grows; its end caps are rebuilt separately. Paint retains its sparse field and surface patch caches. Mesh upload and shading remain native Blender work and can still become expensive for large strokes.

The router preserves captured gestures across editor boundaries, releases them on deactivation, and separates field editing from application shortcuts. File operations finish a live stroke before saving or replacing the scene. Load/undo/redo handlers invalidate graphics resources whose Blender datablocks may have been replaced. Quit cleanup removes timers, Win32 hooks, and draw handlers.

## AI agents and scripting

```
ui/chat.py ── agent/session.py ── Claude Code CLI ── MCP over stdio ── agent/mcp_server.py
                                                                            │ TCP 127.0.0.1 + token
          api/ tools ◄── agent/runner.py ◄── agent/bridge.py (main-thread pump) ◄┘
Windows > Scripting ──► api/ tools (same functions, called directly)
```

The scripting API is the single source of tools. Each tool is a typed function registered with `@tool`; its signature and docstring generate the MCP schema, and `registry.call` checks names, required arguments, and choices before running it. Tools take metres and convert through the scene's `scale_length`. Paint and pipe strokes go through the same `stroke_object`, `resample`, `write_mesh`, and `store_samples` path as hand-drawn strokes, so Trim, Fill, and rebuilding work on them; Grease Pencil strokes use the group's native object and colour materials.

The chat starts the user's Claude Code CLI (`agent/cli.py` finds it) with `--print --input-format stream-json --output-format stream-json --include-partial-messages --permission-prompt-tool stdio`, an `--mcp-config` naming the `blender` server, `--allowedTools` for the non-approval tools, and a short appended system prompt. The CLI keeps the user's own settings, skills, connectors, and sign-in (Claude Code's credentials in the user profile, shared with the editor extension). The initialize reply supplies the account, Claude Code's model list, and the permission mode; the panel's model, effort, and mode menus start the CLI with `--model`, `--effort`, and `--permission-mode` and change a running chat with the `set_model`, `apply_flag_settings` (`effortLevel`), and `set_permission_mode` control requests, as the VS Code extension does. A warm start shortly after launch initializes the CLI without sending a message. Sign-in and sign-out run `claude auth login` (in a console window) and `claude auth logout`; `claude auth status --json` reports the account when the initialize reply has none. Reader threads only queue output lines; `ChatSession.poll()` applies them on Blender's main thread. Permission requests arrive as `can_use_tool` control requests and are answered from the panel's approval cards. Stop sends an `interrupt` control request (and stops the process if it does not respond); the next message after a stopped process continues with `--resume`.

`mcp_server.py` is started by Claude Code as a separate process with the engine's Python and uses only the standard library. It relays `tools/list` and `tools/call` to the bridge. The bridge listens on 127.0.0.1 with a random port and a per-session token, queues requests from its client threads, and runs them in a Blender timer, never while a stroke or navigation gesture is in progress. Each edit becomes one undo step. The port and token are written to `runtime/config/agent-bridge.json` so agents started elsewhere can connect. `execute_python` is marked as needing approval, so the chat always asks before running agent code.

Windows > Scripting duplicates the viewport into a separate window and turns it into a text editor with a Python console. Scripts are text datablocks saved in the drawing. The app's session operator handles input only in the main window, so the scripting window keeps Blender's native editor shortcuts.

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
