"""scripts/sandbox_native_mcp.py must point every profile variable at the throwaway folder."""

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("sandbox_native_mcp", ROOT / "scripts" / "sandbox_native_mcp.py")
sandbox = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sandbox)

sys.path.insert(0, str(ROOT / "scripts"))
import sandbox_register_e2e as lifecycle


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

    def test_unknown_credentials_startup_injection_and_config_redirects_are_not_inherited(self):
        real = {"CLAUDE_CODE_OAUTH_TOKEN": "synthetic", "ANTHROPIC_AUTH_TOKEN": "synthetic",
                "AWS_SHARED_CREDENTIALS_FILE": "/real/credentials", "GOOGLE_APPLICATION_CREDENTIALS": "/real/google",
                "NODE_OPTIONS": "--require /real/startup.js", "PYTHONPATH": "/real/python",
                "XDG_CONFIG_HOME": "/real/config", "TMP": "/real/tmp", "PATH": "runtime-path"}
        with tempfile.TemporaryDirectory() as temp, mock.patch.dict(os.environ, real, clear=True):
            home = Path(temp) / "home"
            env = sandbox.sandbox_env(home)
            for key in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_AUTH_TOKEN", "AWS_SHARED_CREDENTIALS_FILE",
                        "GOOGLE_APPLICATION_CREDENTIALS", "NODE_OPTIONS", "PYTHONPATH"):
                self.assertNotIn(key, env)
            for key in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "TEMP", "TMP", "TMPDIR"):
                self.assertTrue(Path(env[key]).is_relative_to(home), key)

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

    def test_disposable_project_has_its_own_git_root_and_ignores_real_git_config(self):
        with sandbox.temporary_profile("test-git-root-") as temp:
            base = Path(temp)
            env = sandbox.sandbox_env(base / "home")
            project = base / "project"
            sandbox.create_project(project, env)
            code, root = sandbox.run(["git", "rev-parse", "--show-toplevel"], env, project)
            self.assertEqual(code, 0)
            self.assertEqual(Path(root).resolve(), project.resolve())
            self.assertNotEqual(Path(root).resolve(), ROOT)
            self.assertTrue(Path(env["GIT_CONFIG_GLOBAL"]).is_relative_to(base / "home"))
            self.assertEqual(env["GIT_CONFIG_NOSYSTEM"], "1")


class SandboxLifecycleTests(unittest.TestCase):
    def test_propose_passes_explicit_clean_environment_and_project_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project = base / "project"
            env = sandbox.sandbox_env(base / "home")
            poisoned = {"ANTHROPIC_API_KEY": "synthetic", "OPENAI_API_KEY": "synthetic",
                        "CLAUDE_CONFIG_DIR": str(base / "ambient-claude"), "CODEX_HOME": str(base / "ambient-codex")}
            with mock.patch.dict(os.environ, poisoned), mock.patch.object(lifecycle.subprocess, "run") as run:
                run.return_value.returncode = 0
                self.assertEqual(lifecycle.propose_through({"command": "python", "args": ["brain.py"]},
                                                          "test", env=env, cwd=project), 0)
                self.assertIs(run.call_args.kwargs["env"], env)
                self.assertEqual(run.call_args.kwargs["cwd"], project)
                self.assertNotIn("ANTHROPIC_API_KEY", run.call_args.kwargs["env"])
                self.assertNotIn("OPENAI_API_KEY", run.call_args.kwargs["env"])

    def test_native_list_get_and_propose_keep_explicit_scenario_environment(self):
        for agent in ("claude", "codex"):
            with self.subTest(agent=agent), tempfile.TemporaryDirectory() as temp, \
                    mock.patch.dict(os.environ, dict(os.environ), clear=True):
                base = Path(temp)
                project = base / "project"
                scope = "local" if agent == "claude" else "user"
                where = lifecycle.reg._scope_name(agent, scope, project)
                entry = {"command": "python", "args": ["brain.py"]}
                poisoned = {"ANTHROPIC_API_KEY": "synthetic", "OPENAI_API_KEY": "synthetic",
                            "CLAUDE_CONFIG_DIR": str(base / "ambient-claude"), "CODEX_HOME": str(base / "ambient-codex")}
                plans = [dict(problems=[], original_sha256="reviewed", entry_count=0, setting_count=0),
                         dict(problems=["already exists"], original_sha256="reviewed")]
                calls = []

                def plan(*args, **kwargs):
                    # Poison ambient state after scenario captured its sanitized env.
                    os.environ.update(poisoned)
                    return plans.pop(0)

                def run(cmd, **kwargs):
                    calls.append(cmd)
                    self.assertIn("env", kwargs)
                    self.assertIn("cwd", kwargs)
                    self.assertEqual(kwargs["cwd"], project)
                    env = kwargs["env"]
                    self.assertNotIn("ANTHROPIC_API_KEY", env)
                    self.assertNotIn("OPENAI_API_KEY", env)
                    self.assertEqual(Path(env["CLAUDE_CONFIG_DIR"]), base / "home" / ".claude")
                    self.assertEqual(Path(env["CODEX_HOME"]), base / "home" / ".codex")
                    if len(calls) == 1:
                        self.assertEqual(cmd[1:3], ["mcp", "get" if agent == "codex" else "list"])
                        return mock.Mock(returncode=0, stdout="omniagent-brain Connected\n")
                    self.assertEqual(cmd, ["python", "brain.py"])
                    raise RuntimeError("verified both subprocess calls")

                with mock.patch.object(lifecycle.sandbox, "create_project"), \
                        mock.patch.object(lifecycle.reg, "plan", side_effect=plan), \
                        mock.patch.object(lifecycle.reg, "apply", side_effect=[
                            dict(applied=True, entries_before=0, entries_after=1, settings_added=[], scope=scope),
                            lifecycle.reg.RegisterError("already exists")]), \
                        mock.patch.object(lifecycle.reg, "read_entries", return_value={where: {lifecycle.reg.NAME: entry}}), \
                        mock.patch.object(lifecycle.subprocess, "run", side_effect=run):
                    with self.assertRaisesRegex(RuntimeError, "verified both subprocess calls"):
                        lifecycle.scenario(agent, "native-cli", agent, base)
                self.assertEqual(len(calls), 2)

    def test_theme_scenario_expects_preflight_refusal_without_cli_or_backup(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch.dict(os.environ, dict(os.environ), clear=True), \
                mock.patch.object(lifecycle.reg, "handshake", return_value=sorted(lifecycle.reg.EXPECTED_TOOLS)):
            rows = lifecycle.scenario("claude", "unused-cli", "claude-theme", Path(temp))
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(row["status"] == "PASS" for row in rows), rows)

    def test_config_drift_after_reviewed_plan_is_rejected_before_native_cli(self):
        original_plan = lifecycle.reg.plan
        calls = 0

        def plan_then_edit(*args, **kwargs):
            nonlocal calls
            result = original_plan(*args, **kwargs)
            calls += 1
            if calls == 1:
                config = args[1]
                doc = lifecycle.json.loads(config.read_text(encoding="utf-8"))
                doc["externalSetting"] = "new"
                config.write_text(lifecycle.json.dumps(doc), encoding="utf-8")
            return result

        with tempfile.TemporaryDirectory() as temp, mock.patch.dict(os.environ, dict(os.environ), clear=True), \
                mock.patch.object(lifecycle.reg, "handshake", return_value=sorted(lifecycle.reg.EXPECTED_TOOLS)), \
                mock.patch.object(lifecycle.reg, "plan", side_effect=plan_then_edit), \
                mock.patch.object(lifecycle.reg, "run_cli", side_effect=AssertionError("must reject reviewed SHA drift")) as cli:
            with self.assertRaisesRegex(lifecycle.reg.RegisterError, "changed since the plan"):
                lifecycle.scenario("claude", "unused-cli", "claude", Path(temp))
            cli.assert_not_called()


if __name__ == "__main__":
    unittest.main()
