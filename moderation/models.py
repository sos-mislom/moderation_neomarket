from __future__ import annotations

import uuid

from django.db import models


class BlockingReason(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=64, unique=True)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    hard_block = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["hard_block", "title"]

    def __str__(self) -> str:
        return self.title

    def delete(self, using=None, keep_parents=False):
        self.is_active = False
        self.save(update_fields=["is_active", "updated_at"])


class ModerationCard(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING"
        IN_REVIEW = "IN_REVIEW"
        MODERATED = "MODERATED"
        BLOCKED = "BLOCKED"
        HARD_BLOCKED = "HARD_BLOCKED"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product_id = models.UUIDField()
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.PENDING)
    blocking_reason = models.ForeignKey(
        BlockingReason,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="cards",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
