from django.urls import path

from api.views import ModelsView

urlpatterns = [
    path("models/", ModelsView.as_view(), name="api_models"),
]
