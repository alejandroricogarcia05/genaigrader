from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.messages import success
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import TemplateView


class ApiTokenView(LoginRequiredMixin, TemplateView):
    template_name = "api_token.html"

    def post(self, request, *args, **kwargs):
        if request.POST.get("action") == "rotate":
            request.user.rotate_api_token()
            success(request, "API token rotated successfully.")
        return redirect(reverse_lazy("api_token"))
