"""Receipts make lifecycle cleanup replayable after a lost HTTP response."""

import uuid
from django.db import models


class NexusMembershipCleanup(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey("db.Workspace", on_delete=models.RESTRICT)
    member = models.ForeignKey("db.User", on_delete=models.RESTRICT)
    request = models.JSONField()
    result = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "nexus_membership_cleanups"
