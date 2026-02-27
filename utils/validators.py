"""
Shared validators used across multiple apps.
"""
import re
from django.core.exceptions import ValidationError


def validate_password_strength(password: str) -> None:
    """
    Enforces: min 8 chars, at least 1 uppercase, at least 1 digit.
    """
    if len(password) < 8:
        raise ValidationError('Password must be at least 8 characters long.')
    if not re.search(r'[A-Z]', password):
        raise ValidationError('Password must contain at least one uppercase letter.')
    if not re.search(r'[0-9]', password):
        raise ValidationError('Password must contain at least one digit.')


def validate_hex_color(value: str) -> None:
    """Validates a CSS hex color string (e.g. #AABBCC or #ABC)."""
    if not re.match(r'^#([A-Fa-f0-9]{6}|[A-Fa-f0-9]{3})$', value):
        raise ValidationError(f"'{value}' is not a valid hex color. Use format #RRGGBB.")


def validate_subdomain(value: str) -> None:
    """
    Validates subdomain format: lowercase alphanumeric and hyphens,
    no leading/trailing hyphens, max 63 chars.
    """
    if not re.match(r'^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$', value):
        raise ValidationError(
            'Subdomain must be lowercase alphanumeric with hyphens (no leading/trailing hyphens).'
        )
