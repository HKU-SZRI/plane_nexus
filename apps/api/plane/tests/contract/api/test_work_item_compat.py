# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from unittest.mock import patch

import pytest
from django.urls import resolve
from rest_framework import status
from rest_framework.test import APIClient

from plane.api.middleware.api_authentication import APIKeyAuthentication
from plane.api.views.compat import (
    IssueLinkV1ViewSet,
    IssueRelationV1ViewSet,
    ProjectBulkAssetV1Endpoint,
    ProjectUserDisplayPropertyV1Endpoint,
    SubIssuesV1Endpoint,
)
from plane.db.models import FileAsset, Issue, IssueComment, Project, ProjectMember


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


def assert_matching_patch_responses(api_key_client, create_user, app_url, v1_url, data):
    app_client = APIClient()
    app_client.force_authenticate(user=create_user)

    app_response = app_client.patch(app_url, data, format="json")
    v1_response = api_key_client.patch(v1_url, data, format="json")

    assert app_response.status_code == status.HTTP_200_OK
    assert v1_response.status_code == status.HTTP_200_OK
    assert v1_response.json() == app_response.json()


@pytest.mark.contract
@pytest.mark.django_db
class TestWorkItemCompatResponses:
    @pytest.mark.parametrize(
        ("suffix", "view_class"),
        [
            ("sub-issues", SubIssuesV1Endpoint),
            ("issue-relation", IssueRelationV1ViewSet),
            ("issue-links", IssueLinkV1ViewSet),
        ],
    )
    def test_issue_compat_post_routes_use_api_key_authentication(
        self, project, issue, suffix, view_class
    ):
        path = (
            f"/api/v1/workspaces/{project.workspace.slug}/projects/{project.id}/"
            f"issues/{issue.id}/{suffix}/"
        )
        match = resolve(path)
        resolved_view_class = getattr(match.func, "cls", getattr(match.func, "view_class", None))

        assert resolved_view_class is view_class
        if hasattr(match.func, "actions"):
            assert match.func.actions["post"] == "create"
        else:
            assert "post" in match.func.view_initkwargs["http_method_names"]
        assert view_class.authentication_classes == [APIKeyAuthentication]

    @pytest.mark.parametrize(
        ("path", "method", "view_class"),
        [
            (
                "assets/v2/workspaces/{workspace_slug}/projects/{project_id}/{entity_id}/bulk/",
                "post",
                ProjectBulkAssetV1Endpoint,
            ),
            (
                "workspaces/{workspace_slug}/projects/{project_id}/user-properties/",
                None,
                ProjectUserDisplayPropertyV1Endpoint,
            ),
        ],
    )
    def test_project_compat_routes_use_api_key_authentication(
        self, workspace, project, issue, path, method, view_class
    ):
        resolved_path = path.format(
            workspace_slug=workspace.slug,
            project_id=project.id,
            entity_id=issue.id,
        )
        match = resolve(f"/api/v1/{resolved_path}")
        resolved_view_class = getattr(match.func, "cls", getattr(match.func, "view_class", None))

        assert resolved_view_class is view_class
        if method:
            assert method in match.func.view_initkwargs["http_method_names"]
        assert view_class.authentication_classes == [APIKeyAuthentication]

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

    def test_project_user_properties_response_matches_app_api(
        self, api_key_client, create_user, workspace, project
    ):
        path = f"workspaces/{workspace.slug}/projects/{project.id}/user-properties/"

        assert_matching_get_responses(
            api_key_client,
            create_user,
            f"/api/{path}",
            f"/api/v1/{path}",
        )
        assert_matching_patch_responses(
            api_key_client,
            create_user,
            f"/api/{path}",
            f"/api/v1/{path}",
            {
                "display_properties": {
                    "priority": True,
                    "state": True,
                    "assignee": True,
                }
            },
        )

    def test_project_bulk_asset_binds_comment_description_through_api_key_compat(
        self, api_key_client, workspace, project, issue, create_user
    ):
        comment = IssueComment.objects.create(
            comment_html="<p>Asset comment</p>",
            issue=issue,
            workspace=workspace,
            project=project,
            actor=create_user,
            created_by=create_user,
            updated_by=create_user,
        )
        asset = FileAsset.objects.create(
            attributes={"name": "screenshot.png", "type": "image/png", "size": 128},
            asset=f"{workspace.id}/screenshot.png",
            size=128,
            workspace=workspace,
            project=project,
            created_by=create_user,
            entity_type=FileAsset.EntityTypeContext.COMMENT_DESCRIPTION,
            is_uploaded=True,
        )

        response = api_key_client.post(
            f"/api/v1/assets/v2/workspaces/{workspace.slug}/projects/{project.id}/{comment.id}/bulk/",
            {"asset_ids": [str(asset.id)]},
            format="json",
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT
        asset.refresh_from_db()
        assert asset.comment_id == comment.id
        assert asset.issue_id is None
        assert asset.entity_type == FileAsset.EntityTypeContext.COMMENT_DESCRIPTION
