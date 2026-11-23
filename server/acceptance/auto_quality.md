# CareerPilot Auto offline quality

From this repository root, run the scorer with the existing CareerPilot server environment.

```powershell
$python = 'D:/agent-interview/server/.venv/Scripts/python.exe'
$reportDir = Join-Path ([System.IO.Path]::GetTempPath()) 'careerpilot-auto-quality'
New-Item -ItemType Directory -Force -Path $reportDir | Out-Null
& $python server/acceptance/auto_quality.py --baseline 70660b2 --output (Join-Path $reportDir 'baseline.json')
& $python server/acceptance/auto_quality.py --output (Join-Path $reportDir 'candidate.json')
```

The baseline archives the exact dev commit. The candidate snapshots the current `server/app` working tree. Both use `auto_quality_v1.json` beside the runner. A run returns nonzero if a case errors or the fixture digest differs from the frozen value. The JSON records the source and runner digests, aggregate metrics, strata, and per-case results.

The expected fixture SHA-256 is `e55a67f83a1b620bff605762b63a3ab5fc7ddfbc43ce26fcac99c6e95f92340d`. Keep the adjacent `.gitattributes` with the fixture. Its `-text` rule turns off Git line-ending conversion for that path, including checkouts with `core.autocrlf=true`. Check the bytes with `Get-FileHash server/acceptance/auto_quality_v1.json`. Verify the attribute with `git check-attr text -- server/acceptance/auto_quality_v1.json` and check for `unset`.

The runner uses synthetic rows and repositories. It evaluates initial model tier and evidence retrieval with no PostgreSQL service, model credentials, or provider calls. Keep the frozen labels separate from implementation tuning.
