# CareerPilot local acceptance and preview

Run these commands from the CareerPilot checkout in PowerShell 7.

```powershell
pwsh -NoProfile -File .\scripts\agent\verify.ps1 -RunDir .agent-runs/pilot-local
pwsh -NoProfile -File .\scripts\agent\preview.ps1 -RunDir .agent-runs/pilot-local
```

Open the printed loopback URL. Press Ctrl+C to stop the server. Omit `-Port` to use an available port, or pass a specific local port. Use `-Check` to fetch the page, record the HTTP response, and close the server.

To verify a separate worktree from the original checkout, pass its root explicitly.

```powershell
$run = 'D:\agent-interview\.agent-runs\night-2026-11-19'
pwsh -NoProfile -File D:\agent-interview\scripts\agent\verify.ps1 -RepoRoot (Join-Path $run 'worktree') -RunDir $run -ChangePatch (Join-Path $run 'change.patch') -BaseCommit $baselineSha
```

Relative paths are resolved from the caller's current directory. Keep the run directory outside the verified source directories. The receipt records the absolute Git worktree root under `workspace`.

For nightly verification, set `$baselineSha` to the full commit SHA recorded at delivery start. Pass both arguments after the checkpoint writes `change.patch` and marks new files intent-to-add. Strict mode compares the supplied patch to the exact bytes produced by `git diff --no-ext-diff --binary <base>` before testing. It hashes the working diff again after testing. A mismatch, a changed diff, or an uncheckpointed non-ignored file blocks the run.

For an interactive run on the shared checkout, pass `-ChangePatch` alone to attach a partial patch. Attachment mode hashes the tracked working diff against the starting HEAD before and after the tests. Its digest can differ from the attachment's digest. Both modes accept a UTF-8 `.patch` or `.diff` up to 1 MiB directly inside the run directory and save a copy. The receipt records `patch_mode`, `patch_base`, `checked_patch_sha256` at the start, `checked_patch_after_sha256` at the end, and the attachment's own `change_patch.sha256`. Patch fields are null for runs without a patch.

Verification requires Git, Node.js 20.6+, and Python 3.12+ with `server/requirements.txt` and `server/requirements-dev.txt` installed. Python lookup tries the target worktree's Windows venv, the verifier checkout's Windows venv, then `python` on `PATH`. Dependency setup belongs to the caller.

Install frontend dependencies with npm in the candidate's `web/node_modules`. To reuse an install, link the candidate's `web/node_modules` to the shared install and pass `-SharedWebRoot <directory-containing-the-shared-package-lock.json>`. The two lockfiles must match byte for byte. Node preflight and each suite check that resolved packages come from the chosen install and match the candidate's lock. The verifier requires npm's installed lock metadata at `node_modules/.package-lock.json`.

| Exit | Verification result |
| --- | --- |
| 0 | All selected behavioral checks passed. |
| 1 | A selected suite failed or reported an incomplete run. |
| 2 | Verification was blocked, for example by missing prerequisites, an invalid worktree, or inputs changing during the run. |

`<RunDir>/verify.json` points to the latest completed attempt. Each `attempt-*` directory preserves its receipt and available evidence, including the input manifest, suite logs, and pytest XML. The fingerprint is the SHA-256 of `inputs.json`. The manifest records the HEAD, verifier digest, scoped status, and content hashes for relevant, non-ignored untracked files alongside tracked sources.

All four suites run in both coverage scopes. The `shared` scope requires 33 named cases. The `shared-and-retest-mapping-review` scope adds two cases when the candidate or baseline contains `web/src/components/RetestMappingReview.tsx`, or the baseline declares either mapping-review case. The baseline is `-BaseCommit` in strict mode and the starting HEAD otherwise. The receipt records `coverage_scope`, `required_case_count`, and `covered_case_count`. It lists observed and missing frontend cases per suite. Additional passing cases are allowed.

The preview renders the recorded checks, runner output, and any attached change patch. The latest receipt must match the saved attempt byte for byte. Give the preview an exact `attempt-*` path to revisit a previous result. Each render writes `preview.json` and `preview.html` to its own `preview-*` directory. If the HTTP fetch succeeds, the directory also contains `http-body.html`. The preview receipt records the acceptance receipt's SHA-256 as `verify_sha256` and the test result as `verify_status`.

For a local-only claim, `finish --id ID --owner OWNER` checks the saved acceptance receipt, patch copy and evidence preview. A new verification attempt requires a matching evidence preview.

For a `dev` claim, verify the feature at its PR head. After the PR merges, use `record-dev` to register the GitHub receipt. The morning report links to the running deployment of the integrated `dev` commit. [Nightly workflow](../../docs/agent/nightly.md) · [Local deployment](../../docs/agent/candidate-preview.md)
