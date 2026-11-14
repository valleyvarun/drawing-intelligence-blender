"""The scripting API and the Blender MCP stack inside a real Blender (background mode)."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import IO, Any, cast

from drawing_app import agent, api
from drawing_app.agent import runner
from drawing_app.agent.bridge import Bridge
from drawing_app.agent.session import Launch
from drawing_app.core.blender import bpy
from drawing_app.core.state import CONFIG, ROOT, get_state
from drawing_app.tools import layers

JsonObject = dict[str, object]


def expect_error(text: str, call: Any, *arguments: object, **keywords: object) -> None:
    try:
        call(*arguments, **keywords)
    except api.ToolError as error:
        assert text in str(error), (text, str(error))
        return
    raise AssertionError(f"Expected a ToolError containing {text!r}")


def close_to(values: object, expected: list[float], tolerance: float = 1e-3) -> bool:
    numbers: list[float] = cast(list[float], values)
    index: int
    for index in range(len(expected)):
        if abs(numbers[index] - expected[index]) > tolerance:
            return False
    return len(numbers) == len(expected)


class McpClient:
    """mcp_server.py started like Claude Code starts it; the bridge is pumped on this (main) thread."""

    def __init__(self, bridge: Bridge) -> None:
        env: dict[str, str] = dict(os.environ)
        env.update({"DRAWING_BRIDGE_PORT": str(bridge.port), "DRAWING_BRIDGE_TOKEN": bridge.token})
        self.bridge: Bridge = bridge
        self.process: subprocess.Popen[bytes] = subprocess.Popen(
            [agent.python(), "-I", str(agent.SERVER_SCRIPT)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.lines: queue.Queue[bytes] = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()
        self.next: int = 0

    def _read(self) -> None:
        stream: IO[bytes] | None = self.process.stdout
        assert stream is not None
        raw: bytes
        with stream:
            for raw in stream:
                self.lines.put(raw)

    def ask(self, method: str, params: JsonObject | None = None) -> JsonObject:
        self.next += 1
        message: JsonObject = {"jsonrpc": "2.0", "id": self.next, "method": method}
        if params is not None:
            message["params"] = params
        assert self.process.stdin is not None
        self.process.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
        self.process.stdin.flush()
        deadline: float = time.monotonic() + 60.0
        while time.monotonic() < deadline:
            self.bridge.pump()
            try:
                reply: object = json.loads(self.lines.get(timeout=0.01).decode("utf-8"))
            except queue.Empty:
                continue
            assert isinstance(reply, dict) and reply.get("id") == self.next, reply
            return cast(JsonObject, reply)
        raise AssertionError(f"No MCP reply to {method}")

    def close(self) -> None:
        assert self.process.stdin is not None
        self.process.stdin.close()
        self.process.wait(timeout=10)
        if self.process.stderr is not None:
            self.process.stderr.close()


def text_result(result: JsonObject) -> object:
    content: list[JsonObject] = cast(list[JsonObject], result["content"])
    return json.loads(str(content[0]["text"]))


def main() -> None:
    checks: Path = ROOT / ".checks"
    checks.mkdir(exist_ok=True)
    report: dict[str, object] = {"blender": str(bpy.app.version_string)}
    try:
        layers.ensure_scene(bpy.context)
        names: list[str] = []
        entry: api.Tool
        for entry in api.tools():
            names.append(entry.name)
        expected: set[str] = {
            "scene_info",
            "object_info",
            "viewport_screenshot",
            "draw_stroke",
            "add_primitive",
            "transform_object",
            "select_objects",
            "delete_objects",
            "set_tool",
            "set_color",
            "set_brush",
            "sketch_mesh",
            "create_group",
            "set_active_group",
            "create_drawing_plane",
            "set_view",
            "frame_objects",
            "execute_python",
        }
        assert set(names) == expected, set(names) ^ expected
        definitions: list[JsonObject] = runner.tool_definitions()
        assert definitions[0]["annotations"] == {"readOnlyHint": True, "destructiveHint": False}
        assert definitions[-1]["name"] == "execute_python"
        report["tools_registered_with_schemas"] = True

        scene: JsonObject = api.scene_info()
        assert scene["active_group"] == "Default Drawing" and scene["file"] == "(unsaved)"
        pipe: JsonObject = api.draw_stroke(
            [[0, 0, 0], [1, 0, 0], [1, 1, 0.5]], kind="pipe", radius=0.03, color="#FF8000"
        )
        pipe_object: Any = bpy.data.objects[str(pipe["name"])]
        assert pipe_object["drawing_kind"] == "pipe" and not pipe_object["drawing_preview"]
        samples: Any = pipe_object["drawing_samples"]
        assert len(samples) % 9 == 0 and len(samples) // 9 == pipe["samples"] and int(str(pipe["samples"])) > 10
        assert pipe_object.name in layers.active_collection(bpy.context, strokes=True).objects
        assert api.object_info(pipe_object.name)["color"] == "#FF8000" and pipe["color"] == "#FF8000"
        assert len(pipe_object.data.polygons) > 0 and len(pipe_object.data.materials) == 16
        assert abs(float(pipe_object.dimensions.x) - 1.06) < 0.01, tuple(pipe_object.dimensions)
        paint: JsonObject = api.draw_stroke(
            [[0, 0, 0], [0.5, 0, 0]], kind="paint", radius=0.05, normal=[0, -1, 0], shape="square"
        )
        paint_object: Any = bpy.data.objects[str(paint["name"])]
        assert paint_object["drawing_kind"] == "paint" and paint_object["drawing_shape"] == "square"
        # Facing -Y, the flat paint spans X and Z.
        assert float(paint_object.dimensions.y) < 0.01 < float(paint_object.dimensions.z)
        loop: JsonObject = api.draw_stroke([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], closed=True, radius=0.02)
        assert abs(float(bpy.data.objects[str(loop["name"])].dimensions.x) - 1.04) < 0.01
        line: JsonObject = api.draw_stroke([[0, 0, 1], [1, 0, 1], [1, 1, 1]], kind="grease", color="red")
        grease: Any = bpy.data.objects[str(line["name"])]
        assert grease.type == "GREASEPENCIL" and api.object_info(grease.name)["strokes"] == 1
        drawing: Any = grease.data.layers[0].current_frame().drawing
        stroke: Any = drawing.strokes[0]
        assert len(stroke.points) == 3 and abs(stroke.points[0].radius - 0.01) < 1e-6
        assert close_to(list(stroke.points[2].position), [1.0, 1.0, 1.0])
        assert grease.material_slots[stroke.material_index].material.get("drawing_pencil") == "Red"
        report["strokes_like_hand_drawn"] = True

        expect_error("at least two points", api.draw_stroke, [[0, 0, 0]])
        expect_error("Colours are hex strings", api.draw_stroke, [[0, 0, 0], [1, 0, 0]], color="sparkly")
        expect_error("radius must be between", api.draw_stroke, [[0, 0, 0], [1, 0, 0]], radius=0)
        expect_error("points must be [x, y, z]", api.draw_stroke, [[0, 0], [1, 0]])
        expect_error(
            "kind must be one of", api.call, "draw_stroke", {"points": [[0, 0, 0], [1, 0, 0]], "kind": "laser"}
        )
        report["invalid_input_explained"] = True

        cube: JsonObject = api.add_primitive("cube", location=[2, 0, 0.5], size=1.0, name="Agent Cube")
        cube_object: Any = bpy.data.objects[str(cube["name"])]
        assert cube_object.name in layers.active_collection(bpy.context).objects and cube_object.show_wire
        api.transform_object(cube_object.name, location=[3, 0, 0.5], rotation=[0, 0, 45], scale=[1, 2, 1])
        info: JsonObject = api.object_info(cube_object.name)
        assert close_to(info["location"], [3, 0, 0.5]) and close_to(info["rotation_degrees"], [0, 0, 45])
        assert close_to(info["dimensions"], [1, 2, 1]) and cast(JsonObject, info["mesh"])["vertices"] == 8
        selected: JsonObject = api.select_objects([cube_object.name, "Nothing"])
        assert selected == {"selected": [cube_object.name], "skipped": ["Nothing"]}
        assert bpy.context.view_layer.objects.active == cube_object and cube_object.select_get()
        cube_object.hide_select = True
        expect_error("is locked", api.transform_object, cube_object.name, location=[0, 0, 0])
        assert api.delete_objects([cube_object.name])["locked"] == [cube_object.name]
        cube_object.hide_select = False
        removed: JsonObject = api.delete_objects([cube_object.name, "Missing"])
        assert removed == {"deleted": ["Agent Cube"], "locked": [], "missing": ["Missing"]}
        sphere: JsonObject = api.add_primitive("sphere", size=2.0)
        assert close_to(api.object_info(str(sphere["name"]))["dimensions"], [2, 2, 2], 0.01)
        report["objects_added_transformed_selected_deleted"] = True

        group: JsonObject = api.create_group("Agent Group")
        assert group["active_group"] == "Agent Group" == get_state().active_group
        grouped: JsonObject = api.draw_stroke([[0, 0, 2], [1, 0, 2]])
        assert grouped["group"] == "Agent Group"
        assert api.set_active_group("Default Drawing")["active_group"] == "Default Drawing"
        expect_error("No drawing group", api.set_active_group, "Nowhere")
        plane: JsonObject = api.create_drawing_plane(location=[0, 0, 1], rotation=[90, 0, 0], size=4)
        plane_object: Any = bpy.data.objects[str(plane["name"])]
        assert plane_object.get("drawing_plane") and get_state().drawing_plane
        assert get_state().active_plane == plane_object.name and close_to(list(plane_object.scale), [2, 2, 1])
        report["groups_and_drawing_planes"] = True

        colors: JsonObject = api.set_color("#00FF00")
        assert colors == {
            "toolbox": "#00FF00",
            "paint": "#00FF00",
            "pipe": "#00FF00",
            "printer": "#00FF00",
            "extruder": "#00FF00",
            "sketch": "#00FF00",
            "grease": "#00FF00",
        }
        colors = api.set_color("blue", tool="pipe")
        assert colors["pipe"] != "#00FF00" and colors["paint"] == "#00FF00"
        brush: JsonObject = api.set_brush("pipe", radius=0.05, shape="square", opacity=0.5)
        assert (brush["radius"], brush["shape"], brush["opacity"]) == (0.05, "square", 0.5)
        assert api.set_tool("pipe") == {"tool": "pipe"} and get_state().tool == "pipe"
        api.set_tool("select")
        report["tool_colour_and_brush_state"] = True

        run: JsonObject = api.execute_python("print('from the agent')\nlen(bpy.data.objects)")
        assert run["output"] == "from the agent\n" and run["result"] == len(bpy.data.objects)
        assert api.execute_python("api.scene_info()['active_group']")["result"] == "Default Drawing"
        expect_error("ZeroDivisionError", api.execute_python, "1 / 0")
        expect_error("Syntax error on line 1", api.execute_python, "def (")
        report["python_runs_in_app"] = True

        listed: JsonObject = runner.handle("call_tool", {"name": "scene_info", "arguments": {}})
        assert listed["isError"] is False and cast(JsonObject, text_result(listed))["active_group"] == "Default Drawing"
        screenshot: JsonObject = runner.handle("call_tool", {"name": "viewport_screenshot", "arguments": {}})
        assert screenshot["isError"] is True and "viewport is not available" in json.dumps(screenshot["content"])
        unknown: JsonObject = runner.handle("call_tool", {"name": "teleport", "arguments": {}})
        assert unknown["isError"] is True and "Unknown tool" in json.dumps(unknown["content"])
        assert runner.handle("ping", {}) == {}
        report["runner_reports_errors_as_tool_results"] = True

        bridge: Bridge = Bridge(runner.handle, None)
        bridge.start()
        client: McpClient = McpClient(bridge)
        try:
            initialized: JsonObject = client.ask("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})
            assert cast(JsonObject, initialized["result"])["protocolVersion"] == "2025-06-18"
            tools: list[JsonObject] = cast(
                list[JsonObject], cast(JsonObject, client.ask("tools/list")["result"])["tools"]
            )
            assert len(tools) == len(expected)
            before: int = len(bpy.data.objects)
            drawn: JsonObject = client.ask(
                "tools/call",
                {
                    "name": "draw_stroke",
                    "arguments": {"points": [[0, 2, 0], [1, 2, 0]], "kind": "pipe", "color": "#123456"},
                },
            )
            result: JsonObject = cast(JsonObject, drawn["result"])
            assert result["isError"] is False and len(bpy.data.objects) == before + 1
            assert cast(JsonObject, text_result(result))["color"] == "#123456"
            failed: JsonObject = cast(
                JsonObject, client.ask("tools/call", {"name": "object_info", "arguments": {"name": "Ghost"}})["result"]
            )
            assert failed["isError"] is True and "No object named 'Ghost'" in json.dumps(failed["content"])
        finally:
            client.close()
            bridge.stop()
        report["mcp_server_drives_the_app"] = True

        fake: Path = CONFIG.parent / "fake-claude.py"
        fake.write_text("print('fake')\n", encoding="utf-8")
        previous: str = get_state().agent_cli
        get_state().agent_cli = str(fake)
        # The chat's saved model, permission mode, and effort start Claude Code.
        get_state().agent_model, get_state().agent_mode, get_state().agent_effort = "sonnet", "plan", "high"
        launched: Bridge = Bridge(runner.handle, None)
        launched.start()
        setattr(agent, "_BRIDGE", launched)
        try:
            launch: Launch = agent._launch()
        finally:
            setattr(agent, "_BRIDGE", None)
            launched.stop()
            get_state().agent_cli = previous
            get_state().agent_model = get_state().agent_mode = get_state().agent_effort = ""
            fake.unlink()
        assert (launch.model, launch.permission_mode, launch.effort) == ("sonnet", "plan", "high")
        assert launch.command[-1] == str(fake) and Path(launch.command[0]).name.lower().startswith("python")
        server: JsonObject = cast(JsonObject, cast(JsonObject, launch.mcp_config["mcpServers"])["blender"])
        assert server["args"] == ["-I", str(agent.SERVER_SCRIPT)] and Path(str(server["command"])).is_file()
        assert cast(JsonObject, server["env"])["DRAWING_BRIDGE_TOKEN"] == launched.token
        assert "mcp__blender__draw_stroke" in launch.allowed_tools
        assert "mcp__blender__execute_python" not in launch.allowed_tools
        assert launch.cwd == str(CONFIG.parent / "agent") and "blender" in launch.system_prompt
        assert str(agent.SERVER_SCRIPT) in agent.mcp_command()
        report["chat_launches_claude_with_blender_tools"] = True
        report["passed"] = True
    finally:
        (checks / "agent-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("Agent integration passed")


main()
