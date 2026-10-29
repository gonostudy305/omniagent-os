# Path Adapter v0.3 — experimental render-aware plan and apply

The original `path_adapter.py` and `omniagent.py plan --adapter-manifest` remain
**read-only**. The v0.3b `omniagent.py apply` path can stage rendered files and
install or update **exactly one selected skill and target** with separate
consent, a verified backup and a journal that `rollback` can undo.

This is experimental. Use **disposable source/target folders only** until the
Windows compatibility and power-loss recovery reviews are complete. It never
edits the canonical SSOT source. The existing v0.2 commands without a manifest
remain unchanged except for a stricter late vendor-dependency guard.

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
`ssot/skills/`: template a copy, and treat adopting skills into the main state as a separate step that needs explicit approval.

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

The optional `--adapter-manifest` in **`plan`** adds:

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
do not upload it to GitHub. Ordinary v0.2 operations remain available, **except**
that a managed record containing `rendered_for` is deliberately marked
`blocked_rendered` in a plan without `--adapter-manifest` and cannot be
overwritten by a plain `apply`. With `--skip-blocked`, it is left untouched
while unrelated safe actions can proceed. `adopt` and `rollback` retain their
existing behavior.

### Experimental v0.3b apply and rollback (disposable targets only)

**First**, take a backup and inspect `plan --adapter-manifest --json`. Then
run **one skill on one disposable target** with both consent flags:

```powershell
$sandboxTarget = Join-Path $PWD '.local\adapter-fixtures\target-agents'
python omniagent.py apply --source $fixture `
  --state-dir .local/adapter-fixtures/manager `
  --target "agents=$sandboxTarget" --skill demo-skill `
  --adapter-manifest .local/adapters.json `
  --allow-rendered-apply --yes

# Capture the transaction ID from the apply output, then undo without a manifest:
python omniagent.py rollback --state-dir .local/adapter-fixtures/manager `
  --transaction 'YYYYMMDDTHHMMSSZ-xxxxxxxxxxxx' --yes
```

It stages the raw skill with source-hash verification, renders only explicitly
configured UTF-8 files, and verifies the resulting tree against `render_hash`
before touching the target. Updates retain verified backups. The manager's
private state continues to use **schema version 1**, retaining `hash` as the
actual installed-byte digest; new rendered entries also record `source_hash`
and `rendered_for`. Journals retain `applied_hash` so the original bytes can be
restored. Old v0.2 journals remain readable.

Unmanaged collisions, manual edits, symlinks, and installed `node_modules`
block updates. Previously adopted skills require the **additional** flag
`--allow-update-adopted`; never combine it with `--skip-blocked` unless you
have inspected all statuses. `adopt` itself is **not** render-aware yet.

**No accidental downgrade to the raw SSOT:** after rendered apply, a later
`python omniagent.py apply --yes` without the manifest refuses that managed
skill, even if the source file still contains placeholders. The explicit
render-aware command may manage it only when the manifest contains that skill
and the recorded `rendered_for` agrees with the target name. Always inspect
the adapter-aware plan before applying again.

Crash/power-loss journal recovery, dependency hydration, safe reconciliation
of the 36 real mismatches, and runtime smoke tests remain unimplemented.

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
3. Finish adapter-aware `adopt` and state migration review. The guarded
   `apply` and rollback paths work with synthetic fixtures but are not yet
   approved for the production `ssot/skills` or agent directories.
4. Test on disposable stores on both Windows and Linux; then smoke-test the
   behavior of Claude, Codex and Antigravity, one skill at a time.

## Adapter-aware adopt (v0.3c)

`adopt --adapter-manifest MANIFEST --target NAME=PATH` also recognises an installed
folder that is byte-identical to the per-agent *rendered* skill (status
`eligible_rendered`). Without `--yes` it is a preview. With `--yes` it writes only
manager state and the journal; **no skill file is modified**.

The record stores `hash` (the rendered hash that is actually installed),
`source_hash` (the unrendered SSOT hash), `rendered_for` (the agent) and
`origin: adopted`. Consequences:

- A plain `plan`/`apply` without the manifest reports `blocked_rendered` and never
  overwrites it; `plan --adapter-manifest` reports it `unchanged`.
- Rolling back the adoption only removes the bookkeeping entry.
- Folders that were edited, rendered for a different agent, or that differ in any
  way stay `mismatch` and are not adopted. A skill not listed in the manifest keeps
  the v0.2 behaviour (exact match with the unrendered SSOT).
