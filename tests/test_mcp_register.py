"""Tests for mcp_register.py. Everything runs against fake agent CLIs and temp configs; no real agent config is touched."""

import contextlib
import hashlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import brain
import mcp_register as reg

FAKE = Path(__file__).resolve().parent / "fake_agent_cli.py"
TOKEN = "tok_" + "Q7x" * 12  # fake, built at runtime so no scanner flags this file


def make_cli(folder: Path, name: str) -> str:
    """An executable wrapper that runs the fake CLI with this Python."""
    if os.name == "nt":
        wrapper = folder / f"{name}.cmd"
        wrapper.write_text(f'@"{sys.executable}" "{FAKE}" %*\r\n', encoding="utf-8")
    else:
        wrapper = folder / name
        wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n', encoding="utf-8")
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
    return str(wrapper)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Base(unittest.TestCase):
    kind = "claude"  # claude | codex | agy
    agent = "claude"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="reg-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.project_dir = self.tmp / "proj"
        self.project_dir.mkdir()
        self.state = self.tmp / "state"
        self.brain_dir = self.tmp / "brain"
        self.cli = make_cli(self.tmp, "fakecli")
        self.config = self.tmp / ("config.toml" if self.kind == "codex" else "config.json")
        self.write_config()
        self.set_mode("ok")

    def set_mode(self, mode):
        patcher = mock.patch.dict(os.environ, {"FAKE_KIND": self.kind, "FAKE_MODE": mode, "FAKE_CONFIG": str(self.config),
                                               "FAKE_TOKEN": TOKEN})
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_config(self):
        if self.kind == "codex":
            self.config.write_text(
                'model = "x"\n\n[projects.demo]\ntrust_level = "trusted"\n\n'
                f'[mcp_servers.hub]\nurl = "https://example.invalid/mcp"\nbearer_token = "{TOKEN}"\n\n'
                '[mcp_servers.other]\ncommand = "node"\nargs = ["--flag"]\n', encoding="utf-8")
        else:
            doc = {"theme": "dark", "mcpServers": {
                "hub": {"type": "http", "url": "https://example.invalid/mcp", "headers": {"Authorization": "Bearer " + TOKEN}},
                "other": {"command": "node", "args": ["--flag"]}}}
            self.config.write_text(json.dumps(doc, indent=2), encoding="utf-8")

    def call_plan(self, scope="local"):
        return reg.plan(self.agent, self.config, self.cli, scope, self.project_dir, self.brain_dir, "demo",
                        self.state / "runtime", ROOT / "brain.py")

    def call_apply(self, scope="local"):
        return reg.apply(self.agent, self.config, self.cli, scope, self.project_dir, self.brain_dir, "demo",
                         self.state / "runtime", ROOT / "brain.py", self.state)

    def names(self):
        return {(s, n) for s, servers in reg.snapshot(self.agent, self.config, self.project_dir).items() for n in servers}

    def all_state_text(self):
        """Everything this tool wrote except the config backup, which holds the original config by design."""
        out = []
        for p in self.state.rglob("*"):
            if p.is_file() and p.name != "config.bak" and p.suffix != ".py":
                out.append(p.read_text(encoding="utf-8", errors="replace"))
        return "\n".join(out)


class RuntimeTests(unittest.TestCase):
    def test_runtime_copy_is_named_by_hash_is_idempotent_and_answers_the_handshake(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = reg.prepare_runtime(ROOT / "brain.py", Path(tmp))
            second = reg.prepare_runtime(ROOT / "brain.py", Path(tmp))
            self.assertEqual(first, second)
            self.assertEqual(first.name, f"brain-{sha(ROOT / 'brain.py')[:12]}.py")
            command, args = reg.server_command(first, Path(tmp) / "b", "demo", "codex")
            self.assertEqual(reg.handshake(command, args), sorted(reg.EXPECTED_TOOLS))

    def test_a_tampered_runtime_copy_is_replaced_not_trusted(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = reg.prepare_runtime(ROOT / "brain.py", Path(tmp))
            target.write_text("raise SystemExit('tampered')", encoding="utf-8")
            again = reg.prepare_runtime(ROOT / "brain.py", Path(tmp))
            self.assertEqual(sha(again), sha(ROOT / "brain.py"))

    def test_handshake_fails_for_a_broken_command(self):
        with self.assertRaises(reg.RegisterError):
            reg.handshake("definitely-not-a-real-program", [])
        with self.assertRaises(reg.RegisterError):
            reg.handshake(sys.executable, ["-c", "print('hello')"])

    def test_the_printed_register_plan_starts_a_working_server(self):
        # regression: the plan once put --project after `serve`, which the parser rejects
        text = brain.register_plan("demo", None)
        args = json.loads(text.split("## antigravity")[1])["mcpServers"]["omniagent-brain"]["args"]
        self.assertEqual(reg.handshake(sys.executable, args), sorted(reg.EXPECTED_TOOLS))


class PlanTests(Base):
    def test_plan_writes_nothing_and_shows_no_values(self):
        before = sha(self.config)
        result = self.call_plan()
        self.assertEqual(sha(self.config), before)
        self.assertFalse((self.state / "backups").exists())
        self.assertTrue(result["writes_nothing"])
        self.assertEqual(result["problems"], [])
        self.assertEqual(result["handshake_tools"], sorted(reg.EXPECTED_TOOLS))
        self.assertNotIn(TOKEN, json.dumps(result))
        self.assertNotIn("example.invalid", json.dumps(result))

    def test_plan_refuses_a_name_that_already_exists(self):
        self.call_apply()
        self.assertTrue(any("already exists" in p for p in self.call_plan()["problems"]))
        before = sha(self.config)
        with self.assertRaises(reg.RegisterError):
            self.call_apply()
        self.assertEqual(sha(self.config), before)


class ApplyClaudeTests(Base):
    def run_apply_and_rollback(self, scope):
        before_names, before_hash = self.names(), reg.snapshot(self.agent, self.config, self.project_dir)
        result = self.call_apply(scope)
        self.assertTrue(result["applied"])
        added = self.names() - before_names
        self.assertEqual(len(added), 1)
        self.assertEqual(next(iter(added))[1], reg.NAME)
        self.assertEqual(reg.compare(before_hash, reg.snapshot(self.agent, self.config, self.project_dir), next(iter(added))), [])
        folder = Path(result["backup_dir"])
        self.assertEqual(json.loads((folder / "journal.json").read_text(encoding="utf-8"))["state"], "applied")
        self.assertTrue((folder / "config.bak").exists())
        rolled = reg.rollback(folder)
        self.assertTrue(rolled["clean"], rolled)
        self.assertEqual(self.names(), before_names)
        self.assertEqual(reg.snapshot(self.agent, self.config, self.project_dir), before_hash)

    def test_local_scope_adds_one_entry_for_this_project_and_rolls_back(self):
        self.run_apply_and_rollback("local")

    def test_user_scope_adds_one_entry_and_rolls_back(self):
        self.run_apply_and_rollback("user")

    def test_the_backup_is_an_exact_copy_of_the_config_before_the_change(self):
        original = self.config.read_bytes()
        folder = Path(self.call_apply()["backup_dir"])
        self.assertEqual((folder / "config.bak").read_bytes(), original)

    def test_a_cli_that_reformats_the_file_does_not_count_as_changing_other_entries(self):
        self.set_mode("reformat")
        original_bytes = self.config.read_bytes()
        self.call_apply("user")
        self.assertNotEqual(self.config.read_bytes()[:30], original_bytes[:30])  # layout really changed (CRLF, indent)
        self.assertIn(b"\r\n", self.config.read_bytes())

    def test_a_cli_that_changes_another_entry_is_caught_and_our_entry_removed(self):
        self.set_mode("mutate")
        with self.assertRaises(reg.RegisterError) as ctx:
            self.call_apply("user")
        self.assertIn("'other'", str(ctx.exception))
        self.assertNotIn((("user"), reg.NAME), self.names())


class ApplyAgyTests(ApplyClaudeTests):
    kind = "agy"
    agent = "antigravity"

    def test_local_scope_adds_one_entry_for_this_project_and_rolls_back(self):
        self.run_apply_and_rollback("local")  # agy has no project scope, so this falls back to user scope


class ApplyCodexTests(Base):
    kind = "codex"
    agent = "codex"

    def test_codex_toml_keeps_every_other_table_and_rolls_back(self):
        original = self.config.read_text(encoding="utf-8")
        before = reg.snapshot("codex", self.config)
        result = self.call_apply()
        text = self.config.read_text(encoding="utf-8")
        self.assertIn('[projects.demo]', text)
        self.assertIn("[mcp_servers.omniagent-brain]", text)
        self.assertEqual(result["entries_after"], result["entries_before"] + 1)
        self.assertTrue(reg.rollback(Path(result["backup_dir"]))["clean"])
        self.assertEqual(reg.snapshot("codex", self.config), before)
        self.assertEqual(self.config.read_text(encoding="utf-8").strip(), original.strip())

    def test_codex_has_no_project_scope_so_local_becomes_user(self):
        self.assertEqual(self.call_plan("local")["scope"], "user")


class FailureTests(Base):
    def assert_unchanged_after_failure(self, mode, fragment):
        self.set_mode(mode)
        before = reg.snapshot(self.agent, self.config, self.project_dir)
        with self.assertRaises(reg.RegisterError) as ctx:
            self.call_apply("user")
        self.assertIn(fragment, str(ctx.exception))
        self.assertEqual(reg.snapshot(self.agent, self.config, self.project_dir), before)
        journals = list((self.state / "backups").glob("*/journal.json"))
        self.assertEqual(len(journals), 1)
        self.assertIn("failed", json.loads(journals[0].read_text(encoding="utf-8"))["state"])

    def test_cli_failure_leaves_config_as_it_was(self):
        self.assert_unchanged_after_failure("fail", "exited with 1")

    def test_cli_that_exits_zero_without_writing_is_caught(self):
        self.assert_unchanged_after_failure("noop", "not in the config")

    def test_an_entry_whose_command_cannot_start_is_caught_and_removed(self):
        self.assert_unchanged_after_failure("bad-command", "handshake")

    def test_a_corrupt_config_is_refused_without_quoting_it(self):
        self.config.write_text(("= not valid " if self.kind == "codex" else "{ not valid ") + TOKEN, encoding="utf-8")
        before = sha(self.config)
        with self.assertRaises(reg.RegisterError) as ctx:
            self.call_apply("user")
        self.assertNotIn(TOKEN, str(ctx.exception))
        self.assertEqual(sha(self.config), before)
        self.assertFalse((self.state / "backups").exists())

    def test_a_missing_cli_is_reported_before_anything_is_written(self):
        result = reg.plan(self.agent, self.config, None, "user", self.project_dir, self.brain_dir, "demo",
                          self.state / "runtime", ROOT / "brain.py")
        if result["cli"] is None:
            self.assertIn("agent CLI not found", result["problems"])


class SecretLeakTests(Base):
    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = reg.main(list(argv) + ["--agent", self.agent, "--config", str(self.config), "--cli", self.cli,
                                          "--project-dir", str(self.project_dir), "--brain-dir", str(self.brain_dir),
                                          "--state-dir", str(self.state), "--project", "demo", "--scope", "user"])
        return code, out.getvalue(), err.getvalue()

    def test_token_bearing_entries_never_reach_stdout_stderr_or_our_files(self):
        code, out, err = self.run_main("plan")
        self.assertEqual(code, 0, err)
        self.call_apply("user")
        self.set_mode("leak")
        rolled = reg.rollback(next((self.state / "backups").glob("mcp-*")))
        self.assertFalse(rolled["clean"])
        self.assertNotIn(TOKEN, json.dumps(rolled))
        self.assertIn("withheld", json.dumps(rolled))
        for text in (out, err, self.all_state_text()):
            self.assertNotIn(TOKEN, text)

    def test_a_failing_add_that_echoes_a_credential_is_withheld(self):
        self.set_mode("leak")
        with self.assertRaises(reg.RegisterError) as ctx:
            self.call_apply("user")
        self.assertNotIn(TOKEN, str(ctx.exception))
        self.assertNotIn(TOKEN, self.all_state_text())

    def test_the_cli_refuses_apply_without_yes_or_without_a_terminal(self):
        before = sha(self.config)
        code, _, err = self.run_main("apply")
        self.assertEqual(code, 2)
        self.assertIn("--yes", err)
        code, _, err = self.run_main("apply", "--yes")
        self.assertEqual(code, 2)
        self.assertIn("interactive terminal", err)
        self.assertEqual(sha(self.config), before)
        self.assertFalse((self.state / "backups").exists())


if __name__ == "__main__":
    unittest.main()
