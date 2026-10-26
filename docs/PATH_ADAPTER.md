# Path Adapter v0.3a — read-only preview

This is the **first, deliberately read-only** component of v0.3. It does not
update agent installations, modify the canonical SSOT, adopt existing skills,
or touch `.local/manager/state.json`.

## Why explicit templates?

Relative references such as `scripts/run.py` and `references/README.md` should
remain relative. The [Agent Skills specification](https://agentskills.io/specification)
recommends resolving supporting files from the skill root. A path adapter is
only appropriate when a skill really needs a vendor-specific path.

We do **not** blindly replace every `.claude/` with `.agents/`. Some references
are Claude-only (hooks, settings, secrets or CLI commands), and changing them
would break the skill. Only a human-selected list of UTF-8 files can contain
placeholders.

## Manifest format

`adapters.example.json` is a harmless, tracked example. Create a **private**
manifest under `.local/` for actual skills. Its shape is:

```json
{
  "version": 1,
  "targets": {
    "claude": { "SKILLS_DIR": ".claude/skills" },
    "agents": { "SKILLS_DIR": ".agents/skills" },
    "gemini": { "SKILLS_DIR": ".gemini/config/skills" }
  },
  "skills": {
    "demo-skill": { "files": ["SKILL.md", "references/setup.md"] }
  }
}
```

In the **synthetic** `demo-skill/SKILL.md`:

```markdown
See {{SKILLS_DIR}}/{{SKILL_NAME}}/scripts/run.py
```

For the `agents` target, the in-memory expected content becomes
`.agents/skills/demo-skill/scripts/run.py`; for `claude`, it becomes
`.claude/skills/demo-skill/scripts/run.py`. Files not named in `files` are
kept byte-for-byte, and an undeclared template placeholder is an error.

Some skills legitimately contain literal `{{UPPER_CASE}}` text (for example
mail or document templates). List those files under `ignore_placeholders` for the
skill: they are copied unchanged and not treated as templates. A file cannot be
in both `files` and `ignore_placeholders`; any other file with a placeholder
still fails closed.

```json
"skills": { "demo-skill": { "files": ["SKILL.md"], "ignore_placeholders": ["references/mail.md"] } }
```

Command output is forced to UTF-8 (invalid characters replaced) so `--diff`
works on Windows consoles that default to cp1252.

Supported placeholders: `{{SKILLS_DIR}}`, `{{AGENT_CONFIG_DIR}}`,
`{{SECRET_DIR}}` (paths only, **never secret values**) and built-in
`{{SKILL_NAME}}`. Undefined placeholders cause a hard failure. Manifest path
values reject shell metacharacters and parent-directory traversal.

## Try on disposable data first (PowerShell)

Use a **copy** of one source skill in a temporary folder under `.local/`,
manually replace only verified path references with placeholders there, and
add that skill to a private copy of the manifest. Do **not** modify the live
`ssot/skills/` yet: the v0.2 manager is not adapter-aware.

```powershell
New-Item -ItemType Directory -Force .local | Out-Null
Copy-Item adapters.example.json .local/adapters.json

$fixture = Join-Path $PWD '.local\adapter-fixtures\skills'
$agents = Join-Path $HOME '.agents\skills'

# Edit .local/adapters.json and place a disposable templated skill under $fixture.
# The target is *read*, never written.
python path_adapter.py --manifest .local/adapters.json `
  --source $fixture --target "agents=$agents" --skill demo-skill --diff

# Additional v0.3a preview: manager statuses against rendered hashes.
# JSON is printed to your local terminal only (no private report file).
python omniagent.py plan --adapter-manifest .local/adapters.json `
  --source $fixture --target "agents=$agents" --skill demo-skill --json
```

`--diff` is optional and requires exactly one `--skill` and one `--target`.
It may display private target content **only in the local terminal**; do not
paste it into a public issue. Omitting `--diff` prints status and a short hash.

### Three distinct hashes in `omniagent.py plan`

The optional `--adapter-manifest` is accepted by **`plan` only**. It adds:

- `source_hash`: SHA-256 of the raw canonical SSOT tree (with placeholders).
- `render_hash`: SHA-256 of the expected tree after per-agent rendering.
- `installed_hash`: SHA-256 of the existing target tree; `null` if absent or
  unsafe to read. The installed comparison ignores regenerable caches.

`recorded_hash` is the existing v0.2 manager state value, if any, and
`render_matches_installed` indicates byte-for-byte equality of the relevant
tree hashes. **No manager state is migrated or written.** Existing `blocked`,
`blocked_unmanaged`, and `conflict` statuses remain blocked even when rendered
content happens to match the installed bytes. Only *managed and unmodified*
entries can be reclassified from a false `update` to `unchanged`.

The JSON includes machine-specific paths and potentially private skill names;
do not upload it to GitHub. For ordinary v0.2 operations without this option,
`omniagent.py plan`, `adopt`, `apply` and `rollback` behave exactly as before.
In particular, **`apply --adapter-manifest` is intentionally unsupported**.

## Status meanings

| Status | Meaning |
|---|---|
| `match` | Installed folder exactly matches the per-target rendered tree hash |
| `drift` | Installed folder exists but differs; inspect text/assets manually |
| `missing` | Installed skill folder does not exist; no installation occurs |
| `unsafe` | Target is linked, not a directory, or fails the existing safety checks |

Matching a rendered hash is **not proof the skill runs**. Script paths, plugins,
MCP dependencies and vendor-specific commands still need a runtime smoke test.
Like the v0.2 manager, this hash comparison ignores regenerable target-side
`node_modules` and `__pycache__`; those files are not safe to delete during an
update, so v0.2's `blocked_vendor` guard must remain in place.

## What comes next

1. Gather a sample of the 32 `agents` + 4 `claude` path-drift cases **locally**.
   Classify actual skill-folder paths separately from vendor-only commands and
   secrets. Do not publish private samples.
2. Make portable copies of only the verified cases and confirm `match` with
   `path_adapter.py` against the existing agent installation.
3. Build adapter-aware `apply`/`adopt`/`rollback` with transaction journals,
   backups, explicit opt-in and target revalidation. The manager's read-only
   `plan --adapter-manifest` is available, but **do not run `omniagent.py apply`
   against a templated canonical SSOT**: it still copies template source
   literally.
4. Test on disposable stores on both Windows and Linux; then smoke-test the
   behavior of Claude, Codex and Antigravity, one skill at a time.
