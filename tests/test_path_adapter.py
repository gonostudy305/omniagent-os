"""Synthetic, cross-platform checks for the read-only Path Adapter preview."""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import path_adapter as adapter
from omniagent import SafetyError, tree_digest


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
