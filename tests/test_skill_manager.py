"""Safety and rollback tests using isolated fake agent stores (stdlib only)."""

import json
import os
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

    def test_discover_warns_about_directories_without_skill_md(self):
        make_skill(self.source, "writing")
        (self.source / "common").mkdir()
        (self.source / "synced").mkdir()
        result = self.run_cli("discover", "--source", self.source,
                              "--target", f"claude={self.target}")
        self.assertIn("WARNING", result.stdout)
        self.assertIn("common", result.stdout)
        self.assertIn("synced", result.stdout)
        self.assertIn("1 skills", result.stdout)

    def test_adopt_exact_match_without_touching_target_files(self):
        make_skill(self.source, "writing", "same")
        target_skill = make_skill(self.target, "writing", "same")
        before = (target_skill / "SKILL.md").stat().st_mtime_ns
        preview = self.run_cli("adopt", *self.common())
        self.assertIn("eligible", preview.stdout)
        self.assertFalse(self.state.exists())
        result = self.run_cli("adopt", *self.common(), "--yes")
        transaction = result.stdout.split("Adoption transaction: ", 1)[1].splitlines()[0]
        self.assertEqual(before, (target_skill / "SKILL.md").stat().st_mtime_ns)
        self.assertIn("unchanged", self.run_cli("plan", *self.common()).stdout)
        tracked = json.loads((self.state / "state.json").read_text())["managed"]
        self.assertEqual(tracked[str(target_skill)]["origin"], "adopted")
        self.run_cli("rollback", "--state-dir", self.state, "--yes", "--transaction", transaction)
        self.assertTrue((target_skill / "SKILL.md").is_file())
        self.assertIn("blocked_unmanaged", self.run_cli("plan", *self.common()).stdout)

    def test_adopt_mismatch_shows_diff_only_when_selected(self):
        make_skill(self.source, "writing", "source")
        target_skill = make_skill(self.target, "writing", "local version")
        self.assertIn("mismatch", self.run_cli("adopt", *self.common()).stdout)
        self.run_cli("adopt", *self.common(), "--diff", expected=2)
        result = self.run_cli("adopt", *self.common(), "--skill", "writing", "--diff")
        self.assertIn("-source", result.stdout)
        self.assertIn("+local version", result.stdout)
        self.run_cli("adopt", *self.common(), "--yes")
        self.assertFalse((self.state / "state.json").exists())
        self.assertIn("local version", (target_skill / "SKILL.md").read_text())

    def test_undo_adoption_never_deletes_manually_edited_skill(self):
        make_skill(self.source, "writing", "original")
        target_skill = make_skill(self.target, "writing", "original")
        result = self.run_cli("adopt", *self.common(), "--yes")
        transaction = result.stdout.split("Adoption transaction: ", 1)[1].splitlines()[0]
        (target_skill / "SKILL.md").write_text("personal edits", encoding="utf-8")
        self.run_cli("rollback", "--state-dir", self.state, "--yes", "--transaction", transaction)
        self.assertEqual((target_skill / "SKILL.md").read_text(), "personal edits")
        self.assertEqual(json.loads((self.state / "state.json").read_text())["managed"], {})

    def test_adopt_multiple_existing_agent_stores_in_one_transaction(self):
        make_skill(self.source, "writing", "same")
        make_skill(self.target, "writing", "same")
        gemini = self.base / "gemini" / "skills"
        make_skill(gemini, "writing", "same")
        args = ("--source", self.source, "--state-dir", self.state,
                "--target", f"claude={self.target}", "--target", f"gemini={gemini}")
        result = self.run_cli("adopt", *args, "--yes")
        self.assertEqual(result.stdout.count("eligible"), 2)
        tracked = json.loads((self.state / "state.json").read_text())["managed"]
        self.assertEqual(len(tracked), 2)
        transaction = result.stdout.split("Adoption transaction: ", 1)[1].splitlines()[0]
        self.run_cli("rollback", "--state-dir", self.state, "--yes", "--transaction", transaction)
        self.assertTrue((self.target / "writing" / "SKILL.md").is_file())
        self.assertTrue((gemini / "writing" / "SKILL.md").is_file())

    def test_adopt_ignores_runtime_artifacts_and_keeps_them(self):
        source_skill = make_skill(self.source, "writing", "same")
        target_skill = make_skill(self.target, "writing", "same")
        for skill in (source_skill, target_skill):
            (skill / "scripts").mkdir()
            (skill / "scripts" / "run.py").write_text("print('hi')", encoding="utf-8")
        vendor = target_skill / "node_modules" / "lib"
        vendor.mkdir(parents=True)
        (vendor / "dep.js").write_text("user-managed dependency")
        cache = target_skill / "scripts" / "__pycache__"
        cache.mkdir(parents=True)
        (cache / "x.cpython-313.pyc").write_bytes(b"cache")
        self.assertIn("eligible", self.run_cli("adopt", *self.common()).stdout)
        result = self.run_cli("adopt", *self.common(), "--yes")
        self.assertIn("Adoption transaction", result.stdout)
        self.assertTrue((vendor / "dep.js").exists())
        self.assertTrue((cache / "x.cpython-313.pyc").exists())
        self.assertIn("already_managed", self.run_cli("adopt", *self.common()).stdout)

    def test_non_runtime_extra_directory_still_makes_adopt_unsafe(self):
        make_skill(self.source, "writing", "same")
        target_skill = make_skill(self.target, "writing", "same")
        (target_skill / ".git").mkdir()
        self.assertIn("unsafe", self.run_cli("adopt", *self.common(), "--yes").stdout)
        self.assertFalse((self.state / "state.json").exists())

    def test_update_of_adopted_skill_with_node_modules_is_blocked(self):
        skill = make_skill(self.source, "writing", "v1")
        target_skill = make_skill(self.target, "writing", "v1")
        (target_skill / "node_modules").mkdir()
        (target_skill / "node_modules" / "dep.js").write_text("keep")
        self.run_cli("adopt", *self.common(), "--yes")
        (skill / "SKILL.md").write_text("---\nname: writing\n---\nv2\n", encoding="utf-8")
        self.assertIn("blocked_vendor", self.run_cli("plan", *self.common()).stdout)
        self.run_cli("apply", *self.common(), "--yes", "--allow-update-adopted", expected=2)
        self.run_cli("apply", *self.common(), "--yes", "--allow-update-adopted", "--skip-blocked")
        self.assertTrue((target_skill / "node_modules" / "dep.js").exists())
        self.assertIn("v1", (target_skill / "SKILL.md").read_text(encoding="utf-8"))

    def test_adopted_skill_cannot_be_updated_without_extra_consent(self):
        skill = make_skill(self.source, "writing", "v1")
        target_skill = make_skill(self.target, "writing", "v1")
        adopted = self.run_cli("adopt", *self.common(), "--yes")
        adopt_id = adopted.stdout.split("Adoption transaction: ", 1)[1].splitlines()[0]
        (skill / "SKILL.md").write_text("v2", encoding="utf-8")
        self.assertIn("update_adopted", self.run_cli("plan", *self.common()).stdout)
        self.run_cli("apply", *self.common(), "--yes", expected=2)
        self.run_cli("apply", *self.common(), "--yes", "--skip-blocked")
        self.assertEqual((target_skill / "SKILL.md").read_text(), "---\nname: writing\n---\nv1\n")
        updated = self.run_cli("apply", *self.common(), "--yes", "--allow-update-adopted")
        update_id = self.transaction(updated.stdout)
        self.assertEqual((target_skill / "SKILL.md").read_text(), "v2")
        self.run_cli("rollback", "--state-dir", self.state, "--yes",
                     "--transaction", adopt_id, expected=2)
        self.run_cli("rollback", "--state-dir", self.state, "--yes",
                     "--transaction", update_id)
        self.run_cli("rollback", "--state-dir", self.state, "--yes",
                     "--transaction", adopt_id)
        self.assertTrue(target_skill.is_dir())

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

    def test_dead_pid_lock_is_reclaimed_automatically(self):
        make_skill(self.source, "writing")
        self.state.mkdir(parents=True)
        (self.state / "manager.lock").write_text("99999999", encoding="utf-8")
        result = self.run_cli("apply", *self.common(), "--yes")
        self.assertIn("install", result.stdout)
        self.assertTrue((self.target / "writing" / "SKILL.md").is_file())
        self.assertFalse((self.state / "manager.lock").exists())

    def test_live_pid_lock_is_never_automatically_removed(self):
        make_skill(self.source, "writing")
        self.state.mkdir(parents=True)
        (self.state / "manager.lock").write_text(str(os.getpid()), encoding="utf-8")
        self.run_cli("apply", *self.common(), "--yes", expected=2)
        self.assertEqual((self.state / "manager.lock").read_text(), str(os.getpid()))
        self.assertFalse(self.target.exists())


if __name__ == "__main__":
    unittest.main()
