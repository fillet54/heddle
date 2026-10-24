# Heddle - Document based system

Heddle turns component documents written in reStructuredText into a checked
model and woven output. The document is the source of truth: prose and
declarations live together, and diagrams, tables and (later) code are
generated from it.

```
rst documents ──► front end ──► IR + checks ──► back ends
                 (conventions)  (meaning)      (diagrams, HTML, … C++ later)
```

This first slice covers **state machines**: write one in an ordinary rst
document and get an HTML page with a generated, auto-laid-out diagram, and
C++ for the component generated from the same model.

## Quick start

Requirements: Python 3.10+ and Node.js 18+ (Node runs the layout engine).
Third-party packages: docutils, Jinja2 (which brings MarkupSafe), and elkjs
(no dependencies of its own).

```sh
python -m venv .venv && . .venv/bin/activate
pip install -e .          # installs docutils and jinja2
npm install               # installs elkjs (the Eclipse Layout Kernel)

heddle build examples -o build      # → build/arm_controller.html
heddle build examples -o build --watch   # rebuild on save; the page auto-refreshes
heddle check examples               # diagnostics only, exit 1 on errors
heddle render examples -o build     # one SVG per state machine
heddle ir examples/arm_controller.rst   # the IR as JSON
heddle gen examples -o examples/generated          # C++ from the documents
heddle gen examples -o examples/generated --check  # CI: fail if generated code is stale
python -m unittest                  # tests (compiles the C++ when g++ is present)
```

## Writing a component document

See [`examples/arm_controller.rst`](examples/arm_controller.rst). The
conventions are plain rst; there are no custom directives.

* **The document is the component.** Its title names it; the field list right
  after the title (`:component:`, `:rate:`, `:layer:`) describes it.
* **`.. default-role:: sym`** makes every `` `name` `` in the prose a checked
  reference: it must be declared, and it links to its declaration.
* **Known section titles declare things:** `Inputs`, `Outputs`,
  `Parameters`, `State` and `Procedures`. Other titles are prose; a title that
  looks like a typo of a known one (`Inptus`) gets a warning.
* **Declarations are definition lists:** `name : type`, then the description,
  then an optional field list (`:value:` for parameters, `:reset:` for state
  variables).
* **A state machine** is a subsection of `Procedures` with `States` and
  `Transitions` subsections.
  * States: `name`, a description, optional `:entry:`, `:during:`, `:exit:`.
    The first state is initial unless the machine has `:initial: name`.
  * Transitions: `source → target : priority` (`->` also works), a description
    saying why, then `:guard:` and optional `:action:`. Priority 0 is
    evaluated first. `any` as the source means every state except the target.
  * Optional `:direction: right|down` on the machine section; the default,
    auto, lays out left to right and switches to top-down when too wide.

## Checks

Convention checks (front end): unknown section titles close to known ones,
unknown fields, missing types or priorities, malformed transition terms.

Model checks (IR): unknown states, missing guards, two transitions that can
fire at the same priority out of the same state (including `any`),
unreachable states, dead-end states, undeclared names in guards and actions,
assignments to inputs, parameters or states, states named like ports, and
unresolved `` `name` `` references in prose. Diagnostics print as
`file:line: level: message` and also appear as a banner at the top of the
built page.

## How the diagram is made

1. **Transform** the IR before layout, because most ugly state diagrams come
   from feeding the raw graph to the layout engine:
   * `any → X` becomes one edge from a dashed *any state* group instead of an
     arrow from every state;
   * parallel transitions between the same two states merge into one edge with
     stacked labels;
   * self-transitions move into the state box (`↻ [3] arm_cmd`).
2. **Lay out** with ELK Layered (elkjs): orthogonal routing, and *model order*
   so the order you wrote states and transitions in decides what reads as
   forward; recovery transitions become the back edges. Edges that cross the
   group border are split at a port on the border and joined after layout.
3. **Render** to SVG with its own stylesheet (light and dark), hover
   tooltips with each transition's description, and `data-state` /
   `data-edge` attributes for later overlays such as test coverage.

## Code generation

`heddle gen` follows iMatix GSL's model-oriented programming, in Python:

1. **Models are data.** `heddle/gen/model.py` turns the IR into a generation
   model: C++ names, guards and actions parsed and translated, each state's
   transitions merged with the `any` transitions and sorted by priority, and
   every element's rst line kept for traceability. Templates only print.
2. **One template per output.** `TARGET_SETS["cpp"]` maps
   `cpp/component.hpp.j2` and `cpp/component.cpp.j2` to `<component>.hpp/.cpp`.
   `--templates DIR` puts your own templates in front of the built-in ones,
   so a project can change the output without forking Heddle.
3. **GSL-style templates.** A line starting with `.` is a command
   (`.for state in m.states`, `.if`, `.set`), `.-` is a template comment, and
   everything else is output with `{{ … }}` substitutions, so a template
   reads like the code it produces. Undefined names fail loudly.
4. **Generated code is committed like a lockfile.** Files are written only
   when they change, and `--check` fails when the documents, model or
   templates moved on without regenerating.

Guards and actions use a small expression language (`and`, `or`, `not`,
comparisons, arithmetic, `=`/`+=` assignments; no calls or loops) with a real
parser, so a bad guard is reported against its rst line, not as a compiler
error in generated code.

The generated C++ for the example is in
[`examples/generated`](examples/generated). Each component becomes a class
with `Inputs`, `Outputs`, `Parameters` and state-variable structs, an
`enum class` per state machine, and one entry point, `step(inputs)`, run
once per frame. State machine semantics:

* the constructor and `reset()` enter the initial state (running its entry
  actions);
* each frame, the transitions out of the current state are tried in priority
  order (0 first, `any` transitions included) and at most one fires: exit
  actions, then the transition's action, then the target's entry actions;
* when none fires, the state's `during` actions run;
* a self-transition leaves and re-enters its state.

Every transition is preceded by its description and followed by the rst
line it came from. The code compiles cleanly with
`-Wall -Wextra -Wpedantic -Wshadow -Wconversion -Wsign-conversion -Werror`
on gcc and clang; `tests/cpp/test_arm_controller.cpp` drives a full
scenario against it.

## Layout

```
heddle/
  ir.py            the IR: plain dataclasses with source locations
  frontend.py      rst conventions → IR (docutils)
  checks.py        rule checks over the IR
  diagnostics.py   file:line diagnostics
  build.py         HTML weave (docutils html5 writer)
  heddle.css       page style
  diagram/
    view.py        IR → view graph (the readability transforms)
    elk.py         view graph → ELK JSON → geometry
    elk_layout.cjs runs elkjs under Node
    svg.py         geometry → SVG
  gen/
    expr.py        guard/action language: parser and C++ printer
    types.py       document types → C++ types
    model.py       IR → generation model (denormalise and validate)
    engine.py      targets, GSL-style Jinja environment, writer
    templates/cpp/ component.hpp.j2, component.cpp.j2
examples/          sample component documents
  generated/       C++ generated from them (committed; checked in tests)
tests/             unittest suite
```
