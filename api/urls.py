from django.urls import path

from api.views import (
    EvaluationResultsView,
    EvaluationStatusView,
    EvaluationsView,
    ModelsView,
)

urlpatterns = [
    path("models", ModelsView.as_view(), name="api_models"),
    path("evaluations", EvaluationsView.as_view(), name="api_evaluations"),
    path(
        "evaluations/<str:evaluation_id>/status",
        EvaluationStatusView.as_view(),
        name="api_evaluation_status",
    ),
    path(
        "evaluations/<str:evaluation_id>/results",
        EvaluationResultsView.as_view(),
        name="api_evaluation_results",
    ),
]
