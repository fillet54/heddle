"""State machine diagrams: IR → view transforms → ELK layout → SVG."""

from __future__ import annotations

from ..ir import StateMachine
from .elk import Layout, LayoutError, layout, to_elk
from .svg import render
from .view import View, build_view

__all__ = ["LayoutError", "diagram_svg", "build_view", "layout", "render", "to_elk",
           "Layout", "View"]


MAX_AUTO_WIDTH = 1100.0  # wider than this, "auto" switches to a top-down flow


def diagram_svg(machine: StateMachine, direction: str | None = None) -> str:
    """Render one state machine to an SVG string.

    ``direction`` is ``right``, ``down`` or ``auto``; it defaults to the
    machine's own ``:direction:`` field, then ``auto``. Auto lays the machine
    out left to right and falls back to top-down when that is too wide to
    read in a document column.
    """
    view = build_view(machine)
    direction = direction or machine.direction or "auto"
    if direction == "auto":
        lay = layout(view, "right")
        if lay.width > MAX_AUTO_WIDTH:
            lay = layout(view, "down")
    else:
        lay = layout(view, direction)
    return render(view, lay, initial=machine.initial)
