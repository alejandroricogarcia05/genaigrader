from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from api.serializers import CreateEvaluationSerializer
from genaigrader.services.api_evaluation_service import (
    MAX_ITERATIONS,
    MAX_PAYLOAD_SIZE,
    OrchestratorEnqueueError,
    create_batch_evaluation,
    get_batch_history,
    get_batch_results,
    get_batch_status,
)
from genaigrader.services.get_models_service import get_model_names_for_user


class ModelsView(APIView):
    def get(self, request):
        return Response({"models": get_model_names_for_user(request.user)})


class EvaluationsView(APIView):
    def get(self, request):
        limit = request.query_params.get("limit", 50)
        offset = request.query_params.get("offset", 0)
        try:
            limit = int(limit)
            offset = int(offset)
        except (ValueError, TypeError):
            return Response(
                {"error": "bad_request", "message": "Invalid limit or offset"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if limit < 1 or offset < 0:
            return Response(
                {"error": "bad_request", "message": "Invalid limit or offset"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(get_batch_history(request.user, limit, offset))

    def post(self, request):
        if len(request.body) > MAX_PAYLOAD_SIZE:
            return Response(
                {
                    "error": "payload_too_large",
                    "message": "Payload exceeds the allowed size limit",
                },
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )

        serializer = CreateEvaluationSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"error": "bad_request", "message": "Malformed or incomplete schema"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        data = serializer.validated_data
        if data["iterations"] > MAX_ITERATIONS:
            return Response(
                {
                    "error": "payload_too_large",
                    "message": f"Iterations limit exceeded (max {MAX_ITERATIONS})",
                },
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )

        try:
            batch, total_tasks = create_batch_evaluation(request.user, data)
        except OrchestratorEnqueueError:
            return Response(
                {
                    "error": "enqueue_failed",
                    "message": "Failed to queue evaluation tasks",
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except ValueError as e:
            return Response(
                {"error": "bad_request", "message": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "evaluation_id": batch.public_id,
                "status": "pending",
                "total_tasks": total_tasks,
            },
            status=status.HTTP_202_ACCEPTED,
        )


class EvaluationStatusView(APIView):
    def get(self, request, evaluation_id):
        data = get_batch_status(request.user, evaluation_id)
        if data is None:
            return Response(
                {"error": "not_found", "message": "Evaluation not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(data)


class EvaluationResultsView(APIView):
    def get(self, request, evaluation_id):
        data = get_batch_results(request.user, evaluation_id)
        if data is None:
            return Response(
                {"error": "not_found", "message": "Evaluation not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        if data == "not_ready":
            return Response(
                {
                    "error": "not_ready",
                    "message": "The evaluation is still being processed.",
                },
                status=status.HTTP_409_CONFLICT,
            )
        return Response(data)
