"""Hardening from the independent review: fsync, junctions, case-insensitive directory names."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import omniagent
from omniagent import SafetyError


def make_skill(root: Path, name: str) -> Path:
    skill = root / name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(f"---\nname: {name}\n---\nbody\n", encoding="utf-8")
    return skill


def make_junction(link: Path, target: Path) -> bool:
    """Create a Windows directory junction; False when the platform or sandbox cannot."""
    if os.name != "nt":
        return False
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                            capture_output=True, text=True)
    return result.returncode == 0


class HardeningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def test_save_json_syncs_to_disk_before_replacing(self):
        calls = []
        real_fsync, real_replace = os.fsync, os.replace
        with mock.patch.object(omniagent.os, "fsync", side_effect=lambda fd: (calls.append("fsync"), real_fsync(fd))[1]), \
                mock.patch.object(omniagent.os, "replace", side_effect=lambda a, b: (calls.append("replace"), real_replace(a, b))[1]):
            omniagent.save_json(self.base / "state" / "x.json", {"a": 1})
        self.assertEqual(calls, ["fsync", "replace"])

    def test_node_modules_is_found_regardless_of_case(self):
        skill = make_skill(self.base, "alpha")
        (skill / "Node_Modules").mkdir()
        self.assertTrue(omniagent.has_vendor(skill))

    def test_runtime_dir_with_different_case_does_not_change_installed_digest(self):
        skill = make_skill(self.base, "alpha")
        before = omniagent.tree_digest(skill, source=False)
        (skill / "__Pycache__").mkdir()
        (skill / "__Pycache__" / "x.cpython-313.pyc").write_bytes(b"\0")
        self.assertEqual(omniagent.tree_digest(skill, source=False), before)

    def test_git_dir_with_different_case_is_not_copied_from_source(self):
        skill = make_skill(self.base, "alpha")
        (skill / ".GIT").mkdir()
        (skill / ".GIT" / "config").write_text("x", encoding="utf-8")
        files = [rel for rel, _ in omniagent.skill_files(skill)]
        self.assertEqual(files, ["SKILL.md"])

    def test_junction_is_detected_and_refused(self):
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("private", encoding="utf-8")
        skill = make_skill(self.base, "alpha")
        link = skill / "linked"
        if not make_junction(link, outside):
            self.skipTest("cannot create a junction on this platform")
        self.assertTrue(omniagent.is_link(link))
        self.assertFalse(omniagent.is_link(skill))
        with self.assertRaises(SafetyError):
            omniagent.skill_files(skill)


if __name__ == "__main__":
    unittest.main()
