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

The command uses the base's `docker-compose.yml` and candidate Dockerfiles to bring up PostgreSQL, the API, the worker, and the web service. It adds the built-in `app.seed --demo` option and routes `/api/v1` through the preview's Nginx. The browser opens the synthetic Demo workspace on the preview port. The HTTP gate requests the app page, its compiled JavaScript, API readiness, and the seeded Demo dashboard through that address. The existing `server/run_isolated_tests.py` entry point creates an additional test database in the candidate's PostgreSQL container. Point the command at the actual test for the task. For a Python environment with project dependencies, use its absolute interpreter path as the first JSON argument.

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
