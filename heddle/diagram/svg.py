"""Laid-out view → SVG.

The SVG carries its own small stylesheet with CSS custom properties, so it
renders standalone, follows the reader's light/dark preference, and can be
restyled by a host page by overriding the ``--hd-*`` variables.
"""

from __future__ import annotations

import re
from html import escape

from .elk import Layout
from .view import DETAIL_SIZE, LINE_H, NAME_SIZE, View

STYLE = """
.hd-svg{--hd-ink:#1f2328;--hd-quiet:#59636e;--hd-edge:#57606a;--hd-box:#ffffff;
--hd-box-stroke:#8c959f;--hd-bg:#ffffff;--hd-group:#f6f8fa;--hd-group-stroke:#afb8c1;
--hd-any:#cf222e;--hd-initial:#1f2328;
font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
@media (prefers-color-scheme:dark){.hd-svg{--hd-ink:#e6edf3;--hd-quiet:#9198a1;
--hd-edge:#9198a1;--hd-box:#161b22;--hd-box-stroke:#6e7681;--hd-bg:#0d1117;
--hd-group:#11161d;--hd-group-stroke:#3d444d;--hd-any:#ff7b72;--hd-initial:#e6edf3}}
.hd-svg .mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.hd-svg .group{fill:var(--hd-group);stroke:var(--hd-group-stroke);stroke-dasharray:6 4}
.hd-svg .group-label{fill:var(--hd-quiet);font-size:11.5px;font-style:italic}
.hd-svg .state{fill:var(--hd-box);stroke:var(--hd-box-stroke);stroke-width:1.25}
.hd-svg .state.initial-state{stroke:var(--hd-ink);stroke-width:1.75}
.hd-svg .sep{stroke:var(--hd-box-stroke);stroke-width:.75}
.hd-svg .name{fill:var(--hd-ink);font-weight:600;font-size:13px}
.hd-svg .detail{fill:var(--hd-quiet);font-size:11px}
.hd-svg .edge{fill:none;stroke:var(--hd-edge);stroke-width:1.25}
.hd-svg .edge.any{stroke:var(--hd-any);stroke-dasharray:5 3}
.hd-svg .arrow{fill:var(--hd-edge)}
.hd-svg .arrow.any{fill:var(--hd-any)}
.hd-svg .initial{fill:var(--hd-initial)}
.hd-svg .label-bg{fill:var(--hd-bg);opacity:.92}
.hd-svg .label{fill:var(--hd-ink);font-size:11px}
.hd-svg .label.any{fill:var(--hd-any)}
.hd-svg .action{fill:var(--hd-quiet);font-size:11px}
.hd-svg g.node:hover .state{stroke:var(--hd-ink);stroke-width:2}
.hd-svg g.edge-group:hover .edge{stroke-width:2.25}
"""

RADIUS = 6.0


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "sm"


def _rounded_path(points: list[tuple[float, float]]) -> str:
    """An orthogonal polyline with its corners rounded."""
    if not points:
        return ""
    d = [f"M{points[0][0]:.1f} {points[0][1]:.1f}"]
    for i in range(1, len(points) - 1):
        (x0, y0), (x1, y1), (x2, y2) = points[i - 1], points[i], points[i + 1]
        r = min(RADIUS, abs(x1 - x0 + y1 - y0) / 2, abs(x2 - x1 + y2 - y1) / 2)

        def toward(ax, ay, bx, by, dist):
            dx, dy = bx - ax, by - ay
            length = (dx * dx + dy * dy) ** 0.5 or 1.0
            return ax + dx / length * dist, ay + dy / length * dist

        sx, sy = toward(x1, y1, x0, y0, r)
        ex, ey = toward(x1, y1, x2, y2, r)
        d.append(f"L{sx:.1f} {sy:.1f}Q{x1:.1f} {y1:.1f} {ex:.1f} {ey:.1f}")
    d.append(f"L{points[-1][0]:.1f} {points[-1][1]:.1f}")
    return "".join(d)


def render(view: View, lay: Layout, initial: str | None = None) -> str:
    sid = _slug(view.name)
    w, h = lay.width, lay.height
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" class="hd-svg" '
        f'viewBox="0 0 {w:.0f} {h:.0f}" width="{w:.0f}" height="{h:.0f}" '
        f'role="img" aria-label="State machine {escape(view.name)}" data-machine="{sid}">',
        f"<style>{STYLE}</style>",
        "<defs>",
        f'<marker id="{sid}-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        f'markerHeight="7" orient="auto-start-reverse"><path class="arrow" d="M0 0L10 5L0 10z"/>'
        f"</marker>",
        f'<marker id="{sid}-arrow-any" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        f'markerHeight="7" orient="auto-start-reverse"><path class="arrow any" '
        f'd="M0 0L10 5L0 10z"/></marker>',
        "</defs>",
    ]

    # Groups first, so everything else sits on top of them.
    for g in view.groups:
        b = lay.groups.get(g.id)
        if not b:
            continue
        out.append(f'<g class="group-box" data-group="{escape(g.id)}">'
                   f'<rect class="group" x="{b.x:.1f}" y="{b.y:.1f}" width="{b.w:.1f}" '
                   f'height="{b.h:.1f}" rx="12"/>'
                   f'<text class="group-label" x="{b.x + 14:.1f}" y="{b.y + 21:.1f}">'
                   f"{escape(g.label)}</text></g>")

    # Edges.
    labels = []
    for e in view.edges:
        geom = lay.edges.get(e.id)
        if not geom or not geom.points:
            continue
        cls = "edge any" if e.kind == "any" else "edge"
        marker = f"{sid}-arrow-any" if e.kind == "any" else f"{sid}-arrow"
        tip = f"<title>{escape(e.tooltip)}</title>" if e.tooltip else ""
        out.append(f'<g class="edge-group" data-edge="{escape(e.id)}">{tip}'
                   f'<path class="{cls}" d="{_rounded_path(geom.points)}" '
                   f'marker-end="url(#{marker})"/></g>')
        if geom.label and e.lines:
            labels.append((e, geom.label))

    # States.
    for n in view.nodes:
        b = lay.nodes.get(n.id)
        if not b:
            continue
        tip = f"<title>{escape(n.tooltip)}</title>" if n.tooltip else ""
        if n.kind == "initial":
            out.append(f'<g class="node" data-state="initial">{tip}<circle class="initial" '
                       f'cx="{b.x + b.w / 2:.1f}" cy="{b.y + b.h / 2:.1f}" r="{b.w / 2:.1f}"/></g>')
            continue
        name = n.lines[0].text
        is_initial = " initial-state" if name == initial else ""
        parts = [f'<g class="node" data-state="{escape(name)}">{tip}',
                 f'<rect class="state{is_initial}" x="{b.x:.1f}" y="{b.y:.1f}" '
                 f'width="{b.w:.1f}" height="{b.h:.1f}" rx="9"/>',
                 f'<text class="name" x="{b.x + b.w / 2:.1f}" y="{b.y + 22:.1f}" '
                 f'text-anchor="middle" font-size="{NAME_SIZE}">{escape(name)}</text>']
        details = n.lines[1:]
        if details:
            sep_y = b.y + 34
            parts.append(f'<line class="sep" x1="{b.x:.1f}" y1="{sep_y:.1f}" '
                         f'x2="{b.x + b.w:.1f}" y2="{sep_y:.1f}"/>')
            for i, ln in enumerate(details):
                y = sep_y + 15 + i * LINE_H
                parts.append(f'<text class="detail mono" x="{b.x + 14:.1f}" y="{y:.1f}" '
                             f'font-size="{DETAIL_SIZE}" xml:space="preserve">'
                             f"{escape(ln.text)}</text>")
        parts.append("</g>")
        out.append("".join(parts))

    # Edge labels last, on a background so lines passing behind stay legible.
    for e, lb in labels:
        cls = " any" if e.kind == "any" else ""
        parts = [f'<g class="edge-label" data-edge="{escape(e.id)}">',
                 f'<rect class="label-bg" x="{lb.x:.1f}" y="{lb.y:.1f}" width="{lb.w:.1f}" '
                 f'height="{lb.h:.1f}" rx="3"/>']
        for i, ln in enumerate(e.lines):
            kind = "action" if ln.style == "action" else f"label{cls}"
            parts.append(f'<text class="{kind} mono" x="{lb.x + 4:.1f}" '
                         f'y="{lb.y + 13 + i * 13:.1f}" font-size="{DETAIL_SIZE}">'
                         f"{escape(ln.text)}</text>")
        parts.append("</g>")
        out.append("".join(parts))

    out.append("</svg>")
    return "\n".join(out)
