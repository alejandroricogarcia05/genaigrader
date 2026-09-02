# GenAI Grader — Project Context

## Purpose
Django web platform for automated academic exam evaluation using LLMs (local via Ollama or remote via OpenAI-compatible APIs).

## Tech Stack
- **Backend**: Django 5.2+ (Python 3.11+), django-q2 (task queue, DB as broker)
- **LLM**: Ollama SDK (local), OpenAI SDK (remote), streaming + `<think>` tag handling
- **DB**: SQLite3 (dev, WAL mode) / PostgreSQL 15 (prod)
- **Auth**: django-allauth (local + Google OAuth + OIDC)
- **Frontend**: Django templates, vanilla JS (jQuery), custom CSS
- **Infra**: Docker Compose, Gunicorn, WhiteNoise, uv (package manager)
- **Quality**: black, isort, ruff, pre-commit, GitHub Actions CI

## TDD Workflow (mandatory)

All development is test-driven. Tests are the source of truth — the starting point
and the verification step of every change. Never ship code without a test that
exercises it.

### Red-Green Loop
1. **Red** — Write a failing test that captures the desired behaviour *before*
   touching implementation code. Run it and watch it fail for the right reason
   (missing feature, not a broken environment).
2. **Green** — Write the minimal implementation to make the test pass.
3. **Refactor** — Clean up while keeping the test green.
4. Re-run the full targeted test file before finishing.

Every task that adds or changes behaviour follows this loop. A change with no
test is incomplete — do not report it as done.

### Running tests
Tests run on the system Python (Django 5.2.1 available, `python manage.py test`).
Target just the file you touched for fast feedback:
```
python manage.py test genaigrader.tests.<module>
```
Run the whole suite before considering a task complete. (Virtualenvs are forbidden
per Restrictions — do not activate them.)

### Test placement
- Unit/service tests → `genaigrader/tests/`
- API contract tests → `genaigrader/tests/test_api_v1_integration.py`
- Auth tests → `users/tests/`
Match the style of existing test files (Django `TestCase`, `APITestCase` where the
app is under test).

## Definition of Done (check before reporting complete)

Every merged change must clear this checklist. These prevent the recurring
reviewer notes observed on past PRs:

1. **Every public function has a docstring** saying what it returns. Multi-value
   returns (tuples/dicts) document each field/position explicitly.
2. **Type hints on every parameter** of new/modified functions, including `user`
   (`CustomUser`), `evaluations` (`QuerySet`), etc. No partially-typed signatures.
3. **Names say exactly what the code does.** No invented domain concepts
   (e.g. "enabled" unless a model field actually exists). Reuse established
   `_for_user` / `_get_or_create_` / `_aggregate_` naming patterns.
4. **No module-name redundancy in function names** (e.g. `api_evaluation_service.create_api_*`
   is wrong → `create_*`) unless the prefix disambiguates a genuinely separate flow.
5. **Tests written and green** — see TDD Workflow above.
6. **black, isort, ruff pass** on every touched file.

## Architecture
**Views → Services → Models** (three-tier, thin views):
- `genaigrader/views/` — HTTP handlers, `@login_required`, delegate to services (8 view modules)
- `genaigrader/services/` — All business logic (12 service modules)
- `genaigrader/models.py` — Django ORM models
- `genaigrader/llm_api.py` — Unified LLM abstraction (Ollama + OpenAI)
- `genaigrader/tasks.py` — django-q2 task entry points

### Service Interaction Map
```
upload_file_service.handle_file_upload()
  ├── course_service.get_or_create_course()
  ├── model_service.get_or_create_model()
  ├── llm_api.LlmApi.validate()
  ├── file_service.save_uploaded_file()
  ├── exam_service.process_exam_file()
  ├── stream_service.create_evaluation_stub()
  │     └── ollama_version_service.get_evaluation_ollama_version()
  └── [async] tasks.evaluate_question_task()
        └── stream_service.evaluate_single_question()
              ├── llm_service.generate_prompt()
              ├── llm_api.LlmApi.generate_response()
              │     ├── _use_local_model()  [Ollama SDK]
              │     └── _use_external_model() [OpenAI SDK]
              └── stream_service.compute_evaluation_summary()

graphics_service.compute_model_statistics()
  ├── confidence_service.compute_averages()
  └── model_service.resolve_model_color()

models.Model.save()
  └── model_service.auto_classify_and_color()
```

## Data Model
```
CustomUser → Course → Exam → Question → QuestionOption
Model → Evaluation → QuestionEvaluation
Family → Model (classification/colors)
CustomUser → ExternalIdentity (social login)
```

### Key Model Details

**Evaluation**: `status` ∈ {pending, running, completed, failed}. Grade = correct_answers / total × 10 (0–10 scale). Fields: `prompt`, `ev_date`, `grade`, `time`, `model` (FK), `exam` (FK), `ollama_version`, `notes`, `total_questions`, `failed_question_id`, `failed_reason`.

**QuestionEvaluation**: Per-question result. Fields: `evaluation` (FK), `question` (FK), `question_option` (FK, nullable — null = invalid/no response), `response_text`, `is_correct` (nullable), `question_time` (seconds).

**Model**: `is_external` property = True when both `api_url` and `api_key` are set. Local models have `user=null`. `save()` auto-calls `model_service.auto_classify_and_color()` for family/parameter classification.

**Family**: Groups models by family name (e.g., "llama3.2"). Each family has a `base_color`; individual model colors are derived from family base + parameter count/version.

## Evaluation Pipeline
1. User uploads exam text file via `/evaluate/`
2. File saved to `uploaded_files/`, parsed → Exam + Question + QuestionOption records
3. If model selected → `Evaluation` stub created (status=pending)
4. **One django-q2 task per question** enqueued via `evaluate_question_task`
5. Each task: `LlmApi.generate_response(prompt)` → stream → match answer
6. `QuestionEvaluation` record created per question (via `get_or_create` to prevent duplicates)
7. When all questions done → final grade computed, status=completed
8. Frontend polls `/evaluation/<id>/status/` and `/evaluation/<id>/questions/`
9. Results in Analysis/Exam Detail pages, CSV export available

### Concurrency Safety
- **`select_for_update()` + `transaction.atomic()`** in `evaluate_single_question` prevents race conditions when multiple question tasks complete simultaneously for the same evaluation.
- **`pending → running` transition is atomic**: `Evaluation.objects.filter(id=..., status="pending").update(status="running")` — only succeeds if still pending.
- **Progress based on `QuestionEvaluation` rows**, not django-q2 task records (django-q2 may prune completed task records).

## Batch Evaluations

### Overview
Batch evaluations allow running the same exam(s) against multiple models with repetitions, creating a cartesian product of `exams × models × repetitions` evaluations.

### Data Flow
```
Form (exams[], models[], repetitions, user_prompt, notes)
  → POST /batch-evaluations/ (JSON body)
  → handle_batch_evaluations_post():
      Triple loop: exam × model × rep
        → create_evaluation_stub() per combination
        → async_task(evaluate_question_task) per question
  → Response: {evaluation_ids, evaluations_meta, task_ids, total_tasks}
  → Frontend starts TWO parallel polling loops:
      Loop A: pollEvaluations() → POST /batch-evaluation-status/ (3s interval)
              → Progress bar + summary table rows
      Loop B: pollEvaluationQuestions() × N → GET /evaluation/<id>/questions/ (2s interval)
              → Per-question detail rendering
  → Also registers with notifications.js via window.addPendingEvaluation()
```

### Key Endpoints

| URL | Method | Purpose |
|---|---|---|
| `/batch-evaluations/` | GET | Render form (exams grouped by course, models grouped local/remote) |
| `/batch-evaluations/` | POST | Create stubs + enqueue tasks. Returns JSON with `evaluations` meta array |
| `/batch-evaluation-status/` | GET/POST | Bulk status for N evaluation IDs. Single grouped COUNT query. Max 1000 IDs |
| `/evaluation/<id>/questions/` | GET | Per-question detail for one evaluation |

### Frontend Architecture (`static/js/batch_evaluations.js`)

- **`handleBatchEvalTask(response)`**: Orchestrator. Builds `metaById` map from `data.evaluations` (containing `repetition`/`total_repetitions`), starts both polling loops, registers with notification system.
- **`pollEvaluations(evaluationIds, metaById, onProgress, onAllComplete)`**: Batch-level poll. Renders summary table rows via `renderEvaluationRow(ev, meta)`. Updates progress bar from `completed_tasks/total_tasks` sums.
- **`pollEvaluationQuestions(evalId, meta)`**: Per-evaluation poll. Renders heading + question detail boxes incrementally.
- **`renderEvaluationRow(ev, meta)`**: Renders table row with date, model, subject, exam, repetition (from meta), grade, time.
- **`formatDuration(ms)`**: Converts ms to `Xd Xh Xm Xs`.
- **`updateEvalCountIndicator()`**: Live counter showing `exams × models × repetitions` total.

### Repetition Metadata
- `repetition` and `total_repetitions` are **not persisted** in the `Evaluation` model — they are session metadata returned in the POST response's `evaluations` array.
- The frontend propagates this via `metaById` map to both the summary table and per-question headings.

### Error Handling
- **Server**: Invalid JSON → 400. Invalid repetitions → fallback to 1. Exam with no questions → `ValueError`. Deleted evaluation → task discarded. LLM failure → `status='failed'` with `failed_question_id`/`failed_reason`.
- **Client**: Non-JSON response (session redirect) → stop polling. 10 consecutive fetch failures → stop polling. Failed evaluation → "Failed" in grade cell + error details in `#batch-eval-errors`.

## Cross-Page Notification System (`static/js/notifications.js`)

IIFE loaded on every page via `base.html`. Provides persistent toast notifications that survive page navigation.

- **Storage**: `localStorage` key `genaigrader_pending_evaluations`. Each entry: `{evalId, examId, addedAt}`.
- **Public API**: `window.addPendingEvaluation(evalId, examId)`, `window.addPendingDownload(taskId, modelName)`.
- **Polling**: Recursive `setTimeout` (5s base, exponential backoff to 30s max, 10 retries). Single `POST /batch-evaluation-status/` for all pending evals.
- **Stale cleanup**: Purges entries older than 4 hours.
- **Cross-tab**: `scanAndRestart()` runs every 15s to restart stopped loops when new items appear in localStorage.
- **Toasts**: Max 3 visible, auto-dismiss 8s, slide-in/out animations, clickable → navigate to exam detail.

## LLM Abstraction (`genaigrader/llm_api.py`)

`LlmApi` wraps two backends behind a single streaming generator:
- **Local**: `ollama.Client()` with 300s timeout → `chat(stream=True)`
- **External**: `openai.OpenAI()` with 300s timeout → `chat.completions(stream=True)`
- **`<think>` tag handling**: `_yield_thinking_aware()` buffers content between `<think>...</think>` and only yields post-think tokens.
- **SSRF protection**: `is_private_url()` resolves hostname via DNS, rejects private/loopback/reserved/link-local IPs. Defense-in-depth (TOCTOU/DNS rebinding possible).
- **Validation cache**: Process-level `_validated_model_ids` set avoids re-validating the same model within a worker.

## Key Routes

| URL | View | Methods | Purpose |
|---|---|---|---|
| `/` | `home_view` | GET | Landing |
| `/evaluate/` | `evaluate_view` | GET | Upload exam form |
| `/upload/` | `upload_file` | POST | File upload handler |
| `/course/` | `course_view` | GET, POST | Course management |
| `/course/update/<id>/` | `update_course` | PUT | Rename course |
| `/course/delete/<id>/` | `delete_course` | DELETE | Delete course |
| `/course/exam/update/<id>/` | `update_exam` | PUT | Rename exam |
| `/course/exam/delete/<id>/` | `delete_exam` | DELETE | Delete exam |
| `/exam/<id>/` | `exam_detail` | GET | Exam detail + results + charts |
| `/question/<id>/analytics/` | `question_analytics` | GET | Per-question accuracy stats |
| `/evaluation/<id>/status/` | `evaluation_status` | GET | Poll single evaluation |
| `/evaluation/<id>/questions/` | `evaluation_questions` | GET | Per-question results |
| `/evaluation/delete/<id>/` | `delete_evaluation` | DELETE | Delete evaluation |
| `/batch-evaluations/` | `batch_evaluations_view` | GET, POST | Batch eval form + submit |
| `/batch-evaluation-status/` | `batch_evaluation_status` | GET, POST | Bulk poll evaluations |
| `/analysis/` | `analysis_view` | GET | Global statistics |
| `/api/` | `api_view` | GET | Model management page |
| `/model/create/` | `create_model` | POST | Create external model |
| `/model/update/<id>/` | `update_model` | PUT | Update model |
| `/model/delete/<id>/` | `delete_model` | DELETE | Delete model |
| `/model/pull/` | `pull_model` | POST | Queue Ollama download |
| `/task/<id>/` | `task_status` | GET | Poll single django-q2 task |
| `/batch-task-status/` | `batch_task_status` | GET, POST | Bulk poll tasks |
| `/export/all/` | `export_all_evaluations` | GET | CSV export all evals |
| `/export/course/<id>/` | `export_course_evaluations` | GET | CSV export per course |
| `/settings/` | `UserSettingsView` | GET, POST | User profile |
| `/accounts/` | allauth URLs | — | Auth (login, signup, social) |

## External API (`/api/v1/`)
- Finalized contract for external client apps: `docs/api.md` — single source of truth; read it before implementing or modifying any `/api/v1/` endpoint
- Endpoints: `GET /models`, `POST /evaluations`, `GET /evaluations` (history), `GET /evaluations/{id}/status`, `GET /evaluations/{id}/results` (RF1–RF4, RF6)
- Auth: `Authorization: Bearer <api_token>` via DRF (`users/authentication.py`); error shape `{"error", "message"}` on all 4xx/5xx
- Distinct from `/api/` (the HTML model management page) — do not confuse them
- Agent workflow: `.opencode/skills/genaigrader-api/SKILL.md`

## Frontend JS Architecture

| File | Page | Key Functions |
|---|---|---|
| `polling.js` | Global utils | `getCookie()`, `pollTask()`, `pollBatchTasks()` — recursive `setTimeout` with backoff |
| `notifications.js` | Global (base.html) | Cross-page toasts via localStorage. `addPendingEvaluation()`, `addPendingDownload()` |
| `evaluate.js` | `/evaluate/` | Form submit → `/upload/`, live question polling, duplicate exam detection (409) |
| `batch_evaluations.js` | `/batch-evaluations/` | Form submit, dual polling loops, summary table, per-question details |
| `exam_detail.js` | `/exam/<id>/` | DataTables, Chart.js error bar charts, live eval polling, question analytics |
| `analysis.js` | `/analysis/` | Chart.js charts with confidence intervals from server-rendered JSON |
| `course.js` | `/course/` | Inline CRUD for courses and exams |
| `api.js` | `/api/` | Model CRUD, Ollama download with `pollTask()` |
| `profile_settings.js` | `/settings/` | Enable/disable save button based on field changes |

## Conventions
- **No inline styles** — all styling via CSS classes (one CSS file per page in `static/css/`)
- **Business logic in services**, not views or models
- **Per-question task granularity** — one django-q2 task per question
- **DB as task broker** — no Redis dependency (`Q_CLUSTER.orm = "default"`)
- **Code style**: black (format), isort (imports), ruff (lint)
- **Tests**: Django `TestCase`, 18 test files in `genaigrader/tests/` and `users/tests/`
- **Package manager**: `uv` (not pip/poetry)
- **Dual input format**: Batch status endpoints accept both GET (comma-separated query param) and POST (JSON body). POST recommended for large batches.
- **Order-preserving responses**: Batch endpoints guarantee results match input ID order.

## Exam File Format
```
Question text
a) Option A
b) Option B
c) Option C

a
```
Questions separated by blank lines, correct answer letter on its own line. Parsed by `exam_service.process_exam_file()` — a state machine: `statement → options → correct → repeat`.

## django-q2 Configuration
| Setting | Value |
|---|---|
| `name` | `"genaigrader"` |
| `workers` | 1 |
| `timeout` | 7200 (2h) |
| `retry` | 7300 |
| `queue_limit` | 50 |
| `orm` | `"default"` (DB broker) |
| `ack_failures` | True |
| `save_limit` | 0 (no pruning) |
| `sync` | False |

Task enqueuing pattern:
```python
async_task(evaluate_question_task, eval_id, question_id, user_prompt,
           group=f"eval:{eval_id}", timeout=EVALUATION_TASK_TIMEOUT)  # 3600s
```

## Deployment
| Env | DB | Web | Worker |
|---|---|---|---|
| Docker prod | PostgreSQL | Gunicorn | django-q2 container |
| Docker dev | SQLite WAL | runserver | django-q2 container |
| Scripts/ngrok | SQLite WAL | Gunicorn | django-q2 (tmux) |

- `Docker/entrypoint.sh` handles migration locking (atomic `mkdir`) to prevent concurrent migrations
- Worker and web are separate containers in Docker
- Prod uses `dj_database_url` or `DJANGO_DB_ENGINE=postgres` with `POSTGRES_*` env vars. `CONN_MAX_AGE=500`.
- Static files: WhiteNoise `CompressedManifestStaticFilesStorage`

## Auth & Security
- `CustomUser` extends `AbstractUser` with unique email + auto-generated `api_token` (via `secrets.token_urlsafe(32)`)
- Social login: Google OAuth + OIDC via django-allauth
- SSRF protection: external model URLs validated against private IPs
- `SOCIALACCOUNT_LOGIN_ON_GET = False` (CSRF prevention for social login)
- `ACCOUNT_EMAIL_VERIFICATION = "mandatory"`
- Ownership checks: all views scope queries to `exam__course__user=request.user`
- File upload limits: 10 MB (`DATA_UPLOAD_MAX_MEMORY_SIZE`, `FILE_UPLOAD_MAX_MEMORY_SIZE`)

## Directory Map
```
mi_web/          → Django project config (settings, urls, wsgi, asgi)
genaigrader/     → Core app (views, models, services, templates, tests)
  views/         → 8 view modules (home, evaluate, course, exam_details,
                   evaluation, analysis, api, task, user_settings, batch_evaluations)
  services/      → 12 service modules (stream, llm, upload_file, exam, course,
                   model, file, get_models, graphics, question_analytics,
                   confidence, ollama_version)
  templates/     → Django templates + partials/ + account/
  tests/         → 18 test files
users/           → Custom user app (models, adapters, signals, tests)
static/css/      → Per-page CSS files (12 files)
static/js/       → Per-page JS files (10 files, jQuery-based)
Docker/          → docker-compose.dev.yml, docker-compose.prod.yml, entrypoint.sh
scripts/         → Operational scripts (start/stop/restart/migrate)
uploaded_files/  → User-uploaded exam files (MEDIA_ROOT)
```

## Restrictions
- **Do NOT activate or interact with Python virtual environments** (`source .venv/bin/activate`, `venv\Scripts\activate`, etc.) — the agent runs in a Linux shell while the host is Windows (WSL), so virtualenv commands will fail. Do not use uv or pip, ask the user for help.
- **Running tests is always permitted and required.** Use `python manage.py test` directly (system Python has Django 5.2.1). See TDD Workflow above.
