"""scripts/sandbox_native_mcp.py must point every profile variable at the throwaway folder."""

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("sandbox_native_mcp", ROOT / "scripts" / "sandbox_native_mcp.py")
sandbox = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sandbox)


class SandboxEnvTests(unittest.TestCase):
    def test_profile_variables_point_into_the_sandbox_and_keys_are_dropped(self):
        real = {"USERPROFILE": "C:\\Users\\real", "home": "/home/real", "AppData": "x", "LOCALAPPDATA": "y",
                "CLAUDE_CONFIG_DIR": "z", "codex_home": "w", "ANTHROPIC_API_KEY": "k1", "OPENAI_API_KEY": "k2",
                "PATH": os.environ.get("PATH", "")}
        with tempfile.TemporaryDirectory() as temp, mock.patch.dict(os.environ, real, clear=True):
            home = Path(temp) / "home"
            env = sandbox.sandbox_env(home)
            upper = {}
            for key, value in env.items():
                self.assertNotIn(key.upper(), upper, f"{key} passed twice")  # Windows keys are case-insensitive
                upper[key.upper()] = value
            for key in ("USERPROFILE", "HOME", "APPDATA", "LOCALAPPDATA", "CLAUDE_CONFIG_DIR", "CODEX_HOME"):
                self.assertTrue(Path(upper[key]).is_relative_to(home), key)
            self.assertNotIn("ANTHROPIC_API_KEY", upper)
            self.assertNotIn("OPENAI_API_KEY", upper)
            self.assertEqual(upper["PATH"], real["PATH"])

    def test_fingerprint_uses_stat_only(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(sandbox, "REAL_HOME", Path(temp)):
            secret = Path(temp) / ".codex" / "auth.json"
            secret.parent.mkdir()
            secret.write_text("{}", encoding="utf-8")
            with mock.patch.object(Path, "read_bytes", side_effect=AssertionError("read")), \
                    mock.patch.object(Path, "read_text", side_effect=AssertionError("read")), \
                    mock.patch("builtins.open", side_effect=AssertionError("open")):
                prints = sandbox.fingerprint()
            self.assertEqual(prints[".codex/auth.json"][0], 2)
            self.assertIsNone(prints[".claude.json"])


if __name__ == "__main__":
    unittest.main()
