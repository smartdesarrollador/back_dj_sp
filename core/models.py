"""
Base abstract models shared across all apps.
"""
import uuid
from django.db import models


class BaseModel(models.Model):
    """
    Abstract base model providing UUID primary key and audit timestamps.
    All domain models should inherit from this class.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

    def __str__(self) -> str:
        return str(self.id)
