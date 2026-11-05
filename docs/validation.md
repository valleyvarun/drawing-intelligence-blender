# Validation

## Download and standalone repository checks (2026-11-04)

The Blender Windows wizard compiles with warnings treated as errors. Its checks passed for invalid options and paths, occupied folders, Blender SHA-256 rejection, installation and update in a path containing spaces, shortcut and uninstall registration, failed-update preservation, installed-app startup, and uninstall with projects/preferences retained. The official Blender ZIP used in the test matched the pinned checksum. The install test uses a local app archive; the production GitHub archive is verified after publishing.

The HUD button was removed and the viewport controls reflowed. Drawing, navigation, project/USD, and native UI input tests passed with Blender 5.2.2 LTS. Fresh light/dark captures confirm the updated viewport. Black, Ruff, syntax compilation, standalone Pyright, and Pyright with the enclosing editor's exact 742 extra paths passed with no errors or warnings. No Kit extension files were changed.

The download page passes JavaScript syntax and Prettier checks. Desktop browser preview access timed out, so responsive layout has not been visually verified in a browser during this change.

## Application regressions

The maintained checks use isolated Blender processes, not the user's existing Blender session. All logs, projects, temporary data, reports, and captures stay under `blender-app`.

| Check | Coverage |
| --- | --- |
| `tests/test_drawing.py` | All paint/pipe/Grease Pencil shapes, pressure, geometry, cancellation, native undo, layer restrictions, construction-plane retention, surface raycasts, cursor snapping, and preview cleanup. |
| `tests/test_navigation.py` | Native Workbench settings and guide lifecycle; mathutils-backed camera tests for orbit/fly/pan/cursor zoom, six axis views, capture release, planar ruler bounds, signed labels, and undo resource invalidation. |
| `tests/test_project.py` | Preferences, malformed values, native project metadata/layers, non-metre units, ordinary-project state reset, and USD geometry round trip. |
| `tests/test_ui.py` | Native window events for cursor placement/increments, pipe drawing, release over panels, undo/redo over panels, orbit, wheel zoom, and both theme screenshots with unselected polygon outlines. |
| `tests/test_ui_lifecycle.py` | Typed numeric fields and increments, unit conversions, layer controls, native F2 rename dialogs, object locks, popup cancellation, repeated layout reset, project reopening, and input after reopening. |
| `tests/test_surface_render.py` | Native Workbench captures of paint across cube and curved surfaces, pipe end caps, Grease Pencil brush shapes, and paired foreground-occlusion captures. |
| `scripts/static_check.py` | Syntax plus generation of the exact host-editor type-checking context. |

Results are recorded in `.checks/*-report.json`; the executable used was Blender 5.2.2 LTS. Static validation includes Black at 120 columns, Ruff, standalone Pyright, and a separate Pyright run with all 742 current editor extra paths.

The final run passed the geometry, navigation, project, native input, and UI lifecycle suites. Both Pyright configurations reported zero errors and warnings; Black, Ruff, and syntax checks passed. Native captures are available for [light mode](screenshots/light.png), [dark mode](screenshots/dark.png), and [drawing tools](screenshots/drawing-tools.png). Surface captures were also inspected at close range with overlays temporarily hidden to check paint continuity.

These tests establish operation on the available machine. They do not establish performance on every GPU, actual stylus/touch-device behavior, pixel-for-pixel identity with the Omniverse renderer, or universal USD round-trip fidelity. The optional native engine build profile is supplied but has not been compiled; `C:\blender\lib\windows_x64` lacks its dependency bundle.
