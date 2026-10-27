"""Crash-recovery tests: simulate a killed apply with BaseException (not caught by cleanup)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import omniagent
from omniagent import SafetyError


def make_skill(root: Path, name: str, text: str) -> Path:
    skill = root / name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(f"---\nname: {name}\n---\n{text}\n", encoding="utf-8")
    return skill


class Crash(BaseException):
    """Stands in for a killed process: `except Exception` cleanup must not run."""


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.source = base / "ssot"
        self.source.mkdir()
        self.target = base / "agent" / "skills"
        self.state = base / "state"
        self.targets = {"claude": self.target}

    def apply(self, **kw):
        return omniagent.apply(self.source, self.state, self.targets, None, False, **kw)

    def journal(self):
        files = sorted((self.state / "transactions").glob("*.json"), key=lambda f: f.stat().st_mtime_ns)
        return json.loads(files[-1].read_text(encoding="utf-8"))

    def managed(self):
        path = self.state / "state.json"
        return json.loads(path.read_text(encoding="utf-8"))["managed"] if path.exists() else {}

    def test_crash_during_install_is_reverted(self):
        make_skill(self.source, "alpha", "a")
        make_skill(self.source, "beta", "b")
        real = omniagent.copy_skill
        calls = []

        def crash_on_second(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise Crash()
            return real(*args, **kwargs)

        with mock.patch.object(omniagent, "copy_skill", crash_on_second):
            with self.assertRaises(Crash):
                self.apply()
        self.assertEqual(self.journal()["status"], "in_progress")
        self.assertTrue((self.target / "alpha" / "SKILL.md").exists())
        self.assertFalse((self.target / "beta").exists())
        self.assertEqual(self.managed(), {})
        # Everything else refuses until the crash is handled.
        with self.assertRaises(SafetyError):
            self.apply()
        steps = omniagent.recover(self.state, False)
        self.assertEqual([s["action"] for s in steps], ["revert", "untouched"])
        self.assertTrue((self.target / "alpha").exists())  # preview changed nothing
        omniagent.recover(self.state, True)
        self.assertFalse((self.target / "alpha").exists())
        self.assertEqual(self.journal()["status"], "recovered_reverted")
        self.apply()  # normal operation resumes
        self.assertTrue((self.target / "beta" / "SKILL.md").exists())

    def test_crash_after_old_copy_moved_restores_from_backup(self):
        skill = make_skill(self.source, "alpha", "v1")
        self.apply()
        (skill / "SKILL.md").write_text("---\nname: alpha\n---\nv2\n", encoding="utf-8")
        real_rename = Path.rename
        calls = []

        def crash_on_second_rename(path, target):
            calls.append(1)
            if len(calls) == 2:  # old copy already moved aside, new one not yet in place
                raise Crash()
            return real_rename(path, target)

        with mock.patch.object(Path, "rename", crash_on_second_rename):
            with self.assertRaises(Crash):
                self.apply()
        self.assertFalse((self.target / "alpha").exists())  # the dangerous moment
        steps = omniagent.recover(self.state, True)
        self.assertEqual(steps[0]["action"], "restore")
        self.assertIn("v1", (self.target / "alpha" / "SKILL.md").read_text(encoding="utf-8"))
        self.assertEqual(self.journal()["status"], "recovered_reverted")

    def test_crash_before_replacement_leaves_installed_copy_untouched(self):
        skill = make_skill(self.source, "alpha", "v1")
        self.apply()
        (skill / "SKILL.md").write_text("---\nname: alpha\n---\nv2\n", encoding="utf-8")
        with mock.patch.object(omniagent, "copy_skill", side_effect=Crash()):
            with self.assertRaises(Crash):
                self.apply()
        steps = omniagent.recover(self.state, True)
        self.assertEqual(steps[0]["action"], "untouched")
        self.assertIn("v1", (self.target / "alpha" / "SKILL.md").read_text(encoding="utf-8"))

    def test_recover_refuses_when_installed_copy_was_edited(self):
        make_skill(self.source, "alpha", "a")
        make_skill(self.source, "beta", "b")
        real = omniagent.copy_skill
        calls = []

        def crash_on_second(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise Crash()
            return real(*args, **kwargs)

        with mock.patch.object(omniagent, "copy_skill", crash_on_second):
            with self.assertRaises(Crash):
                self.apply()
        (self.target / "alpha" / "SKILL.md").write_text("my edit", encoding="utf-8")
        with self.assertRaises(SafetyError):
            omniagent.recover(self.state, True)
        self.assertEqual((self.target / "alpha" / "SKILL.md").read_text(encoding="utf-8"), "my edit")
        self.assertEqual(self.journal()["status"], "in_progress")

    def test_recover_refuses_when_node_modules_present(self):
        make_skill(self.source, "alpha", "a")
        make_skill(self.source, "beta", "b")
        real = omniagent.copy_skill
        calls = []

        def crash_on_second(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise Crash()
            return real(*args, **kwargs)

        with mock.patch.object(omniagent, "copy_skill", crash_on_second):
            with self.assertRaises(Crash):
                self.apply()
        vendor = self.target / "alpha" / "node_modules"
        vendor.mkdir()
        (vendor / "dep.js").write_text("keep", encoding="utf-8")
        with self.assertRaises(SafetyError):
            omniagent.recover(self.state, True)
        self.assertTrue((vendor / "dep.js").exists())

    def test_adopt_and_rollback_also_wait_for_recovery(self):
        make_skill(self.source, "alpha", "a")
        make_skill(self.source, "beta", "b")
        real = omniagent.copy_skill
        calls = []

        def crash_on_second(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise Crash()
            return real(*args, **kwargs)

        with mock.patch.object(omniagent, "copy_skill", crash_on_second):
            with self.assertRaises(Crash):
                self.apply()
        with self.assertRaises(SafetyError):
            omniagent.adopt(self.source, self.state, self.targets, None)
        with self.assertRaises(SafetyError):
            omniagent.rollback(self.state, "20261026T000000Z-aaaaaaaaaaaa")

    def test_successful_apply_never_leaves_in_progress_journal(self):
        make_skill(self.source, "alpha", "a")
        self.apply()
        self.assertEqual(self.journal()["status"], "applied")
        self.assertEqual(omniagent.interrupted_transactions(self.state), [])
        self.assertEqual(omniagent.recover(self.state, True), [])


if __name__ == "__main__":
    unittest.main()
