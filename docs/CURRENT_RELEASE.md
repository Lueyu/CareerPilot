# Current development snapshot — 2026-11-17

This snapshot includes the personal learning and assessment loop, question workshop,
project intelligence, persistent Project Autopilot Worker, source-backed memory
review, streaming diagnostics and bounded checkpoint recovery.

The latest complete backend regression before source-split recovery passed 638
tests. Source-split, queue, budget, compatibility and API integration checks passed
100 tests; these sets overlap. The full updated suite is recorded separately when
finished. Frontend tests previously passed 152 cases and the build passed.

The long real-model project acceptance run remains unfinished and was stopped at
the user's request to deploy this snapshot. A complete real-model project report
and its pending memory candidates have not yet been accepted. Passing fixtures
does not establish semantic accuracy or long-term learning effectiveness.

Personal databases, credentials, raw model responses, backups and local acceptance
artifacts are not part of this Git snapshot. Existing model configuration is
preserved. Memory candidates require user confirmation; project observations do
not become personal competency evidence. Public content publishing remains off.

Local startup: `deploy/start-personal.ps1`; the personal application uses
http://127.0.0.1:5173 and the API uses http://127.0.0.1:8000.
Back up the actual database before applying migrations or restarting services.
