# Validation

## Finding Claude Code and signing in (2026-11-10)

The chat now finds Claude Code in the Claude desktop app's folder (`%LOCALAPPDATA%\Claude-Data\<package>\claude-code\<version>\<build>\claude.exe`), npm's and WinGet's default folders, and the PATH Windows has saved, in addition to PATH, the native installer, and editor extensions. The newest bundled copy is used. A chosen copy that has been removed falls back to the search. Sign in and Sign out report a missing Claude Code in the chat instead of doing nothing, and Locate Claude Code refuses files that cannot start it.

`test_agent_cli.py` covers each location, npm shims with JavaScript and native targets, ordering, and the fallback; `test_agent_chat.py` covers the sign-in notice and that sign-in does not pass on a parent session's `CLAUDECODE` flag. On this machine, with no files faked, the search chose the desktop app's Claude Code 2.1.321, which answered `--version` and `auth status --json` with the same sign-in as the VS Code extension (`%USERPROFILE%\.claude`). Black, Ruff, syntax, both Pyright configurations, all lightweight tests, and `test_agent_native.py` pass. Run `test_agent_native.py` with a new `-ProfileName`: its saved profile keeps the brush settings from earlier runs, and a square pipe brush left there fails its pipe-size check (1.04 instead of 1.06). The sign-in flow in the browser was not run, and the fix has not yet been tried on another laptop.

## Claude Code chat, Blender MCP, and scripting (2026-11-08)

Black, Ruff, syntax compilation, and both Pyright configurations (standalone and editor context) pass with zero errors. The tools were installed in a temporary folder outside the repository because this machine's `.checks/python-tools` copy is not readable by the current user.

Host-only tests pass: `test_api_registry.py`, `test_agent_chat.py` (a stand-in Claude Code CLI speaking stream-json), `test_agent_mcp.py` (the real MCP server process and bridge), and the existing lightweight suite. The Blender engine test `test_agent_native.py` passes; it runs every tool on real scene data and drives the real MCP server process against the bridge. `test_project.py` and `test_surface_render.py` pass.

The protocol was checked against Claude Code 2.1.319 (the CLI bundled with the VS Code extension): streamed events, one `assistant` event per content block, `can_use_tool` permission requests with `--permission-prompt-tool stdio`, and allow replies. A full in-app run typed a request into the chat with simulated input; Claude Code (Haiku, about US$0.01) called `mcp__blender__draw_stroke`, the app drew the pipe circle, and the reply streamed into the panel. Windows > Scripting opened the text editor and console and ran the example script.

Chat parity follow-up: `test_agent_chat.py` covers the warm start, the initialize reply's account and models, model/mode/effort flags and live control requests, account status, and sign-in/out through a stand-in CLI; `test_pencil_toolbar.py` covers the Clayers/chat divider and saved settings. With Claude Code 2.1.319, `--model`, `--permission-mode`, and `--effort` and the live `set_model`, `set_permission_mode`, and `apply_flag_settings` requests were confirmed without sending a message. In the app, the warm start showed the account and model list, choosing Sonnet and Plan mode switched the running CLI, and dragging the divider saved the new split.

Known failures that predate this change and reproduce on the committed version: `test_drawing.py` stops at the sampled-material check after `leave()`; `test_ui.py` fails its first walk-keymap count; `test_ui_lifecycle.py` cannot click the Units button because the 1600×900 test window at this machine's UI scale is narrower than the viewport header. A copy of the lifecycle test without the header steps passes through the layer, object rename/lock, undo, and layout-reset steps. `test_pipe_editing_native.py` needs a window; with one, its checks pass and its cleanup then fails restoring the asset-shelf flag.

## Recovery audit (2026-11-06)

Routine validation now runs without Blender or GPU work. Engine and native UI regressions require explicit `-EngineTests` or `-UI` switches, run sequentially, and use a CPU-thread limit and process timeout. Static checks use locally available tools; no dependency download is part of validation.

The garbled repository-root folders contained only empty Blender thumbnail directories. A read-only Windows API probe reproduced a failed `SHGetSpecialFolderPathW(CSIDL_PROFILE)` lookup in the restricted session. The matching native source in `imbuf/intern/thumbs.cc` ignores that return value and reads an uninitialized path buffer. Both launch paths now check profile resolution before starting the engine. No native executable patch is needed for this check.

Windows recorded low virtual memory before the unclean restart. That event did not identify a responsible process, so it does not establish the cause of the computer shutdown. The recovery work did not run Blender, native UI, rendering, installation integration, or native engine compilation tests. Engine-test process timeouts were reviewed and syntax-checked without starting an engine. Earlier engine reports below are historical results, not a fresh verification after the crash.

Recovery validation passed: Black (36 files), Ruff, Python syntax (37 files), standalone and editor-context Pyright (zero errors/warnings), nine PowerShell syntax checks, seven mocked callback-lifecycle tests, and seven launcher preference cases. The C# profile preflight correctly rejected the unavailable Windows profile without starting Blender. A small offline Git fixture verified that source export preserves LFS pointers without invoking download filters. The launcher and committed setup executable were rebuilt with C# warnings treated as errors.

Removed ten empty thumbnail artifacts and 1,330,722,404 bytes of generated package/source staging. Application source, the private engine, the downloaded engine archive, local Blender source, and runtime project/preferences data are retained.

## Download and standalone repository checks (2026-11-04)

The Blender Windows wizard compiles with warnings treated as errors. Its checks passed for invalid options and paths, occupied folders, Blender SHA-256 rejection, installation and update in a path containing spaces, shortcut and uninstall registration, failed-update preservation, installed-app startup, and uninstall with projects/preferences retained. The official Blender ZIP used in the test matched the pinned checksum. The install test uses a local app archive; the production GitHub archive is verified after publishing.

The HUD button was removed and the viewport controls reflowed. Drawing, navigation, project/USD, and native UI input tests passed with Blender 5.2.2 LTS. Fresh light/dark captures confirm the updated viewport. Black, Ruff, syntax compilation, standalone Pyright, and Pyright with the enclosing editor's exact 742 extra paths passed with no errors or warnings. No Kit extension files were changed.

The download page passes JavaScript syntax and Prettier checks. Desktop browser preview access timed out, so responsive layout has not been visually verified in a browser during this change.

## Application regressions

The fly-guide regression reproduces Blender's native Walk transition: its camera control preserves the eye but temporarily sets orbit distance to zero. The prior clipping calculation changed near/far from `0.024 / 144` to `0.000001 / 24` for the same eye position. The corrected code retains the pre-flight scale for clipping, grid spacing, and ruler extent, and computes the eye without inverting a transient view matrix. Five host-only tests pass. The optional native UI test now also checks right-button look rotation, fixed eye position, and ruler stability while Walk is active; that UI test has not been run for this fix.

The optional engine checks use isolated Blender processes, not the user's existing Blender session. Application-controlled logs, projects, temporary data, reports, and captures stay under the repository; Blender's native thumbnail cache uses the Windows profile.

| Check | Coverage |
| --- | --- |
| `tests/test_desktop_lifecycle.py` | Host-only mocked cleanup for successful, failed, and repeated native window-hook detachment. |
| `tests/test_touch_lifecycle.py` | Host-only callback retention, input shutdown, retry, and destroyed-window cleanup. |
| `tests/test_layout_lifecycle.py` | Host-only rejection of mismatched startup panel sizes, resize persistence without border drift, DPI scaling, and recovery of invalid saved widths. |
| `tests/test_fly_guides.py` | Host-only native Walk zero-distance transition, stable ruler/clipping scale, normal zoom afterward, transient matrix handling, and per-viewport cache cleanup. |
| `tests/test_drawing.py` | All paint/pipe/Grease Pencil shapes, pressure, geometry, cancellation, native undo, layer restrictions, construction-plane retention, surface raycasts, cursor snapping, and preview cleanup. |
| `tests/test_navigation.py` | Native Workbench settings and guide lifecycle; mathutils-backed camera tests for orbit/fly/pan/cursor zoom, six axis views, capture release, planar ruler bounds, signed labels, and undo resource invalidation. |
| `tests/test_project.py` | Preferences, malformed values, native project metadata/layers, non-metre units, ordinary-project state reset, and USD geometry round trip. |
| `tests/test_ui.py` | Native window events for cursor placement/increments, pipe drawing, release over panels, undo/redo over panels, orbit, wheel zoom, and both theme screenshots with unselected polygon outlines. |
| `tests/test_ui_lifecycle.py` | Typed numeric fields and increments, unit conversions, layer controls, native F2 rename dialogs, object locks, popup cancellation, repeated layout reset, project reopening, and input after reopening. |
| `tests/test_pencil_viewport.py` | Native viewport picks in a temporary scene: individual and multiple Grease Pencil strokes, transform isolation, other-object selection, empty clicks, perspective, and deletion. Restores the original scene and view; run in an existing Blender UI session. |
| `tests/test_pipe_editing.py` | Host-only physical Trim junctions, interpolation, and depth-separated crossings. |
| `tests/test_curve_fill.py` | Curve-only endpoint closure, near-planar tolerance, uneven junctions, holes, tilted planes, local scale, lazy caches, dense paths, and numerical precision. |
| `tests/test_fill_process.py` | Real lightweight worker tests: asynchronous submission, bounded encoding, cache reuse, cancellation, cleanup, and failure recovery. |
| `tests/test_pipe_editing_native.py` | Temporary-scene Blender checks for asynchronous curve-only Fill, progress text, stale results, real drawing rays, mesh-independent boundaries, and physical Trim. Records input/poll timings and restores the user's scene. |
| `tests/test_pipe_fill_async_native.py` | Real event-loop probe with 8,001 curve samples: checks timer heartbeats and viewport updates continue during Fill, then restores the scene and records `.checks/pipe-fill-async-report.json`. |
| `tests/test_fill_viewport.py` | Host-only fill visibility, cache invalidation, save/undo restoration, depth sorting, and temporary surface selection. |
| `tests/test_translucent_fill_native.py` | Temporary-scene GPU viewport rendering reproduces Grease Pencil disappearing behind translucent mesh fills, then checks matching pipe/Grease Pencil compositing, foreground occlusion, zero/full opacity, and native fill-interior rectangle selection. Uses a 256-pixel offscreen viewport and invokes the custom drawing pass directly; restores the original scene. |
| `tests/test_pencil_surface_native.py` | Native 256-pixel offscreen depth checks for tilted translucent fills, zero-opacity surfaces, and nearer meshes. Confirms temporary native surface exposure and restoration. Actual pen input is covered separately; this test does not replay a native brush stroke. |
| `tests/test_surface_render.py` | Native Workbench captures of paint across cube and curved surfaces, pipe end caps, Grease Pencil brush shapes, and paired foreground-occlusion captures. |
| `tests/test_api_registry.py` | Host-only tool schemas from signatures and docstrings, defaults, enums, read-only and approval hints, and argument checks. |
| `tests/test_agent_chat.py` | Host-only chat session with a stand-in CLI: streaming, launch flags, allow/deny/always permission replies, cancelled prompts, interrupt, resume, new chat, and missing CLI. |
| `tests/test_agent_mcp.py` | Host-only MCP server process: initialize, tools/list and tools/call relay, errors, discovery file, token rejection, and an absent app. |
| `tests/test_agent_native.py` | Blender: every scripting tool on real data (strokes, objects, groups, planes, colours, Python), runner results, and the MCP server driving the app through the bridge. |
| `scripts/static_check.py` | Syntax plus generation of the exact host-editor type-checking context. |

Results are recorded in `.checks/*-report.json`; the earlier engine runs used Blender 5.2.2 LTS. Static validation includes Black at 120 columns, Ruff, standalone Pyright, and a separate Pyright run using the actual repository editor paths. This standalone checkout currently has one extra import path, `app`.

The final run passed the geometry, navigation, project, native input, and UI lifecycle suites. Both Pyright configurations reported zero errors and warnings; Black, Ruff, and syntax checks passed. Native captures are available for [light mode](screenshots/light.png), [dark mode](screenshots/dark.png), and [drawing tools](screenshots/drawing-tools.png). Surface captures were also inspected at close range with overlays temporarily hidden to check paint continuity.

These tests establish operation on the available machine. They do not establish performance on every GPU, actual stylus/touch-device behavior, pixel-for-pixel identity with the Omniverse renderer, or universal USD round-trip fidelity. The optional native engine build profile is supplied but has not been compiled; `C:\blender\lib\windows_x64` lacks its dependency bundle.
