from rest_framework.response import Response
from rest_framework.views import APIView

from genaigrader.services.get_models_service import get_enabled_model_names


class ModelsView(APIView):
    def get(self, request):
        return Response({"models": get_enabled_model_names(request.user)})
