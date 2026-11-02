"""Private lifecycle cleanup; only removes the inactive member's assignments."""

import hashlib

from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.throttling import SimpleRateThrottle

from plane.app.views.nexus_onboarding import _check_internal_key
from plane.db.models import (
    Issue,
    IssueActivity,
    IssueAssignee,
    NexusMembershipCleanup,
    ProjectMember,
    User,
    Workspace,
    WorkspaceMember,
)


class CleanupRequest(serializers.Serializer):
    operation_id = serializers.UUIDField()
    workspace_slug = serializers.SlugField(max_length=255)
    plane_id = serializers.UUIDField()
    actor_id = serializers.UUIDField(allow_null=True, default=None)
    reason = serializers.CharField(max_length=1000, allow_blank=False)
    deactivate = serializers.BooleanField(default=False)


class MembershipStateRequest(serializers.Serializer):
    workspace_slug = serializers.SlugField(max_length=255)
    plane_id = serializers.UUIDField()


class MembershipServiceThrottle(SimpleRateThrottle):
    # Valid service requests must not exhaust the anonymous 30/minute budget
    # while draining a 50-member cleanup batch. Keep a separate bounded budget.
    scope = "nexus_membership_service"
    rate = "1000/minute"

    def get_cache_key(self, request, view):
        digest = hashlib.sha256(request.headers.get("X-Internal-Key", "").encode()).hexdigest()
        return f"{self.scope}:{digest}"


class NexusMembershipCleanupEndpoint(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get_throttles(self):
        if _check_internal_key(self.request):
            return [MembershipServiceThrottle()]
        return super().get_throttles()

    def get(self, request):
        # State reconciliation must still work when the deactivated member
        # happened to own the ordinary API key. This channel has its own key.
        if not _check_internal_key(request):
            raise PermissionDenied("Invalid internal key")
        serializer = MembershipStateRequest(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        member = WorkspaceMember.objects.filter(
            workspace__slug=values["workspace_slug"],
            member_id=values["plane_id"],
            workspace__deleted_at__isnull=True,
        ).first()
        if member is None:
            return Response({"error": "Workspace member not found"}, status=404)
        return Response(
            {
                "workspace_slug": values["workspace_slug"],
                "plane_id": str(values["plane_id"]),
                "is_active": member.is_active,
            }
        )

    def post(self, request):
        if not _check_internal_key(request):
            raise PermissionDenied("Invalid internal key")
        serializer = CleanupRequest(data=request.data)
        serializer.is_valid(raise_exception=True)
        if set(request.data) - set(serializer.fields):
            return Response({"error": "Unexpected cleanup fields"}, status=400)
        values = serializer.validated_data
        with transaction.atomic():
            workspace = Workspace.objects.select_for_update().filter(slug=values["workspace_slug"]).first()
            if workspace is None:
                return Response({"error": "Workspace not found"}, status=404)
            member = (
                WorkspaceMember.objects.select_for_update()
                .filter(
                    workspace=workspace,
                    member_id=values["plane_id"],
                )
                .first()
            )
            if member is None:
                return Response({"error": "Workspace member not found"}, status=404)
            existing = NexusMembershipCleanup.objects.filter(id=values["operation_id"]).first()
            # Canonical identity of the command excludes 'deactivate': normal
            # cleanup can reconcile a preceding emergency call with the same ID.
            command = {
                "workspace_slug": workspace.slug,
                "plane_id": str(values["plane_id"]),
                "actor_id": str(values["actor_id"]) if values["actor_id"] else None,
                "reason": values["reason"],
            }
            if existing and existing.request != command:
                return Response({"error": "Operation ID already used for another command"}, status=409)
            if existing and member.is_active:
                return Response({"error": "Member was reactivated; old cleanup cannot run"}, status=409)
            if existing:
                return Response(existing.result)
            if member.is_active and not values["deactivate"]:
                return Response({"error": "Member must be inactive before cleanup"}, status=409)
            if values["actor_id"] and not User.objects.filter(id=values["actor_id"]).exists():
                return Response({"error": "Audit actor not found"}, status=400)
            # Emergency shutdown is atomic with remote membership/assignment
            # invalidation. Ordinary shutdown uses the existing admin endpoint.
            if member.is_active:
                member.is_active = False
                member.save(update_fields=["is_active", "updated_at"])
            project_ids = list(
                ProjectMember.objects.filter(
                    workspace=workspace,
                    member_id=values["plane_id"],
                ).values_list("project_id", flat=True)
            )
            ProjectMember.objects.filter(workspace=workspace, member_id=values["plane_id"]).update(
                is_active=False,
                updated_at=timezone.now(),
            )
            assignments = list(
                IssueAssignee.objects.filter(
                    workspace=workspace,
                    assignee_id=values["plane_id"],
                    issue__deleted_at__isnull=True,
                ).values("id", "issue_id", "project_id")
            )
            # Serialize against normal issue updates; soft-delete only this
            # user's links so every other assignee, ID and creator is preserved.
            list(
                Issue.objects.select_for_update()
                .filter(
                    id__in=[row["issue_id"] for row in assignments],
                    workspace=workspace,
                )
                .values_list("id", flat=True)
            )
            stamp = timezone.now()
            IssueAssignee.objects.filter(id__in=[row["id"] for row in assignments]).update(
                deleted_at=stamp,
                updated_at=stamp,
            )
            # Incremental readers must also observe the assignment change.
            Issue.objects.filter(id__in=[row["issue_id"] for row in assignments], workspace=workspace).update(
                updated_at=stamp
            )
            for row in assignments:
                IssueActivity.objects.create(
                    workspace=workspace,
                    project_id=row["project_id"],
                    issue_id=row["issue_id"],
                    actor_id=values["actor_id"],
                    verb="updated",
                    field="assignees",
                    old_value=str(values["plane_id"]),
                    new_value=None,
                    old_identifier=values["plane_id"],
                    comment=f"Membership cleanup {values['operation_id']}: {values['reason']}",
                )
            result = {
                "operation_id": str(values["operation_id"]),
                **command,
                "project_ids": sorted(
                    {str(pid) for pid in project_ids} | {str(row["project_id"]) for row in assignments}
                ),
                "unassigned_work_item_ids": sorted({str(row["issue_id"]) for row in assignments}),
            }
            NexusMembershipCleanup.objects.create(
                id=values["operation_id"],
                workspace=workspace,
                member_id=values["plane_id"],
                request=command,
                result=result,
            )
            return Response(result)
