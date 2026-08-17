import json

from django.test import Client, TestCase

from genaigrader.models import BatchEvaluation, Course, Exam, Model, QuestionEvaluation
from users.models import CustomUser


def _base_payload(external_id="exam-123"):
    return {
        "exam": {
            "external_id": external_id,
            "course": "Operating Systems",
            "title": "OS Exam",
            "questions": [
                {
                    "question_text": "What is a pointer?",
                    "choices": [
                        {"choice_text": "A memory address", "isCorrect": True},
                        {"choice_text": "A primitive type", "isCorrect": False},
                    ],
                }
            ],
        },
        "models": ["gpt-4-turbo"],
        "iterations": 1,
    }


class ApiEvaluationsBaseTest(TestCase):
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

    def _mark_completed(self, evaluation_id, grade=8.5):
        batch = BatchEvaluation.objects.get(public_id=evaluation_id)
        for evaluation in batch.evaluations.all():
            evaluation.status = "completed"
            evaluation.grade = grade
            evaluation.save()
            for question in batch.exam.question_set.all():
                QuestionEvaluation.objects.create(
                    evaluation=evaluation,
                    question=question,
                    question_option=question.correct_option,
                    response_text="a",
                    is_correct=True,
                    question_time=1.0,
                )


class ApiEvaluationsCreateTest(ApiEvaluationsBaseTest):
    def test_post_creates_batch_and_returns_202(self):
        payload = _base_payload()
        payload["iterations"] = 2
        response = self._post(payload)
        self.assertEqual(response.status_code, 202)
        data = response.json()
        self.assertTrue(data["evaluation_id"].startswith("eval-"))
        self.assertEqual(data["status"], "pending")
        self.assertEqual(data["total_tasks"], 2)

    def test_post_auto_creates_course(self):
        self._post(_base_payload())
        self.assertTrue(
            Course.objects.filter(name="Operating Systems", user=self.user).exists()
        )

    def test_post_reuses_exam_with_same_external_id(self):
        self._post(_base_payload())
        self._post(_base_payload())
        self.assertEqual(
            Exam.objects.filter(user=self.user, external_id="exam-123").count(), 1
        )

    def test_post_reuses_existing_course_case_insensitive(self):
        self._post(_base_payload())
        payload = _base_payload(external_id="exam-other")
        payload["exam"]["course"] = "operating systems"
        self._post(payload)
        self.assertEqual(
            Course.objects.filter(user=self.user).count(),
            1,
        )

    def test_post_invalid_schema_returns_400(self):
        response = self._post({"exam": {}})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "bad_request")

    def test_post_zero_correct_choices_returns_400(self):
        payload = _base_payload()
        payload["exam"]["questions"][0]["choices"] = [
            {"choice_text": "A", "isCorrect": False},
            {"choice_text": "B", "isCorrect": False},
        ]
        response = self._post(payload)
        self.assertEqual(response.status_code, 400)

    def test_post_iterations_over_limit_returns_413(self):
        payload = _base_payload()
        payload["iterations"] = 101
        response = self._post(payload)
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["error"], "payload_too_large")

    def test_post_invalid_model_returns_400(self):
        payload = _base_payload()
        payload["models"] = ["does-not-exist"]
        response = self._post(payload)
        self.assertEqual(response.status_code, 400)

    def test_post_missing_auth_returns_401(self):
        response = self.client.post(
            self.url,
            data=json.dumps(_base_payload()),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)


class ApiEvaluationsStatusTest(ApiEvaluationsBaseTest):
    def test_get_status_returns_progress(self):
        response = self._post(_base_payload())
        evaluation_id = response.json()["evaluation_id"]
        status_response = self.client.get(
            f"/api/v1/evaluations/{evaluation_id}/status", **self.auth
        )
        self.assertEqual(status_response.status_code, 200)
        data = status_response.json()
        self.assertEqual(data["evaluation_id"], evaluation_id)
        self.assertIn(data["status"], ["pending", "processing"])
        self.assertEqual(data["progress"]["total"], 1)

    def test_get_status_completed(self):
        response = self._post(_base_payload())
        evaluation_id = response.json()["evaluation_id"]
        self._mark_completed(evaluation_id)
        status_response = self.client.get(
            f"/api/v1/evaluations/{evaluation_id}/status", **self.auth
        )
        data = status_response.json()
        self.assertEqual(data["status"], "completed")
        self.assertEqual(data["progress"]["completed"], 1)

    def test_get_status_not_found_returns_404(self):
        response = self.client.get(
            "/api/v1/evaluations/eval-nonexistent/status", **self.auth
        )
        self.assertEqual(response.status_code, 404)

    def test_get_status_other_user_returns_404(self):
        response = self._post(_base_payload())
        evaluation_id = response.json()["evaluation_id"]
        other = CustomUser.objects.create_user(
            username="other", email="other@example.com", password="password123"
        )
        other_response = self.client.get(
            f"/api/v1/evaluations/{evaluation_id}/status",
            HTTP_AUTHORIZATION=f"Bearer {other.api_token}",
        )
        self.assertEqual(other_response.status_code, 404)


class ApiEvaluationsResultsTest(ApiEvaluationsBaseTest):
    def test_get_results_while_running_returns_409(self):
        response = self._post(_base_payload())
        evaluation_id = response.json()["evaluation_id"]
        results_response = self.client.get(
            f"/api/v1/evaluations/{evaluation_id}/results", **self.auth
        )
        self.assertEqual(results_response.status_code, 409)
        self.assertEqual(results_response.json()["error"], "not_ready")

    def test_get_results_completed_returns_200(self):
        response = self._post(_base_payload())
        evaluation_id = response.json()["evaluation_id"]
        self._mark_completed(evaluation_id, grade=8.5)
        results_response = self.client.get(
            f"/api/v1/evaluations/{evaluation_id}/results", **self.auth
        )
        self.assertEqual(results_response.status_code, 200)
        data = results_response.json()
        self.assertEqual(data["evaluation_id"], evaluation_id)
        self.assertIn("gpt-4-turbo", data["results"])
        entry = data["results"]["gpt-4-turbo"][0]
        self.assertEqual(entry["iteration"], 1)
        self.assertEqual(entry["overall_score"], 8.5)
        detail = entry["details"][0]
        self.assertEqual(detail["question_id"], "q1")
        self.assertEqual(detail["selected_option"], "a")
        self.assertEqual(detail["correct"], "true")

    def test_get_results_not_found_returns_404(self):
        response = self.client.get(
            "/api/v1/evaluations/eval-nonexistent/results", **self.auth
        )
        self.assertEqual(response.status_code, 404)


class ApiEvaluationsHistoryTest(ApiEvaluationsBaseTest):
    def test_get_history_returns_list(self):
        self._post(_base_payload())
        response = self.client.get(self.url, **self.auth)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("count", data)
        self.assertIn("results", data)
        self.assertGreaterEqual(data["count"], 1)
        item = data["results"][0]
        self.assertIn("evaluation_id", item)
        self.assertIn("created_at", item)
        self.assertIn("status", item)
        self.assertEqual(item["models_used"], ["gpt-4-turbo"])

    def test_get_history_pagination(self):
        for i in range(3):
            self._post(_base_payload(external_id=f"exam-{i}"))
        response = self.client.get(f"{self.url}?limit=2&offset=0", **self.auth)
        data = response.json()
        self.assertEqual(len(data["results"]), 2)
        self.assertIsNotNone(data["next"])
        self.assertIsNone(data["previous"])

    def test_get_history_invalid_params_returns_400(self):
        response = self.client.get(f"{self.url}?limit=abc", **self.auth)
        self.assertEqual(response.status_code, 400)

    def test_get_history_scoped_to_user(self):
        self._post(_base_payload())
        other = CustomUser.objects.create_user(
            username="other", email="other@example.com", password="password123"
        )
        response = self.client.get(
            self.url, HTTP_AUTHORIZATION=f"Bearer {other.api_token}"
        )
        self.assertEqual(response.json()["count"], 0)
