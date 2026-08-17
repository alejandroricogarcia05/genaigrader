import logging

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as _drf_exception_handler

logger = logging.getLogger(__name__)


def exception_handler(exc, context):
    response = _drf_exception_handler(exc, context)
    if response is not None:
        error_type = _error_type_for_status(response.status_code)
        message = _extract_message(response.data)
        response.data = {"error": error_type, "message": message}
        return response

    # Unhandled exception: DRF returned None, which would otherwise become
    # Django's default 500 HTML page. Shape it per the API contract and log
    # the traceback so debugging information is still available server-side.
    logger.exception("Unhandled exception in API view: %s", exc, exc_info=True)
    return Response(
        {"error": "internal_error", "message": "Internal server error"},
        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


def _extract_message(data):
    if isinstance(data, dict):
        detail = data.get("detail")
        if isinstance(detail, str):
            return detail
        if isinstance(detail, list) and detail:
            return str(detail[0])
        for value in data.values():
            if isinstance(value, list) and value:
                return str(value[0])
            if isinstance(value, str):
                return value
    if isinstance(data, list) and data:
        return str(data[0])
    return str(data)


def _error_type_for_status(code):
    mapping = {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        405: "method_not_allowed",
        409: "conflict",
        413: "payload_too_large",
        500: "internal_error",
    }
    return mapping.get(code, "error")
