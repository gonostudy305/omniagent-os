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
# Cmdlets exported by Microsoft.PowerShell.Utility in Windows PowerShell 5.1, plus Get-FileHash
# (a script function there, a cmdlet in PowerShell 7).
UTILITY_CMDLETS = (
    "Add-Member,Add-Type,Clear-Variable,Compare-Object,ConvertFrom-Csv,ConvertFrom-Json,ConvertFrom-String,"
    "ConvertFrom-StringData,Convert-String,ConvertTo-Csv,ConvertTo-Html,ConvertTo-Json,ConvertTo-Xml,"
    "Debug-Runspace,Disable-PSBreakpoint,Disable-RunspaceDebug,Enable-PSBreakpoint,Enable-RunspaceDebug,"
    "Export-Alias,Export-Clixml,Export-Csv,Export-FormatData,Export-PSSession,Format-Custom,Format-List,"
    "Format-Table,Format-Wide,Get-Alias,Get-Culture,Get-Date,Get-Event,Get-EventSubscriber,Get-FormatData,"
    "Get-Host,Get-Member,Get-PSBreakpoint,Get-PSCallStack,Get-Random,Get-Runspace,Get-RunspaceDebug,"
    "Get-TraceSource,Get-TypeData,Get-UICulture,Get-Unique,Get-Variable,Group-Object,Import-Alias,"
    "Import-Clixml,Import-Csv,Import-LocalizedData,Import-PSSession,Invoke-Expression,Invoke-RestMethod,"
    "Invoke-WebRequest,Measure-Command,Measure-Object,New-Alias,New-Event,New-Object,New-TimeSpan,"
    "New-Variable,Out-File,Out-GridView,Out-Printer,Out-String,Read-Host,Register-EngineEvent,"
    "Register-ObjectEvent,Remove-Event,Remove-PSBreakpoint,Remove-TypeData,Remove-Variable,Select-Object,"
    "Select-String,Select-Xml,Send-MailMessage,Set-Alias,Set-Date,Set-PSBreakpoint,Set-TraceSource,"
    "Set-Variable,Show-Command,Sort-Object,Start-Sleep,Tee-Object,Trace-Command,Unblock-File,"
    "Unregister-Event,Update-FormatData,Update-List,Update-TypeData,Wait-Debugger,Wait-Event,Write-Debug,"
    "Write-Error,Write-Host,Write-Information,Write-Output,Write-Progress,Write-Verbose,Write-Warning,"
    "Get-FileHash").split(",")


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
        self.state_root = Path(self.temp.name) / "smoke-state"
        cmd = [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT),
               "-Target", str(self.target), "-Skills", "alpha,beta", "-ReportDir", str(self.reports),
               "-StateRoot", str(self.state_root)]
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

    def test_trial_works_when_launched_from_powershell_7(self):
        # GitHub's Windows runner runs steps in pwsh 7, whose PSModulePath is inherited by
        # powershell.exe. A Core-only Microsoft.PowerShell.Utility first on the path hides
        # Get-FileHash in Windows PowerShell 5.1 (CI run 40813644887). Same for a user who
        # starts the script from a PowerShell 7 terminal.
        shadow = Path(self.temp.name) / "ps7-modules" / "Microsoft.PowerShell.Utility"
        shadow.mkdir(parents=True)
        (shadow / "Microsoft.PowerShell.Utility.psd1").write_text(
            "@{ GUID = '1DA87E53-152B-403E-98DC-74D7B4D63D59'; ModuleVersion = '7.0.0.0'; "
            "CompatiblePSEditions = @('Core'); PowerShellVersion = '3.0'; "
            "NestedModules = 'Microsoft.PowerShell.Commands.Utility.dll'; "
            # Like the real PS7 manifest, it claims every Utility cmdlet; otherwise the first
            # Write-Host or Get-Date would import the real 5.1 module and hide the problem.
            f"CmdletsToExport = @({', '.join(repr(c) for c in UTILITY_CMDLETS)}); "
            "FunctionsToExport = @() }\n", encoding="utf-8")
        # os.environ keys are upper-cased on Windows; drop every spelling so only ours is passed.
        original = os.environ.get("PSModulePath", "")
        env = {k: v for k, v in os.environ.items() if k.upper() != "PSMODULEPATH"}
        env["PSModulePath"] = str(shadow.parent) + os.pathsep + original
        result = self.run_script("-Mode", "Trial", env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.statuses()[1]["trial.alpha.bytes"], "PASS")

    def test_trial_state_is_unique_kept_as_evidence_and_outside_the_repo(self):
        repo_state = ROOT / ".local" / "smoke-state"
        before = set(repo_state.iterdir()) if repo_state.is_dir() else set()
        for _ in range(2):  # two runs within the same second must not share a state folder
            self.assertEqual(self.run_script("-Mode", "Trial").returncode, 0)
        kept = [p for p in self.state_root.iterdir() if p.is_dir()]
        self.assertEqual(len(kept), 2)
        for folder in kept:  # evidence of what was adopted and rolled back stays for review
            journals = list((folder / "transactions").glob("*.json"))
            self.assertEqual([json.loads(j.read_text(encoding="utf-8"))["status"] for j in journals],
                             ["rolled_back"])
        after = set(repo_state.iterdir()) if repo_state.is_dir() else set()
        self.assertEqual(before, after)

    def test_read_only_plan_never_reads_or_leaves_a_shared_temp_state(self):
        stale = Path(os.environ["TEMP"]) / "omniagent-smoke-readonly"
        self.assertFalse(stale.exists(), "a stale shared state folder from an old run exists; inspect it")
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(stale.exists())
        self.assertEqual(list(Path(os.environ["TEMP"]).glob("omniagent-smoke-readonly*")), [])

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
