# Known-red backend baseline — F3 graded CI gate

Recorded 2026-11-27 for wave 0-A / KNOWN_ISSUES F3. The graded gate makes
**only currently-green checks required**. The full `pytest tests` suite is
**not** green on `dev` (`a59a33f`), so it runs as a non-blocking advisory job
(`Backend · full suite advisory`) instead. F3 adds only workflow files; none of
the failures below were introduced by the gate itself.

## Evidence

- Local isolated run, full suite: `.agent-runs/20261127-wave0/evidence/f3-backend.log`
  — `9 failed, 1223 passed, 2 skipped` (`EXIT=1`), 2026-11-27, `*_test` DB on
  loopback, LLM/GitHub credentials cleared.
- Local targeted red probe: `.agent-runs/20261127-wave0/evidence/f3-backend-red10.log`
  — `7 failed, 3 passed` (`EXIT=1`), deliberate RED probe of the study-chat /
  routing failures.
- GitHub Actions run `45968388833`, job `Backend · isolated pytest`
  (PR #50, full suite): `10 failed, 1209 passed, 15 skipped`.
- Frontend is green in both: `.agent-runs/20261127-wave0/evidence/f3-frontend.log`
  — `npm ci` / `npm test` / `npm run typecheck` / `npm run build`, `EXIT=0`.

The local and CI failure sets differ: 15 skipped vs 2 skipped, and the
`pg_restore` / `test_local_safety` / long stress-test failures are environment
dependent. The exclusions below are the **union** of both environments so the
required gate is green everywhere. Excluding a whole file also drops its green
tests from the required gate; those still run in the advisory full suite.

## Excluded from the REQUIRED backend gate

| file | reason / observed failure |
|---|---|
| `tests/test_bank_review_actions.py` | CI: `test_approval_checks_visible_answer_and_recognizes_duplicate` — `assert True is False` |
| `tests/test_learning_feedback_processes.py` | CI: `test_practice_chain_dump_restore_and_real_worker_resume[concept]` and `[project-snapshot]` — `pg_restore` tool failure (environment dependent; green locally) |
| `tests/test_standalone_ops_study_chat.py` | CI + local: `test_exact_lookup_requires_configured_header_auth` and `test_dashboard_proof_authorizes_only_header_reads_and_keeps_original_expiry` — `503` instead of `200`; `test_lookup_projects_question_only_inventory_counts[0-0-True/False]` and `[21-12-True/False]` — `sources()` missing `response_language` keyword (6 failures) |
| `tests/test_study_routing.py` | CI + local: `test_generation_deadline_closes_a_stalled_attempt` — `error_kind` is `cancelled` instead of `timeout` |
| `tests/test_local_safety.py` | Local only: `test_restore_rejects_original_database_before_any_connection` (environment dependent) |
| `tests/test_podcast_content.py` | Local only: `test_synthesis_joins_worker_that_exceeds_output_limit[...]` — 60s stress test timeout (environment dependent) |

## CI timezone normalization (CI run 46047119931)

**Observed failure** — PR #50, `Backend · isolated pytest (currently-green gate)`,
Ubuntu runner, 2026-11-27T18:11 UTC: `2 failed, 1103 passed, 11 skipped, 9 warnings in 204.11s (0:03:24)`,
`##[error]Process completed with exit code 1.`

- `tests/test_learning_loop.py:58` —
  `assert {t.due_date for t in retests} == {date.today() + timedelta(days=n) for n in (1, 3, 7)}`
  → `assert {datetime.dat...(2026, 12, 5)} == {datetime.dat...(2026, 12, 4)}`
  → Extra items in the left set: `datetime.date(2026, 12, 5)`, `datetime.date(2026, 11, 29)`,
  `datetime.date(2026, 12, 1)`
  → Extra items in the right set: `datetime.date(2026, 11, 30)`, `datetime.date(2026, 12, 4)`,
  `datetime.date(2026, 11, 28)`
- `tests/test_learning_loop.py:209` → `app/api/v1/exams.py:295` →
  `fastapi.exceptions.HTTPException: 409: scheduled retest is not due`

**Root cause (inspected, not inferred from test names)**:
`app/api/v1/learning.py:37-38` defines `_today()` as
`datetime.now(timezone(timedelta(hours=8))).date()` (fixed UTC+8); `start_retest` sets
`due_date=_today()`, and `complete_task` schedules retests from `_today()`. The tests and
`app/api/v1/exams.py:294` use process-local `date.today()`. The Ubuntu runner is UTC while
the local Windows verification box is UTC+8, so between 16:00–24:00 UTC the two dates differ
by one day. The earlier CI full-suite run at 08:47 UTC passed these tests because both zones
were still on the same calendar day.

**Required behavior (from the inspected assertions)**: retests are scheduled at the app's
UTC+8 "today" + 1/3/7 days; `start_retest` must issue an exam for a due retest; the explicit
`409: scheduled retest is not due` rejection for a not-yet-due retest is intentional and must
be preserved. The recorded error is evidence only, not a requirement to keep the bug or error
behavior; do not default to keeping or removing any behavior.

**Resolution inside F3**: pin `TZ: Asia/Shanghai` on both backend jobs so CI's `date.today()`
matches the app's fixed UTC+8 date. This does **not** add an `--ignore` — the six exclusions
above are unchanged — and does not touch application source. The local green rerun
(`1115 passed, 1 skipped`) was obtained in UTC+8, the same clock this pin reproduces.
The underlying app/test timezone inconsistency is a separate follow-up item below.

## Follow-up cleanup item (separate PR, not part of F3)

Fix the red tests above one at a time. For each: reproduce on an isolated
`*_test` database with credentials cleared, fix the underlying issue, verify the
test is green both locally and on CI, then remove exactly one `--ignore` flag
from the `backend` job and its entry from this file. **Do not** bulk-remove the
ignore list or widen the required gate until each test is proven green; the
contract test `.github/workflows/tests/test_ci_gate.py` locks the ignore list to
this baseline so the gate cannot be silently relaxed.

The `ruff` / `eslint` / `knip` advisory baseline has the same rule: cleanup is a
separate item, and the lint job must stay non-blocking until each check is green.
