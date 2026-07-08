# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import pytest
from rest_framework import status

from plane.db.models import Page, Project, ProjectMember


@pytest.fixture
def project(db, workspace, create_user):
    """Create a test project with the user as a member"""
    project = Project.objects.create(
        name="Test Project",
        identifier="TP",
        workspace=workspace,
        created_by=create_user,
    )
    ProjectMember.objects.create(
        project=project,
        member=create_user,
        role=20,  # Admin role
        is_active=True,
    )
    return project


@pytest.fixture
def create_page(db, project, create_user):
    """Create a test page"""
    page = Page.objects.create(
        name="Existing Page",
        workspace=project.workspace,
        owned_by=create_user,
    )
    page.projects.add(project)
    return page


@pytest.mark.contract
class TestPageListCreateAPIEndpoint:
    """Test Page List and Create v1 API Endpoint"""

    def get_page_url(self, workspace_slug, project_id):
        return f"/api/v1/workspaces/{workspace_slug}/projects/{project_id}/pages/"

    @pytest.mark.django_db
    def test_create_page_success(self, api_key_client, workspace, project):
        url = self.get_page_url(workspace.slug, project.id)

        response = api_key_client.post(url, {"name": "New Page"}, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert Page.objects.count() == 1

        created_page = Page.objects.first()
        assert created_page.name == "New Page"
        assert project in created_page.projects.all()

    @pytest.mark.django_db
    def test_list_pages_success(self, api_key_client, workspace, project, create_page):
        url = self.get_page_url(workspace.slug, project.id)

        Page.objects.create(
            name="Second Page", workspace=project.workspace, owned_by=create_page.owned_by
        ).projects.add(project)

        response = api_key_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2
