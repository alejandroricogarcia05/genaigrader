from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

User = get_user_model()


class ApiTokenViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="tokenuser",
            email="token@example.com",
            password="password123",
        )
        self.url = reverse("api_token")

    def test_get_redirects_when_unauthenticated(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)

    def test_get_renders_token_when_authenticated(self):
        self.client.login(username="tokenuser", password="password123")
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.user.api_token)

    def test_rotate_without_csrf_returns_403(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.login(username="tokenuser", password="password123")
        old_token = self.user.api_token
        response = csrf_client.post(self.url, {"action": "rotate"})
        self.assertEqual(response.status_code, 403)
        self.user.refresh_from_db()
        self.assertEqual(self.user.api_token, old_token)

    def test_rotate_with_csrf_changes_token(self):
        self.client.login(username="tokenuser", password="password123")
        old_token = self.user.api_token
        response = self.client.post(self.url, {"action": "rotate"})
        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertNotEqual(self.user.api_token, old_token)

    def test_old_token_invalid_after_rotate(self):
        self.client.login(username="tokenuser", password="password123")
        old_token = self.user.api_token
        self.client.post(self.url, {"action": "rotate"})
        self.user.refresh_from_db()
        self.assertNotEqual(self.user.api_token, old_token)
        self.assertFalse(User.objects.filter(api_token=old_token).exists())
