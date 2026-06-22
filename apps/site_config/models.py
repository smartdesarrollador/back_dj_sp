from django.db import models


class FooterConfig(models.Model):
    tagline       = models.CharField(max_length=200, blank=True, default='')
    email         = models.EmailField(blank=True, default='')
    whatsapp      = models.CharField(max_length=30, blank=True, default='')
    phone         = models.CharField(max_length=30, blank=True, default='')
    facebook_url  = models.URLField(blank=True, default='')
    instagram_url = models.URLField(blank=True, default='')
    youtube_url   = models.URLField(blank=True, default='')
    linkedin_url  = models.URLField(blank=True, default='')
    updated_at    = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Footer Config'

    def __str__(self) -> str:
        return 'Footer Configuration'


class FooterLink(models.Model):
    config = models.ForeignKey(FooterConfig, on_delete=models.CASCADE, related_name='links')
    label  = models.CharField(max_length=100)
    url    = models.CharField(max_length=200)
    order  = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['order']

    def __str__(self) -> str:
        return self.label
