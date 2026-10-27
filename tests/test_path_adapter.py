"""Synthetic, cross-platform checks for the read-only Path Adapter preview."""

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import path_adapter as adapter
import omniagent
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
            record["adopted_transaction"] = "20261026T010000Z-aaaaaaaaaaaa"
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

    def adapter_apply_cli(self, *extra, expected=0, target=None):
        target_root = target or self.agents
        cmd = [sys.executable, "-I", str(Path(adapter.__file__).parent / "omniagent.py"),
               "apply", "--adapter-manifest", str(self.manifest), "--yes",
               "--source", str(self.source), "--state-dir", str(self.state),
               "--target", f"agents={target_root}", "--skill", "demo-skill", *extra]
        cp = subprocess.run(cmd, text=True, capture_output=True, encoding="utf-8")
        self.assertEqual(cp.returncode, expected, cp.stdout + cp.stderr)
        return cp

    def rollback_cli(self, transaction: str, *, expected=0):
        cmd = [sys.executable, "-I", str(Path(adapter.__file__).parent / "omniagent.py"),
               "rollback", "--state-dir", str(self.state), "--transaction", transaction,
               "--yes"]
        cp = subprocess.run(cmd, text=True, capture_output=True, encoding="utf-8")
        self.assertEqual(cp.returncode, expected, cp.stdout + cp.stderr)
        return cp

    def test_apply_requires_separate_consent_and_one_skill(self):
        cp = self.adapter_apply_cli(expected=2)
        self.assertIn("--allow-rendered-apply", cp.stderr)
        self.assertFalse(self.agents.exists())
        cmd = [sys.executable, "-I", str(Path(adapter.__file__).parent / "omniagent.py"),
               "apply", "--adapter-manifest", str(self.manifest), "--yes",
               "--allow-rendered-apply", "--source", str(self.source),
               "--state-dir", str(self.state), "--target", f"agents={self.agents}"]
        cp = subprocess.run(cmd, text=True, capture_output=True, encoding="utf-8")
        self.assertEqual(cp.returncode, 2)
        self.assertIn("one --skill", cp.stderr)
        self.assertFalse(self.agents.exists())

    def test_rendered_apply_internal_api_also_rejects_multiple_targets(self):
        with self.assertRaisesRegex(SafetyError, "exactly one skill and target"):
            omniagent.apply(self.source, self.state,
                            {"agents": self.agents, "claude": self.claude},
                            {"demo-skill"}, False,
                            adapter_manifest=self.config, adapter_module=adapter)
        self.assertFalse(self.agents.exists())
        self.assertFalse(self.claude.exists())

    def test_rendered_install_then_rollback_without_manifest(self):
        original_source = (self.source_skill / "SKILL.md").read_bytes()
        cp = self.adapter_apply_cli("--allow-rendered-apply")
        txn = cp.stdout.split("Transaction: ", 1)[1].splitlines()[0]
        installed = self.agents / "demo-skill"
        self.assertIn(b".agents/skills", (installed / "SKILL.md").read_bytes())
        self.assertNotIn(b"{{", (installed / "SKILL.md").read_bytes())
        self.assertEqual((self.source_skill / "SKILL.md").read_bytes(), original_source)
        state = json.loads((self.state / "state.json").read_text())["managed"][str(installed)]
        self.assertEqual(state["hash"], tree_digest(installed, source=False))
        self.assertEqual(state["source_hash"], tree_digest(self.source_skill))
        self.assertEqual(state["rendered_for"], "agents")
        journal = json.loads((self.state / "transactions" / f"{txn}.json").read_text())
        self.assertEqual(journal["actions"][0]["applied_hash"], state["hash"])
        self.rollback_cli(txn)
        self.assertFalse(installed.exists())
        self.assertEqual(json.loads((self.state / "state.json").read_text())["managed"], {})

    def test_plain_apply_cannot_replace_a_rendered_install_even_with_skip_blocked(self):
        self.adapter_apply_cli("--allow-rendered-apply")
        target = self.agents / "demo-skill"
        before = (target / "SKILL.md").read_bytes()
        before_state = (self.state / "state.json").read_bytes()
        before_journals = sorted((self.state / "transactions").iterdir())
        self.assertNotEqual(before, (self.source_skill / "SKILL.md").read_bytes())
        self.assertEqual(plan(self.source, self.state, {"agents": self.agents})[0]["status"],
                         "blocked_rendered")

        cmd = [sys.executable, "-I", str(Path(adapter.__file__).parent / "omniagent.py"),
               "apply", "--yes", "--source", str(self.source),
               "--state-dir", str(self.state), "--target", f"agents={self.agents}",
               "--skill", "demo-skill"]
        denied = subprocess.run(cmd, text=True, capture_output=True, encoding="utf-8")
        self.assertEqual(denied.returncode, 2, denied.stderr)
        self.assertIn("--adapter-manifest", denied.stderr)
        skipped = subprocess.run([*cmd, "--skip-blocked"], text=True,
                                 capture_output=True, encoding="utf-8")
        self.assertEqual(skipped.returncode, 0, skipped.stderr)
        self.assertIn("nothing changed", skipped.stdout)
        self.assertEqual((target / "SKILL.md").read_bytes(), before)
        self.assertEqual((self.state / "state.json").read_bytes(), before_state)
        self.assertEqual(sorted((self.state / "transactions").iterdir()), before_journals)

        rendered_plan = adapter.render_aware_plan(self.source, self.state,
                                                    {"agents": self.agents}, self.config)
        self.assertEqual(rendered_plan[0]["status"], "unchanged")
        self.assertIn("nothing changed", self.adapter_apply_cli("--allow-rendered-apply").stdout)

    def test_plain_skip_blocked_installs_other_skills_without_touching_rendered(self):
        self.adapter_apply_cli("--allow-rendered-apply")
        rendered = self.agents / "demo-skill" / "SKILL.md"
        before = rendered.read_bytes()
        plain = self.source / "plain"
        plain.mkdir()
        (plain / "SKILL.md").write_text("# Ordinary unrendered skill\n", encoding="utf-8")
        actions = plan(self.source, self.state, {"agents": self.agents})
        self.assertEqual({a["name"]: a["status"] for a in actions},
                         {"demo-skill": "blocked_rendered", "plain": "install"})
        cmd = [sys.executable, "-I", str(Path(adapter.__file__).parent / "omniagent.py"),
               "apply", "--yes", "--skip-blocked", "--source", str(self.source),
               "--state-dir", str(self.state), "--target", f"agents={self.agents}"]
        cp = subprocess.run(cmd, text=True, capture_output=True, encoding="utf-8")
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertEqual(rendered.read_bytes(), before)
        self.assertEqual((self.agents / "plain" / "SKILL.md").read_text(encoding="utf-8"),
                         "# Ordinary unrendered skill\n")

    def test_manifest_without_template_cannot_revert_a_rendered_record(self):
        self.adapter_apply_cli("--allow-rendered-apply")
        rendered = self.agents / "demo-skill" / "SKILL.md"
        original = rendered.read_bytes()
        plain = self.source / "plain"
        plain.mkdir()
        (plain / "SKILL.md").write_text("# No adapter placeholders\n", encoding="utf-8")
        self.config["skills"] = {"plain": {"files": ["SKILL.md"]}}
        self.save_manifest()
        actions = adapter.render_aware_plan(self.source, self.state,
                                             {"agents": self.agents}, self.config)
        self.assertEqual(next(a for a in actions if a["name"] == "demo-skill")["status"],
                         "blocked_rendered")
        cp = self.adapter_apply_cli("--allow-rendered-apply", expected=2)
        self.assertIn("skip-blocked", cp.stderr)
        self.assertEqual(rendered.read_bytes(), original)

    def test_rendered_record_for_another_agent_remains_blocked(self):
        self.adapter_apply_cli("--allow-rendered-apply")
        rendered = self.agents / "demo-skill" / "SKILL.md"
        original = rendered.read_bytes()
        state_path = self.state / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["managed"][str(self.agents / "demo-skill")]["rendered_for"] = "claude"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        actions = adapter.render_aware_plan(self.source, self.state,
                                             {"agents": self.agents}, self.config)
        self.assertEqual(actions[0]["status"], "blocked_rendered")
        cp = self.adapter_apply_cli("--allow-rendered-apply", expected=2)
        self.assertIn("skip-blocked", cp.stderr)
        self.assertEqual(rendered.read_bytes(), original)

    def test_manual_change_to_rendered_skill_is_still_a_conflict(self):
        self.adapter_apply_cli("--allow-rendered-apply")
        installed = self.agents / "demo-skill" / "SKILL.md"
        installed.write_text("Manual user change\n", encoding="utf-8")
        self.assertEqual(plan(self.source, self.state, {"agents": self.agents})[0]["status"],
                         "conflict")
        cp = self.adapter_apply_cli("--allow-rendered-apply", expected=2)
        self.assertIn("skip-blocked", cp.stderr)
        self.assertEqual(installed.read_text(encoding="utf-8"), "Manual user change\n")

    def test_rendered_update_from_v02_record_restores_exact_old_content(self):
        target = self.copy_rendered(self.agents, ".claude/skills")
        old_hash = tree_digest(target, source=False)
        old_content = (target / "SKILL.md").read_bytes()
        self.record_managed(self.agents, old_hash)
        cp = self.adapter_apply_cli("--allow-rendered-apply")
        txn = cp.stdout.split("Transaction: ", 1)[1].splitlines()[0]
        self.assertIn(b".agents/skills", (target / "SKILL.md").read_bytes())
        self.assertNotEqual(tree_digest(target, source=False), old_hash)
        self.rollback_cli(txn)
        self.assertEqual((target / "SKILL.md").read_bytes(), old_content)
        record = json.loads((self.state / "state.json").read_text())["managed"][str(target)]
        self.assertEqual(record["hash"], old_hash)
        self.assertNotIn("rendered_for", record)

    def test_adopted_update_requires_both_opt_ins(self):
        target = self.copy_rendered(self.agents, ".claude/skills")
        self.record_managed(self.agents, tree_digest(target, source=False), adopted=True)
        cp = self.adapter_apply_cli("--allow-rendered-apply", expected=2)
        self.assertIn("skip-blocked", cp.stderr)
        self.assertIn(b".claude/skills", (target / "SKILL.md").read_bytes())
        # Once explicitly permitted, the installed rendered copy is reversible.
        cp = self.adapter_apply_cli("--allow-rendered-apply", "--allow-update-adopted")
        txn = cp.stdout.split("Transaction: ", 1)[1].splitlines()[0]
        self.assertIn(b".agents/skills", (target / "SKILL.md").read_bytes())
        self.rollback_cli(txn)
        self.assertIn(b".claude/skills", (target / "SKILL.md").read_bytes())

    def test_rendered_apply_blocks_unmanaged_and_vendor(self):
        target = self.copy_rendered(self.agents, ".claude/skills")
        original = (target / "SKILL.md").read_bytes()
        cp = self.adapter_apply_cli("--allow-rendered-apply", expected=2)
        self.assertIn("unmanaged", cp.stderr)
        self.assertEqual((target / "SKILL.md").read_bytes(), original)
        self.record_managed(self.agents, tree_digest(target, source=False))
        (target / "node_modules").mkdir()
        (target / "node_modules" / "dep.js").write_text("vendor", encoding="utf-8")
        cp = self.adapter_apply_cli("--allow-rendered-apply", expected=2)
        self.assertIn("skip-blocked", cp.stderr)
        self.assertTrue((target / "node_modules" / "dep.js").exists())
        self.assertEqual((target / "SKILL.md").read_bytes(), original)

    def test_vendor_appearing_after_plan_blocks_rendered_update(self):
        target = self.copy_rendered(self.agents, ".claude/skills")
        self.record_managed(self.agents, tree_digest(target, source=False))
        old_content = (target / "SKILL.md").read_bytes()
        real_has_vendor = omniagent.has_vendor
        calls = 0

        def late_vendor_check(path):
            nonlocal calls
            calls += 1
            if calls == 2:
                (target / "node_modules").mkdir()
                (target / "node_modules" / "dependency.js").write_text("keep", encoding="utf-8")
            return real_has_vendor(path)

        with mock.patch.object(omniagent, "has_vendor", side_effect=late_vendor_check):
            with self.assertRaisesRegex(SafetyError, "vendor dependencies"):
                omniagent.apply(self.source, self.state, {"agents": self.agents},
                                {"demo-skill"}, False,
                                adapter_manifest=self.config, adapter_module=adapter)
        self.assertEqual((target / "SKILL.md").read_bytes(), old_content)
        self.assertEqual((target / "node_modules" / "dependency.js").read_text(), "keep")

    def test_rendered_rollback_refuses_late_vendor_files(self):
        cp = self.adapter_apply_cli("--allow-rendered-apply")
        txn = cp.stdout.split("Transaction: ", 1)[1].splitlines()[0]
        target = self.agents / "demo-skill"
        (target / "node_modules").mkdir()
        (target / "node_modules" / "dependency.js").write_text("keep", encoding="utf-8")
        cp = self.rollback_cli(txn, expected=2)
        self.assertIn("changed", cp.stderr)
        self.assertTrue((target / "node_modules" / "dependency.js").exists())

    def test_rendered_rollback_rejects_manual_edits(self):
        cp = self.adapter_apply_cli("--allow-rendered-apply")
        txn = cp.stdout.split("Transaction: ", 1)[1].splitlines()[0]
        skill_file = self.agents / "demo-skill" / "SKILL.md"
        skill_file.write_text("User-owned edits", encoding="utf-8")
        cp = self.rollback_cli(txn, expected=2)
        self.assertIn("changed", cp.stderr)
        self.assertEqual(skill_file.read_text(encoding="utf-8"), "User-owned edits")

    def test_rendered_apply_fails_safely_for_unknown_placeholder(self):
        (self.source_skill / "SKILL.md").write_text("Use {{SECRET_DIR}}/key", encoding="utf-8")
        cp = self.adapter_apply_cli("--allow-rendered-apply", expected=2)
        self.assertIn("Unresolved placeholder", cp.stderr)
        self.assertFalse(self.agents.exists())

    def test_v02_apply_rejects_rendered_flag_without_manifest(self):
        cmd = [sys.executable, "-I", str(Path(adapter.__file__).parent / "omniagent.py"),
               "apply", "--allow-rendered-apply", "--yes",
               "--source", str(self.source), "--state-dir", str(self.state),
               "--target", f"agents={self.agents}"]
        cp = subprocess.run(cmd, text=True, capture_output=True, encoding="utf-8")
        self.assertNotEqual(cp.returncode, 0)
        self.assertIn("requires --adapter-manifest", cp.stderr)
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

    # --- v0.3c: adapter-aware adopt (state only) ---

    def adopt_cli(self, *extra, expected=0):
        import subprocess
        cp = subprocess.run(
            [sys.executable, "-I", str(Path(adapter.__file__).parent / "omniagent.py"), "adopt",
             "--source", str(self.source), "--state-dir", str(self.state), *extra],
            capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(cp.returncode, expected, cp.stdout + cp.stderr)
        return cp

    def snapshot(self, root: Path):
        return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}

    def test_adopt_rendered_copy_only_records_state(self):
        installed = self.copy_rendered(self.agents, ".agents/skills")
        before = self.snapshot(installed)
        args = ("--target", f"agents={self.agents}", "--adapter-manifest", str(self.manifest))
        self.assertIn("eligible_rendered", self.adopt_cli(*args).stdout)
        self.assertFalse((self.state / "state.json").exists())
        done = self.adopt_cli(*args, "--yes")
        self.assertEqual(self.snapshot(installed), before)
        record = json.loads((self.state / "state.json").read_text(encoding="utf-8"))["managed"][str(installed)]
        manifest = adapter.load_manifest(self.manifest)
        expected, _ = adapter.rendered_skill(self.source_skill, ["SKILL.md"], manifest["targets"]["agents"])
        self.assertEqual(record["hash"], expected)
        self.assertEqual(record["rendered_for"], "agents")
        self.assertEqual(record["origin"], "adopted")
        self.assertNotEqual(record["hash"], record["source_hash"])
        # A plain v0.2 plan must now refuse to overwrite it; the render-aware plan sees it unchanged.
        plain = self.adopt_cli("--target", f"agents={self.agents}")
        self.assertIn("already_managed", plain.stdout)
        self.assertIn("Adoption transaction", done.stdout)
        import subprocess
        rendered_plan = subprocess.run(
            [sys.executable, "-I", str(Path(adapter.__file__).parent / "omniagent.py"), "plan",
             "--source", str(self.source), "--state-dir", str(self.state),
             "--target", f"agents={self.agents}", "--adapter-manifest", str(self.manifest)],
            capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(rendered_plan.returncode, 0, rendered_plan.stderr)
        self.assertIn("unchanged", rendered_plan.stdout)
        plain_plan = subprocess.run(
            [sys.executable, "-I", str(Path(adapter.__file__).parent / "omniagent.py"), "plan",
             "--source", str(self.source), "--state-dir", str(self.state),
             "--target", f"agents={self.agents}"], capture_output=True, text=True, encoding="utf-8")
        self.assertIn("blocked_rendered", plain_plan.stdout)

    def test_adopt_rendered_without_manifest_stays_mismatch(self):
        self.copy_rendered(self.agents, ".agents/skills")
        out = self.adopt_cli("--target", f"agents={self.agents}", "--yes").stdout
        self.assertIn("mismatch", out)
        self.assertFalse((self.state / "state.json").exists())

    def test_adopt_rendered_for_wrong_agent_stays_mismatch(self):
        self.copy_rendered(self.claude, ".agents/skills")  # claude folder holding agents paths
        out = self.adopt_cli("--target", f"claude={self.claude}", "--adapter-manifest", str(self.manifest), "--yes").stdout
        self.assertIn("mismatch", out)
        self.assertFalse((self.state / "state.json").exists())

    def test_adopt_rendered_rollback_untracks_without_touching_files(self):
        installed = self.copy_rendered(self.agents, ".agents/skills")
        before = self.snapshot(installed)
        args = ("--target", f"agents={self.agents}", "--adapter-manifest", str(self.manifest), "--yes")
        txn = self.adopt_cli(*args).stdout.split("Adoption transaction: ", 1)[1].splitlines()[0]
        import subprocess
        cp = subprocess.run([sys.executable, "-I", str(Path(adapter.__file__).parent / "omniagent.py"),
                             "rollback", "--state-dir", str(self.state), "--transaction", txn, "--yes"],
                            capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertEqual(self.snapshot(installed), before)
        state = json.loads((self.state / "state.json").read_text(encoding="utf-8"))
        self.assertNotIn(str(installed), state["managed"])

    def test_adopt_rendered_edited_copy_is_not_adopted(self):
        installed = self.copy_rendered(self.agents, ".agents/skills")
        (installed / "scripts" / "run.py").write_text("print('edited')\n", encoding="utf-8")
        out = self.adopt_cli("--target", f"agents={self.agents}", "--adapter-manifest", str(self.manifest), "--yes").stdout
        self.assertIn("mismatch", out)
        self.assertFalse((self.state / "state.json").exists())

    def test_adopt_with_manifest_missing_target_is_rejected(self):
        self.copy_rendered(self.agents, ".agents/skills")
        del self.config["targets"]["agents"]
        self.save_manifest()
        self.adopt_cli("--target", f"agents={self.agents}", "--adapter-manifest", str(self.manifest), "--yes", expected=2)
        self.assertFalse((self.state / "state.json").exists())

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
