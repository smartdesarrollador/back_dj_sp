"""Custom DRF throttle classes for rate limiting authentication and plan-based API access."""
from rest_framework.throttling import AnonRateThrottle, SimpleRateThrottle


class LoginRateThrottle(AnonRateThrottle):
    """5 intentos/minuto por IP — protege contra brute force en login."""
    scope = 'login'


class RegisterRateThrottle(AnonRateThrottle):
    """3 registros/hora por IP — limita spam de cuentas."""
    scope = 'register'


class MFARateThrottle(AnonRateThrottle):
    """5 intentos/minuto por IP — protege MFA validate y recovery."""
    scope = 'mfa'


class ForgotPasswordRateThrottle(AnonRateThrottle):
    """5 intentos/hora por IP — limita enumeración de emails."""
    scope = 'forgot_password'


class PlanBasedUserThrottle(SimpleRateThrottle):
    """
    Throttle dinámico según plan del tenant.
    - free:         1 000/hora
    - starter:      5 000/hora
    - professional: 10 000/hora
    - enterprise:   ilimitado (None)
    """
    PLAN_RATES = {
        'free': '1000/hour',
        'starter': '5000/hour',
        'professional': '10000/hour',
        'enterprise': None,
    }

    def get_rate(self) -> str:
        return '1000/hour'  # default; overridden dynamically in allow_request

    def get_cache_key(self, request, view) -> str | None:
        if not request.user or not request.user.is_authenticated:
            return None  # no aplica a anónimos
        return f'throttle_plan_user_{request.user.pk}'

    def allow_request(self, request, view) -> bool:
        if not request.user or not request.user.is_authenticated:
            return True
        try:
            plan = request.user.tenant.plan
        except AttributeError:
            return True
        rate_str = self.PLAN_RATES.get(plan, '1000/hour')
        if rate_str is None:
            return True  # enterprise: ilimitado
        self.rate = rate_str
        self.num_requests, self.duration = self.parse_rate(self.rate)
        return super().allow_request(request, view)
