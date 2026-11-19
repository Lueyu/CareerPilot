# Learning feedback backend contract — 2026-11-17

## One grading record

An immutable `LearningSubmission` references one practice `Exam`, `AnswerRevision`, and `ScoringRun(mode=PRACTICE)`. The existing strict judge and persistent scoring worker produce the result. Feedback is a projection of that run's validated criterion results, not a second grade. Practice publication never calls formal exam finalization and does not create `Evidence`, `SkillState`, or `CompetencySnapshot`.

Reading completion records progress only. A new formal retest requires a new answer and grading. Same-family conceptual practice can become `PRACTICE_CORRECT` or `ASSISTED_CORRECT`; it never becomes independent transfer verification. Project review can become `CODE_REVIEWED`, with test execution and personal understanding explicitly unverified.

## Persistent relations and versions

- `LearningGoal`: original task/evidence/exam/question/answer revision/rubric links, origin hash deduplication, frozen criterion and job-standard snapshot, plan revision, verification states, source-memory versions, and history metadata.
- `LearningActivity`: immutable assessment-question snapshot and content hash, activity version, position/kind/status, source authoring mode, monotonic assistance events, and latest-submission pointer.
- `LearningSubmission`: append-only revision, optional previous-submission link, idempotency key, canonical payload/input hash, frozen assistance and activity version, scoring identifiers, and timestamps.

The same request key with changed raw input is rejected. A revised submission must name the current previous submission. An older completed result remains readable but cannot replace the newest activity state. Complete results are displayed only for their own submission; failures expose the actual scoring-run state and retry eligibility.

## API

| Endpoint | Behavior |
| --- | --- |
| `GET /learning/tasks/{id}/path` | Read existing path. Unprepared tasks return `goal:null`, empty activities/submissions. No implicit model call or lesson generation. |
| `POST /learning/tasks/{id}/prepare` | Create or reuse the source-bound goal and three conceptual practice scaffolds, then return the path. |
| `POST /learning/activities/{id}/assistance` | `{kind:HINT\|REFERENCE,request_key,expected_activity_revision}`; save exposure before returning material. |
| `POST /learning/activities/{id}/submissions` | Conceptual `{payload,request_key,expected_activity_revision,parent_submission_id?}`; HTTP 202 with submission/scoring identifiers and feedback URL. Project submissions use the project preview route. |
| `GET /learning/submissions/{id}` | Current run state, this submission's payload and feedback; returned completed feedback records exposure for later attempts. |
| `POST /learning/submissions/{id}/retry` | Recheck current sources, then resume the existing run; completed scoring steps are reused. |
| Existing task completion/retest endpoints | Completion changes reading progress only. Retest requires the existing task prerequisites and current valid sources. |

Path summaries omit feedback bodies so reading the path cannot bypass feedback exposure. Activity fields include `revision`, `content_version`, `authoring_source`, prompt, structured success criteria, allowed actions, assistance, and compact latest-submission state. `DEFERRED`, `SKIPPED`, and `PAUSED` activities require explicit resume before assistance or submission.

## Source and assistance rules

A source must be an actually assessed, structurally valid observation bound to a completed original exam, frozen question, answer revision and rubric. Pending question/standard feedback blocks new use as “needs review”; it does not silently change historical grades. Withdrawn or changed confirmed memories block new dispatch. The worker rechecks sources before an external request, and source invalidation cancels/fences unfinished practice runs. In-flight external work cannot be recalled, but invalidated output cannot publish as current progress.

Valid assisted observations remain usable for learning goals, marked `ASSISTED_OBSERVATION`, with original assistance recorded. They are excluded from independent competency calculation. `assessed_observations(..., include_assisted=True)` is reserved for learning-source validation; the competency default remains strict. Practice itself does not emit mastery evidence.

Assistance freezes `hint_used`, `reference_viewed`, `feedback_seen`, `ai_declared`, prior-attempt and source-family conditions. UI aliases are normalized and merged with server facts; clients cannot clear previously recorded assistance. Returning bank reference material records a canonical-family exposure event. Formal retest selection excludes exposed families. This records material returned by the application, not proof that the user read it, and cannot observe undisclosed outside assistance.

Internal `PRACTICE_ONLY` question rows are excluded from formal selection, bank lists/facets and generation duplicate context/approval matching. They cannot be selected manually for a formal exam. Practice exams are excluded from formal exam lists and dashboard recent exams.

## Project review boundary

`validate_project_submission` loads a current-workspace immutable preview snapshot, checks activity/project/version/hash binding, and rebuilds the canonical payload. Arbitrary text cannot substitute for the preview. The frozen scoring payload includes the project contract, snapshot identifiers, declared test evidence and `project_source_text` from actual preview files only. Source-claim quotes must appear in that source text, rather than merely in an explanation or supplied log.

No submitted code or test command is executed. Model observations are code review, not verified test execution or personal understanding. Judge/semantic validation errors retain known safe provider metadata and private raw response for the existing attempt audit, without exposing raw responses publicly.

## Service integration

`ensure_goal`, `create_activity`, and `submit_activity` flush but leave commit ownership to their caller. `prepare_path` and `record_assistance` commit before returning. The worker publishes practice feedback and memory-candidate hooks inside its existing atomic publication transaction.

Memory integration uses `attach_learning_context`, `on_submission_completed`, and read-only `extra_path`. Formal retest completion calls `on_verification_completed`; it verifies frozen independence, the specific covered criterion, and actual delayed-test due time. Candidates require the separate confirmation workflow; no practice feedback directly becomes a confirmed memory or an activated teaching strategy.

## Limits of this implementation

The three conceptual activities are deterministic templates using the frozen source criterion plus the activity instruction. They are explicitly `deterministic_prompt_template`, `AI_DRAFT`, and not human calibrated or model-authored personalized lessons. Existing teaching content is reused when available. Reference answers are not automatically fed back as learner evidence. Source version correctness, recovery, and fixture-based grading checks do not establish scoring accuracy or long-term learning effectiveness.
