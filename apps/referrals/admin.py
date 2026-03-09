from django.contrib import admin

from .models import Referral, ReferralCode


@admin.register(ReferralCode)
class ReferralCodeAdmin(admin.ModelAdmin):
    list_display = ['code', 'tenant', 'created_at']
    search_fields = ['code', 'tenant__name']
    readonly_fields = ['code', 'tenant', 'created_at', 'updated_at']


@admin.register(Referral)
class ReferralAdmin(admin.ModelAdmin):
    list_display = ['referrer', 'referred', 'status', 'credit_amount', 'activated_at']
    list_filter = ['status']
    search_fields = ['referrer__name', 'referred__name']
    readonly_fields = ['created_at', 'updated_at']
