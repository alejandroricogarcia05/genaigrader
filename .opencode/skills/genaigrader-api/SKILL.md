---
name: genaigrader-api
description: Use when implementing, modifying, testing, or reviewing the external REST API under /api/v1/ (endpoints /models, /evaluations, /evaluations/{id}/status, /evaluations/{id}/results — requirements RF1, RF2, RF3, RF4, RF6). Points to the finalized API contract and project conventions.
---

# GenAI Grader External API

The finalized contract for external client applications lives in
`docs/api.md`. Read it fully before touching any `/api/v1/` endpoint. It is
the single source of truth — do not invent routes, fields, status codes, or
error shapes that are not in it.

## Workflow

1. Read `docs/api.md` (the spec) end to end.
2. Read the Architecture and Evaluation Pipeline sections of `AGENTS.md`.
3. Implement endpoints as Django REST Framework views/serializers, routed via
   `path("api/v1/", include(...))` in `mi_web/urls.py`.
4. Keep views thin: all business logic goes in `genaigrader/services/`,
   reusing existing services (course creation, evaluation stubs, task
   enqueuing) instead of duplicating logic.
5. Scope every query to `request.user` (ownership checks), exactly like the
   existing views do.
6. Add tests in `genaigrader/tests/` using Django `TestCase`, following the
   style of the existing test files.
7. Run black, isort, and ruff before finishing.
8. Self-review against the **Definition of Done** in `AGENTS.md` (docstrings,
   full type hints, honest names, no module-name redundancy) before reporting
   done.

## Conventions and known gaps

- **Auth scheme gap:** the spec requires `Authorization: Bearer <token>`,
  but `users/authentication.py` (`ApiTokenAuthentication`) currently only
  accepts the `Token` keyword, and `users/tests/test_authentication.py`
  asserts `Bearer` is rejected. When implementing the API, extend the class
  to accept `Bearer` and update that test accordingly.
- **Error shape:** every 4xx/5xx response must return
  `{"error": "...", "message": "..."}`. Use a shared custom DRF exception
  handler so 401/404/405/500 from DRF internals also match the contract.
- **Pagination:** `GET /evaluations` uses `limit`/`offset` returning
  `count`/`next`/`previous`/`results` — that is DRF's
  `LimitOffsetPagination`, not the project-wide default
  `PageNumberPagination`. Configure it per-view.
- **Route collision:** the existing `/api/` route is the HTML model
  management page (`api_view`). `/api/v1/` is the external JSON API. Do not
  confuse or merge them.
- **Async behavior:** `POST /evaluations` returns `202 Accepted` immediately.
  Evaluation must reuse the existing pipeline (`create_evaluation_stub` +
  `evaluate_question_task` per question); never block the request on LLM
  calls.
- **Course auto-creation:** `POST /evaluations` must create the course for
  the current user when no course with the normalized name exists
  (reuse `course_service.get_or_create_course()`).
