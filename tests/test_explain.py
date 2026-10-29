"""`plan --explain` / `adopt --explain`: deterministic, read-only, path-free migration diagnostics."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "omniagent.py"
sys.path.insert(0, str(ROOT))

import omniagent


def make_skill(root: Path, name: str, text: str = "hello") -> Path:
    skill = root / name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(f"---\nname: {name}\n---\n{text}\n", encoding="utf-8")
    return skill


def snapshot(*roots: Path) -> dict:
    """Bytes and mtimes of every file and directory, to prove a command wrote nothing."""
    result = {}
    for root in roots:
        for path in sorted(root.rglob("*")):
            info = path.stat()
            data = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "DIR"
            result[str(path)] = (data, info.st_mtime_ns)
    return result


class ExplainTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / "ssot" / "skills"
        self.source.mkdir(parents=True)
        self.target = self.base / "claude" / "skills"
        self.target.mkdir(parents=True)
        self.state = self.base / "private" / "manager"

    def run_cli(self, *args, expected=0):
        result = subprocess.run([sys.executable, "-I", str(SCRIPT), *map(str, args)],
                                capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def common(self, source=None):
        return ("--source", source or self.source, "--state-dir", self.state,
                "--target", f"claude={self.target}")

    def dirty_workstation(self):
        """One skill per plan status, built through the real CLI, as on a messy machine."""
        for name in ("b-unchanged", "c-update", "d-adopted", "e-edited", "f-missing",
                     "g-unmanaged", "h-file", "i-vendor", "j-secret"):
            make_skill(self.source, name, "v1")
        make_skill(self.target, "d-adopted", "v1")                 # identical: adoptable
        make_skill(self.target, "g-unmanaged", "my own version")   # user-owned, differs
        (self.target / "h-file").write_text("not a folder", encoding="utf-8")
        self.run_cli("adopt", *self.common(), "--yes")
        self.run_cli("apply", *self.common(), "--yes", "--skip-blocked")
        make_skill(self.source, "a-install", "new")
        (self.source / "c-update" / "SKILL.md").write_text("---\nname: c-update\n---\nv2\n", encoding="utf-8")
        (self.source / "d-adopted" / "SKILL.md").write_text("---\nname: d-adopted\n---\nv2\n", encoding="utf-8")
        (self.target / "e-edited" / "SKILL.md").write_text("local edit\n", encoding="utf-8")
        shutil.rmtree(self.target / "f-missing")
        (self.source / "i-vendor" / "SKILL.md").write_text("---\nname: i-vendor\n---\nv2\n", encoding="utf-8")
        (self.target / "i-vendor" / "node_modules" / "dep").mkdir(parents=True)
        (self.target / "i-vendor" / "node_modules" / "dep" / "index.js").write_text("x", encoding="utf-8")
        (self.target / "j-secret" / ".env").write_text("PLACEHOLDER=1\n", encoding="utf-8")

    def test_every_dirty_state_gets_a_decision_reason_and_undo(self):
        self.dirty_workstation()
        doc = json.loads(self.run_cli("plan", *self.common(), "--explain", "--json").stdout)
        self.assertEqual(doc["schema"], "omniagent.explain/1")
        got = {e["name"]: (e["status"], e["cause"], e["decision"], e["l0_hash"]) for e in doc["entries"]}
        self.assertEqual(got, {
            "a-install": ("install", None, "will_change", "SKIP"),
            "b-unchanged": ("unchanged", None, "no_change", "PASS"),
            "c-update": ("update", None, "will_change", "FAIL"),
            "d-adopted": ("update_adopted", None, "needs_opt_in", "FAIL"),
            "e-edited": ("conflict", "edited", "blocked", "FAIL"),
            "f-missing": ("conflict_missing", None, "blocked", "FAIL"),
            "g-unmanaged": ("blocked_unmanaged", "untracked", "blocked", "UNKNOWN"),
            "h-file": ("blocked", "not_a_directory", "blocked", "UNKNOWN"),
            "i-vendor": ("blocked_vendor", None, "blocked", "FAIL"),
            "j-secret": ("conflict", "unreadable", "blocked", "UNKNOWN"),
        })
        self.assertEqual(doc["summary"], {"will_change": 2, "needs_opt_in": 1, "blocked": 6, "no_change": 1})
        for e in doc["entries"]:
            self.assertTrue(e["why"] and e["undo"], e)
            if e["decision"] not in {"will_change", "needs_opt_in"}:
                self.assertEqual(e["changes"], "nothing", e)  # blocked and no-op entries write nothing
            if e["decision"] == "blocked":
                self.assertEqual(e["authority"], "target", e)  # existing bytes always win
                self.assertTrue(e["resolve"], e)
        for level in ("L1", "L2", "L3"):
            self.assertEqual(doc["verification"][level], "NOT_VERIFIED")

    def test_explain_writes_nothing_and_is_byte_for_byte_deterministic(self):
        self.dirty_workstation()
        before = snapshot(self.source, self.target, self.state)
        first = self.run_cli("plan", *self.common(), "--explain", "--json").stdout
        second = self.run_cli("plan", *self.common(), "--explain", "--json").stdout
        text = self.run_cli("plan", *self.common(), "--explain").stdout
        adopt = self.run_cli("adopt", *self.common(), "--explain", "--json").stdout
        self.assertEqual(first, second)
        self.assertEqual(before, snapshot(self.source, self.target, self.state))
        self.assertFalse((self.state / "manager.lock").exists())
        for output in (first, text, adopt):
            # Shareable in a bug report: no machine paths, no content hashes.
            self.assertNotIn(self.temp.name, output)
            self.assertNotIn(Path(self.temp.name).name, output)
            self.assertNotIn(omniagent.tree_digest(self.source / "b-unchanged"), output)
        self.assertIn("NEEDS_OPT_IN", text)
        self.assertIn("NOT_VERIFIED", text)
        self.assertIn("Nothing was written", text)

    def test_text_output_without_explain_is_unchanged(self):
        make_skill(self.source, "writing")
        plain = self.run_cli("plan", *self.common()).stdout
        self.assertEqual(plain.strip().split(), ["claude", "install", "writing"])

    def test_adopt_explain_separates_state_only_writes_from_blocked_entries(self):
        make_skill(self.source, "same", "x")
        make_skill(self.target, "same", "x")
        make_skill(self.source, "differs", "x")
        make_skill(self.target, "differs", "y")
        make_skill(self.source, "absent", "x")
        doc = json.loads(self.run_cli("adopt", *self.common(), "--explain", "--json").stdout)
        got = {e["name"]: (e["status"], e["decision"], e["changes"]) for e in doc["entries"]}
        self.assertEqual(got, {
            "absent": ("install", "no_change", "nothing"),
            "differs": ("mismatch", "blocked", "nothing"),
            "same": ("eligible", "will_change", "manager_state_only"),
        })
        self.assertFalse(self.state.exists())

    def test_explain_refuses_to_be_combined_with_writes(self):
        make_skill(self.source, "same", "x")
        make_skill(self.target, "same", "x")
        refused = self.run_cli("adopt", *self.common(), "--explain", "--yes", expected=2)
        self.assertIn("read-only", refused.stderr)
        self.run_cli("adopt", *self.common(), "--json", expected=2)
        self.assertFalse(self.state.exists())
        self.run_cli("apply", *self.common(), "--yes", "--explain", expected=2)  # argparse: unknown flag
        self.assertFalse((self.target / "same" / ".omniagent").exists())

    def test_folder_managed_from_another_source_is_explained_not_fatal(self):
        make_skill(self.source, "shared", "x")
        self.run_cli("apply", *self.common(), "--yes")
        moved = self.base / "ssot-copy" / "skills"
        shutil.copytree(self.source, moved)
        make_skill(moved, "fresh", "x")
        make_skill(self.target, "fresh", "x")
        doc = json.loads(self.run_cli("adopt", *self.common(moved), "--explain", "--json").stdout)
        got = {e["name"]: (e["status"], e["decision"]) for e in doc["entries"]}
        self.assertEqual(got, {"fresh": ("eligible", "will_change"),
                               "shared": ("managed_elsewhere", "blocked")})
        # Before this fix the identical "shared" folder was reported eligible and
        # then aborted the whole adoption, so "fresh" could never be adopted.
        self.run_cli("adopt", *self.common(moved), "--yes")
        managed = json.loads((self.state / "state.json").read_text(encoding="utf-8"))["managed"]
        self.assertEqual(managed[str(self.target / "shared")]["source"], str((self.source / "shared").resolve()))
        self.assertEqual(managed[str(self.target / "fresh")]["origin"], "adopted")

    def test_rendered_install_is_explained_with_and_without_the_manifest(self):
        make_skill(self.source, "tpl", "see {{SKILLS_DIR}}/tpl")
        make_skill(self.target, "tpl", "see .claude/skills/tpl")
        manifest = self.base / "adapters.json"
        manifest.write_text(json.dumps({"version": 1, "targets": {"claude": {"SKILLS_DIR": ".claude/skills"}},
                                        "skills": {"tpl": {"files": ["SKILL.md"]}}}), encoding="utf-8")
        adopt = json.loads(self.run_cli("adopt", *self.common(), "--adapter-manifest", manifest,
                                        "--explain", "--json").stdout)
        self.assertEqual(adopt["mode"], "rendered")
        self.assertEqual(adopt["entries"][0]["status"], "eligible_rendered")
        self.run_cli("adopt", *self.common(), "--adapter-manifest", manifest, "--yes")
        plain = json.loads(self.run_cli("plan", *self.common(), "--explain", "--json").stdout)
        self.assertEqual((plain["entries"][0]["status"], plain["entries"][0]["decision"]),
                         ("blocked_rendered", "blocked"))
        rendered = json.loads(self.run_cli("plan", *self.common(), "--adapter-manifest", manifest,
                                           "--explain", "--json").stdout)
        self.assertEqual((rendered["entries"][0]["status"], rendered["entries"][0]["l0_hash"]),
                         ("unchanged", "PASS"))

    def test_every_status_the_code_emits_is_documented(self):
        code = SCRIPT.read_text(encoding="utf-8") + (ROOT / "path_adapter.py").read_text(encoding="utf-8")
        documented = set(omniagent.EXPLANATIONS["plan"]) | set(omniagent.EXPLANATIONS["adopt"])
        for status in documented:
            self.assertIn(f'"{status}"', code)
        emitted = {"install", "update", "update_adopted", "unchanged", "blocked", "blocked_unmanaged",
                   "blocked_vendor", "blocked_rendered", "conflict", "conflict_missing",
                   "eligible", "eligible_rendered", "mismatch", "unsafe", "managed_elsewhere",
                   "already_managed"}
        self.assertLessEqual(emitted, documented)

    def test_an_undocumented_status_fails_closed(self):
        doc = omniagent.explain([{"target": "t", "name": "n", "status": "something_new"}], "plan")
        entry = doc["entries"][0]
        self.assertEqual((entry["decision"], entry["changes"], entry["l0_hash"]), ("blocked", "nothing", "UNKNOWN"))

    @unittest.skipUnless(hasattr(os, "symlink"), "needs symlink support")
    def test_symlinked_target_entry_is_blocked_with_cause(self):
        make_skill(self.source, "linked")
        outside = make_skill(self.base / "outside", "linked")
        try:
            os.symlink(outside, self.target / "linked", target_is_directory=True)
        except OSError:
            self.skipTest("symlink creation not permitted")
        doc = json.loads(self.run_cli("plan", *self.common(), "--explain", "--json").stdout)
        entry = doc["entries"][0]
        self.assertEqual((entry["status"], entry["cause"], entry["decision"]), ("blocked", "link", "blocked"))


if __name__ == "__main__":
    unittest.main()
