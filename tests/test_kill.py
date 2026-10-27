"""Real process-death tests: a child runs `apply` and is killed (os._exit) at a chosen point.

Unlike the in-process tests in test_recovery.py, nothing in the child cleans up after
itself (no finally blocks, no TemporaryDirectory removal), as with a power cut or kill -9.
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
import os, sys
sys.path.insert(0, {root!r})
from pathlib import Path
import omniagent

mode = os.environ["KILL_MODE"]
calls = {{"n": 0}}
real_copy, real_save, real_rename = omniagent.copy_skill, omniagent.save_json, Path.rename

def die():
    os._exit(137)

if mode == "second_copy":
    def fake(*a, **k):
        calls["n"] += 1
        if calls["n"] == 2:
            die()
        return real_copy(*a, **k)
    omniagent.copy_skill = fake
elif mode == "after_old_moved":
    def fake(self, target):
        calls["n"] += 1
        if calls["n"] == 2:
            die()
        return real_rename(self, target)
    Path.rename = fake
elif mode == "partial_backup":
    import shutil
    real_tree = shutil.copytree
    def fake(src, dst, *a, **k):
        if "backups" in Path(dst).parts:
            Path(dst).mkdir(parents=True)
            (Path(dst) / "half.txt").write_text("partial")
            die()
        return real_tree(src, dst, *a, **k)
    shutil.copytree = fake
elif mode == "rb_after_moved_aside":
    def fake(self, target):
        calls["n"] += 1
        if calls["n"] == 1 and "backups" not in str(self):
            real_rename(self, target)
            die()
        return real_rename(self, target)
    Path.rename = fake
elif mode == "rb_partial_copy":
    import shutil
    real_tree = shutil.copytree
    def fake(src, dst, *a, **k):
        if "backups" in Path(src).parts:
            Path(dst).mkdir(parents=True)
            (Path(dst) / "half.txt").write_text("partial")
            die()
        return real_tree(src, dst, *a, **k)
    shutil.copytree = fake
elif mode == "rb_before_state":
    def fake(path, data):
        if Path(path).name == "state.json":
            die()
        return real_save(path, data)
    omniagent.save_json = fake
elif mode == "before_state":
    def fake(path, data):
        if Path(path).name == "state.json":
            die()
        return real_save(path, data)
    omniagent.save_json = fake
elif mode == "after_state":
    def fake(path, data):
        if Path(path).parent.name == "transactions" and data.get("status") == "applied":
            die()
        return real_save(path, data)
    omniagent.save_json = fake
sys.exit(omniagent.cli(sys.argv[1:]))
"""


def make_skill(root: Path, name: str, text: str) -> Path:
    skill = root / name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(f"---\nname: {name}\n---\n{text}\n", encoding="utf-8")
    (skill / "notes.txt").write_text("x" * 5000, encoding="utf-8")
    return skill


class KillTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.source = base / "ssot"
        self.source.mkdir()
        self.target = base / "agent" / "skills"
        self.state = base / "state"

    def args(self, *extra):
        return ["--source", str(self.source), "--state-dir", str(self.state),
                "--target", f"claude={self.target}", *extra]

    def cli(self, command, *extra, kill=None, expected=0):
        if kill:
            cmd = [sys.executable, "-I", "-c", CHILD.format(root=str(ROOT)), command, *self.args(*extra)]
            env = {**os.environ, "KILL_MODE": kill}
        else:
            cmd = [sys.executable, "-I", str(SCRIPT), command, *self.args(*extra)]
            env = None
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=env)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def rollback(self, txn, kill=None, expected=0):
        args = ["rollback", "--state-dir", str(self.state), "--transaction", txn, "--yes"]
        if kill:
            cmd = [sys.executable, "-I", "-c", CHILD.format(root=str(ROOT)), *args]
            env = {**os.environ, "KILL_MODE": kill}
        else:
            cmd = [sys.executable, "-I", str(SCRIPT), *args]
            env = None
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=env)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def last_transaction(self):
        files = sorted((self.state / "transactions").glob("*.json"), key=lambda f: f.stat().st_mtime_ns)
        return json.loads(files[-1].read_text(encoding="utf-8"))["transaction"]

    def updated_skill(self):
        skill = make_skill(self.source, "alpha", "v1")
        self.cli("apply", "--yes")
        (skill / "SKILL.md").write_text("---\nname: alpha\n---\nv2\n", encoding="utf-8")
        self.cli("apply", "--yes")
        return self.last_transaction()

    def test_rollback_killed_after_skill_moved_aside_is_finished_by_recover(self):
        txn = self.updated_skill()
        self.rollback(txn, kill="rb_after_moved_aside", expected=137)
        self.assertFalse((self.target / "alpha").exists())
        self.cli("apply", "--yes", expected=2)  # blocked until recovered
        self.recover("--yes")
        self.assertIn("v1", (self.target / "alpha" / "SKILL.md").read_text(encoding="utf-8"))
        self.assertEqual(self.status()["alpha"], "update")

    def test_rollback_killed_during_restore_copy_never_leaves_a_partial_skill(self):
        txn = self.updated_skill()
        self.rollback(txn, kill="rb_partial_copy", expected=137)
        self.recover("--yes")
        self.assertIn("v1", (self.target / "alpha" / "SKILL.md").read_text(encoding="utf-8"))
        self.assertFalse((self.target / "alpha" / "half.txt").exists())
        self.assertEqual(self.status()["alpha"], "update")

    def test_rollback_killed_before_state_write_is_finished_by_recover(self):
        txn = self.updated_skill()
        self.rollback(txn, kill="rb_before_state", expected=137)
        self.recover("--yes")
        self.assertEqual(self.status()["alpha"], "update")  # state hash matches the restored v1 files
        self.assertIn("v1", (self.target / "alpha" / "SKILL.md").read_text(encoding="utf-8"))

    def test_rollback_of_a_fresh_install_killed_midway_is_finished(self):
        make_skill(self.source, "beta", "b")
        self.cli("apply", "--yes")
        txn = self.last_transaction()
        self.rollback(txn, kill="rb_after_moved_aside", expected=137)
        self.recover("--yes")
        self.assertFalse((self.target / "beta").exists())
        self.assertEqual(self.managed(), {})

    def recover(self, *extra, expected=0):
        result = subprocess.run([sys.executable, "-I", str(SCRIPT), "recover", "--state-dir", str(self.state), *extra],
                                capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def managed(self):
        path = self.state / "state.json"
        return json.loads(path.read_text(encoding="utf-8"))["managed"] if path.exists() else {}

    def status(self):
        out = self.cli("plan").stdout
        return {line.split()[2]: line.split()[1] for line in out.splitlines() if len(line.split()) == 3}

    def journal_status(self):
        files = sorted((self.state / "transactions").glob("*.json"), key=lambda f: f.stat().st_mtime_ns)
        return json.loads(files[-1].read_text(encoding="utf-8"))["status"]

    def test_killed_during_install_is_reverted_and_leftovers_listed(self):
        make_skill(self.source, "alpha", "a")
        make_skill(self.source, "beta", "b")
        self.cli("apply", "--yes", kill="second_copy", expected=137)
        self.assertEqual(self.journal_status(), "in_progress")
        self.assertTrue(any(self.target.glob(".omniagent-stage-*")), "staging folder should survive a hard kill")
        self.cli("apply", "--yes", expected=2)  # blocked until recovered
        preview = self.recover()
        self.assertIn("revert", preview.stdout)
        self.assertTrue((self.target / "alpha").exists())
        done = self.recover("--yes")
        self.assertIn("leftover staging folder", done.stdout)
        self.assertFalse((self.target / "alpha").exists())
        self.cli("apply", "--yes")
        self.assertTrue((self.target / "beta" / "SKILL.md").exists())

    def test_killed_after_old_copy_moved_aside_restores_it(self):
        skill = make_skill(self.source, "alpha", "v1")
        self.cli("apply", "--yes")
        (skill / "SKILL.md").write_text("---\nname: alpha\n---\nv2\n", encoding="utf-8")
        self.cli("apply", "--yes", kill="after_old_moved", expected=137)
        self.assertFalse((self.target / "alpha").exists())
        self.recover("--yes")
        self.assertIn("v1", (self.target / "alpha" / "SKILL.md").read_text(encoding="utf-8"))
        self.assertEqual(self.status()["alpha"], "update")  # state and files agree again

    def test_partial_backup_does_not_block_recovery(self):
        skill = make_skill(self.source, "alpha", "v1")
        self.cli("apply", "--yes")
        (skill / "SKILL.md").write_text("---\nname: alpha\n---\nv2\n", encoding="utf-8")
        self.cli("apply", "--yes", kill="partial_backup", expected=137)
        self.assertIn("v1", (self.target / "alpha" / "SKILL.md").read_text(encoding="utf-8"))
        self.recover("--yes")
        self.assertEqual(self.status()["alpha"], "update")

    def test_killed_before_state_write_is_rolled_forward(self):
        make_skill(self.source, "alpha", "a")
        self.cli("apply", "--yes", kill="before_state", expected=137)
        self.assertEqual(self.managed(), {})
        self.assertEqual(self.journal_status(), "committing")
        self.recover("--yes")
        self.assertIn(str(self.target / "alpha"), self.managed())
        self.assertEqual(self.status()["alpha"], "unchanged")

    def test_killed_after_state_write_is_finished(self):
        make_skill(self.source, "alpha", "a")
        self.cli("apply", "--yes", kill="after_state", expected=137)
        self.assertIn(str(self.target / "alpha"), self.managed())
        self.assertEqual(self.journal_status(), "committing")
        self.recover("--yes")
        self.assertEqual(self.journal_status(), "applied")
        self.assertEqual(self.status()["alpha"], "unchanged")


if __name__ == "__main__":
    unittest.main()
