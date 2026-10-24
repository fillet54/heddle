"""The code generation engine, in the spirit of iMatix GSL.

GSL's ideas, carried over:

* **models are data**: generators read the generation model (Python data
  built from the IR), never the rst;
* **two passes**: load, denormalise and validate the whole model first
  (``model.py``), then generate;
* **one template per output** ("targets"): adding an output never touches
  existing ones;
* **template mode with dot commands**: a line starting with ``.`` is a
  command (``.for``, ``.if``, ``.set``...), everything else is output text, so
  a template reads like the code it produces. ``.-`` starts a comment line.

Templates are Jinja2. Undefined names fail loudly, and output is written
only when it changes, so generated files can be committed and checked like
a lockfile (``heddle gen --check``).
"""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass
from pathlib import Path

import jinja2

from .model import GComponent, pascal, snake

TEMPLATES = Path(__file__).with_name("templates")


@dataclass(frozen=True)
class Target:
    """One output file produced by one template."""

    name: str
    template: str
    output: str  # file name pattern; {snake} and {name} come from the component


TARGET_SETS: dict[str, list[Target]] = {
    "cpp": [
        Target("header", "cpp/component.hpp.j2", "{snake}.hpp"),
        Target("source", "cpp/component.cpp.j2", "{snake}.cpp"),
    ],
}


def _doc(text: str, prefix: str = "/// ", width: int = 88) -> str:
    """Wrap prose into comment lines (no trailing newline)."""
    text = " ".join((text or "").split())
    if not text:
        return prefix.rstrip()
    return "\n".join(textwrap.wrap(text, width=width - len(prefix), initial_indent=prefix,
                                   subsequent_indent=prefix, break_long_words=False,
                                   break_on_hyphens=False))


DOT_COMMAND = re.compile(r"^([ \t]*)\.([A-Za-z].*?)[ \t]*$")


def dot_commands(source: str) -> str:
    """Translate GSL-style dot lines into Jinja tags.

    ``.for x in xs`` becomes ``{% for x in xs %}`` and ``.- text`` is dropped.
    With ``trim_blocks`` and ``lstrip_blocks`` a tag that fills its line
    vanishes together with that line, and blank lines around it survive (which
    Jinja's own line statements do not guarantee).
    """
    out = []
    for line in source.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped.startswith(".-"):
            continue
        m = DOT_COMMAND.match(line.rstrip("\r\n"))
        if m:
            newline = line[len(line.rstrip("\r\n")):]
            out.append(f"{m.group(1)}{{% {m.group(2)} %}}{newline}")
        else:
            out.append(line)
    return "".join(out)


class _Loader(jinja2.FileSystemLoader):
    """File loader that applies GSL-style dot commands."""

    def get_source(self, environment, template):
        source, filename, uptodate = super().get_source(environment, template)
        return dot_commands(source), filename, uptodate


def environment(extra_dirs: list[Path] | None = None) -> jinja2.Environment:
    """A Jinja environment configured for GSL-style templates.

    Directories in ``extra_dirs`` are searched before the built-in templates,
    so a project can override any template without forking Heddle.
    """
    dirs = [str(d) for d in (extra_dirs or [])] + [str(TEMPLATES)]
    env = jinja2.Environment(
        loader=_Loader(dirs),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        undefined=jinja2.StrictUndefined,
        autoescape=False,
    )
    env.filters.update(doc=_doc, snake=snake, pascal=pascal)
    return env


@dataclass
class Output:
    path: Path
    text: str


def render(model: GComponent, target_set: str = "cpp",
           template_dirs: list[Path] | None = None) -> list[Output]:
    env = environment(template_dirs)
    outputs = []
    for target in TARGET_SETS[target_set]:
        template = env.get_template(target.template)
        text = template.render(c=model, component=model, target=target)
        name = target.output.format(snake=model.snake, name=model.name)
        outputs.append(Output(Path(name), text))
    return outputs


@dataclass
class WriteResult:
    path: Path
    status: str  # "written" | "unchanged" | "stale" (check mode)


def write(outputs: list[Output], outdir: Path, check: bool = False) -> list[WriteResult]:
    """Write outputs under ``outdir``; with ``check``, only compare."""
    results = []
    for out in outputs:
        path = outdir / out.path
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current == out.text:
            results.append(WriteResult(path, "unchanged"))
        elif check:
            results.append(WriteResult(path, "stale"))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(out.text, encoding="utf-8")
            results.append(WriteResult(path, "written"))
    return results
