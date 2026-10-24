import unittest
from pathlib import Path

from heddle.checks import check_all
from heddle.frontend import parse_file, parse_text

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "arm_controller.rst"

HEADER = """\
=====
Thing
=====

:component: Thing

.. default-role:: sym

"""


def doc(body: str):
    result = parse_text(HEADER + body, "t.rst")
    check_all(result)
    return result


def messages(result):
    return [d.message for d in result.diagnostics]


class ExampleDocument(unittest.TestCase):
    def setUp(self):
        self.result = parse_file(EXAMPLE)
        check_all(self.result)
        self.comp = self.result.component

    def test_clean(self):
        self.assertEqual([str(d) for d in self.result.diagnostics], [])

    def test_declarations(self):
        self.assertEqual(self.comp.name, "ArmController")
        self.assertEqual(self.comp.fields["rate"], "50 Hz")
        self.assertIn("arm_cmd", [p.name for p in self.comp.inputs])
        self.assertEqual([p.name for p in self.comp.outputs], ["is_armed"])
        self.assertEqual(self.comp.parameters[0].value, "100")
        self.assertEqual(self.comp.state_vars[0].value, "0")

    def test_machine(self):
        m = self.comp.machines[0]
        self.assertEqual(m.name, "Arm Control")
        self.assertEqual(m.initial, "idle")
        self.assertEqual([s.name for s in m.states],
                         ["idle", "testing", "standby", "arming", "armed", "fault"])
        arming = m.state("arming")
        self.assertEqual(arming.during, "arm_timer += 1")
        first = m.transitions[0]
        self.assertEqual((first.source, first.target, first.priority, first.guard),
                         ("idle", "testing", 1, "test_cmd"))
        self.assertTrue(first.loc.line and first.loc.file.endswith("arm_controller.rst"))
        anys = [t for t in m.transitions if t.is_any]
        self.assertEqual([(t.target, t.priority) for t in anys], [("fault", 0)])


MACHINE = """\
Inputs
======

go : bool
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

{transitions}
"""


class Checks(unittest.TestCase):
    def machine(self, transitions: str):
        return doc(MACHINE.format(transitions=transitions))

    def test_ascii_arrow_and_clean(self):
        r = self.machine("a -> b : 1\n   :guard: go\n\nb -> a : 1\n   :guard: not go\n")
        self.assertEqual(messages(r), [])

    def test_unknown_target(self):
        r = self.machine("a -> zz : 1\n   :guard: go\n")
        self.assertTrue(any("target 'zz'" in m for m in messages(r)))

    def test_missing_priority(self):
        r = self.machine("a -> b\n   :guard: go\n")
        self.assertTrue(any("no priority" in m for m in messages(r)))

    def test_priority_clash_with_any(self):
        # `any -> b` also applies out of `a`, where `a -> b` already uses priority 1.
        r = self.machine("a -> b : 1\n   :guard: go\n\nany -> b : 1\n   :guard: go\n")
        self.assertTrue(any("priority 1 out of 'a'" in m for m in messages(r)))
        # `any -> a` does not apply out of `a` itself, so there is no clash.
        r = self.machine("a -> b : 1\n   :guard: go\n\nany -> a : 1\n   :guard: go\n")
        self.assertFalse(any("priority" in m for m in messages(r)))

    def test_undeclared_name_in_guard(self):
        r = self.machine("a -> b : 1\n   :guard: go and nope\n\nb -> a : 1\n   :guard: go\n")
        self.assertTrue(any("'nope'" in m for m in messages(r)))

    def test_assignment_to_input(self):
        r = self.machine("a -> b : 1\n   :guard: go\n   :action: go = false\n\n"
                         "b -> a : 1\n   :guard: go\n")
        self.assertTrue(any("assigns to input 'go'" in m for m in messages(r)))

    def test_unreachable_and_dead_end(self):
        r = self.machine("a -> a : 1\n   :guard: go\n")
        msgs = messages(r)
        self.assertTrue(any("'b' is unreachable" in m for m in msgs))
        self.assertTrue(any("'a' has no transition out" in m for m in msgs))

    def test_section_typo(self):
        r = doc("Inptus\n======\n\nx : bool\n   X.\n")
        self.assertTrue(any("did you mean 'Inputs'" in m for m in messages(r)))

    def test_prose_sections_are_fine(self):
        r = doc("Background\n==========\n\nJust prose.\n")
        self.assertEqual(messages(r), [])

    def test_unresolved_sym(self):
        r = doc("Talks about `ghost`.\n")
        self.assertTrue(any("`ghost`" in m for m in messages(r)))

    def test_state_name_collision(self):
        r = doc(MACHINE.format(transitions="a -> b : 1\n   :guard: go\n\nb -> a : 1\n   :guard: go\n")
                .replace("go : bool", "go : bool\n   Go.\n\nb : bool"))
        self.assertTrue(any("same name as input 'b'" in m for m in messages(r)))


if __name__ == "__main__":
    unittest.main()
