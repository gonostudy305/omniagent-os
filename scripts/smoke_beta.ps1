<#
.SYNOPSIS
  Windows beta smoke test for OmniAgent-OS (Windows PowerShell 5.1, no Administrator needed).

.DESCRIPTION
  Default mode (ReadOnly) only reads. It never writes to any agent skills folder.
  It checks Python, repository layout, the adapter manifest, and for each pilot skill:
  presence, junction/symlink, frontmatter, leftover placeholders, referenced files,
  skill-to-skill dependencies, and the source/render/installed hashes.

  Mode Trial additionally records an adoption into a NEW state folder under -StateRoot
  (default .local\smoke-state; state only, never skill files), runs plan, rolls the adoption
  back, and proves with a byte-level fingerprint that the skill folders did not change.
  Each Trial state folder is kept as evidence (its journal shows the adoption and rollback)
  and is never deleted by this script; delete it by hand once you have reviewed it.

  What this script can NOT prove: that an agent really loads the skill, or that the skill
  completes its task. Those are listed as NOT VERIFIED and need a manual check (docs\BETA.md).

  Exit codes: 0 = no check failed, 1 = at least one check failed, 2 = environment error
  (for example Python missing).

.EXAMPLE
  powershell -File scripts\smoke_beta.ps1 -Source .local\experiments\templated26\source -Manifest .local\experiments\templated26\adapters.json
#>
[CmdletBinding()]
param(
    [string]$Source,
    [string]$Manifest,
    [string]$Target = (Join-Path $HOME ".agents\skills"),
    [string]$AgentName = "agents",
    [string[]]$Skills = @("threejs", "ask", "preview"),
    [ValidateSet("ReadOnly", "Trial")][string]$Mode = "ReadOnly",
    [string]$ReportDir,
    [string]$StateRoot,
    [switch]$RunTests
)

$ErrorActionPreference = "Stop"
# `powershell -File ... -Skills a,b` delivers one string "a,b"; accept both forms.
$Skills = @($Skills | ForEach-Object { $_ -split "," } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
$RepoRoot = Split-Path -Parent $PSScriptRoot
if (-not $ReportDir) { $ReportDir = Join-Path $RepoRoot ".local\reports" }
$Results = New-Object System.Collections.ArrayList
$Stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
if (-not $StateRoot) { $StateRoot = Join-Path $RepoRoot ".local\smoke-state" }
# Seconds alone collide when two runs start in the same second.
$RunId = "$Stamp-$([guid]::NewGuid().ToString('N').Substring(0, 8))"

function Add-Result([string]$Id, [string]$Status, [string]$Detail) {
    [void]$Results.Add([pscustomobject]@{ id = $Id; status = $Status; detail = $Detail })
    $color = @{ PASS = "Green"; FAIL = "Red"; WARN = "Yellow"; SKIP = "DarkGray"; "NOT VERIFIED" = "Cyan" }[$Status]
    Write-Host ("[{0,-12}] {1}  {2}" -f $Status, $Id, $Detail) -ForegroundColor $color
}

function Redact([string]$Text) {
    # Keep personal paths and the user name out of the saved report.
    if ($null -eq $Text) { return $Text }
    $t = $Text
    foreach ($pair in @(@($RepoRoot, "<repo>"), @($HOME, "~"), @($env:USERNAME, "<user>"))) {
        if ($pair[0]) { $t = $t.Replace($pair[0], $pair[1]) }
    }
    return $t
}

function Quote-Arg([string]$Value) {
    $v = $Value -replace '"', '\"'
    if ($v.EndsWith("\")) { $v = $v + "\" }
    return '"' + $v + '"'
}

function Invoke-Omni([string[]]$ArgList) {
    $line = (@("-I", (Join-Path $RepoRoot "omniagent.py")) + $ArgList | ForEach-Object { Quote-Arg $_ }) -join " "
    $o = [IO.Path]::GetTempFileName(); $e = [IO.Path]::GetTempFileName()
    $env:PYTHONUTF8 = "1"
    try {
        $p = Start-Process -FilePath $script:PythonExe -ArgumentList $line -NoNewWindow -Wait -PassThru `
            -RedirectStandardOutput $o -RedirectStandardError $e
        return [pscustomobject]@{
            Code = $p.ExitCode
            Out  = [IO.File]::ReadAllText($o, [Text.Encoding]::UTF8)
            Err  = [IO.File]::ReadAllText($e, [Text.Encoding]::UTF8)
        }
    }
    finally { Remove-Item -LiteralPath $o, $e -Force -ErrorAction SilentlyContinue }
}

function Test-Reparse($Item) { return [bool]($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) }

function Get-TreeFingerprint([string]$Root) {
    # Bytes, names, sizes, empty directories and modification times: any change alters the value.
    $sb = New-Object System.Text.StringBuilder
    $items = Get-ChildItem -LiteralPath $Root -Recurse -Force | Sort-Object FullName
    # .NET, not Get-FileHash: started from PowerShell 7, Windows PowerShell 5.1 inherits a
    # PSModulePath whose Core-only Utility module hides Get-FileHash (CI run 40813644887).
    $sha = [System.Security.Cryptography.SHA256]::Create()
    foreach ($i in $items) {
        $rel = $i.FullName.Substring($Root.Length).TrimStart("\")
        if ($i.PSIsContainer) { [void]$sb.Append("D|$rel|$($i.LastWriteTimeUtc.Ticks)`n") }
        else {
            $stream = [System.IO.File]::OpenRead($i.FullName)
            try { $h = [BitConverter]::ToString($sha.ComputeHash($stream)).Replace("-", "") }
            finally { $stream.Dispose() }
            [void]$sb.Append("F|$rel|$($i.Length)|$h|$($i.LastWriteTimeUtc.Ticks)`n")
        }
    }
    $hash = $sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($sb.ToString()))
    return ([BitConverter]::ToString($hash)).Replace("-", "").ToLower()
}

Write-Host "OmniAgent-OS beta smoke test  (mode: $Mode)" -ForegroundColor White

# ---- 1. Python
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
    Add-Result "env.python" "FAIL" "python not found on PATH"
    Write-Host "Install Python 3.9+ and retry." -ForegroundColor Red
    exit 2
}
$script:PythonExe = $py.Source
$ver = (& $script:PythonExe -c "import sys;print('%d.%d.%d' % sys.version_info[:3])")
if ([version]$ver -lt [version]"3.9") { Add-Result "env.python" "FAIL" "Python $ver is older than 3.9"; exit 2 }
Add-Result "env.python" "PASS" "Python $ver"

# ---- 2. Repository layout
$required = @("omniagent.py", "path_adapter.py", "tests", "docs\BETA.md", "docs\USAGE.md")
$missing = @($required | Where-Object { -not (Test-Path -LiteralPath (Join-Path $RepoRoot $_)) })
if ($missing.Count -gt 0) { Add-Result "repo.layout" "FAIL" ("missing: " + ($missing -join ", ")) }
else { Add-Result "repo.layout" "PASS" "required files present" }

if ($RunTests) {
    $env:PYTHONUTF8 = "1"
    $test = Start-Process -FilePath $script:PythonExe -ArgumentList @("-I", "-m", "unittest", "discover", "-s", (Quote-Arg (Join-Path $RepoRoot "tests"))) `
        -NoNewWindow -Wait -PassThru -RedirectStandardError ([IO.Path]::GetTempFileName()) -RedirectStandardOutput ([IO.Path]::GetTempFileName())
    if ($test.ExitCode -eq 0) { Add-Result "repo.unit_tests" "PASS" "unittest discover exit 0" }
    else { Add-Result "repo.unit_tests" "FAIL" "unittest discover exit $($test.ExitCode)" }
}
else { Add-Result "repo.unit_tests" "SKIP" "pass -RunTests to run the unit tests (about 25 s)" }

# ---- 3. Target and source layout
$targetOk = Test-Path -LiteralPath $Target -PathType Container
if (-not $targetOk) { Add-Result "target.exists" "FAIL" "target folder not found: $(Redact $Target)" }
elseif (Test-Reparse (Get-Item -LiteralPath $Target -Force)) { Add-Result "target.exists" "FAIL" "target root is a junction or symlink" }
else { Add-Result "target.exists" "PASS" "$(Redact $Target)" }

$sourceOk = $false
if ($Source -and (Test-Path -LiteralPath $Source -PathType Container)) {
    $Source = (Resolve-Path -LiteralPath $Source).Path
    $links = @(Get-ChildItem -LiteralPath $Source -Force | Where-Object { Test-Reparse $_ })
    if ($links.Count -gt 0) { Add-Result "source.layout" "FAIL" "junction or symlink at source root: $($links[0].Name)" }
    else { $sourceOk = $true; Add-Result "source.layout" "PASS" "$(@(Get-ChildItem -LiteralPath $Source -Directory).Count) folders" }
}
else { Add-Result "source.layout" "SKIP" "pass -Source (templated SSOT) to check hashes; not assumed to exist" }

$manifestOk = $false
if ($Manifest -and (Test-Path -LiteralPath $Manifest -PathType Leaf)) {
    try {
        $m = Get-Content -LiteralPath $Manifest -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($m.version -eq 1 -and $m.targets) { $manifestOk = $true; Add-Result "manifest.valid" "PASS" "version 1, targets: $(($m.targets.PSObject.Properties.Name) -join ', ')" }
        else { Add-Result "manifest.valid" "FAIL" "manifest needs version 1 and a targets object" }
    }
    catch { Add-Result "manifest.valid" "FAIL" "manifest is not valid JSON" }
}
else { Add-Result "manifest.valid" "SKIP" "pass -Manifest (adapters.json) to check hashes; not assumed to exist" }

# ---- 4. Per-skill checks on the installed copies (read-only)
$installedDirs = @{}
foreach ($name in $Skills) {
    $dir = Join-Path $Target $name
    if (-not (Test-Path -LiteralPath $dir -PathType Container)) { Add-Result "skill.$name.present" "FAIL" "not installed in target"; continue }
    $installedDirs[$name] = $dir
    $reparse = @(Get-ChildItem -LiteralPath $dir -Recurse -Force | Where-Object { Test-Reparse $_ })
    if ((Test-Reparse (Get-Item -LiteralPath $dir -Force)) -or $reparse.Count -gt 0) { Add-Result "skill.$name.links" "FAIL" "junction or symlink inside the skill" }
    else { Add-Result "skill.$name.links" "PASS" "no junction or symlink" }

    $md = Join-Path $dir "SKILL.md"
    if (-not (Test-Path -LiteralPath $md -PathType Leaf)) { Add-Result "skill.$name.frontmatter" "FAIL" "SKILL.md missing"; continue }
    $text = [IO.File]::ReadAllText($md, [Text.Encoding]::UTF8)
    $fm = [regex]::Match($text, "(?s)\A---\r?\n(.*?)\r?\n---")
    $hasName = $fm.Success -and ($fm.Groups[1].Value -match "(?m)^name:\s*\S")
    $hasDesc = $fm.Success -and ($fm.Groups[1].Value -match "(?m)^description:\s*\S")
    if ($hasName -and $hasDesc) { Add-Result "skill.$name.frontmatter" "PASS" "name and description present" }
    else { Add-Result "skill.$name.frontmatter" "FAIL" "frontmatter needs name and description" }

    $left = [regex]::Matches($text, "\{\{[A-Z_]+\}\}")
    if ($left.Count -gt 0) { Add-Result "skill.$name.placeholders" "FAIL" "$($left.Count) unrendered placeholder(s) in the installed SKILL.md" }
    else { Add-Result "skill.$name.placeholders" "PASS" "none left" }

    # Files the skill refers to, and other skills it depends on.
    $deps = @([regex]::Matches($text, "skills/([A-Za-z0-9][A-Za-z0-9._-]*)/") | ForEach-Object { $_.Groups[1].Value } |
        Where-Object { $_ -ne $name } | Sort-Object -Unique)
    $depProblems = @(); $depNotes = @()
    foreach ($d in $deps) { if (-not (Test-Path -LiteralPath (Join-Path $Target $d) -PathType Container)) { $depProblems += "skill '$d' is referenced but not installed" } }
    $refs = @([regex]::Matches($text, "(?:scripts|references|assets)/[\w./-]+\.\w+") | ForEach-Object { $_.Value } | Sort-Object -Unique)
    foreach ($r in $refs) {
        $rel = $r -replace "/", "\"
        if (Test-Path -LiteralPath (Join-Path $dir $rel)) { continue }
        $found = $false
        foreach ($d in $deps) { if (Test-Path -LiteralPath (Join-Path (Join-Path $Target $d) $rel)) { $found = $true; $depNotes += "$r is provided by skill '$d'" } }
        if (-not $found) { $depProblems += "referenced file not found: $r" }
    }
    if ($depProblems.Count -gt 0) { Add-Result "skill.$name.dependencies" "FAIL" ($depProblems -join "; ") }
    elseif ($depNotes.Count -gt 0) { Add-Result "skill.$name.dependencies" "PASS" (($depNotes | Sort-Object -Unique) -join "; ") }
    else { Add-Result "skill.$name.dependencies" "PASS" "$($refs.Count) referenced file(s) present" }

    if ([regex]::IsMatch($text, "(?m)^\s*node\s") -and -not (Get-Command node -ErrorAction SilentlyContinue)) {
        Add-Result "skill.$name.runtime" "WARN" "the skill runs node, which is not on PATH"
    }
}

# ---- 5. Hashes: source, render, installed
$planned = @{}
if ($sourceOk -and $manifestOk -and $targetOk) {
    # A fresh, never-created path: plan only reads state, so no stale state from an earlier
    # run can be picked up and nothing is left behind.
    $readOnlyState = Join-Path $env:TEMP "omniagent-smoke-readonly-$RunId"
    $argsPlan = @("plan", "--source", $Source, "--state-dir", $readOnlyState,
        "--target", "$AgentName=$Target", "--adapter-manifest", $Manifest, "--json")
    foreach ($s in $Skills) { $argsPlan += @("--skill", $s) }
    $r = Invoke-Omni $argsPlan
    if ($r.Code -ne 0) { Add-Result "hash.plan" "FAIL" ("plan failed: " + (Redact ($r.Err.Trim() -split "`n" | Select-Object -Last 1))) }
    else {
        foreach ($row in ($r.Out | ConvertFrom-Json)) { $planned[$row.name] = $row }
        foreach ($name in $Skills) {
            $row = $planned[$name]
            if (-not $row) { Add-Result "skill.$name.hashes" "FAIL" "skill not found in the source"; continue }
            $short = { param($h) if ($h) { $h.Substring(0, 12) } else { "-" } }
            $detail = "source=$(& $short $row.source_hash) render=$(& $short $row.render_hash) installed=$(& $short $row.installed_hash) status=$($row.status)"
            # The state path is fresh, so every installed skill is unmanaged here; only `blocked`
            # (a link or a file instead of a folder) can still appear. `conflict*` needs a record
            # and is kept only as a guard. (`unsafe`/`mismatch` are adopt statuses, never plan.)
            if ($row.status -eq "blocked" -or $row.status -like "conflict*") { Add-Result "skill.$name.hashes" "FAIL" $detail }
            elseif ($row.render_matches_installed) { Add-Result "skill.$name.hashes" "PASS" $detail }
            else { Add-Result "skill.$name.hashes" "FAIL" "render and installed hashes differ. $detail" }
        }
    }
}
else { Add-Result "hash.plan" "SKIP" "needs -Source, -Manifest and an existing target" }

# ---- 6. Trial: adopt into a fresh state folder, plan, roll back, prove nothing changed
if ($Mode -eq "Trial") {
    if (-not ($sourceOk -and $manifestOk -and $targetOk) -or $installedDirs.Count -ne $Skills.Count) {
        Add-Result "trial.run" "FAIL" "Trial mode needs -Source, -Manifest and every pilot skill installed"
    }
    else {
        $state = Join-Path $StateRoot $RunId
        if (Test-Path -LiteralPath $state) { throw "Trial state folder already exists: $state" }
        $before = @{}; foreach ($n in $Skills) { $before[$n] = Get-TreeFingerprint $installedDirs[$n] }
        $common = @("--source", $Source, "--state-dir", $state, "--target", "$AgentName=$Target", "--adapter-manifest", $Manifest)
        foreach ($s in $Skills) { $common += @("--skill", $s) }
        $adopt = Invoke-Omni (@("adopt") + $common + @("--yes"))
        $txn = [regex]::Match($adopt.Out, "Adoption transaction: (\S+)").Groups[1].Value
        if ($adopt.Code -ne 0 -or -not $txn) { Add-Result "trial.adopt" "FAIL" "adopt --yes did not record a transaction" }
        else {
            Add-Result "trial.adopt" "PASS" "recorded in a new state folder (state only)"
            $planAfter = Invoke-Omni (@("plan") + $common + @("--json"))
            $unchanged = @(($planAfter.Out | ConvertFrom-Json) | Where-Object { $_.status -eq "unchanged" }).Count
            if ($unchanged -eq $Skills.Count) { Add-Result "trial.plan_after_adopt" "PASS" "all $unchanged skills report unchanged" }
            else { Add-Result "trial.plan_after_adopt" "FAIL" "$unchanged of $($Skills.Count) skills report unchanged" }
            $rb = Invoke-Omni @("rollback", "--state-dir", $state, "--transaction", $txn, "--yes")
            if ($rb.Code -ne 0) { Add-Result "trial.rollback" "FAIL" "rollback failed" }
            else {
                $managed = (Get-Content -LiteralPath (Join-Path $state "state.json") -Raw -Encoding UTF8 | ConvertFrom-Json).managed
                if (@($managed.PSObject.Properties).Count -eq 0) { Add-Result "trial.rollback" "PASS" "adoption undone, state is empty again" }
                else { Add-Result "trial.rollback" "FAIL" "state still lists managed skills" }
            }
        }
        foreach ($n in $Skills) {
            if ((Get-TreeFingerprint $installedDirs[$n]) -eq $before[$n]) { Add-Result "trial.$n.bytes" "PASS" "bytes, sizes, empty folders and times identical" }
            else { Add-Result "trial.$n.bytes" "FAIL" "the skill folder changed during the trial" }
        }
    }
}

# ---- 7. What a script cannot prove
foreach ($n in $Skills) {
    Add-Result "agent.$n.loaded" "NOT VERIFIED" "open Codex/Claude and confirm it loads '$n' (docs\BETA.md checklist)"
    Add-Result "agent.$n.task" "NOT VERIFIED" "confirm '$n' completes its task in the agent"
}

# ---- 8. Report (personal paths redacted)
$fail = @($Results | Where-Object { $_.status -eq "FAIL" }).Count
$skip = @($Results | Where-Object { $_.status -eq "SKIP" }).Count
$pass = @($Results | Where-Object { $_.status -eq "PASS" }).Count
$verdict = if ($fail -gt 0) { "FAIL" } elseif ($skip -gt 0) { "PASS (partial: $skip check(s) skipped)" } else { "PASS" }
New-Item -ItemType Directory -Force -Path $ReportDir | Out-Null
$base = Join-Path $ReportDir "smoke_beta_$Stamp"
$report = [ordered]@{
    generated_utc = $Stamp; mode = $Mode; verdict = $verdict; passed = $pass; failed = $fail; skipped = $skip
    skills = $Skills
    results = @($Results | ForEach-Object { [ordered]@{ id = $_.id; status = $_.status; detail = (Redact $_.detail) } })
}
($report | ConvertTo-Json -Depth 5) | Set-Content -LiteralPath "$base.json" -Encoding UTF8
Write-Host ""
Write-Host "Verdict: $verdict   (passed $pass, failed $fail, skipped $skip)" -ForegroundColor White
Write-Host "Agent loading and task completion are NOT VERIFIED by this script." -ForegroundColor Cyan
Write-Host "Report: $(Redact "$base.json")"
if ($fail -gt 0) { exit 1 }
exit 0
