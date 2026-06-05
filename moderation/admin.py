from __future__ import annotations

from django.contrib import admin

from .models import BlockingReason, ModerationCard


@admin.register(BlockingReason)
class BlockingReasonAdmin(admin.ModelAdmin):
    list_display = ("code", "title", "hard_block", "is_active")
    list_filter = ("hard_block", "is_active")
    search_fields = ("code", "title")
    readonly_fields = ("created_at", "updated_at")
    actions = ("deactivate",)

    @admin.action(description="Deactivate selected reasons")
    def deactivate(self, request, queryset):
        queryset.update(is_active=False)

    def delete_model(self, request, obj):
        obj.delete()

    def delete_queryset(self, request, queryset):
        queryset.update(is_active=False)


@admin.register(ModerationCard)
class ModerationCardAdmin(admin.ModelAdmin):
    list_display = ("product_id", "status", "blocking_reason")
    list_filter = ("status", "blocking_reason")
    search_fields = ("product_id",)
