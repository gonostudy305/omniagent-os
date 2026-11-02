"""`inventory --json` (roadmap item 5): read-only provenance, no machine paths or content, honest `unknown`."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_explain import ROOT, SCRIPT, make_skill, snapshot

SECRET = "sk-live-DO-NOT-LEAK-4242"


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / "ssot"
        self.source.mkdir()
        self.target = self.base / "agent" / "skills"
        self.target.mkdir(parents=True)
        self.state = self.base / "state"

    def run_cli(self, *args, expected=0):
        result = subprocess.run([sys.executable, "-I", str(SCRIPT), *args],
                                capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def apply(self):
        self.run_cli("apply", "--source", str(self.source), "--state-dir", str(self.state),
                     "--target", f"claude={self.target}", "--yes")

    def inventory(self, *targets):
        targets = targets or (f"claude={self.target}",)
        args = ["inventory", "--state-dir", str(self.state), "--json"]
        for t in targets:
            args += ["--target", t]
        out = self.run_cli(*args).stdout
        self.assertNotIn(str(self.base), out)
        self.assertNotIn(str(Path.home()), out)
        self.assertNotIn(SECRET, out)
        return json.loads(out)

    def entry(self, doc, name):
        return next(e for e in doc["entries"] if e["name"] == name)

    def last_transaction(self):
        files = sorted((self.state / "transactions").glob("*.json"), key=lambda f: f.stat().st_mtime_ns)
        return files[-1].stem

    def test_installed_skill_has_origin_transaction_and_matching_hash(self):
        make_skill(self.source, "alpha")
        self.apply()
        e = self.entry(self.inventory(), "alpha")
        self.assertEqual((e["target"], e["kind"], e["origin"]), ("claude", "skill", "installed"))
        self.assertEqual(e["transaction"], self.last_transaction())
        self.assertEqual(e["integrity"], "matches_record")
        self.assertEqual(len(e["hash"]), 64)

    def test_adopted_skill_reports_adoption_transaction(self):
        make_skill(self.source, "alpha")
        make_skill(self.target, "alpha")
        self.run_cli("adopt", "--source", str(self.source), "--state-dir", str(self.state),
                     "--target", f"claude={self.target}", "--yes")
        e = self.entry(self.inventory(), "alpha")
        self.assertEqual((e["origin"], e["integrity"]), ("adopted", "matches_record"))
        self.assertEqual(e["transaction"], self.last_transaction())

    def test_unmanaged_and_edited_and_missing(self):
        make_skill(self.source, "alpha")
        make_skill(self.source, "beta")
        self.apply()
        (self.target / "alpha" / "SKILL.md").write_text("edited", encoding="utf-8")
        import shutil
        shutil.rmtree(self.target / "beta")
        make_skill(self.target, "gamma")
        (self.target / "not-a-skill").mkdir()
        doc = self.inventory()
        self.assertEqual(self.entry(doc, "alpha")["integrity"], "differs_from_record")
        self.assertEqual((self.entry(doc, "beta")["kind"], self.entry(doc, "beta")["hash"]), ("missing", "unknown"))
        gamma = self.entry(doc, "gamma")
        self.assertEqual((gamma["origin"], gamma["transaction"], gamma["integrity"]), ("unmanaged", None, "no_record"))
        self.assertEqual(self.entry(doc, "not-a-skill")["kind"], "not_a_skill")
        self.assertEqual(doc["summary"]["by_origin"], {"installed": 2, "unmanaged": 2})

    def test_credential_like_file_is_unknown_and_never_leaked(self):
        skill = make_skill(self.target, "leaky")
        (skill / ".env").write_text(f"API_KEY={SECRET}\n", encoding="utf-8")
        (skill / "id.pem").write_text(SECRET, encoding="utf-8")
        out = self.run_cli("inventory", "--state-dir", str(self.state), "--target", f"claude={self.target}",
                           "--json").stdout
        for leak in (SECRET, ".env", "id.pem", "API_KEY"):
            self.assertNotIn(leak, out)
        e = self.entry(json.loads(out), "leaky")
        self.assertEqual((e["hash"], e["integrity"], e["reason"]), ("unknown", "unknown", "credential_like_file"))

    def test_managed_skill_that_gains_a_credential_file_is_unknown_not_matching(self):
        make_skill(self.source, "alpha")
        self.apply()
        (self.target / "alpha" / ".env").write_text(SECRET, encoding="utf-8")
        e = self.entry(self.inventory(), "alpha")
        self.assertEqual((e["origin"], e["hash"], e["integrity"]), ("installed", "unknown", "unknown"))

    def test_link_at_target_is_reported_without_following(self):
        outside = make_skill(self.base / "outside", "elsewhere")
        try:
            os.symlink(outside, self.target / "linked", target_is_directory=True)
        except OSError:
            self.skipTest("symlink creation not permitted here")
        e = self.entry(self.inventory(), "linked")
        self.assertEqual((e["kind"], e["hash"], e["integrity"]), ("link", "unknown", "unknown"))

    def test_invalid_names_are_not_printed(self):
        make_skill(self.target, "bad name with spaces")
        doc = self.inventory()
        self.assertEqual([e["name"] for e in doc["entries"]], ["unknown"])
        self.assertNotIn("bad name", json.dumps(doc))

    def test_target_roots_are_named_not_pathed(self):
        doc = self.inventory(f"claude={self.target}", f"codex={self.base / 'absent'}")
        self.assertEqual(doc["targets"], [{"target": "claude", "status": "ok"}, {"target": "codex", "status": "not_found"}])

    def test_inventory_writes_nothing(self):
        make_skill(self.source, "alpha")
        self.apply()
        make_skill(self.target, "gamma")
        before = snapshot(self.state, self.target)
        self.inventory()
        self.run_cli("inventory", "--state-dir", str(self.state), "--target", f"claude={self.target}")
        self.assertEqual(snapshot(self.state, self.target), before)
        fresh = self.base / "never-created"
        self.run_cli("inventory", "--state-dir", str(fresh), "--target", f"claude={self.target}", "--json")
        self.assertFalse(fresh.exists())

    def test_output_is_deterministic(self):
        for name in ("beta", "alpha"):
            make_skill(self.target, name)
        self.assertEqual(self.inventory(), self.inventory())
        self.assertEqual([e["name"] for e in self.inventory()["entries"]], ["alpha", "beta"])

    def test_corrupt_state_fails_closed(self):
        self.state.mkdir()
        (self.state / "state.json").write_text("[]", encoding="utf-8")
        self.run_cli("inventory", "--state-dir", str(self.state), "--target", f"claude={self.target}", expected=2)

    def test_requires_explicit_target(self):
        self.run_cli("inventory", "--state-dir", str(self.state), expected=2)


if __name__ == "__main__":
    unittest.main()
