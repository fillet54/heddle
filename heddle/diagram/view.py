"""IR → view graph: the readability transforms applied before layout.

Most unreadable state diagrams come from feeding the raw graph to the
layout engine. Before layout we:

* collapse ``any → X`` transitions into one edge from a dashed group that
  holds every state they apply to, instead of N arrows into X;
* merge parallel transitions between the same two states into one edge
  with stacked labels;
* move self-transitions into the state box as text lines, since self-loops
  add clutter and say little;
* keep the authored order of states and transitions, which the layout uses
  to decide what reads as "forward".
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass, field

from ..ir import StateMachine, Transition

# Text metrics used to size boxes and labels before layout. They are
# deliberately a little generous so text never touches a border.
NAME_SIZE = 13.0
NAME_CHAR_W = 7.9      # bold sans at 13 px
DETAIL_SIZE = 11.0
MONO_CHAR_W = 6.7      # monospace at 11 px
LINE_H = 15.0
PAD_X = 14.0
LABEL_WRAP = 30        # characters per edge-label line


@dataclass
class Line:
    text: str
    style: str  # "name" | "detail" | "label" | "action"


@dataclass
class VNode:
    id: str
    kind: str  # "state" | "initial"
    lines: list[Line] = field(default_factory=list)
    tooltip: str = ""
    width: float = 0.0
    height: float = 0.0
    parent: str | None = None  # group id


@dataclass
class VEdge:
    id: str
    source: str
    target: str
    kind: str  # "normal" | "any" | "initial"
    lines: list[Line] = field(default_factory=list)
    tooltip: str = ""
    label_w: float = 0.0
    label_h: float = 0.0


@dataclass
class VGroup:
    id: str
    label: str
    children: list[str]


@dataclass
class View:
    name: str
    nodes: list[VNode]
    edges: list[VEdge]
    groups: list[VGroup]


def _mono_width(text: str) -> float:
    return len(text) * MONO_CHAR_W


def _wrap(text: str, width: int = LABEL_WRAP) -> list[str]:
    return textwrap.wrap(text, width=width, break_long_words=False) or [""]


def _transition_lines(t: Transition, prefix: str = "") -> list[Line]:
    guard = _wrap(f"{prefix}[{t.priority}] {t.guard}")
    lines = [Line(g, "label") for g in guard]
    if t.action:
        lines += [Line(a, "action") for a in _wrap(f"/ {t.action}")]
    return lines


def _state_node(s, self_loops: list[Transition]) -> VNode:
    node = VNode(id=f"s_{s.name}", kind="state", tooltip=s.description)
    node.lines.append(Line(s.name, "name"))
    for attr in ("entry", "during", "exit"):
        value = getattr(s, attr)
        if value:
            node.lines.append(Line(f"{attr} / {value}", "detail"))
    for t in self_loops:
        for i, ln in enumerate(_transition_lines(t, prefix="↻ ")):
            node.lines.append(Line(ln.text if i == 0 else "  " + ln.text, "detail"))
    name_w = len(s.name) * NAME_CHAR_W
    detail_w = max((_mono_width(ln.text) for ln in node.lines[1:]), default=0.0)
    node.width = max(96.0, name_w, detail_w) + 2 * PAD_X
    details = len(node.lines) - 1
    node.height = 34.0 + (10.0 + details * LINE_H if details else 0.0)
    if self_loops:
        node.tooltip += "".join(
            f"\n↻ [{t.priority}] {t.guard}: {t.description}" for t in self_loops
        )
    return node


def build_view(m: StateMachine) -> View:
    states = [s.name for s in m.states]
    any_ts = [t for t in m.transitions if t.is_any and t.target in states]
    any_targets = {t.target for t in any_ts}

    groups: list[VGroup] = []
    group_id = None
    if any_ts:
        group_id = "g_any"
        members = [f"s_{s}" for s in states if s not in any_targets]
        groups.append(VGroup(group_id, "any state", members))

    self_loops = {s: [t for t in m.transitions if t.is_self and t.source == s] for s in states}

    nodes: list[VNode] = []
    initial = VNode(id="initial", kind="initial", width=14.0, height=14.0,
                    tooltip=f"initial state: {m.initial}")
    for s in m.states:
        node = _state_node(s, self_loops[s.name])
        if group_id and s.name not in any_targets:
            node.parent = group_id
        if s.name == m.initial:
            initial.parent = node.parent
            nodes.append(initial)  # model order: the dot sits just before its state
        nodes.append(node)

    edges: list[VEdge] = []
    if m.initial in states:
        edges.append(VEdge("e_initial", "initial", f"s_{m.initial}", "initial"))

    # Merge parallel transitions, keeping first-authored order.
    merged: dict[tuple[str, str], list[Transition]] = {}
    for t in m.transitions:
        if t.is_any or t.is_self or t.source not in states or t.target not in states:
            continue
        merged.setdefault((t.source, t.target), []).append(t)

    for i, ((src, dst), ts) in enumerate(merged.items()):
        ts = sorted(ts, key=lambda t: t.priority)
        edge = VEdge(f"e{i}", f"s_{src}", f"s_{dst}", "normal")
        for t in ts:
            edge.lines += _transition_lines(t)
        edge.tooltip = "\n".join(f"[{t.priority}] {t.guard}: {t.description}" for t in ts)
        edges.append(edge)

    for j, t in enumerate(any_ts):
        edge = VEdge(f"a{j}", group_id, f"s_{t.target}", "any", _transition_lines(t),
                     f"from any state, [{t.priority}] {t.guard}: {t.description}")
        edges.append(edge)

    for e in edges:
        if e.lines:
            e.label_w = max(_mono_width(ln.text) for ln in e.lines) + 8.0
            e.label_h = len(e.lines) * 13.0 + 4.0

    return View(m.name, nodes, edges, groups)
