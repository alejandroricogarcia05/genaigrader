from django.test import Client, TestCase

from genaigrader.models import Model
from users.models import CustomUser


class ApiModelsViewTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = CustomUser.objects.create_user(
            username="apiuser",
            email="api@example.com",
            password="password123",
        )
        self.other_user = CustomUser.objects.create_user(
            username="otheruser",
            email="other@example.com",
            password="password123",
        )
        self.local_model = Model.objects.create(description="llama3.2:1b")
        self.user_external = Model.objects.create(
            description="gpt-4-turbo",
            api_url="https://api.openai.com/v1",
            api_key="sk-test",
            user=self.user,
        )
        self.other_external = Model.objects.create(
            description="claude-3",
            api_url="https://api.anthropic.com/v1",
            api_key="sk-other",
            user=self.other_user,
        )
        self.url = "/api/v1/models"

    def test_bearer_token_returns_200(self):
        response = self.client.get(
            self.url, HTTP_AUTHORIZATION=f"Bearer {self.user.api_token}"
        )
        self.assertEqual(response.status_code, 200)

    def test_token_keyword_returns_200(self):
        response = self.client.get(
            self.url, HTTP_AUTHORIZATION=f"Token {self.user.api_token}"
        )
        self.assertEqual(response.status_code, 200)

    def test_response_contains_local_and_own_external_models(self):
        response = self.client.get(
            self.url, HTTP_AUTHORIZATION=f"Bearer {self.user.api_token}"
        )
        data = response.json()
        self.assertIn("models", data)
        self.assertIn("llama3.2:1b", data["models"])
        self.assertIn("gpt-4-turbo", data["models"])

    def test_response_excludes_other_users_external_models(self):
        response = self.client.get(
            self.url, HTTP_AUTHORIZATION=f"Bearer {self.user.api_token}"
        )
        data = response.json()
        self.assertNotIn("claude-3", data["models"])

    def test_models_are_sorted_alphabetically(self):
        response = self.client.get(
            self.url, HTTP_AUTHORIZATION=f"Bearer {self.user.api_token}"
        )
        data = response.json()
        self.assertEqual(data["models"], sorted(data["models"]))

    def test_missing_header_returns_401(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 401)
        data = response.json()
        self.assertIn("error", data)
        self.assertIn("message", data)
        self.assertEqual(data["error"], "unauthorized")

    def test_invalid_token_returns_401(self):
        response = self.client.get(
            self.url, HTTP_AUTHORIZATION="Bearer invalid_token_xyz"
        )
        self.assertEqual(response.status_code, 401)
        data = response.json()
        self.assertIn("error", data)
        self.assertIn("message", data)
        self.assertEqual(data["error"], "unauthorized")

    def test_wrong_keyword_returns_401(self):
        response = self.client.get(
            self.url, HTTP_AUTHORIZATION=f"Basic {self.user.api_token}"
        )
        self.assertEqual(response.status_code, 401)
        data = response.json()
        self.assertEqual(data["error"], "unauthorized")

    def test_post_method_not_allowed(self):
        response = self.client.post(
            self.url,
            data="{}",
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.user.api_token}",
        )
        self.assertEqual(response.status_code, 405)
        data = response.json()
        self.assertIn("error", data)
        self.assertIn("message", data)
        self.assertEqual(data["error"], "method_not_allowed")

    def test_empty_models_list(self):
        Model.objects.all().delete()
        response = self.client.get(
            self.url, HTTP_AUTHORIZATION=f"Bearer {self.user.api_token}"
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["models"], [])
