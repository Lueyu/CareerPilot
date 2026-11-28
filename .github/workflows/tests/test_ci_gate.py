"""Run locally with python .github/workflows/tests/test_ci_gate.py.

Locks the F3 graded CI gate: required checks stay required, advisory checks
stay non-blocking, the known-red exclusions stay tied to a documented baseline,
and no credentials or fork-unsafe triggers sneak into CI. Kept dependency-free
(stdlib only) so the gate-contract job needs no install.
"""

import re
import unittest
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[1]
CI_TEXT = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
NIGHTLY_TEXT = (WORKFLOWS / "nightly-crash-probe.yml").read_text(encoding="utf-8")
BASELINE_TEXT = (WORKFLOWS / "known-red-backend-baseline.md").read_text(encoding="utf-8")

REQUIRED_BACKEND_IGNORES = (
    "tests/test_bank_review_actions.py",
    "tests/test_learning_feedback_processes.py",
    "tests/test_standalone_ops_study_chat.py",
    "tests/test_study_routing.py",
    "tests/test_local_safety.py",
    "tests/test_podcast_content.py",
)


def split_jobs(text):
    section = text.split("\njobs:\n", 1)[1]
    parts = re.split(r"(?m)^  ([a-z][a-z0-9-]*):[ \t]*\n", section)
    return {parts[i]: parts[i + 1] for i in range(1, len(parts), 2)}


class CIGateContract(unittest.TestCase):
    def setUp(self):
        self.jobs = split_jobs(CI_TEXT)

    def test_no_fork_unsafe_trigger(self):
        self.assertNotIn("pull_request_target", CI_TEXT)
        self.assertIn("pull_request:", CI_TEXT)
        self.assertIn("branches: [dev, master]", CI_TEXT)

    def test_minimal_permissions(self):
        self.assertIn("permissions:\n  contents: read", CI_TEXT)
        self.assertNotIn("write-all", CI_TEXT)

    def test_all_gate_jobs_present(self):
        self.assertTrue(
            {"backend", "backend-full-suite", "frontend", "lint-baseline", "gate-contract"}
            <= set(self.jobs),
            f"missing gate jobs: {set(self.jobs)}",
        )

    def test_backend_uses_isolated_pg_service(self):
        body = self.jobs["backend"]
        self.assertIn("postgres:16-alpine", body)
        self.assertIn("pg_isready", body)
        self.assertIn("requirements.txt -r requirements-dev.txt", body)
        self.assertIn("python run_isolated_tests.py tests -q --tb=short", body)
        self.assertNotIn("continue-on-error", body)

    def test_required_backend_only_runs_currently_green_tests(self):
        body = self.jobs["backend"]
        for path in REQUIRED_BACKEND_IGNORES:
            self.assertIn("--ignore=" + path, body, f"missing exclusion for {path}")
            self.assertIn(path, BASELINE_TEXT, f"baseline missing evidence for {path}")
        self.assertIn("Follow-up cleanup item", BASELINE_TEXT)
        self.assertNotIn("continue-on-error", body)

    def test_full_suite_is_advisory_only(self):
        body = self.jobs["backend-full-suite"]
        self.assertIn("python run_isolated_tests.py tests -q --tb=short", body)
        self.assertIn("suite_code=$?", body)
        self.assertIn("exit 0", body)
        self.assertNotIn("continue-on-error: false", body)

    def test_backend_jobs_pin_app_timezone(self):
        for job in ("backend", "backend-full-suite"):
            self.assertIn(
                "TZ: Asia/Shanghai", self.jobs[job],
                f"{job} must pin TZ so date.today() matches app _today()",
            )

    def test_frontend_required_checks(self):
        body = self.jobs["frontend"]
        for command in ("npm ci", "npm test", "npm run typecheck", "npm run build"):
            self.assertIn(command, body)
        self.assertNotIn("continue-on-error", body)

    def test_credentials_are_cleared(self):
        self.assertIn('LLM_API_KEY: ""', CI_TEXT)
        self.assertIn('GITHUB_TOKEN: ""', CI_TEXT)
        self.assertIsNone(re.search(r'(?m)^[ \t]+LLM_API_KEY:[ \t]*"[^"]+"', CI_TEXT))
        self.assertIsNone(re.search(r'(?m)^[ \t]+GITHUB_TOKEN:[ \t]*"[^"]+"', CI_TEXT))
        for job in ("backend", "backend-full-suite"):
            body = self.jobs[job]
            self.assertIn('LLM_API_KEY: ""', body)
            self.assertIn('GITHUB_TOKEN: ""', body)

    def test_advisory_job_never_blocks(self):
        body = self.jobs["lint-baseline"]
        for command in ("ruff check .", "npm run lint", "npm run knip"):
            self.assertIn(command, body)
        self.assertIn("exit 0", body)
        self.assertNotIn("continue-on-error: false", body)

    def test_gate_contract_is_executed_by_ci(self):
        self.assertIn(
            "python .github/workflows/tests/test_ci_gate.py",
            self.jobs["gate-contract"],
        )

    def test_nightly_probe_is_opt_in(self):
        self.assertIn("workflow_dispatch:", NIGHTLY_TEXT)
        self.assertIn("schedule:", NIGHTLY_TEXT)
        self.assertIn("postgres:16-alpine", NIGHTLY_TEXT)
        self.assertIn("run_isolated_tests.py tests/study_chat_crash_probe.py", NIGHTLY_TEXT)
        # Default collection must never pull the crash probe into the PR gate.
        self.assertNotIn("study_chat_crash_probe.py", self.jobs["backend"])
        self.assertNotIn("study_chat_crash_probe.py", self.jobs["frontend"])


if __name__ == "__main__":
    unittest.main()
