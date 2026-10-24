"""Weave: component document → HTML page with generated diagrams.

The page is the rst document itself, with three additions:

* each state machine section gets its generated diagram and a transition
  table, placed after its introduction and before its States section;
* every `name` written with the default `sym` role links to the place it
  is declared;
* a banner at the top lists any diagnostics, so problems are visible in the
  rendered page as well as on the command line.
"""

from __future__ import annotations

from html import escape
from pathlib import Path

from docutils import nodes
from docutils.core import publish_from_doctree

from .checks import check_all
from .diagnostics import Diagnostics
from .diagram import LayoutError, diagram_svg
from .frontend import SYM_CLASS, ParseResult, parse_file
from .ir import ANY, StateMachine

CSS = Path(__file__).with_name("heddle.css")


def _transition_table(m: StateMachine) -> str:
    rows = []
    for t in m.transitions:
        src = "<em>any</em>" if t.source == ANY else f"<code>{escape(t.source)}</code>"
        action = f"<code>{escape(t.action)}</code>" if t.action else ""
        rows.append(
            f"<tr><td>{src}</td><td><code>{escape(t.target)}</code></td>"
            f'<td class="num">{t.priority}</td><td><code>{escape(t.guard)}</code></td>'
            f"<td>{action}</td><td>{escape(t.description)}</td>"
            f'<td class="src">line {t.loc.line}</td></tr>'
        )
    return (
        f"<details><summary>Transition table ({len(m.transitions)})</summary>"
        '<table class="hd-transitions"><thead><tr><th>From</th><th>To</th><th>Pri</th>'
        "<th>Guard</th><th>Action</th><th>Why</th><th></th></tr></thead><tbody>"
        + "".join(rows) + "</tbody></table></details>"
    )


def _figure(m: StateMachine, svg: str | None, error: str | None) -> nodes.raw:
    body = svg if svg else f'<p class="hd-diags has-errors">No diagram: {escape(error or "")}</p>'
    html = (
        f'<figure class="hd-figure" id="diagram-{escape(m.name.lower().replace(" ", "-"))}">'
        f'<div class="hd-diagram">{body}</div>'
        f"<figcaption>{escape(m.name)}: generated from the States and Transitions below. "
        f"Hover a state or transition for its description.</figcaption>"
        f"{_transition_table(m)}</figure>"
    )
    return nodes.raw("", html, format="html")


def _insert_diagrams(result: ParseResult, direction: str | None) -> None:
    for m in result.component.machines:
        section = result.machine_sections.get(m.name)
        if section is None:
            continue
        svg = error = None
        try:
            svg = diagram_svg(m, direction)
        except LayoutError as exc:
            error = str(exc)
            result.diagnostics.error(f"diagram for '{m.name}' failed: {error}", m.loc)
        first_sub = next((i for i, c in enumerate(section.children)
                          if isinstance(c, nodes.section)), len(section.children))
        section.insert(first_sub, _figure(m, svg, error))


def _link_symbols(result: ParseResult) -> None:
    for lit in list(result.doctree.findall(nodes.literal)):
        if SYM_CLASS not in lit.get("classes", []):
            continue
        anchor = result.anchors.get(lit.astext().strip())
        if anchor:
            ref = nodes.reference("", "", refid=anchor)
            lit.replace_self(ref)
            ref += lit


def _banner(diags: Diagnostics) -> nodes.raw | None:
    if not diags:
        return None
    items = "".join(
        f'<li><span class="level-{d.level}">{d.level}</span> '
        f"<code>line {d.loc.line or '?'}</code> {escape(d.message)}</li>"
        for d in diags.sorted()
    )
    errs, warns = len(diags.errors), len(diags.warnings)
    cls = "hd-diags has-errors" if errs else "hd-diags"
    summary = f"Heddle found {errs} error(s) and {warns} warning(s) in this document."
    return nodes.raw("", f'<div class="{cls}"><strong>{summary}</strong><ul>{items}</ul></div>',
                     format="html")


def _place_banner(doctree: nodes.document, banner: nodes.raw) -> None:
    # After the title and docinfo, before the first paragraph.
    idx = 0
    for i, child in enumerate(doctree.children):
        if isinstance(child, (nodes.title, nodes.docinfo, nodes.subtitle)):
            idx = i + 1
    doctree.insert(idx, banner)


def build_html(src: str | Path, out: str | Path, direction: str | None = None,
               refresh: int | None = None) -> Diagnostics:
    """Build one component document to an HTML file. Returns its diagnostics."""
    result = parse_file(src)
    check_all(result)
    _insert_diagrams(result, direction)
    _link_symbols(result)
    banner = _banner(result.diagnostics)
    if banner is not None:
        _place_banner(result.doctree, banner)
    if refresh:
        result.doctree.insert(0, nodes.raw(
            "", f'<meta http-equiv="refresh" content="{int(refresh)}">', format="html"))

    html = publish_from_doctree(
        result.doctree,
        writer_name="html5",
        settings_overrides={
            "stylesheet_path": f"minimal.css,{CSS}",
            "embed_stylesheet": True,
            "report_level": 5,
            "output_encoding": "unicode",
        },
    )
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return result.diagnostics
