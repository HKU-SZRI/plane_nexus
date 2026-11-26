"""Internal, idempotent provisioning of a workspace-scoped scheduling bot."""

import uuid

from django.db import transaction
from django.utils.crypto import constant_time_compare
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from plane.db.models import Profile, Project, ProjectMember, User, Workspace, WorkspaceMember
from plane.app.views.nexus_onboarding import _check_internal_key

BOT_TYPE = "nexus_recurring"


class AgentRequest(serializers.Serializer):
    workspace_slug = serializers.SlugField(max_length=255)
    project_id = serializers.UUIDField()


def _membership(model, *, defaults, **lookup):
    # A removed membership is a deliberate revocation, not a fresh project.
    member = model.all_objects.filter(**lookup).order_by("-created_at").first()
    if member is None:
        member = model.objects.create(**lookup, **defaults)
    if member.deleted_at or not member.is_active or member.role < 15:
        raise PermissionDenied("Recurring Agent membership has been revoked")
    return member


class NexusRecurringAgentEndpoint(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        # Reuse the existing private onboarding channel. No caller-selected
        # identity, role, display name or attribution fields are accepted.
        if not _check_internal_key(request):
            raise PermissionDenied("Invalid internal key")
        serializer = AgentRequest(data=request.data)
        serializer.is_valid(raise_exception=True)
        if set(request.data) != {"workspace_slug", "project_id"}:
            return Response({"error": "Unexpected provisioning fields"}, status=400)
        values = serializer.validated_data
        with transaction.atomic():
            workspace = Workspace.objects.select_for_update().filter(
                slug=values["workspace_slug"], deleted_at__isnull=True
            ).first()
            if workspace is None:
                return Response({"error": "Workspace not found"}, status=404)
            project = Project.objects.select_for_update().filter(
                id=values["project_id"], workspace=workspace, deleted_at__isnull=True
            ).first()
            if project is None:
                return Response({"error": "Project not found in workspace"}, status=404)
            if project.archived_at:
                return Response({"error": "Archived project is read-only"}, status=409)
            # Stable across Nexus reinstalls; separate workspaces cannot share
            # an account. The workspace lock serializes concurrent first calls.
            actor_id = uuid.uuid5(workspace.id, "nexus:recurring-agent:v1")
            email = f"nexus-recurring-{workspace.id}@agents.nexus.invalid"
            user = User.objects.filter(id=actor_id).first()
            if user is None:
                if User.objects.filter(email=email).exists():
                    return Response({"error": "Recurring Agent identity collision"}, status=409)
                user = User(
                    id=actor_id, username=f"nexus-agent-{actor_id.hex}", email=email,
                    first_name="Nexus", last_name="Agent", display_name="Nexus Agent",
                    is_bot=True, bot_type=BOT_TYPE, is_active=True,
                    is_staff=False, is_superuser=False, is_password_autoset=True,
                )
                user.set_unusable_password()
                user.save()
                Profile.objects.get_or_create(user=user)
            if (
                not user.is_bot or user.bot_type != BOT_TYPE or not user.is_active
                or not constant_time_compare(user.email or "", email)
                or user.is_staff or user.is_superuser or user.has_usable_password()
            ):
                raise PermissionDenied("Recurring Agent identity is unavailable or modified")
            _membership(WorkspaceMember, workspace=workspace, member=user,
                        defaults={"role": 15, "is_active": True})
            _membership(ProjectMember, project=project, member=user,
                        defaults={"workspace": workspace, "role": 15, "is_active": True})
            return Response({
                "user_id": str(user.id), "is_bot": True,
                "workspace_slug": workspace.slug, "project_id": str(project.id),
            })
