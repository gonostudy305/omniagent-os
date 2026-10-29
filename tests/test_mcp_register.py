"""Tests for mcp_register.py. Everything runs against fake agent CLIs and temp configs; no real agent config is touched."""

import contextlib
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import threading
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

    def external_edit(self) -> bytes:
        """Another program changes the config: one new server."""
        if self.kind == "codex":
            self.config.write_text(self.config.read_text(encoding="utf-8") + '\n[mcp_servers.late]\ncommand = "late"\n',
                                   encoding="utf-8")
        else:
            doc = json.loads(self.config.read_text(encoding="utf-8-sig"))
            doc.setdefault("mcpServers", {})["late"] = {"command": "late"}
            self.config.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        return self.config.read_bytes()

    def journal(self, folder=None):
        folder = folder or next((self.state / "backups").glob("mcp-*"))
        return json.loads((Path(folder) / "journal.json").read_text(encoding="utf-8"))

    def locks(self):
        return list((self.state / "locks").glob("*.lock")) if (self.state / "locks").exists() else []

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
        original_bytes = self.config.read_bytes()
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
        self.assertEqual(rolled["mode"], "exact")
        self.assertEqual(self.config.read_bytes(), original_bytes)
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
        self.assertEqual(self.config.read_text(encoding="utf-8"), original)

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
        rolled = reg.rollback(next((self.state / "backups").glob("mcp-*")), entry_only=True)
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
        self.assertIn("--expect-sha", err)
        code, _, err = self.run_main("apply", "--yes", "--expect-sha", sha(self.config))
        self.assertEqual(code, 2)
        self.assertIn("interactive terminal", err)
        self.assertEqual(sha(self.config), before)
        self.assertFalse((self.state / "backups").exists())


class Crash(BaseException):
    """Stands in for a killed process: the tool's own error handling does not catch it."""


class PlanContractTests(Base):
    def test_plan_shows_target_hashes_entry_and_recovery_steps(self):
        result = self.call_plan("user")
        self.assertTrue(result["config_exists"])
        self.assertEqual(result["original_sha256"], sha(self.config))
        self.assertEqual(result["proposed_entry"]["name"], reg.NAME)
        self.assertEqual(result["proposed_entry"]["args"][-3:], ["serve", "--agent", self.agent])
        self.assertIsNone(result["expected_output_sha256"])
        self.assertIn("not predictable", result["expected_output_note"])
        self.assertTrue(any("rollback --entry-only" in s for s in result["backup_and_rollback"]))
        self.assertIn(f"--expect-sha {sha(self.config)}", result["apply_with"])
        self.assertEqual(result["existing_entries"]["user"], ["hub", "other"])
        self.assertNotIn(TOKEN, json.dumps(result))
        self.assertNotIn("example.invalid", json.dumps(result))

    def test_plan_for_a_missing_config_says_so_and_creates_nothing(self):
        self.config.unlink()
        result = self.call_plan("user")
        self.assertFalse(result["config_exists"])
        self.assertEqual(result["original_sha256"], reg.ABSENT)
        self.assertEqual(result["problems"], [])
        self.assertFalse(self.config.exists())


class MalformedConfigTests(Base):
    def assert_refused(self, raw: bytes, fragment: str):
        self.config.write_bytes(raw)
        with self.assertRaises(reg.RegisterError) as ctx:
            self.call_apply("user")
        self.assertIn(fragment, str(ctx.exception))
        self.assertNotIn(TOKEN, str(ctx.exception))
        self.assertEqual(self.config.read_bytes(), raw)
        self.assertFalse((self.state / "backups").exists())

    def test_utf16_is_refused(self):
        self.assert_refused(json.dumps({"mcpServers": {}, "t": TOKEN}).encode("utf-16"), "not UTF-8")

    def test_a_json_array_is_refused(self):
        self.assert_refused(json.dumps([TOKEN]).encode("utf-8"), "not a JSON object")

    def test_servers_in_an_unknown_shape_are_refused(self):
        self.assert_refused(json.dumps({"mcpServers": [TOKEN]}).encode("utf-8"), "shape")
        self.assert_refused(json.dumps({"mcpServers": {"hub": TOKEN}}).encode("utf-8"), "shape")

    def test_projects_in_an_unknown_shape_are_refused(self):
        self.assert_refused(json.dumps({"projects": {"x": TOKEN}}).encode("utf-8"), "shape")


class MalformedCodexTests(MalformedConfigTests):
    kind = "codex"
    agent = "codex"

    def test_utf16_is_refused(self):
        self.assert_refused(f'model = "{TOKEN}"\n'.encode("utf-16"), "not UTF-8")

    def test_a_json_array_is_refused(self):
        self.assert_refused(f'mcp_servers = ["{TOKEN}"]\n'.encode("utf-8"), "shape")

    def test_servers_in_an_unknown_shape_are_refused(self):
        self.assert_refused(f'[mcp_servers]\nhub = "{TOKEN}"\n'.encode("utf-8"), "shape")

    def test_projects_in_an_unknown_shape_are_refused(self):
        self.assert_refused(f'mcp_servers = "{TOKEN}"\n'.encode("utf-8"), "shape")


class ByteExactTests(Base):
    def test_bom_and_crlf_come_back_byte_for_byte(self):
        doc = {"theme": "dark", "mcpServers": {"other": {"command": "node", "args": ["--flag"]}}}
        raw = b"\xef\xbb\xbf" + json.dumps(doc, indent=4).replace("\n", "\r\n").encode("utf-8")
        self.config.write_bytes(raw)
        result = self.call_apply("user")
        self.assertNotEqual(self.config.read_bytes(), raw)  # the CLI rewrote it without BOM and CRLF
        self.assertEqual(result["original_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertTrue(reg.rollback(Path(result["backup_dir"]))["clean"])
        self.assertEqual(self.config.read_bytes(), raw)

    def test_a_config_that_did_not_exist_is_removed_again(self):
        self.config.unlink()
        result = self.call_apply("user")
        self.assertEqual(result["original_sha256"], reg.ABSENT)
        self.assertTrue(self.config.exists())
        rolled = reg.rollback(Path(result["backup_dir"]))
        self.assertTrue(rolled["clean"], rolled)
        self.assertFalse(self.config.exists())

    def test_a_created_config_that_was_edited_afterwards_is_kept(self):
        self.config.unlink()
        result = self.call_apply("user")
        edited = self.external_edit()
        rolled = reg.rollback(Path(result["backup_dir"]))
        self.assertFalse(rolled["clean"])
        self.assertEqual(self.config.read_bytes(), edited)


class DriftTests(Base):
    def test_an_edit_between_plan_and_apply_is_refused(self):
        expected = self.call_plan("user")["original_sha256"]
        edited = self.external_edit()
        with self.assertRaises(reg.RegisterError) as ctx:
            reg.apply(self.agent, self.config, self.cli, "user", self.project_dir, self.brain_dir, "demo",
                      self.state / "runtime", ROOT / "brain.py", self.state, expected_sha=expected)
        self.assertIn("changed since the plan", str(ctx.exception))
        self.assertEqual(self.config.read_bytes(), edited)
        self.assertFalse((self.state / "backups").exists())

    def test_an_edit_while_backing_up_stops_before_anything_is_written(self):
        real_backup = reg.backup_config

        def backup_then_edit(config, folder):
            digest = real_backup(config, folder)
            self.external_edit()
            return digest
        with mock.patch.object(reg, "backup_config", backup_then_edit):
            with self.assertRaises(reg.RegisterError) as ctx:
                self.call_apply("user")
        self.assertIn("changed while preparing", str(ctx.exception))
        self.assertNotIn(reg.NAME, {n for _, n in self.names()})
        self.assertIn(("user", "late"), self.names())
        self.assertEqual(self.journal()["state"], "aborted-unchanged")
        self.assertEqual(self.locks(), [])

    def test_an_edit_after_apply_blocks_the_byte_restore_and_entry_only_keeps_it(self):
        folder = Path(self.call_apply("user")["backup_dir"])
        edited = self.external_edit()
        rolled = reg.rollback(folder)
        self.assertFalse(rolled["clean"])
        self.assertEqual(rolled["mode"], "refused")
        self.assertIn("--entry-only", rolled["problems"][0])
        self.assertEqual(self.config.read_bytes(), edited)
        self.assertEqual(self.journal(folder)["state"], "applied")
        rolled = reg.rollback(folder, entry_only=True)
        self.assertTrue(rolled["clean"], rolled)
        self.assertNotIn(("user", reg.NAME), self.names())
        self.assertIn(("user", "late"), self.names())
        self.assertIn(("user", "hub"), self.names())
        with self.assertRaises(reg.RegisterError):
            reg.rollback(folder)  # already rolled back
        self.assertEqual(self.locks(), [])


class DriftCodexTests(DriftTests):
    kind = "codex"
    agent = "codex"


class PermissionTests(Base):
    def test_a_read_only_config_is_left_alone(self):
        original = self.config.read_bytes()
        os.chmod(self.config, stat.S_IREAD)
        self.addCleanup(os.chmod, self.config, stat.S_IREAD | stat.S_IWRITE)
        with self.assertRaises(reg.RegisterError) as ctx:
            self.call_apply("user")
        self.assertIn("exited with", str(ctx.exception))
        self.assertEqual(self.config.read_bytes(), original)
        self.assertEqual(self.journal()["state"], "failed-rolled-back")
        self.assertEqual(self.locks(), [])

    def test_a_restore_that_cannot_write_reports_and_leaves_the_applied_file(self):
        folder = Path(self.call_apply("user")["backup_dir"])
        applied = self.config.read_bytes()
        with mock.patch.object(reg.os, "replace", side_effect=PermissionError("denied")):
            rolled = reg.rollback(folder)
        self.assertFalse(rolled["clean"])
        self.assertIn("PermissionError", rolled["problems"][0])
        self.assertEqual(self.config.read_bytes(), applied)
        self.assertEqual(self.journal(folder)["state"], "applied")
        self.assertEqual(self.locks(), [])
        self.assertEqual([p.name for p in self.config.parent.iterdir() if p.name.endswith(".tmp")], [])
        self.assertTrue(reg.rollback(folder)["clean"])  # and it works once the file can be written


class InterruptedTests(Base):
    def crash(self, target: str, nth: int = 1):
        """Kill the run on the nth call of `target`, leaving the lock behind as a dead process would."""
        real, calls = getattr(reg, target), []

        def fake(*args, **kwargs):
            calls.append(1)
            if len(calls) == nth:
                raise Crash()
            return real(*args, **kwargs)
        with mock.patch.object(reg, target, fake), mock.patch.object(reg, "_release"):
            with self.assertRaises(Crash):
                self.call_apply("user")
        self.assertEqual(len(self.locks()), 1)
        return next((self.state / "backups").glob("mcp-*"))

    def test_a_crash_after_the_entry_was_written_is_reverted_by_recover(self):
        before = self.names()
        folder = self.crash("handshake", nth=2)  # 1st handshake is the plan's, 2nd checks the written entry
        self.assertEqual(self.journal(folder)["state"], "verifying")
        self.assertIn(("user", reg.NAME), self.names())
        with self.assertRaises(reg.RegisterError) as ctx:  # nothing new starts while the run is unsettled
            reg.apply(self.agent, self.config, self.cli, "user", self.project_dir, self.brain_dir, "demo",
                      self.state / "runtime", ROOT / "brain.py", self.state)
        self.assertNotIn(TOKEN, str(ctx.exception))
        report = reg.recover(self.state)
        outcomes = {r.get("backup_dir") or r.get("lock"): r["outcome"] for r in report}
        self.assertEqual(outcomes[folder.name], "recovered-entry-removed")
        self.assertIn("released", outcomes.values())
        self.assertEqual(self.names(), before)
        self.assertEqual(self.locks(), [])
        self.assertNotIn(TOKEN, json.dumps(report) + self.all_state_text())
        self.assertEqual(self.journal(folder)["state"], "recovered-entry-removed")
        self.assertTrue(self.call_apply("user")["applied"])  # a fresh run works again

    def test_a_crash_before_the_cli_ran_changes_nothing(self):
        for target, state in (("backup_config", "prepared"), ("run_cli", "writing")):
            with self.subTest(target=target):
                original = self.config.read_bytes()
                folder = self.crash(target)
                self.assertEqual(self.journal(folder)["state"], state)
                report = reg.recover(self.state)
                self.assertIn({"backup_dir": folder.name, "outcome": "aborted-unchanged", "detail": ""}, report)
                self.assertEqual(self.config.read_bytes(), original)
                self.assertEqual(self.locks(), [])
                shutil.rmtree(self.state / "backups")

    def test_recover_leaves_a_config_alone_when_other_entries_changed(self):
        folder = self.crash("handshake", nth=2)
        edited = self.external_edit()
        report = reg.recover(self.state)
        entry = next(r for r in report if r.get("backup_dir") == folder.name)
        self.assertEqual(entry["outcome"], "needs-attention")
        self.assertIn("late", entry["detail"])
        self.assertEqual(self.config.read_bytes(), edited)
        self.assertEqual(self.journal(folder)["state"], "failed-needs-attention")
        self.assertEqual(self.locks(), [])  # settled as needs-attention, so its lock is released

    def test_an_unreadable_journal_is_reported_and_left_alone(self):
        folder = self.state / "backups" / "mcp-broken"
        folder.mkdir(parents=True)
        (folder / "journal.json").write_text("{ not json", encoding="utf-8")
        original = self.config.read_bytes()
        report = reg.recover(self.state)
        self.assertEqual(report, [{"backup_dir": "mcp-broken", "outcome": "needs-attention",
                                   "detail": "cannot read the journal in mcp-broken"}])
        self.assertEqual((folder / "journal.json").read_text(encoding="utf-8"), "{ not json")
        self.assertEqual(self.config.read_bytes(), original)

    def test_a_lock_left_before_any_journal_is_released_by_recover(self):
        lock = reg._lock_path(self.state, self.config)
        lock.parent.mkdir(parents=True)
        lock.write_text(json.dumps({"backup_dir": str(self.state / "backups" / "mcp-never")}), encoding="utf-8")
        self.assertEqual(reg.recover(self.state), [{"lock": lock.name, "outcome": "released", "detail": "run state never-started"}])
        self.assertFalse(lock.exists())


class ConcurrencyTests(Base):
    def test_a_held_lock_refuses_a_second_run(self):
        original = self.config.read_bytes()
        with reg.config_lock(self.state, self.config, self.state / "other-run"):
            with self.assertRaises(reg.RegisterError) as ctx:
                self.call_apply("user")
        self.assertIn("running or was interrupted", str(ctx.exception))
        self.assertEqual(self.config.read_bytes(), original)
        self.assertEqual(self.locks(), [])

    def test_parallel_registrations_add_exactly_one_entry(self):
        barrier, outcomes = threading.Barrier(4), []

        def worker():
            barrier.wait()
            try:
                self.call_apply("user")
                outcomes.append("applied")
            except reg.RegisterError as exc:
                outcomes.append(str(exc))
        threads = [threading.Thread(target=worker) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=120)
        self.assertEqual(outcomes.count("applied"), 1, outcomes)
        self.assertEqual(len(outcomes), 4)
        self.assertEqual(sum(1 for _, n in self.names() if n == reg.NAME), 1)
        self.assertEqual(self.locks(), [])


class EndToEndTests(Base):
    """The fake CLI writes the entry; the entry it wrote is then started and used as the agent would."""

    def test_registered_entry_runs_the_brain_with_its_project_and_identity_then_rolls_back_exactly(self):
        original = self.config.read_bytes()
        result = self.call_apply("user")
        entry = reg.read_entries(self.agent, self.config, self.project_dir)["user"][reg.NAME]
        messages = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                     "params": {"name": "brain_propose", "arguments": {"kind": "fact", "title": "E2E", "body": "Hi."}}}]
        done = subprocess.run([entry["command"], *entry["args"]], capture_output=True, text=True, encoding="utf-8",
                              timeout=60, input="".join(json.dumps(m) + "\n" for m in messages))
        self.assertEqual(done.returncode, 0, done.stderr)
        notes = brain.Brain(self.brain_dir, "demo").search(include_unverified=True)
        self.assertEqual([(n["title"], n["source_agent"], n["status"]) for n in notes], [("E2E", self.agent, "inbox")])
        self.assertTrue(reg.rollback(Path(result["backup_dir"]))["clean"])
        self.assertEqual(self.config.read_bytes(), original)


class EndToEndCodexTests(EndToEndTests):
    kind = "codex"
    agent = "codex"


class EndToEndAgyTests(EndToEndTests):
    kind = "agy"
    agent = "antigravity"


class DefaultConfigTests(unittest.TestCase):
    def test_agent_config_dirs_from_the_environment_are_honoured(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": tmp, "CODEX_HOME": tmp}):
            self.assertEqual(reg.default_config("claude"), Path(tmp) / ".claude.json")
            self.assertEqual(reg.default_config("codex"), Path(tmp) / "config.toml")
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(reg, "HOME", Path("H")):
            self.assertEqual(reg.default_config("claude"), Path("H") / ".claude.json")
            self.assertEqual(reg.default_config("codex"), Path("H") / ".codex" / "config.toml")
            self.assertEqual(reg.default_config("antigravity"), Path("H") / ".gemini" / "config" / "mcp_config.json")


if __name__ == "__main__":
    unittest.main()
