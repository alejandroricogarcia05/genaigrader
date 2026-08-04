# External API Specification — `/api/v1/`

Finalized contract between GenAI Grader and external client applications.
This document is the single source of truth for implementing the endpoints
below. Do not deviate from routes, payloads, or error shapes defined here.

## Global Rules

- **Base URL:** `/api/v1/`
- **Authentication:** `Authorization: Bearer <token>` header on all routes.
  The token is the user's `CustomUser.api_token` (manageable/rotatable at
  `/api-token/`).
- **Format:** `Content-Type: application/json`
- **Base errors:** any failure (4xx or 5xx) returns:

```json
{"error": "error_type", "message": "Brief description"}
```

## Endpoint Definitions

### 1. Models (RF1)

**Summary:** Get the LLMs enabled for the current token.

**Route and method:** `GET /models`

- **Path parameters:** none
- **Query parameters:** none
- **Payload (body):** none

**Successful response (200 OK):**

```json
{
  "models": ["gpt-4-turbo", "llama-3-70b"]
}
```

**Error responses:**

- `401 Unauthorized`: the token is missing from the header or is invalid.

### 2. Create Evaluation (RF2)

**Summary:** Queue an exam for batch evaluation.

**Route and method:** `POST /evaluations`

- **Path parameters:** none
- **Query parameters:** none

**Payload (body):**

```json
{
  "exam": {
    "external_id": "exam-123",
    "course": "Operating Systems",
    "title": "Operating Systems Exam",
    "questions": [
      {
        "question_text": "What is a pointer?",
        "choices": [
          {
            "choice_text": "A variable that stores a memory address",
            "isCorrect": true
          },
          {
            "choice_text": "A primitive data type",
            "isCorrect": false
          }
        ]
      }
    ]
  },
  "models": ["gpt-4-turbo"],
  "iterations": 5
}
```

Field notes:

- `exam.external_id`: client-side identifier for the exam.
- `exam.course`: if no course with this normalized name exists for the
  current user, GenAI Grader creates it.
- `exam.questions[].choices[].isCorrect`: marks the correct choice; exactly
  one choice per question is expected to be correct.
- `models`: one or more model names, as returned by `GET /models`.
- `iterations`: number of repetitions to run per model.

**Successful response (202 Accepted):**

```json
{
  "evaluation_id": "eval-98765",
  "status": "pending",
  "total_tasks": 5
}
```

**Error responses:**

- `400 Bad Request`: malformed or incomplete JSON schema.
- `413 Payload Too Large`: the exam exceeds the allowed size limit
  (e.g. 5 MB) or the iterations limit.

### 3. Evaluation Status (RF3)

**Summary:** Poll the progress of the asynchronous processing.

**Route and method:** `GET /evaluations/{evaluation_id}/status`

**Path parameters:**

- `evaluation_id` (string): unique identifier of the evaluation.

**Query parameters:** none

**Payload (body):** none

**Successful response (200 OK):**

```json
{
  "evaluation_id": "eval-98765",
  "status": "processing",
  "progress": {
    "completed": 3,
    "failed": 0,
    "pending": 2,
    "total": 5
  }
}
```

**Error responses:**

- `404 Not Found`: the provided ID does not exist in the database.

### 4. Evaluation Results (RF4)

**Summary:** Return the grades once the evaluation has finished
(`completed`).

**Route and method:** `GET /evaluations/{evaluation_id}/results`

**Path parameters:**

- `evaluation_id` (string): unique identifier of the evaluation.

**Query parameters:** none

**Payload (body):** none

**Successful response (200 OK):**

```json
{
  "evaluation_id": "eval-98765",
  "results": {
    "gpt-4-turbo": [
      {
        "iteration": 1,
        "overall_score": 8.5,
        "details": [
          {"question_id": "q1", "selected_option": "a", "correct": "true"}
        ]
      }
    ]
  }
}
```

**Error responses:**

- `409 Conflict`: the evaluation is still running.

```json
{
  "error": "not_ready",
  "message": "The evaluation is still being processed."
}
```

- `404 Not Found`: the ID does not exist.

### 5. Evaluation History (RF6)

**Summary:** Paginated list of all submitted evaluations.

**Route and method:** `GET /evaluations`

- **Path parameters:** none

**Query parameters:**

- `limit` (integer, optional): maximum results per page (e.g. 50).
- `offset` (integer, optional): displacement from the start for pagination
  (e.g. 0).

**Payload (body):** none

**Successful response (200 OK):**

```json
{
  "count": 142,
  "next": "/api/v1/evaluations?limit=50&offset=50",
  "previous": null,
  "results": [
    {
      "evaluation_id": "eval-98765",
      "created_at": "2026-05-17T18:00:00Z",
      "status": "completed",
      "models_used": ["gpt-4-turbo"]
    }
  ]
}
```

**Error responses:**

- `400 Bad Request`: invalid `limit` or `offset` values (e.g. letters
  instead of numbers).
