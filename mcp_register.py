#!/usr/bin/env python3
"""Register the `omniagent-brain` MCP server in Claude Code, Codex or Antigravity (M3).

Lifecycle: inspect -> preview (`plan`) -> approve -> `apply` -> verify -> `rollback`, plus `recover` for a run
that was interrupted. `plan` and `runtime` never touch an agent's configuration.

`apply` takes the config's SHA-256 from the plan you reviewed and refuses if the file changed since. It
backs the file up, runs the agent's own `mcp add` (the agent owns its file format), then checks that exactly
one entry was added and every other entry is unchanged (compared after parsing, because the CLIs reformat
their files), and that the registered command answers an MCP handshake. Each stage is journaled.

Every setting outside the MCP tables is compared too (by hash of its parsed value, per top-level key and, for
Claude, per project key): a setting the CLI removed or changed fails the run, which is then reverted.

`rollback` restores the original bytes, but only while the config still has exactly the bytes recorded right
after `apply`. If anything changed it since, it refuses and leaves the file alone; `rollback --entry-only`
then removes just our entry with the agent's own `mcp remove`, and only while it is still the exact entry apply
registered.

One lock per config file and user, independent of `--state-dir`, held as an OS file lock: the OS releases it
when its process ends, however it ends, so "is the owner still running" never depends on a PID or a file's age.
A run that ends without settling its journal leaves an owner record that blocks new runs until `recover`.
Standard library only.

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
from omniagent import process_alive

if os.name == "nt":
    import msvcrt
else:
    import fcntl

NAME = "omniagent-brain"
AGENTS = ("claude", "codex", "antigravity")
EXPECTED_TOOLS = {t["name"] for t in brain.TOOLS}
HOME = Path.home()
DEFAULT_STATE = Path(__file__).resolve().parent / ".local"
# Journal states. A run that stopped in one of the open states is handled by `recover`.
OPEN_STATES = ("prepared", "backed-up", "writing", "verifying")
ABSENT = "absent"  # what the plan shows, and `--expect-sha` takes, for a config file that does not exist yet
SERVER_KEY = {"claude": "mcpServers", "codex": "mcp_servers", "antigravity": "mcpServers"}


class RegisterError(Exception):
    pass


class LockBusy(RegisterError):
    """Another process holds the config's lock, or whether one does could not be determined."""


def short(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()[:12]


def entry_sha(entry: dict) -> str:
    return hashlib.sha256(json.dumps(entry, sort_keys=True).encode("utf-8")).hexdigest()


def is_proposed(entry: dict, proposed: dict) -> bool:
    """The entry runs exactly the proposed command and carries nothing else: no environment, headers or extras
    beyond what `mcp add` itself writes for a stdio server (Claude adds `type` and an empty `env`)."""
    return (entry.get("command") == proposed["command"] and list(entry.get("args") or []) == proposed["args"]
            and set(entry) <= {"type", "command", "args", "env"} and entry.get("type", "stdio") == "stdio"
            and not entry.get("env"))


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


def _load(agent: str, config: Path) -> dict:
    """The parsed config ({} if there is no file). Errors never quote the file's content."""
    if file_sha(config) is None:
        return {}
    try:
        text = config.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError:
        raise RegisterError(f"{config.name} is not UTF-8 text; nothing was changed") from None
    try:
        doc = tomllib.loads(text) if agent == "codex" else json.loads(text) if text.strip() else {}
    except ValueError:  # JSON and TOML decode errors can quote file content, so say nothing more
        raise RegisterError(f"{config.name} is not valid {'TOML' if agent == 'codex' else 'JSON'}; nothing was changed") from None
    if not isinstance(doc, dict):
        raise RegisterError(f"{config.name} is not a JSON object; nothing was changed")
    return doc


def _projects(doc: dict, config: Path) -> dict[str, dict]:
    projects = doc.get("projects") or {}
    if not isinstance(projects, dict) or not all(isinstance(v, dict) for v in projects.values()):
        raise RegisterError(f"{config.name} has a project list in a shape this tool does not know; nothing was changed")
    return projects


def read_entries(agent: str, config: Path, project_dir: Path | None = None) -> dict[str, dict[str, dict]]:
    """{scope: {server name: entry}} read from the config file. Callers must hash, never print, the entries."""
    doc = _load(agent, config)
    scopes = {"user": _servers(doc.get(SERVER_KEY[agent]), config)}
    if agent != "claude":
        return scopes
    for key, value in _projects(doc, config).items():
        servers = _servers(value.get("mcpServers"), config)
        if servers:
            scopes["project:" + _norm(key)] = servers
    if project_dir is not None:
        scopes.setdefault("project:" + _norm(str(project_dir)), {})
    return scopes


def settings(agent: str, config: Path) -> dict[str, str]:
    """Every setting outside the MCP server tables: {name: hash of its parsed value}. Top-level keys, plus each
    key of each Claude project record. Formatting and TOML comments are not settings and are not compared."""
    doc, out = _load(agent, config), {}
    for key, value in doc.items():
        if key == SERVER_KEY[agent]:
            continue
        if agent == "claude" and key == "projects":
            for path, record in _projects(doc, config).items():
                out.update({f"projects[{path}].{name}": short(item) for name, item in record.items() if name != "mcpServers"})
            continue
        out[key] = short(value)
    return out


def settings_changes(before: dict[str, str], after: dict[str, str]) -> tuple[list[str], list[str]]:
    """(problems, added): a removed or changed setting is a problem; a new one is only reported. Names only,
    with the home folder in project paths replaced by `~`."""
    problems = [f"setting {brain.redact_paths(k)!r} was removed" for k in sorted(set(before) - set(after))]
    problems += [f"setting {brain.redact_paths(k)!r} changed" for k in sorted(before) if k in after and after[k] != before[k]]
    return problems, [brain.redact_paths(k) for k in sorted(set(after) - set(before))]


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

def lock_dir() -> Path:
    """Per user, not per --state-dir: every run on one config file meets the same lock, whatever folder it journals to.
    Not under AppData: the Microsoft Store Python redirects AppData writes into its own package folder, so two
    Pythons would see different locks."""
    return Path.home() / ".omniagent-os" / "register-locks"


def lock_id(config: Path) -> str:
    """Same id for every spelling of the same file (relative, `..`, case on Windows, symlinks)."""
    return hashlib.sha256(_norm(os.path.realpath(config)).encode("utf-8")).hexdigest()[:32]


def _owner_path(lid: str) -> Path:
    return lock_dir() / f"{lid}.owner.json"


def _lock_region(handle) -> None:
    handle.seek(0)
    if os.name == "nt":
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_region(handle) -> None:
    handle.seek(0)
    if os.name == "nt":
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def held(lid: str):
    """Hold the OS lock `<lid>.lock` without waiting. The OS drops it when the holding process ends, however it
    ends, so a held lock means a live owner. Busy and "could not tell" both raise LockBusy: fail closed."""
    path = lock_dir() / f"{lid}.lock"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(path, "a+b")  # never deleted: unlinking a lock file others may hold races on POSIX
    except OSError as exc:
        raise LockBusy(f"cannot open the registration lock ({type(exc).__name__}); nothing was changed") from None
    try:
        try:
            _lock_region(handle)
        except OSError:
            raise LockBusy("another registration for this config is running right now (or its lock could not be "
                           "checked); nothing was changed") from None
        try:
            yield
        finally:
            _unlock_region(handle)
    finally:
        handle.close()


def _settled(folder: Path) -> bool:
    """True when the run in `folder` never started or reached a final state. An unreadable journal is not settled."""
    if not (Path(folder) / "journal.json").exists():
        return True
    try:
        return _read_journal(folder)["state"] not in OPEN_STATES
    except RegisterError:
        return False


@contextmanager
def config_lock(config: Path, folder: Path):
    """Hold the config's OS lock and record this run as its owner. A run that ends with its journal still open
    (an exception, or a killed process) leaves the owner record behind, and new runs are refused until `recover`
    settles that journal. Nothing here is ever cleared for being old."""
    lid = lock_id(config)
    with held(lid):
        owner = _owner_path(lid)
        try:
            fd = os.open(owner, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise RegisterError(f"an earlier registration for {Path(config).name} was interrupted; "
                                "run `recover` with its --state-dir; nothing was changed") from None
        with os.fdopen(fd, "w", encoding="utf-8") as handle:  # pid and time are for people; liveness is the OS lock
            json.dump({"config": str(config), "backup_dir": os.path.realpath(folder), "pid": os.getpid(), "at": brain.now()}, handle)
        try:
            yield
        finally:
            if _settled(folder):
                owner.unlink(missing_ok=True)


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


def run_cli(cmd: list[str], cwd: Path | None, on_start=None) -> tuple[int, str]:
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace",
                                cwd=cwd, stdin=subprocess.DEVNULL)
    except OSError as exc:
        raise RegisterError(f"could not run the {Path(cmd[0]).name} command ({type(exc).__name__})") from None
    if on_start is not None:
        try:
            on_start(proc.pid)
        except Exception as exc:
            proc.kill()
            proc.wait()
            raise RegisterError(f"could not record child writer PID ({exc}); process terminated") from exc
    try:
        stdout, stderr = proc.communicate(timeout=120)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()
        raise RegisterError(f"could not run the {Path(cmd[0]).name} command (TimeoutExpired)") from None
    return proc.returncode, safe_text(stdout + "\n" + stderr)


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
        "entry_count": len(flat(snap)), "setting_count": len(settings(agent, config)),
        "changes": [f"add one entry {NAME!r} to scope {where} with the agent's own `mcp add`",
                    "the agent CLI rewrites the file (it may reformat it); every other MCP entry and every other "
                    "setting must compare equal after parsing, or the change is reverted; settings the CLI adds "
                    "are listed in the result"],
        "expected_output_sha256": None,
        "expected_output_note": "not predictable: the agent CLI writes the file; apply records the resulting "
                                "hash, and rollback restores the original bytes only while the file still has it",
        "backup_and_rollback": [
            "apply copies the config to <state-dir>/backups/mcp-<time>-<agent>/config.bak (may hold tokens; stays in .local)",
            "rollback --backup-dir <that folder>: byte-exact restore if the config is unchanged since apply",
            "if it changed since: rollback refuses; rollback --entry-only removes just this entry with `mcp remove`, "
            "and refuses if the entry itself was changed",
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


def restore_original(config: Path, folder: Path, original_sha: str) -> dict:
    """Put the original bytes back (or remove a file that did not exist). Callers check first that nothing but
    this run wrote the file. The replace is one step: the file is either restored or as it was."""
    try:
        if original_sha == ABSENT:
            config.unlink(missing_ok=True)
        else:
            data = (Path(folder) / "config.bak").read_bytes()
            if hashlib.sha256(data).hexdigest() != original_sha:
                return {"clean": False, "problems": ["config.bak does not match the recorded original; nothing was restored"]}
            atomic_write(config, data)
    except OSError as exc:
        return {"clean": False, "problems": [f"could not restore {config.name} ({type(exc).__name__}); the file was left as it was"]}
    if (file_sha(config) or ABSENT) != original_sha:
        return {"clean": False, "problems": ["the restored file does not match the original hash"]}
    return {"clean": True, "problems": []}


def apply(agent: str, config: Path, cli: str, scope: str, project_dir: Path, brain_dir: Path, project: str,
          runtime_dir: Path, source: Path, state_dir: Path, confirm=None, expected_sha: str | None = None) -> dict:
    """Register our entry. `expected_sha` is the plan's `original_sha256`; the run stops if the file differs."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    folder = Path(state_dir) / "backups" / f"mcp-{stamp}-{secrets.token_hex(3)}-{agent}"
    with config_lock(config, folder):  # first, so the checks below see the file no other run of ours is changing
        preview = plan(agent, config, cli, scope, project_dir, brain_dir, project, runtime_dir, source)
        if preview["problems"]:
            raise RegisterError("; ".join(preview["problems"]))
        if expected_sha is not None and preview["original_sha256"] != expected_sha:
            raise RegisterError(f"{config.name} changed since the plan you reviewed; run plan again; nothing was changed")
        scope = preview["scope"]
        where = _scope_name(agent, scope, project_dir)
        command, args = server_command(Path(preview["runtime"]), brain_dir, project, agent)
        proposed = {"command": command, "args": args}
        if confirm is not None:
            confirm(f"Register {NAME} for {agent} (scope {scope}) in {preview['config_path']}; a backup is taken first.")
        folder.mkdir(parents=True)
        before, before_settings = snapshot(agent, config, project_dir), settings(agent, config)
        journal = {"agent": agent, "scope": scope, "config": str(config), "cli": cli, "project_dir": str(project_dir),
                   "name": NAME, "proposed_entry": proposed, "before_entries": before,
                   "before_settings": before_settings, "original_sha256": preview["original_sha256"],
                   "written_entry_sha256": None, "applied_sha256": None, "applied_entry_sha256": None,
                   "state": "prepared", "at": brain.now()}
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
        journal["writer_status"] = "spawning"
        journal["child_pid"] = None
        write_journal(folder, journal)
        def _on_start(pid):
            journal["child_pid"] = pid
            journal["writer_status"] = "running"
            write_journal(folder, journal)
        try:
            code, out = run_cli(add_command(agent, cli, scope, command, args), project_dir, on_start=_on_start)
            journal["writer_status"] = "exited"
            journal["writer_exit_code"] = code
        except RegisterError as exc:  # the CLI did not start or timed out: check what it left, like any failure
            code, out = -1, str(exc)
            journal["writer_status"] = "failed"
            journal["writer_exit_code"] = -1
        finally:
            journal["child_pid"] = None
            write_journal(folder, journal)
        written = file_sha(config)
        problems, added = [], []
        if code != 0:
            problems.append(f"`mcp add` exited with {code}: {out}")
        try:
            entry = read_entries(agent, config, project_dir).get(where, {}).get(NAME)
            after = snapshot(agent, config, project_dir)
            problems += compare(before, after, (where, NAME))
            changed, added = settings_changes(before_settings, settings(agent, config))
            problems += [f"{p} (by the agent CLI, or by another program while it ran)" for p in changed]
        except RegisterError as exc:
            after, entry = {}, None
            problems.append(str(exc))
        journal["written_entry_sha256"] = entry_sha(entry) if entry is not None else None  # what recover may remove
        journal["state"] = "verifying"
        write_journal(folder, journal)
        if entry is None:
            problems.append("the new entry is not in the config")
        elif not is_proposed(entry, proposed):
            problems.append("the registered entry does not run the proposed command")
        else:
            try:
                handshake(entry["command"], list(entry.get("args") or []))
            except RegisterError as exc:
                problems.append(f"registered command failed the handshake: {exc}")
        if problems:
            current = file_sha(config)
            if current == original:
                removed = {"clean": True, "problems": []}  # the CLI wrote nothing
            elif current == written:
                removed = restore_original(config, folder, original or ABSENT)  # only the CLI wrote: undo all of it
            else:  # something else wrote after the CLI: keep that, remove only our entry
                expected_entry_sha = journal.get("written_entry_sha256")
                entry_matcher = ((lambda e: entry_sha(e) == expected_entry_sha) if expected_entry_sha
                                 else (lambda e: is_proposed(e, proposed)))
                removed = rollback_entry(agent, config, cli, scope, project_dir, before, matches=entry_matcher)
                removed["problems"] += settings_changes(before_settings, settings(agent, config))[0]
                removed["clean"] = not removed["problems"]
            journal["state"] = "failed-rolled-back" if removed["clean"] else "failed-needs-attention"
            write_journal(folder, journal)
            raise RegisterError("registration failed and was reverted: " + "; ".join(problems) if removed["clean"]
                                else "registration failed and could not be fully reverted (config.bak holds the "
                                     "original): " + "; ".join(problems + removed["problems"]))
        journal["applied_sha256"] = file_sha(config)
        journal["applied_entry_sha256"] = entry_sha(entry)
        journal["state"] = "applied"
        write_journal(folder, journal)
    return {"applied": True, "agent": agent, "scope": scope, "backup_dir": str(folder), "entries_before": len(flat(before)),
            "entries_after": len(flat(after)), "settings_added": added, "original_sha256": preview["original_sha256"],
            "applied_sha256": journal["applied_sha256"]}


def _manual_steps(agent: str, cli: str, scope: str) -> str:
    name = Path(cli or agent).name
    return (f"check it with `{name} mcp get {NAME}`; if you no longer want it, remove it yourself with "
            f"`{' '.join([name] + remove_command(agent, name, scope)[1:])}`")


def rollback_entry(agent: str, config: Path, cli: str, scope: str, project_dir: Path, before: dict,
                   matches=None) -> dict:
    """Remove only our entry with the agent's own CLI; every other entry must match `before` afterwards.

    `matches(entry)` must accept the entry found, or it is left alone (someone changed it since we wrote it).
    A setting the CLI removed or changed while removing is reverted by restoring the bytes it started from.
    """
    where = _scope_name(agent, scope, project_dir)
    problems = []
    entry = read_entries(agent, config, project_dir).get(where, {}).get(NAME)
    if entry is not None:
        if matches is not None and not matches(entry):
            return {"clean": False, "problems": [
                f"the {NAME} entry ({'project ' + brain.redact_paths(str(project_dir)) if where != 'user' else 'user'} "
                f"scope) is not the one this tool registered (its command, arguments or "
                f"other properties changed since), so it was left in place and nothing was changed; "
                + _manual_steps(agent, cli, scope)]}
        start, start_settings = config.read_bytes(), settings(agent, config)
        code, out = run_cli(remove_command(agent, cli, scope), project_dir)
        if code != 0:
            problems.append(f"`mcp remove` exited with {code}: {out}")
        changed = settings_changes(start_settings, settings(agent, config))[0]
        if changed:
            atomic_write(config, start)
            problems.append("`mcp remove` also changed other settings (" + "; ".join(changed) + "), so the file "
                            "was put back as it was before the remove; " + _manual_steps(agent, cli, scope))
            return {"clean": False, "problems": problems}
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


def rollback(backup_dir: Path, entry_only: bool = False) -> dict:
    """Undo one `apply`. Default: restore the original bytes, only if the config is exactly as apply left it."""
    folder = Path(backup_dir)
    journal = _read_journal(folder)
    config = Path(journal["config"])
    if journal["state"] != "applied":
        raise RegisterError(f"journal state is {journal['state']!r}, not 'applied'; nothing was changed"
                            + (" (run recover)" if journal["state"] in OPEN_STATES else ""))
    with config_lock(config, folder):
        if entry_only:
            recorded = journal.get("applied_entry_sha256")
            if not recorded:
                return {"clean": False, "mode": "refused", "problems": [
                    "this journal comes from an older version and does not record the registered entry, so it cannot "
                    "tell whether the entry was changed; nothing was changed; "
                    + _manual_steps(journal["agent"], journal["cli"], journal["scope"])]}
            # Other entries may have changed legitimately since apply; compare with the file as it is now.
            current = snapshot(journal["agent"], config, Path(journal["project_dir"]))
            result = rollback_entry(journal["agent"], config, journal["cli"], journal["scope"],
                                    Path(journal["project_dir"]), current, lambda entry: entry_sha(entry) == recorded)
            result["mode"] = "entry-only"
            if result["clean"]:
                journal["state"] = "rolled-back-entry"
                write_journal(folder, journal)
            return result
        if file_sha(config) != journal["applied_sha256"]:
            return {"clean": False, "mode": "refused", "problems": [
                f"{config.name} changed after registration (its hash differs from the one recorded by apply), so its "
                "bytes were not restored and nothing was changed; rollback --entry-only removes just our entry "
                "with the agent's own `mcp remove`, or restore config.bak by hand after comparing"]}
        result = restore_original(config, folder, journal["original_sha256"])
        result["mode"] = "exact"
        if result["clean"]:
            journal["state"] = "rolled-back-exact"
            write_journal(folder, journal)
            result["restored_sha256"] = journal["original_sha256"]
        return result


def _settle(folder: Path, journal: dict) -> tuple[str, str]:
    """Decide and do what an interrupted run needs. The caller holds the config's lock."""
    config, agent, project_dir = Path(journal["config"]), journal["agent"], Path(journal["project_dir"])
    where = _scope_name(agent, journal["scope"], project_dir)
    if journal["state"] == "writing":
        writer_status = journal.get("writer_status")
        child_pid = journal.get("child_pid")
        if writer_status != "exited":
            if child_pid and process_alive(child_pid) is True:
                return "in-progress", (f"writer process (PID {child_pid}) is still running; wait for it to exit "
                                      f"before recovering (or terminate it manually with taskkill/kill); left alone")
            if child_pid and process_alive(child_pid) is None:
                return "in-progress", (f"writer process (PID {child_pid}) status cannot be determined; "
                                      f"wait for it to exit or terminate it manually; left alone")
            if not child_pid:
                return "needs-attention", ("interrupted during 'writing' stage without evidence that the child writer "
                                          "exited; writer status is unknown; left alone for manual resolution (config.bak holds original)")
    if (file_sha(config) or ABSENT) == journal["original_sha256"]:
        return "aborted-unchanged", ""
    if journal["state"] in ("prepared", "backed-up"):
        return "aborted-unchanged", "this run never wrote; the config was changed by something else"
    if "before_settings" not in journal or "proposed_entry" not in journal:
        return "needs-attention", "journal from an older version; compare the config with config.bak by hand"
    now_snap = snapshot(agent, config, project_dir)
    problems = compare(journal["before_entries"], now_snap, (where, NAME))
    problems += settings_changes(journal["before_settings"], settings(agent, config))[0]
    if problems:
        return "needs-attention", "; ".join(problems) + "; left as it is (config.bak holds the original)"
    if (where, NAME) not in flat(now_snap):
        return "aborted-unchanged", ""
    written = journal.get("written_entry_sha256")  # None if the run stopped while the CLI was still running
    removed = rollback_entry(agent, config, journal["cli"], journal["scope"], project_dir, now_snap,
                             (lambda e: entry_sha(e) == written) if written else
                             (lambda e: is_proposed(e, journal["proposed_entry"])))
    return ("recovered-entry-removed" if removed["clean"] else "needs-attention"), "; ".join(removed["problems"])


def recover(state_dir: Path) -> list[dict]:
    """Settle runs in `state_dir` that stopped in an open state, then clear owner records of settled runs.

    A run whose config lock is held by a live process is left completely alone ("in-progress"), as is anything
    whose lock state cannot be determined. Fails closed: an unreadable journal, or a config whose other entries
    or settings changed, is reported and left as it is.
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
        lid = lock_id(Path(journal["config"]))
        try:
            with held(lid):
                journal = _read_journal(folder)  # read again under the lock
                if journal["state"] not in OPEN_STATES:
                    continue
                try:
                    outcome, detail = _settle(folder, journal)
                except RegisterError as exc:
                    outcome, detail = "needs-attention", str(exc)
                if outcome == "in-progress":
                    report.append({"backup_dir": folder.name, "outcome": "in-progress", "detail": detail})
                    continue
                journal["state"] = outcome if outcome != "needs-attention" else "failed-needs-attention"
                write_journal(folder, journal)
                owner = _owner_path(lid)
                if _owner_folder(owner) == _norm(os.path.realpath(folder)):
                    owner.unlink(missing_ok=True)
        except LockBusy as exc:
            report.append({"backup_dir": folder.name, "outcome": "in-progress", "detail": f"{exc}; left alone"})
            continue
        report.append({"backup_dir": folder.name, "outcome": outcome, "detail": detail})
    for owner in sorted(lock_dir().glob("*.owner.json")) if lock_dir().exists() else []:
        lid = owner.name.split(".")[0]
        try:
            with held(lid):
                folder = _owner_folder(owner)
                if folder is None:
                    if owner.exists():
                        report.append({"lock": lid, "outcome": "needs-attention", "detail": "unreadable owner record; left in place"})
                    continue
                if not (Path(folder) / "journal.json").exists():
                    state = "never-started"
                else:
                    try:
                        state = _read_journal(Path(folder))["state"]
                    except RegisterError as exc:
                        report.append({"lock": lid, "outcome": "needs-attention", "detail": f"{exc}; left in place"})
                        continue
                if state in OPEN_STATES:
                    report.append({"lock": lid, "outcome": "needs-attention", "detail": "its run is still open in "
                                   f"{brain.redact_paths(str(Path(folder).parent.parent))}; run recover with that --state-dir"})
                    continue
                owner.unlink(missing_ok=True)
                report.append({"lock": lid, "outcome": "released", "detail": f"run state {state}"})
        except LockBusy as exc:
            report.append({"lock": lid, "outcome": "in-progress", "detail": f"{exc}; left alone"})
    return report


def _owner_folder(owner: Path) -> str | None:
    """The normalized backup folder an owner record names, or None if it is missing or unreadable."""
    try:
        return _norm(os.path.realpath(json.loads(owner.read_text(encoding="utf-8"))["backup_dir"]))
    except (OSError, ValueError, KeyError, TypeError):
        return None


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
            result = rollback(args.backup_dir, args.entry_only)
            print(json.dumps(result, indent=2))
            return 0 if result["clean"] else 2
        if args.action == "recover":
            brain.confirm_as_owner("Settle interrupted registrations. A run that is still going is detected by its "
                                   "lock and left alone.", "recover")
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
