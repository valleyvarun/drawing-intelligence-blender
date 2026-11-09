"""Registry of the tools shared by Python scripts and AI agents.

A tool is a typed Python function registered with @tool. Its signature and docstring generate the
JSON schema that the MCP server publishes, so the scripting editor and agents run the same code. The
docstring's first paragraphs describe the tool; its "Args:" section describes each parameter.
"""

from __future__ import annotations

import collections.abc
import inspect
import json
import re
import types
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal, TypeAlias, TypeVar, Union, get_args, get_origin, get_type_hints

JsonObject: TypeAlias = dict[str, object]
ToolFunction = TypeVar("ToolFunction", bound=Callable[..., object])


class ToolError(Exception):
    """A tool could not do what was asked; the message is returned to the caller."""


@dataclass(frozen=True)
class Image:
    """A PNG image produced by a tool; agents receive the picture itself."""

    path: str
    mime_type: str = "image/png"


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    schema: JsonObject
    function: Callable[..., object]
    # Undo step name for agent calls; empty for tools that do not change the drawing.
    undo: str
    read_only: bool
    # Agents must ask the user first (the chat panel shows an approval prompt).
    approval: bool

    def definition(self) -> JsonObject:
        """MCP tool definition."""
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.schema,
            "annotations": {"readOnlyHint": self.read_only, "destructiveHint": self.approval},
        }


_TOOLS: dict[str, Tool] = {}


def _docstring(function: Callable[..., object]) -> tuple[str, dict[str, str]]:
    """Description paragraphs and per-argument text from a Google-style docstring."""
    lines: list[str] = inspect.cleandoc(function.__doc__ or "").splitlines()
    summary: list[str] = []
    arguments: dict[str, str] = {}
    current: str = ""
    section: str = ""
    line: str
    for line in lines:
        stripped: str = line.strip()
        if stripped in ("Args:", "Returns:", "Raises:", "Example:", "Examples:"):
            section = stripped
            continue
        if section == "Args:":
            match: re.Match[str] | None = re.match(r"^\s{4}(\w+):\s*(.*)$", line)
            if match is not None:
                current = match.group(1)
                arguments[current] = match.group(2).strip()
            elif current and stripped:
                arguments[current] += " " + stripped
        elif not section:
            summary.append(stripped)
    paragraphs: list[str] = []
    paragraph: str
    for paragraph in " \n".join(summary).split(" \n \n"):
        if paragraph.strip():
            paragraphs.append(" ".join(paragraph.split()))
    return "\n\n".join(paragraphs), arguments


def _schema(annotation: object) -> JsonObject:
    """JSON schema for a parameter annotation."""
    origin: object = get_origin(annotation)
    arguments: tuple[object, ...] = get_args(annotation)
    member: object
    if origin is Union or origin is types.UnionType:
        members: list[object] = []
        for member in arguments:
            if member is not type(None):
                members.append(member)
        if len(members) == 1:
            return _schema(members[0])
        options: list[JsonObject] = []
        for member in members:
            options.append(_schema(member))
        return {"anyOf": options}
    if origin is Literal:
        values: list[str] = []
        for member in arguments:
            values.append(str(member))
        return {"type": "string", "enum": values}
    if origin in (list, collections.abc.Sequence):
        return {"type": "array", "items": _schema(arguments[0])} if arguments else {"type": "array"}
    if origin is tuple:
        if len(arguments) == 2 and arguments[1] is Ellipsis:
            return {"type": "array", "items": _schema(arguments[0])}
        return {"type": "array", "items": _schema(arguments[0]), "minItems": len(arguments), "maxItems": len(arguments)}
    if origin is dict or annotation is dict:
        return {"type": "object"}
    if annotation is bool:
        return {"type": "boolean"}
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    if annotation is str:
        return {"type": "string"}
    raise TypeError(f"Unsupported tool parameter type: {annotation!r}")


def _input_schema(function: Callable[..., object], documented: dict[str, str]) -> JsonObject:
    hints: dict[str, object] = get_type_hints(function)
    properties: JsonObject = {}
    required: list[str] = []
    name: str
    parameter: inspect.Parameter
    for name, parameter in inspect.signature(function).parameters.items():
        schema: JsonObject = _schema(hints[name])
        if name in documented:
            schema["description"] = documented[name]
        if parameter.default is inspect.Parameter.empty:
            required.append(name)
        elif parameter.default is not None:
            schema["default"] = parameter.default
        properties[name] = schema
    result: JsonObject = {"type": "object", "properties": properties}
    if required:
        result["required"] = required
    return result


def tool(*, undo: str = "", read_only: bool = False, approval: bool = False) -> Callable[[ToolFunction], ToolFunction]:
    """Register a function as a tool. Edits name an undo step; inspection tools are read-only."""

    def register(function: ToolFunction) -> ToolFunction:
        description: str
        documented: dict[str, str]
        description, documented = _docstring(function)
        name: str = function.__name__
        _TOOLS[name] = Tool(name, description, _input_schema(function, documented), function, undo, read_only, approval)
        return function

    return register


def tools() -> list[Tool]:
    """Every registered tool, in registration order."""
    return list(_TOOLS.values())


def get(name: str) -> Tool:
    found: Tool | None = _TOOLS.get(name)
    if found is None:
        raise ToolError(f"Unknown tool {name!r}. Available: {', '.join(_TOOLS)}")
    return found


def call(name: str, arguments: Mapping[str, object] | None = None) -> object:
    """Run a tool by name with JSON-style arguments, as agents do."""
    entry: Tool = get(name)
    values: dict[str, object] = dict(arguments or {})
    parameters: Mapping[str, inspect.Parameter] = inspect.signature(entry.function).parameters
    unknown: list[str] = sorted(set(values) - set(parameters))
    if unknown:
        raise ToolError(f"{name} does not take: {', '.join(unknown)}")
    missing: list[str] = []
    key: str
    parameter: inspect.Parameter
    for key, parameter in parameters.items():
        if parameter.default is inspect.Parameter.empty and key not in values:
            missing.append(key)
    if missing:
        raise ToolError(f"{name} needs: {', '.join(missing)}")
    # Literal choices are not checked by Python itself; agents get a clear message instead.
    properties: object = entry.schema.get("properties")
    value: object
    for key, value in values.items():
        described: object = properties.get(key) if isinstance(properties, dict) else None
        options: object = described.get("enum") if isinstance(described, dict) else None
        if isinstance(options, list) and value is not None and value not in options:
            choices: list[str] = []
            option: object
            for option in options:
                choices.append(str(option))
            raise ToolError(f"{key} must be one of: {', '.join(choices)}")
    return entry.function(**values)


def to_json(value: object) -> str:
    """Tool results as readable JSON text."""
    return json.dumps(value, indent=2, ensure_ascii=False, default=str)
