import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from heddle.checks import check_all
from heddle.frontend import parse_file, parse_text
from heddle.gen import build_model, generate, write
from heddle.gen import expr

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "arm_controller.rst"
GENERATED = ROOT / "examples" / "generated"
SCENARIO = ROOT / "tests" / "cpp" / "test_arm_controller.cpp"


def ident(name: str) -> str:
    return name


class Expressions(unittest.TestCase):
    def cpp(self, text: str) -> str:
        return expr.to_cpp(expr.parse_guard(text), ident)

    def test_operators_and_precedence(self):
        self.assertEqual(self.cpp("a and b"), "a && b")
        self.assertEqual(self.cpp("not (a == b)"), "!(a == b)")
        self.assertEqual(self.cpp("a or b and c"), "a || (b && c)")
        self.assertEqual(self.cpp("(a or b) and c"), "(a || b) && c")
        self.assertEqual(self.cpp("a - (b - c)"), "a - (b - c)")
        self.assertEqual(self.cpp("a - b - c"), "a - b - c")
        self.assertEqual(self.cpp("-(a + b) < 3"), "-(a + b) < 3")
        self.assertEqual(self.cpp("x >= LIMIT and not y"), "x >= LIMIT && !y")

    def test_actions(self):
        acts = expr.parse_actions("t = 0; t += 1")
        self.assertEqual([expr.assign_to_cpp(a, ident) for a in acts], ["t = 0;", "t += 1;"])

    def test_rejects(self):
        for bad in ["a ==", "a < b < c", "a $ b", "f(x)", "a b", "(a"]:
            with self.assertRaises(expr.ExprError, msg=bad):
                expr.parse_guard(bad)
        with self.assertRaises(expr.ExprError):
            expr.parse_actions("a + 1")


class Model(unittest.TestCase):
    def setUp(self):
        result = parse_file(EXAMPLE)
        check_all(result)
        self.diags = result.diagnostics
        self.model = build_model(result.component, self.diags, root=ROOT)
        self.machine = self.model.machines[0]

    def state(self, name):
        return next(s for s in self.machine.states if s.name == name)

    def test_clean(self):
        self.assertEqual([str(d) for d in self.diags], [])

    def test_names_resolve_to_where_they_live(self):
        arming = self.state("arming")
        guards = [t.guard_cpp for t in arming.transitions]
        self.assertIn("vars_.arm_timer >= params_.ARM_DELAY", guards)
        self.assertEqual(arming.during_cpp, ["vars_.arm_timer += 1;"])
        self.assertEqual(self.state("armed").entry_cpp, ["out_.is_armed = true;"])

    def test_any_transitions_merge_in_priority_order(self):
        idle = self.state("idle")
        self.assertEqual([(t.target, t.priority, t.from_any) for t in idle.transitions],
                         [("fault", 0, True), ("testing", 1, False)])
        self.assertFalse(any(t.target == "fault" for t in self.state("fault").transitions))

    def test_traceability(self):
        t = self.state("idle").transitions[1]
        self.assertEqual(t.origin, "examples/arm_controller.rst:107")

    def test_parameter_value(self):
        self.assertEqual(self.model.parameters[0].init, "100")
        self.assertEqual(self.model.parameters[0].cpp_type, "std::uint32_t")


DOC = """\
=====
Thing
=====

:component: Thing

Inputs
======

go : {type}
   Go.

Procedures
==========

M
-

States
^^^^^^

a
   A.

b
   B.

Transitions
^^^^^^^^^^^

a -> b : 1
   :guard: {guard}

b -> a : 1
   :guard: go
"""


class GenerationErrors(unittest.TestCase):
    def gen(self, type_="bool", guard="go"):
        result = parse_text(DOC.format(type=type_, guard=guard), "t.rst")
        check_all(result)
        return generate(result)

    def test_unknown_type(self):
        outputs, diags = self.gen(type_="mystery")
        self.assertEqual(outputs, [])
        self.assertTrue(any("no C++ mapping" in d.message for d in diags.errors))

    def test_guard_cannot_name_a_state(self):
        outputs, diags = self.gen(guard="go and b")
        self.assertEqual(outputs, [])
        self.assertTrue(any("'b'" in d.message for d in diags.errors))

    def test_bad_expression_is_reported_against_the_rst_line(self):
        outputs, diags = self.gen(guard="go and")
        self.assertEqual(outputs, [])
        self.assertTrue(any(d.loc.line for d in diags.errors))


class GeneratedCode(unittest.TestCase):
    def outputs(self):
        result = parse_file(EXAMPLE)
        check_all(result)
        outputs, diags = generate(result, root=ROOT)
        self.assertEqual([str(d) for d in diags], [])
        return outputs

    def test_committed_output_is_up_to_date(self):
        # Generated files are committed like a lockfile; this fails when the
        # document, model or templates change without `heddle gen`.
        results = write(self.outputs(), GENERATED, check=True)
        stale = [str(r.path) for r in results if r.status != "unchanged"]
        self.assertEqual(stale, [], "run: heddle gen examples -o examples/generated")

    def test_output_is_deterministic(self):
        self.assertEqual([o.text for o in self.outputs()], [o.text for o in self.outputs()])

    @unittest.skipUnless(shutil.which("g++"), "needs g++")
    def test_compiles_cleanly_and_scenario_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            write(self.outputs(), tmp)
            exe = tmp / "scenario"
            build = subprocess.run(
                ["g++", "-std=c++17", "-Wall", "-Wextra", "-Wpedantic", "-Wshadow",
                 "-Wconversion", "-Wsign-conversion", "-Werror", f"-I{tmp}",
                 str(tmp / "arm_controller.cpp"), str(SCENARIO), "-o", str(exe)],
                capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([str(exe)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


if __name__ == "__main__":
    unittest.main()
