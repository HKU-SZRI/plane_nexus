# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Aggregate recorded work-item activity without scanning per-item APIs."""

from datetime import timedelta

from django.db.models import Count
from django.db.models.functions import Greatest
from django.utils import timezone
from rest_framework import serializers
from rest_framework.permissions import BasePermission
from rest_framework.response import Response

from plane.app.views import BaseAPIView
from plane.app.permissions.base import is_trusted_nexus_call
from plane.db.models import IssueActivity, IssueAssignee, Project, WorkspaceMember


class MemberActivityReportPermission(BasePermission):
    """Global activity evidence is restricted to admins or scoped Nexus calls."""

    def has_permission(self, request, view):
        if is_trusted_nexus_call(request):
            return True
        if request.user.is_anonymous:
            return False
        return WorkspaceMember.objects.filter(
            workspace__slug=view.workspace_slug,
            member=request.user,
            role=20,
            is_active=True,
        ).exists()


class MemberActivityReportInput(serializers.Serializer):
    start_at = serializers.DateTimeField()
    end_at = serializers.DateTimeField()
    project_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    include_light_activity = serializers.BooleanField(default=False)

    def validate(self, data):
        start, end = data["start_at"], data["end_at"]
        if start >= end or end - start > timedelta(days=60):
            raise serializers.ValidationError("Report window must be positive and at most 60 days")
        if end > timezone.now() + timedelta(seconds=60):
            raise serializers.ValidationError("Report cutoff cannot be in the future")
        return data


class WorkspaceMemberActivityEndpoint(BaseAPIView):
    permission_classes = [MemberActivityReportPermission]
    # Primary DB avoids adding replica lag to the activity worker's own delay.
    use_read_replica = False

    def post(self, request, slug):
        serializer = MemberActivityReportInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        start, end = data["start_at"], data["end_at"]
        project_id = data["project_id"]
        if project_id and not Project.objects.filter(id=project_id, workspace__slug=slug).exists():
            return Response({"error": "Project not found in workspace"}, status=404)

        # Start from the membership roster, so never-active members are included.
        # Nexus roles are independent of Plane roles: include every enabled human.
        members = (
            WorkspaceMember.objects.filter(
                workspace__slug=slug,
                is_active=True,
                member__is_active=True,
                member__is_bot=False,
                created_at__lt=end,
            )
            .select_related("member")
            .order_by("member_id")
        )
        if project_id:
            members = (
                members.filter(
                    member__member_project__project_id=project_id,
                    member__member_project__is_active=True,
                    member__member_project__deleted_at__isnull=True,
                )
                .annotate(scope_started_at=Greatest("created_at", "member__member_project__created_at"))
                .filter(scope_started_at__lt=end)
                .distinct()
            )
        members = list(members)
        ids = [membership.member_id for membership in members]
        # Keep historical evidence for archived/soft-deleted work items/projects.
        # Activity rows themselves follow Plane's standard soft-deletion manager.
        activities = IssueActivity.objects.filter(
            workspace__slug=slug,
            actor_id__in=ids,
            created_at__lt=end,
        )
        if project_id:
            activities = activities.filter(project_id=project_id)
        if not data["include_light_activity"]:
            activities = activities.exclude(field__in=["vote", "reaction", "draft"])
        counts = dict(
            activities.filter(created_at__gte=start)
            .order_by()
            .values("actor_id")
            .annotate(count=Count("id"))
            .values_list("actor_id", "count")
        )
        latest = {
            row["actor_id"]: row
            for row in activities.order_by("actor_id", "-created_at", "-id")
            .distinct("actor_id")
            .values(
                "actor_id",
                "created_at",
                "project_id",
                "issue_id",
                "project__identifier",
                "issue__sequence_id",
            )
        }
        assignments = IssueAssignee.objects.filter(
            workspace__slug=slug,
            assignee_id__in=ids,
            issue__deleted_at__isnull=True,
            issue__is_draft=False,
            issue__archived_at__isnull=True,
            project__archived_at__isnull=True,
            project__deleted_at__isnull=True,
        ).exclude(issue__state__group__in=["completed", "cancelled", "triage"])
        if project_id:
            assignments = assignments.filter(project_id=project_id)
        open_counts = dict(
            assignments.order_by()
            .values("assignee_id")
            .annotate(count=Count("issue_id", distinct=True))
            .values_list("assignee_id", "count")
        )
        rows = []
        for membership in members:
            member = membership.member
            last = latest.get(member.id)
            identifier = None
            if last and last["issue__sequence_id"] is not None:
                identifier = f"{last['project__identifier']}-{last['issue__sequence_id']}"
            rows.append(
                {
                    "member_id": str(member.id),
                    "display_name": member.display_name or member.first_name or member.email,
                    "email": member.email,
                    "membership_started_at": getattr(membership, "scope_started_at", membership.created_at),
                    "activity_count_in_window": counts.get(member.id, 0),
                    "last_activity_at": last["created_at"] if last else None,
                    "last_activity_project_id": str(last["project_id"]) if last else None,
                    "last_activity_work_item_id": str(last["issue_id"]) if last and last["issue_id"] else None,
                    "last_activity_identifier": identifier,
                    "open_work_item_count": open_counts.get(member.id, 0),
                }
            )
        return Response(
            {
                "workspace_slug": slug,
                "project_id": str(project_id) if project_id else None,
                "start_at": start,
                "end_at": end,
                "include_light_activity": data["include_light_activity"],
                "complete": True,
                "members": rows,
            }
        )
