"""The Heddle intermediate representation (IR).

The IR is the hub between the document front end and every back end.
It is plain, fully resolved data: generators never see docutils nodes,
and every node carries the source location it came from so that rule
failures and generated output can point back at the exact rst line.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class Loc:
    """A position in an rst source file (1-based line)."""

    file: str
    line: int | None = None

    def __str__(self) -> str:
        return f"{self.file}:{self.line}" if self.line else self.file


@dataclass
class Port:
    name: str
    type: str
    description: str
    loc: Loc


@dataclass
class Variable:
    """A parameter or a state variable."""

    name: str
    type: str
    description: str
    loc: Loc
    value: str | None = None  # parameter value or state reset value


@dataclass
class State:
    name: str
    description: str
    loc: Loc
    entry: str | None = None
    during: str | None = None
    exit: str | None = None


ANY = "any"  # reserved source name: "from every state except the target"


@dataclass
class Transition:
    source: str  # a state name, or ANY
    target: str
    priority: int
    guard: str
    description: str
    loc: Loc
    action: str | None = None

    @property
    def is_any(self) -> bool:
        return self.source == ANY

    @property
    def is_self(self) -> bool:
        return self.source == self.target


@dataclass
class StateMachine:
    name: str
    description: str
    initial: str
    states: list[State]
    transitions: list[Transition]
    loc: Loc
    direction: str | None = None  # diagram flow: "right" or "down"

    def state(self, name: str) -> State | None:
        return next((s for s in self.states if s.name == name), None)


@dataclass
class Component:
    name: str
    title: str
    loc: Loc
    fields: dict[str, str] = field(default_factory=dict)
    description: str = ""  # the prose between the title and the first section
    inputs: list[Port] = field(default_factory=list)
    outputs: list[Port] = field(default_factory=list)
    parameters: list[Variable] = field(default_factory=list)
    state_vars: list[Variable] = field(default_factory=list)
    machines: list[StateMachine] = field(default_factory=list)

    def names(self) -> dict[str, str]:
        """Every declared name in this component, mapped to its kind."""
        out: dict[str, str] = {}
        for p in self.inputs:
            out[p.name] = "input"
        for p in self.outputs:
            out[p.name] = "output"
        for v in self.parameters:
            out[v.name] = "parameter"
        for v in self.state_vars:
            out[v.name] = "state variable"
        for m in self.machines:
            for s in m.states:
                out.setdefault(s.name, "state")
        return out

    def to_dict(self) -> dict:
        return asdict(self)
