# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from unittest.mock import patch

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from plane.db.models import Issue, Project, ProjectMember


@pytest.fixture
def project(db, workspace, create_user):
    project = Project.objects.create(
        name="Test Project",
        identifier="NEXUS",
        workspace=workspace,
        created_by=create_user,
    )
    ProjectMember.objects.create(
        project=project,
        member=create_user,
        role=20,
        is_active=True,
    )
    return project


@pytest.fixture
def issue(db, project, create_user):
    return Issue.objects.create(
        name="Parent work item",
        workspace=project.workspace,
        project=project,
        created_by=create_user,
    )


@pytest.fixture
def sub_issue(db, project, issue, create_user):
    return Issue.objects.create(
        name="Child work item",
        workspace=project.workspace,
        project=project,
        parent=issue,
        created_by=create_user,
    )


def assert_matching_get_responses(api_key_client, create_user, app_url, v1_url, params=None):
    app_client = APIClient()
    app_client.force_authenticate(user=create_user)

    app_response = app_client.get(app_url, params or {})
    v1_response = api_key_client.get(v1_url, params or {})

    assert app_response.status_code == status.HTTP_200_OK
    assert v1_response.status_code == status.HTTP_200_OK
    assert v1_response.json() == app_response.json()


@pytest.mark.contract
@pytest.mark.django_db
class TestWorkItemCompatResponses:
    def test_description_versions_response_matches_app_api(
        self, api_key_client, create_user, workspace, project, issue
    ):
        path = (
            f"workspaces/{workspace.slug}/projects/{project.id}/"
            f"work-items/{issue.id}/description-versions/"
        )
        assert_matching_get_responses(
            api_key_client,
            create_user,
            f"/api/{path}",
            f"/api/v1/{path}",
        )

    def test_issue_relation_response_matches_app_api(
        self, api_key_client, create_user, workspace, project, issue
    ):
        path = (
            f"workspaces/{workspace.slug}/projects/{project.id}/"
            f"issues/{issue.id}/issue-relation/"
        )
        assert_matching_get_responses(
            api_key_client,
            create_user,
            f"/api/{path}",
            f"/api/v1/{path}",
        )

    def test_sub_issues_response_matches_app_api(
        self, api_key_client, create_user, workspace, project, issue, sub_issue
    ):
        path = (
            f"workspaces/{workspace.slug}/projects/{project.id}/"
            f"issues/{issue.id}/sub-issues/"
        )
        assert_matching_get_responses(
            api_key_client,
            create_user,
            f"/api/{path}",
            f"/api/v1/{path}",
        )

    @patch("plane.app.views.issue.base.recent_visited_task.delay")
    def test_identifier_detail_response_and_expansions_match_app_api(
        self, _recent_visited_delay, api_key_client, create_user, workspace, project, issue
    ):
        path = f"workspaces/{workspace.slug}/work-items/{project.identifier}-{issue.sequence_id}/"
        params = {"expand": "issue_reactions,issue_attachments,issue_link,parent"}
        assert_matching_get_responses(
            api_key_client,
            create_user,
            f"/api/{path}",
            f"/api/v1/{path}",
            params,
        )

        response = api_key_client.get(f"/api/v1/{path}", params)
        assert {
            "issue_reactions",
            "issue_attachments",
            "issue_link",
            "parent",
        }.issubset(response.json())
