import shutil
import subprocess
import unittest
from pathlib import Path

from heddle.diagram import build_view, to_elk
from heddle.frontend import parse_file

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "arm_controller.rst"


def elk_available() -> bool:
    if not shutil.which("node"):
        return False
    probe = subprocess.run(["node", "-e", "require('elkjs/lib/elk.bundled.js')"],
                           cwd=ROOT / "heddle" / "diagram", capture_output=True)
    return probe.returncode == 0


class ViewTransforms(unittest.TestCase):
    def setUp(self):
        self.machine = parse_file(EXAMPLE).component.machines[0]
        self.view = build_view(self.machine)

    def test_any_transitions_collapse_to_one_group_edge(self):
        anys = [e for e in self.view.edges if e.kind == "any"]
        self.assertEqual(len(anys), 1)
        self.assertEqual(anys[0].source, "g_any")
        group = self.view.groups[0]
        self.assertNotIn("s_fault", group.children)
        self.assertIn("s_idle", group.children)

    def test_parallel_transitions_merge(self):
        edges = [e for e in self.view.edges if (e.source, e.target) == ("s_armed", "s_standby")]
        self.assertEqual(len(edges), 1)
        text = [ln.text for ln in edges[0].lines]
        self.assertEqual(text, ["[1] disarm_cmd", "[2] release_done"])

    def test_self_loops_move_into_the_box(self):
        self.assertFalse(any(e.source == e.target for e in self.view.edges))
        arming = next(n for n in self.view.nodes if n.id == "s_arming")
        self.assertTrue(any("↻ [3] arm_cmd" in ln.text for ln in arming.lines))

    def test_model_order(self):
        ids = [n.id for n in self.view.nodes]
        self.assertEqual(ids[:3], ["initial", "s_idle", "s_testing"])

    def test_group_crossing_edges_are_split_at_ports(self):
        graph = to_elk(self.view)
        ids = {e["id"] for e in graph["edges"]}
        group = next(c for c in graph["children"] if c["id"] == "g_any")
        self.assertTrue(any(i.endswith("__outer") for i in ids))
        self.assertTrue(any(e["id"].endswith("__inner") for e in group["edges"]))
        self.assertTrue(group["ports"])


@unittest.skipUnless(elk_available(), "needs node and `npm install` (elkjs)")
class Rendering(unittest.TestCase):
    def test_svg(self):
        from heddle.diagram import diagram_svg
        m = parse_file(EXAMPLE).component.machines[0]
        for direction in ("right", "down", "auto"):
            svg = diagram_svg(m, direction)
            self.assertTrue(svg.startswith("<svg"))
            for state in ("idle", "testing", "standby", "arming", "armed", "fault"):
                self.assertIn(f'data-state="{state}"', svg)
            self.assertIn("fault_detected", svg)
            self.assertNotIn('d=""', svg)  # every edge got a route


if __name__ == "__main__":
    unittest.main()
