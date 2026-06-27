from django.contrib import admin

from apps.vault.models import VaultItem, VaultKey


@admin.register(VaultKey)
class VaultKeyAdmin(admin.ModelAdmin):
    list_display = ('user', 'tenant', 'recovery_used_at', 'created_at')
    search_fields = ('user__email',)
    # Never expose key material in the admin.
    readonly_fields = (
        'salt', 'wrapped_dek', 'master_verifier',
        'recovery_salt', 'wrapped_dek_recovery', 'recovery_verifier',
    )


@admin.register(VaultItem)
class VaultItemAdmin(admin.ModelAdmin):
    list_display = ('title', 'item_type', 'user', 'tenant', 'favorite', 'created_at')
    list_filter = ('item_type', 'favorite')
    search_fields = ('title', 'user__email')
    readonly_fields = ('data_ciphertext',)
