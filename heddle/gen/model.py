"""IR → generation model: plain Python data, ready for templates.

This is the "denormalise and validate" pass of model-oriented programming:
everything a template needs is computed here, once, so templates only print.
In particular:

* every name has its target-language spelling (enum values, members);
* guards and actions are parsed and translated, with names resolved to
  where they live (``in.x``, ``out_.x``, ``params_.X``, ``vars_.x``);
* each state carries its own priority-ordered transition list, with the
  ``any`` transitions merged in, so the template never re-derives semantics;
* every element keeps its rst source location for traceability comments.

Problems found here are reported as diagnostics against the rst line, the
same way front-end and check problems are.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..diagnostics import Diagnostics
from ..ir import Component, Loc, StateMachine
from . import expr
from .types import CppTypes

CPP_KEYWORDS = set("""
alignas alignof and and_eq asm auto bitand bitor bool break case catch char char8_t char16_t
char32_t class compl concept const consteval constexpr constinit const_cast continue co_await
co_return co_yield decltype default delete do double dynamic_cast else enum explicit export
extern false float for friend goto if inline int long mutable namespace new noexcept not not_eq
nullptr operator or or_eq private protected public register reinterpret_cast requires return
short signed sizeof static static_assert static_cast struct switch template this thread_local
throw true try typedef typeid typename union unsigned using virtual void volatile wchar_t while
xor xor_eq
""".split())


def snake(text: str) -> str:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower()


def pascal(text: str) -> str:
    return "".join(p[:1].upper() + p[1:] for p in re.split(r"[^A-Za-z0-9]+", text) if p)


def cpp_ident(name: str) -> str:
    return name + "_" if name in CPP_KEYWORDS else name


def short_loc(loc: Loc, root: Path | None = None) -> str:
    path = Path(loc.file)
    if root is not None:
        try:
            path = path.resolve().relative_to(root.resolve())
        except ValueError:
            pass
    return f"{path.as_posix()}:{loc.line}" if loc.line else path.as_posix()


@dataclass
class GVar:
    name: str            # name in the document
    member: str          # C++ member name
    cpp_type: str
    init: str            # C++ initialiser
    description: str
    source: str


@dataclass
class GTransition:
    source: str          # the state it was written from, or "any"
    target: str
    target_enum: str
    priority: int
    guard: str           # the guard as written
    guard_cpp: str
    actions_cpp: list[str]
    description: str
    origin: str          # rst location
    from_any: bool
    target_has_entry: bool = False


@dataclass
class GState:
    name: str
    enum: str
    description: str
    origin: str
    entry_cpp: list[str] = field(default_factory=list)
    during_cpp: list[str] = field(default_factory=list)
    exit_cpp: list[str] = field(default_factory=list)
    transitions: list[GTransition] = field(default_factory=list)


@dataclass
class GMachine:
    name: str            # title in the document
    ident: str           # snake_case, used for member functions
    enum_type: str       # e.g. ArmControlState
    member: str          # current-state member, e.g. arm_control_
    description: str
    origin: str
    initial: GState
    states: list[GState]
    has_entry: bool
    has_exit: bool
    reads_inputs: bool


@dataclass
class GComponent:
    name: str            # C++ class name
    snake: str           # file stem
    title: str
    description: str
    fields: dict[str, str]
    source: str          # rst path as given
    namespace: str
    inputs: list[GVar]
    outputs: list[GVar]
    parameters: list[GVar]
    state_vars: list[GVar]
    machines: list[GMachine]

    @property
    def facts(self) -> list[tuple[str, str]]:
        """Document fields worth repeating in the class comment."""
        return [(k, v) for k, v in self.fields.items() if k != "component"]

    @property
    def guard_macro(self) -> str:
        return f"{self.namespace.upper().replace('::', '_')}_{self.snake.upper()}_HPP"


class _Builder:
    def __init__(self, comp: Component, diags: Diagnostics, types: CppTypes,
                 namespace: str, root: Path | None):
        self.comp = comp
        self.diags = diags
        self.types = types
        self.namespace = namespace
        self.root = root
        self.kinds = {}
        for p in comp.inputs:
            self.kinds[p.name] = ("input", f"in.{cpp_ident(p.name)}")
        for p in comp.outputs:
            self.kinds[p.name] = ("output", f"out_.{cpp_ident(p.name)}")
        for v in comp.parameters:
            self.kinds[v.name] = ("parameter", f"params_.{cpp_ident(v.name)}")
        for v in comp.state_vars:
            self.kinds[v.name] = ("state variable", f"vars_.{cpp_ident(v.name)}")

    def origin(self, loc: Loc) -> str:
        return short_loc(loc, self.root)

    # -- names ---------------------------------------------------------------

    def resolver(self, loc: Loc, what: str, writable: bool = False):
        def resolve(name: str) -> str:
            kind = self.kinds.get(name)
            if kind is None:
                raise expr.ExprError(f"'{name}' in {what} is not an input, output, "
                                     f"parameter or state variable")
            if writable and kind[0] not in ("output", "state variable"):
                raise expr.ExprError(f"{what} cannot assign to {kind[0]} '{name}'")
            return kind[1]
        return resolve

    def guard(self, text: str, loc: Loc, what: str) -> str:
        try:
            return expr.to_cpp(expr.parse_guard(text), self.resolver(loc, what))
        except expr.ExprError as exc:
            self.diags.error(f"{what}: {exc}", loc)
            return "false"

    def actions(self, text: str | None, loc: Loc, what: str) -> list[str]:
        try:
            out = []
            for a in expr.parse_actions(text):
                read = self.resolver(loc, what)
                target = self.resolver(loc, what, writable=True)(a.target)
                out.append(f"{target} {a.op} {expr.to_cpp(a.value, read)};")
            return out
        except expr.ExprError as exc:
            self.diags.error(f"{what}: {exc}", loc)
            return []

    def literal(self, text: str | None, cpp_type: str, loc: Loc, what: str) -> str:
        if not text:
            return self.types.default(cpp_type)

        def no_names(name: str) -> str:
            raise expr.ExprError(f"{what} must be a literal, not '{name}'")

        try:
            return expr.to_cpp(expr.parse_guard(text), no_names)
        except expr.ExprError as exc:
            self.diags.error(f"{what}: {exc}", loc)
            return self.types.default(cpp_type)

    # -- declarations ---------------------------------------------------------

    def var(self, decl, kind: str, value: str | None = None) -> GVar:
        cpp_type = self.types.lookup(decl.type)
        if cpp_type is None:
            self.diags.error(
                f"{kind} '{decl.name}' has type '{decl.type}', which has no C++ mapping "
                f"(known: {', '.join(self.types.names())})", decl.loc)
            cpp_type = "int"
        init = self.literal(value, cpp_type, decl.loc, f"value of {kind} '{decl.name}'")
        return GVar(decl.name, cpp_ident(decl.name), cpp_type, init, decl.description,
                    self.origin(decl.loc))

    # -- state machines --------------------------------------------------------

    def machine(self, m: StateMachine) -> GMachine:
        ident = snake(m.name)
        enum_type = pascal(m.name) + "State"
        states = {}
        for s in m.states:
            g = GState(s.name, cpp_ident(s.name), s.description, self.origin(s.loc))
            g.entry_cpp = self.actions(s.entry, s.loc, f"entry action of '{s.name}'")
            g.during_cpp = self.actions(s.during, s.loc, f"during action of '{s.name}'")
            g.exit_cpp = self.actions(s.exit, s.loc, f"exit action of '{s.name}'")
            states[s.name] = g

        reads_inputs = False
        for t in m.transitions:
            if t.target not in states or (not t.is_any and t.source not in states):
                continue  # already reported by the checks
            what = f"guard of {t.source} → {t.target}"
            guard_cpp = self.guard(t.guard, t.loc, what)
            reads_inputs |= "in." in guard_cpp
            gt = GTransition(t.source, t.target, states[t.target].enum, t.priority, t.guard,
                             guard_cpp,
                             self.actions(t.action, t.loc, f"action of {t.source} → {t.target}"),
                             t.description, self.origin(t.loc), t.is_any)
            sources = [s for s in states if s != t.target] if t.is_any else [t.source]
            for src in sources:
                states[src].transitions.append(gt)

        for g in states.values():
            for t in g.transitions:
                t.target_has_entry = bool(states[t.target].entry_cpp)
            # Priority order; ties are rejected by the checks, so authored order
            # only matters for well-formed but equal priorities in bad input.
            g.transitions.sort(key=lambda t: t.priority)
            reads_inputs |= any("in." in line for line in g.during_cpp + g.entry_cpp)

        ordered = [states[s.name] for s in m.states]
        initial = states.get(m.initial, ordered[0])
        return GMachine(m.name, ident, enum_type, ident + "_", m.description,
                        self.origin(m.loc), initial, ordered,
                        any(s.entry_cpp for s in ordered), any(s.exit_cpp for s in ordered),
                        reads_inputs)

    def component(self) -> GComponent:
        c = self.comp
        return GComponent(
            name=pascal(c.name) if not re.match(r"^[A-Z][A-Za-z0-9]*$", c.name) else c.name,
            snake=snake(c.name),
            title=c.title,
            description=c.description,
            fields=dict(c.fields),
            source=short_loc(Loc(c.loc.file), self.root),
            namespace=self.namespace,
            inputs=[self.var(p, "input") for p in c.inputs],
            outputs=[self.var(p, "output") for p in c.outputs],
            parameters=[self.var(v, "parameter", v.value) for v in c.parameters],
            state_vars=[self.var(v, "state variable", v.value) for v in c.state_vars],
            machines=[self.machine(m) for m in c.machines],
        )


def build_model(comp: Component, diags: Diagnostics, namespace: str = "components",
                types: CppTypes | None = None, root: Path | None = None) -> GComponent:
    """Derive the generation model for one component."""
    return _Builder(comp, diags, types or CppTypes(), namespace, root).component()
