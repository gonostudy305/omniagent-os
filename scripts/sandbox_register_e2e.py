"""Full mcp_register lifecycle against the REAL Claude Code / Codex CLIs, inside a throwaway profile only.

For each agent, twice (no config yet; a config with unrelated custom settings): plan -> apply (with the
plan's hash) -> the agent's own CLI lists the entry -> start the registered command and propose a note ->
check project and identity -> rollback -> config bytes compared with the original. Every profile variable
points into a temporary folder (see sandbox_native_mcp.py); the real profile files are compared by size and
modification time only. No sign-in, no model call. Usage: python -I scripts/sandbox_register_e2e.py
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import brain  # noqa: E402
import mcp_register as reg  # noqa: E402
import sandbox_native_mcp as sandbox  # noqa: E402

SEEDS = {
    "claude": json.dumps({"theme": "dark", "customSetting": {"keep": [1, 2, 3]},
                          "mcpServers": {"other": {"type": "stdio", "command": "node", "args": ["other.js"], "env": {}}}},
                         indent=4).replace("\n", "\r\n").encode("utf-8"),
    "codex": b'# my settings\r\nmodel = "custom-model"\r\n\r\n[mcp_servers.other]\r\ncommand = "node"\r\nargs = ["other.js"]\r\n',
}


def tree(home: Path) -> dict:
    return {p.relative_to(home).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in home.rglob("*") if p.is_file()}


def propose_through(entry: dict, title: str) -> int:
    messages = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                 "params": {"name": "brain_propose", "arguments": {"kind": "fact", "title": title, "body": "From the registered entry."}}}]
    return subprocess.run([entry["command"], *entry["args"]], input="".join(json.dumps(m) + "\n" for m in messages),
                          capture_output=True, text=True, encoding="utf-8", timeout=60).returncode


def scenario(agent: str, exe: str, seeded: bool, base: Path) -> list[dict]:
    home, project_dir, brain_dir, state = base / "home", base / "project", base / "brain", base / "state"
    project_dir.mkdir(parents=True)
    env = sandbox.sandbox_env(home)  # built from the current environment, so before clearing it
    os.environ.clear()
    os.environ.update(env)  # the CLIs started by mcp_register inherit this
    config = reg.default_config(agent)
    if seeded:
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_bytes(SEEDS[agent])
    original = config.read_bytes() if config.exists() else None
    tree_before = tree(home)
    label = f"{agent} ({'existing config with custom settings' if seeded else 'no config yet'})"
    rows = []

    def row(check, ok, detail=""):
        rows.append({"scenario": label, "check": check, "status": "PASS" if ok else "FAIL", "detail": detail})

    scope = "local" if agent == "claude" else "user"
    preview = reg.plan(agent, config, exe, scope, project_dir, brain_dir, "sandbox-demo", state / "runtime", ROOT / "brain.py")
    row("plan: no problems, nothing written", not preview["problems"] and tree(home) == tree_before,
        f"original_sha256={preview['original_sha256'][:12]} entries={preview['entry_count']}")
    result = reg.apply(agent, config, exe, scope, project_dir, brain_dir, "sandbox-demo", state / "runtime",
                       ROOT / "brain.py", state, expected_sha=preview["original_sha256"])
    row("apply with the plan's hash", result["applied"], f"entries {result['entries_before']} -> {result['entries_after']}")
    where = reg._scope_name(agent, result["scope"], project_dir)
    entry = reg.read_entries(agent, config, project_dir)[where][reg.NAME]
    listed = subprocess.run([exe, "mcp", "get" if agent == "codex" else "list", *([reg.NAME] if agent == "codex" else [])],
                            cwd=project_dir, capture_output=True, text=True, encoding="utf-8", errors="replace",
                            stdin=subprocess.DEVNULL, timeout=120)
    seen = listed.returncode == 0 and reg.NAME in listed.stdout
    connected = agent != "claude" or "Connected" in listed.stdout.split(reg.NAME, 1)[-1].splitlines()[0]
    row(f"agent's own `mcp {'get' if agent == 'codex' else 'list'}` sees the entry"
        + (" and connects" if agent == "claude" else ""), seen and connected)
    code = propose_through(entry, f"E2E {agent}")
    notes = brain.Brain(brain_dir, "sandbox-demo").search(include_unverified=True)
    row("registered command serves project 'sandbox-demo' as this agent", code == 0 and
        [(n["source_agent"], n["status"]) for n in notes] == [(agent, "inbox")], f"notes={len(notes)}")
    rolled = reg.rollback(Path(result["backup_dir"]))
    after = config.read_bytes() if config.exists() else None
    row("rollback restores the config byte for byte", rolled["clean"] and after == original,
        f"mode={rolled.get('mode')} problems={rolled['problems']}")
    extra = sorted(set(tree(home)) - set(tree_before))
    changed = sorted(k for k in tree_before if tree(home).get(k) != tree_before[k])
    rows.append({"scenario": label, "check": "other files in the throwaway profile after rollback", "status": "INFO",
                 "detail": f"new: {extra or 'none'}; changed: {changed or 'none'}"})
    return rows


def main() -> int:
    saved_env = dict(os.environ)
    before = sandbox.fingerprint()
    results = []
    clients = {"claude": shutil.which("claude"), "codex": shutil.which("codex")}
    try:
        for agent, exe in clients.items():
            if not exe:
                results.append({"scenario": agent, "check": "cli present", "status": "NOT_VERIFIED", "detail": "not on PATH"})
                continue
            version = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=60).stdout.strip()
            results.append({"scenario": agent, "check": "version", "status": "INFO", "detail": version})
            for seeded in (False, True):
                with tempfile.TemporaryDirectory(prefix="omniagent-reg-") as temp:
                    try:
                        results += scenario(agent, exe, seeded, Path(temp))
                    except reg.RegisterError as exc:
                        results.append({"scenario": agent, "check": "run", "status": "FAIL", "detail": str(exc)})
                    finally:
                        os.environ.clear()
                        os.environ.update(saved_env)
    finally:
        os.environ.clear()
        os.environ.update(saved_env)
    after = sandbox.fingerprint()
    results.append({"scenario": "-", "check": "real profile files unchanged (size, mtime)",
                    "status": "PASS" if before == after else "FAIL",
                    "detail": ", ".join(k for k in sandbox.WATCHED if before[k] != after[k]) or "all unchanged"})
    results.append({"scenario": "antigravity", "check": "any", "status": "NOT_VERIFIED",
                    "detail": "no `agy` CLI on PATH; covered only by the fake-CLI tests"})
    sys.stdout.reconfigure(encoding="utf-8")
    home_text = str(sandbox.REAL_HOME)
    print(json.dumps(results, indent=2, ensure_ascii=False).replace(home_text.replace("\\", "\\\\"), "~").replace(home_text, "~"))
    return 1 if any(r["status"] == "FAIL" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
