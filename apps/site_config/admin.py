from django.contrib import admin

from apps.site_config.models import FooterConfig, FooterLink


class FooterLinkInline(admin.TabularInline):
    model = FooterLink
    extra = 0


@admin.register(FooterConfig)
class FooterConfigAdmin(admin.ModelAdmin):
    inlines = [FooterLinkInline]
