"""Register omniagent-brain in REAL Claude Code / Codex CLIs, but only inside a throwaway profile.

Profile, cache and temporary-directory variables point into a temporary folder. This environment
redirection is not an OS sandbox and cannot constrain an arbitrary CLI. The real profile files are compared by
size and modification time before and after (their content is never read). Nothing here signs in,
calls a model or invokes a tool: it shows whether each native CLI accepts, lists and (Claude Code)
health-checks the server. Usage: python -I scripts/sandbox_native_mcp.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRAIN = ROOT / "brain.py"
NAME = "omniagent-brain"
REAL_HOME = Path.home()
# Files a careless run could touch. Only stat() is used: credentials are never opened.
WATCHED = [".claude.json", ".claude/settings.json", ".claude/.credentials.json", ".codex/config.toml",
           ".codex/auth.json", ".gemini/settings.json", ".gemini/antigravity/mcp_config.json"]


def fingerprint() -> dict:
    result = {}
    for rel in WATCHED:
        path = REAL_HOME / rel
        result[rel] = (path.stat().st_size, path.stat().st_mtime_ns) if path.exists() else None
    return result


def sandbox_env(home: Path) -> dict:
    # Inherit only the OS/runtime essentials: credentials, provider switches, injected
    # startup code and unknown config redirects must not reach the native clients.
    allowed = {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC",
               "PROCESSOR_ARCHITECTURE", "PROCESSOR_IDENTIFIER", "NUMBER_OF_PROCESSORS", "LANG"}
    env = {k.upper(): v for k, v in os.environ.items() if k.upper() in allowed}
    for sub in ("AppData/Roaming", "AppData/Local", ".claude", ".codex", "tmp", ".config", ".cache", ".data", ".state", ".git-template"):
        (home / sub).mkdir(parents=True, exist_ok=True)
    env.update(USERPROFILE=str(home), HOME=str(home), APPDATA=str(home / "AppData/Roaming"),
               LOCALAPPDATA=str(home / "AppData/Local"), CLAUDE_CONFIG_DIR=str(home / ".claude"),
               CODEX_HOME=str(home / ".codex"), TEMP=str(home / "tmp"), TMP=str(home / "tmp"),
               TMPDIR=str(home / "tmp"), XDG_CONFIG_HOME=str(home / ".config"),
               XDG_CACHE_HOME=str(home / ".cache"), XDG_DATA_HOME=str(home / ".data"),
               XDG_STATE_HOME=str(home / ".state"), GIT_CONFIG_NOSYSTEM="1",
               GIT_CONFIG_GLOBAL=str(home / ".gitconfig"), GIT_TEMPLATE_DIR=str(home / ".git-template"))
    if os.name == "nt":
        env.update(HOMEDRIVE=home.drive, HOMEPATH=str(home)[len(home.drive):])
    return env


def temporary_profile(prefix: str):
    """Keep disposable profiles inside the ignored workspace directory."""
    parent = ROOT / ".local" / "sandbox"
    parent.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(prefix=prefix, dir=parent)


def run(cmd: list[str], env: dict, cwd: Path) -> tuple[int, str]:
    try:
        done = subprocess.run(cmd, env=env, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", stdin=subprocess.DEVNULL, timeout=120)
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    return done.returncode, (done.stdout + done.stderr).strip()


def create_project(project: Path, env: dict) -> None:
    """An independent Git root prevents Claude from selecting the containing workspace."""
    project.mkdir(parents=True)
    code, out = run(["git", "init", "--quiet", str(project)], env, project)
    if code != 0:
        raise RuntimeError(f"cannot initialize disposable Git project: {out}")
    code, root = run(["git", "rev-parse", "--show-toplevel"], env, project)
    if code != 0 or Path(root).resolve() != project.resolve():
        raise RuntimeError("disposable project did not resolve to its own Git root")


def tree(home: Path) -> dict:
    return {p.relative_to(home).as_posix(): p.read_bytes() for p in home.rglob("*") if p.is_file()}


def main() -> int:
    before = fingerprint()
    results = []
    with temporary_profile("omniagent-native-") as temp:
        base = Path(temp)
        home, project, brain_dir = base / "home", base / "project", base / "brain"
        env = sandbox_env(home)
        create_project(project, env)
        server = [sys.executable, "-I", str(BRAIN), "--brain-dir", str(brain_dir), "--project", "sandbox-demo",
                  "serve", "--agent"]
        clients = {"claude": shutil.which("claude"), "codex": shutil.which("codex")}
        for agent, exe in clients.items():
            if not exe:
                results.append({"agent": agent, "check": "cli present", "status": "NOT_VERIFIED",
                                "detail": "CLI not found on PATH"})
                continue
            empty = tree(home)
            version = run([exe, "--version"], env, project)[1].splitlines()[0]
            code, out = run([exe, "mcp", "add", *(["--scope", "user"] if agent == "claude" else []),
                             NAME, "--", *server, agent], env, project)
            results.append({"agent": agent, "version": version, "check": "mcp add (sandbox profile)",
                            "status": "PASS" if code == 0 else "FAIL", "detail": out[-300:]})
            code, out = run([exe, "mcp", "list"], env, project)
            listed = NAME in out
            # Claude Code connects to each server to show its health; Codex only reads its config.
            connected = agent == "claude" and listed and "Connected" in out.split(NAME, 1)[1].splitlines()[0]
            results.append({"agent": agent, "check": "mcp list shows the server", "status": "PASS" if listed else "FAIL",
                            "detail": out[-300:]})
            if agent == "claude":
                results.append({"agent": agent, "check": "native health check (starts server, MCP handshake)",
                                "status": "PASS" if connected else "FAIL", "detail": out[-300:]})
            else:
                results.append({"agent": agent, "check": "native handshake", "status": "NOT_VERIFIED",
                                "detail": "`codex mcp list` does not start servers"})
            code, out = run([exe, "mcp", "remove", *(["--scope", "user"] if agent == "claude" else []), NAME],
                            env, project)
            gone = NAME not in run([exe, "mcp", "list"], env, project)[1]
            results.append({"agent": agent, "check": "mcp remove", "status": "PASS" if code == 0 and gone else "FAIL",
                            "detail": out[-300:]})
            leftover = sorted(set(tree(home)) - set(empty))
            results.append({"agent": agent, "check": "files the CLI left in the sandbox profile", "status": "INFO",
                            "detail": ", ".join(leftover) or "none"})
            for check in ("tool invocation by the agent's model", "cross-agent retrieval"):
                results.append({"agent": agent, "check": check, "status": "NOT_VERIFIED",
                                "detail": "needs a signed-in agent; not done in a throwaway profile"})
    after = fingerprint()
    results.append({"agent": "-", "check": "real profile files unchanged (size, mtime)",
                    "status": "PASS" if before == after else "FAIL",
                    "detail": ", ".join(k for k in WATCHED if before[k] != after[k]) or "all unchanged"})
    results.append({"agent": "antigravity", "check": "any", "status": "NOT_VERIFIED",
                    "detail": "no Antigravity CLI on PATH; IDE registration is a manual step"})
    home_text = str(REAL_HOME)
    sys.stdout.reconfigure(encoding="utf-8")  # CLI output contains symbols a cp1252 console cannot encode
    print(json.dumps(results, indent=2, ensure_ascii=False).replace(home_text, "~")
          .replace(home_text.replace("\\", "\\\\"), "~"))
    return 1 if any(r["status"] == "FAIL" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
