#requires -Version 7.0
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$RunDir,
    [string]$RepoRoot = "$PSScriptRoot/../..",
    [string]$ChangePatch,
    [string]$BaseCommit,
    [string]$SharedWebRoot
)

$ErrorActionPreference = 'Stop'
$invocationRoot = [IO.Path]::GetFullPath("$PSScriptRoot/../..")
$utf8 = [Text.UTF8Encoding]::new($false)
$timer = [Diagnostics.Stopwatch]::StartNew()

function Write-Json($Path, $Value) {
    [IO.File]::WriteAllText($Path, (ConvertTo-Json -InputObject $Value -Depth 12) + "`n", $utf8)
}

function Invoke-Program($Executable, [string[]]$Arguments, $Directory, $Environment = @{}, $Timeout = 120, [switch]$HashOutput) {
    $start = [Diagnostics.ProcessStartInfo]::new($Executable)
    $start.UseShellExecute = $false
    $start.WorkingDirectory = $Directory
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.StandardOutputEncoding = $utf8
    $start.StandardErrorEncoding = $utf8
    $start.CreateNoWindow = $true
    foreach ($argument in $Arguments) { $start.ArgumentList.Add($argument) }
    foreach ($name in $Environment.Keys) {
        if ($null -eq $Environment[$name]) { [void]$start.Environment.Remove($name) }
        else { $start.Environment[$name] = $Environment[$name] }
    }
    $child = [Diagnostics.Process]::Start($start)
    $hasher = $null
    $hashStream = $null
    try {
        if ($HashOutput) {
            $hasher = [Security.Cryptography.SHA256]::Create()
            $hashStream = [Security.Cryptography.CryptoStream]::new([IO.Stream]::Null, $hasher, 'Write')
            $stdout = $child.StandardOutput.BaseStream.CopyToAsync($hashStream)
        } else {
            $stdout = $child.StandardOutput.ReadToEndAsync()
        }
        $stderr = $child.StandardError.ReadToEndAsync()
        $timedOut = -not $child.WaitForExit($Timeout * 1000)
        if ($timedOut -and -not $child.HasExited) { $child.Kill($true) }
        $child.WaitForExit()
        $output = $stdout.GetAwaiter().GetResult()
        if ($HashOutput) {
            $hashStream.FlushFinalBlock()
            $output = [BitConverter]::ToString($hasher.Hash).Replace('-', '').ToLowerInvariant()
        }
        return @{
            code = $child.ExitCode
            stdout = $output
            stderr = $stderr.GetAwaiter().GetResult()
            timed_out = $timedOut
        }
    } finally {
        if ($hashStream) { $hashStream.Dispose() }
        if ($hasher) { $hasher.Dispose() }
        $child.Dispose()
    }
}

function Read-Git([string[]]$Arguments, [switch]$HashOutput) {
    $result = Invoke-Program $git (@('--no-optional-locks', '-c', 'core.fsmonitor=false') + $Arguments) $root @{
        GIT_INDEX_FILE = $null; GIT_DIR = $null; GIT_WORK_TREE = $null; GIT_COMMON_DIR = $null
    } 30 -HashOutput:$HashOutput
    if ($result.timed_out -or $result.code -ne 0) { throw "Git $($Arguments[0]) failed: $($result.stderr.Trim())" }
    return $result.stdout
}

function Get-WorkingPatchHash($Base, [switch]$Strict) {
    if ($Strict -and (Read-Git @('ls-files', '--others', '--exclude-standard', '-z'))) {
        throw 'Strict patch requires every non-ignored new file to be in the index. Run the nightly checkpoint first.'
    }
    return Read-Git @('diff', '--no-ext-diff', '--binary', $Base) -HashOutput
}

function Get-Inputs {
    $commit = (Read-Git @('rev-parse', '--verify', 'HEAD')).Trim()
    $paths = @(
        'README.md', 'scripts/agent/verify*',
        'server/app', 'server/tests', 'server/requirements.txt', 'server/requirements-dev.txt',
        'web/src', 'web/tests', 'web/package.json', 'web/package-lock.json'
    )
    $headPaths = Read-Git (@('ls-tree', '-r', '--name-only', '-z', $commit, '--') + $paths)
    # ls-tree matches literal paths; the wildcard below needs Git pathspec handling.
    $indexPaths = Read-Git (@('ls-files', '--cached', '--others', '--exclude-standard', '-z', '--') + $paths)
    $headScripts = Read-Git @('ls-tree', '-r', '--name-only', '-z', $commit, '--', 'scripts/agent')
    $names = [Collections.Generic.SortedSet[string]]::new([StringComparer]::Ordinal)
    foreach ($name in (($headPaths + $indexPaths + $headScripts) -split "`0")) {
        if (!$name -or ($name.StartsWith('scripts/agent/') -and $name -notlike 'scripts/agent/verify*')) { continue }
        [void]$names.Add($name)
    }
    $files = @(foreach ($name in $names) {
        $path = Join-Path $root $name
        $digest = if ([IO.File]::Exists($path)) { (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() }
        [ordered]@{ path = $name; sha256 = $digest }
    })
    $scopedStatus = Read-Git (@('status', '--porcelain=v1', '-z', '--untracked-files=all', '--no-renames', '--') + $paths)
    return [ordered]@{
        checked_commit = $commit
        verifier_sha256 = (Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash.ToLowerInvariant()
        scoped_status = @($scopedStatus -split "`0" | Where-Object { $_ })
        files = $files
    }
}

function Save-Evidence($Name, $Result) {
    $content = $Result.stdout
    if ($Result.stderr) { $content += "`n--- stderr ---`n" + $Result.stderr }
    [IO.File]::WriteAllText((Join-Path $attemptPath $Name), $content, $utf8)
}

function Read-ChangePatch($Path) {
    $file = Get-Item -LiteralPath $Path
    if ($file -isnot [IO.FileInfo] -or ($file.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $file.Length -gt 1MB) {
        throw 'ChangePatch must be a regular patch file no larger than 1 MiB.'
    }
    $bytes = [IO.File]::ReadAllBytes($file.FullName)
    $content = [Text.UTF8Encoding]::new($false, $true).GetString($bytes)
    if ($bytes.Length -gt 1MB -or $content.Contains("`0") -or $content -notmatch '(?m)^(diff --git |--- [^\r\n]+\r?\n\+\+\+ )') {
        throw 'ChangePatch must contain a UTF-8 Git or unified diff no larger than 1 MiB.'
    }
    return ,$bytes
}

$caseNames = @(
    'test_frozen_question_rubric_and_answer_control_scoring'
    'test_open_question_fails_closed_without_model_and_warms_configuration'
    'test_one_zero_score_is_visible_observation_but_domain_stays_unknown'
    'test_repeated_canonical_question_does_not_inflate_coverage_or_confidence'
    'test_legacy_claim_import_project_are_not_assessed_mastery'
    'test_failed_question_breakdown_does_not_invent_zero_score'
)
$mappingCases = @(
    'mapping review opens without loading the answer and requires an explicit approval'
    'revoke an exact historical mapping when the current rubric cannot be reviewed'
)
$checks = @(
    [ordered]@{ name = 'scoring-and-evidence'; suite = 'server/tests/test_core_scoring.py'; runtime = 'python'; cases = $caseNames }
    [ordered]@{ name = 'exam-submission-and-offline-data'; suite = 'web/tests/core-loop.test.mjs'; runtime = 'node'; required_cases = @(
        'Dashboard never substitutes demo evidence while real data is unavailable'
        'Profile never substitutes demo evidence while real data is unavailable'
        'ProfileDetail never substitutes demo evidence while real data is unavailable'
        'ExamCenter never substitutes demo evidence while real data is unavailable'
        'DiagnosisReport never substitutes demo evidence while real data is unavailable'
        'Learning never substitutes demo evidence while real data is unavailable'
        'LearningTask never substitutes demo evidence while real data is unavailable'
        'API requests default to the personal workspace'
        'workspace export shares the selected workspace with JSON requests'
        'unknown values remain unknown instead of a numeric zero'
        'heuristic confidence describes observation coverage rather than scoring reliability'
        'failed atomic submission retains the callers final answers'
        'all final answers and revisions are submitted atomically using their own question type'
        'loading an explicit exam never creates or substitutes another exam'
        'concurrent creation uses the same persisted idempotency key'
    ) }
    [ordered]@{ name = 'scoring-state-presentation'; suite = 'web/tests/scoring-state-presentation.test.mjs'; runtime = 'node'; required_cases = @(
        'QUEUED is a current scoring state, not missing historical evidence or an evaluation'
        'RUNNING is a current scoring state, not missing historical evidence or an evaluation'
        'RETRY_WAIT is a current scoring state, not missing historical evidence or an evaluation'
        'FAILED is a current scoring state, not missing historical evidence or an evaluation'
        'NEEDS_REVIEW is a current scoring state, not missing historical evidence or an evaluation'
        'a successful zero-score question keeps its real feedback while another question waits'
        'a succeeded status without a valid numeric result cannot display evaluation content'
        'a genuinely completed historical result retains its historical evidence limitation'
        'partial criterion credit shows saved positive evidence without calling it a passing anchor or rounding the total'
    ) }
    [ordered]@{ name = 'reference-exposure-and-retest-review'; suite = 'web/tests/learning-bank-exposure.test.mjs'; runtime = 'node'; required_cases = @(
        'a failed reference exposure write cannot reveal a bundled answer'
        'starting a question with its server numeric id never fetches reference material'
        'a successful reference read explains its effect on independent verification'
    ) }
)
foreach ($check in $checks) {
    $check.status = 'blocked'
    $check.reason = 'Check has not run.'
    $check.exit_code = $null
    $check.evidence = @()
    $check.covered_case_count = 0
}

try {
    $runPath = [IO.Path]::TrimEndingDirectorySeparator($ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($RunDir))
    $attempt = "attempt-$([DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ'))-$([guid]::NewGuid().ToString('N').Substring(0, 12))"
    $attemptPath = Join-Path $runPath $attempt
    [void][IO.Directory]::CreateDirectory($attemptPath)
} catch {
    [Console]::Error.WriteLine("Cannot create evidence directory: $($_.Exception.Message)")
    exit 2
}

$report = [ordered]@{
    schema_version = 1
    status = 'blocked'
    workspace = $null
    checked_commit = $null
    dirty = $null
    dirty_inputs_fingerprint = $null
    inputs = $null
    attempt = $attempt
    checks = $checks
    change_patch = $null
    patch_mode = $null
    patch_base = $null
    checked_patch_sha256 = $null
    checked_patch_after_sha256 = $null
    coverage_scope = $null
    required_case_count = $null
    covered_case_count = 0
    limitations = @(
        'The fingerprint covers the invoked verifier and scoped Git working files, including non-ignored untracked sources. It excludes ignored local env files, installed dependencies and run outputs.'
        'Checks run on live working files. The verifier compares commit, status and file bytes before and after testing.'
        'Backend cases use in-memory fixtures. Frontend cases render components and mock transport. PostgreSQL, real providers, browser flows and the full suites are outside this run.'
    )
}

try {
    $root = [IO.Path]::TrimEndingDirectorySeparator($ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($RepoRoot))
    if (![IO.Directory]::Exists($root)) { throw "Worktree directory does not exist: $root" }
    $git = (Get-Command git -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1).Source
    if (!$git) { throw 'Git is required to identify the checked inputs.' }
    $gitRoot = [IO.Path]::GetFullPath((Read-Git @('rev-parse', '--show-toplevel')).TrimEnd("`r", "`n"))
    if (![StringComparer]::OrdinalIgnoreCase.Equals($root, $gitRoot)) {
        throw "RepoRoot must name a Git worktree root. Git reports: $gitRoot"
    }
    $root = $gitRoot
    $report.workspace = $root
    if ($BaseCommit -and !$ChangePatch) { throw 'BaseCommit requires ChangePatch.' }
    if ($BaseCommit -and $BaseCommit -notmatch '\A(?:[0-9a-f]{40}|[0-9a-f]{64})\z') {
        throw 'BaseCommit must be a full Git commit SHA.'
    }
    $before = Get-Inputs
    $report.checked_commit = $before.checked_commit
    $report.dirty = $before.scoped_status.Count -gt 0
    $report.inputs = 'inputs.json'
    $inputPath = Join-Path $attemptPath $report.inputs
    Write-Json $inputPath $before
    $report.dirty_inputs_fingerprint = (Get-FileHash -LiteralPath $inputPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($ChangePatch) {
        $patchPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($ChangePatch)
        if (![StringComparer]::OrdinalIgnoreCase.Equals([IO.Path]::GetDirectoryName($patchPath), $runPath) -or $patchPath -notmatch '\.(diff|patch)$') {
            throw 'ChangePatch must be a .patch or .diff file directly inside RunDir.'
        }
        $copy = Join-Path $attemptPath 'candidate.patch'
        [IO.File]::WriteAllBytes($copy, (Read-ChangePatch $patchPath))
        $report.change_patch = [ordered]@{
            source = [IO.Path]::GetFileName($patchPath)
            evidence = 'candidate.patch'
            sha256 = (Get-FileHash -LiteralPath $copy -Algorithm SHA256).Hash.ToLowerInvariant()
        }
        $report.patch_mode = if ($BaseCommit) { 'strict' } else { 'attachment' }
        $report.patch_base = if ($BaseCommit) {
            (Read-Git @('rev-parse', '--verify', '--end-of-options', "$BaseCommit^{commit}")).Trim()
        } else {
            $before.checked_commit
        }
        $report.checked_patch_sha256 = Get-WorkingPatchHash $report.patch_base -Strict:([bool]$BaseCommit)
        if ($BaseCommit -and $report.checked_patch_sha256 -cne $report.change_patch.sha256) {
            throw 'The supplied change patch does not match the working Git diff against BaseCommit.'
        }
    }

    $coverageBase = if ($BaseCommit) { $report.patch_base } else { $before.checked_commit }
    $mappingFeature = 'web/src/components/RetestMappingReview.tsx'
    $mappingCheck = $checks | Where-Object { $_.name -eq 'reference-exposure-and-retest-review' }
    $baselinePaths = (Read-Git @('ls-tree', '-r', '--name-only', '-z', $coverageBase, '--', $mappingFeature, $mappingCheck.suite)) -split "`0"
    $mappingRequired = [IO.File]::Exists((Join-Path $root $mappingFeature)) -or $mappingFeature -cin $baselinePaths
    if (!$mappingRequired -and $mappingCheck.suite -cin $baselinePaths) {
        $baselineSuite = Read-Git @('show', "${coverageBase}:$($mappingCheck.suite)")
        $mappingRequired = @($mappingCases | Where-Object {
            $baselineSuite -cmatch ('(?m)^[ \t]*(?:test|it)(?:\.\w+)?\(\s*([''"\x60]){0}\1' -f [regex]::Escape($_))
        }).Count -gt 0
    }
    $report.coverage_scope = if ($mappingRequired) { 'shared-and-retest-mapping-review' } else { 'shared' }
    if ($mappingRequired) { $mappingCheck.required_cases += $mappingCases }
    $report.required_case_count = $caseNames.Count + ($checks.required_cases | Measure-Object).Count

    $python = Join-Path $root 'server/.venv/Scripts/python.exe'
    if (!(Test-Path -LiteralPath $python -PathType Leaf)) {
        $python = Join-Path $invocationRoot 'server/.venv/Scripts/python.exe'
    }
    if (!(Test-Path -LiteralPath $python -PathType Leaf)) {
        $python = (Get-Command python -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1).Source
    }
    $node = (Get-Command node -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1).Source
    $web = Join-Path $root 'web'
    $localEnv = @{ TEMP = $attemptPath; TMP = $attemptPath; TMPDIR = $attemptPath }
    $pythonEnv = $localEnv + @{
        PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'; PYTEST_ADDOPTS = $null; PYTEST_PLUGINS = $null
        CAREERPILOT_TEST_DATABASE_URL = 'postgresql+asyncpg://pilot:pilot@127.0.0.1:1/careerpilot_pilot_test'
        LLM_PROVIDER = ''; LLM_API_KEY = ''; GITHUB_TOKEN = ''; GITCODE_TOKEN = ''
    }
    $nodeEnv = $localEnv + @{ NODE_OPTIONS = $null; NODE_PATH = $null; NODE_V8_COVERAGE = $null; FORCE_COLOR = $null }
    $nodeEnv.CAREERPILOT_VERIFY_WEB = $web
    $nodeEnv.CAREERPILOT_VERIFY_SHARED_WEB = if ($SharedWebRoot) {
        $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($SharedWebRoot)
    } else { $null }
    $nodeGuard = Join-Path $attemptPath 'node-dependencies.mjs'
    [IO.File]::WriteAllText($nodeGuard, @'
import { readFileSync, realpathSync } from 'node:fs';
import Module, { isBuiltin, register } from 'node:module';
import { isAbsolute, join, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { isMainThread } from 'node:worker_threads';

const web = realpathSync(process.env.CAREERPILOT_VERIFY_WEB);
const shared = process.env.CAREERPILOT_VERIFY_SHARED_WEB;
const modulePath = join(web, 'node_modules');
const modules = realpathSync(modulePath);
const lock = readFileSync(join(web, 'package-lock.json'));
if (shared && !lock.equals(readFileSync(join(shared, 'package-lock.json')))) {
    throw new Error('SharedWebRoot must have a byte-identical package-lock.json.');
}
const allowed = shared ? realpathSync(join(shared, 'node_modules')) : modulePath;
if (relative(allowed, modules) !== '') {
    throw new Error('web/node_modules must be local or point to the explicitly supplied SharedWebRoot install.');
}
const expected = JSON.parse(lock).packages;
const installed = JSON.parse(readFileSync(join(modules, '.package-lock.json'))).packages;
const checked = new Set();

function checkResolution(specifier, filename) {
    if (isBuiltin(specifier) || specifier.startsWith('.') || isAbsolute(specifier) ||
        /^[a-z][a-z0-9+.-]*:/i.test(specifier)) return;
    const path = relative(modules, realpathSync(filename));
    if (isAbsolute(path) || path.startsWith('..' + sep) || path === '..') {
        throw new Error(`Dependency ${specifier} resolved outside ${modules}: ${filename}`);
    }
    const parts = ['node_modules', ...path.split(sep)];
    const packageStart = parts.lastIndexOf('node_modules') + 1;
    const packageEnd = packageStart + (parts[packageStart].startsWith('@') ? 2 : 1);
    const key = parts.slice(0, packageEnd).join('/');
    if (checked.has(key)) return;
    const pinned = expected[key];
    const entry = installed[key];
    const manifest = join(modules, ...parts.slice(1, packageEnd), 'package.json');
    if (!pinned || !entry || pinned.version !== entry.version || pinned.integrity !== entry.integrity ||
        pinned.version !== JSON.parse(readFileSync(manifest)).version) {
        throw new Error(`Dependency ${key} does not match the candidate package-lock.json.`);
    }
    checked.add(key);
}

export async function resolve(specifier, context, nextResolve) {
    const result = await nextResolve(specifier, context);
    if (result.url.startsWith('file:')) checkResolution(specifier, fileURLToPath(result.url));
    return result;
}

if (isMainThread) {
    const resolveFilename = Module._resolveFilename;
    Module._resolveFilename = function (specifier, ...args) {
        const filename = resolveFilename.call(this, specifier, ...args);
        checkResolution(specifier, filename);
        return filename;
    };
    register(import.meta.url);
}
'@, $utf8)
    $nodeArgs = @('--no-global-search-paths', '--import', ([Uri]::new($nodeGuard).AbsoluteUri))
    $pytestConfig = Join-Path $attemptPath 'pytest.ini'
    [IO.File]::WriteAllText($pytestConfig, "[pytest]`nasyncio_default_fixture_loop_scope = function`n", $utf8)
    $runtime = @{
        python = @{
            executable = $python; cwd = $attemptPath; env = $pythonEnv
            probe = @('-I', '-B', '-X', 'utf8', '-c', 'import sys; print(sys.version); assert sys.version_info >= (3, 12); import pytest, pytest_asyncio, asyncpg, fastapi, sqlalchemy, pydantic_settings, httpx')
            requirement = 'Python 3.12+ with the server and pytest dependencies'
        }
        node = @{
            executable = $node; cwd = $web; env = $nodeEnv
            probe = $nodeArgs + @('-e', 'console.log(process.version); for (const name of ["typescript","react","react-dom/server","react-router-dom/server.js","react-test-renderer"]) console.log(require.resolve(name));')
            requirement = 'Node.js 20.6+ and a lock-matched npm installation in web/node_modules'
        }
    }
    foreach ($name in @('python', 'node')) {
        $tool = $runtime[$name]
        if (!$tool.executable) {
            $tool.blocker = "Required runtime unavailable: $($tool.requirement)."
            continue
        }
        $tool.log = "$name-preflight.log"
        try {
            $probe = Invoke-Program $tool.executable $tool.probe $tool.cwd $tool.env 30
            Save-Evidence $tool.log $probe
            if ($probe.timed_out -or $probe.code -ne 0) { $tool.blocker = "Preflight failed: $($tool.requirement)." }
        } catch {
            $tool.blocker = "Cannot start $name preflight: $($_.Exception.Message)"
            [IO.File]::WriteAllText((Join-Path $attemptPath $tool.log), $tool.blocker, $utf8)
        }
    }

    foreach ($check in $checks) {
        $tool = $runtime[$check.runtime]
        if ($tool.log) { $check.evidence += $tool.log }
        if ($tool.blocker) {
            $check.reason = $tool.blocker
            continue
        }
        $log = "$($check.name).log"
        if ($check.runtime -eq 'python') {
            $xml = Join-Path $attemptPath 'scoring-and-evidence.xml'
            $testPath = Join-Path $root $check.suite
            $arguments = @('-I', '-B', '-X', 'utf8', '-m', 'pytest', '-c', $pytestConfig,
                '-p', 'pytest_asyncio.plugin', '-p', 'no:cacheprovider', '-vv', '--tb=short',
                '--basetemp', (Join-Path $attemptPath 'pytest-tmp'), '--junitxml', $xml)
            $arguments += @($check.cases | ForEach-Object { "${testPath}::$_" })
        } else {
            $arguments = $nodeArgs + @('--import', './tests/register.mjs', '--test', '--test-reporter', 'tap', "./$($check.suite.Substring(4))")
        }
        $check.working_directory = $tool.cwd
        $check.argv = @($tool.executable) + $arguments
        try {
            $result = Invoke-Program $tool.executable $arguments $tool.cwd $tool.env
            Save-Evidence $log $result
            $check.evidence += $log
            $check.exit_code = $result.code
            if ($check.runtime -eq 'python') {
                if (Test-Path -LiteralPath $xml -PathType Leaf) {
                    $check.evidence += 'scoring-and-evidence.xml'
                    [xml]$junit = [IO.File]::ReadAllText($xml)
                    $check.tests = $junit.SelectNodes('//testcase').Count
                    $passed = @($junit.SelectNodes('//testcase[not(skipped or failure or error)]') | ForEach-Object { $_.name })
                    $check.covered_case_count = @($check.cases | Where-Object { $_ -cin $passed }).Count
                    $complete = $check.tests -eq $check.cases.Count -and $check.covered_case_count -eq $check.cases.Count
                } else {
                    $complete = $false
                }
            } else {
                $count = [regex]::Match($result.stdout, '(?m)^# tests (\d+)\s*$')
                $check.tests = if ($count.Success) { [int]$count.Groups[1].Value } else { 0 }
                $check.cases = @([regex]::Matches($result.stdout, '(?m)^[ \t]*# Subtest: (.+?)\r?$') | ForEach-Object { $_.Groups[1].Value })
                $passed = @([regex]::Matches($result.stdout, '(?m)^[ \t]*ok \d+ - (.+?)\r?$') | ForEach-Object { $_.Groups[1].Value })
                $check.missing_cases = @($check.required_cases | Where-Object { $_ -cnotin $check.cases })
                $check.covered_case_count = @($check.required_cases | Where-Object { $_ -cin $passed }).Count
                $complete = $check.tests -ge $check.required_cases.Count -and
                    $check.covered_case_count -eq $check.required_cases.Count -and
                    $check.missing_cases.Count -eq 0 -and
                    $result.stdout -match "(?m)^# pass $($check.tests)\s*$" -and
                    $result.stdout -match '(?m)^# (skip|skipped) 0\s*$' -and $result.stdout -match '(?m)^# todo 0\s*$'
            }
            if ($result.timed_out) {
                $check.reason = 'Test process exceeded the 120-second deadline.'
            } elseif ($result.code -ne 0 -or !$complete) {
                $check.status = 'failed'
                $check.reason = if ($check.missing_cases) {
                    "Missing required cases: $($check.missing_cases -join '; ')"
                } else {
                    'Test runner failed or did not run all required cases. See the runner log.'
                }
            } else {
                $check.status = 'passed'
                $check.reason = $null
            }
        } catch {
            $check.reason = $_.Exception.Message
        }
    }
    $report.covered_case_count = ($checks.covered_case_count | Measure-Object -Sum).Sum
    if ($ChangePatch) {
        $report.checked_patch_after_sha256 = Get-WorkingPatchHash $report.patch_base -Strict:([bool]$BaseCommit)
        if ($report.checked_patch_after_sha256 -cne $report.checked_patch_sha256) {
            throw 'The working Git diff changed during verification.'
        }
    }
    $after = Get-Inputs
    if ((ConvertTo-Json -InputObject $before -Depth 8 -Compress) -cne (ConvertTo-Json -InputObject $after -Depth 8 -Compress)) {
        Write-Json (Join-Path $attemptPath 'inputs-after.json') $after
        throw 'Relevant inputs changed during verification. See inputs-after.json and rerun on stable inputs.'
    }
    if ($report.change_patch -and (Get-FileHash -LiteralPath $patchPath -Algorithm SHA256).Hash.ToLowerInvariant() -cne $report.change_patch.sha256) {
        throw 'The supplied change patch changed during verification.'
    }
    $report.status = if ($checks.status -contains 'failed') { 'failed' }
                     elseif ($checks.status -contains 'blocked') { 'blocked' }
                     else { 'passed' }
} catch {
    $report.reason = $_.Exception.Message
}

$report.exit_code = @{ passed = 0; failed = 1; blocked = 2 }[$report.status]
$report.elapsed_ms = $timer.ElapsedMilliseconds
try {
    Write-Json (Join-Path $attemptPath 'verify.json') $report
    $latest = Join-Path $runPath 'verify.json'
    $pending = Join-Path $runPath "$attempt.verify.json"
    [IO.File]::Copy((Join-Path $attemptPath 'verify.json'), $pending)
    [IO.File]::Move($pending, $latest, $true)
    Write-Output "$($report.status): $latest ($attempt)"
} catch {
    [Console]::Error.WriteLine("Cannot publish verification receipt: $($_.Exception.Message)")
    exit 2
}
exit $report.exit_code
