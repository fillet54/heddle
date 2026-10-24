"""Command line: ``python -m heddle <command> ...``.

  build   rst → HTML pages with generated state machine diagrams
  check   run the front end and rule checks only
  render  write one SVG per state machine
  ir      print the IR as JSON
  gen     generate code (C++) from component documents
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .build import build_html
from .checks import check_all
from .diagnostics import Diagnostics
from .diagram import LayoutError, diagram_svg
from .frontend import parse_file


def _report(diags: Diagnostics) -> int:
    for d in diags.sorted():
        print(d, file=sys.stderr)
    return 1 if diags.errors else 0


def _sources(paths: list[str]) -> list[Path]:
    out: list[Path] = []
    for p in map(Path, paths):
        out += sorted(p.rglob("*.rst")) if p.is_dir() else [p]
    return out


def cmd_build(args) -> int:
    outdir = Path(args.output)

    def build_all() -> int:
        status = 0
        for src in _sources(args.sources):
            target = outdir / (src.stem + ".html")
            diags = build_html(src, target, args.direction, refresh=2 if args.watch else None)
            status |= _report(diags)
            n_err, n_warn = len(diags.errors), len(diags.warnings)
            print(f"built {target}  ({n_err} errors, {n_warn} warnings)", file=sys.stderr)
        return status

    status = build_all()
    if not args.watch:
        return status

    print("watching for changes; open the HTML file in a browser (it reloads every 2 s). "
          "Ctrl-C to stop.", file=sys.stderr)

    def stamp():
        return {p: p.stat().st_mtime for p in _sources(args.sources)}

    last = stamp()
    try:
        while True:
            time.sleep(0.5)
            now = stamp()
            if now != last:
                last = now
                build_all()
    except KeyboardInterrupt:
        return 0


def cmd_check(args) -> int:
    status = 0
    for src in _sources(args.sources):
        status |= _report(check_all(parse_file(src)))
    return status


def cmd_render(args) -> int:
    outdir = Path(args.output)
    outdir.mkdir(parents=True, exist_ok=True)
    status = 0
    for src in _sources(args.sources):
        result = parse_file(src)
        status |= _report(check_all(result))
        for m in result.component.machines:
            target = outdir / f"{src.stem}.{m.name.lower().replace(' ', '_')}.svg"
            try:
                target.write_text(diagram_svg(m, args.direction), encoding="utf-8")
                print(f"wrote {target}", file=sys.stderr)
            except LayoutError as exc:
                print(f"{m.loc}: error: diagram failed: {exc}", file=sys.stderr)
                status = 1
    return status


def cmd_gen(args) -> int:
    from .gen import generate, write  # needs jinja2; keep other commands free of it

    outdir = Path(args.output)
    root = Path.cwd()
    status = 0
    for src in _sources(args.sources):
        result = parse_file(src)
        check_all(result)
        outputs, diags = generate(result, args.target, args.namespace,
                                  [Path(d) for d in args.templates], root)
        status |= _report(diags)
        if diags.errors:
            print(f"{src}: not generated (fix the errors above)", file=sys.stderr)
            continue
        for res in write(outputs, outdir, check=args.check):
            if res.status == "stale":
                status = 1
                print(f"{res.path}: out of date with {src}; run `heddle gen`", file=sys.stderr)
            elif res.status == "written":
                print(f"wrote {res.path}", file=sys.stderr)
            elif args.verbose:
                print(f"unchanged {res.path}", file=sys.stderr)
    return status


def cmd_ir(args) -> int:
    result = parse_file(args.source)
    status = _report(check_all(result))
    json.dump(result.component.to_dict(), sys.stdout, indent=2)
    print()
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="heddle", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    def direction(p):
        p.add_argument("--direction", choices=["auto", "right", "down"], default=None,
                       help="diagram flow (default: the machine's :direction:, else auto)")

    p = sub.add_parser("build", help="build HTML pages")
    p.add_argument("sources", nargs="+", help="rst files or directories")
    p.add_argument("-o", "--output", default="build", help="output directory (default: build)")
    p.add_argument("--watch", action="store_true", help="rebuild whenever a source changes")
    direction(p)
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("check", help="run checks only")
    p.add_argument("sources", nargs="+")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("render", help="write one SVG per state machine")
    p.add_argument("sources", nargs="+")
    p.add_argument("-o", "--output", default="build")
    direction(p)
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("gen", help="generate code from component documents")
    p.add_argument("sources", nargs="+", help="rst files or directories")
    p.add_argument("-o", "--output", default="generated", help="output directory")
    p.add_argument("--target", default="cpp", help="target set (default: cpp)")
    p.add_argument("--namespace", default="components", help="C++ namespace")
    p.add_argument("--templates", action="append", default=[], metavar="DIR",
                   help="directory searched before the built-in templates (repeatable)")
    p.add_argument("--check", action="store_true",
                   help="write nothing; exit 1 if any generated file is out of date")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=cmd_gen)

    p = sub.add_parser("ir", help="print the IR as JSON")
    p.add_argument("source")
    p.set_defaults(func=cmd_ir)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
