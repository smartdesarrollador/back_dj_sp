import hashlib
import os

from django.db import models

from core.models import BaseModel


PLATFORM_CHOICES = [
    ('windows', 'Windows'),
    ('macos', 'macOS'),
    ('linux', 'Linux'),
]

ALLOWED_EXTENSIONS = {'.exe', '.msi', '.dmg'}
MAX_FILE_SIZE_BYTES = 500 * 1024 * 1024  # 500 MB


def _release_upload_path(instance, filename):
    return f'releases/{instance.platform}/{instance.version}/{filename}'


class DesktopRelease(BaseModel):
    version = models.CharField(max_length=50, db_index=True)
    platform = models.CharField(max_length=10, choices=PLATFORM_CHOICES)
    file = models.FileField(upload_to=_release_upload_path)
    file_name = models.CharField(max_length=255, editable=False)
    file_size = models.BigIntegerField(editable=False)
    sha256 = models.CharField(max_length=64, editable=False)
    release_notes = models.TextField(blank=True)
    is_published = models.BooleanField(default=False, db_index=True)
    download_count = models.PositiveBigIntegerField(default=0)

    class Meta:
        db_table = 'desktop_releases'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['platform', 'is_published'], name='release_platform_pub_idx'),
            models.Index(fields=['version', 'platform'], name='release_version_platform_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['version', 'platform'],
                name='unique_release_version_platform',
            )
        ]

    def __str__(self) -> str:
        return f'{self.version} ({self.platform})'

    def save(self, *args, **kwargs) -> None:
        if self.file and not self.file_name:
            self.file_name = os.path.basename(self.file.name)
            self.file_size = self.file.size
            self.sha256 = self._compute_sha256()
        super().save(*args, **kwargs)

    def _compute_sha256(self) -> str:
        h = hashlib.sha256()
        self.file.seek(0)
        for chunk in iter(lambda: self.file.read(8192), b''):
            h.update(chunk)
        self.file.seek(0)
        return h.hexdigest()
