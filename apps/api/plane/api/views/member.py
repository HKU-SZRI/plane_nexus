# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
import json

# Third Party imports
from rest_framework.response import Response
from rest_framework import status
from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import Count, Q
from django.utils import timezone
from drf_spectacular.utils import (
    extend_schema,
    OpenApiResponse,
    OpenApiRequest,
)

# Module imports
from .base import BaseAPIView
from plane.api.serializers import UserLiteSerializer, ProjectMemberSerializer
from plane.db.models import Project, ProjectMember, StateGroup, User, Workspace, WorkspaceMember
from plane.bgtasks.webhook_task import model_activity, webhook_activity
from plane.utils.host import base_host
from plane.utils.permissions import ProjectMemberPermission, WorkSpaceAdminPermission, ProjectAdminPermission
from plane.utils.openapi import (
    WORKSPACE_SLUG_PARAMETER,
    PROJECT_ID_PARAMETER,
    UNAUTHORIZED_RESPONSE,
    FORBIDDEN_RESPONSE,
    WORKSPACE_NOT_FOUND_RESPONSE,
    PROJECT_NOT_FOUND_RESPONSE,
    WORKSPACE_MEMBER_EXAMPLE,
    PROJECT_MEMBER_EXAMPLE,
)


class WorkspaceMemberAPIEndpoint(BaseAPIView):
    permission_classes = [WorkSpaceAdminPermission]
    use_read_replica = True

    @extend_schema(
        operation_id="get_workspace_members",
        summary="List workspace members",
        description="Retrieve all users who are members of the specified workspace.",
        tags=["Members"],
        parameters=[WORKSPACE_SLUG_PARAMETER],
        responses={
            200: OpenApiResponse(
                description="List of workspace members with their roles",
                response={
                    "type": "array",
                    "items": {
                        "allOf": [
                            {"$ref": "#/components/schemas/UserLite"},
                            {
                                "type": "object",
                                "properties": {
                                    "role": {
                                        "type": "integer",
                                        "description": "Member role in the workspace",
                                    },
                                    "is_active": {
                                        "type": "boolean",
                                        "description": "Whether the member is active in the workspace",
                                    },
                                },
                            },
                        ]
                    },
                },
                examples=[WORKSPACE_MEMBER_EXAMPLE],
            ),
            401: UNAUTHORIZED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
            404: WORKSPACE_NOT_FOUND_RESPONSE,
        },
    )
    # Get all the users that are present inside the workspace
    def get(self, request, slug):
        """List workspace members

        Retrieve all users who are members of the specified workspace.
        Returns user profiles with their respective workspace roles and permissions.
        """
        # Check if the workspace exists
        if not Workspace.objects.filter(slug=slug).exists():
            return Response(
                {"error": "Provided workspace does not exist"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        workspace_members = WorkspaceMember.objects.filter(workspace__slug=slug).select_related("member")

        # Get all the users with their roles
        users_with_roles = []
        for workspace_member in workspace_members:
            user_data = UserLiteSerializer(workspace_member.member).data
            user_data["role"] = workspace_member.role
            user_data["is_active"] = workspace_member.is_active
            users_with_roles.append(user_data)

        return Response(users_with_roles, status=status.HTTP_200_OK)


class WorkspaceMemberDetailAPIEndpoint(BaseAPIView):
    permission_classes = [WorkSpaceAdminPermission]

    @extend_schema(
        operation_id="update_workspace_member",
        summary="Update a workspace member",
        description=(
            "Update a workspace member identified by their user id. Currently supports "
            "toggling `is_active`. Deactivating a member also deactivates their project "
            "memberships in the workspace."
        ),
        tags=["Members"],
        parameters=[WORKSPACE_SLUG_PARAMETER],
        request=OpenApiRequest(
            request={
                "type": "object",
                "properties": {
                    "is_active": {
                        "type": "boolean",
                        "description": "Whether the member is active in the workspace",
                    }
                },
            }
        ),
        responses={
            200: OpenApiResponse(description="Workspace member updated"),
            400: OpenApiResponse(description="Invalid request"),
            401: UNAUTHORIZED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
            404: WORKSPACE_NOT_FOUND_RESPONSE,
        },
    )
    def patch(self, request, slug, pk):
        """Update a workspace member

        `pk` is the member's user id (the same identifier returned as `id` by the
        workspace members list endpoint).
        """
        if not Workspace.objects.filter(slug=slug).exists():
            return Response(
                {"error": "Provided workspace does not exist"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            workspace_member = WorkspaceMember.objects.get(workspace__slug=slug, member_id=pk)
        except WorkspaceMember.DoesNotExist:
            return Response(
                {"error": "Member does not exist in the workspace"},
                status=status.HTTP_404_NOT_FOUND,
            )

        is_active = request.data.get("is_active")
        if not isinstance(is_active, bool):
            return Response(
                {"error": "`is_active` (boolean) is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Deactivating a member: guard against removing the only workspace admin
        if not is_active:
            if (
                workspace_member.role == 20
                and not WorkspaceMember.objects.filter(
                    workspace__slug=slug, role=20, is_active=True
                ).count()
                > 1
            ):
                return Response(
                    {"error": "You cannot deactivate the only admin of the workspace."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if (
                Project.objects.annotate(
                    total_members=Count("project_projectmember"),
                    member_with_role=Count(
                        "project_projectmember",
                        filter=Q(
                            project_projectmember__member_id=workspace_member.member_id,
                            project_projectmember__role=20,
                        ),
                    ),
                )
                .filter(total_members=1, member_with_role=1, workspace__slug=slug)
                .exists()
            ):
                return Response(
                    {
                        "error": "This member is the only admin of some projects; reassign a project admin before deactivating."  # noqa: E501
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Cascade: deactivate the member's project memberships in this workspace
            ProjectMember.objects.filter(
                workspace__slug=slug, member_id=workspace_member.member_id, is_active=True
            ).update(is_active=False, updated_at=timezone.now())

        workspace_member.is_active = is_active
        workspace_member.save(update_fields=["is_active"])

        user_data = UserLiteSerializer(workspace_member.member).data
        user_data["role"] = workspace_member.role
        user_data["is_active"] = workspace_member.is_active
        return Response(user_data, status=status.HTTP_200_OK)


class ProjectMemberListCreateAPIEndpoint(BaseAPIView):
    permission_classes = [ProjectMemberPermission]
    use_read_replica = True

    def get_permissions(self):
        if self.request.method == "GET":
            return [ProjectMemberPermission()]
        return [ProjectAdminPermission()]

    @extend_schema(
        operation_id="get_project_members",
        summary="List project members",
        description="Retrieve all users who are members of the specified project.",
        tags=["Members"],
        parameters=[WORKSPACE_SLUG_PARAMETER, PROJECT_ID_PARAMETER],
        responses={
            200: OpenApiResponse(
                description="List of project members with their roles",
                response=UserLiteSerializer,
                examples=[PROJECT_MEMBER_EXAMPLE],
            ),
            401: UNAUTHORIZED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
            404: PROJECT_NOT_FOUND_RESPONSE,
        },
    )
    # Get all the users that are present inside the workspace
    def get(self, request, slug, project_id):
        """List project members

        Retrieve all users who are members of the specified project.
        Returns user profiles with their project-specific roles and access levels.
        """
        # Check if the workspace exists
        if not Workspace.objects.filter(slug=slug).exists():
            return Response(
                {"error": "Provided workspace does not exist"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Fetch ProjectMember records with member info in one query
        project_members = (
            ProjectMember.objects
            .filter(project_id=project_id, workspace__slug=slug, is_active=True)
            .select_related("member")
            .annotate(
                started_issues=Count(
                    "member__assignee",
                    filter=Q(
                        member__assignee__project_id=project_id,
                        member__assignee__state__group=StateGroup.STARTED,
                        member__assignee__deleted_at__isnull=True,
                        member__assignee__archived_at__isnull=True,
                        member__assignee__is_draft=False,
                    ),
                    distinct=True,
                ),
                unstarted_issues=Count(
                    "member__assignee",
                    filter=Q(
                        member__assignee__project_id=project_id,
                        member__assignee__state__group__in=[StateGroup.BACKLOG, StateGroup.UNSTARTED],
                        member__assignee__deleted_at__isnull=True,
                        member__assignee__archived_at__isnull=True,
                        member__assignee__is_draft=False,
                    ),
                    distinct=True,
                ),
            )
        )

        # Build response: user fields + record id + role
        result = []
        for pm in project_members:
            user_data = UserLiteSerializer(pm.member).data
            user_data["member_id"] = str(pm.id)
            user_data["role"] = pm.role
            user_data["started_issues"] = pm.started_issues
            user_data["unstarted_issues"] = pm.unstarted_issues
            result.append(user_data)

        return Response(result, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="create_project_member",
        summary="Create project member",
        description="Create a new project member",
        tags=["Members"],
        parameters=[WORKSPACE_SLUG_PARAMETER, PROJECT_ID_PARAMETER],
        responses={201: OpenApiResponse(description="Project member created", response=ProjectMemberSerializer)},
        request=OpenApiRequest(request=ProjectMemberSerializer),
    )
    def post(self, request, slug, project_id):
        # A ProjectMember row is soft-deleted via is_active=False (not deleted_at), so a
        # previously removed member still holds the unique (project, member) constraint.
        # Reactivate that row instead of trying to create a duplicate.
        existing_member = ProjectMember.objects.filter(
            project_id=project_id, member_id=request.data.get("member")
        ).first()

        if existing_member:
            current_instance = json.dumps(ProjectMemberSerializer(existing_member).data, cls=DjangoJSONEncoder)
            serializer = ProjectMemberSerializer(
                existing_member, data=request.data, partial=True, context={"slug": slug}
            )
            serializer.is_valid(raise_exception=True)
            serializer.save()
            existing_member.is_active = True
            existing_member.save(update_fields=["is_active"])
            model_activity.delay(
                model_name="project_member",
                model_id=str(existing_member.id),
                requested_data=request.data,
                current_instance=current_instance,
                actor_id=request.user.id,
                slug=slug,
                origin=base_host(request=request, is_app=True),
            )
            webhook_activity.delay(
                event="project_member",
                verb="updated",
                field="is_active",
                old_value=False,
                new_value=True,
                actor_id=request.user.id,
                slug=slug,
                current_site=base_host(request=request, is_app=True),
                event_id=existing_member.id,
                old_identifier=None,
                new_identifier=None,
            )
            return Response(serializer.data, status=status.HTTP_200_OK)

        serializer = ProjectMemberSerializer(data=request.data, context={"slug": slug})
        serializer.is_valid(raise_exception=True)
        serializer.save(project_id=project_id)
        model_activity.delay(
            model_name="project_member",
            model_id=str(serializer.instance.id),
            requested_data=request.data,
            current_instance=None,
            actor_id=request.user.id,
            slug=slug,
            origin=base_host(request=request, is_app=True),
        )
        webhook_activity.delay(
            event="project_member",
            verb="created",
            field=None,
            old_value=None,
            new_value=None,
            actor_id=request.user.id,
            slug=slug,
            current_site=base_host(request=request, is_app=True),
            event_id=serializer.instance.id,
            old_identifier=None,
            new_identifier=None,
        )
        return Response(serializer.data, status=status.HTTP_201_CREATED)


# API endpoint to get and update a project member
class ProjectMemberDetailAPIEndpoint(ProjectMemberListCreateAPIEndpoint):
    @extend_schema(
        operation_id="get_project_member",
        summary="Get project member",
        description="Retrieve a project member by ID.",
        tags=["Members"],
        parameters=[WORKSPACE_SLUG_PARAMETER, PROJECT_ID_PARAMETER],
        responses={
            200: OpenApiResponse(description="Project member", response=ProjectMemberSerializer),
            401: UNAUTHORIZED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
            404: PROJECT_NOT_FOUND_RESPONSE,
        },
    )
    # Get a project member by ID
    def get(self, request, slug, project_id, pk):
        """Get project member

        Retrieve a project member by ID.
        Returns a project member with their project-specific roles and access levels.
        """
        # Check if the workspace exists
        if not Workspace.objects.filter(slug=slug).exists():
            return Response(
                {"error": "Provided workspace does not exist"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Get the workspace members that are present inside the workspace
        project_members = ProjectMember.objects.get(project_id=project_id, workspace__slug=slug, pk=pk)
        user = User.objects.get(id=project_members.member_id)
        user = UserLiteSerializer(user).data
        return Response(user, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="update_project_member",
        summary="Update project member",
        description="Update a project member",
        tags=["Members"],
        parameters=[WORKSPACE_SLUG_PARAMETER, PROJECT_ID_PARAMETER],
        responses={200: OpenApiResponse(description="Project member updated", response=ProjectMemberSerializer)},
        request=OpenApiRequest(request=ProjectMemberSerializer),
    )
    def patch(self, request, slug, project_id, pk):
        project_member = ProjectMember.objects.get(project_id=project_id, workspace__slug=slug, pk=pk)
        current_instance = json.dumps(ProjectMemberSerializer(project_member).data, cls=DjangoJSONEncoder)
        serializer = ProjectMemberSerializer(project_member, data=request.data, partial=True, context={"slug": slug})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        model_activity.delay(
            model_name="project_member",
            model_id=str(project_member.id),
            requested_data=request.data,
            current_instance=current_instance,
            actor_id=request.user.id,
            slug=slug,
            origin=base_host(request=request, is_app=True),
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="delete_project_member",
        summary="Delete project member",
        description="Delete a project member",
        tags=["Members"],
        parameters=[WORKSPACE_SLUG_PARAMETER, PROJECT_ID_PARAMETER],
        responses={204: OpenApiResponse(description="Project member deleted")},
    )
    def delete(self, request, slug, project_id, pk):
        project_member = ProjectMember.objects.get(project_id=project_id, workspace__slug=slug, pk=pk)
        project_member.is_active = False
        project_member.save()
        webhook_activity.delay(
            event="project_member",
            verb="deleted",
            field=None,
            old_value=None,
            new_value=None,
            actor_id=request.user.id,
            slug=slug,
            current_site=base_host(request=request, is_app=True),
            event_id=project_member.id,
            old_identifier=None,
            new_identifier=None,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)
