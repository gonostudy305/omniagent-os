"""Full mcp_register lifecycle against the REAL Claude Code / Codex CLIs, inside a throwaway profile only.

For each agent, twice (no config yet; a config with unrelated custom settings): plan -> apply (with the
plan's hash) -> unrelated settings compared -> the agent's own CLI lists the entry -> start the registered
command and propose a note -> check project and identity -> rollback -> config bytes compared with the
original -> apply again, then `rollback --entry-only` refuses a same-name entry the user changed and removes the
unchanged one. A config holding `theme` must be blocked before invoking the Claude CLI,
without creating a backup or changing the config. An already-present entry must also be refused.

Profile variables point into a temporary folder (see sandbox_native_mcp.py); this is environment
redirection, not an OS sandbox. Real profile files are compared by size and modification time only.
No sign-in, no model call; the script itself invokes the registered MCP command.
Usage: python -I scripts/sandbox_register_e2e.py
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import brain  # noqa: E402
import mcp_register as reg  # noqa: E402
import sandbox_native_mcp as sandbox  # noqa: E402

SEEDS = {
    "claude": json.dumps({"model": "custom-model", "hooks": {"PreToolUse": []}, "customSetting": {"keep": [1, 2, 3]},
                          "mcpServers": {"other": {"type": "stdio", "command": "node", "args": ["other.js"], "env": {}}}},
                         indent=4).replace("\n", "\r\n").encode("utf-8"),
    "claude-theme": json.dumps({"theme": "dark", "customSetting": {"keep": [1, 2, 3]}}, indent=4).encode("utf-8"),
    "codex": b'# my settings\r\nmodel = "custom-model"\r\n\r\n[profiles.p]\r\nmodel = "other"\r\n\r\n'
             b'[mcp_servers.other]\r\ncommand = "node"\r\nargs = ["other.js"]\r\n',
}


def edit_our_entry(agent: str, config: Path, project_dir: Path) -> None:
    """The user gives the same-name entry an environment variable, as a hand edit would."""
    if agent == "codex":
        text = config.read_text(encoding="utf-8")
        config.write_text(text.replace(f"[mcp_servers.{reg.NAME}]\n", f'[mcp_servers.{reg.NAME}]\nenv = {{ X = "1" }}\n', 1),
                          encoding="utf-8")
        return
    doc = json.loads(config.read_text(encoding="utf-8"))
    for key, record in doc.get("projects", {}).items():
        if reg._norm(key) == reg._norm(str(project_dir)) and reg.NAME in record.get("mcpServers", {}):
            record["mcpServers"][reg.NAME]["env"] = {"X": "1"}
    config.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def tree(home: Path) -> dict:
    return {p.relative_to(home).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in home.rglob("*") if p.is_file()}


def propose_through(entry: dict, title: str, *, env: dict, cwd: Path) -> int:
    messages = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                 "params": {"name": "brain_propose", "arguments": {"kind": "fact", "title": title, "body": "From the registered entry."}}}]
    return subprocess.run([entry["command"], *entry["args"]], input="".join(json.dumps(m) + "\n" for m in messages),
                          env=env, cwd=cwd, capture_output=True, text=True, encoding="utf-8", timeout=60).returncode


def scenario(agent: str, exe: str, seed: str | None, base: Path) -> list[dict]:
    home, project_dir, brain_dir, state = base / "home", base / "project", base / "brain", base / "state"
    env = sandbox.sandbox_env(home)  # built from the current environment, so before clearing it
    sandbox.create_project(project_dir, env)
    os.environ.clear()
    os.environ.update(env)  # the CLIs started by mcp_register inherit this
    config = reg.default_config(agent)
    if seed:
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_bytes(SEEDS[seed])
    original = config.read_bytes() if config.exists() else None
    settings_before = reg.settings(agent, config)
    tree_before = tree(home)
    label = f"{agent} ({ {None: 'no config yet', 'claude-theme': 'existing config with a theme key'}.get(seed, 'existing config with custom settings')})"
    rows = []

    def row(check, ok, detail=""):
        rows.append({"scenario": label, "check": check, "status": "PASS" if ok else "FAIL", "detail": detail})

    scope = "local" if agent == "claude" else "user"
    preview = reg.plan(agent, config, exe, scope, project_dir, brain_dir, "sandbox-demo", state / "runtime", ROOT / "brain.py")
    def do_apply(reviewed):
        return reg.apply(agent, config, exe, scope, project_dir, brain_dir, "sandbox-demo", state / "runtime",
                         ROOT / "brain.py", state, expected_sha=reviewed["original_sha256"])
    if seed == "claude-theme":
        row("plan blocks theme and leaves the profile unchanged",
            any("theme" in p for p in preview["problems"]) and tree(home) == tree_before)
        with mock.patch.object(reg, "run_cli", side_effect=AssertionError("theme must block before native CLI")) as cli:
            try:
                do_apply(preview)
            except reg.RegisterError as exc:
                row("apply blocks theme before native CLI, without backup or config changes",
                    "before running the CLI" in str(exc) and not cli.called
                    and config.read_bytes() == original and not (state / "backups").exists(), str(exc))
            else:
                row("apply blocks theme before native CLI, without backup or config changes", False)
        return rows
    row("plan: no problems, config unchanged", not preview["problems"] and tree(home) == tree_before,
        f"original_sha256={preview['original_sha256'][:12]} entries={preview['entry_count']} settings={preview['setting_count']}")
    result = do_apply(preview)
    row("apply with the plan's hash", result["applied"], f"entries {result['entries_before']} -> {result['entries_after']}")
    kept = reg.settings(agent, config)
    row("every earlier setting is unchanged after apply", all(kept.get(k) == v for k, v in settings_before.items()),
        f"settings_added={result['settings_added'] or 'none'}")
    applied = config.read_bytes()
    collision = reg.plan(agent, config, exe, scope, project_dir, brain_dir, "sandbox-demo", state / "runtime", ROOT / "brain.py")
    with mock.patch.object(reg, "run_cli", side_effect=AssertionError("collision must block before native CLI")) as cli:
        try:
            do_apply(collision)
        except reg.RegisterError as exc:
            row("existing omniagent-brain entry is refused without invoking native CLI",
                any("already exists" in p for p in collision["problems"]) and "already exists" in str(exc)
                and not cli.called and config.read_bytes() == applied)
        else:
            row("existing omniagent-brain entry is refused without invoking native CLI", False)
    where = reg._scope_name(agent, result["scope"], project_dir)
    entry = reg.read_entries(agent, config, project_dir)[where][reg.NAME]
    listed = subprocess.run([exe, "mcp", "get" if agent == "codex" else "list", *([reg.NAME] if agent == "codex" else [])],
                            env=env, cwd=project_dir, capture_output=True, text=True, encoding="utf-8", errors="replace",
                            stdin=subprocess.DEVNULL, timeout=120)
    seen = listed.returncode == 0 and reg.NAME in listed.stdout
    connected = agent != "claude" or "Connected" in listed.stdout.split(reg.NAME, 1)[-1].splitlines()[0]
    row(f"agent's own `mcp {'get' if agent == 'codex' else 'list'}` sees the entry"
        + (" and connects" if agent == "claude" else ""), seen and connected)
    code = propose_through(entry, f"E2E {agent}", env=env, cwd=project_dir)
    notes = brain.Brain(brain_dir, "sandbox-demo").search(include_unverified=True)
    row("registered command serves project 'sandbox-demo' as this agent", code == 0 and
        [(n["source_agent"], n["status"]) for n in notes] == [(agent, "inbox")], f"notes={len(notes)}")
    rolled = reg.rollback(Path(result["backup_dir"]))
    after = config.read_bytes() if config.exists() else None
    row("rollback restores the config byte for byte", rolled["clean"] and after == original,
        f"mode={rolled.get('mode')} problems={rolled['problems']}")
    second_preview = reg.plan(agent, config, exe, scope, project_dir, brain_dir, "sandbox-demo", state / "runtime", ROOT / "brain.py")
    again = Path(do_apply(second_preview)["backup_dir"])
    applied_bytes, applied_settings = config.read_bytes(), reg.settings(agent, config)
    edit_our_entry(agent, config, project_dir)
    edited = config.read_bytes()
    refused = reg.rollback(again, entry_only=True)
    row("rollback --entry-only refuses a same-name entry the user changed",
        not refused["clean"] and config.read_bytes() == edited, (refused["problems"] or ["?"])[0][:120])
    config.write_bytes(applied_bytes)  # the user undoes the edit
    removed = reg.rollback(again, entry_only=True)
    names = {n for servers in reg.read_entries(agent, config, project_dir).values() for n in servers}
    row("rollback --entry-only removes the unchanged entry and keeps every setting",
        removed["clean"] and reg.NAME not in names and reg.settings(agent, config) == applied_settings,
        f"problems={removed['problems']}")
    extra = sorted(set(tree(home)) - set(tree_before))
    changed = sorted(k for k in tree_before if tree(home).get(k) != tree_before[k])
    rows.append({"scenario": label, "check": "other files in the throwaway profile after rollback", "status": "INFO",
                 "detail": f"new: {extra or 'none'}; changed: {changed or 'none'}"})
    return rows


def main() -> int:
    saved_env = dict(os.environ)
    before = sandbox.fingerprint()
    real_locks = sandbox.REAL_HOME / ".omniagent-os"
    locks_before = real_locks.exists()
    results = []
    clients = {"claude": shutil.which("claude"), "codex": shutil.which("codex")}
    try:
        for agent, exe in clients.items():
            if not exe:
                results.append({"scenario": agent, "check": "cli present", "status": "NOT_VERIFIED", "detail": "not on PATH"})
                continue
            with sandbox.temporary_profile("omniagent-version-") as temp:
                base = Path(temp)
                project = base / "project"
                env = sandbox.sandbox_env(base / "home")
                sandbox.create_project(project, env)
                code, version = sandbox.run([exe, "--version"], env, project)
            results.append({"scenario": agent, "check": "version", "status": "INFO" if code == 0 else "FAIL", "detail": version})
            for seed in ((None, "claude", "claude-theme") if agent == "claude" else (None, "codex")):
                with sandbox.temporary_profile("omniagent-reg-") as temp:
                    try:
                        results += scenario(agent, exe, seed, Path(temp))
                    except reg.RegisterError as exc:
                        results.append({"scenario": agent, "check": "run", "status": "FAIL", "detail": str(exc)})
                    finally:
                        os.environ.clear()
                        os.environ.update(saved_env)
            results.append({"scenario": agent, "check": "actual model invokes brain_search / brain_propose",
                            "status": "NOT_VERIFIED", "detail": "No sign-in or credentials copied; direct scripted MCP calls are not model calls."})
    finally:
        os.environ.clear()
        os.environ.update(saved_env)
    after = sandbox.fingerprint()
    results.append({"scenario": "-", "check": "real profile files unchanged (size, mtime)",
                    "status": "PASS" if before == after else "FAIL",
                    "detail": ", ".join(k for k in sandbox.WATCHED if before[k] != after[k]) or "all unchanged"})
    results.append({"scenario": "-", "check": "no lock folder created in the real home",
                    "status": "PASS" if real_locks.exists() == locks_before else "FAIL", "detail": "~/.omniagent-os"})
    results.append({"scenario": "antigravity", "check": "any", "status": "NOT_VERIFIED",
                    "detail": "no `agy` CLI on PATH; covered only by the fake-CLI tests"})
    sys.stdout.reconfigure(encoding="utf-8")
    home_text = str(sandbox.REAL_HOME)
    print(json.dumps(results, indent=2, ensure_ascii=False).replace(home_text.replace("\\", "\\\\"), "~").replace(home_text, "~"))
    return 1 if any(r["status"] == "FAIL" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
