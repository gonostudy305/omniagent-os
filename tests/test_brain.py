"""Shared second brain: store rules, secret handling, provenance and the stdio MCP server."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "brain.py"
sys.path.insert(0, str(ROOT))
import brain
from brain import Brain, BrainError, SecretError


def fake_secrets() -> dict:
    """Built at runtime so no realistic secret literal is committed."""
    return {
        "private key": "-----BEGIN " + "RSA PRIVATE KEY-----\nabc",
        "github token": "gh" + "p_" + "a1B2c3D4" * 4,
        "api key (sk-)": "sk" + "-" + "Ab12" * 8,
        "aws access key": "AK" + "IA" + "ABCDEFGHIJKLMNOP",
        "bearer token": "Bearer " + "abcdEFGH1234" * 3,
        "credential assignment": "pass" + "word = " + "correcthorse",
        "jwt": "ey" + "J" + "abcdefgh" + "." + "ey" + "Jabcdefgh" + "." + "abcdefghij",
        "url credentials": "https://" + "user:" + "hunter2x" + "@example.com/x",
    }


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name) / "brain"
        self.brain = Brain(self.dir, "demo")

    def test_agent_proposals_are_unverified_and_hidden_by_default(self):
        note = self.brain.propose("claude", "decision", "Use SQLite", "One SQLite file per project.")
        self.assertEqual(note["status"], "inbox")
        self.assertIn("UNVERIFIED", note["trust"])
        self.assertEqual(self.brain.search("sqlite"), [])
        found = self.brain.search("sqlite", include_unverified=True)
        self.assertEqual([n["id"] for n in found], [note["id"]])
        self.assertFalse(found[0]["verified"])
        self.assertEqual(found[0]["source_agent"], "claude")

    def test_only_the_owner_can_verify_and_it_is_recorded(self):
        note = self.brain.propose("codex", "fact", "Port", "The service listens on port 8080.")
        for who in ("claude", "codex", "antigravity", "anyone"):
            with self.assertRaises(BrainError):
                self.brain.verify(note["id"], who)
        verified = self.brain.verify(note["id"], "user", "checked in the config")
        self.assertEqual(verified["status"], "verified")
        self.assertIn("user", [e["type"] for e in verified["evidence"]])
        self.assertEqual([n["id"] for n in self.brain.search("port")], [note["id"]])

    def test_notes_written_by_the_owner_are_verified(self):
        note = self.brain.propose("user", "lesson", "Backups first", "Back up before any write.")
        self.assertEqual(note["status"], "verified")

    def test_secrets_are_refused_without_echoing_the_value(self):
        for kind, text in fake_secrets().items():
            with self.assertRaises(SecretError) as ctx:
                self.brain.propose("claude", "fact", "title", "note: " + text)
            self.assertNotIn(text[-12:], str(ctx.exception), kind)
            with self.assertRaises(SecretError):
                self.brain.propose("claude", "fact", text, "fine body")
        self.assertEqual(self.brain.search(include_unverified=True), [])

    def test_hashes_urls_and_ordinary_prose_are_allowed(self):
        body = ("The artifact sha256 is " + "ab12" * 16 + ". See https://example.com/docs/page. "
                "We rotate the token yearly and never store the password in notes.")
        note = self.brain.propose("claude", "fact", "Release hash", body)
        self.assertEqual(note["status"], "inbox")

    def test_home_paths_are_redacted(self):
        home = str(Path.home())
        note = self.brain.propose("claude", "fact", "Where", f"Skills live in {home}\\.agents\\skills and C:\\Users\\Someone\\x.")
        self.assertNotIn(home, note["body"])
        self.assertNotIn("Someone", note["body"])
        self.assertIn("~", note["body"])

    def test_projects_are_isolated_and_names_are_validated(self):
        other = Brain(self.dir, "other")
        self.brain.propose("user", "fact", "Only here", "Visible in demo.")
        self.assertEqual(other.search(include_unverified=True), [])
        for bad in ("../x", "A", "", "a/b", "a" * 65, ".hidden"):
            with self.assertRaises(BrainError):
                Brain(self.dir, bad)

    def test_a_database_cannot_be_reused_under_another_project(self):
        self.brain.propose("user", "fact", "x", "y")
        (self.dir / "mismatch.db").write_bytes(self.brain.path.read_bytes())
        with self.assertRaises(BrainError):
            Brain(self.dir, "mismatch").search()

    def test_disagreeing_notes_become_contested_until_the_owner_chooses(self):
        a = self.brain.propose("claude", "decision", "Storage", "Use SQLite.")
        b = self.brain.propose("codex", "decision", "storage", "Use a vector database.")
        self.assertEqual(b["status"], "contested")
        self.assertEqual({c["id"] for c in self.brain.conflicts()}, {a["id"], b["id"]})
        self.assertEqual(self.brain.search("storage"), [])
        self.brain.verify(a["id"])
        self.assertEqual(self.brain.read(a["id"])["status"], "verified")
        self.assertEqual(self.brain.read(b["id"])["status"], "superseded")
        self.assertEqual(self.brain.conflicts(), [])

    def test_supersedes_retires_the_old_note_without_a_conflict(self):
        old = self.brain.propose("claude", "decision", "Storage", "Use files.")
        new = self.brain.propose("claude", "decision", "Storage", "Use SQLite.", supersedes=old["id"])
        self.assertEqual(self.brain.read(old["id"])["status"], "superseded")
        self.assertEqual(new["status"], "inbox")
        with self.assertRaises(BrainError):
            self.brain.propose("claude", "decision", "Storage", "Again.", supersedes=old["id"])
        with self.assertRaises(BrainError):
            self.brain.verify(old["id"])

    def test_identical_proposals_are_not_duplicated(self):
        a = self.brain.propose("claude", "fact", "Same", "Same text.")
        b = self.brain.propose("codex", "fact", "same", "Same text.")
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(len(self.brain.search(include_unverified=True)), 1)

    def test_file_evidence_is_hashed_and_drift_is_flagged(self):
        evidence = Path(self.temp.name) / "spec.md"
        evidence.write_text("v1", encoding="utf-8")
        note = self.brain.propose("claude", "fact", "Spec", "The spec says v1.",
                                  [{"type": "file", "ref": str(evidence)}])
        self.assertEqual(len(note["evidence"][0]["sha256"]), 64)
        self.brain.verify(note["id"])
        self.assertEqual(self.brain.recheck(), [])
        evidence.write_text("v2", encoding="utf-8")
        drift = self.brain.recheck()
        self.assertEqual(drift[0]["reason"], "file changed")
        self.assertEqual(self.brain.read(note["id"])["status"], "contested")
        evidence.unlink()
        self.brain.verify(note["id"])
        self.assertEqual(self.brain.recheck()[0]["reason"], "file missing")

    def test_bad_input_is_refused(self):
        cases = [("nobody", "fact", "t", "b"), ("claude", "gossip", "t", "b"), ("claude", "fact", "", "b"),
                 ("claude", "fact", "t", "   "), ("claude", "fact", "t", "x" * (brain.MAX_BODY + 1)),
                 ("claude", "fact", "t" * (brain.MAX_TITLE + 1), "b")]
        for case in cases:
            with self.assertRaises(BrainError, msg=str(case[:3])):
                self.brain.propose(*case)
        with self.assertRaises(BrainError):
            self.brain.propose("claude", "fact", "t", "b", [{"type": "file", "ref": str(Path(self.temp.name) / "nope")}])
        with self.assertRaises(BrainError):
            self.brain.propose("claude", "fact", "t", "b", [{"type": "note"}])

    def test_search_input_cannot_inject_sql(self):
        self.brain.propose("user", "fact", "Plain", "Plain text about 100% safety.")
        self.brain.propose("user", "fact", "Other", "Nothing special here.")
        for query in ("'; drop table notes; --", "_", "\\", 'x" or 1=1 --', "a%b", "100%x"):
            self.assertEqual(self.brain.search(query, include_unverified=True), [], query)
        # Wildcard characters match only themselves: "%" finds the one note that contains a percent sign.
        self.assertEqual([n["title"] for n in self.brain.search("%")], ["Plain"])
        self.assertEqual(len(self.brain.search()), 2)

    def test_audit_log_records_who_did_what(self):
        note = self.brain.propose("codex", "fact", "A", "b")
        self.brain.verify(note["id"])
        log = self.brain.audit_log()
        self.assertEqual([(r["agent"], r["action"]) for r in reversed(log)], [("codex", "propose"), ("user", "verify")])

    def test_export_then_import_arrives_unverified_and_never_trusts_flags(self):
        owner = self.brain.propose("user", "decision", "Keep", "Verified by the owner.")
        self.brain.propose("claude", "fact", "Idea", "Just an idea.")
        dump = Path(self.temp.name) / "dump.jsonl"
        self.assertEqual(self.brain.export_jsonl(dump), 2)
        other = Brain(self.dir, "copy")
        self.assertEqual(other.import_jsonl(dump), {"added": 2, "skipped_existing": 0})
        self.assertEqual(other.import_jsonl(dump), {"added": 0, "skipped_existing": 2})
        imported = other.read(owner["id"])
        self.assertEqual(imported["status"], "inbox")
        self.assertNotIn("user", [e["type"] for e in imported["evidence"]])
        self.assertEqual(other.search(), [])

    def test_import_refuses_files_with_secrets_or_bad_notes(self):
        row = {"id": "n-20261028-abcdef", "kind": "fact", "source_agent": "claude", "title": "t",
               "body": "x " + fake_secrets()["github token"], "evidence": []}
        path = Path(self.temp.name) / "bad.jsonl"
        path.write_text(json.dumps(row) + "\n", encoding="utf-8")
        with self.assertRaises(SecretError):
            Brain(self.dir, "imp").import_jsonl(path)
        path.write_text("not json\n", encoding="utf-8")
        with self.assertRaises(BrainError):
            Brain(self.dir, "imp").import_jsonl(path)
        row.update(body="fine", id="../../etc")
        path.write_text(json.dumps(row) + "\n", encoding="utf-8")
        with self.assertRaises(BrainError):
            Brain(self.dir, "imp").import_jsonl(path)
        self.assertEqual(Brain(self.dir, "imp").search(include_unverified=True), [])

    def test_backup_restore_round_trip_and_safety(self):
        note = self.brain.propose("user", "fact", "Keep me", "Important.")
        copy = Path(self.temp.name) / "backup" / "demo.db"
        info = self.brain.backup(copy)
        self.assertEqual((info["project"], info["notes"]), ("demo", 1))
        with self.assertRaises(BrainError):
            self.brain.backup(copy)  # never overwrites a backup
        self.brain.propose("user", "fact", "Later", "Added after the backup.")
        with self.assertRaises(BrainError):
            self.brain.restore(copy)  # refuses to replace silently
        with self.assertRaises(BrainError):
            Brain(self.dir, "other").restore(copy)  # wrong project
        restored = self.brain.restore(copy, replace=True)
        self.assertEqual(restored["notes"], 1)
        self.assertEqual([n["id"] for n in self.brain.search()], [note["id"]])
        self.assertTrue(list(self.dir.glob("demo.pre-restore-*.db")), "a safety copy of the replaced database is kept")

    def test_a_corrupt_backup_is_refused(self):
        bad = Path(self.temp.name) / "bad.db"
        bad.write_bytes(b"this is not a database" * 20)
        with self.assertRaises(Exception):
            self.brain.restore(bad)
        self.assertFalse(self.brain.path.exists())


class McpClient:
    def __init__(self, brain_dir: Path, project: str, agent: str):
        self.proc = subprocess.Popen(
            [sys.executable, "-I", str(SCRIPT), "--brain-dir", str(brain_dir), "--project", project, "serve", "--agent", agent],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        self.next_id = 0

    def raw(self, text: str) -> dict:
        self.proc.stdin.write(text + "\n")
        self.proc.stdin.flush()
        return json.loads(self.proc.stdout.readline())

    def request(self, method: str, params: dict | None = None) -> dict:
        self.next_id += 1
        return self.raw(json.dumps({"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params or {}}))

    def tool(self, name: str, **arguments):
        reply = self.request("tools/call", {"name": name, "arguments": arguments})
        result = reply["result"]
        return json.loads(result["content"][0]["text"]) if not result.get("isError") else result["content"][0]["text"]

    def close(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=10)
        self.proc.stdout.close()
        self.proc.stderr.close()


class McpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name) / "brain"
        self.clients = []

    def client(self, agent: str, project: str = "demo") -> McpClient:
        c = McpClient(self.dir, project, agent)
        self.clients.append(c)
        self.addCleanup(c.close)
        c.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}})
        return c

    def owner(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-I", str(SCRIPT), "--brain-dir", str(self.dir), "--project", "demo", *args],
                              capture_output=True, text=True, encoding="utf-8")

    def test_handshake_and_tool_list_expose_no_verify_or_delete(self):
        c = self.proc_client = McpClient(self.dir, "demo", "claude")
        self.addCleanup(c.close)
        init = c.request("initialize", {"protocolVersion": "2025-06-18"})
        self.assertEqual(init["result"]["serverInfo"]["name"], "omniagent-brain")
        self.assertIn("tools", init["result"]["capabilities"])
        names = {t["name"] for t in c.request("tools/list")["result"]["tools"]}
        self.assertEqual(names, {"brain_search", "brain_read", "brain_propose", "brain_conflicts"})
        self.assertEqual(c.request("ping")["result"], {})

    def test_protocol_errors_do_not_kill_the_server(self):
        c = self.client("claude")
        self.assertEqual(c.raw("{not json")["error"]["code"], -32700)
        self.assertEqual(c.raw("[1,2]")["error"]["code"], -32700)
        self.assertEqual(c.request("nope/nothing")["error"]["code"], -32601)
        self.assertIn("unknown tool", c.tool("brain_delete"))
        self.assertEqual(c.request("ping")["result"], {})

    def test_provenance_comes_from_the_launch_not_from_the_client(self):
        c = self.client("codex")
        note = c.tool("brain_propose", kind="fact", title="Spoof", body="Claims to be the owner.",
                      source_agent="user", agent="user", status="verified", project="other")
        self.assertEqual((note["source_agent"], note["status"]), ("codex", "inbox"))
        self.assertTrue(note["source_session"])
        self.assertFalse((self.dir / "other.db").exists())

    def test_the_owner_identity_cannot_be_launched_over_mcp(self):
        result = subprocess.run([sys.executable, "-I", str(SCRIPT), "--brain-dir", str(self.dir), "--project", "demo",
                                 "serve", "--agent", "user"], capture_output=True, text=True, input="", encoding="utf-8")
        self.assertNotEqual(result.returncode, 0)

    def test_a_secret_sent_over_mcp_is_refused_and_not_echoed(self):
        c = self.client("claude")
        secret = fake_secrets()["github token"]
        reply = c.tool("brain_propose", kind="fact", title="t", body="x " + secret)
        self.assertIsInstance(reply, str)
        self.assertIn("secret", reply)
        self.assertNotIn(secret, reply)
        self.assertEqual(c.tool("brain_search", include_unverified=True), {"notes": []})

    def test_unicode_round_trip(self):
        c = self.client("antigravity")
        note = c.tool("brain_propose", kind="lesson", title="Sao lưu trước khi ghi", body="Luôn sao lưu trước. Tiếng Việt có dấu: ệ ơ ư.")
        read = c.tool("brain_read", id=note["id"])
        self.assertIn("ệ ơ ư", read["body"])

    def test_vertical_slice_claude_writes_codex_reads_gemini_retrieves(self):
        evidence = Path(self.temp.name) / "ADR-1.md"
        evidence.write_text("Decision record", encoding="utf-8")
        claude, codex, gemini = self.client("claude"), self.client("codex"), self.client("antigravity")

        # 1. Claude records an architecture decision with evidence.
        note = claude.tool("brain_propose", kind="decision", title="Memory backend",
                           body="Use a local SQLite file per project, exposed over stdio MCP.",
                           evidence=[{"type": "file", "ref": str(evidence), "detail": "ADR-1"}])
        self.assertEqual(note["status"], "inbox")

        # 2. Codex, a different process, can read it back, clearly labelled as unverified.
        self.assertEqual(codex.tool("brain_search", query="sqlite"), {"notes": []})
        hit = codex.tool("brain_search", query="sqlite", include_unverified=True)["notes"]
        self.assertEqual([h["id"] for h in hit], [note["id"]])
        read = codex.tool("brain_read", id=note["id"])
        self.assertEqual(read["source_agent"], "claude")
        self.assertIn("UNVERIFIED", read["trust"])
        self.assertEqual(read["evidence"][0]["type"], "file")

        # 3. The owner confirms it; Gemini now retrieves it by default, as verified.
        done = self.owner("verify", note["id"], "--detail", "reviewed ADR-1")
        self.assertEqual(done.returncode, 0, done.stderr)
        got = gemini.tool("brain_search", query="memory backend")["notes"]
        self.assertEqual([(g["id"], g["verified"]) for g in got], [(note["id"], True)])
        self.assertEqual(gemini.tool("brain_read", id=note["id"])["status"], "verified")

        # 4. A contradicting proposal from another agent surfaces as a conflict for the owner.
        codex.tool("brain_propose", kind="decision", title="memory backend", body="Use a hosted vector service.")
        conflicts = gemini.tool("brain_conflicts")["conflicts"]
        self.assertEqual(len(conflicts), 2)
        self.assertEqual(gemini.tool("brain_search", query="memory backend"), {"notes": []})

        # 5. The audit trail names every actor.
        log = self.owner("audit")
        actors = {row["agent"] for row in json.loads(log.stdout)}
        self.assertTrue({"claude", "codex", "user"} <= actors)

    def test_register_plan_prints_snippets_and_writes_nothing(self):
        before = set(self.dir.rglob("*")) if self.dir.exists() else set()
        out = self.owner("register-plan")
        self.assertEqual(out.returncode, 0, out.stderr)
        for expected in ("claude mcp add omniagent-brain", "[mcp_servers.omniagent-brain]", '"mcpServers"', "Not applied"):
            self.assertIn(expected, out.stdout)
        self.assertEqual(set(self.dir.rglob("*")) if self.dir.exists() else set(), before)

    def test_owner_cli_end_to_end(self):
        made = self.owner("propose", "--kind", "lesson", "--title", "Backups", "--body", "Back up first.")
        self.assertEqual(made.returncode, 0, made.stderr)
        self.assertEqual(json.loads(made.stdout)["status"], "verified")
        listed = json.loads(self.owner("list").stdout)
        self.assertEqual([n["title"] for n in listed], ["Backups"])
        refused = self.owner("propose", "--kind", "fact", "--title", "t", "--body", "token = " + "abcdefgh1234")
        self.assertEqual(refused.returncode, 2)
        self.assertIn("secret", refused.stderr)
        self.assertNotIn("abcdefgh1234", refused.stderr)


if __name__ == "__main__":
    unittest.main()
