# Public library API

Mount app.api.public_library.router under /api/v1 in the public app. The route prefix is /library. The router imports PublicLibraryRole on app.db.Base. Create the metadata in the new cloud database after all routers are imported. Authentication supplies the workspace through app.api.public_auth.current_workspace. All requests use that workspace.

## Roles and questions

| Request | Input | Response |
| --- | --- | --- |
| GET /library/roles | | selected_role and roles. Each role contains role, jd, selected_topic, and directions. A direction has name, example, and origin (preset or model). |
| PUT /library/role | JSON with role and optional jd. Omit jd to keep it. Send an empty string to clear it. | Saves and selects the role. New roles start with preset directions, including when the model is unavailable. |
| PUT /library/direction | JSON with role and topic | Saves the selected topic for that role. Returns the role. |
| POST /library/directions | JSON with role | Adds or updates recommendations based on the saved JD through complete_text. Returns the role, or a gateway error. Presets and previously saved data remain available. |
| POST /library/example | JSON with role and topic | Saves or reuses the visible example and returns its question. Uses no model call. |
| POST /library/starter | JSON with role and topic | Generates and saves up to three starter questions through complete_text. Returns questions. Subsequent requests can return the saved questions. |
| GET /library/questions | Optional role, topic, and before query parameters | questions and next_before. Returns at most 50 private questions per page. |

A question contains id, title, role, topic, answer when available as plain text, filename, origin, and source_type. Its Question.role and Question.topic store the literal role and direction. New library questions have no calibrated skill or scoring rubric. Changing the selected workspace role leaves question history under its original role. Signup can set Workspace.target_role to an empty string. A workspace without a public role profile has selected_role set to null.

## Document review

| Request | Input | Response |
| --- | --- | --- |
| POST /library/imports | Multipart containing file, role, and topic | Creates or reopens a draft with source_text and items. An extraction failure returns a saved FAILED record and a Chinese error message. |
| GET /library/imports | Optional before query parameter | imports and next_before. Returns up to 30 summaries across the workspace's roles. |
| GET /library/imports/{id} | | Reopens the source, review states, and saved edits. |
| POST /library/imports/{id}/suggest | | Uses complete_text to find questions or propose an answer. The response separates original excerpts, edited text, and AI additions. |
| PUT /library/imports/{id}/draft | JSON with items | Saves candidate edits and checkboxes without creating questions. |
| POST /library/imports/{id}/approve | Same body as draft save | Saves selected draft questions. Unchecked entries remain available for a later review. Repeated approval returns the existing links. |

Each edit in items has id, title, answer, and selected. The response item includes those fields plus status, question_id, source_title, source_answer, ai_title, and ai_answer. Status is DRAFT, APPROVED, or MERGED. MERGED links to an existing question with the same normalized title for that role and topic and preserves its saved answer.

Upload limits are 6 MiB for one PDF, DOCX, MD, or Markdown file and 24,000 extracted characters. PDFs have a 30-page limit and require selectable text. DOCX archives have a 16 MiB expanded-size limit. Extraction runs in a local subprocess with a deadline and Linux memory and CPU limits. The server keeps the extracted text, review, and source filename. Uploading a supported document uses no model. Only the suggest request sends the extracted text to the configured provider.

## App integration

- Render the default Library export from web/src/seed/Library.tsx. Pass onPractice with the question ID, title, and mode. Pass onCoach to open the learning assistant.
- GET /privacy returns region and provider for the pre-upload disclosure. POST /feedback accepts question_id and text.
- Await export_library(db, workspace_id) in the authenticated export handler and include its result under library. It returns selected_role, roles with directions, imports with candidates, and private questions. It is JSON serializable.
- The router uses the new public model and auth modules at runtime. The isolated library tests supply boundary fixtures for both.
