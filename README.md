# 3D Drawing for Blender

Download page: [`index.html`](index.html). Windows setup: [`wizard/dist/Drawing-Intelligence-Blender-Setup.exe`](wizard/dist/Drawing-Intelligence-Blender-Setup.exe). Build and installer details are in [`wizard/README.md`](wizard/README.md).

A separate Windows drawing application using Blender's native window, Workbench viewport, grid, geometry, undo, and GPU support. The interface follows the existing drawing app: Tool Box on the left, the viewport in the center, and Clayers / Stage on the right. Blender's general editing workspaces and panels are hidden.

## Run

Double-click **`launch.cmd`**. The Windows setup wizard downloads a private Blender **5.2.2 LTS** runtime into `engine/`. Engine binaries are not stored in Git. A source checkout can use `DRAWING_BLENDER` or the normal Windows Blender installation directory. This version was tested with Blender 5.2.2 LTS.

To choose an engine explicitly:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\run.ps1 -Blender 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe'
```

All application settings and temporary files go in `runtime/` beside the application. The launcher does not install an add-on into your usual Blender profile or change the Omniverse application.

## Drawing and navigation

- **Paint, Pipe, Grease Pencil:** select a tool, then drag in the viewport. Click the active tool again to return to the tool chooser. Pressure switches use actual tablet pressure.
- **Canvas:** use the ground, create/select a drawing plane, draw on an object, or place a 3D cursor. The cursor icon starts placement; its checkbox enables/disables it. Cursor placement on the ground snaps to the grid when the ruler is enabled.
- **Move / Rotate / Scale:** use the native gizmo or type XYZ values. Minus/plus buttons adjust values by unit-aware increments. Rotation fields use degrees.
- **Orbit:** hold RMB to orbit, MMB to pan, and use the wheel to zoom toward the pointer. Shift + wheel changes zoom speed, which is saved for the next launch.
- **Fly:** choose Fly in the mouse-navigation menu, hold RMB, and use WASDQE. Shift accelerates and Ctrl slows movement.
- **Orientation:** click a positive/negative axis circle or drag the lower-left gizmo. Axis views are centered on the original world origin.
- **Ruler:** numbers use world coordinates. A placed, enabled cursor anchors the ruler; otherwise it follows the view on the ground within 20 metres measured in XY. Occluded portions remain faintly visible.
- **Clayers:** create, group, ungroup, activate, hide, and lock drawing collections. Stage lists and selects objects. Double-click a name, or hover it and press F2, to rename it.
- **Files:** save native `.blend` projects or import/export USD. Native projects preserve the complete drawing data and canvas metadata. Ctrl+S/O/N, Ctrl+Z/Shift+Ctrl+Z, Ctrl+D, Delete, and F are available.

The rightmost viewport control selects Light or Dark mode. The update-rate menu limits this application's navigation updates; it is not a driver-level cap on Blender's native transform-gizmo drawing.

## Package or build

Create an application package using an installed engine:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\package.ps1 -BlenderDirectory 'C:\Program Files\Blender Foundation\Blender 5.2'
```

Output is `dist/3D-Drawing/`; it includes the complete engine when `-BlenderDirectory` is supplied. Without that argument, the package finds an installed Blender. Engine files, checks, generated packages, and local preferences are ignored by Git.

`scripts/build-engine.ps1` and `cmake/drawing.cmake` provide an optional out-of-source Blender build using `C:\blender`. Output stays inside this folder. The matching Windows dependency bundle and Visual Studio C++ tools are required. The runnable application uses the installed engine; the optional custom engine has not been compiled here because the checkout's Windows dependency directory is empty.

## Validate

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\validate.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\validate.ps1 -UI
```

The first command runs Blender geometry, layers, navigation, project, and USD regressions. `-UI` also launches isolated test windows and sends native mouse/keyboard events. Reports and screenshots are written to `.checks/`; each test uses its own settings profile.

Formatting/lint/type settings are in `pyproject.toml`. `scripts/static_check.py` checks syntax and generates a separate Pyright configuration using the repository editor's actual extra paths without changing VS Code settings. See [architecture](docs/architecture.md) and [validation details](docs/validation.md).

The UI and workflows have been exercised on the available machine. Low-end GPU performance, physical touch hardware, and physical stylus pressure still need device testing. Native Workbench uses 16 opacity levels for paint/pipe display; full vertex alpha is retained in the geometry for material rendering and export. Native `.blend` is the reliable interchange format for Grease Pencil; USD support is provided by the chosen Blender engine.
