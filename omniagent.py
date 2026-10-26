"""Conservative, local-first skill deployment for multiple coding agents.

Only explicit targets are writable. The default workflow is read-only planning.
Managed copies are tracked under .local/manager, never in the public repository.
"""

from __future__ import annotations

import argparse
import hashlib
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
SECRET_FILES = {"auth.json", ".env", ".claude.json"}
SKILL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
TRANSACTION_ID = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{12}$")


class SafetyError(Exception):
    """Refuse to modify files when the operation cannot be proven safe."""


def within(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def validate_layout(source: Path, state_dir: Path, targets: dict[str, Path]) -> None:
    if not source.is_dir() or source.is_symlink():
        raise SafetyError(f"Source directory is missing or is a symlink: {source}")
    source_abs, state_abs = source.resolve(), state_dir.resolve()
    for name, root in targets.items():
        if root.is_symlink():
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
    if skill.is_symlink() or not skill.is_dir() or not (skill / "SKILL.md").is_file():
        raise SafetyError(f"Invalid skill directory: {skill}")
    files: list[tuple[str, Path]] = []
    for root, dirs, names in os.walk(skill, followlinks=False):
        root_path = Path(root)
        for dirname in list(dirs):
            subdir = root_path / dirname
            if dirname in EXCLUDED_DIRS and source:
                dirs.remove(dirname)
            elif subdir.is_symlink():
                raise SafetyError(f"Symlink in skill source is not allowed: {subdir}")
            elif dirname in EXCLUDED_DIRS:
                raise SafetyError(f"Extra directory in managed copy: {subdir}")
        for name in names:
            path = root_path / name
            if source and (name.lower() in EXCLUDED_FILES or name.endswith(".pyc")):
                continue
            if (name.lower() in SECRET_FILES or name.lower().startswith(".env.")
                    or path.suffix.lower() in {".pem", ".key", ".token"}):
                raise SafetyError(f"Potential credentials in source; inspect manually: {path}")
            if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
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
            if name in EXCLUDED_DIRS and source:
                dirs.remove(name)
            elif path.is_symlink() or name in EXCLUDED_DIRS:
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


def sources_at(source: Path) -> dict[str, tuple[Path, str]]:
    result = {}
    names_lower = set()
    for path in sorted(source.iterdir()):
        if path.is_symlink():
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
    os.replace(temp_path, path)


@contextmanager
def manager_lock(state_dir: Path):
    """Avoid concurrent writers sharing the same private manager state."""
    state_dir.mkdir(parents=True, exist_ok=True)
    lockfile = state_dir / "manager.lock"
    try:
        fd = os.open(lockfile, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise SafetyError(f"Manager is locked: {lockfile}. Confirm no process is running before removing a stale lock.") from exc
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
            if dest.is_symlink() or (dest.exists() and not dest.is_dir()):
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
                        elif current == source_hash:
                            status = "unchanged"
                        else:
                            status = "update"
            elif record:
                status = "conflict_missing"
            else:
                status = "install"
            actions.append({"target": target_name, "name": name, "status": status,
                            "source": str(skill_dir.resolve()), "source_hash": source_hash,
                            "destination": str(dest)})
    return actions


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


def remove_managed(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        raise SafetyError(f"Refusing to remove unexpected destination: {path}")
    shutil.rmtree(path)


def apply(source: Path, state_dir: Path, targets: dict[str, Path],
          selected: set[str] | None, skip_blocked: bool) -> tuple[str | None, list[dict]]:
    with manager_lock(state_dir):
        return _apply_locked(source, state_dir, targets, selected, skip_blocked)


def _apply_locked(source: Path, state_dir: Path, targets: dict[str, Path],
                  selected: set[str] | None, skip_blocked: bool) -> tuple[str | None, list[dict]]:
    actions = plan(source, state_dir, targets, selected)
    blocked = [a for a in actions if a["status"].startswith(("blocked", "conflict"))]
    if blocked and not skip_blocked:
        raise SafetyError(f"{len(blocked)} existing skills are unmanaged or changed; "
                          "nothing applied. Use --skip-blocked to install only safe entries.")
    changed = [a for a in actions if a["status"] in {"install", "update"}]
    if not changed:
        return None, actions

    state = state_at(state_dir)
    old_records = {a["destination"]: state["managed"].get(a["destination"])
                   for a in changed}
    transaction = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:12]
    backup_dir = state_dir / "backups" / transaction
    backup_dir.mkdir(parents=True, exist_ok=False)
    completed = []
    try:
        for index, action in enumerate(changed):
            dest = Path(action["destination"])
            source_path = Path(action["source"])
            dest.parent.mkdir(parents=True, exist_ok=True)
            # Recheck immediately before mutating, including changes since plan.
            if action["status"] == "install" and (dest.exists() or dest.is_symlink()):
                raise SafetyError(f"Destination appeared since planning: {dest}")
            if action["status"] == "update":
                prior_hash = old_records[str(dest)]["hash"]
                if not dest.is_dir() or dest.is_symlink() or tree_digest(dest, source=False) != prior_hash:
                    raise SafetyError(f"Managed skill changed since planning: {dest}")
            with tempfile.TemporaryDirectory(prefix=".omniagent-stage-", dir=dest.parent) as temp:
                staging = Path(temp) / "skill"
                copy_skill(source_path, staging, action["source_hash"])
                backup = backup_dir / str(index)
                if action["status"] == "update":
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
            completed.append({**action, "backup": str(backup) if action["status"] == "update" else None,
                              "previous": old_records[str(dest)]})

        # Save recovery journal before publishing the updated manifest.
        journal = {"version": 1, "status": "applied", "transaction": transaction,
                   "actions": completed}
        save_json(state_dir / "transactions" / f"{transaction}.json", journal)
        for a in completed:
            state["managed"][a["destination"]] = {"source": a["source"],
                                                      "hash": a["source_hash"]}
        save_json(state_dir / "state.json", state)
    except Exception:
        for a in reversed(completed):
            dest = Path(a["destination"])
            if dest.is_dir() and not dest.is_symlink() and tree_digest(dest, source=False) == a["source_hash"]:
                remove_managed(dest)
                if a["backup"]:
                    shutil.copytree(a["backup"], dest)
        journal_path = state_dir / "transactions" / f"{transaction}.json"
        if journal_path.exists():
            saved = read_json(journal_path, {})
            saved["status"] = "failed_reverted"
            save_json(journal_path, saved)
        raise
    return transaction, actions


def rollback(state_dir: Path, transaction: str) -> int:
    with manager_lock(state_dir):
        return _rollback_locked(state_dir, transaction)


def _rollback_locked(state_dir: Path, transaction: str) -> int:
    if not TRANSACTION_ID.fullmatch(transaction):
        raise SafetyError("Invalid transaction ID")
    journal_path = state_dir / "transactions" / f"{transaction}.json"
    journal = read_json(journal_path, {})
    if journal.get("status") != "applied" or journal.get("transaction") != transaction:
        raise SafetyError("Transaction not found or already rolled back")
    state = state_at(state_dir)
    actions = journal["actions"]
    # Validate the whole transaction first; never delete a user-edited skill.
    for a in actions:
        dest = Path(a["destination"])
        if (dest.is_symlink() or not dest.is_dir()
                or tree_digest(dest, source=False) != a["source_hash"]
                or state["managed"].get(str(dest), {}).get("hash") != a["source_hash"]):
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
    if source.is_dir() and not source.is_symlink():
        skills = sources_at(source)
        print(f"source     {len(skills):4} skills  {source}")
    else:
        print(f"source        (not initialized)  {source}")
    for name, root in targets.items():
        if root.is_dir():
            skills = sum(1 for child in root.iterdir()
                         if child.is_dir() and (child / "SKILL.md").is_file())
            print(f"{name:10} {skills:4} skills  {root}")
        else:
            print(f"{name:10}      (not found)  {root}")


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Safe local skill manager (copy-based, no symlinks)")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("discover", "plan", "apply"):
        part = sub.add_parser(command)
        part.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
        part.add_argument("--state-dir", type=Path, default=DEFAULT_STATE)
        part.add_argument("--target", action="append", metavar="NAME=PATH")
        part.add_argument("--skill", action="append", help="only manage named skill (repeatable)")
        if command == "apply":
            part.add_argument("--yes", action="store_true", help="explicitly approve filesystem writes")
            part.add_argument("--skip-blocked", action="store_true")
    undo = sub.add_parser("rollback")
    undo.add_argument("--state-dir", type=Path, default=DEFAULT_STATE)
    undo.add_argument("--transaction", required=True)
    undo.add_argument("--yes", action="store_true")
    args = parser.parse_args(argv)
    try:
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
        actions = plan(source, state_dir, targets, selected)
        if args.command == "apply":
            if not args.yes:
                raise SafetyError("Apply modifies skill folders; inspect plan first, then use --yes")
            transaction, actions = apply(source, state_dir, targets, selected, args.skip_blocked)
            print(f"Transaction: {transaction or '(nothing changed)'}")
        for a in actions:
            print(f"{a['target']:10} {a['status']:18} {a['name']}")
        return 0
    except (SafetyError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(cli())
