# CareerPilot Auto offline quality

Run both suites from this repository root with the CareerPilot server environment.

```powershell
$python = 'python'  # Run in the activated CareerPilot server environment.
$out = Join-Path ([System.IO.Path]::GetTempPath()) 'careerpilot-auto-quality'
New-Item -ItemType Directory -Force -Path $out | Out-Null
$frozenRouter = (Get-FileHash server/app/services/study_routing.py -Algorithm SHA256).Hash.ToLowerInvariant()
$acceptedRouterBytes = @(
    '487edc2928fa906111595f41f8f21182d650d63904802220b10499a3eb1c9312', # LF
    'ddf3be9076b4c9706528ec6755b257707c40762bb888313565abb29540d6a33f'  # CRLF
)
if ($frozenRouter -notin $acceptedRouterBytes) { throw 'Unexpected study-auto-v2.2 router content' }
foreach ($suite in 'primary', 'confirmation') {
    & $python server/acceptance/auto_quality.py --suite $suite --baseline 70660b2 --output (Join-Path $out "$suite-baseline.json")
    if ($LASTEXITCODE -ne 0) { throw "Baseline failed for $suite" }
    & $python server/acceptance/auto_quality.py --suite $suite --expect-policy-version study-auto-v2.2 --expect-router-sha256 $frozenRouter --output (Join-Path $out "$suite-candidate.json")
    if ($LASTEXITCODE -ne 0) { throw "Candidate failed for $suite" }
}
```

The baseline archives the exact dev commit. The candidate snapshots the current `server/app` working tree. The two candidate checks require the frozen router bytes and the expected policy version on every production routing trace. When evaluating a later snapshot, choose the expected version and router digest for that snapshot. Each JSON records suite identity, source and scorer digests, aggregate metrics, strata, and per-case outcomes.

| Suite | Adjacent fixture | SHA-256 |
| --- | --- | --- |
| Primary | `auto_quality_v1.json` | `e55a67f83a1b620bff605762b63a3ab5fc7ddfbc43ce26fcac99c6e95f92340d` |
| Confirmation | `auto_quality_confirmation_v1.json` | `f3bc87e8135a3711d153beb8d62f55095e1c1e580f42cdcf7a5d9e0c6ac056c9` |

Keep the adjacent `.gitattributes` with both fixtures. Its `-text` rules disable Git line-ending conversion for these paths, including checkouts with `core.autocrlf=true`. Use `Get-FileHash` for the file SHA-256. Use `git check-attr text -- server/acceptance/auto_quality_v1.json server/acceptance/auto_quality_confirmation_v1.json` to verify that both text attributes are unset.

Both suites run offline with synthetic rows and repositories. The primary suite evaluates initial tier and retrieval. The confirmation suite evaluates initial tier on new questions and returns zero retrieval cases. Keep judgement labels and case results out of future policy tuning.
