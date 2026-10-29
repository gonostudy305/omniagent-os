"""Fake `claude` / `codex` / `agy` CLI used by test_mcp_register.py. It edits only the file named by FAKE_CONFIG.

FAKE_KIND: claude | codex | agy        which config dialect to write
FAKE_MODE: ok | fail | leak | noop | mutate | bad-command | reformat
"""

import json
import os
import re
import sys
from pathlib import Path

NAME = "omniagent-brain"
TOKEN = os.environ.get("FAKE_TOKEN", "")
kind, mode = os.environ["FAKE_KIND"], os.environ.get("FAKE_MODE", "ok")
path = Path(os.environ["FAKE_CONFIG"])
argv = sys.argv[1:]

if argv[:2] not in (["mcp", "add"], ["mcp", "remove"]):
    sys.exit("unsupported")
adding = argv[1] == "add"

if mode == "leak":
    print("failed while handling header Authorization: Bearer " + TOKEN, file=sys.stderr)
    sys.exit(1)
if mode == "fail":
    print("boom", file=sys.stderr)
    sys.exit(1)
if mode == "noop":
    sys.exit(0)

scope = "local"
if "--scope" in argv:
    scope = argv[argv.index("--scope") + 1]
command_line = argv[argv.index("--") + 1:] if "--" in argv else []


def entry():
    if mode == "bad-command":
        return {"command": "definitely-not-a-real-program", "args": []}
    return {"command": command_line[0], "args": command_line[1:]}


def write_json(doc):
    if mode == "reformat":  # a different indent, key order and CRLF line endings than the original
        path.write_bytes(json.dumps(doc, indent=4, sort_keys=True).replace("\n", "\r\n").encode("utf-8"))
    else:
        path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


if kind in ("claude", "agy"):
    doc = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    if kind == "claude" and scope == "local":
        key = os.getcwd().replace("\\", "/")
        servers = doc.setdefault("projects", {}).setdefault(key, {}).setdefault("mcpServers", {})
    else:
        servers = doc.setdefault("mcpServers", {})
    if adding:
        servers[NAME] = entry()
    else:
        servers.pop(NAME, None)
    if mode == "mutate" and "other" in doc.get("mcpServers", {}):
        doc["mcpServers"]["other"]["args"] = ["changed"]
    write_json(doc)
else:  # codex: TOML text, so only our own table is ever touched
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    block = re.compile(r"\n?\[mcp_servers\." + NAME + r"\]\n.*?(?=\n\[|\Z)", re.S)
    text = block.sub("\n", text)
    if adding:
        args = ", ".join(json.dumps(a) for a in command_line[1:])
        text = text.rstrip("\n") + f"\n\n[mcp_servers.{NAME}]\ncommand = {json.dumps(command_line[0])}\nargs = [{args}]\n"
    if mode == "mutate":
        text = text.replace('args = ["--flag"]', 'args = ["changed"]')
    path.write_text(text, encoding="utf-8")
