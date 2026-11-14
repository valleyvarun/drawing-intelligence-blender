"""Widget records shared by the panel overlay and the panels it draws."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypeAlias

# Left, bottom, width, height in UI units.
Rect: TypeAlias = tuple[float, float, float, float]
# Label, action, payload.
MenuItem: TypeAlias = tuple[str, str, object]


@dataclass
class Widget:
    """A clickable or editable region recorded while a panel draws."""

    key: str
    bounds: Rect
    action: str = ""
    payload: object = None
    kind: str = "button"
    value: float = 0.0
    minimum: float = -1e9
    maximum: float = 1e9
    scale: float = 1.0
    prefix: tuple[object, ...] = ()
    tooltip: str = ""
    # A slider whose values are spaced by ratio along its track.
    log: bool = False


@dataclass
class Edit:
    """Text being edited in a field."""

    widget: Widget
    text: str
    caret: int
    selected: bool = True


@dataclass(frozen=True)
class PopupSlider:
    """A slider row below a dropdown's items; dragging or typing in it keeps the dropdown open.

    It sends (name, value * scale) with `action`, as the Tool Box sliders do.
    """

    name: str
    label: str
    action: str
    value: float
    minimum: float
    maximum: float
    scale: float = 1.0


@dataclass
class Popup:
    """An open dropdown menu; `key` is the button that opened it, which refreshes its sliders."""

    area: int
    x: float
    top: float
    items: list[MenuItem]
    key: str = ""
    sliders: list[PopupSlider] = field(default_factory=list)


@dataclass(frozen=True)
class Question:
    """A yes-or-no question shown over the viewport. Yes (or Enter) runs the action; No (or Esc) does nothing."""

    title: str
    message: str
    action: str
    payload: object = None


def hit(bounds: Rect, x: float, y: float) -> bool:
    return bounds[0] <= x <= bounds[0] + bounds[2] and bounds[1] <= y <= bounds[1] + bounds[3]
