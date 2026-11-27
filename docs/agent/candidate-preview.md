# Live candidate previews

Run the controller from the CareerPilot checkout with Python 3.12+ and a local Docker Engine with Compose v2. On Windows, use Docker Desktop in Linux container mode and PowerShell 7. Docker can download build images and dependencies anonymously. Prepare the host test runtime separately if a check needs Python or Node packages. The candidate worktree and run directory must stay available through morning review.

## Deploy the integrated dev branch

Fetch `origin/dev` and use its exact SHA in a fresh, detached worktree for each project. Create a run directory with an empty `change.patch`. Point `start` below at that worktree, set `$base` to the fetched SHA and use the empty patch's SHA-256. Run the checks for the combined user journeys with the preview's fictional data. Record the deployed SHA, URL and reports with the list of landed PRs. The same identity checks verify a deployed Git commit even when the patch is empty.

## Start from a checkpoint

Use the ledger's run directory and candidate worktree. The ledger writes the exact binary Git patch to `change.patch`. New files must be included with `git add --intent-to-add` before checkpointing. Each command below uses the same identity. Set `$tree` and `$run` to the paths for the selected task.

```powershell
$cli = 'D:/agent-interview/scripts/agent/candidate_delivery.py'
$base = (git -C $tree rev-parse HEAD).Trim()
$sha = (Get-FileHash -LiteralPath (Join-Path $run change.patch) -Algorithm SHA256).Hash.ToLowerInvariant()
```

### CareerPilot

```powershell
$project = 'careerpilot'
$regression = '["python","server/run_isolated_tests.py","tests/test_assessment_contract_guards.py","-q"]'
python $cli start --run-dir $run --workspace $tree --base $base --patch-sha $sha --project $project --regression $regression
```

Use the project Python with backend dependencies for this controller. It uses the base Compose and candidate Dockerfiles, with one completed preflight plus four running services (PostgreSQL, API, worker, web). Each owned attempt creates a protected `preview.env` with random synthetic credentials; repeated init preserves its bytes. The adapter adds `app.seed --demo` and explicit known-new-installation registration before serving. Ordinary startup never inserts NORMAL. `/api/v1` uses the preview Nginx.

The page now opens WorkspaceLogin. Enter slug `demo` and API_TOKEN from this attempt's private `preview.env`; never print the token or copy personal .env. Browser regression commands receive `CANDIDATE_API_TOKEN` and supply it at runtime through login or a fresh context's sessionStorage. `VITE_API_TOKEN` is unused. The HTTP gate reads the same private file for the Demo dashboard and checks real HTML, compiled JavaScript and API readiness. `server/run_isolated_tests.py` creates a further owned test database. Use absolute interpreter paths in JSON check arguments.

If Python-launched Docker fails while native PowerShell works, preserve the failed controller receipt. A separately recorded native fallback must bind the exact source/hash, owned container/image/volume/listener identities, served assets and browser journeys. Its success does not change the original controller receipt to PASS. Keep both evidence sets; never change Docker ACLs or personal services to bypass the failure.

### Squabble

```powershell
$project = 'squabble'
$regression = '["node","--test","scripts/ensure-jwt-secret.spec.cjs"]'
python $cli start --run-dir $run --workspace $tree --base $base --patch-sha $sha --project $project --regression $regression
```

This uses the base's `docker-compose.demo.yml`, the candidate Dockerfile's `npm run build:web`, `scripts/start-app.cjs`, and the existing fictional SQL seed. The browser opens Squabble's sign-in page. Register a fictional local account for authenticated acceptance. The HTTP gate requests the app page, its compiled JavaScript and `/api/health`, which queries the candidate database. The example runs the existing JWT startup regression and applies to startup secret behavior. For other tasks, select the affected test from the checkout. A command that needs dependencies can use a trusted local wrapper to run `npm ci` in the snapshot and invoke the chosen suite. Create any extra databases for integration tests on the candidate PostgreSQL server and set that suite's test database variable there.

## Supply task-specific checks

Pass `--regression` a JSON argument array for a check that demonstrates the relevant behavior. Commands run in the Git snapshot, with a fresh home, no inherited provider credentials, and a loopback-only `CANDIDATE_PREVIEW_URL`, `CANDIDATE_DATABASE_URL`, and `DATABASE_URL`. CareerPilot also sets the snapshot's backend `PYTHONPATH`. Host commands are for trusted project tests and fixture scripts. Make a check exit nonzero if its required test, dependency, fixture, or assertion is skipped. Use independent commands for the regression and browser assertions where both apply.

Pass `--ui` with a JSON command array for client changes or any task whose acceptance includes user-visible behavior. The browser command should open `CANDIDATE_PREVIEW_URL` using a fresh browser profile, perform the affected action with fictional data, assert the visible result, and log the observed behavior. A trusted wrapper under the run directory can run the browser with an explicit executable path. The controller requires a browser command for changes under CareerPilot's `web/` and Squabble's `client/`. For shared or server changes that affect the UI, supply it explicitly. Project fixture-only UI probes can supplement a live browser check.

## Check, recover, and stop

```powershell
python $cli check --run-dir $run --workspace $tree --base $base --patch-sha $sha --project $project
python $cli stop --run-dir $run
```

After `start` returns, Docker keeps the detached preview available for morning review. Keep Docker Desktop running and leave the preview up until review ends. Read `preview_url` from `candidate-delivery.json` and record that URL and the receipt path in the morning report. The attempt directory named in the receipt holds `startup.log`, `regression.log`, and `ui.log` when applicable. The gate rechecks the Git binding, log digests, owned container and image IDs, and fresh HTTP evidence. `verify_delivery(run_dir, workspace, base, patch_sha, project)` returns that receipt on success and raises `ValueError` when the gate fails. Recovery uses the same accepted patch, base, workspace and receipt. The original workspace may remain at the base with the patch applied or point at its exact published child. Preserve the ledger's previously bound acceptance when reconciling an already-published ref.

Stop is repeatable. It checks the unique ownership label, Compose project and deployment config before stopping those container IDs. A failed or interrupted attempt with a receipt can be stopped the same way. A crash before writing the receipt cannot have started containers. The next start uses a fresh UUID and preserves any orphan files. After a stop, the live gate fails and the receipt continues to show the acceptance result and stop time. A new `start` after stop archives the prior receipt and creates its own database volume, address, and logs. Stop preserves the candidate containers' stopped state, synthetic database volumes, images, network, and evidence files. The tool never invokes Docker prune or deletes data.

Each attempt uses a clean Git snapshot and Compose's own isolated project resources. Docker assigns both host ports on `127.0.0.1` and the controller has no path to the projects' personal launchers. Compose receives an explicit empty env file and a fresh home. Runtime containers use a project-scoped Docker bridge, which can make outbound connections. Keep provider settings empty and use fictional inputs. The built-in health and application checks require no provider calls.
