"""View graph → ELK JSON → laid-out geometry, using elkjs through Node.

ELK Layered is a Sugiyama-style layered layout like Graphviz `dot`, but
far more configurable. The options that matter most for state machines:

* orthogonal edge routing (right-angle connectors read better than splines);
* model order: the authored order of states and transitions is used to
  break cycles and order nodes, so the nominal flow reads left to right
  and recovery transitions become the back edges;
* hierarchy handling, so the "any state" group is laid out together with
  the states it contains.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .view import View

SCRIPT = Path(__file__).with_name("elk_layout.cjs")

LAYOUT_OPTIONS = {
    "elk.algorithm": "layered",
    "elk.direction": "RIGHT",
    "elk.edgeRouting": "ORTHOGONAL",
    # Each group is laid out on its own, with the same options, so model order
    # also governs the states inside it. Edges that cross a group boundary are
    # split at a port on the group's border (see to_elk).
    "elk.hierarchyHandling": "SEPARATE_CHILDREN",
    "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES",
    "elk.layered.cycleBreaking.strategy": "MODEL_ORDER",
    "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
    "elk.layered.nodePlacement.favorStraightEdges": "true",
    "elk.spacing.nodeNode": "28",
    "elk.layered.spacing.nodeNodeBetweenLayers": "44",
    "elk.spacing.edgeNode": "16",
    "elk.spacing.edgeEdge": "12",
    "elk.layered.spacing.edgeEdgeBetweenLayers": "12",
    "elk.layered.spacing.edgeNodeBetweenLayers": "16",
    "elk.spacing.edgeLabel": "4",
    "elk.edgeLabels.placement": "CENTER",
    "elk.layered.edgeLabels.sideSelection": "SMART_UP",
    "elk.padding": "[top=16,left=16,bottom=16,right=16]",
}

GROUP_PADDING = "[top=34,left=18,bottom=18,right=18]"

DIRECTIONS = {"right": "RIGHT", "down": "DOWN"}


class LayoutError(RuntimeError):
    pass


@dataclass
class Box:
    id: str
    x: float
    y: float
    w: float
    h: float


@dataclass
class EdgeGeom:
    id: str
    points: list[tuple[float, float]]
    label: Box | None


@dataclass
class Layout:
    width: float
    height: float
    nodes: dict[str, Box]
    groups: dict[str, Box]
    edges: dict[str, EdgeGeom]


def to_elk(view: View, direction: str = "right") -> dict:
    """Build the ELK input graph. The order of children and edges is model order.

    An edge between a state inside a group and one outside it is split in two
    at a port on the group's border: the outer half is laid out with the
    group's siblings, the inner half inside the group. ``layout`` joins the
    halves back together.
    """
    options = dict(LAYOUT_OPTIONS)
    options["elk.direction"] = DIRECTIONS.get(direction, "RIGHT")
    parent = {n.id: n.parent for n in view.nodes}
    for g in view.groups:
        parent[g.id] = None

    groups = {}
    for g in view.groups:
        gopts = dict(options)
        gopts["elk.padding"] = GROUP_PADDING
        gopts["elk.portConstraints"] = "FIXED_SIDE"
        groups[g.id] = {"id": g.id, "layoutOptions": gopts, "children": [], "ports": [],
                        "edges": []}

    root: list = []
    placed_groups = set()
    for n in view.nodes:
        if n.parent and n.parent not in placed_groups:
            root.append(groups[n.parent])  # the group sits where its first member is
            placed_groups.add(n.parent)
        target = groups[n.parent]["children"] if n.parent else root
        target.append({"id": n.id, "width": n.width, "height": n.height})
    for gid, g in groups.items():
        if gid not in placed_groups:
            root.append(g)

    def label(e, eid):
        return [{"id": f"{eid}_label", "text": e.id, "width": e.label_w, "height": e.label_h}]

    root_edges = []
    for e in view.edges:
        ps, pt = parent.get(e.source), parent.get(e.target)
        if ps == pt:
            edge = {"id": e.id, "sources": [e.source], "targets": [e.target]}
            if e.lines:
                edge["labels"] = label(e, e.id)
            (groups[ps]["edges"] if ps else root_edges).append(edge)
            continue
        # Crosses a group border: split at a port. Incoming edges enter from
        # below, outgoing ones leave from the flow side, so returns read as
        # returns.
        inner_group = pt if pt else ps
        incoming = pt is not None
        port = f"{e.id}_port"
        side = "SOUTH" if incoming else ("EAST" if direction == "right" else "SOUTH")
        groups[inner_group]["ports"].append(
            {"id": port, "width": 1, "height": 1, "layoutOptions": {"elk.port.side": side}})
        outer = {"id": f"{e.id}__outer",
                 "sources": [e.source if incoming else port],
                 "targets": [port if incoming else e.target]}
        inner = {"id": f"{e.id}__inner",
                 "sources": [port if incoming else e.source],
                 "targets": [e.target if incoming else port]}
        if e.lines:
            outer["labels"] = label(e, e.id)
        root_edges.append(outer)
        groups[inner_group]["edges"].append(inner)

    return {"id": "root", "layoutOptions": options, "children": root, "edges": root_edges}


def _node_command() -> list[str]:
    node = shutil.which("node")
    if not node:
        raise LayoutError("Node.js is required for layout: install it and run `npm install` "
                          "in the heddle repository root")
    return [node, str(SCRIPT)]


def run_elk(graph: dict) -> dict:
    env = dict(os.environ)
    extra = env.get("HEDDLE_NODE_PATH")
    if extra:
        env["NODE_PATH"] = os.pathsep.join(p for p in (extra, env.get("NODE_PATH")) if p)
    proc = subprocess.run(_node_command(), input=json.dumps(graph), capture_output=True,
                          text=True, env=env, timeout=60)
    if proc.returncode != 0:
        msg = proc.stderr.strip()
        if "Cannot find module 'elkjs" in msg:
            msg = "elkjs is not installed: run `npm install` in the heddle repository root"
        raise LayoutError(msg)
    return json.loads(proc.stdout)


def _absolute(graph: dict) -> tuple[dict[str, tuple[float, float]], dict[str, dict]]:
    """Absolute offsets of every node (including the root) and the node dicts."""
    offsets: dict[str, tuple[float, float]] = {graph["id"]: (0.0, 0.0)}
    all_nodes: dict[str, dict] = {}

    def walk(node: dict, ox: float, oy: float) -> None:
        for child in node.get("children", []):
            cx, cy = ox + child.get("x", 0.0), oy + child.get("y", 0.0)
            offsets[child["id"]] = (cx, cy)
            all_nodes[child["id"]] = child
            walk(child, cx, cy)

    walk(graph, 0.0, 0.0)
    return offsets, all_nodes


def _edges(node: dict):
    """Every edge in the laid-out graph, with the id of the node that holds it."""
    for e in node.get("edges", []):
        yield node["id"], e
    for child in node.get("children", []):
        yield from _edges(child)


def _geom(e: dict, ox: float, oy: float):
    pts: list[tuple[float, float]] = []
    for sec in e.get("sections", []):
        seq = [sec["startPoint"], *sec.get("bendPoints", []), sec["endPoint"]]
        pts += [(p["x"] + ox, p["y"] + oy) for p in seq]
    label = None
    for lb in e.get("labels", []):
        label = Box(lb["id"], lb["x"] + ox, lb["y"] + oy, lb["width"], lb["height"])
    return pts, label


def layout(view: View, direction: str = "right") -> Layout:
    out = run_elk(to_elk(view, direction))
    offsets, all_nodes = _absolute(out)
    group_ids = {g.id for g in view.groups}

    nodes, groups = {}, {}
    for nid, n in all_nodes.items():
        x, y = offsets[nid]
        box = Box(nid, x, y, n["width"], n["height"])
        (groups if nid in group_ids else nodes)[nid] = box

    edges: dict[str, EdgeGeom] = {}
    halves: dict[str, dict[str, tuple]] = {}
    for owner, e in _edges(out):
        ox, oy = offsets.get(e.get("container", owner), offsets[owner])
        pts, label = _geom(e, ox, oy)
        eid = e["id"]
        if "__" in eid:
            base, half = eid.split("__")
            halves.setdefault(base, {})[half] = (pts, label, e)
        else:
            edges[eid] = EdgeGeom(eid, pts, label)

    # Join split edges back together at their port.
    for base, parts in halves.items():
        outer_pts, label, outer = parts.get("outer", ([], None, {}))
        inner_pts, _, _ = parts.get("inner", ([], None, {}))
        incoming = bool(outer) and outer["targets"][0].endswith("_port")
        pts = outer_pts + inner_pts[1:] if incoming else inner_pts + outer_pts[1:]
        edges[base] = EdgeGeom(base, pts, label)

    return Layout(out["width"], out["height"], nodes, groups, edges)
