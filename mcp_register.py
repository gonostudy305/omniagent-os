#!/usr/bin/env python3
"""Register the `omniagent-brain` MCP server in Claude Code, Codex or Antigravity (M3).

Lifecycle: inspect -> preview (`plan`) -> approve -> `apply` -> verify -> `rollback`, plus `recover` for a run
that was interrupted. `plan` and `runtime` never touch an agent's configuration.

`apply` takes the config's SHA-256 from the plan you reviewed and refuses if the file changed since. It
backs the file up, runs the agent's own `mcp add` (the agent owns its file format), then checks that exactly
one entry was added and every other entry is unchanged (compared after parsing, because the CLIs reformat
their files), and that the registered command answers an MCP handshake. Each stage is journaled.

`rollback` restores the original bytes, but only while the config still has exactly the bytes recorded right
after `apply`. If anything changed it since, it refuses and leaves the file alone; `rollback --entry-only`
then removes just our entry with the agent's own `mcp remove`. Standard library only.

Agent config files can hold tokens. This tool therefore never prints an entry's value: output carries
names, counts, paths of our own server and hashes only, and a CLI's own output is shown only when it
passes the secret scan.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import tomllib
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import brain

NAME = "omniagent-brain"
AGENTS = ("claude", "codex", "antigravity")
EXPECTED_TOOLS = {t["name"] for t in brain.TOOLS}
HOME = Path.home()
DEFAULT_STATE = Path(__file__).resolve().parent / ".local"
# Journal states. A run that stopped in one of the open states is handled by `recover`.
OPEN_STATES = ("prepared", "backed-up", "writing", "verifying")
ABSENT = "absent"  # what the plan shows, and `--expect-sha` takes, for a config file that does not exist yet


class RegisterError(Exception):
    pass


def short(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()[:12]


def file_sha(path: Path) -> str | None:
    """SHA-256 of the file, or None if it does not exist."""
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RegisterError(f"cannot read {Path(path).name} ({type(exc).__name__}); nothing was changed") from None


def default_config(agent: str) -> Path:
    """Where each agent's own CLI writes, checked against Claude Code 2.1.246 and Codex 0.163.0."""
    if agent == "claude":
        base = os.environ.get("CLAUDE_CONFIG_DIR")
        return Path(base) / ".claude.json" if base else HOME / ".claude.json"
    if agent == "codex":
        return Path(os.environ.get("CODEX_HOME") or HOME / ".codex") / "config.toml"
    return HOME / ".gemini" / "config" / "mcp_config.json"  # agy, per `agy mcp list` (docs/M3_REGISTRATION_PLAN.md)


def atomic_write(path: Path, data: bytes) -> None:
    """Write through a temporary file in the same folder, then replace in one step."""
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    try:
        with open(tmp, "xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def write_journal(folder: Path, journal: dict) -> None:
    atomic_write(Path(folder) / "journal.json", json.dumps(journal, indent=2).encode("utf-8"))


# ---------------------------------------------------------------- M3.0 runtime snapshot + handshake

def prepare_runtime(source: Path, runtime_dir: Path) -> Path:
    """Copy brain.py to `<runtime_dir>/brain-<12 hex of sha256>.py` so a branch switch cannot break the server."""
    data = Path(source).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    target = Path(runtime_dir) / f"brain-{digest[:12]}.py"
    if file_sha(target) != digest:
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            atomic_write(target, data)
        except PermissionError:
            # Windows refuses to replace a file another run is executing; that run wrote the same bytes.
            if file_sha(target) != digest:
                raise RegisterError("could not write the runtime copy") from None
    if file_sha(target) != digest:
        raise RegisterError("runtime copy does not match its source")
    return target


def server_command(runtime: Path, brain_dir: Path, project: str, agent: str) -> tuple[str, list[str]]:
    return sys.executable, brain.server_args(runtime, project, Path(brain_dir), agent)


def handshake(command: str, args: list[str], timeout: float = 30) -> list[str]:
    """Start the exact registered command, run initialize + tools/list, return the tool names."""
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "register-check", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    try:
        done = subprocess.run([command] + list(args), input="".join(json.dumps(r) + "\n" for r in requests),
                              capture_output=True, text=True, encoding="utf-8", timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RegisterError(f"could not start the server command ({type(exc).__name__})") from None
    replies = {}
    for line in done.stdout.splitlines():
        try:
            reply = json.loads(line)
        except ValueError:
            continue
        if isinstance(reply, dict) and "id" in reply:
            replies[reply["id"]] = reply
    if "result" not in replies.get(1, {}) or "result" not in replies.get(2, {}):
        raise RegisterError("the server did not answer initialize and tools/list")
    names = {t["name"] for t in replies[2]["result"]["tools"]}
    if names != EXPECTED_TOOLS:
        raise RegisterError("the server lists unexpected tools")
    return sorted(names)


# ---------------------------------------------------------------- config snapshots (hashes only)

def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def _servers(value, config: Path) -> dict[str, dict]:
    """A server table must map names to tables; anything else is a layout we do not understand."""
    if value is None:
        return {}
    if not isinstance(value, dict) or not all(isinstance(v, dict) for v in value.values()):
        raise RegisterError(f"{config.name} has an MCP server list in a shape this tool does not know; nothing was changed")
    return dict(value)


def read_entries(agent: str, config: Path, project_dir: Path | None = None) -> dict[str, dict[str, dict]]:
    """{scope: {server name: entry}} read from the config file. Callers must hash, never print, the entries."""
    if file_sha(config) is None:
        return {}
    try:
        text = config.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError:
        raise RegisterError(f"{config.name} is not UTF-8 text; nothing was changed") from None
    try:
        if agent == "codex":
            return {"user": _servers(tomllib.loads(text).get("mcp_servers"), config)}
        doc = json.loads(text) if text.strip() else {}
    except ValueError:  # JSON and TOML decode errors can quote file content, so say nothing more
        raise RegisterError(f"{config.name} is not valid {'TOML' if agent == 'codex' else 'JSON'}; nothing was changed") from None
    if not isinstance(doc, dict):
        raise RegisterError(f"{config.name} is not a JSON object; nothing was changed")
    scopes = {"user": _servers(doc.get("mcpServers"), config)}
    if agent == "antigravity":
        return scopes
    projects = doc.get("projects") or {}
    if not isinstance(projects, dict) or not all(isinstance(v, dict) for v in projects.values()):
        raise RegisterError(f"{config.name} has a project list in a shape this tool does not know; nothing was changed")
    for key, value in projects.items():
        servers = _servers(value.get("mcpServers"), config)
        if servers:
            scopes["project:" + _norm(key)] = servers
    if project_dir is not None:
        scopes.setdefault("project:" + _norm(str(project_dir)), {})
    return scopes


def snapshot(agent: str, config: Path, project_dir: Path | None = None) -> dict[str, dict[str, str]]:
    return {scope: {name: short(entry) for name, entry in servers.items()}
            for scope, servers in read_entries(agent, config, project_dir).items()}


def flat(snap: dict[str, dict[str, str]]) -> dict[tuple[str, str], str]:
    return {(scope, name): digest for scope, servers in snap.items() for name, digest in servers.items()}


def compare(before: dict, after: dict, added: tuple[str, str] | None) -> list[str]:
    """Problems found between two snapshots. `added` is the one entry that may appear (or disappear)."""
    b, a = flat(before), flat(after)
    problems = []
    for key in sorted(set(b) | set(a)):
        if key == added:
            continue
        if key not in a:
            problems.append(f"entry {key[1]!r} in scope {key[0]} disappeared")
        elif key not in b:
            problems.append(f"entry {key[1]!r} in scope {key[0]} appeared")
        elif a[key] != b[key]:
            problems.append(f"entry {key[1]!r} in scope {key[0]} changed")
    return problems


# ---------------------------------------------------------------- one run per config at a time

def _lock_path(state_dir: Path, config: Path) -> Path:
    return Path(state_dir) / "locks" / f"{hashlib.sha256(_norm(str(Path(config).absolute())).encode()).hexdigest()[:16]}.lock"


def _release(lock: Path) -> None:
    lock.unlink(missing_ok=True)


@contextmanager
def config_lock(state_dir: Path, config: Path, folder: Path):
    """Created atomically; a second run on the same config is refused. Never removed for being old: an
    interrupted run leaves it behind on purpose, and only `recover` clears it after checking the journal."""
    lock = _lock_path(state_dir, config)
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise RegisterError(f"another registration for {Path(config).name} is running or was interrupted; "
                            "if none is running, run `recover`") from None
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump({"config": str(config), "backup_dir": str(folder), "pid": os.getpid(), "at": brain.now()}, handle)
    try:
        yield
    finally:
        _release(lock)


# ---------------------------------------------------------------- the agent CLIs

def _scope_name(agent: str, scope: str, project_dir: Path) -> str:
    return "project:" + _norm(str(project_dir)) if agent == "claude" and scope == "local" else "user"


def add_command(agent: str, cli: str, scope: str, command: str, args: list[str]) -> list[str]:
    tail = ["--", command] + list(args)
    if agent == "claude":
        return [cli, "mcp", "add", "--scope", scope, NAME] + tail
    return [cli, "mcp", "add", NAME] + tail


def remove_command(agent: str, cli: str, scope: str) -> list[str]:
    if agent == "claude":
        return [cli, "mcp", "remove", "--scope", scope, NAME]
    return [cli, "mcp", "remove", NAME]


def safe_text(text: str) -> str:
    """CLI output is shown only when the secret scan passes; otherwise it is withheld."""
    text = brain.redact_paths(text.strip())[:300]
    return "(output withheld: it looked like it contained a credential)" if brain.scan_secrets(text) else text


def run_cli(cmd: list[str], cwd: Path | None) -> tuple[int, str]:
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=120, cwd=cwd, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RegisterError(f"could not run the {Path(cmd[0]).name} command ({type(exc).__name__})") from None
    return done.returncode, safe_text(done.stdout + "\n" + done.stderr)


# ---------------------------------------------------------------- plan / apply / rollback / recover

def plan(agent: str, config: Path, cli: str | None, scope: str, project_dir: Path, brain_dir: Path,
         project: str, runtime_dir: Path, source: Path) -> dict:
    """Everything `apply` would do, with nothing written to the agent's config (only .local/runtime is prepared)."""
    if agent not in AGENTS:
        raise RegisterError(f"agent must be one of {list(AGENTS)}")
    if scope not in ("local", "user"):
        raise RegisterError("scope must be 'local' or 'user'")
    if agent != "claude" and scope == "local":
        scope = "user"  # Codex and agy have no per-project flag on `mcp add`
    where = _scope_name(agent, scope, project_dir)
    found = cli or shutil.which(agent if agent != "antigravity" else "agy")
    problems = []
    if not found:
        problems.append("agent CLI not found")
    original = file_sha(config)
    snap = snapshot(agent, config, project_dir)
    if any(NAME in servers for servers in snap.values()):
        problems.append(f"an entry named {NAME} already exists; remove it first (agy 'add' would overwrite)")
    runtime = prepare_runtime(source, runtime_dir)
    command, args = server_command(runtime, brain_dir, project, agent)
    try:
        tools = handshake(command, args)
    except RegisterError as exc:
        tools = []
        problems.append(str(exc))
    return {
        "agent": agent, "scope": scope, "target_scope": where,
        "config_file": config.name, "config_path": brain.redact_paths(str(config)), "config_exists": original is not None,
        "original_sha256": original or ABSENT,
        "cli": Path(found).name if found else None,
        "command": add_command(agent, found or agent, scope, command, args),
        "proposed_entry": {"name": NAME, "command": command, "args": args},
        "runtime": str(runtime), "handshake_tools": tools,
        "existing_entries": {s: sorted(v) for s, v in snap.items() if s == where or s == "user"},
        "entry_count": len(flat(snap)),
        "changes": [f"add one entry {NAME!r} to scope {where} with the agent's own `mcp add`",
                    "the agent CLI rewrites the file (it may reformat it and update its own keys); every other "
                    "MCP entry must compare equal after parsing, or the change is reverted"],
        "expected_output_sha256": None,
        "expected_output_note": "not predictable: the agent CLI writes the file; apply records the resulting "
                                "hash, and rollback restores the original bytes only while the file still has it",
        "backup_and_rollback": [
            "apply copies the config to <state-dir>/backups/mcp-<time>-<agent>/config.bak (may hold tokens; stays in .local)",
            "rollback --backup-dir <that folder>: byte-exact restore if the config is unchanged since apply",
            "if it changed since: rollback refuses; rollback --entry-only removes just this entry with `mcp remove`",
        ],
        "apply_with": f"apply --agent {agent} --scope {scope} --yes --expect-sha {original or ABSENT}",
        "problems": problems, "writes_nothing": True,
    }


def backup_config(config: Path, folder: Path) -> str | None:
    """Copy the config into the backup folder; return its SHA-256 (None if there was no file)."""
    original = file_sha(config)
    if original is not None:
        target = Path(folder) / "config.bak"  # may contain tokens: stays under .local, never in git
        atomic_write(target, config.read_bytes())
        shutil.copymode(config, target)
        if file_sha(target) != original:
            raise RegisterError("backup copy does not match the config; nothing was changed")
    return original


def apply(agent: str, config: Path, cli: str, scope: str, project_dir: Path, brain_dir: Path, project: str,
          runtime_dir: Path, source: Path, state_dir: Path, confirm=None, expected_sha: str | None = None) -> dict:
    """Register our entry. `expected_sha` is the plan's `original_sha256`; the run stops if the file differs."""
    preview = plan(agent, config, cli, scope, project_dir, brain_dir, project, runtime_dir, source)
    if preview["problems"]:
        raise RegisterError("; ".join(preview["problems"]))
    if expected_sha is not None and preview["original_sha256"] != expected_sha:
        raise RegisterError(f"{config.name} changed since the plan you reviewed; run plan again; nothing was changed")
    scope = preview["scope"]
    where = _scope_name(agent, scope, project_dir)
    if confirm is not None:
        confirm(f"Register {NAME} for {agent} (scope {scope}) in {preview['config_path']}; a backup is taken first.")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    folder = Path(state_dir) / "backups" / f"mcp-{stamp}-{secrets.token_hex(3)}-{agent}"
    with config_lock(state_dir, config, folder):
        folder.mkdir(parents=True)
        before = snapshot(agent, config, project_dir)
        journal = {"agent": agent, "scope": scope, "config": str(config), "cli": cli, "project_dir": str(project_dir),
                   "name": NAME, "before_entries": before, "original_sha256": preview["original_sha256"],
                   "applied_sha256": None, "state": "prepared", "at": brain.now()}
        write_journal(folder, journal)
        original = backup_config(config, folder)
        journal["state"] = "backed-up"
        write_journal(folder, journal)
        # Re-check immediately before the write: nothing may have changed the file since the plan or the backup.
        if (original or ABSENT) != preview["original_sha256"] or file_sha(config) != original:
            journal["state"] = "aborted-unchanged"
            write_journal(folder, journal)
            raise RegisterError(f"{config.name} changed while preparing; nothing was written")
        journal["state"] = "writing"
        write_journal(folder, journal)
        try:
            code, out = run_cli(add_command(agent, cli, scope, *server_command(Path(preview["runtime"]), brain_dir, project, agent)),
                                project_dir)
        except RegisterError as exc:  # the CLI did not start or timed out: check what it left, like any failure
            code, out = -1, str(exc)
        journal["state"] = "verifying"
        write_journal(folder, journal)
        problems = []
        if code != 0:
            problems.append(f"`mcp add` exited with {code}: {out}")
        try:
            after = snapshot(agent, config, project_dir)
            problems += compare(before, after, (where, NAME))
            entry = read_entries(agent, config, project_dir).get(where, {}).get(NAME)
        except RegisterError as exc:
            after, entry = {}, None
            problems.append(str(exc))
        if entry is None:
            problems.append("the new entry is not in the config")
        else:
            try:
                handshake(entry.get("command", ""), list(entry.get("args", [])))
            except RegisterError as exc:
                problems.append(f"registered command failed the handshake: {exc}")
        if problems:
            if file_sha(config) == original:
                removed = {"clean": True, "problems": []}  # the CLI wrote nothing
            else:
                removed = rollback_entry(agent, config, cli, scope, project_dir, before)
            journal["state"] = "failed-rolled-back" if removed["clean"] else "failed-needs-attention"
            write_journal(folder, journal)
            raise RegisterError("registration failed and was reverted: " + "; ".join(problems) if removed["clean"]
                                else "registration failed and could not be fully reverted: " + "; ".join(problems + removed["problems"]))
        journal["applied_sha256"] = file_sha(config)
        journal["state"] = "applied"
        write_journal(folder, journal)
    return {"applied": True, "agent": agent, "scope": scope, "backup_dir": str(folder), "entries_before": len(flat(before)),
            "entries_after": len(flat(after)), "original_sha256": preview["original_sha256"],
            "applied_sha256": journal["applied_sha256"]}


def rollback_entry(agent: str, config: Path, cli: str, scope: str, project_dir: Path, before: dict) -> dict:
    """Remove only our entry with the agent's own CLI; every other entry must match `before` afterwards."""
    where = _scope_name(agent, scope, project_dir)
    problems = []
    present = (where, NAME) in flat(snapshot(agent, config, project_dir))
    if present:
        code, out = run_cli(remove_command(agent, cli, scope), project_dir)
        if code != 0:
            problems.append(f"`mcp remove` exited with {code}: {out}")
    after = snapshot(agent, config, project_dir)
    if (where, NAME) in flat(after):
        problems.append("the entry is still present")
    problems += compare(before, after, (where, NAME))
    return {"clean": not problems, "problems": problems}


def _read_journal(folder: Path) -> dict:
    try:
        journal = json.loads((Path(folder) / "journal.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise RegisterError(f"cannot read the journal in {Path(folder).name}") from None
    if not isinstance(journal, dict) or "state" not in journal or "config" not in journal:
        raise RegisterError(f"the journal in {Path(folder).name} is incomplete")
    return journal


def rollback(backup_dir: Path, entry_only: bool = False, state_dir: Path | None = None) -> dict:
    """Undo one `apply`. Default: restore the original bytes, only if the config is exactly as apply left it."""
    folder = Path(backup_dir)
    journal = _read_journal(folder)
    config = Path(journal["config"])
    if journal["state"] != "applied":
        raise RegisterError(f"journal state is {journal['state']!r}, not 'applied'; nothing was changed"
                            + (" (run recover)" if journal["state"] in OPEN_STATES else ""))
    with config_lock(Path(state_dir) if state_dir else folder.parent.parent, config, folder):
        if entry_only:
            # Other entries may have changed legitimately since apply; compare with the file as it is now.
            current = snapshot(journal["agent"], config, Path(journal["project_dir"]))
            result = rollback_entry(journal["agent"], config, journal["cli"], journal["scope"],
                                    Path(journal["project_dir"]), current)
            result["mode"] = "entry-only"
            if result["clean"]:
                journal["state"] = "rolled-back-entry"
                write_journal(folder, journal)
            return result
        current = file_sha(config)
        if current != journal["applied_sha256"]:
            return {"clean": False, "mode": "refused", "problems": [
                f"{config.name} changed after registration (its hash differs from the one recorded by apply), so its "
                "bytes were not restored and nothing was changed; rollback --entry-only removes just our entry "
                "with the agent's own `mcp remove`, or restore config.bak by hand after comparing"]}
        try:
            if journal["original_sha256"] == ABSENT:
                config.unlink()  # the file did not exist before, and nobody has changed it since apply created it
            else:
                data = (folder / "config.bak").read_bytes()
                if hashlib.sha256(data).hexdigest() != journal["original_sha256"]:
                    raise RegisterError("config.bak does not match the recorded original; nothing was changed")
                atomic_write(config, data)
        except OSError as exc:
            # The replace is a single step, so the file is either fully restored or still as apply left it.
            return {"clean": False, "mode": "exact", "problems": [f"could not restore {config.name} ({type(exc).__name__}); "
                                                                   "the file is as apply left it"]}
        if (file_sha(config) or ABSENT) != journal["original_sha256"]:
            return {"clean": False, "mode": "exact", "problems": ["the restored file does not match the original hash"]}
        journal["state"] = "rolled-back-exact"
        write_journal(folder, journal)
        return {"clean": True, "mode": "exact", "problems": [], "restored_sha256": journal["original_sha256"]}


def recover(state_dir: Path) -> list[dict]:
    """Settle runs that stopped in an open state, then clear their locks. Run it only when no apply is running.

    Fails closed: a journal it cannot read, or a config whose other entries changed, is reported and left alone.
    """
    state_dir = Path(state_dir)
    report = []
    for folder in sorted((state_dir / "backups").glob("mcp-*")):
        try:
            journal = _read_journal(folder)
        except RegisterError as exc:
            report.append({"backup_dir": folder.name, "outcome": "needs-attention", "detail": str(exc)})
            continue
        if journal["state"] not in OPEN_STATES:
            continue
        config, agent, project_dir = Path(journal["config"]), journal["agent"], Path(journal["project_dir"])
        where = _scope_name(agent, journal["scope"], project_dir)
        outcome, detail = "needs-attention", ""
        try:
            if (file_sha(config) or ABSENT) == journal["original_sha256"]:
                outcome = "aborted-unchanged"
            elif journal["state"] in ("prepared", "backed-up"):
                outcome, detail = "aborted-unchanged", "this run never wrote; the config was changed by something else"
            else:
                now_snap = snapshot(agent, config, project_dir)
                problems = compare(journal["before_entries"], now_snap, (where, NAME))
                if problems:
                    detail = "; ".join(problems) + "; left as it is"
                elif (where, NAME) not in flat(now_snap):
                    outcome = "aborted-unchanged"
                else:
                    removed = rollback_entry(agent, config, journal["cli"], journal["scope"], project_dir, now_snap)
                    outcome = "recovered-entry-removed" if removed["clean"] else "needs-attention"
                    detail = "; ".join(removed["problems"])
        except RegisterError as exc:
            detail = str(exc)
        journal["state"] = outcome if outcome != "needs-attention" else "failed-needs-attention"
        write_journal(folder, journal)
        report.append({"backup_dir": folder.name, "outcome": outcome, "detail": detail})
    for lock in sorted((state_dir / "locks").glob("*.lock")):
        try:
            owner = Path(json.loads(lock.read_text(encoding="utf-8"))["backup_dir"])
            state = _read_journal(owner)["state"] if (owner / "journal.json").exists() else "never-started"
        except (OSError, ValueError, KeyError, RegisterError):
            report.append({"lock": lock.name, "outcome": "needs-attention", "detail": "unreadable lock; left in place"})
            continue
        if state in OPEN_STATES:
            report.append({"lock": lock.name, "outcome": "needs-attention", "detail": "its run is still open; left in place"})
        else:
            _release(lock)
            report.append({"lock": lock.name, "outcome": "released", "detail": f"run state {state}"})
    return report


# ---------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mcp_register", description="register omniagent-brain in an agent (preview first)")
    parser.add_argument("action", choices=["runtime", "plan", "apply", "rollback", "recover"])
    parser.add_argument("--agent", choices=AGENTS)
    parser.add_argument("--scope", choices=["local", "user"], default="local")
    parser.add_argument("--project", default="omniagent-os")
    parser.add_argument("--project-dir", type=Path, default=Path.cwd())
    parser.add_argument("--brain-dir", type=Path, default=brain.DEFAULT_DIR)
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--config", type=Path, help="override the agent's config file (tests)")
    parser.add_argument("--cli", help="override the agent CLI executable (tests)")
    parser.add_argument("--backup-dir", type=Path, help="for rollback: the backup folder printed by apply")
    parser.add_argument("--entry-only", action="store_true", help="rollback: remove only our entry with `mcp remove`")
    parser.add_argument("--expect-sha", help="apply: the original_sha256 shown by plan ('absent' if there was no file)")
    parser.add_argument("--yes", action="store_true", help="required by apply, which also needs an interactive terminal")
    args = parser.parse_args(argv)
    source = Path(brain.__file__).resolve()
    runtime_dir = args.state_dir / "runtime"
    try:
        if args.action == "rollback":
            if not args.backup_dir:
                raise RegisterError("rollback needs --backup-dir")
            how = "remove only the omniagent-brain entry" if args.entry_only else "restore the config's original bytes"
            brain.confirm_as_owner(f"Roll back {args.backup_dir.name}: {how}.", "rollback")
            result = rollback(args.backup_dir, args.entry_only, args.state_dir)
            print(json.dumps(result, indent=2))
            return 0 if result["clean"] else 2
        if args.action == "recover":
            brain.confirm_as_owner("Settle interrupted registrations. Only do this when no apply is running.", "recover")
            print(json.dumps(recover(args.state_dir), indent=2))
            return 0
        if args.action == "runtime":
            runtime = prepare_runtime(source, runtime_dir)
            command, cmd_args = server_command(runtime, args.brain_dir, args.project, "claude")
            print(json.dumps({"runtime": str(runtime), "python": command, "handshake_tools": handshake(command, cmd_args)}, indent=2))
            return 0
        if not args.agent:
            raise RegisterError("--agent is required")
        config = args.config or default_config(args.agent)
        if args.action == "plan":
            print(json.dumps(plan(args.agent, config, args.cli, args.scope, args.project_dir, args.brain_dir,
                                  args.project, runtime_dir, source), indent=2))
            return 0
        if not args.yes:
            raise RegisterError("apply needs --yes (after reading `plan`)")
        if not args.expect_sha:
            raise RegisterError("apply needs --expect-sha with the original_sha256 shown by `plan`")
        found = args.cli or shutil.which(args.agent if args.agent != "antigravity" else "agy")
        if not found:
            raise RegisterError("agent CLI not found")
        print(json.dumps(apply(args.agent, config, found, args.scope, args.project_dir, args.brain_dir, args.project,
                               runtime_dir, source, args.state_dir,
                               confirm=lambda summary: brain.confirm_as_owner(summary, args.agent),
                               expected_sha=args.expect_sha), indent=2))
        return 0
    except (RegisterError, brain.BrainError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
