from typing import Any

from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.messages import success
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import TemplateView


class ApiTokenView(LoginRequiredMixin, TemplateView):
    """View to display and manage the user API token and API base URL."""

    template_name = "api_token.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Add API configuration details to the template context.

        Returns:
            dict[str, Any]: Template context including 'api_url' pointing to /api/v1/.
        """
        context = super().get_context_data(**kwargs)
        context["api_url"] = self.request.build_absolute_uri("/api/v1/")
        return context

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """Rotate user API token and redirect to the API token view.

        Returns:
            HttpResponse: Redirect to the API token page.
        """
        if request.POST.get("action") == "rotate":
            request.user.rotate_api_token()
            success(request, "API token rotated successfully.")
        return redirect(reverse_lazy("api_token"))
