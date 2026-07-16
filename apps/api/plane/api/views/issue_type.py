# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.db import IntegrityError

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.api.serializers import IssueTypeSerializer, ProjectIssueTypeSerializer
from plane.app.permissions import WorkspaceEntityPermission, ProjectEntityPermission
from plane.db.models import IssueType, ProjectIssueType, Workspace
from .base import BaseAPIView


class IssueTypeListCreateAPIEndpoint(BaseAPIView):
    """Workspace Issue Type List and Create Endpoint"""

    serializer_class = IssueTypeSerializer
    model = IssueType
    permission_classes = [WorkspaceEntityPermission]
    use_read_replica = True

    def get_queryset(self):
        return (
            IssueType.objects.filter(workspace__slug=self.kwargs.get("slug"))
            .filter(is_active=True)
            .select_related("workspace")
            .distinct()
        )

    def post(self, request, slug):
        """Create issue type

        Create a new workspace-level issue type (e.g. Complaint, Request, Enquiry, Appeal).
        """
        try:
            workspace = Workspace.objects.get(slug=slug)
            serializer = IssueTypeSerializer(data=request.data)
            if serializer.is_valid():
                serializer.save(workspace_id=workspace.id)
                return Response(serializer.data, status=status.HTTP_201_CREATED)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        except IntegrityError:
            return Response(
                {"error": "Issue type with the same name already exists in the workspace"},
                status=status.HTTP_409_CONFLICT,
            )

    def get(self, request, slug):
        """List issue types

        Retrieve all active issue types for the workspace.
        """
        return self.paginate(
            request=request,
            queryset=self.get_queryset(),
            on_results=lambda issue_types: IssueTypeSerializer(issue_types, many=True).data,
        )


class IssueTypeDetailAPIEndpoint(BaseAPIView):
    """Workspace Issue Type Detail Endpoint"""

    serializer_class = IssueTypeSerializer
    model = IssueType
    permission_classes = [WorkspaceEntityPermission]
    use_read_replica = True

    def get_queryset(self):
        return IssueType.objects.filter(workspace__slug=self.kwargs.get("slug")).select_related("workspace")

    def get(self, request, slug, issue_type_id):
        """Retrieve issue type"""
        issue_type = self.get_queryset().get(pk=issue_type_id)
        return Response(IssueTypeSerializer(issue_type).data, status=status.HTTP_200_OK)

    def patch(self, request, slug, issue_type_id):
        """Update issue type"""
        issue_type = self.get_queryset().get(pk=issue_type_id)
        serializer = IssueTypeSerializer(issue_type, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, slug, issue_type_id):
        """Delete issue type

        Issue types still enabled on a project cannot be deleted — disable them on every
        project first so `type_id` references on existing work items aren't orphaned.
        """
        issue_type = self.get_queryset().get(pk=issue_type_id)

        if ProjectIssueType.objects.filter(issue_type=issue_type).exists():
            return Response(
                {"error": "Issue type is enabled on a project and cannot be deleted"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        issue_type.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ProjectIssueTypeListCreateAPIEndpoint(BaseAPIView):
    """Project Issue Type List and Create Endpoint (enable a workspace issue type on a project)"""

    serializer_class = ProjectIssueTypeSerializer
    model = ProjectIssueType
    permission_classes = [ProjectEntityPermission]
    use_read_replica = True

    def get_queryset(self):
        return (
            ProjectIssueType.objects.filter(workspace__slug=self.kwargs.get("slug"))
            .filter(project_id=self.kwargs.get("project_id"))
            .filter(
                project__project_projectmember__member=self.request.user,
                project__project_projectmember__is_active=True,
            )
            .select_related("issue_type", "project", "workspace")
            .distinct()
        )

    def post(self, request, slug, project_id):
        """Enable issue type for project

        Make a workspace-level issue type selectable on this project.
        """
        try:
            serializer = ProjectIssueTypeSerializer(data=request.data)
            if serializer.is_valid():
                serializer.save(project_id=project_id)
                return Response(serializer.data, status=status.HTTP_201_CREATED)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        except IntegrityError:
            return Response(
                {"error": "Issue type is already enabled for this project"},
                status=status.HTTP_409_CONFLICT,
            )

    def get(self, request, slug, project_id):
        """List issue types enabled for project"""
        return self.paginate(
            request=request,
            queryset=self.get_queryset(),
            on_results=lambda items: ProjectIssueTypeSerializer(items, many=True).data,
        )


class ProjectIssueTypeDetailAPIEndpoint(BaseAPIView):
    """Project Issue Type Detail Endpoint"""

    serializer_class = ProjectIssueTypeSerializer
    model = ProjectIssueType
    permission_classes = [ProjectEntityPermission]
    use_read_replica = True

    def get_queryset(self):
        return (
            ProjectIssueType.objects.filter(workspace__slug=self.kwargs.get("slug"))
            .filter(project_id=self.kwargs.get("project_id"))
            .select_related("issue_type", "project", "workspace")
        )

    def get(self, request, slug, project_id, pk):
        """Retrieve project issue type"""
        project_issue_type = self.get_queryset().get(pk=pk)
        return Response(ProjectIssueTypeSerializer(project_issue_type).data, status=status.HTTP_200_OK)

    def patch(self, request, slug, project_id, pk):
        """Update project issue type

        Used to flip `is_default` — the type auto-assigned to new work items that
        don't specify `type_id` (see IssueSerializer.create in plane/api/serializers/issue.py).
        """
        project_issue_type = self.get_queryset().get(pk=pk)
        serializer = ProjectIssueTypeSerializer(project_issue_type, data=request.data, partial=True)
        if serializer.is_valid():
            if serializer.validated_data.get("is_default"):
                ProjectIssueType.objects.filter(project_id=project_id).exclude(pk=pk).update(is_default=False)
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, slug, project_id, pk):
        """Disable issue type for project"""
        project_issue_type = self.get_queryset().get(pk=pk)
        project_issue_type.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
