#!/usr/bin/env python3
"""Register the `omniagent-brain` MCP server in Claude Code, Codex or Antigravity (M3).

Preview first: `plan` and `runtime` never touch an agent's configuration. `apply` backs the config up,
runs the agent's own `mcp add` command, then checks that exactly one entry was added and every other
entry is unchanged (compared after parsing, not byte for byte, because the CLIs reformat their files).
`rollback` runs `mcp remove` and checks the same. Standard library only.

Agent config files can hold tokens. This tool therefore never prints an entry's value: output carries
names, counts and short hashes only, and a CLI's own output is shown only when it passes the secret scan.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

import brain

NAME = "omniagent-brain"
AGENTS = ("claude", "codex", "antigravity")
EXPECTED_TOOLS = {t["name"] for t in brain.TOOLS}
HOME = Path.home()
DEFAULT_CONFIG = {
    "claude": HOME / ".claude.json",
    "codex": HOME / ".codex" / "config.toml",
    "antigravity": HOME / ".gemini" / "config" / "mcp_config.json",
}
DEFAULT_STATE = Path(__file__).resolve().parent / ".local"


class RegisterError(Exception):
    pass


def short(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------- M3.0 runtime snapshot + handshake

def prepare_runtime(source: Path, runtime_dir: Path) -> Path:
    """Copy brain.py to `<runtime_dir>/brain-<12 hex of sha256>.py` so a branch switch cannot break the server."""
    data = Path(source).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    target = Path(runtime_dir) / f"brain-{digest[:12]}.py"
    if not (target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == digest):
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, target)
    if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
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


def read_entries(agent: str, config: Path, project_dir: Path | None = None) -> dict[str, dict[str, dict]]:
    """{scope: {server name: entry}} read from the config file. Callers must hash, never print, the entries."""
    if not config.exists():
        return {}
    text = config.read_text(encoding="utf-8-sig")
    try:
        if agent == "codex":
            return {"user": dict(tomllib.loads(text).get("mcp_servers", {}))}
        doc = json.loads(text) if text.strip() else {}
    except ValueError:  # JSON and TOML decode errors can quote file content, so say nothing more
        raise RegisterError(f"{config.name} is not valid {'TOML' if agent == 'codex' else 'JSON'}; nothing was changed") from None
    if agent == "antigravity":
        return {"user": dict(doc.get("mcpServers", {}))}
    scopes = {"user": dict(doc.get("mcpServers", {}))}
    for key, value in (doc.get("projects") or {}).items():
        if value.get("mcpServers"):
            scopes["project:" + _norm(key)] = dict(value["mcpServers"])
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


# ---------------------------------------------------------------- the agent CLIs

def _scope_name(agent: str, scope: str, project_dir: Path) -> str:
    return "project:" + _norm(str(project_dir)) if agent == "claude" and scope == "local" else "user"


def add_command(agent: str, cli: str, scope: str, command: str, args: list[str]) -> list[str]:
    tail = ["--", command] + list(args)
    if agent == "claude":
        return [cli, "mcp", "add", "--scope", scope, NAME] + tail
    if agent == "codex":
        return [cli, "mcp", "add", NAME] + tail
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


# ---------------------------------------------------------------- plan / apply / rollback

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
        "agent": agent, "scope": scope, "config_file": config.name, "cli": Path(found).name if found else None,
        "command": add_command(agent, found or agent, scope, command, args),
        "runtime": str(runtime), "handshake_tools": tools,
        "existing_entries": {s: sorted(v) for s, v in snap.items() if s == where or s == "user"},
        "entry_count": len(flat(snap)), "problems": problems, "writes_nothing": True,
    }


def apply(agent: str, config: Path, cli: str, scope: str, project_dir: Path, brain_dir: Path, project: str,
          runtime_dir: Path, source: Path, state_dir: Path, confirm=None) -> dict:
    preview = plan(agent, config, cli, scope, project_dir, brain_dir, project, runtime_dir, source)
    if preview["problems"]:
        raise RegisterError("; ".join(preview["problems"]))
    scope = preview["scope"]
    where = _scope_name(agent, scope, project_dir)
    if confirm is not None:
        confirm(f"Register {NAME} for {agent} (scope {scope}) in {config.name}; a backup is taken first.")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    folder = Path(state_dir) / "backups" / f"mcp-{stamp}-{agent}"
    folder.mkdir(parents=True, exist_ok=True)
    before = snapshot(agent, config, project_dir)
    backed_up = config.exists()
    if backed_up:
        shutil.copy2(config, folder / "config.bak")  # may contain tokens: stays under .local, never in git
        if hashlib.sha256((folder / "config.bak").read_bytes()).hexdigest() != hashlib.sha256(config.read_bytes()).hexdigest():
            raise RegisterError("backup copy does not match the config; nothing was changed")
    journal = {"agent": agent, "scope": scope, "config": str(config), "cli": cli, "project_dir": str(project_dir),
               "name": NAME, "before_entries": {k: v for k, v in before.items()}, "state": "applying", "backup": backed_up}
    (folder / "journal.json").write_text(json.dumps(journal, indent=2), encoding="utf-8")
    code, out = run_cli(add_command(agent, cli, scope, *server_command(Path(preview["runtime"]), brain_dir, project, agent)),
                        project_dir)
    problems = []
    if code != 0:
        problems.append(f"`mcp add` exited with {code}: {out}")
    after = snapshot(agent, config, project_dir)
    problems += compare(before, after, (where, NAME))
    entry = read_entries(agent, config, project_dir).get(where, {}).get(NAME)
    if entry is None:
        problems.append("the new entry is not in the config")
    else:
        try:
            handshake(entry.get("command", ""), list(entry.get("args", [])))
        except RegisterError as exc:
            problems.append(f"registered command failed the handshake: {exc}")
    if problems:
        removed = rollback_entry(agent, config, cli, scope, project_dir, before)
        journal["state"] = "failed-rolled-back" if removed["clean"] else "failed-needs-attention"
        (folder / "journal.json").write_text(json.dumps(journal, indent=2), encoding="utf-8")
        raise RegisterError("registration failed and was reverted: " + "; ".join(problems) if removed["clean"]
                            else "registration failed and could not be fully reverted: " + "; ".join(problems + removed["problems"]))
    journal["state"] = "applied"
    (folder / "journal.json").write_text(json.dumps(journal, indent=2), encoding="utf-8")
    return {"applied": True, "agent": agent, "scope": scope, "backup_dir": str(folder), "entries_before": len(flat(before)),
            "entries_after": len(flat(after))}


def rollback_entry(agent: str, config: Path, cli: str, scope: str, project_dir: Path, before: dict) -> dict:
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


def rollback(backup_dir: Path) -> dict:
    journal = json.loads((Path(backup_dir) / "journal.json").read_text(encoding="utf-8"))
    result = rollback_entry(journal["agent"], Path(journal["config"]), journal["cli"], journal["scope"],
                            Path(journal["project_dir"]), journal["before_entries"])
    if result["clean"]:
        journal["state"] = "rolled-back"
        (Path(backup_dir) / "journal.json").write_text(json.dumps(journal, indent=2), encoding="utf-8")
    return result


# ---------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mcp_register", description="register omniagent-brain in an agent (preview first)")
    parser.add_argument("action", choices=["runtime", "plan", "apply", "rollback"])
    parser.add_argument("--agent", choices=AGENTS)
    parser.add_argument("--scope", choices=["local", "user"], default="local")
    parser.add_argument("--project", default="omniagent-os")
    parser.add_argument("--project-dir", type=Path, default=Path.cwd())
    parser.add_argument("--brain-dir", type=Path, default=brain.DEFAULT_DIR)
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--config", type=Path, help="override the agent's config file (tests)")
    parser.add_argument("--cli", help="override the agent CLI executable (tests)")
    parser.add_argument("--backup-dir", type=Path, help="for rollback: the backup folder printed by apply")
    parser.add_argument("--yes", action="store_true", help="required by apply, which also needs an interactive terminal")
    args = parser.parse_args(argv)
    source = Path(brain.__file__).resolve()
    runtime_dir = args.state_dir / "runtime"
    try:
        if args.action == "rollback":
            if not args.backup_dir:
                raise RegisterError("rollback needs --backup-dir")
            brain.confirm_as_owner("Remove the omniagent-brain entry using the recorded journal.", "rollback")
            print(json.dumps(rollback(args.backup_dir), indent=2))
            return 0
        if args.action == "runtime":
            runtime = prepare_runtime(source, runtime_dir)
            command, cmd_args = server_command(runtime, args.brain_dir, args.project, "claude")
            print(json.dumps({"runtime": str(runtime), "python": command, "handshake_tools": handshake(command, cmd_args)}, indent=2))
            return 0
        if not args.agent:
            raise RegisterError("--agent is required")
        config = args.config or DEFAULT_CONFIG[args.agent]
        if args.action == "plan":
            print(json.dumps(plan(args.agent, config, args.cli, args.scope, args.project_dir, args.brain_dir,
                                  args.project, runtime_dir, source), indent=2))
            return 0
        if not args.yes:
            raise RegisterError("apply needs --yes (after reading `plan`)")
        found = args.cli or shutil.which(args.agent if args.agent != "antigravity" else "agy")
        if not found:
            raise RegisterError("agent CLI not found")
        print(json.dumps(apply(args.agent, config, found, args.scope, args.project_dir, args.brain_dir, args.project,
                               runtime_dir, source, args.state_dir,
                               confirm=lambda summary: brain.confirm_as_owner(summary, args.agent)), indent=2))
        return 0
    except (RegisterError, brain.BrainError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
