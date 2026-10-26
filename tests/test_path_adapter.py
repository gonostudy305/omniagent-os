"""Synthetic, cross-platform checks for the read-only Path Adapter preview."""

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import path_adapter as adapter
from omniagent import SafetyError, plan, tree_digest


class PathAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "ssot"
        self.state = self.root / "state"
        self.agents = self.root / "agents"
        self.claude = self.root / "claude"
        self.source_skill = self.source / "demo-skill"
        self.source_skill.mkdir(parents=True)
        (self.source_skill / "SKILL.md").write_bytes(
            b"# Demo\nUse {{SKILLS_DIR}}/{{SKILL_NAME}}/scripts/run.py\r\n"
        )
        (self.source_skill / "scripts").mkdir()
        (self.source_skill / "scripts" / "run.py").write_text("print('ok')\n", encoding="utf-8")
        self.config = {
            "version": 1,
            "targets": {
                "agents": {"SKILLS_DIR": ".agents/skills"},
                "claude": {"SKILLS_DIR": ".claude/skills"},
            },
            "skills": {"demo-skill": {"files": ["SKILL.md"]}},
        }
        self.manifest = self.root / "manifest.json"
        self.save_manifest()

    def save_manifest(self):
        self.manifest.write_text(json.dumps(self.config), encoding="utf-8")

    def copy_rendered(self, root: Path, skills_path: str):
        skill = root / "demo-skill"
        (skill / "scripts").mkdir(parents=True)
        (skill / "SKILL.md").write_bytes(
            f"# Demo\nUse {skills_path}/demo-skill/scripts/run.py\r\n".encode()
        )
        (skill / "scripts" / "run.py").write_text("print('ok')\n", encoding="utf-8")
        return skill

    def record_managed(self, root: Path, digest: str, *, adopted: bool = False):
        record = {"source": str(self.source_skill.resolve()), "hash": digest}
        if adopted:
            record["origin"] = "adopted"
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "state.json").write_text(json.dumps({
            "version": 1, "managed": {str(root / "demo-skill"): record}
        }), encoding="utf-8")

    def test_manifest_and_target_specific_hash(self):
        config = adapter.load_manifest(self.manifest)
        agents_digest, rendered = adapter.rendered_skill(
            self.source_skill, ["SKILL.md"], config["targets"]["agents"]
        )
        claude_digest, _ = adapter.rendered_skill(
            self.source_skill, ["SKILL.md"], config["targets"]["claude"]
        )
        self.assertNotEqual(agents_digest, claude_digest)
        self.assertIn(b".agents/skills", rendered["SKILL.md"])
        self.assertIn(b"\r\n", rendered["SKILL.md"])
        self.assertNotIn(b"{{", rendered["SKILL.md"])

    def test_virtual_digest_matches_real_rendered_skill(self):
        expected, _ = adapter.rendered_skill(
            self.source_skill, ["SKILL.md"], self.config["targets"]["agents"]
        )
        target = self.copy_rendered(self.agents, ".agents/skills")
        self.assertEqual(tree_digest(target, source=False), expected)

    def test_render_plan_distinguishes_three_hashes_without_false_update(self):
        target = self.copy_rendered(self.agents, ".agents/skills")
        installed_hash = tree_digest(target, source=False)
        raw_hash = tree_digest(self.source_skill)
        self.assertNotEqual(raw_hash, installed_hash)
        self.record_managed(self.agents, installed_hash)
        before = (target / "SKILL.md").read_bytes()
        state_before = (self.state / "state.json").read_bytes()

        self.assertEqual(plan(self.source, self.state, {"agents": self.agents})[0]["status"], "update")
        result = adapter.render_aware_plan(self.source, self.state, {"agents": self.agents}, self.config)[0]
        self.assertEqual(result["status"], "unchanged")
        self.assertEqual(result["source_hash"], raw_hash)
        self.assertEqual(result["render_hash"], installed_hash)
        self.assertEqual(result["installed_hash"], installed_hash)
        self.assertTrue(result["render_matches_installed"])
        self.assertEqual((target / "SKILL.md").read_bytes(), before)
        self.assertEqual((self.state / "state.json").read_bytes(), state_before)

    def test_render_plan_hash_differs_per_agent_but_source_hash_is_shared(self):
        agents_target = self.copy_rendered(self.agents, ".agents/skills")
        claude_target = self.copy_rendered(self.claude, ".claude/skills")
        results = adapter.render_aware_plan(
            self.source, self.state, {"agents": self.agents, "claude": self.claude}, self.config)
        by_agent = {item["target"]: item for item in results}
        self.assertEqual(by_agent["agents"]["source_hash"], by_agent["claude"]["source_hash"])
        self.assertNotEqual(by_agent["agents"]["render_hash"], by_agent["claude"]["render_hash"])
        self.assertEqual(by_agent["agents"]["render_hash"], tree_digest(agents_target, source=False))
        self.assertEqual(by_agent["claude"]["render_hash"], tree_digest(claude_target, source=False))
        self.assertTrue(all(item["status"] == "blocked_unmanaged" for item in results))

    def test_render_plan_keeps_unmanaged_collision_blocked_even_if_render_matches(self):
        self.copy_rendered(self.agents, ".agents/skills")
        result = adapter.render_aware_plan(self.source, self.state, {"agents": self.agents}, self.config)[0]
        self.assertEqual(result["status"], "blocked_unmanaged")
        self.assertTrue(result["render_matches_installed"])
        self.assertIsNone(result["recorded_hash"])
        self.assertFalse(self.state.exists())

    def test_render_plan_keeps_manual_edit_conflict_even_if_render_matches(self):
        target = self.copy_rendered(self.agents, ".agents/skills")
        self.record_managed(self.agents, "0" * 64)
        result = adapter.render_aware_plan(self.source, self.state, {"agents": self.agents}, self.config)[0]
        self.assertEqual(result["status"], "conflict")
        self.assertTrue(result["render_matches_installed"])
        self.assertNotEqual(result["recorded_hash"], result["installed_hash"])

    def test_render_plan_recommends_update_but_does_not_write(self):
        target = self.copy_rendered(self.agents, ".claude/skills")
        self.record_managed(self.agents, tree_digest(target, source=False), adopted=True)
        before = (target / "SKILL.md").read_bytes()
        result = adapter.render_aware_plan(self.source, self.state, {"agents": self.agents}, self.config)[0]
        self.assertEqual(result["status"], "update_adopted")
        self.assertFalse(result["render_matches_installed"])
        self.assertEqual((target / "SKILL.md").read_bytes(), before)

    def test_render_plan_retains_vendor_guard(self):
        target = self.copy_rendered(self.agents, ".claude/skills")
        self.record_managed(self.agents, tree_digest(target, source=False))
        (target / "node_modules").mkdir()
        (target / "node_modules" / "index.js").write_text("vendor")
        result = adapter.render_aware_plan(self.source, self.state, {"agents": self.agents}, self.config)[0]
        self.assertEqual(result["status"], "blocked_vendor")
        self.assertTrue((target / "node_modules" / "index.js").exists())

    def test_render_plan_non_templated_skill_keeps_raw_hash(self):
        plain = self.source / "plain"
        plain.mkdir()
        (plain / "SKILL.md").write_text("# No variables\n")
        results = adapter.render_aware_plan(self.source, self.state, {"agents": self.agents}, self.config)
        by_name = {item["name"]: item for item in results}
        self.assertEqual(by_name["plain"]["source_hash"], by_name["plain"]["render_hash"])
        self.assertIsNone(by_name["plain"]["installed_hash"])
        self.assertEqual(by_name["plain"]["status"], "install")

    def test_manager_cli_render_plan_json_is_read_only(self):
        target = self.copy_rendered(self.agents, ".agents/skills")
        self.record_managed(self.agents, tree_digest(target, source=False))
        cmd = [sys.executable, "-I", str(Path(adapter.__file__).parent / "omniagent.py"),
               "plan", "--adapter-manifest", str(self.manifest), "--source", str(self.source),
               "--state-dir", str(self.state), "--target", f"agents={self.agents}", "--json"]
        state_before = (self.state / "state.json").read_bytes()
        cp = subprocess.run(cmd, text=True, capture_output=True, encoding="utf-8")
        self.assertEqual(cp.returncode, 0, cp.stderr)
        result = json.loads(cp.stdout)[0]
        self.assertEqual(result["status"], "unchanged")
        self.assertEqual(result["installed_hash"], result["render_hash"])
        self.assertEqual((self.state / "state.json").read_bytes(), state_before)

    def test_apply_cannot_use_adapter_manifest(self):
        cmd = [sys.executable, "-I", str(Path(adapter.__file__).parent / "omniagent.py"),
               "apply", "--adapter-manifest", str(self.manifest), "--yes",
               "--source", str(self.source), "--target", f"agents={self.agents}"]
        cp = subprocess.run(cmd, text=True, capture_output=True, encoding="utf-8")
        self.assertNotEqual(cp.returncode, 0)
        self.assertIn("unrecognized arguments", cp.stderr)
        self.assertFalse(self.agents.exists())

    def test_plan_is_read_only_and_classifies_targets(self):
        exact = self.copy_rendered(self.agents, ".agents/skills")
        wrong = self.copy_rendered(self.claude, ".agents/skills")
        before = {str(p): p.read_bytes() for p in (exact / "SKILL.md", wrong / "SKILL.md")}
        result = adapter.adapter_plan(self.source, self.state,
                                      {"agents": self.agents, "claude": self.claude}, self.config)
        self.assertEqual([e["status"] for e in result], ["match", "drift"])
        self.assertFalse(self.state.exists())
        for path, original in before.items():
            self.assertEqual(Path(path).read_bytes(), original)

    def test_missing_target_is_reported_without_creating_it(self):
        result = adapter.adapter_plan(self.source, self.state, {"agents": self.agents}, self.config)
        self.assertEqual(result[0]["status"], "missing")
        self.assertFalse(self.agents.exists())

    def test_unlisted_placeholder_file_fails_closed(self):
        (self.source_skill / "scripts" / "run.py").write_text("print('{{SKILLS_DIR}}')")
        with self.assertRaisesRegex(SafetyError, "Undeclared"):
            adapter.adapter_plan(self.source, self.state, {"agents": self.agents}, self.config)

    def test_missing_configured_file_rejected(self):
        self.config["skills"]["demo-skill"]["files"].append("references/missing.md")
        with self.assertRaisesRegex(SafetyError, "do not exist"):
            adapter.adapter_plan(self.source, self.state, {"agents": self.agents}, self.config)

    def test_unresolved_placeholder_rejected(self):
        (self.source_skill / "SKILL.md").write_text("{{SECRET_DIR}}/credentials")
        with self.assertRaisesRegex(SafetyError, "Unresolved placeholder"):
            adapter.adapter_plan(self.source, self.state, {"agents": self.agents}, self.config)

    def test_bad_manifest_schema_and_traversal_rejected(self):
        bad_files = (["../SKILL.md"], ["/etc/passwd"], ["scripts\\run.py"],
                     ["SKILL.md", "SKILL.md"])
        for values in bad_files:
            with self.subTest(values=values):
                self.config["skills"]["demo-skill"]["files"] = values
                self.save_manifest()
                with self.assertRaises(SafetyError):
                    adapter.load_manifest(self.manifest)

    def test_shell_metacharacters_in_path_value_rejected(self):
        for dangerous in ("foo; rm", "foo && bar", "$(whoami)", "C:\\src\\..\\private"):
            with self.subTest(dangerous=dangerous):
                self.config["targets"]["agents"]["SKILLS_DIR"] = dangerous
                self.save_manifest()
                with self.assertRaises(SafetyError):
                    adapter.load_manifest(self.manifest)

    def test_symlink_in_source_rejected(self):
        link = self.source_skill / "scripts" / "outside"
        try:
            link.symlink_to(self.root / "outside")
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks not available under this test account")
        with self.assertRaises(SafetyError):
            adapter.adapter_plan(self.source, self.state, {"agents": self.agents}, self.config)

    def test_cli_dry_run(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = adapter.cli(["--manifest", str(self.manifest), "--source", str(self.source),
                              "--state-dir", str(self.state), "--target", f"agents={self.agents}"])
        self.assertEqual(rc, 0)
        self.assertIn("missing", out.getvalue())
        self.assertIn("Read-only preview", out.getvalue())
        self.assertFalse(self.state.exists())

    def test_ignored_placeholder_file_is_allowed_but_unlisted_one_still_fails(self):
        (self.source_skill / "references").mkdir()
        (self.source_skill / "references" / "tpl.md").write_text("Hello {{FIRST_NAME}}\n", encoding="utf-8")
        self.config["skills"]["demo-skill"]["ignore_placeholders"] = ["references/tpl.md"]
        self.manifest.write_text(json.dumps(self.config), encoding="utf-8")
        manifest = adapter.load_manifest(self.manifest)
        digest, rendered = adapter.rendered_skill(
            self.source_skill, ["SKILL.md"], manifest["targets"]["agents"], ["references/tpl.md"])
        self.assertNotIn("references/tpl.md", rendered)
        with self.assertRaises(SafetyError):
            adapter.rendered_skill(self.source_skill, ["SKILL.md"], manifest["targets"]["agents"])

    def test_ignore_list_cannot_overlap_template_files(self):
        self.config["skills"]["demo-skill"]["ignore_placeholders"] = ["SKILL.md"]
        self.manifest.write_text(json.dumps(self.config), encoding="utf-8")
        with self.assertRaises(SafetyError):
            adapter.load_manifest(self.manifest)

    def test_diff_with_non_ascii_survives_cp1252_console(self):
        import os
        import subprocess
        dest = self.agents / "demo-skill"
        dest.mkdir(parents=True)
        (dest / "SKILL.md").write_text("# Demo\nTiếng Việt \u1eef\n", encoding="utf-8")
        env = {**os.environ, "PYTHONIOENCODING": "cp1252"}
        result = subprocess.run(
            [sys.executable, str(Path(adapter.__file__)), "--manifest", str(self.manifest),
             "--source", str(self.source), "--state-dir", str(self.state),
             "--target", f"agents={self.agents}", "--skill", "demo-skill", "--diff"],
            capture_output=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
        self.assertNotIn(b"Traceback", result.stderr)

    def test_diff_requires_one_skill_and_target(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = adapter.cli(["--manifest", str(self.manifest), "--source", str(self.source),
                              "--target", f"agents={self.agents}", "--diff"])
        self.assertEqual(rc, 2)
        self.assertIn("--diff requires exactly one", err.getvalue())

    def test_diff_is_local_only(self):
        self.copy_rendered(self.agents, ".claude/skills")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = adapter.cli(["--manifest", str(self.manifest), "--source", str(self.source),
                              "--state-dir", str(self.state), "--target", f"agents={self.agents}",
                              "--skill", "demo-skill", "--diff"])
        self.assertEqual(rc, 0)
        self.assertIn("drift", out.getvalue())
        self.assertIn("--- rendered/SKILL.md", out.getvalue())
        self.assertFalse(self.state.exists())


if __name__ == "__main__":
    unittest.main()
