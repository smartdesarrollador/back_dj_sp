"""
Custom exceptions and DRF exception handler.
"""
from rest_framework import status
from rest_framework.views import exception_handler
from rest_framework.exceptions import APIException
from rest_framework.response import Response


# ─── Custom Exceptions ────────────────────────────────────────────────────────

class PlanLimitExceeded(APIException):
    """Raised when a tenant exceeds their plan's resource limit."""
    status_code = status.HTTP_402_PAYMENT_REQUIRED
    default_detail = 'You have reached the limit for your current plan. Please upgrade to continue.'
    default_code = 'plan_limit_exceeded'


class FeatureNotAvailable(APIException):
    """Raised when a feature is not available on the current plan."""
    status_code = status.HTTP_402_PAYMENT_REQUIRED
    default_detail = 'This feature is not available on your current plan.'
    default_code = 'feature_not_available'


class TenantNotFound(APIException):
    """Raised when no tenant can be resolved from the request."""
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = 'Tenant not found. Make sure the X-Tenant-Slug header is set correctly.'
    default_code = 'tenant_not_found'


class CrossTenantAccessDenied(APIException):
    """Raised when a user tries to access data from another tenant."""
    status_code = status.HTTP_403_FORBIDDEN
    default_detail = 'Access to resources from another tenant is not allowed.'
    default_code = 'cross_tenant_access_denied'


class MFARequired(APIException):
    """Raised when MFA validation is required to complete login."""
    status_code = status.HTTP_200_OK
    default_detail = 'MFA validation required.'
    default_code = 'mfa_required'


class InvalidToken(APIException):
    """Raised when a token (email verification, password reset) is invalid or expired."""
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = 'The token is invalid or has expired.'
    default_code = 'invalid_token'


# ─── Global Exception Handler ─────────────────────────────────────────────────

def custom_exception_handler(exc, context):
    """
    Returns consistent error response format:
    {
        "error": {
            "code": "error_code",
            "message": "Human readable message",
            "details": {...}  # optional field-level errors
        }
    }
    """
    response = exception_handler(exc, context)

    if response is not None:
        error_data = {
            'error': {
                'code': getattr(exc, 'default_code', 'error'),
                'message': _get_message(response.data),
            }
        }

        # Preserve field-level validation errors under 'details'
        if isinstance(response.data, dict) and any(
            isinstance(v, list) for v in response.data.values()
        ):
            error_data['error']['details'] = response.data

        response.data = error_data

    return response


def _get_message(data) -> str:
    if isinstance(data, dict):
        if 'detail' in data:
            return str(data['detail'])
        # Return first field error
        for key, value in data.items():
            if isinstance(value, list) and value:
                return f"{key}: {value[0]}"
        return 'Validation error'
    if isinstance(data, list) and data:
        return str(data[0])
    return str(data)
