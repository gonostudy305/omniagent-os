"""Safety and rollback tests using isolated fake agent stores (stdlib only)."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "omniagent.py"


def make_skill(root: Path, name: str, text: str = "hello") -> Path:
    skill = root / name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(f"---\nname: {name}\n---\n{text}\n", encoding="utf-8")
    return skill


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / "ssot" / "skills"
        self.source.mkdir(parents=True)
        self.target = self.base / "claude" / "skills"
        self.state = self.base / "private" / "manager"

    def run_cli(self, *args, expected=0):
        result = subprocess.run([sys.executable, "-I", str(SCRIPT), *map(str, args)],
                                capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def common(self):
        return ("--source", self.source, "--state-dir", self.state,
                "--target", f"claude={self.target}")

    def transaction(self, output: str) -> str:
        return output.split("Transaction: ", 1)[1].splitlines()[0]

    def test_plan_is_read_only_and_apply_needs_consent(self):
        make_skill(self.source, "writing")
        result = self.run_cli("plan", *self.common())
        self.assertIn("install", result.stdout)
        self.assertFalse(self.target.exists())
        self.assertFalse(self.state.exists())
        self.run_cli("apply", *self.common(), expected=2)
        self.assertFalse(self.target.exists())

    def test_discover_works_before_local_ssot_is_initialized(self):
        result = self.run_cli("discover", "--source", self.base / "missing",
                              "--target", f"claude={self.target}")
        self.assertIn("not initialized", result.stdout)
        self.assertIn("not found", result.stdout)
        self.assertFalse(self.state.exists())

    def test_install_excludes_vendor_and_rollback(self):
        skill = make_skill(self.source, "writing")
        vendor = skill / "node_modules" / "package"
        vendor.mkdir(parents=True)
        (vendor / "file.js").write_text("ignored")
        result = self.run_cli("apply", *self.common(), "--yes")
        self.assertTrue((self.target / "writing" / "SKILL.md").is_file())
        self.assertFalse((self.target / "writing" / "node_modules").exists())
        transaction = self.transaction(result.stdout)
        self.run_cli("plan", *self.common())
        self.run_cli("rollback", "--state-dir", self.state,
                     "--transaction", transaction, "--yes")
        self.assertFalse((self.target / "writing").exists())
        self.assertEqual(json.loads((self.state / "state.json").read_text())["managed"], {})
        self.run_cli("rollback", "--state-dir", self.state,
                     "--transaction", transaction, "--yes", expected=2)

    def test_managed_update_and_rollback_restores_previous_contents(self):
        skill = make_skill(self.source, "writing", "first")
        self.run_cli("apply", *self.common(), "--yes")
        (skill / "SKILL.md").write_text("second", encoding="utf-8")
        self.assertIn("update", self.run_cli("plan", *self.common()).stdout)
        result = self.run_cli("apply", *self.common(), "--yes")
        self.assertEqual((self.target / "writing" / "SKILL.md").read_text(), "second")
        self.run_cli("rollback", "--state-dir", self.state, "--yes",
                     "--transaction", self.transaction(result.stdout))
        self.assertIn("first", (self.target / "writing" / "SKILL.md").read_text())
        self.assertIn("update", self.run_cli("plan", *self.common()).stdout)

    def test_unmanaged_collision_blocks_without_touching_existing_data(self):
        make_skill(self.source, "writing", "new")
        make_skill(self.source, "another")
        make_skill(self.target, "writing", "personal")
        self.run_cli("apply", *self.common(), "--yes", expected=2)
        self.assertFalse((self.target / "another").exists())
        self.run_cli("apply", *self.common(), "--yes", "--skip-blocked")
        self.assertIn("personal", (self.target / "writing" / "SKILL.md").read_text())
        self.assertTrue((self.target / "another" / "SKILL.md").is_file())

    def test_manual_edit_prevents_update_and_rollback(self):
        skill = make_skill(self.source, "writing", "first")
        result = self.run_cli("apply", *self.common(), "--yes")
        (self.target / "writing" / "SKILL.md").write_text("user edit", encoding="utf-8")
        (skill / "SKILL.md").write_text("changed source", encoding="utf-8")
        self.assertIn("conflict", self.run_cli("plan", *self.common()).stdout)
        self.run_cli("apply", *self.common(), "--yes", expected=2)
        self.run_cli("rollback", "--state-dir", self.state, "--yes",
                     "--transaction", self.transaction(result.stdout), expected=2)
        self.assertEqual((self.target / "writing" / "SKILL.md").read_text(), "user edit")

    def test_secret_file_and_codex_system_target_are_blocked(self):
        skill = make_skill(self.source, "writing")
        (skill / ".env").write_text("do not copy", encoding="utf-8")
        self.run_cli("plan", *self.common(), expected=2)
        self.assertFalse(self.target.exists())
        (skill / ".env").unlink()
        protected = self.base / ".codex" / "skills" / ".system"
        self.run_cli("apply", "--source", self.source, "--state-dir", self.state,
                     "--target", f"codex_system={protected}", "--yes", expected=2)
        self.assertFalse(protected.exists())

    def test_env_templates_are_allowed_but_real_env_files_are_blocked(self):
        skill = make_skill(self.source, "api-skill")
        for name in (".env.example", ".env.sample", ".env.template"):
            (skill / name).write_text("API_KEY=your-key-here", encoding="utf-8")
        result = self.run_cli("apply", *self.common(), "--yes")
        self.assertIn("install", result.stdout)
        for name in (".env.example", ".env.sample", ".env.template"):
            self.assertTrue((self.target / "api-skill" / name).is_file())
        (skill / ".env.local").write_text("API_KEY=real", encoding="utf-8")
        self.run_cli("plan", *self.common(), expected=2)

    def test_unexpected_file_prevents_destructive_rollback(self):
        make_skill(self.source, "writing")
        result = self.run_cli("apply", *self.common(), "--yes")
        extra = self.target / "writing" / "user-notes.txt"
        extra.write_text("my notes", encoding="utf-8")
        self.run_cli("rollback", "--state-dir", self.state, "--yes",
                     "--transaction", self.transaction(result.stdout), expected=2)
        self.assertEqual(extra.read_text(encoding="utf-8"), "my notes")

    def test_vendor_added_after_install_blocks_rollback(self):
        make_skill(self.source, "writing")
        result = self.run_cli("apply", *self.common(), "--yes")
        dependency = self.target / "writing" / "node_modules" / "pkg"
        dependency.mkdir(parents=True)
        (dependency / "index.js").write_text("custom dependency", encoding="utf-8")
        self.run_cli("rollback", "--state-dir", self.state, "--yes",
                     "--transaction", self.transaction(result.stdout), expected=2)
        self.assertTrue(dependency.is_dir())

    def test_empty_directory_added_after_install_blocks_rollback(self):
        make_skill(self.source, "writing")
        result = self.run_cli("apply", *self.common(), "--yes")
        notes = self.target / "writing" / "keep-this-empty-folder"
        notes.mkdir()
        self.run_cli("rollback", "--state-dir", self.state, "--yes",
                     "--transaction", self.transaction(result.stdout), expected=2)
        self.assertTrue(notes.is_dir())

    def test_existing_manager_lock_prevents_concurrent_apply(self):
        make_skill(self.source, "writing")
        self.state.mkdir(parents=True)
        (self.state / "manager.lock").write_text("other process", encoding="utf-8")
        self.run_cli("apply", *self.common(), "--yes", expected=2)
        self.assertFalse(self.target.exists())
        self.assertTrue((self.state / "manager.lock").exists())


if __name__ == "__main__":
    unittest.main()
