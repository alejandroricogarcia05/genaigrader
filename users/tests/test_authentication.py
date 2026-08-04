from django.test import RequestFactory, TestCase
from rest_framework.exceptions import AuthenticationFailed

from users.authentication import ApiTokenAuthentication
from users.models import CustomUser


class ApiTokenAuthenticationTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username="tokenuser",
            email="token@example.com",
            password="password123",
        )
        self.auth = ApiTokenAuthentication()

    def test_valid_token_authenticates(self):
        request = RequestFactory().get("/")
        request.META["HTTP_AUTHORIZATION"] = f"Token {self.user.api_token}"
        result = self.auth.authenticate(request)
        self.assertIsNotNone(result)
        user, _ = result
        self.assertEqual(user, self.user)

    def test_bearer_keyword_authenticates(self):
        request = RequestFactory().get("/")
        request.META["HTTP_AUTHORIZATION"] = f"Bearer {self.user.api_token}"
        result = self.auth.authenticate(request)
        self.assertIsNotNone(result)
        user, _ = result
        self.assertEqual(user, self.user)

    def test_missing_header_returns_none(self):
        request = RequestFactory().get("/")
        result = self.auth.authenticate(request)
        self.assertIsNone(result)

    def test_invalid_token_raises_error(self):
        request = RequestFactory().get("/")
        request.META["HTTP_AUTHORIZATION"] = "Token invalid_token_12345"
        with self.assertRaises(AuthenticationFailed):
            self.auth.authenticate(request)

    def test_invalid_bearer_token_raises_error(self):
        request = RequestFactory().get("/")
        request.META["HTTP_AUTHORIZATION"] = "Bearer invalid_token_12345"
        with self.assertRaises(AuthenticationFailed):
            self.auth.authenticate(request)

    def test_wrong_keyword_returns_none(self):
        request = RequestFactory().get("/")
        request.META["HTTP_AUTHORIZATION"] = f"Basic {self.user.api_token}"
        result = self.auth.authenticate(request)
        self.assertIsNone(result)

    def test_empty_token_after_keyword_returns_none(self):
        request = RequestFactory().get("/")
        request.META["HTTP_AUTHORIZATION"] = "Token "
        result = self.auth.authenticate(request)
        self.assertIsNone(result)

    def test_empty_bearer_token_returns_none(self):
        request = RequestFactory().get("/")
        request.META["HTTP_AUTHORIZATION"] = "Bearer "
        result = self.auth.authenticate(request)
        self.assertIsNone(result)

    def test_authenticate_header_returns_bearer(self):
        result = self.auth.authenticate_header(None)
        self.assertEqual(result, "Bearer")
