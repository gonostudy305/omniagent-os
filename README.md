# OmniAgent-OS / Local Agent Skills SSOT

> **A Unified Single-Source-of-Truth (SSOT) & Cross-Agent Management Layer for Claude Code, OpenAI Codex, and Antigravity (Gemini).**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)](#)
[![Agents Supported](https://img.shields.io/badge/Agents-Claude%20Code%20%7C%20Codex%20%7C%20Antigravity-orange)](#)

---

## 📌 The Problem: The "Siloed Agent" Nightmare

Modern developers increasingly leverage multiple AI coding engines on the same local workstation:
- **Claude Code** for architecture analysis and planning.
- **OpenAI Codex CLI** for rapid implementation, unit tests, and refactoring.
- **Google Antigravity (Gemini)** for multimodal tasks, generative UI, and complex workflows.

However, each agent installs its own fragmented set of skills and isolated memory silos:
- `.claude/skills`
- `.codex/skills` & `.agents/skills`
- `.gemini/config/skills`

### 🔍 Real-World Audit Findings (60,000+ Files Sample)
During our empirical audit of a multi-agent developer workstation, we observed:
1. **The Impact of Embedded Dependencies (`node_modules`):**  
   Copying skill directories across agents ballooned local storage to **60,000+ files (~1.1 GB)**. In our audit sample, **over 85% of these files were nested `node_modules`** inside 7–9 complex skills. These are reproducible dependencies that should be installed on demand rather than duplicated across agent configurations. The actual instructional markdown and source code accounted for **~3,200 files (~55–75 MB)**.
2. **Artificial "Content Drift" (Hardcoded Paths):**  
   Out of 38 skills flagged as differing across agent directories, **31 skills (81.6%) were logically identical**; their only difference was hardcoded directory paths (e.g., `.claude/skills/...` vs `.agents/skills/...` vs `.Codex/secrets/...`).
3. **True Version Drift:**  
   Only **3 skills** in our sample had genuine logic updates (`skill-domain-mcp`, `skill-media-sync`, and `skill-market-analyst`), where the Antigravity/Gemini versions contained enhanced hallucination guards and tracking policies.

---

## 🏛 Architecture: Unified Agent OS

```mermaid
flowchart TD
    SSOT["Central SSOT Repository<br/>(Normalized Skills Library)<br/>ssot/skills/"]
    
    SSOT -->|Agnostic Path Resolver| ADAPTER["Path & Template Adapter<br/>({{SKILLS_DIR}}, {{SECRET_DIR}})"]
    
    ADAPTER -->|Target: Claude| CLAUDE[".claude/skills/"]
    ADAPTER -->|Target: Gemini| GEMINI[".gemini/config/skills/"]
    ADAPTER -->|Target: Codex / Agents| AGENTS[".agents/skills/"]
    
    CODEX_SYS[".codex/skills/.system/<br/>(Internal OpenAI Runtime)"] -.->|Preserved Isolately| CODEX_CLI["Codex CLI Runtime"]
```

---

## 🚀 Key Features

- **Single Source of Truth (SSOT):** Maintain one canonical copy of skills to eliminate redundant copies across agent directories.
- **Safe Skill Manager MVP:** Inventory, plan, copy, update, and roll back *managed* skill copies. Existing unmanaged skills are never replaced automatically.
- **Smart Path Decoupling (v0.3 preview):** The read-only `path_adapter.py` previews explicitly templated references (`{{SKILLS_DIR}}`) and verifies their rendered hashes per agent. Adapter-aware installation is still planned.
- **Clean Source & Reproducible Dependencies:** Keeps repository source free of committed vendor packages (`node_modules`), with planned hydration scripts for skills requiring local runtime environments.
- **Preserved System Runtimes:** Safely isolates vendor-specific runtimes (e.g., OpenAI `.system` skills) without polluting cross-agent tool registries.
- **Audit & Validation Tooling:** Built-in Python scripts to audit, deduplicate, and verify parity across all local agent installations.

---

## 📂 Repository Structure

```text
├── .gitignore
├── README.md
├── LICENSE
├── audit_skills.py           # Fast SHA-256 hash & line-level unified diff auditor
├── generate_report.py        # Generates markdown audit reports
├── omniagent.py              # Safe discover / plan / adopt / apply / rollback CLI
├── path_adapter.py           # Read-only v0.3 per-agent template preview
├── adapters.example.json     # Public example; private manifest belongs in .local/
├── tests/                    # Synthetic audit and Skill Manager safety tests
├── SKILLS_AUDIT_REPORT.md    # Case study audit report across 4 skill stores
└── ssot/                     # Local SSOT directory (managed locally / .gitignored)
    └── skills/               # Canonical, clean skills library
```

---

## 🛠 Quick Start

### 1. Audit your local environment
Run the non-destructive audit script to inspect your current skills across Claude, Gemini, and Codex:
```powershell
python audit_skills.py --output .local/reports
python generate_report.py --input .local/reports/audit.json
```
Add `--store NAME=PATH` (repeatable) to audit custom skill directories. Run the tests with `python tests/test_audit.py`.

### 2. Inspect Audit Results
The scripts write `audit.json`, `audit_report.md` and `narrative_report.md` to `.local/reports/` (git-ignored, since they contain machine-specific paths and skill names). `SKILLS_AUDIT_REPORT.md` in this repo is a static, anonymized example.

### 3. Manage skills safely (v0.2 preview)

`omniagent.py` only writes to explicitly selected targets. **Plan does not change any files.** It never overwrites an existing unmanaged skill, and it deliberately leaves Codex's internal `.codex/skills/.system` alone. The default mode copies files, so Windows symlink privileges are not required.

Example from PowerShell (run at the repo root):

```powershell
# Read-only: show which standard stores are present (SSOT may be absent).
python omniagent.py discover

# Review changes for a single agent, using your private local SSOT.
$skills = Join-Path $PWD 'ssot\skills'
$claude = Join-Path $HOME '.claude\skills'
python omniagent.py plan --source $skills --target "claude=$claude"

# Preview existing unmanaged skills that exactly match the SSOT.
python omniagent.py adopt --source $skills --target "claude=$claude"

# Only record exact matches in private manager state; NO skill files are changed.
python omniagent.py adopt --source $skills --target "claude=$claude" --yes

# For a mismatch, review its SKILL.md diff in your LOCAL terminal only.
python omniagent.py adopt --source $skills --target "claude=$claude" --skill my-skill --diff

# Only after reviewing the plan: install missing skills and update managed,
# unchanged copies. Leave unmanaged skills and local edits untouched.
python omniagent.py apply --source $skills --target "claude=$claude" --yes --skip-blocked

# Copy the transaction ID printed by apply to undo that operation later:
python omniagent.py rollback --transaction 'YYYYMMDDTHHMMSSZ-xxxxxxxxxxxx' --yes
```

For multiple stores, repeat `--target NAME=PATH`, e.g. `--target "agents=$HOME\.agents\skills"`. To deploy just one skill, use `--skill NAME` (repeatable).

**What adoption does:** `adopt` checks an existing target folder against its canonical SSOT copy. Only byte-for-byte matching files and directory structure qualify as `eligible`; others remain `mismatch`, `unsafe`, or `already_managed`. `adopt` without `--yes` is read-only. With `--yes`, it writes **only** `.local/manager/state.json` and a transaction journal. `rollback --transaction ID --yes` for an adoption transaction **untracks** the skill without deleting or rewriting its files.

An adopted skill stays unchanged by subsequent `apply` operations unless you additionally specify `--allow-update-adopted`. This separate opt-in makes upgrading user-owned folders an explicit decision. A `--diff` shows `SKILL.md` text only when exactly one skill and one target are selected; it may reveal private content in your local terminal, and differences in other assets must be inspected separately.

Important safety boundaries:

- Backups, transaction journals, hashes, and machine-specific paths remain under `.local/manager/`, which is excluded from Git. **Back up this folder securely** if you want to keep rollback capability.
- A skill is changed only if previously installed or explicitly adopted *and* its current content still matches the recorded checksum. Updates to adopted folders additionally require `--allow-update-adopted`. Unmanaged collisions and manual edits are blocked; `--skip-blocked` explicitly leaves those entries alone.
- `node_modules`, `.git`, and caches are not copied. Suspected credential files and source symlinks cause a refusal. Dependencies are **not hydrated** yet.
- This preview does not rewrite hardcoded paths inside `SKILL.md`, merge second brains, or manage external agent runtimes. It does not guarantee crash-safe filesystem transactions or coordinate concurrent managers using different state directories.
- The manager serializes writers with an OS mutex. A leftover `manager.lock` is reclaimed only when the OS **confirms its PID is no longer running**; live, reused, or unrecognizable PIDs fail closed and require manual inspection. Never delete an active lock. The ignored `.local/manager/manager.mutex` file may remain after shutdown; that is normal and its OS lock releases automatically.
- On Windows, run the CLI from a normal user PowerShell session; test with disposable `--target` directories before using real agent stores.

Run isolated tests (no third-party Python packages required):

```powershell
python tests/test_audit.py
python -m unittest discover -s tests -p test_skill_manager.py -v
```

### 4. Preview portable paths (v0.3a, read-only)

Use a **disposable SSOT copy** with explicit placeholders and an opt-in private
manifest, as described in [the Path Adapter guide](docs/PATH_ADAPTER.md). For
example, once your private manifest and fixture are ready:

```powershell
python path_adapter.py --manifest .local/adapters.json `
  --source .local/adapter-fixtures/skills `
  --target "agents=$HOME\.agents\skills" --skill demo-skill --diff
```

This command does **not** write to agent stores or manager state. Do not run
`omniagent.py apply` against templated SSOT sources until adapter-aware apply
and rollback are implemented.

---

## 📚 Docs

- [docs/PROJECT_CONTEXT.md](docs/PROJECT_CONTEXT.md): why this project exists, design principles, decisions, roadmap
- [docs/DEVELOPMENT_LOG.md](docs/DEVELOPMENT_LOG.md): what was done on a real machine, metrics, lessons
- [docs/USAGE.md](docs/USAGE.md): Skill Manager runbook (discover, adopt, plan, apply, rollback)
- [docs/PATH_ADAPTER.md](docs/PATH_ADAPTER.md): v0.3a read-only template manifest and rendered-hash preview

---

## 🗺 Roadmap

- [x] Phase 1: Safe multi-agent configuration backup with zero credential leakage.
- [x] Phase 2: Complete cross-agent skill audit and line-level diff drift analysis.
- [x] Phase 3: Extraction of canonical SSOT structure (local-first).
- [x] Phase 4a: Conservative local Skill Manager MVP (discover, plan, adopt exact matches, copy, rollback).
- [x] Phase 4b preview: Read-only, explicit per-agent path template renderer and drift detection (v0.3a).
- [ ] Phase 4b apply: Safe adapter-aware installation, rollback, drift reconciliation, dependency-aware validation, and crash-safe journal recovery.
- [ ] Phase 5: Automated dependency hydration for skills with package requirements.
- [ ] Phase 6: Cross-Agent Memory Integration via MCP (Memorix / Local Knowledge Graph).

---

## 📄 License
MIT License.
