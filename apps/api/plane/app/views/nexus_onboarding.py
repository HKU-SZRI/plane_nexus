from __future__ import annotations

import os
import uuid

from django.db import transaction
from django.utils.crypto import get_random_string
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from plane.db.models import Profile, Project, ProjectMember, ProjectUserProperty, User, Workspace, WorkspaceMember


class NexusOnboardUserEndpoint(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        expected_key = os.getenv("PLANE_NEXUS_ONBOARDING_KEY", "")
        provided_key = request.headers.get("X-Internal-Key", "")
        if not expected_key or provided_key != expected_key:
            return Response({"error": "Invalid internal key"}, status=status.HTTP_403_FORBIDDEN)

        email = str(request.data.get("email") or "").strip().lower()
        workspace_slug = str(request.data.get("workspace_slug") or "").strip()
        try:
            role = int(request.data.get("role", 15))
        except (TypeError, ValueError):
            return Response({"error": "Invalid role"}, status=status.HTTP_400_BAD_REQUEST)

        if not email or "@" not in email:
            return Response({"error": "Valid email is required"}, status=status.HTTP_400_BAD_REQUEST)
        if not workspace_slug:
            return Response({"error": "workspace_slug is required"}, status=status.HTTP_400_BAD_REQUEST)
        if role not in {5, 15, 20}:
            return Response({"error": "Invalid role"}, status=status.HTTP_400_BAD_REQUEST)

        first_name = str(request.data.get("first_name") or "").strip()
        last_name = str(request.data.get("last_name") or "").strip()
        project_id = str(request.data.get("project_id") or "").strip()

        try:
            with transaction.atomic():
                workspace = Workspace.objects.get(slug=workspace_slug)
                project = None
                if project_id:
                    project = Project.objects.get(pk=project_id, workspace=workspace)
                user = User.objects.filter(email=email).first()
                user_created = False
                if user is None:
                    user = User(
                        email=email,
                        username=uuid.uuid4().hex,
                        first_name=first_name,
                        last_name=last_name,
                    )
                    user.set_password(get_random_string(32))
                    user.is_password_autoset = True
                    user.is_email_verified = True
                    user.save()
                    profile, _ = Profile.objects.get_or_create(user=user)
                    user_created = True
                else:
                    update_fields = []
                    if first_name and not user.first_name:
                        user.first_name = first_name
                        update_fields.append("first_name")
                    if last_name and not user.last_name:
                        user.last_name = last_name
                        update_fields.append("last_name")
                    if not getattr(user, "is_email_verified", False):
                        user.is_email_verified = True
                        update_fields.append("is_email_verified")
                    if update_fields:
                        user.save(update_fields=update_fields)
                    profile, _ = Profile.objects.get_or_create(user=user)

                member, member_created = WorkspaceMember.objects.get_or_create(
                    workspace=workspace,
                    member=user,
                    defaults={"role": role, "is_active": True},
                )
                changed = False
                if member.role != role:
                    member.role = role
                    changed = True
                if not member.is_active:
                    member.is_active = True
                    changed = True
                if changed:
                    member.save(update_fields=["role", "is_active", "updated_at"])

                if profile.last_workspace_id != workspace.id:
                    profile.last_workspace_id = workspace.id
                    profile.save(update_fields=["last_workspace_id", "updated_at"])

                project_member = None
                project_member_created = False
                if project is not None:
                    project_member, project_member_created = ProjectMember.objects.get_or_create(
                        project=project,
                        member=user,
                        defaults={"role": role, "is_active": True, "workspace": workspace},
                    )
                    changed = False
                    if project_member.role != role:
                        project_member.role = role
                        changed = True
                    if not project_member.is_active:
                        project_member.is_active = True
                        changed = True
                    if changed:
                        project_member.save(update_fields=["role", "is_active", "updated_at"])
                    ProjectUserProperty.objects.get_or_create(
                        project=project,
                        user=user,
                        defaults={"workspace": workspace},
                    )
        except Workspace.DoesNotExist:
            return Response({"error": "Workspace not found"}, status=status.HTTP_404_NOT_FOUND)
        except Project.DoesNotExist:
            return Response({"error": "Project not found"}, status=status.HTTP_404_NOT_FOUND)

        return Response(
            {
                "ok": True,
                "user": {
                    "id": str(user.id),
                    "email": user.email,
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "created": user_created,
                },
                "workspace": {
                    "slug": workspace.slug,
                    "role": member.role,
                    "member_created": member_created,
                },
                "project": (
                    {
                        "id": str(project.id),
                        "name": project.name,
                        "role": project_member.role,
                        "member_created": project_member_created,
                    }
                    if project is not None and project_member is not None
                    else None
                ),
            },
            status=status.HTTP_200_OK,
        )
