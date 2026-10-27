"""Beta criterion: adopt, plan and rollback of adoption must not change a single byte of skill folders."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "omniagent.py"


def snapshot(root: Path) -> dict:
    """Every file's bytes plus every directory (so empty directories count too)."""
    result = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        result[rel] = p.read_bytes() if p.is_file() else None
    return result


class ByteIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.source = base / "ssot"
        self.target = base / "agents" / "skills"
        self.state = base / "state"
        for name in ("plain", "templated"):
            skill = self.source / name
            (skill / "scripts").mkdir(parents=True)
            (skill / "scripts" / "run.py").write_bytes(b"print('hi')\r\nprint('caf\xc3\xa9')\n")
            (skill / "empty-dir").mkdir()
        (self.source / "plain" / "SKILL.md").write_bytes(b"# plain\r\nuse .claude/skills here\r\n")
        (self.source / "templated" / "SKILL.md").write_bytes(
            b"# t\nrun {{SKILLS_DIR}}/{{SKILL_NAME}}/scripts/run.py\n")
        # Installed copies: "plain" equals the SSOT; "templated" equals the agents rendering.
        for name in ("plain", "templated"):
            shutil_target = self.target / name
            (shutil_target / "scripts").mkdir(parents=True)
            (shutil_target / "scripts" / "run.py").write_bytes(
                (self.source / name / "scripts" / "run.py").read_bytes())
            (shutil_target / "empty-dir").mkdir()
        (self.target / "plain" / "SKILL.md").write_bytes((self.source / "plain" / "SKILL.md").read_bytes())
        (self.target / "templated" / "SKILL.md").write_bytes(
            b"# t\nrun .agents/skills/templated/scripts/run.py\n")
        self.manifest = base / "adapters.json"
        self.manifest.write_text(json.dumps({
            "version": 1,
            "targets": {"agents": {"SKILLS_DIR": ".agents/skills"}},
            "skills": {"templated": {"files": ["SKILL.md"]}},
        }), encoding="utf-8")

    def run_cli(self, command, *extra, expected=0):
        cmd = [sys.executable, "-I", str(SCRIPT), command]
        if command != "rollback":
            cmd += ["--source", str(self.source), "--target", f"agents={self.target}"]
        cmd += ["--state-dir", str(self.state), *extra]
        cp = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(cp.returncode, expected, cp.stdout + cp.stderr)
        return cp

    def test_full_cycle_leaves_every_byte_untouched(self):
        before = snapshot(self.target)
        adapter = ("--adapter-manifest", str(self.manifest))

        preview = self.run_cli("adopt", *adapter)
        self.assertIn("eligible_rendered", preview.stdout)
        self.assertIn("eligible ", preview.stdout)
        self.assertEqual(snapshot(self.target), before)

        adopted = self.run_cli("adopt", *adapter, "--yes")
        txn = adopted.stdout.split("Adoption transaction: ", 1)[1].splitlines()[0]
        self.assertEqual(snapshot(self.target), before)

        self.assertIn("unchanged", self.run_cli("plan", *adapter).stdout)
        self.assertIn("blocked_rendered", self.run_cli("plan").stdout)
        self.assertEqual(snapshot(self.target), before)

        self.run_cli("rollback", "--transaction", txn, "--yes")
        self.assertEqual(snapshot(self.target), before)
        self.assertEqual(json.loads((self.state / "state.json").read_text(encoding="utf-8"))["managed"], {})


if __name__ == "__main__":
    unittest.main()
