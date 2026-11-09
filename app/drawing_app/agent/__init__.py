"""AI agents in Drawing Intelligence: a Claude Code chat panel and the Blender MCP server.

    ui/chat.py ── ChatSession (session.py) ── Claude Code CLI ── stdio MCP ── mcp_server.py
                                                                                   │ local TCP + token
    api/ (scripting tools) ◄── runner.py ◄── Bridge (bridge.py, main-thread pump) ◄┘

The chat runs Claude Code headless, as the VS Code extension does, with the user's own sign-in and settings, and
shows its stream in the side panel; the panel's menus switch model, effort, and permission mode live. Claude reaches the app
through the `blender` MCP server, whose tools are the scripting API in `drawing_app.api`. Agents
started outside the app (for example Claude Code in an editor) can use the same server: it finds the
running app through a discovery file in the configuration folder (Windows > Copy Claude Code MCP Command).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ..core.blender import bpy
from ..core.state import CONFIG, get_state, save_preferences
from . import cli, runner
from .bridge import DISCOVERY_FILE, Bridge
from .session import ChatSession, Launch, LaunchError
from .transcript import JsonObject

# MCP server name: Claude sees its tools as mcp__blender__<tool>.
SERVER: str = "blender"
SERVER_SCRIPT: Path = Path(__file__).with_name("mcp_server.py")
SYSTEM_PROMPT: str = """You are running inside Drawing Intelligence, a 3D sketching app built on Blender 5.2. \
The user chats with you from a narrow panel beside the 3D viewport, so keep replies short.

Use the `blender` MCP tools to see and change the drawing. Lengths are metres and Z is up.
- scene_info and object_info describe the drawing; viewport_screenshot shows it. Check visual work with a screenshot.
- draw_stroke draws pipe, paint, or Grease Pencil strokes through 3D points into the active drawing group. \
This is a drawing app, so build things from strokes unless asked otherwise.
- add_primitive, transform_object, select_objects, and delete_objects edit objects; set_tool, set_color, set_brush, \
create_group, set_active_group, create_drawing_plane, set_view, and frame_objects match the app's controls.
- execute_python runs Python in the app with bpy and the same tools as `api` (from drawing_app import api). \
Use it when no other tool fits; the user approves each run.
Each change you make is one undo step for the user."""

_SESSION: ChatSession | None = None
_BRIDGE: Bridge | None = None
_REDRAW: Callable[[], None] | None = None


def workspace() -> Path:
    """Claude Code's working folder: the drawing's folder once saved, else the app's agent folder."""
    if bpy.data.filepath:
        return Path(bpy.data.filepath).resolve().parent
    return CONFIG.parent / "agent"


def python() -> str:
    return cli.python_executable(str(bpy.app.binary_path))


def mcp_command() -> str:
    """A `claude mcp add` command that connects another Claude Code to this app's tools."""
    return (
        f'claude mcp add {SERVER} --scope user -e DRAWING_APP_CONFIG="{CONFIG}" '
        f'-- "{python()}" -I "{SERVER_SCRIPT}"'
    )


def _launch() -> Launch:
    """How the chat starts Claude Code: its MCP server, auto-allowed tools, and app instructions."""
    from .. import api

    command: list[str] | None = cli.find(get_state().agent_cli)
    if command is None:
        raise LaunchError(cli.INSTALL_HINT)
    servers: JsonObject = {}
    if _BRIDGE is not None and _BRIDGE.running:
        servers[SERVER] = {
            "type": "stdio",
            "command": python(),
            "args": ["-I", str(SERVER_SCRIPT)],
            "env": {
                "DRAWING_BRIDGE_PORT": str(_BRIDGE.port),
                "DRAWING_BRIDGE_TOKEN": _BRIDGE.token,
                "DRAWING_APP_CONFIG": str(CONFIG),
            },
        }
    allowed: list[str] = []
    entry: api.Tool
    for entry in api.tools():
        if not entry.approval:
            allowed.append(f"mcp__{SERVER}__{entry.name}")
    return Launch(
        command=command,
        cwd=str(workspace()),
        mcp_config={"mcpServers": servers} if servers else {},
        allowed_tools=allowed,
        # Questions are asked in plain chat; the panel has no multiple-choice form.
        disallowed_tools=["AskUserQuestion"],
        system_prompt=SYSTEM_PROMPT,
        model=get_state().agent_model,
        permission_mode=get_state().agent_mode,
        effort=get_state().agent_effort,
    )


def session() -> ChatSession:
    global _SESSION
    if _SESSION is None:
        _SESSION = ChatSession(_launch)
    return _SESSION


def bridge() -> Bridge | None:
    return _BRIDGE


def set_cli(path: str) -> None:
    """Use a chosen Claude Code executable from now on."""
    get_state().agent_cli = path
    save_preferences()
    chat: ChatSession = session()
    chat.close()
    chat.missing_cli = False
    chat.transcript.add_notice(f"Using Claude Code at {path}")
    chat.warm()


def new_chat() -> None:
    """Forget the conversation and have Claude Code ready for the next one."""
    chat: ChatSession = session()
    chat.reset()
    chat.warm()


def set_model(value: str) -> None:
    """Choose the model, now and for later chats (empty follows Claude Code's settings)."""
    get_state().agent_model = value
    save_preferences()
    session().set_model(value)


def set_mode(mode: str) -> None:
    get_state().agent_mode = mode
    save_preferences()
    session().set_mode(mode)


def set_effort(level: str) -> None:
    get_state().agent_effort = level
    save_preferences()
    session().set_effort(level)


def _warm() -> float | None:
    """Start Claude Code shortly after the app opens, so the chat is ready like the VS Code panel."""
    if _SESSION is not None or _BRIDGE is not None:
        session().warm()
    return None


def _tick() -> float | None:
    """Run bridge requests and apply chat output on Blender's main thread."""
    from ..core import controller

    if _BRIDGE is None and _SESSION is None:
        return None
    changed: bool = False
    # Scripted edits wait while the user is in the middle of a stroke or navigation.
    if _BRIDGE is not None and not controller.busy():
        changed = _BRIDGE.pump() > 0
    working: bool = False
    if _SESSION is not None:
        changed = _SESSION.poll() or changed
        working = _SESSION.status != "idle"
    if (changed or working) and _REDRAW is not None:
        _REDRAW()
    return 0.03 if changed or working else 0.1


def install(redraw: Callable[[], None]) -> None:
    """Start the bridge for MCP servers and the timer that serves it and the chat."""
    global _BRIDGE, _REDRAW
    _REDRAW = redraw
    if _BRIDGE is None and not bpy.app.background:
        _BRIDGE = Bridge(runner.handle, CONFIG / DISCOVERY_FILE)
        try:
            _BRIDGE.start()
        except OSError as error:
            get_state().status = f"Agent bridge unavailable: {error}"
            _BRIDGE = None
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=0.1, persistent=True)
    if _BRIDGE is not None and not bpy.app.timers.is_registered(_warm):
        bpy.app.timers.register(_warm, first_interval=2.0)


def uninstall() -> None:
    """Stop Claude Code and the bridge; nothing keeps running after the app closes."""
    global _SESSION, _BRIDGE, _REDRAW
    timer: Callable[[], float | None]
    for timer in (_tick, _warm):
        if bpy.app.timers.is_registered(timer):
            bpy.app.timers.unregister(timer)
    if _SESSION is not None:
        _SESSION.close()
        _SESSION = None
    if _BRIDGE is not None:
        _BRIDGE.stop()
        _BRIDGE = None
    _REDRAW = None
