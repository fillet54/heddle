"""The rst front end: component documents in, IR out.

It owns the document *conventions* and nothing else:

* the document is the component; its title names it and the docinfo field
  list (``:component:``, ``:rate:``...) right after the title describes it;
* top-level sections with a known title declare things: ``Inputs``,
  ``Outputs``, ``Parameters``, ``State`` and ``Procedures``. Any other
  title is ordinary prose, unless it looks like a typo of a known title;
* inside those sections, a definition list declares one thing per term:
  ``name : type`` with the definition holding prose and an optional
  field list of attributes;
* a subsection of ``Procedures`` that has ``States`` and ``Transitions``
  subsections is a state machine. A transition term is
  ``source → target : priority`` (``->`` also works, ``any`` as the source
  means every state except the target).

Front-end problems are reported as *convention* errors with the rst line.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from pathlib import Path

from docutils import nodes
from docutils.core import publish_doctree
from docutils.parsers.rst import roles

from .diagnostics import Diagnostics
from .ir import ANY, Component, Loc, Port, State, StateMachine, Transition, Variable

COMPONENT_SECTIONS = ("Inputs", "Outputs", "Parameters", "State", "Procedures")
MACHINE_SECTIONS = ("States", "Transitions")

ALLOWED_FIELDS = {
    "port": set(),
    "parameter": {"value"},
    "state variable": {"reset"},
    "state": {"entry", "during", "exit"},
    "transition": {"guard", "action"},
    "machine": {"initial", "kind", "direction"},
}

IDENT = re.compile(r"^[A-Za-z_]\w*$")
TRANSITION_TERM = re.compile(r"^\s*(\S+)\s*(?:→|->)\s*(\S+)\s*$")

SYM_CLASS = "sym"


# --------------------------------------------------------------------------
# The `sym` role: names in prose that resolve against the component.
# --------------------------------------------------------------------------

def _sym_role(name, rawtext, text, lineno, inliner, options=None, content=None):
    node = nodes.literal(rawtext, text, classes=[SYM_CLASS])
    node.line = lineno
    return [node], []


def register_roles() -> None:
    """Register Heddle's rst roles with docutils (idempotent)."""
    roles.register_local_role(SYM_CLASS, _sym_role)


# --------------------------------------------------------------------------
# Small doctree helpers
# --------------------------------------------------------------------------

def _title(section: nodes.section) -> str:
    return section[0].astext().strip()


def _subsections(node: nodes.Element) -> list[nodes.section]:
    return [c for c in node.children if isinstance(c, nodes.section)]


def _line(node: nodes.Node) -> int | None:
    while node is not None:
        if getattr(node, "line", None):
            return node.line
        node = node.parent
    return None


def _fields(field_list: nodes.field_list) -> dict[str, str]:
    out = {}
    for f in field_list.children:
        name = f[0].astext().strip().lower()
        out[name] = " ".join(f[1].astext().split())
    return out


class _Item:
    """One definition-list entry: term, classifiers, prose and fields."""

    def __init__(self, item: nodes.definition_list_item):
        self.node = item
        self.term = item.next_node(nodes.term).astext().strip()
        self.classifiers = [
            c.astext().strip() for c in item.children if isinstance(c, nodes.classifier)
        ]
        self.description = ""
        self.fields: dict[str, str] = {}
        definition = item.next_node(nodes.definition)
        if definition is not None:
            paras = [
                " ".join(p.astext().split())
                for p in definition.children
                if isinstance(p, nodes.paragraph)
            ]
            self.description = " ".join(paras)
            for fl in definition.children:
                if isinstance(fl, nodes.field_list):
                    self.fields.update(_fields(fl))
        self.line = _line(item)


def _items(section: nodes.section) -> list[_Item]:
    out = []
    for child in section.children:
        if isinstance(child, nodes.definition_list):
            out.extend(_Item(i) for i in child.children)
    return out


# --------------------------------------------------------------------------
# The front end proper
# --------------------------------------------------------------------------

class FrontEnd:
    def __init__(self, path: str, diags: Diagnostics):
        self.path = path
        self.diags = diags
        # machine name -> its docutils section, so the HTML build can place the
        # diagram without the IR knowing anything about docutils.
        self.machine_sections: dict[str, nodes.section] = {}
        # declared name -> anchor id on its definition-list term (first wins)
        self.anchors: dict[str, str] = {}

    def anchor(self, item: "_Item", kind: str) -> None:
        """Give a declaration's term an id so `name` references can link to it."""
        anchor = f"{kind}-{item.term}".lower().replace("_", "-")
        term = item.node.next_node(nodes.term)
        if anchor not in term["ids"]:
            term["ids"].append(anchor)
        self.anchors.setdefault(item.term, anchor)

    def loc(self, line: int | None) -> Loc:
        return Loc(self.path, line)

    # -- generic checks ------------------------------------------------------

    def _check_title(self, section: nodes.section, known: tuple[str, ...], where: str) -> None:
        title = _title(section)
        if title in known:
            return
        close = difflib.get_close_matches(title, known, n=1, cutoff=0.75)
        if close:
            self.diags.warning(
                f"section '{title}' in {where} is treated as prose; "
                f"did you mean '{close[0]}'? (recognised: {', '.join(known)})",
                self.loc(_line(section)),
            )

    def _check_fields(self, item: _Item, kind: str) -> None:
        allowed = ALLOWED_FIELDS[kind]
        for name in item.fields:
            if name not in allowed:
                hint = f"; allowed: {', '.join(sorted(allowed))}" if allowed else ""
                self.diags.warning(
                    f"unknown field ':{name}:' on {kind} '{item.term}'{hint}",
                    self.loc(item.line),
                )

    def _check_name(self, name: str, kind: str, line: int | None) -> bool:
        if not IDENT.match(name):
            self.diags.error(f"{kind} name '{name}' is not a valid identifier", self.loc(line))
            return False
        return True

    # -- declarations --------------------------------------------------------

    def _ports(self, section: nodes.section) -> list[Port]:
        out = []
        for it in _items(section):
            if not self._check_name(it.term, "port", it.line):
                continue
            if not it.classifiers:
                self.diags.error(
                    f"port '{it.term}' has no type; write '{it.term} : <type>'", self.loc(it.line)
                )
            self._check_fields(it, "port")
            self.anchor(it, "port")
            out.append(Port(it.term, it.classifiers[0] if it.classifiers else "?",
                            it.description, self.loc(it.line)))
        return out

    def _variables(self, section: nodes.section, kind: str, value_field: str) -> list[Variable]:
        out = []
        for it in _items(section):
            if not self._check_name(it.term, kind, it.line):
                continue
            if not it.classifiers:
                self.diags.error(
                    f"{kind} '{it.term}' has no type; write '{it.term} : <type>'", self.loc(it.line)
                )
            self._check_fields(it, kind)
            self.anchor(it, kind.replace(" ", "-"))
            out.append(Variable(it.term, it.classifiers[0] if it.classifiers else "?",
                                it.description, self.loc(it.line), it.fields.get(value_field)))
        return out

    # -- state machines ------------------------------------------------------

    def _machine(self, section: nodes.section) -> StateMachine | None:
        name = _title(section)
        subs = {_title(s): s for s in _subsections(section)}
        for s in _subsections(section):
            self._check_title(s, MACHINE_SECTIONS, f"state machine '{name}'")
        missing = [t for t in MACHINE_SECTIONS if t not in subs]
        if missing:
            self.diags.error(
                f"state machine '{name}' has no {' or '.join(missing)} section",
                self.loc(_line(section)),
            )
            return None

        description = " ".join(
            " ".join(p.astext().split()) for p in section.children if isinstance(p, nodes.paragraph)
        )
        mfields: dict[str, str] = {}
        for fl in section.children:
            if isinstance(fl, nodes.field_list):
                mfields.update(_fields(fl))
        for fname in mfields:
            if fname not in ALLOWED_FIELDS["machine"]:
                self.diags.warning(f"unknown field ':{fname}:' on state machine '{name}'",
                                   self.loc(_line(section)))

        states: list[State] = []
        for it in _items(subs["States"]):
            if not self._check_name(it.term, "state", it.line):
                continue
            if it.term == ANY:
                self.diags.error(f"'{ANY}' is reserved and cannot name a state", self.loc(it.line))
                continue
            if it.classifiers:
                self.diags.warning(f"state '{it.term}' takes no classifier; ignoring "
                                   f"': {it.classifiers[0]}'", self.loc(it.line))
            self._check_fields(it, "state")
            self.anchor(it, "state")
            states.append(State(it.term, it.description, self.loc(it.line),
                                it.fields.get("entry"), it.fields.get("during"),
                                it.fields.get("exit")))

        transitions: list[Transition] = []
        for it in _items(subs["Transitions"]):
            m = TRANSITION_TERM.match(it.term)
            if not m:
                self.diags.error(
                    f"transition '{it.term}' should read 'source → target : priority'",
                    self.loc(it.line),
                )
                continue
            source, target = m.groups()
            if not it.classifiers:
                self.diags.error(
                    f"transition '{it.term}' has no priority; write '{it.term} : 1'",
                    self.loc(it.line),
                )
                continue
            try:
                priority = int(it.classifiers[0])
            except ValueError:
                self.diags.error(
                    f"priority '{it.classifiers[0]}' on '{it.term}' is not an integer",
                    self.loc(it.line),
                )
                continue
            self._check_fields(it, "transition")
            transitions.append(Transition(source, target, priority, it.fields.get("guard", ""),
                                          it.description, self.loc(it.line),
                                          it.fields.get("action")))

        if not states:
            self.diags.error(f"state machine '{name}' declares no states", self.loc(_line(section)))
            return None
        initial = mfields.get("initial", states[0].name)
        direction = mfields.get("direction")
        if direction and direction not in ("right", "down"):
            self.diags.warning(f"':direction: {direction}' on '{name}' should be right or down",
                               self.loc(_line(section)))
            direction = None
        return StateMachine(name, description, initial, states, transitions,
                            self.loc(_line(section)), direction)

    # -- the document --------------------------------------------------------

    def component(self, doctree: nodes.document) -> Component:
        title_node = doctree.next_node(nodes.title)
        title = title_node.astext().strip() if title_node else Path(self.path).stem
        fields: dict[str, str] = {}
        docinfo = doctree.next_node(nodes.docinfo)
        if docinfo is not None:
            for f in docinfo.children:
                if isinstance(f, nodes.field):
                    fields[f[0].astext().strip().lower()] = " ".join(f[1].astext().split())
        if "component" not in fields:
            self.diags.warning(
                "no ':component:' field under the title; using the title as the component name",
                self.loc(1),
            )
        intro = " ".join(" ".join(p.astext().split()) for p in doctree.children
                         if isinstance(p, nodes.paragraph))
        comp = Component(fields.get("component", title.replace(" ", "")), title,
                         self.loc(1), fields, intro)

        for section in _subsections(doctree):
            self._check_title(section, COMPONENT_SECTIONS, "the component")
            t = _title(section)
            if t == "Inputs":
                comp.inputs += self._ports(section)
            elif t == "Outputs":
                comp.outputs += self._ports(section)
            elif t == "Parameters":
                comp.parameters += self._variables(section, "parameter", "value")
            elif t == "State":
                comp.state_vars += self._variables(section, "state variable", "reset")
            elif t == "Procedures":
                for proc in _subsections(section):
                    titles = {_title(s) for s in _subsections(proc)}
                    if titles & set(MACHINE_SECTIONS):
                        machine = self._machine(proc)
                        if machine:
                            self.machine_sections[machine.name] = proc
                            comp.machines.append(machine)
        return comp


@dataclass
class ParseResult:
    doctree: nodes.document
    component: Component
    diagnostics: Diagnostics
    machine_sections: dict[str, nodes.section]
    anchors: dict[str, str]


def _system_messages(doctree: nodes.document, path: str, diags: Diagnostics) -> None:
    """Turn docutils parse problems into diagnostics and drop them from the tree."""
    for msg in list(doctree.findall(nodes.system_message)):
        level = msg.get("level", 0)
        if level >= 2:
            text = msg[0].astext() if len(msg) else msg.astext()
            loc = Loc(path, msg.get("line"))
            (diags.error if level >= 3 else diags.warning)(f"rst: {text}", loc)
        msg.parent.remove(msg)


def parse_doctree(text: str, path: str, diags: Diagnostics) -> nodes.document:
    register_roles()
    doctree = publish_doctree(
        text,
        source_path=path,
        settings_overrides={
            "report_level": 5,  # silence docutils' own stderr reporting
            "halt_level": 5,
            "warning_stream": False,
            "doctitle_xform": True,
            "sectsubtitle_xform": False,
        },
    )
    _system_messages(doctree, path, diags)
    return doctree


def parse_file(path: str | Path) -> ParseResult:
    """Parse a component document from disk."""
    path = str(path)
    return parse_text(Path(path).read_text(encoding="utf-8"), path)


def parse_text(text: str, path: str = "<string>") -> ParseResult:
    """Parse a component document held in a string."""
    diags = Diagnostics()
    doctree = parse_doctree(text, path, diags)
    fe = FrontEnd(path, diags)
    comp = fe.component(doctree)
    return ParseResult(doctree, comp, diags, fe.machine_sections, fe.anchors)
