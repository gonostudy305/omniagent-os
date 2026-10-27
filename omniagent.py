"""Conservative, local-first skill deployment for multiple coding agents.

Only explicit targets are writable. The default workflow is read-only planning.
Managed copies are tracked under .local/manager, never in the public repository.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_SOURCE = REPO_ROOT / "ssot" / "skills"
DEFAULT_STATE = REPO_ROOT / ".local" / "manager"
DEFAULT_TARGETS = {
    "claude": Path.home() / ".claude" / "skills",
    "gemini": Path.home() / ".gemini" / "config" / "skills",
    "agents": Path.home() / ".agents" / "skills",
}
EXCLUDED_DIRS = {"node_modules", ".git", "__pycache__", ".local"}
EXCLUDED_FILES = {".ds_store", "thumbs.db"}
# Regenerable local artifacts: ignored when reading an installed copy, never copied or hashed.
RUNTIME_DIRS = {"node_modules", "__pycache__"}
SECRET_FILES = {"auth.json", ".env", ".claude.json"}
ENV_TEMPLATES = {".env.example", ".env.sample", ".env.template"}
SKILL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
TRANSACTION_ID = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{12}$")


class SafetyError(Exception):
    """Refuse to modify files when the operation cannot be proven safe."""


def is_link(path: Path) -> bool:
    """Symlink or Windows junction/reparse point; both can point outside the tree."""
    if path.is_symlink():
        return True
    if os.name == "nt":
        try:
            return bool(os.lstat(path).st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
        except OSError:
            return False
    return False


def within(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def validate_layout(source: Path, state_dir: Path, targets: dict[str, Path]) -> None:
    if not source.is_dir() or is_link(source):
        raise SafetyError(f"Source directory is missing or is a symlink: {source}")
    source_abs, state_abs = source.resolve(), state_dir.resolve()
    for name, root in targets.items():
        if is_link(root):
            raise SafetyError(f"Target root is a symlink: {root}")
        target_abs = root.resolve()
        if (within(source_abs, target_abs) or within(target_abs, source_abs)
                or within(state_abs, target_abs) or within(target_abs, state_abs)):
            raise SafetyError(f"Source, state and target must not overlap: {name}={root}")
        parts = [p.lower() for p in target_abs.parts]
        if ".system" in parts and ".codex" in parts:
            raise SafetyError("Refusing to manage Codex internal .system skills")
    roots = [p.resolve() for p in targets.values()]
    if any(within(a, b) or within(b, a) for i, a in enumerate(roots)
           for b in roots[i + 1:]):
        raise SafetyError("Target directories must not overlap each other")


def parse_targets(values: list[str] | None) -> dict[str, Path]:
    if not values:
        return {}
    targets: dict[str, Path] = {}
    for item in values:
        if "=" not in item:
            raise SafetyError(f"Expected --target NAME=PATH, got {item!r}")
        name, raw_path = item.split("=", 1)
        if not SKILL_NAME.fullmatch(name) or not raw_path or name in targets:
            raise SafetyError(f"Invalid or repeated target: {item!r}")
        targets[name] = Path(raw_path).expanduser().absolute()
    return targets


def skill_files(skill: Path, *, source: bool = True) -> list[tuple[str, Path]]:
    if is_link(skill) or not skill.is_dir() or not (skill / "SKILL.md").is_file():
        raise SafetyError(f"Invalid skill directory: {skill}")
    files: list[tuple[str, Path]] = []
    for root, dirs, names in os.walk(skill, followlinks=False):
        root_path = Path(root)
        for dirname in list(dirs):
            subdir = root_path / dirname
            if dirname.lower() in EXCLUDED_DIRS and source:
                dirs.remove(dirname)
            elif not source and dirname.lower() in RUNTIME_DIRS:
                dirs.remove(dirname)
            elif is_link(subdir):
                raise SafetyError(f"Symlink in skill source is not allowed: {subdir}")
            elif dirname.lower() in EXCLUDED_DIRS:
                raise SafetyError(f"Extra directory in managed copy: {subdir}")
        for name in names:
            path = root_path / name
            if (source and name.lower() in EXCLUDED_FILES) or name.endswith(".pyc"):
                continue
            if (name.lower() in SECRET_FILES or (name.lower().startswith(".env.") and name.lower() not in ENV_TEMPLATES)
                    or path.suffix.lower() in {".pem", ".key", ".token"}):
                raise SafetyError(f"Potential credentials in source; inspect manually: {path}")
            if is_link(path) or not stat.S_ISREG(path.stat().st_mode):
                raise SafetyError(f"Non-regular file in skill: {path}")
            files.append((path.relative_to(skill).as_posix(), path))
    return sorted(files, key=lambda item: item[0])


def skill_directories(skill: Path, *, source: bool = True) -> list[str]:
    """Account for empty directories so rollback cannot erase unseen user data."""
    result = []
    for root, dirs, _ in os.walk(skill, followlinks=False):
        root_path = Path(root)
        for name in list(dirs):
            path = root_path / name
            if name.lower() in EXCLUDED_DIRS and source:
                dirs.remove(name)
            elif not source and name.lower() in RUNTIME_DIRS:
                dirs.remove(name)
            elif is_link(path) or name.lower() in EXCLUDED_DIRS:
                raise SafetyError(f"Unexpected directory or symlink: {path}")
            else:
                result.append(path.relative_to(skill).as_posix())
    return sorted(result)


def tree_digest(skill: Path, *, source: bool = True) -> str:
    digest = hashlib.sha256()
    for directory in skill_directories(skill, source=source):
        digest.update(b"DIR\0")
        digest.update(directory.encode("utf-8"))
        digest.update(b"\0")
    for rel, path in skill_files(skill, source=source):
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as handle:
            while chunk := handle.read(128 * 1024):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def has_vendor(skill: Path) -> bool:
    """True if an installed copy holds node_modules, which update/rollback would delete."""
    return any(d.lower() == "node_modules" for _, dirs, _ in os.walk(skill, followlinks=False) for d in dirs)


def sources_at(source: Path) -> dict[str, tuple[Path, str]]:
    result = {}
    names_lower = set()
    for path in sorted(source.iterdir()):
        if is_link(path):
            raise SafetyError(f"Symlink at source root is not allowed: {path}")
        if not path.is_dir() or not (path / "SKILL.md").exists():
            continue
        if not SKILL_NAME.fullmatch(path.name) or path.name.lower() in names_lower:
            raise SafetyError(f"Invalid or case-colliding skill name: {path.name}")
        names_lower.add(path.name.lower())
        result[path.name] = (path, tree_digest(path))
    return result


def read_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SafetyError(f"Malformed JSON state: {path}")
    return data


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=".omniagent-", delete=False) as handle:
        temp_path = Path(handle.name)
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())  # a power cut must not leave an empty journal behind
    os.replace(temp_path, path)


def process_alive(pid: int) -> bool | None:
    """True/False if the OS can tell; None means unknown (never auto-unlock)."""
    if pid <= 0:
        return None
    if pid == os.getpid():
        return True
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE, not terminate access
        if not handle:
            return False if ctypes.get_last_error() == 87 else None  # ERROR_INVALID_PARAMETER
        try:
            result = kernel.WaitForSingleObject(handle, 0)
            return False if result == 0 else True if result == 0x102 else None
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True


@contextmanager
def recovery_mutex(state_dir: Path):
    """OS-released lock serializes stale-lock recovery across new CLI processes."""
    with (state_dir / "manager.mutex").open("a+b") as handle:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise SafetyError("Another Skill Manager process holds the OS mutex") from exc
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise SafetyError("Another Skill Manager process holds the OS mutex") from exc
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def manager_lock(state_dir: Path):
    """Lock writers; reclaim an orphaned PID lock only if definitely dead."""
    state_dir.mkdir(parents=True, exist_ok=True)
    lockfile = state_dir / "manager.lock"
    with recovery_mutex(state_dir):
        if lockfile.exists():
            raw_pid = lockfile.read_text(encoding="utf-8", errors="replace").strip()
            alive = process_alive(int(raw_pid)) if raw_pid.isdecimal() else None
            if alive is not False:
                raise SafetyError(f"Manager lock may still be active: {lockfile}. "
                                  "Verify the other process has exited before manual recovery.")
            # OS mutex prevents two new managers from reclaiming the same stale file.
            # Older managers still use O_EXCL, so they cannot race past our new lock.
            lockfile.unlink()
        try:
            fd = os.open(lockfile, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise SafetyError(f"Manager lock acquired by another process: {lockfile}") from exc
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(str(os.getpid()))
            yield
        finally:
            lockfile.unlink(missing_ok=True)


def state_at(state_dir: Path) -> dict:
    state = read_json(state_dir / "state.json", {"version": 1, "managed": {}})
    if state.get("version") != 1 or not isinstance(state.get("managed"), dict):
        raise SafetyError("Unsupported or corrupt manager state")
    return state


def plan(source: Path, state_dir: Path, targets: dict[str, Path],
         selected: set[str] | None = None) -> list[dict]:
    validate_layout(source, state_dir, targets)
    skills = sources_at(source)
    if selected is not None and selected - skills.keys():
        raise SafetyError(f"Unknown skill(s): {', '.join(sorted(selected - skills.keys()))}")
    manifest = state_at(state_dir)["managed"]
    actions = []
    for target_name, root in targets.items():
        for name, (skill_dir, source_hash) in skills.items():
            if selected is not None and name not in selected:
                continue
            dest = root / name
            key = str(dest)
            record = manifest.get(key)
            if is_link(dest) or (dest.exists() and not dest.is_dir()):
                status = "blocked"
            elif dest.exists():
                if not record or record.get("source") != str(skill_dir.resolve()):
                    status = "blocked_unmanaged"
                else:
                    try:
                        current = tree_digest(dest, source=False)
                    except SafetyError:
                        status = "conflict"
                    else:
                        if current != record.get("hash"):
                            status = "conflict"
                        elif "rendered_for" in record:
                            # A plain v0.2 apply must never overwrite a skill
                            # intentionally rendered for a specific agent.
                            # Only an explicit adapter-aware plan can opt back
                            # into managing these per-target bytes.
                            status = "blocked_rendered"
                        elif current == source_hash:
                            status = "unchanged"
                        else:
                            status = "update_adopted" if record.get("origin") == "adopted" else "update"
                            if has_vendor(dest):
                                status = "blocked_vendor"
            elif record:
                status = "conflict_missing"
            else:
                status = "install"
            actions.append({"target": target_name, "name": name, "status": status,
                            "source": str(skill_dir.resolve()), "source_hash": source_hash,
                            "destination": str(dest)})
    return actions


def adoption_plan(source: Path, state_dir: Path, targets: dict[str, Path],
                  selected: set[str] | None = None, adapter_manifest: dict | None = None,
                  adapter_module=None) -> list[dict]:
    """Inspect existing unmanaged folders, without modifying any skill or state.

    With an adapter manifest, a folder that equals the per-agent *rendered* skill
    is reported as ``eligible_rendered``; it is recorded with its rendered hash.
    """
    if adapter_manifest is not None:
        if adapter_module is None:
            raise SafetyError("Adapter-aware adoption requires a validated adapter module")
        for agent in targets:
            if agent not in adapter_manifest["targets"]:
                raise SafetyError(f"No adapter variables configured for target: {agent}")
    result = []
    for item in plan(source, state_dir, targets, selected):
        status = item["status"]
        if status == "blocked_unmanaged":
            try:
                digest = tree_digest(Path(item["destination"]), source=False)
            except (SafetyError, OSError):
                status = "unsafe"
            else:
                config = adapter_manifest["skills"].get(item["name"]) if adapter_manifest else None
                render_hash = None
                if config:
                    render_hash, _ = adapter_module.rendered_skill(
                        Path(item["source"]), config["files"],
                        adapter_manifest["targets"][item["target"]],
                        config.get("ignore_placeholders"))
                if render_hash is not None and digest == render_hash and render_hash != item["source_hash"]:
                    status = "eligible_rendered"
                    item = {**item, "render_hash": render_hash}
                else:
                    status = "eligible" if digest == item["source_hash"] else "mismatch"
        elif status in {"update", "update_adopted", "unchanged", "blocked_rendered"}:
            status = "already_managed"
        result.append({**item, "status": status})
    return result


def show_adoption_diff(item: dict) -> None:
    """Display only an explicitly requested local SKILL.md diff; never persist it."""
    if item["status"] != "mismatch":
        return
    source = Path(item["source"]) / "SKILL.md"
    target = Path(item["destination"]) / "SKILL.md"
    if (not source.is_file() or is_link(source) or not target.is_file()
            or is_link(target) or source.stat().st_size > 1_000_000
            or target.stat().st_size > 1_000_000):
        print("SKILL.md missing, linked, or too large; inspect manually")
        return
    before = source.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    after = target.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    lines = list(difflib.unified_diff(before, after, fromfile="SSOT/SKILL.md",
                                      tofile="target/SKILL.md", n=3))
    if lines:
        print("CAUTION: diff may include private local content; terminal display only.")
        for line in lines[:150]:
            print(line, end="" if line.endswith("\n") else "\n")
        if len(lines) > 150:
            print(f"... {len(lines) - 150} more diff lines omitted")
    else:
        print("SKILL.md matches; the difference is in assets, files, or directories")


def adopt(source: Path, state_dir: Path, targets: dict[str, Path],
          selected: set[str] | None, adapter_manifest: dict | None = None,
          adapter_module=None) -> tuple[str | None, list[dict]]:
    """Record exactly matching existing skills as adopted; never touch target files."""
    with manager_lock(state_dir):
        ensure_no_interrupted(state_dir)
        actions = adoption_plan(source, state_dir, targets, selected,
                                adapter_manifest, adapter_module)
        eligible = [a for a in actions if a["status"] in {"eligible", "eligible_rendered"}]
        if not eligible:
            return None, actions
        # A second verification under the exclusive manager lock guards against
        # target edits made while discovering candidates.
        for item in eligible:
            dest = Path(item["destination"])
            expected = item["render_hash"] if item["status"] == "eligible_rendered" else item["source_hash"]
            if is_link(dest) or not dest.is_dir() or tree_digest(dest, source=False) != expected:
                raise SafetyError(f"Skill changed during adoption; no state written: {dest}")
        state = state_at(state_dir)
        if any(item["destination"] in state["managed"] for item in eligible):
            raise SafetyError("Skill became managed during adoption; no state written")
        transaction = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:12]
        journal_path = state_dir / "transactions" / f"{transaction}.json"
        journal_actions = [{**a, "applied_hash": a["render_hash"]} if a["status"] == "eligible_rendered" else a
                           for a in eligible]
        journal = {"version": 1, "kind": "adopt", "status": "applied",
                   "transaction": transaction, "actions": journal_actions}
        save_json(journal_path, journal)
        try:
            for item in eligible:
                record = {"source": item["source"], "hash": item["source_hash"],
                          "origin": "adopted", "adopted_transaction": transaction}
                if item["status"] == "eligible_rendered":
                    record.update({"hash": item["render_hash"], "source_hash": item["source_hash"],
                                   "rendered_for": item["target"]})
                state["managed"][item["destination"]] = record
            save_json(state_dir / "state.json", state)
        except Exception:
            journal["status"] = "failed"
            save_json(journal_path, journal)
            raise
        return transaction, actions


def interrupted_transactions(state_dir: Path) -> list[tuple[Path, dict]]:
    """Journals left in_progress by a crashed or killed apply."""
    found = []
    directory = state_dir / "transactions"
    if directory.is_dir():
        for path in sorted(directory.glob("*.json")):
            try:
                journal = json.loads(path.read_text(encoding="utf-8"))
            except ValueError as exc:
                raise SafetyError(f"Unreadable transaction journal: {path}") from exc
            if isinstance(journal, dict) and journal.get("status") in {"in_progress", "committing"}:
                found.append((path, journal))
    return found


def ensure_no_interrupted(state_dir: Path) -> None:
    if interrupted_transactions(state_dir):
        raise SafetyError("An interrupted transaction exists; inspect it with 'recover' first")


def recover(state_dir: Path, apply_changes: bool) -> list[dict]:
    """Return interrupted applies to their pre-transaction state, or preview doing so.

    Fails closed: if any skill is neither the new content nor the recorded
    previous content (for example edited by hand), nothing is changed.
    """
    with manager_lock(state_dir):
        steps: list[dict] = []
        for journal_path, journal in interrupted_transactions(state_dir):
            if journal["status"] == "committing":
                # Files were all replaced and verified; only the bookkeeping may lag.
                for a in journal["actions"]:
                    dest = Path(a["destination"])
                    if is_link(dest) or not dest.is_dir():
                        raise SafetyError(f"Cannot finish commit; destination missing: {dest}")
                    try:
                        digest = tree_digest(dest, source=False)
                    except SafetyError as exc:
                        raise SafetyError(f"Cannot finish commit; destination was changed: {dest}") from exc
                    if digest != a["applied_hash"]:
                        raise SafetyError(f"Cannot finish commit; destination was changed: {dest}")
                    steps.append({"transaction": journal["transaction"], "destination": str(dest),
                                  "action": "commit", "stage_leftovers": []})
                if apply_changes:
                    state = state_at(state_dir)
                    state["managed"].update(journal["records"])
                    save_json(state_dir / "state.json", state)
                    journal["status"] = "applied"
                    save_json(journal_path, journal)
                continue
            planned = []
            for a in journal.get("actions", []):
                dest = Path(a["destination"])
                previous = a.get("previous")
                backup = Path(a["backup"]) if a.get("backup") else None
                if is_link(dest) or (dest.exists() and not dest.is_dir()):
                    raise SafetyError(f"Cannot recover; unexpected destination: {dest}")
                dest_is_previous = False
                if dest.is_dir() and previous and not has_vendor(dest):
                    try:
                        dest_is_previous = tree_digest(dest, source=False) == previous["hash"]
                    except SafetyError:
                        pass
                if backup is not None and backup.exists() and not dest_is_previous:
                    if tree_digest(backup, source=False) != previous["hash"]:
                        raise SafetyError(f"Cannot recover; backup changed: {backup}")
                if dest.is_dir():
                    if has_vendor(dest):
                        raise SafetyError(f"Cannot recover; vendor dependencies present: {dest}")
                    try:
                        digest = tree_digest(dest, source=False)
                    except SafetyError as exc:
                        raise SafetyError(f"Cannot recover; destination was changed: {dest}") from exc
                    if digest == a["applied_hash"]:
                        if backup is not None and not backup.is_dir():
                            raise SafetyError(f"Cannot recover; backup missing: {backup}")
                        action = "revert"
                    elif previous and digest == previous["hash"]:
                        action = "untouched"
                    else:
                        raise SafetyError(f"Cannot recover; destination was changed: {dest}")
                elif backup is not None:
                    if not backup.is_dir():
                        raise SafetyError(f"Cannot recover; backup missing: {backup}")
                    action = "restore"
                else:
                    action = "untouched"
                planned.append((a, dest, backup, action))
            for a, dest, backup, action in planned:
                steps.append({"transaction": journal["transaction"], "destination": str(dest),
                              "action": action,
                              "stage_leftovers": sorted(str(p) for p in dest.parent.glob(".omniagent-stage-*"))
                              if dest.parent.is_dir() else []})
            if not apply_changes:
                continue
            for a, dest, backup, action in planned:
                if action == "revert":
                    remove_managed(dest)
                if action in {"revert", "restore"} and backup is not None:
                    shutil.copytree(backup, dest)
                    if tree_digest(dest, source=False) != a["previous"]["hash"]:
                        raise SafetyError(f"Restore verification failed: {dest}")
            journal["status"] = "recovered_reverted"
            save_json(journal_path, journal)
        return steps


def copy_skill(source: Path, destination: Path, expected_hash: str) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    for relative in skill_directories(source):
        (destination / relative).mkdir(parents=True, exist_ok=True)
    for relative, file in skill_files(source):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file, target)
    if tree_digest(destination, source=False) != expected_hash:
        raise SafetyError(f"Source changed while copying: {source}")


def copy_rendered_skill(source: Path, destination: Path, source_hash: str,
                        rendered_hash: str, name: str, target: str,
                        adapter_manifest: dict, adapter_module) -> None:
    """Stage a complete, verified per-target rendered tree; never edit SSOT."""
    copy_skill(source, destination, source_hash)
    config = adapter_manifest["skills"].get(name)
    if config:
        expected_hash, rendered = adapter_module.rendered_skill(
            source, config["files"], adapter_manifest["targets"][target],
            config.get("ignore_placeholders"),
        )
        if expected_hash != rendered_hash:
            raise SafetyError(f"Template or manifest changed while staging: {name}")
        for relative, body in rendered.items():
            (destination / relative).write_bytes(body)
    if tree_digest(destination, source=False) != rendered_hash:
        raise SafetyError(f"Rendered staging hash mismatch: {name} ({target})")


def remove_managed(path: Path) -> None:
    if is_link(path) or not path.is_dir():
        raise SafetyError(f"Refusing to remove unexpected destination: {path}")
    shutil.rmtree(path)


def apply(source: Path, state_dir: Path, targets: dict[str, Path],
          selected: set[str] | None, skip_blocked: bool,
          allow_update_adopted: bool = False, adapter_manifest: dict | None = None,
          adapter_module=None) -> tuple[str | None, list[dict]]:
    if adapter_manifest is not None and (selected is None or len(selected) != 1 or len(targets) != 1):
        raise SafetyError("Experimental rendered apply permits exactly one skill and target")
    with manager_lock(state_dir):
        return _apply_locked(source, state_dir, targets, selected, skip_blocked,
                             allow_update_adopted, adapter_manifest, adapter_module)


def _apply_locked(source: Path, state_dir: Path, targets: dict[str, Path],
                  selected: set[str] | None, skip_blocked: bool,
                  allow_update_adopted: bool, adapter_manifest: dict | None = None,
                  adapter_module=None) -> tuple[str | None, list[dict]]:
    ensure_no_interrupted(state_dir)
    if adapter_manifest is not None:
        if adapter_module is None:
            raise SafetyError("Rendered apply requires a validated adapter module")
        actions = adapter_module.render_aware_plan(source, state_dir, targets,
                                                   adapter_manifest, selected)
    else:
        actions = plan(source, state_dir, targets, selected)
    blocked = [a for a in actions if a["status"].startswith(("blocked", "conflict"))
               or (a["status"] == "update_adopted" and not allow_update_adopted)]
    if blocked and not skip_blocked:
        if adapter_manifest is None and any(a["status"] == "blocked_rendered" for a in blocked):
            raise SafetyError("Rendered skills require --adapter-manifest for apply; "
                              "nothing applied. Use --skip-blocked to leave them untouched.")
        raise SafetyError(f"{len(blocked)} existing skills are unmanaged or changed; "
                          "nothing applied. Use --skip-blocked to install only safe entries.")
    changed = [a for a in actions if a["status"] in {"install", "update"}
               or (a["status"] == "update_adopted" and allow_update_adopted)]
    if not changed:
        return None, actions

    state = state_at(state_dir)
    old_records = {a["destination"]: state["managed"].get(a["destination"])
                   for a in changed}
    transaction = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:12]
    backup_dir = state_dir / "backups" / transaction
    backup_dir.mkdir(parents=True, exist_ok=False)
    # Intent journal first: a crash mid-apply leaves status "in_progress" for `recover`.
    planned = [{"destination": a["destination"], "name": a["name"], "target": a["target"],
                "status": a["status"], "applied_hash": a.get("render_hash", a["source_hash"]),
                "backup": str(backup_dir / str(index)) if a["status"] in {"update", "update_adopted"} else None,
                "previous": old_records[a["destination"]]} for index, a in enumerate(changed)]
    save_json(state_dir / "transactions" / f"{transaction}.json",
              {"version": 1, "status": "in_progress", "transaction": transaction, "actions": planned})
    completed = []
    try:
        for index, action in enumerate(changed):
            dest = Path(action["destination"])
            source_path = Path(action["source"])
            dest.parent.mkdir(parents=True, exist_ok=True)
            # Recheck immediately before mutating, including changes since plan.
            if action["status"] == "install" and (dest.exists() or is_link(dest)):
                raise SafetyError(f"Destination appeared since planning: {dest}")
            if action["status"] in {"update", "update_adopted"}:
                if adapter_manifest is None and "rendered_for" in old_records[str(dest)]:
                    raise SafetyError(f"Rendered skill requires --adapter-manifest: {dest}")
                prior_hash = old_records[str(dest)]["hash"]
                if not dest.is_dir() or is_link(dest) or tree_digest(dest, source=False) != prior_hash:
                    raise SafetyError(f"Managed skill changed since planning: {dest}")
                # Runtime files are ignored by hashes. Recheck immediately
                # before replacement so late-added vendor data is never lost.
                if has_vendor(dest):
                    raise SafetyError(f"Installed vendor dependencies block update: {dest}")
            with tempfile.TemporaryDirectory(prefix=".omniagent-stage-", dir=dest.parent) as temp:
                staging = Path(temp) / "skill"
                applied_hash = action.get("render_hash", action["source_hash"])
                if adapter_manifest is not None:
                    copy_rendered_skill(source_path, staging, action["source_hash"],
                                        applied_hash, action["name"], action["target"],
                                        adapter_manifest, adapter_module)
                else:
                    copy_skill(source_path, staging, action["source_hash"])
                backup = backup_dir / str(index)
                if action["status"] in {"update", "update_adopted"}:
                    shutil.copytree(dest, backup)
                    if tree_digest(backup, source=False) != old_records[str(dest)]["hash"]:
                        raise SafetyError(f"Backup verification failed: {dest}")
                    moved = Path(temp) / "previous"
                    dest.rename(moved)
                    try:
                        staging.rename(dest)
                    except Exception:
                        moved.rename(dest)
                        raise
                else:
                    staging.rename(dest)
                # Recorded before the temp dir is cleaned up, so a cleanup failure is still reverted.
                completed.append({**action, "applied_hash": applied_hash,
                                  "backup": str(backup) if action["status"] in {"update", "update_adopted"} else None,
                                  "previous": old_records[str(dest)]})

        # Two-phase commit: journal "committing" carries the exact state records, so a
        # crash before or during the state write can be rolled forward by `recover`.
        records = {}
        for a in completed:
            new_record = {"source": a["source"], "hash": a["applied_hash"]}
            if adapter_manifest is not None:
                new_record["source_hash"] = a["source_hash"]
                new_record["rendered_for"] = a["target"]
            if a["previous"] and a["previous"].get("origin") == "adopted":
                new_record["origin"] = "adopted"
                new_record["adopted_transaction"] = a["previous"]["adopted_transaction"]
            records[a["destination"]] = new_record
        journal = {"version": 1, "status": "committing", "transaction": transaction,
                   "actions": completed, "records": records}
        save_json(state_dir / "transactions" / f"{transaction}.json", journal)
        state["managed"].update(records)
        save_json(state_dir / "state.json", state)
    except Exception:
        for a in reversed(completed):
            dest = Path(a["destination"])
            if (dest.is_dir() and not is_link(dest)
                    and tree_digest(dest, source=False) == a.get("applied_hash", a["source_hash"])):
                remove_managed(dest)
                if a["backup"]:
                    shutil.copytree(a["backup"], dest)
        journal_path = state_dir / "transactions" / f"{transaction}.json"
        if journal_path.exists():
            saved = read_json(journal_path, {})
            saved["status"] = "failed_reverted"
            save_json(journal_path, saved)
        raise
    # State is published; a failure here must not revert files. `recover` finishes it.
    journal["status"] = "applied"
    save_json(state_dir / "transactions" / f"{transaction}.json", journal)
    return transaction, actions


def rollback(state_dir: Path, transaction: str) -> int:
    with manager_lock(state_dir):
        return _rollback_locked(state_dir, transaction)


def _rollback_locked(state_dir: Path, transaction: str) -> int:
    if not TRANSACTION_ID.fullmatch(transaction):
        raise SafetyError("Invalid transaction ID")
    ensure_no_interrupted(state_dir)
    journal_path = state_dir / "transactions" / f"{transaction}.json"
    journal = read_json(journal_path, {})
    if journal.get("status") != "applied" or journal.get("transaction") != transaction:
        raise SafetyError("Transaction not found or already rolled back")
    state = state_at(state_dir)
    actions = journal["actions"]
    if journal.get("kind") == "adopt":
        for item in actions:
            record = state["managed"].get(item["destination"], {})
            if (record.get("origin") != "adopted"
                    or record.get("adopted_transaction") != transaction
                    or record.get("hash") != item.get("applied_hash", item["source_hash"])):
                raise SafetyError(f"Cannot untrack a superseded adoption: {item['destination']}")
        # Undoing adoption only removes manager bookkeeping, never a user's folder.
        for item in actions:
            state["managed"].pop(item["destination"], None)
        save_json(state_dir / "state.json", state)
        journal["status"] = "rolled_back"
        save_json(journal_path, journal)
        return len(actions)
    # Validate the whole transaction first; never delete a user-edited skill.
    for a in actions:
        dest = Path(a["destination"])
        applied_hash = a.get("applied_hash", a["source_hash"])
        if (is_link(dest) or not dest.is_dir() or has_vendor(dest)
                or tree_digest(dest, source=False) != applied_hash
                or state["managed"].get(str(dest), {}).get("hash") != applied_hash
                or state["managed"].get(str(dest), {}).get("source") != a["source"]
                or (not a["previous"] and state["managed"].get(str(dest), {}).get("origin") == "adopted")):
            raise SafetyError(f"Cannot roll back; installed skill changed: {dest}")
        if a["backup"] and (not Path(a["backup"]).is_dir()
                            or tree_digest(Path(a["backup"]), source=False) != a["previous"]["hash"]):
            raise SafetyError(f"Cannot roll back; backup missing or changed: {a['backup']}")
    for a in reversed(actions):
        dest = Path(a["destination"])
        remove_managed(dest)
        if a["backup"]:
            shutil.copytree(a["backup"], dest)
        if a["previous"]:
            state["managed"][str(dest)] = a["previous"]
        else:
            state["managed"].pop(str(dest), None)
    save_json(state_dir / "state.json", state)
    journal["status"] = "rolled_back"
    save_json(journal_path, journal)
    return len(actions)


def discover(source: Path, targets: dict[str, Path]) -> None:
    """Read-only inventory; works on a fresh clone with no SSOT yet."""
    if source.is_dir() and not is_link(source):
        skills = sources_at(source)
        print(f"source     {len(skills):4} skills  {source}")
        for folder in sorted(source.iterdir()):
            if folder.is_dir() and not is_link(folder) and not (folder / "SKILL.md").is_file():
                print(f"WARNING: source folder ignored (missing SKILL.md): {folder.name}")
    else:
        print(f"source        (not initialized)  {source}")
    for name, root in targets.items():
        if root.is_dir():
            skills = sum(1 for child in root.iterdir()
                         if child.is_dir() and (child / "SKILL.md").is_file())
            print(f"{name:10} {skills:4} skills  {root}")
        else:
            print(f"{name:10}      (not found)  {root}")


def load_adapter_module():
    """Load the adjacent adapter safely even when Python runs with -I."""
    sys.modules.setdefault("omniagent", sys.modules[__name__])
    adapter_file = REPO_ROOT / "path_adapter.py"
    if not adapter_file.is_file():
        raise SafetyError("Path Adapter module not installed next to omniagent.py")
    spec = importlib.util.spec_from_file_location("_omniagent_adapter", adapter_file)
    if spec is None or spec.loader is None:
        raise SafetyError("Unable to load Path Adapter module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Safe local skill manager (copy-based, no symlinks)")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("discover", "plan", "apply", "adopt"):
        part = sub.add_parser(command)
        part.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
        part.add_argument("--state-dir", type=Path, default=DEFAULT_STATE)
        part.add_argument("--target", action="append", metavar="NAME=PATH")
        part.add_argument("--skill", action="append", help="only manage named skill (repeatable)")
        if command == "plan":
            part.add_argument("--adapter-manifest", type=Path,
                              help="read-only preview of per-agent rendered hashes (v0.3a)")
            part.add_argument("--json", action="store_true",
                              help="print all hash fields as JSON in the local terminal")
        if command == "apply":
            part.add_argument("--yes", action="store_true", help="explicitly approve filesystem writes")
            part.add_argument("--skip-blocked", action="store_true")
            part.add_argument("--allow-update-adopted", action="store_true",
                              help="explicitly permit replacing a previously adopted exact-match skill")
            part.add_argument("--adapter-manifest", type=Path,
                              help="per-agent rendered apply: experimental, one skill/target only")
            part.add_argument("--allow-rendered-apply", action="store_true",
                              help="separate opt-in to modifying a target with rendered skill files")
        if command == "adopt":
            part.add_argument("--adapter-manifest", type=Path,
                              help="also recognise folders equal to the per-agent rendered skill")
            part.add_argument("--yes", action="store_true", help="record exact matches in private state")
            part.add_argument("--diff", action="store_true", help="show local SKILL.md diff for one selected mismatch")
    fix = sub.add_parser("recover", help="revert an apply that was interrupted (preview unless --yes)")
    fix.add_argument("--state-dir", type=Path, default=DEFAULT_STATE)
    fix.add_argument("--yes", action="store_true", help="perform the reversion")
    undo = sub.add_parser("rollback")
    undo.add_argument("--state-dir", type=Path, default=DEFAULT_STATE)
    undo.add_argument("--transaction", required=True)
    undo.add_argument("--yes", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "recover":
            steps = recover(args.state_dir.expanduser().absolute(), args.yes)
            for s in steps:
                print(f"{s['transaction']}  {s['action']:10} {s['destination']}")
                for leftover in s["stage_leftovers"]:
                    print(f"  leftover staging folder (inspect, then delete manually): {leftover}")
            print(f"{len(steps)} step(s) " + ("reverted" if args.yes else "previewed; rerun with --yes to apply"))
            return 0
        if args.command == "rollback":
            if not args.yes:
                raise SafetyError("Rollback modifies skill folders; rerun with --yes")
            count = rollback(args.state_dir.expanduser().absolute(), args.transaction)
            print(f"Rolled back {count} managed skill copies")
            return 0
        source = args.source.expanduser().absolute()
        state_dir = args.state_dir.expanduser().absolute()
        targets = parse_targets(args.target)
        if args.command == "discover" and not targets:
            targets = DEFAULT_TARGETS
        if args.command == "discover":
            discover(source, targets)
            return 0
        if args.command != "discover" and not targets:
            raise SafetyError("Specify at least one explicit --target NAME=PATH")
        selected = set(args.skill) if args.skill else None
        if args.command == "adopt":
            if args.diff and (not selected or len(selected) != 1 or len(targets) != 1):
                raise SafetyError("For private diffs, select exactly one --skill and one --target")
            adopt_module = adopt_manifest = None
            if args.adapter_manifest:
                adopt_module = load_adapter_module()
                adopt_manifest = adopt_module.load_manifest(args.adapter_manifest.expanduser().absolute())
            if args.yes:
                transaction, actions = adopt(source, state_dir, targets, selected,
                                             adopt_manifest, adopt_module)
                print(f"Adoption transaction: {transaction or '(nothing changed)'}")
            else:
                actions = adoption_plan(source, state_dir, targets, selected,
                                        adopt_manifest, adopt_module)
            for a in actions:
                print(f"{a['target']:10} {a['status']:18} {a['name']}")
                if args.diff:
                    show_adoption_diff(a)
            print(f"Eligible exact matches: {sum(a['status'] in {'eligible', 'eligible_rendered'} for a in actions)}")
            return 0
        adapter_module = None
        adapter_manifest = None
        if args.adapter_manifest:
            adapter_module = load_adapter_module()
            adapter_manifest = adapter_module.load_manifest(args.adapter_manifest.expanduser().absolute())
        if args.command == "plan" and adapter_manifest is not None:
            actions = adapter_module.render_aware_plan(source, state_dir, targets,
                                                       adapter_manifest, selected)
        else:
            actions = plan(source, state_dir, targets, selected)
        if args.command == "apply":
            if not args.yes:
                raise SafetyError("Apply modifies skill folders; inspect plan first, then use --yes")
            if adapter_manifest is not None:
                if not args.allow_rendered_apply:
                    raise SafetyError("Rendered apply needs separate --allow-rendered-apply consent")
                if selected is None or len(selected) != 1 or len(targets) != 1:
                    raise SafetyError("Rendered apply is limited to one --skill and one --target")
            elif args.allow_rendered_apply:
                raise SafetyError("--allow-rendered-apply requires --adapter-manifest")
            transaction, actions = apply(source, state_dir, targets, selected,
                                         args.skip_blocked, args.allow_update_adopted,
                                         adapter_manifest, adapter_module)
            print(f"Transaction: {transaction or '(nothing changed)'}")
        if args.command == "plan" and args.json:
            print(json.dumps(actions, indent=2, ensure_ascii=True))
        else:
            for a in actions:
                print(f"{a['target']:10} {a['status']:18} {a['name']}")
                if args.command == "plan" and args.adapter_manifest:
                    installed = a["installed_hash"] or "(missing/unsafe)"
                    print(f"  source={a['source_hash'][:12]} render={a['render_hash'][:12]} "
                          f"installed={installed[:12]}")
        return 0
    except (SafetyError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(cli())
