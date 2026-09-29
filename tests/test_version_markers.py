"""版の注釈 (skills / agents) が plugin.json の version と揃っていること (bump_version.py --check)。

  python -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "sweepline" / "scripts"))
import bump_version as bv  # noqa: E402


class VersionMarkerTest(unittest.TestCase):
    def test_全文書の注釈が_plugin_json_と揃っている(self):
        self.assertEqual(bv.check(), [])

    def test_文書は_skills_と_agents_の全部(self):
        names = sorted(p.relative_to(bv.PLUGIN).as_posix() for p in bv.documents())
        self.assertIn("skills/impl/SKILL.md", names)
        self.assertIn("skills/sweep/SKILL.md", names)
        self.assertIn("skills/fix/SKILL.md", names)
        self.assertIn("agents/implementer.md", names)
        self.assertEqual(len([n for n in names if n.startswith("skills/")]), 7)

    def test_注釈の形(self):
        self.assertEqual(bv.MARKER_RE.findall("x <!-- sweepline skill version: 0.5.0 --> y"), ["0.5.0"])
        self.assertEqual(bv.MARKER_RE.findall("<!-- sweepline:plan -->"), [])


if __name__ == "__main__":
    unittest.main()
