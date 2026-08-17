"""Integration tests for the external REST API under /api/v1/.

These tests exercise the full evaluation pipeline end-to-end: POST creates
the batch and enqueues the orchestrator; the orchestrator enqueues per-
question tasks; each task runs ``evaluate_single_question`` which calls the
LLM, creates ``QuestionEvaluation`` rows, and computes the final grade.

The only mocked dependency is the LLM (``LlmApi.generate_response``) — the
orchestrator, per-question tasks, DB transactions, and grade computation all
run for real.  ``async_task`` is mocked at both call-sites (the service layer
and the tasks module) so the tests never touch the django-q2 broker.
"""

import json
from unittest.mock import patch

from django.test import Client, TestCase

from genaigrader.models import Course, Evaluation, Exam, Model
from genaigrader.tasks import batch_orchestrator_task, evaluate_question_task
from users.models import CustomUser


def _base_payload(external_id="exam-123", num_questions=1, models=None, iterations=1):
    """Build a valid ``POST /evaluations`` payload."""
    questions = []
    for i in range(num_questions):
        questions.append(
            {
                "question_text": f"Question {i + 1}?",
                "choices": [
                    {"choice_text": "Correct answer", "isCorrect": True},
                    {"choice_text": "Wrong answer", "isCorrect": False},
                ],
            }
        )
    return {
        "exam": {
            "external_id": external_id,
            "course": "Operating Systems",
            "title": "OS Exam",
            "questions": questions,
        },
        "models": models or ["gpt-4-turbo"],
        "iterations": iterations,
    }


class ApiV1IntegrationBase(TestCase):
    """Shared helpers for /api/v1/ integration tests."""

    def setUp(self):
        self.client = Client()
        self.user = CustomUser.objects.create_user(
            username="apiuser",
            email="api@example.com",
            password="password123",
        )
        Model.objects.create(
            description="gpt-4-turbo",
            api_url="https://api.openai.com/v1",
            api_key="sk-test",
            user=self.user,
        )
        self.url = "/api/v1/evaluations"
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {self.user.api_token}"}

    def _post(self, payload):
        return self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
            **self.auth,
        )

    def _get(self, path):
        return self.client.get(path, **self.auth)

    def _post_and_run(
        self,
        payload,
        answer="a",
        llm_raises=False,
        max_tasks=None,
        llm_side_effect=None,
    ):
        """POST an evaluation and execute the pipeline synchronously.

        Parameters
        ----------
        answer : str
            Single letter the fake LLM yields (``"a"`` = correct, ``"b"`` = wrong).
        llm_raises : bool
            If True the LLM always raises ``RuntimeError``.
        max_tasks : int | None
            Run only the first *N* captured question tasks (useful for
            intermediate-state assertions).  ``None`` runs all.
        llm_side_effect : callable | None
            Custom side-effect for ``generate_response``; takes precedence
            over *answer* / *llm_raises*.
        """
        with (
            patch(
                "genaigrader.services.api_evaluation_service.async_task"
            ) as orch_mock,
            patch("genaigrader.tasks.async_task") as task_mock,
            patch("genaigrader.services.stream_service.LlmApi") as llm_mock,
        ):

            if llm_side_effect is not None:
                llm_mock.return_value.generate_response.side_effect = llm_side_effect
            elif llm_raises:

                def _fail(prompt):
                    raise RuntimeError("LLM unavailable")

                llm_mock.return_value.generate_response.side_effect = _fail
            else:
                llm_mock.return_value.generate_response.side_effect = lambda p: iter(
                    [answer]
                )

            response = self._post(payload)
            if response.status_code != 202:
                return response, None

            batch_id = response.json()["evaluation_id"]

            orch_args = orch_mock.call_args[0]
            evaluations_meta = orch_args[1]
            exam_questions_map = orch_args[2]
            user_prompt = orch_args[3] if len(orch_args) > 3 else ""

            batch_orchestrator_task(evaluations_meta, exam_questions_map, user_prompt)

            calls = task_mock.call_args_list
            if max_tasks is not None:
                calls = calls[:max_tasks]
            for call in calls:
                c = call[0]
                eval_id = c[1]
                question_id = c[2]
                p = c[3] if len(c) > 3 else ""
                if llm_raises or llm_side_effect is not None:
                    try:
                        evaluate_question_task(eval_id, question_id, p)
                    except Exception:
                        pass
                else:
                    evaluate_question_task(eval_id, question_id, p)

        return response, batch_id


# ─── End-to-end lifecycle ─────────────────────────────────────────────


class ApiV1EndToEndFlowTest(ApiV1IntegrationBase):
    """Full lifecycle: POST → run pipeline → poll status → fetch results → history."""

    def test_correct_answer_full_lifecycle(self):
        response, batch_id = self._post_and_run(_base_payload(), answer="a")
        self.assertEqual(response.status_code, 202)
        self.assertTrue(batch_id.startswith("eval-"))

        # Status
        status_resp = self._get(f"/api/v1/evaluations/{batch_id}/status")
        self.assertEqual(status_resp.status_code, 200)
        status_data = status_resp.json()
        self.assertEqual(status_data["evaluation_id"], batch_id)
        self.assertEqual(status_data["status"], "completed")
        self.assertEqual(status_data["progress"]["completed"], 1)
        self.assertEqual(status_data["progress"]["total"], 1)
        self.assertEqual(status_data["progress"]["failed"], 0)
        self.assertEqual(status_data["progress"]["pending"], 0)

        # Results
        results_resp = self._get(f"/api/v1/evaluations/{batch_id}/results")
        self.assertEqual(results_resp.status_code, 200)
        results = results_resp.json()
        self.assertEqual(results["evaluation_id"], batch_id)
        self.assertIn("gpt-4-turbo", results["results"])
        entry = results["results"]["gpt-4-turbo"][0]
        self.assertEqual(entry["iteration"], 1)
        self.assertEqual(entry["overall_score"], 10.0)
        detail = entry["details"][0]
        self.assertEqual(detail["question_id"], "q1")
        self.assertEqual(detail["selected_option"], "a")
        self.assertEqual(detail["correct"], "true")

        # History
        history_resp = self._get(self.url)
        self.assertEqual(history_resp.status_code, 200)
        history = history_resp.json()
        self.assertGreaterEqual(history["count"], 1)
        item = next(r for r in history["results"] if r["evaluation_id"] == batch_id)
        self.assertEqual(item["status"], "completed")
        self.assertEqual(item["models_used"], ["gpt-4-turbo"])

    def test_incorrect_answer_full_lifecycle(self):
        response, batch_id = self._post_and_run(_base_payload(), answer="b")
        self.assertEqual(response.status_code, 202)

        results_resp = self._get(f"/api/v1/evaluations/{batch_id}/results")
        self.assertEqual(results_resp.status_code, 200)
        entry = results_resp.json()["results"]["gpt-4-turbo"][0]
        self.assertEqual(entry["overall_score"], 0.0)
        detail = entry["details"][0]
        self.assertEqual(detail["selected_option"], "b")
        self.assertEqual(detail["correct"], "false")

    def test_processing_intermediate_state(self):
        payload = _base_payload(num_questions=2)
        response, batch_id = self._post_and_run(payload, max_tasks=1)
        self.assertEqual(response.status_code, 202)

        status_resp = self._get(f"/api/v1/evaluations/{batch_id}/status")
        status_data = status_resp.json()
        self.assertEqual(status_data["status"], "processing")
        self.assertEqual(status_data["progress"]["total"], 1)
        self.assertEqual(status_data["progress"]["pending"], 1)

    def test_results_409_while_pending(self):
        payload = _base_payload(num_questions=2)
        response, batch_id = self._post_and_run(payload, max_tasks=0)
        self.assertEqual(response.status_code, 202)

        results_resp = self._get(f"/api/v1/evaluations/{batch_id}/results")
        self.assertEqual(results_resp.status_code, 409)
        self.assertEqual(results_resp.json()["error"], "not_ready")

    def test_two_questions_both_correct(self):
        payload = _base_payload(num_questions=2)
        response, batch_id = self._post_and_run(payload, answer="a")
        self.assertEqual(response.status_code, 202)

        results_resp = self._get(f"/api/v1/evaluations/{batch_id}/results")
        entry = results_resp.json()["results"]["gpt-4-turbo"][0]
        self.assertEqual(entry["overall_score"], 10.0)
        self.assertEqual(len(entry["details"]), 2)
        for detail in entry["details"]:
            self.assertEqual(detail["correct"], "true")


# ─── Multi-model, multi-iteration ─────────────────────────────────────


class ApiV1MultiModelMultiIterationTest(ApiV1IntegrationBase):
    def setUp(self):
        super().setUp()
        Model.objects.create(
            description="claude-3",
            api_url="https://api.anthropic.com/v1",
            api_key="sk-claude",
            user=self.user,
        )

    def test_total_tasks_equals_models_times_iterations(self):
        payload = _base_payload(
            models=["gpt-4-turbo", "claude-3"],
            iterations=3,
        )
        response = self._post(payload)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["total_tasks"], 6)

    def test_results_grouped_by_model_with_iterations(self):
        payload = _base_payload(
            num_questions=2,
            models=["gpt-4-turbo", "claude-3"],
            iterations=3,
        )
        response, batch_id = self._post_and_run(payload, answer="a")
        self.assertEqual(response.status_code, 202)

        results_resp = self._get(f"/api/v1/evaluations/{batch_id}/results")
        self.assertEqual(results_resp.status_code, 200)
        results = results_resp.json()["results"]
        self.assertEqual(set(results.keys()), {"gpt-4-turbo", "claude-3"})
        for model_name, iterations in results.items():
            self.assertEqual(len(iterations), 3)
            for i, entry in enumerate(iterations, 1):
                self.assertEqual(entry["iteration"], i)
                self.assertEqual(entry["overall_score"], 10.0)
                self.assertEqual(len(entry["details"]), 2)

    def test_history_models_used_includes_all(self):
        payload = _base_payload(
            models=["gpt-4-turbo", "claude-3"],
            iterations=1,
        )
        response, batch_id = self._post_and_run(payload)
        self.assertEqual(response.status_code, 202)

        history_resp = self._get(self.url)
        item = next(
            r for r in history_resp.json()["results"] if r["evaluation_id"] == batch_id
        )
        self.assertEqual(set(item["models_used"]), {"gpt-4-turbo", "claude-3"})


# ─── Ownership isolation ──────────────────────────────────────────────


class ApiV1OwnershipIsolationTest(ApiV1IntegrationBase):
    def setUp(self):
        super().setUp()
        self.other = CustomUser.objects.create_user(
            username="other",
            email="other@example.com",
            password="password123",
        )
        self.other_auth = {"HTTP_AUTHORIZATION": f"Bearer {self.other.api_token}"}

    def test_other_user_status_404(self):
        _, batch_id = self._post_and_run(_base_payload())
        resp = self.client.get(
            f"/api/v1/evaluations/{batch_id}/status", **self.other_auth
        )
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["error"], "not_found")

    def test_other_user_results_404(self):
        _, batch_id = self._post_and_run(_base_payload())
        resp = self.client.get(
            f"/api/v1/evaluations/{batch_id}/results", **self.other_auth
        )
        self.assertEqual(resp.status_code, 404)

    def test_other_user_history_empty(self):
        self._post_and_run(_base_payload())
        resp = self.client.get(self.url, **self.other_auth)
        self.assertEqual(resp.json()["count"], 0)


# ─── Error contract ───────────────────────────────────────────────────


class ApiV1ErrorContractTest(ApiV1IntegrationBase):
    def test_401_all_endpoints_without_token(self):
        cases = [
            ("GET", "/api/v1/models"),
            ("POST", "/api/v1/evaluations"),
            ("GET", "/api/v1/evaluations"),
            ("GET", "/api/v1/evaluations/eval-x/status"),
            ("GET", "/api/v1/evaluations/eval-x/results"),
        ]
        for method, path in cases:
            with self.subTest(method=method, path=path):
                if method == "POST":
                    resp = self.client.post(
                        path, data="{}", content_type="application/json"
                    )
                else:
                    resp = self.client.get(path)
                self.assertEqual(resp.status_code, 401)
                data = resp.json()
                self.assertIn("error", data)
                self.assertIn("message", data)
                self.assertEqual(data["error"], "unauthorized")

    def test_404_nonexistent_evaluation(self):
        for suffix in ["status", "results"]:
            with self.subTest(suffix=suffix):
                resp = self._get(f"/api/v1/evaluations/eval-nonexistent/{suffix}")
                self.assertEqual(resp.status_code, 404)
                self.assertEqual(resp.json()["error"], "not_found")

    def test_409_results_while_pending(self):
        _, batch_id = self._post_and_run(_base_payload(num_questions=2), max_tasks=0)
        resp = self._get(f"/api/v1/evaluations/{batch_id}/results")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["error"], "not_ready")

    def test_400_pagination_invalid(self):
        for params in ["?limit=abc", "?limit=0", "?offset=-1"]:
            with self.subTest(params=params):
                resp = self._get(f"{self.url}{params}")
                self.assertEqual(resp.status_code, 400)
                self.assertEqual(resp.json()["error"], "bad_request")

    def test_413_payload_too_large(self):
        payload = _base_payload()
        payload["exam"]["questions"][0]["question_text"] = "x" * (6 * 1024 * 1024)
        resp = self._post(payload)
        self.assertEqual(resp.status_code, 413)
        self.assertEqual(resp.json()["error"], "payload_too_large")

    def test_413_iterations_over_limit(self):
        payload = _base_payload()
        payload["iterations"] = 101
        resp = self._post(payload)
        self.assertEqual(resp.status_code, 413)

    def test_400_invalid_model(self):
        payload = _base_payload()
        payload["models"] = ["does-not-exist"]
        resp = self._post(payload)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "bad_request")

    def test_400_malformed_schema(self):
        resp = self._post({"exam": {}})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "bad_request")

    def test_400_invalid_correct_choices(self):
        # Zero correct
        payload = _base_payload()
        payload["exam"]["questions"][0]["choices"] = [
            {"choice_text": "A", "isCorrect": False},
            {"choice_text": "B", "isCorrect": False},
        ]
        resp = self._post(payload)
        self.assertEqual(resp.status_code, 400)

        # Two correct
        payload = _base_payload()
        payload["exam"]["questions"][0]["choices"] = [
            {"choice_text": "A", "isCorrect": True},
            {"choice_text": "B", "isCorrect": True},
        ]
        resp = self._post(payload)
        self.assertEqual(resp.status_code, 400)

    def test_405_method_not_allowed(self):
        resp = self.client.post(
            "/api/v1/models",
            data="{}",
            content_type="application/json",
            **self.auth,
        )
        self.assertEqual(resp.status_code, 405)
        self.assertEqual(resp.json()["error"], "method_not_allowed")

        resp = self.client.delete(self.url, **self.auth)
        self.assertEqual(resp.status_code, 405)

    def test_500_unhandled_exception_returns_shaped_error(self):
        """An unhandled (non-DRF) exception in a view must still return the
        contract error shape, not Django's default 500 HTML page."""
        with patch(
            "api.views.create_batch_evaluation",
            side_effect=RuntimeError("boom"),
        ):
            resp = self._post(_base_payload())
        self.assertEqual(resp.status_code, 500)
        data = resp.json()
        self.assertEqual(data["error"], "internal_error")
        self.assertIn("message", data)

    def test_503_orchestrator_enqueue_failure(self):
        """When the orchestrator cannot be enqueued, stubs are marked failed
        and a shaped 503 is returned."""
        with patch(
            "genaigrader.services.api_evaluation_service.async_task",
            side_effect=RuntimeError("broker down"),
        ):
            resp = self._post(_base_payload())
        self.assertEqual(resp.status_code, 503)
        data = resp.json()
        self.assertEqual(data["error"], "enqueue_failed")
        self.assertIn("message", data)
        self.assertTrue(
            Evaluation.objects.filter(
                status="failed", failed_reason="Orchestrator enqueue failed"
            ).exists()
        )


# ─── Idempotency and reuse ────────────────────────────────────────────


class ApiV1IdempotencyAndReuseTest(ApiV1IntegrationBase):
    def test_repost_same_external_id_reuses_exam(self):
        self._post_and_run(_base_payload(external_id="exam-123"))
        self._post_and_run(_base_payload(external_id="exam-123"))
        self.assertEqual(
            Exam.objects.filter(user=self.user, external_id="exam-123").count(),
            1,
        )

    def test_reuses_course_case_insensitive(self):
        self._post_and_run(_base_payload(external_id="exam-a"))
        payload = _base_payload(external_id="exam-b")
        payload["exam"]["course"] = "operating systems"
        self._post_and_run(payload)
        self.assertEqual(Course.objects.filter(user=self.user).count(), 1)

    def test_different_external_id_same_course(self):
        self._post_and_run(_base_payload(external_id="exam-1"))
        self._post_and_run(_base_payload(external_id="exam-2"))
        self.assertEqual(Course.objects.filter(user=self.user).count(), 1)
        self.assertEqual(Exam.objects.filter(user=self.user).count(), 2)


# ─── Failed pipeline ──────────────────────────────────────────────────


class ApiV1FailedPipelineTest(ApiV1IntegrationBase):
    def test_llm_failure_marks_evaluation_failed(self):
        response, batch_id = self._post_and_run(_base_payload(), llm_raises=True)
        self.assertEqual(response.status_code, 202)

        status_resp = self._get(f"/api/v1/evaluations/{batch_id}/status")
        status_data = status_resp.json()
        self.assertEqual(status_data["status"], "failed")
        self.assertEqual(status_data["progress"]["failed"], 1)
        self.assertEqual(status_data["progress"]["total"], 1)

        results_resp = self._get(f"/api/v1/evaluations/{batch_id}/results")
        self.assertEqual(results_resp.status_code, 200)
        entry = results_resp.json()["results"]["gpt-4-turbo"][0]
        self.assertEqual(entry["overall_score"], 0)
        self.assertEqual(entry["details"], [])

    def test_partial_failure(self):
        call_count = [0]

        def _fail_on_second(prompt):
            call_count[0] += 1
            if call_count[0] >= 2:
                raise RuntimeError("LLM unavailable")
            return iter(["a"])

        payload = _base_payload(num_questions=2)
        response, batch_id = self._post_and_run(
            payload, llm_side_effect=_fail_on_second
        )
        self.assertEqual(response.status_code, 202)

        status_resp = self._get(f"/api/v1/evaluations/{batch_id}/status")
        self.assertEqual(status_resp.json()["status"], "failed")

        results_resp = self._get(f"/api/v1/evaluations/{batch_id}/results")
        self.assertEqual(results_resp.status_code, 200)
        entry = results_resp.json()["results"]["gpt-4-turbo"][0]
        self.assertEqual(entry["overall_score"], 0)
        self.assertEqual(len(entry["details"]), 1)
