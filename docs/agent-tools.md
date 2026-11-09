# Agent tools

Every tool the Blender MCP server offers, copied from the `@tool` functions in `app/drawing_app/api/`.
The docstrings are the source: the first paragraphs become the tool's description and the `Args:`
entries become its parameter descriptions. Edits here do not reach the agent until they are made in
the docstrings themselves.

## `app/drawing_app/api/code.py`

### `execute_python`

```python
@tool(undo='Run Python', approval=True)
def execute_python(code: str) -> JsonObject
```

```text
Run Python inside the app with full access to Blender (bpy) and the drawing API.

The value of a final expression is returned with anything printed. Prefer the other tools when
they fit; use this for anything else, such as modifiers, materials, or bulk edits.

Args:
    code: Python source. `bpy`, `api` (drawing_app.api), and `context` are already defined.
```

## `app/drawing_app/api/drawing.py`

### `draw_stroke`

```python
@tool(undo='Draw stroke')
def draw_stroke(points: list[list[float]], kind: Literal['pipe', 'paint', 'grease']='pipe', radius: float | None=None, color: str | None=None, shape: Literal['circle', 'square', 'triangle'] | None=None, closed: bool=False, normal: list[float] | None=None) -> JsonObject
```

```text
Draw a stroke through 3D points in the active drawing group, like one drawn by hand.

A pipe is a tube, paint is a flat brush ribbon facing `normal`, and grease is a Grease Pencil
line. Give enough points for curves to look smooth; pipe and paint strokes are resampled between
points like hand-drawn ones, so straight segments need only their end points.

Args:
    points: The path as [x, y, z] points in metres; at least two.
    kind: "pipe", "paint", or "grease".
    radius: Half the stroke width in metres; defaults to the tool's brush size (1 cm for grease).
    color: Hex colour such as "#FF8000" or a preset name; defaults to the tool's colour.
    shape: Cross-section of pipe and paint strokes; defaults to the tool's brush shape.
    closed: Connect the last point back to the first.
    normal: Direction a paint stroke faces, as [x, y, z]; defaults to up.
```

### `set_tool`

```python
@tool()
def set_tool(tool: Literal['select', 'move', 'rotate', 'scale', 'paint', 'pipe', 'printer', 'grease']) -> JsonObject
```

```text
Switch the app's active tool, as clicking it in the Tool Box does.

Args:
    tool: "select", "move", "rotate", "scale", "paint", "pipe", "printer" (3D Printer, which pours
        material that settles into a mesh), or "grease" (Grease Pencil Draw).
```

### `set_color`

```python
@tool()
def set_color(color: str, tool: Literal['all', 'paint', 'pipe', 'printer', 'grease']='all') -> JsonObject
```

```text
Set the drawing colour for every drawing tool, like the Tool Box picker, or for one tool.

Args:
    color: Hex colour such as "#FF8000" or a preset name.
    tool: "all" for every drawing tool, or the one tool to recolour.
```

### `set_brush`

```python
@tool()
def set_brush(tool: Literal['paint', 'pipe'], radius: float | None=None, shape: Literal['circle', 'square', 'triangle'] | None=None, hardness: float | None=None, opacity: float | None=None) -> JsonObject
```

```text
Change the brush of the paint or pipe tool; omitted settings are kept.

Args:
    tool: "paint" or "pipe".
    radius: Brush radius in metres.
    shape: Cross-section: "circle", "square", or "triangle".
    hardness: Edge hardness from 0 to 1 (paint).
    opacity: Opacity from 0 to 1.
```

### `create_group`

```python
@tool(undo='Create drawing group')
def create_group(name: str, activate: bool=True) -> JsonObject
```

```text
Create a drawing group; new strokes go to the active group.

Args:
    name: Group name; Blender adds a number if it is taken.
    activate: Make the new group the active one.
```

### `set_active_group`

```python
@tool()
def set_active_group(name: str) -> JsonObject
```

```text
Make a drawing group active, so new strokes go into it.

Args:
    name: Group name, as listed by scene_info.
```

### `create_drawing_plane`

```python
@tool(undo='Create drawing plane')
def create_drawing_plane(location: list[float] | None=None, rotation: list[float] | None=None, size: float=2.0) -> JsonObject
```

```text
Add a drawing plane and draw on it: strokes drawn by hand then land on this plane.

Args:
    location: Centre as [x, y, z] in metres; defaults to the 3D cursor when placed, else the origin.
    rotation: Orientation as [x, y, z] Euler angles in degrees; the default lies flat (facing +Z).
    size: Edge length in metres.
```

## `app/drawing_app/api/scene.py`

### `scene_info`

```python
@tool(read_only=True)
def scene_info() -> JsonObject
```

```text
Summarize the drawing: groups, objects, selection, active tool, colours, units, and canvas options.

Positions and sizes are in metres. Strokes are paint/pipe meshes or a group's Grease Pencil object.
```

### `object_info`

```python
@tool(read_only=True)
def object_info(name: str) -> JsonObject
```

```text
Describe one object: transform, size, world bounds, colour, geometry, and stroke data.

Args:
    name: Object name, as listed by scene_info.
```

### `add_primitive`

```python
@tool(undo='Add shape')
def add_primitive(shape: Literal['cube', 'sphere', 'cylinder', 'cone', 'plane'], location: list[float] | None=None, size: float=1.0, name: str='') -> JsonObject
```

```text
Add a basic shape to the active drawing group, styled like shapes from the Create menu.

Args:
    shape: "cube", "sphere", "cylinder", "cone", or "plane".
    location: Centre as [x, y, z] in metres; defaults to the 3D cursor when it is on, else the origin.
    size: Overall size (edge length or diameter) in metres.
    name: Object name; defaults to the shape's name.
```

### `transform_object`

```python
@tool(undo='Transform object')
def transform_object(name: str, location: list[float] | None=None, rotation: list[float] | None=None, scale: list[float] | None=None) -> JsonObject
```

```text
Move, rotate, or scale an object. Values replace the current ones; omitted ones are kept.

Args:
    name: Object name.
    location: New position as [x, y, z] in metres.
    rotation: New rotation as [x, y, z] Euler angles in degrees.
    scale: New scale factors as [x, y, z].
```

### `delete_objects`

```python
@tool(undo='Delete objects')
def delete_objects(names: list[str]) -> JsonObject
```

```text
Delete objects by name. Locked objects are kept.

Args:
    names: Names of the objects to delete.
```

### `select_objects`

```python
@tool(undo='Select objects')
def select_objects(names: list[str]) -> JsonObject
```

```text
Replace the selection with the named objects; an empty list clears it. The first becomes active.

Args:
    names: Names of the objects to select.
```

## `app/drawing_app/api/view.py`

### `viewport_screenshot`

```python
@tool(read_only=True)
def viewport_screenshot(max_size: int=800) -> Image
```

```text
Capture the 3D viewport as it looks now, to check visual results.

Args:
    max_size: Longest side of the image in pixels (100 to 2000).
```

### `set_view`

```python
@tool()
def set_view(view: Literal['perspective', 'front', 'back', 'right', 'left', 'top', 'bottom']) -> JsonObject
```

```text
Look at the drawing from a named direction, keeping the current view target.

Args:
    view: "perspective", or an orthographic "front", "back", "right", "left", "top", or "bottom" view.
```

### `frame_objects`

```python
@tool()
def frame_objects(names: list[str] | None=None) -> JsonObject
```

```text
Zoom the viewport to show the named objects, or everything when no names are given.

Args:
    names: Objects to frame; omit to frame the whole drawing.
```
