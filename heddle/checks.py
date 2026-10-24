"""Rule checks over the IR (plus `sym` name resolution over the doctree).

Front-end checks are about *conventions*; these are about *meaning*.
They only read the IR, so any future front end gets them for free.
"""

from __future__ import annotations

import re
from collections import defaultdict, deque

from docutils import nodes

from .diagnostics import Diagnostics
from .frontend import SYM_CLASS, _line
from .ir import ANY, Component, Loc, StateMachine

KEYWORDS = {"and", "or", "not", "true", "false"}
IDENTIFIER = re.compile(r"[A-Za-z_]\w*")
ASSIGNMENT = re.compile(r"^\s*([A-Za-z_]\w*)\s*(?:[-+*/]?=)(?!=)")


def _identifiers(expr: str) -> list[str]:
    # Strip string literals before scanning, so 'text' isn't read as names.
    expr = re.sub(r"'[^']*'|\"[^\"]*\"", "", expr)
    return [t for t in IDENTIFIER.findall(expr) if t not in KEYWORDS]


def _statements(actions: str | None) -> list[str]:
    return [s.strip() for s in (actions or "").split(";") if s.strip()]


def check_component(comp: Component, diags: Diagnostics) -> None:
    names = comp.names()

    seen: dict[str, Loc] = {}
    decls = [*comp.inputs, *comp.outputs, *comp.parameters, *comp.state_vars]
    for d in decls:
        if d.name in seen:
            diags.error(f"'{d.name}' is declared twice (first at {seen[d.name]})", d.loc)
        else:
            seen[d.name] = d.loc

    others = {d.name: k for k, ds in (("input", comp.inputs), ("output", comp.outputs),
                                         ("parameter", comp.parameters),
                                         ("state variable", comp.state_vars)) for d in ds}
    for m in comp.machines:
        for s in m.states:
            if s.name in others:
                diags.warning(f"state '{s.name}' has the same name as {others[s.name]} "
                              f"'{s.name}'; `{s.name}` in prose and expressions means the "
                              f"{others[s.name]}", s.loc)
        check_machine(m, comp, names, diags)


def check_machine(m: StateMachine, comp: Component, names: dict[str, str],
                  diags: Diagnostics) -> None:
    state_names = [s.name for s in m.states]
    states = set(state_names)

    # Duplicate state names.
    first: dict[str, Loc] = {}
    for s in m.states:
        if s.name in first:
            diags.error(f"state '{s.name}' is declared twice (first at {first[s.name]})", s.loc)
        first.setdefault(s.name, s.loc)

    if m.initial not in states:
        diags.error(f"initial state '{m.initial}' is not a state of '{m.name}'", m.loc)

    # Transition endpoints and guards.
    valid = []
    for t in m.transitions:
        ok = True
        if t.source != ANY and t.source not in states:
            diags.error(f"transition source '{t.source}' is not a state of '{m.name}'", t.loc)
            ok = False
        if t.target not in states:
            diags.error(f"transition target '{t.target}' is not a state of '{m.name}'", t.loc)
            ok = False
        if not t.guard:
            diags.error(f"transition {t.source} → {t.target} has no ':guard:'", t.loc)
        if ok:
            valid.append(t)

    # Two transitions that can be evaluated at the same priority from the same
    # state make the choice ambiguous. An `any` transition competes with every
    # specific transition out of every state other than its own target.
    by_slot: dict[tuple[str, int], list] = defaultdict(list)
    for t in valid:
        sources = [s for s in state_names if s != t.target] if t.is_any else [t.source]
        for src in sources:
            by_slot[(src, t.priority)].append(t)
    reported = set()
    for (src, prio), ts in by_slot.items():
        if len(ts) > 1:
            key = tuple(sorted(id(t) for t in ts))
            if key in reported:
                continue
            reported.add(key)
            others = ", ".join(f"{t.source} → {t.target} (line {t.loc.line})" for t in ts[1:])
            diags.error(
                f"priority {prio} out of '{src}' is used by more than one transition: "
                f"{ts[0].source} → {ts[0].target} and {others}",
                ts[0].loc,
            )

    # Reachability from the initial state.
    out: dict[str, set[str]] = defaultdict(set)
    any_targets = {t.target for t in valid if t.is_any}
    for t in valid:
        if not t.is_any:
            out[t.source].add(t.target)
    if m.initial in states:
        seen = {m.initial}
        queue = deque([m.initial])
        while queue:
            s = queue.popleft()
            for nxt in out[s] | {a for a in any_targets if a != s}:
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        for s in m.states:
            if s.name not in seen:
                diags.warning(f"state '{s.name}' is unreachable from '{m.initial}'", s.loc)

    # Dead ends: no way out other than a self-transition.
    for s in m.states:
        leaves = {x for x in out[s.name] if x != s.name} | {a for a in any_targets if a != s.name}
        if not leaves:
            diags.warning(f"state '{s.name}' has no transition out of it", s.loc)

    # Names used in guards and actions.
    def check_expr(expr: str | None, what: str, loc: Loc) -> None:
        for ident in _identifiers(expr or ""):
            if ident not in names:
                diags.warning(f"'{ident}' in {what} is not declared in '{comp.name}'", loc)

    def check_actions(actions: str | None, what: str, loc: Loc) -> None:
        check_expr(actions, what, loc)
        for stmt in _statements(actions):
            m_ = ASSIGNMENT.match(stmt)
            if not m_:
                diags.error(f"'{stmt}' in {what} is not an assignment", loc)
                continue
            kind = names.get(m_.group(1))
            if kind in ("input", "parameter", "state"):
                diags.error(f"{what} assigns to {kind} '{m_.group(1)}'", loc)

    for t in m.transitions:
        check_expr(t.guard, f"guard of {t.source} → {t.target}", t.loc)
        check_actions(t.action, f"action of {t.source} → {t.target}", t.loc)
    for s in m.states:
        for attr in ("entry", "during", "exit"):
            check_actions(getattr(s, attr), f"{attr} action of '{s.name}'", s.loc)


def check_sym_references(doctree: nodes.document, comp: Component, path: str,
                         diags: Diagnostics) -> None:
    """Every `name` written with the default `sym` role must be declared."""
    names = comp.names()
    for lit in doctree.findall(nodes.literal):
        if SYM_CLASS in lit.get("classes", []):
            name = lit.astext().strip()
            base = re.split(r"[.\[]", name, maxsplit=1)[0]
            if base not in names:
                diags.warning(f"`{name}` does not name anything declared in '{comp.name}'",
                              Loc(path, _line(lit)))


def check_all(result) -> Diagnostics:
    """Run every check over a ParseResult; returns its (extended) diagnostics."""
    check_component(result.component, result.diagnostics)
    check_sym_references(result.doctree, result.component,
                         result.component.loc.file, result.diagnostics)
    return result.diagnostics
