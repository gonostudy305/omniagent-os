"""Issue #17: a real process death while `apply` is undoing itself after an exception.

The child makes `apply` fail once every skill is replaced (the journal write that would
start the commit, or the state write), so its `except Exception` cleanup runs, and then
dies with os._exit at a chosen point of that cleanup. Fixtures are temp folders only.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "omniagent.py"

CHILD = """
import os, shutil, sys
sys.path.insert(0, {root!r})
from pathlib import Path
import omniagent

fail, kill = os.environ["FAIL_AT"], os.environ["KILL_AT"]
armed = {{"on": False}}
real_save, real_rename, real_tree, real_rmtree = omniagent.save_json, Path.rename, shutil.copytree, shutil.rmtree

def die():
    os._exit(137)

def save(path, data):
    path = Path(path)
    if ((fail == "commit" and path.parent.name == "transactions" and data.get("status") == "committing")
            or (fail == "state" and path.name == "state.json")):
        armed["on"] = True
        if os.environ.get("EDIT_FIRST"):
            (Path(os.environ["EDIT_FIRST"]) / "SKILL.md").write_text("hand edit", encoding="utf-8")
        raise omniagent.SafetyError("injected failure")
    return real_save(path, data)
omniagent.save_json = save

def rmtree(path, *a, **k):
    if armed["on"] and kill == "rmtree":
        next(p for p in Path(path).rglob("*") if p.is_file()).unlink()
        die()
    return real_rmtree(path, *a, **k)
shutil.rmtree = rmtree

def copytree(src, dst, *a, **k):
    result = real_tree(src, dst, *a, **k)
    if armed["on"] and kill == "copy":
        (Path(dst) / "SKILL.md").unlink()
        die()
    return result
shutil.copytree = copytree

def rename(self, target):
    moved = real_rename(self, target)
    if armed["on"] and kill == "old_moved" and Path(target).name == "old":
        die()
    if armed["on"] and kill == "swapped_in" and self.name == "skill" and self.parent.name.startswith(".omniagent-stage-"):
        die()
    return moved
Path.rename = rename
sys.exit(omniagent.cli(sys.argv[1:]))
"""


def make_skill(root: Path, name: str, text: str) -> Path:
    skill = root / name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(f"---\nname: {name}\n---\n{text}\n", encoding="utf-8")
    (skill / "notes.txt").write_text("x" * 5000, encoding="utf-8")
    return skill


class ApplyCleanupKillTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.source = base / "ssot"
        self.source.mkdir()
        self.target = base / "agent" / "skills"
        self.state = base / "state"

    def run_cli(self, *args, env=None, expected=0):
        if env:
            cmd = [sys.executable, "-I", "-c", CHILD.format(root=str(ROOT)), *args]
            env = {**os.environ, **env}
        else:
            cmd = [sys.executable, "-I", str(SCRIPT), *args]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=env)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def apply(self, env=None, expected=0):
        return self.run_cli("apply", "--source", str(self.source), "--state-dir", str(self.state),
                            "--target", f"claude={self.target}", "--yes", env=env, expected=expected)

    def recover(self, expected=0):
        return self.run_cli("recover", "--state-dir", str(self.state), "--yes", expected=expected)

    def journal_status(self):
        files = sorted((self.state / "transactions").glob("*.json"), key=lambda f: f.stat().st_mtime_ns)
        return json.loads(files[-1].read_text(encoding="utf-8"))["status"]

    def text(self, name):
        return (self.target / name / "SKILL.md").read_text(encoding="utf-8")

    def stages(self):
        return set(self.target.glob(".omniagent-stage-*"))

    def updated_pair(self):
        """alpha and beta at v1 managed, source now v2: the next apply replaces both."""
        for name in ("alpha", "beta"):
            make_skill(self.source, name, "v1")
        self.apply()
        for name in ("alpha", "beta"):
            (self.source / name / "SKILL.md").write_text(f"---\nname: {name}\n---\nv2\n", encoding="utf-8")

    def assert_recovered(self, leftovers):
        done = self.recover()
        for stage in leftovers:
            self.assertTrue(stage.exists(), "recover must not delete staging leftovers")
            self.assertIn(f"leftover staging folder (inspect, then delete manually): {stage}", done.stdout)
        self.assertEqual(self.journal_status(), "recovered_reverted")
        self.assertIn("0 step(s)", self.recover().stdout)  # idempotent once settled
        self.assertIn("v1", self.text("alpha"))
        self.assertIn("v1", self.text("beta"))
        plan = self.run_cli("plan", "--source", str(self.source), "--state-dir", str(self.state),
                            "--target", f"claude={self.target}").stdout
        self.assertEqual(plan.count(" update "), 2, plan)

    def killed_cleanup(self, fail, kill):
        self.updated_pair()
        before = self.stages()
        self.apply({"FAIL_AT": fail, "KILL_AT": kill}, expected=137)
        self.assertEqual(self.journal_status(), "in_progress")
        return self.stages() - before

    def test_in_process_failure_still_reverts_and_marks_failed_reverted(self):
        self.updated_pair()
        self.apply({"FAIL_AT": "commit", "KILL_AT": "none"}, expected=2)
        self.assertEqual(self.journal_status(), "failed_reverted")
        self.assertIn("v1", self.text("alpha"))
        self.assertIn("v1", self.text("beta"))

    def test_killed_while_removing_during_cleanup_can_be_recovered(self):
        self.assert_recovered(self.killed_cleanup("commit", "rmtree"))

    def test_killed_while_preparing_restore_copy_can_be_recovered(self):
        self.assert_recovered(self.killed_cleanup("commit", "copy"))

    def test_killed_between_renames_during_cleanup_can_be_recovered(self):
        leftovers = self.killed_cleanup("commit", "old_moved")
        self.assertFalse((self.target / "beta").exists())  # cleanup runs in reverse: beta first
        self.assert_recovered(leftovers)

    def test_killed_after_swap_in_during_cleanup_can_be_recovered(self):
        leftovers = self.killed_cleanup("commit", "swapped_in")
        self.assertIn("v1", self.text("beta"))
        self.assert_recovered(leftovers)

    def test_state_write_failure_killed_during_cleanup_is_reverted_not_rolled_forward(self):
        self.assert_recovered(self.killed_cleanup("state", "old_moved"))

    def test_hand_edit_during_apply_is_kept_and_not_marked_reverted(self):
        self.updated_pair()
        self.apply({"FAIL_AT": "commit", "KILL_AT": "none", "EDIT_FIRST": str(self.target / "alpha")},
                   expected=2)
        self.assertEqual(self.text("alpha"), "hand edit")
        self.assertIn("v1", self.text("beta"))
        self.assertEqual(self.journal_status(), "in_progress")  # revert incomplete: never failed_reverted
        self.recover(expected=2)
        self.assertEqual(self.text("alpha"), "hand edit")


if __name__ == "__main__":
    unittest.main()
