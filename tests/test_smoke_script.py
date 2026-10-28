"""scripts/smoke_beta.ps1 on throwaway fixtures (Windows PowerShell only; never touches agent folders)."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "smoke_beta.ps1"
POWERSHELL = shutil.which("powershell")


@unittest.skipUnless(os.name == "nt" and POWERSHELL and SCRIPT.exists(), "needs Windows PowerShell")
class SmokeScriptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.source = base / "ssot"
        self.target = base / "agent" / "skills"
        self.reports = base / "reports"
        for name in ("alpha", "beta"):
            for root in (self.source, self.target):
                (root / name / "scripts").mkdir(parents=True)
                (root / name / "scripts" / "run.py").write_text("print(1)\n", encoding="utf-8")
        (self.source / "alpha" / "SKILL.md").write_text(
            "---\nname: alpha\ndescription: first\n---\nrun {{SKILLS_DIR}}/alpha/scripts/run.py\n", encoding="utf-8")
        (self.target / "alpha" / "SKILL.md").write_text(
            "---\nname: alpha\ndescription: first\n---\nrun .agents/skills/alpha/scripts/run.py\n", encoding="utf-8")
        for root in (self.source, self.target):
            (root / "beta" / "SKILL.md").write_text("---\nname: beta\ndescription: second\n---\nuse scripts/run.py\n",
                                                    encoding="utf-8")
        self.manifest = base / "adapters.json"
        self.manifest.write_text(json.dumps({
            "version": 1, "targets": {"agents": {"SKILLS_DIR": ".agents/skills"}},
            "skills": {"alpha": {"files": ["SKILL.md"]}}}), encoding="utf-8")

    def run_script(self, *extra, with_adapter=True, env=None):
        cmd = [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT),
               "-Target", str(self.target), "-Skills", "alpha,beta", "-ReportDir", str(self.reports)]
        if with_adapter:
            cmd += ["-Source", str(self.source), "-Manifest", str(self.manifest)]
        cmd += list(extra)
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)

    def statuses(self):
        report = sorted(self.reports.glob("smoke_beta_*.json"))[-1]
        data = json.loads(report.read_text(encoding="utf-8-sig"))
        return data, {r["id"]: r["status"] for r in data["results"]}

    def test_read_only_passes_and_reports_what_it_cannot_verify(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data, status = self.statuses()
        self.assertEqual(status["skill.alpha.hashes"], "PASS")
        self.assertEqual(status["agent.alpha.loaded"], "NOT VERIFIED")
        self.assertEqual(status["agent.beta.task"], "NOT VERIFIED")
        self.assertFalse((self.target.parent.parent / "state").exists())

    def test_drifted_installed_skill_fails_with_exit_1(self):
        (self.target / "beta" / "SKILL.md").write_text("---\nname: beta\ndescription: second\n---\nedited\n",
                                                       encoding="utf-8")
        result = self.run_script()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(self.statuses()[1]["skill.beta.hashes"], "FAIL")

    def test_unrendered_placeholder_and_missing_skill_fail(self):
        (self.target / "alpha" / "SKILL.md").write_text(
            "---\nname: alpha\ndescription: first\n---\nrun {{SKILLS_DIR}}/x\n", encoding="utf-8")
        shutil.rmtree(self.target / "beta")
        result = self.run_script()
        self.assertEqual(result.returncode, 1)
        status = self.statuses()[1]
        self.assertEqual(status["skill.alpha.placeholders"], "FAIL")
        self.assertEqual(status["skill.beta.present"], "FAIL")

    def test_missing_dependency_skill_fails(self):
        (self.target / "beta" / "SKILL.md").write_text(
            "---\nname: beta\ndescription: second\n---\nnode skills/gamma/scripts/x.js\n", encoding="utf-8")
        (self.source / "beta" / "SKILL.md").write_text(
            "---\nname: beta\ndescription: second\n---\nnode skills/gamma/scripts/x.js\n", encoding="utf-8")
        result = self.run_script()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.statuses()[1]["skill.beta.dependencies"], "FAIL")

    def test_junction_inside_a_skill_fails(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        link = self.target / "beta" / "linked"
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True)
        if made.returncode != 0:
            self.skipTest("cannot create a junction here")
        result = self.run_script()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.statuses()[1]["skill.beta.links"], "FAIL")

    def test_missing_adapter_inputs_skip_instead_of_assuming_local_files(self):
        result = self.run_script(with_adapter=False)
        self.assertEqual(result.returncode, 0, result.stdout)
        status = self.statuses()[1]
        self.assertEqual(status["source.layout"], "SKIP")
        self.assertEqual(status["hash.plan"], "SKIP")

    def test_trial_mode_adopts_and_rolls_back_without_changing_a_byte(self):
        before = {p.relative_to(self.target).as_posix(): p.read_bytes()
                  for p in self.target.rglob("*") if p.is_file()}
        result = self.run_script("-Mode", "Trial")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        status = self.statuses()[1]
        for key in ("trial.adopt", "trial.plan_after_adopt", "trial.rollback", "trial.alpha.bytes", "trial.beta.bytes"):
            self.assertEqual(status[key], "PASS", key)
        after = {p.relative_to(self.target).as_posix(): p.read_bytes()
                 for p in self.target.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_report_hides_personal_paths(self):
        self.run_script()
        report = sorted(self.reports.glob("smoke_beta_*.json"))[-1].read_text(encoding="utf-8-sig")
        self.assertNotIn(str(Path.home()), report)
        self.assertNotIn(str(ROOT), report)

    def test_missing_python_is_an_environment_error_exit_2(self):
        env = {k: v for k, v in os.environ.items() if k.upper() != "PATH"}
        env["PATH"] = str(Path(POWERSHELL).parent)  # PowerShell itself, but no Python
        result = self.run_script(env=env)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
