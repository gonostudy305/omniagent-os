"""Roadmap item 6: the full skill lifecycle at paths beyond the 260-character MAX_PATH.

On Windows this needs LongPathsEnabled=1; when it is off the test is skipped with that
reason rather than passing, so the result is never mistaken for coverage.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_kill import CHILD, ROOT, SCRIPT


def long_paths_enabled() -> bool:
    if os.name != "nt":
        return True
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem") as key:
            return winreg.QueryValueEx(key, "LongPathsEnabled")[0] == 1
    except OSError:
        return False


@unittest.skipUnless(long_paths_enabled(), "Windows LongPathsEnabled is off; long paths NOT_VERIFIED")
class LongPathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.source = base / "ssot"
        self.state = base / "state"
        self.target = base.joinpath(*["d" * 40] * 6, "skills")
        self.nested = Path("references", "r" * 60, "f" * 80 + ".md")
        self.assertGreater(len(str(self.target)), 260)
        self.assertGreater(len(str(self.target / "alpha" / self.nested)), 400)

    def make_skill(self, name, text):
        skill = self.source / name
        (skill / self.nested).parent.mkdir(parents=True, exist_ok=True)
        (skill / "SKILL.md").write_text(f"---\nname: {name}\n---\n{text}\n", encoding="utf-8")
        (skill / self.nested).write_text(text * 2000, encoding="utf-8")

    def run_cli(self, *args, kill=None, expected=0):
        if kill:
            cmd = [sys.executable, "-I", "-c", CHILD.format(root=str(ROOT)), *args]
            env = {**os.environ, "KILL_MODE": kill}
        else:
            cmd = [sys.executable, "-I", str(SCRIPT), *args]
            env = None
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=env)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result.stdout

    def common(self):
        return ["--source", str(self.source), "--state-dir", str(self.state), "--target", f"claude={self.target}"]

    def status(self):
        out = self.run_cli("plan", *self.common())
        return {line.split()[2]: line.split()[1] for line in out.splitlines() if len(line.split()) == 3}

    def text(self, name):
        return (self.target / name / "SKILL.md").read_text(encoding="utf-8")

    def test_install_update_and_rollback(self):
        self.make_skill("alpha", "v1")
        self.run_cli("apply", *self.common(), "--yes")
        self.assertEqual(self.status(), {"alpha": "unchanged"})
        self.assertEqual((self.target / "alpha" / self.nested).read_text(encoding="utf-8"), "v1" * 2000)
        self.make_skill("alpha", "v2")
        self.run_cli("apply", *self.common(), "--yes")
        self.assertIn("v2", self.text("alpha"))
        txn = sorted((self.state / "transactions").glob("*.json"), key=lambda f: f.stat().st_mtime_ns)[-1].stem
        self.run_cli("rollback", "--state-dir", str(self.state), "--transaction", txn, "--yes")
        self.assertIn("v1", self.text("alpha"))
        self.assertEqual((self.target / "alpha" / self.nested).read_text(encoding="utf-8"), "v1" * 2000)
        self.assertEqual(self.status(), {"alpha": "update"})

    def test_killed_update_is_recovered(self):
        for name in ("alpha", "beta"):
            self.make_skill(name, "v1")
        self.run_cli("apply", *self.common(), "--yes")
        for name in ("alpha", "beta"):
            self.make_skill(name, "v2")
        self.run_cli("apply", *self.common(), "--yes", kill="second_copy", expected=137)
        self.assertIn("v2", self.text("alpha"))
        self.run_cli("recover", "--state-dir", str(self.state), "--yes")
        self.assertIn("v1", self.text("alpha"))
        self.assertIn("v1", self.text("beta"))
        self.assertEqual(self.status(), {"alpha": "update", "beta": "update"})
        files = sorted((self.state / "transactions").glob("*.json"), key=lambda f: f.stat().st_mtime_ns)
        self.assertEqual(json.loads(files[-1].read_text(encoding="utf-8"))["status"], "recovered_reverted")


if __name__ == "__main__":
    unittest.main()
