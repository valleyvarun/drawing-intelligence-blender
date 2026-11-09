# Drawing Intelligence

Download page: [`index.html`](index.html). Windows setup: [`wizard/dist/Drawing-Intelligence-Blender-Setup.exe`](wizard/dist/Drawing-Intelligence-Blender-Setup.exe). Build and installer details are in [`wizard/README.md`](wizard/README.md).

A separate Windows drawing application with its own launcher, taskbar identity, startup workspace, and private Blender engine. Tool Box is on the left, the native viewport is in the center, and Clayers with a Claude Code chat is on the right. The viewport uses Blender's compiled Workbench renderer directly. Grease Pencil uses Blender's Draw mode, brush assets, and stroke operators.

## Run

Double-click **`launch.cmd`** or **`Drawing Intelligence.exe`**. The launcher starts only the private engine in this application's `engine/` folder. It does not find or launch a separately installed Blender. On a fresh source checkout, `launch.cmd` prepares the private **Blender 5.2.2 LTS** engine and builds the launcher once. The Windows setup wizard installs both for end users.

For development and isolated tests:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\run.ps1
```

Application settings, logs, extensions, and temporary files go in `runtime/` beside the application. Blender's native thumbnail subsystem can still use the Windows thumbnail-cache location. The launcher does not install an add-on into your usual Blender profile. The startup file already contains the three app areas, so the stock Blender workspace is not shown behind the app.

Launching requires Windows to resolve the current user's profile directory. The launcher checks this before starting the engine, because the bundled Blender thumbnail code does not handle a failed profile lookup safely. Restricted sessions that cannot resolve the profile receive an error instead of starting the engine.

## Drawing and navigation

- **Paint / Pipe:** select a tool, then drag in the viewport. Selecting a tool replaces the full toolbox with that tool's icon, settings, and canvas options. Click the active tool or press Esc to return to the full toolbox.
- **Axes spokes:** the guides keep a fixed screen size at any cursor depth or zoom. Change pen direction to turn onto another X/Y/Z axis without lifting the pen. Each turn starts at the current stroke tip; small movements are filtered to avoid accidental axis changes. The connected stroke remains one object and one undo step.
- **Esc:** return to Select and the tool chooser. An active stroke, navigation drag, cursor placement, or text edit is cancelled first; press Esc again to deselect the tool.
- **3D Printer:** hold and drag on the canvas to pour. Particles fall from a nozzle above the pointer, land on the ground, an object, or earlier prints, and merge into a mesh that keeps forming after you let go: it slumps to a natural slope, and one undo step is recorded once it settles. Pouring where a print already stands adds height to it, even with the pointer below it. Settings: Nozzle size (radius, up to 10 m), Particle size (each particle is a block that adds about its own height where it lands, so larger particles build height faster; at most the nozzle's width), Flow (particles per second), Particles per drop (how many fall together; at 100 particles/s and 10 per drop, a burst of 10 falls every 0.1 s), Drop height, and Color. Both size sliders are logarithmic, so millimetres and metres are each easy to reach. A new color, or a much larger or smaller nozzle or particle, starts a new print object, which stacks on top of the others.
- **Drawing tools:** Drawing contains 3D Paint, 3D Pipe, 3D Printer, and the native Draw, Fill, Polyline, Curve, and Shape tools; Shape's settings choose Rectangle or Ellipse. Erase contains Trim and Erase. Native tools use Blender's original icons. Draw opens Blender's brush-thumbnail shelf at the bottom of the viewport. Size, strength, pressure, stabilization, material, placement, and plane controls remain in Tool Box. `F` adjusts the native brush radius. App canvas choices override placement when enabled; otherwise Blender's Origin / View defaults apply.
- **Pipe Fill:** click inside a curve boundary to fill its actual 3D plane. Slightly uneven strokes are fitted to a shared plane, allowing deviations up to 5% of the whole boundary's size; short connector strokes do not reduce that allowance. Open curves are closed for filling by a straight line between their endpoints; the drawn strokes stay unchanged. Only the stroke curves define the boundary; pipe thickness and mesh shape do not affect it. Curve calculations run in a separate lightweight process and reuse cached regions. Longer operations show **Fill processing ...** at the bottom; navigation remains available. Changing tools or editing the source curves discards an unfinished fill. Fill has an **Opacity** control (0–100%) for both pipe and Grease Pencil fills, with no radius control. Strongly warped curves and straight lines without an enclosed area are not filled.
- **Pipe Trim:** drag across a pipe to remove the portion between physical junctions (or a junction and an endpoint); strokes crossing only in the view do not make junctions. Trim keeps the remaining pipe portions and their pressure widths.
- **Selection:** the default interaction is one selection tool with no toolbox button. Click to select or left-drag a rectangle; Shift-click adds/removes items and Shift-drag adds to the selection. Grease Pencil clicks and rectangles select individual strokes, including after using Paint or Pipe. Clayers lists individual Grease Pencil strokes directly under **Strokes**, alongside paint and pipe strokes. Gizmos, `G`/`R`/`S`, Delete and Ctrl+D affect selected strokes. Esc returns to default selection.
- **Canvas:** use the ground, create/select a drawing plane, draw on an object, or place a 3D cursor. Ctrl+Shift+click an object to put a drawing plane on its surface at that point, aligned to the face and lifted a few millimetres so it does not flicker against it. Double-click a drawing plane with the Select, Move, Rotate, or Scale tool to make it the active plane. The cursor icon starts placement; its checkbox enables/disables it. Cursor placement on the ground snaps to the grid when the ruler is enabled.
- **Move / Rotate / Scale:** use the native gizmo or type XYZ values. Minus/plus buttons adjust values by unit-aware increments. Rotation fields use degrees.
- **Orbit:** hold RMB to orbit, MMB to pan, and use the wheel to zoom toward the pointer. Shift + wheel changes zoom speed, which is saved for the next launch.
- **Fly:** choose Fly in the mouse-navigation menu, hold RMB, and use WASDQE. Shift accelerates and Ctrl slows movement.
- **Walk:** choose Walk, hold RMB, look with the mouse, and move with WASD or the arrow keys. The eye stays at eye level above the surface below it, or above the ground where there is none. Shift runs, Ctrl slows, releasing RMB keeps the view, and Esc returns to where the walk started. Walking speed and eye level are sliders in the same menu. `N` switches between Orbit, Fly, and Walk.
- **Orientation:** `1`, `2`, and `3` select orthographic X, Y, and Z views; press the same key again to flip to the negative side. `0` restores perspective. These shortcuts also accept numpad keys and preserve the current view target. Click a positive/negative axis circle or drag the lower-left gizmo to navigate around the world origin.
- **Ruler:** numbers use world coordinates. A placed, enabled cursor anchors the ruler; otherwise it follows the view on the ground within 20 metres measured in XY. Occluded portions remain faintly visible.
- **Clayers:** create, group, ungroup, activate, hide, and lock drawing collections, and select, hide, or lock the objects listed in them. Double-click a name, or hover it and press F2, to rename it.
- **Panel sizes:** drag either vertical divider beside the viewport to resize the sidebars. Dimensions save automatically after resizing and again at shutdown, in `runtime/config/drawing-preferences.json` inside the application folder. The next launch restores your widths, scaled for display DPI; panels shrink only if they cannot fit the window. Drag the divider between Clayers and the chat to share the right panel's height; Clayers starts with a third. View > Reset Workspace restores a 220-unit Tool Box, a 280-unit Clayers / chat panel, and the one-third split.
- **Files:** save native `.blend` projects or import/export USD. Native projects preserve the complete drawing data and canvas metadata. Ctrl+S/O/N, Ctrl+Z/Shift+Ctrl+Z, Ctrl+D, Delete, and F are available.
- **Shortcuts:** Shift+A opens the Create (Add) menu at the pointer. The full list is in `app/drawing_app/core/shortcuts.py`. Add a row to its `SHORTCUTS` table to add a shortcut; the next launch uses it.

The rightmost viewport control selects Light or Dark mode. The update-rate menu limits this application's navigation updates; it is not a driver-level cap on Blender's native transform-gizmo drawing.

## AI agent and scripting

**Claude Code chat.** The lower part of the right panel is a Claude Code chat, like the Claude Code extension in VS Code. Type a request and press Enter (Shift+Enter starts a new line). Claude can see and change the drawing through the app's Blender tools: it reads the scene, draws pipe, paint, and Grease Pencil strokes, adds and moves objects, sets colours and brushes, takes viewport screenshots, and runs Python. Each change Claude makes is one undo step. Running Python, editing files, and other actions that need permission show an approval card: **Allow** runs it once, **Always** allows that tool for the rest of the chat, and **Deny** refuses it. The square button stops the current reply; **+** starts a new chat. Click a tool line to see its input and result.

It is Claude Code itself: the app runs the same `claude` program the Claude Code extension for VS Code runs, in the same headless mode, with your settings, skills, MCP connectors, and usage limits. The app finds it on `PATH`, from the native installer, or inside the VS Code extension (also Cursor and Windsurf); otherwise the chat offers **Locate Claude Code…**. Claude works in the drawing's folder once the drawing is saved, otherwise in `runtime/agent/`.

**Sign-in** is Claude Code's own: it is stored in your user profile and shared with the editor extension, so if Claude Code works in VS Code, the chat is already signed in. The **…** menu shows the account and plan and offers **Sign out**; when nobody is signed in, the chat offers **Sign in to Claude…**, which opens Claude Code's browser sign-in in a console window. Signing out here also signs out the editor.

**Model, effort, and mode** are chosen below the message box, as in VS Code. The model menu lists the models Claude Code offers your account (Default, Opus, Fable, Sonnet, Haiku) and effort levels; the mode menu has **Ask before edits**, **Edit automatically**, and **Plan mode**. Changes apply to the running chat immediately and are remembered. Claude Code starts in the background shortly after the app opens, without sending anything, so the menus are filled and the first reply is quicker.

**Windows > Scripting** opens Blender's text editor with a Python console below it. The script is saved with the drawing. Run it with Alt+P or Text > Run Script. The `api` module has the same tools Claude uses, as Python functions:

```python
from drawing_app import api

print(api.scene_info())
api.draw_stroke([(0, 0, 0), (1, 0, 0.5), (2, 1, 1)], kind="pipe", radius=0.03, color="#FF8000")
```

`api.tools()` lists the tools, `help(api.draw_stroke)` explains one, and `api.call("draw_stroke", {...})` runs one by name as agents do. Lengths are metres and Z is up.

**Blender MCP for other agents.** The same tools are a Model Context Protocol server, `app/drawing_app/agent/mcp_server.py`. Windows > Copy Claude Code MCP Command copies a `claude mcp add` command; run it once in a terminal, and Claude Code in your editor can use the running app's tools (`mcp__blender__…`). The server only accepts local connections that present the app's per-session token.

## Package or build

Create a portable application package using this folder's private engine:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\package.ps1
```

Output is `dist/3D-Drawing/`, including the complete engine and launcher. `-EngineDirectory` can explicitly supply another engine to copy into the package. Engine files, generated launchers/packages, checks, and local preferences are ignored by Git. The downloadable setup executable in `wizard/dist` is intentionally committed.

Blender's matching native source lives locally in `vendor/blender/`. Recreate it on a fresh checkout with `scripts/setup-source.ps1`; the upstream tag and exact commit are pinned in `vendor/blender-source.json`. This includes the viewport, Grease Pencil, windowing, Python API, and bundled source libraries. Git LFS files remain pointers by default; downloading them requires `-IncludeTestData` and uses one transfer at a time. The source tree is ignored by the app repository and can be prepared again from the pin.

`scripts/build-engine.ps1` uses that local source and `cmake/drawing.cmake`; compilation and staging stay in `build/`, then the result is branded and installed into `engine/`. Compiling requires the matching Windows dependency bundle in `vendor/blender/lib/windows_x64` and Visual Studio C++ tools. The current runnable engine comes from the verified official binary release, with app identity changes. It has not been recompiled from the local source; the dependency bundle is not installed.

## Validate

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\validate.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\validate.ps1 -EngineTests
powershell -ExecutionPolicy Bypass -File .\scripts\validate.ps1 -UI
```

The first command runs formatting, lint, syntax, type checks, and host-only regression tests without starting Blender. It uses locally installed checking tools and does not download dependencies. `-EngineTests` explicitly enables Blender geometry, layers, navigation, project, and USD regressions. `-UI` additionally launches isolated test windows and sends native mouse/keyboard events. Engine tests run sequentially with two CPU threads and a timeout per process. Reports and screenshots are written to `.checks/`; each engine test uses its own settings profile.

Formatting/lint/type settings are in `pyproject.toml`. `scripts/static_check.py` checks syntax and generates a separate Pyright configuration using the repository editor's actual extra paths without changing VS Code settings. See [architecture](docs/architecture.md) and [validation details](docs/validation.md).

The UI and workflows have been exercised on the available machine. Low-end GPU performance, physical touch hardware, and physical stylus pressure still need device testing. Native Workbench uses 16 opacity levels for paint/pipe display; full vertex alpha is retained in the geometry for material rendering and export. Native `.blend` is the reliable interchange format for Grease Pencil; USD support is provided by the chosen Blender engine.
