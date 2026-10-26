"""Read-only, opt-in path template preview for OmniAgent-OS.

The adapter never rewrites SSOT sources, target skills, or manager state.
Only explicitly enumerated files may contain path placeholders. A future
release may feed the rendered hashes into the managed apply/rollback flow.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath

from omniagent import (
    DEFAULT_SOURCE,
    DEFAULT_STATE,
    SKILL_NAME,
    SafetyError,
    parse_targets,
    skill_directories,
    skill_files,
    tree_digest,
    validate_layout,
)


PLACEHOLDER = re.compile(rb"\{\{([A-Z][A-Z0-9_]*)\}\}")
PATH_VALUE = re.compile(r"^[\w. :/\\-]+$", re.UNICODE)
PATH_KEYS = {"SKILLS_DIR", "AGENT_CONFIG_DIR", "SECRET_DIR"}
MAX_DIFF_BYTES = 1_000_000


def _check_keys(value: dict, allowed: set[str], where: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise SafetyError(f"Unsupported {where} fields: {', '.join(sorted(unknown))}")


def _check_relpath(value: str) -> None:
    path = PurePosixPath(value)
    if (not value or "\\" in value or path.is_absolute() or
            any(part in {".", "..", ""} for part in value.split("/")) or
            path.as_posix() != value):
        raise SafetyError(f"Invalid template file path: {value!r}")


def load_manifest(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise SafetyError(f"Missing or linked adapter manifest: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise SafetyError(f"Invalid adapter manifest: {exc}") from exc
    if not isinstance(data, dict):
        raise SafetyError("Adapter manifest must be an object")
    _check_keys(data, {"version", "targets", "skills"}, "manifest")
    if data.get("version") != 1 or not isinstance(data.get("targets"), dict) or not isinstance(data.get("skills"), dict):
        raise SafetyError("Adapter manifest requires version=1, targets and skills objects")
    if not data["targets"] or not data["skills"]:
        raise SafetyError("Adapter manifest must select targets and skills")

    for agent, config in data["targets"].items():
        if not SKILL_NAME.fullmatch(agent) or not isinstance(config, dict):
            raise SafetyError(f"Invalid target mapping: {agent!r}")
        if not config or set(config) - PATH_KEYS:
            raise SafetyError(f"Unsupported or empty template variables for target: {agent}")
        for name, value in config.items():
            if (not isinstance(value, str) or not value or value.strip() != value or
                    not PATH_VALUE.fullmatch(value) or
                    ".." in value.replace("\\", "/").split("/")):
                raise SafetyError(f"Unsafe path value: target={agent} key={name}")

    for skill, config in data["skills"].items():
        if not SKILL_NAME.fullmatch(skill) or not isinstance(config, dict):
            raise SafetyError(f"Invalid skill mapping: {skill!r}")
        _check_keys(config, {"files"}, f"skill {skill}")
        files = config.get("files")
        if (not isinstance(files, list) or not files or
                any(not isinstance(f, str) for f in files)):
            raise SafetyError(f"Skill {skill} must specify a non-empty list of files")
        for item in files:
            _check_relpath(item)
        if len(set(files)) != len(files):
            raise SafetyError(f"Repeated template file for skill: {skill}")
    return data


def _render(data: bytes, variables: dict[str, str], *, name: str, relative: str) -> bytes:
    def replace(match: re.Match[bytes]) -> bytes:
        key = match.group(1).decode("ascii")
        if key == "SKILL_NAME":
            return name.encode("utf-8")
        if key not in variables:
            raise SafetyError(f"Unresolved placeholder {{{{{key}}}}} in {name}/{relative}")
        return variables[key].encode("utf-8")

    return PLACEHOLDER.sub(replace, data)


def rendered_skill(skill_dir: Path, template_files: list[str], variables: dict[str, str]) -> tuple[str, dict[str, bytes]]:
    """Hash a virtual rendered tree using the manager's deterministic digest layout."""
    entries = dict(skill_files(skill_dir))
    missing = set(template_files) - entries.keys()
    if missing:
        raise SafetyError(f"Configured template files do not exist in {skill_dir.name}: {sorted(missing)}")
    digest = hashlib.sha256()
    for directory in skill_directories(skill_dir):
        digest.update(b"DIR\0" + directory.encode("utf-8") + b"\0")
    rendered: dict[str, bytes] = {}
    for relative, path in entries.items():
        body = path.read_bytes()
        if relative in template_files:
            # The template must be UTF-8; decoding also prevents binary rewriting.
            try:
                body.decode("utf-8")
            except UnicodeError as exc:
                raise SafetyError(f"Template file is not UTF-8: {skill_dir.name}/{relative}") from exc
            body = _render(body, variables, name=skill_dir.name, relative=relative)
            rendered[relative] = body
        elif PLACEHOLDER.search(body):
            raise SafetyError(f"Undeclared template placeholder: {skill_dir.name}/{relative}")
        digest.update(relative.encode("utf-8") + b"\0" + body + b"\0")
    return digest.hexdigest(), rendered


def adapter_plan(source: Path, state_dir: Path, targets: dict[str, Path], manifest: dict,
                 selected: set[str] | None = None) -> list[dict]:
    validate_layout(source, state_dir, targets)
    if selected and selected - manifest["skills"].keys():
        raise SafetyError(f"Skills not configured for adaptation: {sorted(selected - manifest['skills'].keys())}")
    for agent in targets:
        if agent not in manifest["targets"]:
            raise SafetyError(f"No adapter variables configured for target: {agent}")
    results = []
    for name, config in sorted(manifest["skills"].items()):
        if selected is not None and name not in selected:
            continue
        skill_dir = source / name
        if skill_dir.is_symlink() or not skill_dir.is_dir():
            raise SafetyError(f"Missing canonical skill: {skill_dir}")
        for agent, root in targets.items():
            digest, rendered = rendered_skill(skill_dir, config["files"], manifest["targets"][agent])
            dest = root / name
            if dest.is_symlink() or (dest.exists() and not dest.is_dir()):
                status = "unsafe"
            elif not dest.exists():
                status = "missing"
            else:
                try:
                    status = "match" if tree_digest(dest, source=False) == digest else "drift"
                except (SafetyError, OSError):
                    status = "unsafe"
            results.append({"target": agent, "name": name, "status": status,
                            "rendered_hash": digest, "destination": str(dest),
                            "rendered_files": rendered})
    return results


def show_diff(entry: dict) -> None:
    """Display a bounded, local-only text diff; never persist or upload it."""
    if entry["status"] not in {"match", "drift"}:
        print("Destination unavailable for diff")
        return
    dest = Path(entry["destination"])
    for relative, expected in entry["rendered_files"].items():
        actual_file = dest / relative
        if (not actual_file.is_file() or actual_file.is_symlink() or
                actual_file.stat().st_size > MAX_DIFF_BYTES or len(expected) > MAX_DIFF_BYTES):
            print(f"{relative}: missing, linked, or too large; inspect manually")
            continue
        try:
            before = expected.decode("utf-8").splitlines(keepends=True)
            after = actual_file.read_bytes().decode("utf-8").splitlines(keepends=True)
        except UnicodeError:
            print(f"{relative}: non-UTF-8 destination; inspect manually")
            continue
        lines = list(difflib.unified_diff(before, after, fromfile=f"rendered/{relative}",
                                          tofile=f"target/{relative}", n=3))
        if lines:
            print(f"CAUTION: displaying private local content for {relative} (terminal only)")
            for line in lines[:150]:
                print(line, end="" if line.endswith("\n") else "\n")
            if len(lines) > 150:
                print(f"... {len(lines) - 150} more diff lines omitted")
        else:
            print(f"{relative}: matches rendered content")


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only per-agent path adapter preview")
    parser.add_argument("--manifest", type=Path, required=True, help="Explicit opt-in JSON template manifest")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--target", action="append", metavar="NAME=PATH", required=True)
    parser.add_argument("--skill", action="append", metavar="NAME")
    parser.add_argument("--diff", action="store_true", help="Display private local diff for one skill and target")
    args = parser.parse_args(argv)
    try:
        targets = parse_targets(args.target)
        selected = set(args.skill) if args.skill else None
        if args.diff and (not selected or len(selected) != 1 or len(targets) != 1):
            raise SafetyError("--diff requires exactly one --skill and one --target")
        manifest = load_manifest(args.manifest)
        actions = adapter_plan(args.source, args.state_dir, targets, manifest, selected)
        for entry in actions:
            print(f"{entry['target']:10} {entry['status']:10} {entry['name']:32} {entry['rendered_hash'][:12]}")
        if args.diff and actions:
            show_diff(actions[0])
        print("Read-only preview: no skill, SSOT, or manager state was modified.")
        return 0
    except (SafetyError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(cli())
