from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from users.models import CustomUser


class ApiTokenAuthentication(BaseAuthentication):
    keywords = ("Bearer", "Token")

    def authenticate(self, request):
        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        token = None
        for kw in self.keywords:
            if auth_header.startswith(kw):
                token = auth_header[len(kw) + 1 :].strip()
                break
        if token is None:
            return None
        if not token:
            return None

        try:
            user = CustomUser.objects.get(api_token=token)
        except CustomUser.DoesNotExist:
            raise AuthenticationFailed("Invalid token")

        return (user, None)

    def authenticate_header(self, request):
        return "Bearer"
