import base64
import hashlib
import hmac
import json
import secrets
import time

from django.conf import settings
from django.db import models

from core.models import BaseModel

PAID_PLANS = ('starter', 'professional', 'enterprise')


def _generate_license_key() -> str:
    raw = secrets.token_hex(8).upper()
    return f"{raw[0:4]}-{raw[4:8]}-{raw[8:12]}-{raw[12:16]}"


def _build_activation_token(license_key: str, hardware_id: str, user_id: str) -> str:
    payload = json.dumps(
        {"lk": license_key, "hid": hardware_id, "iat": int(time.time()), "uid": user_id, "v": 1},
        separators=(',', ':'),
        sort_keys=True,
    )
    payload_b64 = base64.urlsafe_b64encode(payload.encode()).rstrip(b'=').decode()
    secret = settings.LICENSE_SIGNING_SECRET.encode()
    sig = hmac.new(secret, payload_b64.encode(), hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).rstrip(b'=').decode()
    return f"{payload_b64}.{sig_b64}"


class DesktopAppLicense(BaseModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='desktop_license',
    )
    license_key = models.CharField(max_length=19, unique=True)
    hardware_id = models.CharField(max_length=64, blank=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='created_licenses',
    )

    class Meta:
        db_table = 'desktop_app_licenses'
        indexes = [
            models.Index(fields=['license_key'], name='dal_license_key_idx'),
            models.Index(fields=['user', 'is_active'], name='dal_user_active_idx'),
        ]

    def __str__(self) -> str:
        return f"{self.license_key} ({self.user.email})"

    @property
    def is_activated(self) -> bool:
        return bool(self.hardware_id and self.activated_at)

    @property
    def status(self) -> str:
        if not self.is_active:
            return 'revoked'
        if self.is_activated:
            return 'active'
        return 'pending'

    def build_activation_token(self, hardware_id: str) -> str:
        return _build_activation_token(self.license_key, hardware_id, str(self.user_id))
