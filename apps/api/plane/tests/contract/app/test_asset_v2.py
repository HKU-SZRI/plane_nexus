# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from unittest.mock import patch

import pytest
from django.conf import settings
from rest_framework import status

from plane.db.models import FileAsset, Issue, IssueComment, Project, ProjectMember


@pytest.mark.contract
class TestProjectAssetV2API:
    def get_project_asset_url(self, workspace_slug, project_id):
        return f"/api/assets/v2/workspaces/{workspace_slug}/projects/{project_id}/"

    def get_project_bulk_asset_url(self, workspace_slug, project_id, entity_id):
        return f"/api/assets/v2/workspaces/{workspace_slug}/projects/{project_id}/{entity_id}/bulk/"

    @pytest.fixture
    def project(self, workspace, create_user):
        project = Project.objects.create(
            name="Asset Project",
            identifier="AST",
            workspace=workspace,
            created_by=create_user,
            updated_by=create_user,
        )
        ProjectMember.objects.create(project=project, workspace=workspace, member=create_user, role=20)
        return project

    @pytest.fixture
    def issue(self, workspace, project, create_user):
        return Issue.objects.create(
            name="Asset Issue",
            workspace=workspace,
            project=project,
            created_by=create_user,
            updated_by=create_user,
        )

    @pytest.fixture
    def comment(self, workspace, project, issue, create_user):
        return IssueComment.objects.create(
            comment_html="<p>Asset comment</p>",
            issue=issue,
            workspace=workspace,
            project=project,
            actor=create_user,
            created_by=create_user,
            updated_by=create_user,
        )

    @pytest.mark.django_db
    @pytest.mark.parametrize(
        ("filename", "mime_type"),
        [
            ("weekly-report.pdf", "application/pdf"),
            ("weekly-report.md", "text/markdown"),
            ("weekly-report.zip", "application/zip"),
            ("screenshot.png", "image/png"),
            ("data.custom", "application/x-plane-custom"),
        ],
    )
    @patch("plane.app.views.asset.v2.S3Storage")
    def test_comment_description_allows_any_mime_type(
        self,
        mock_storage,
        filename,
        mime_type,
        session_client,
        workspace,
        project,
    ):
        mock_storage.return_value.generate_presigned_post.return_value = {
            "url": "http://storage.example/upload",
            "fields": {},
        }
        url = self.get_project_asset_url(workspace.slug, project.id)

        response = session_client.post(
            url,
            {
                "entity_identifier": "",
                "entity_type": FileAsset.EntityTypeContext.COMMENT_DESCRIPTION,
                "name": filename,
                "size": 933429,
                "type": mime_type,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        asset = FileAsset.objects.get(id=response.data["asset_id"])
        assert asset.entity_type == FileAsset.EntityTypeContext.COMMENT_DESCRIPTION
        assert asset.comment_id is None
        assert asset.attributes["name"] == filename
        assert asset.attributes["type"] == mime_type
        assert (
            response.data["asset_url"]
            == f"/api/assets/v2/workspaces/{workspace.slug}/projects/{project.id}/{asset.id}/"
        )

    @pytest.mark.django_db
    def test_non_comment_project_asset_still_rejects_attachment_mime_types(self, session_client, workspace, project):
        url = self.get_project_asset_url(workspace.slug, project.id)

        response = session_client.post(
            url,
            {
                "entity_identifier": str(project.id),
                "entity_type": FileAsset.EntityTypeContext.PROJECT_COVER,
                "name": "weekly-report.md",
                "size": 933429,
                "type": "text/markdown",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data == {
            "error": "Invalid file type. Only JPEG, PNG, WebP, JPG and GIF files are allowed.",
            "status": False,
        }

    @pytest.mark.django_db
    def test_comment_description_rejects_files_larger_than_file_size_limit(self, session_client, workspace, project):
        url = self.get_project_asset_url(workspace.slug, project.id)

        response = session_client.post(
            url,
            {
                "entity_identifier": "",
                "entity_type": FileAsset.EntityTypeContext.COMMENT_DESCRIPTION,
                "name": "large-file.bin",
                "size": settings.FILE_SIZE_LIMIT + 1,
                "type": "application/octet-stream",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data == {
            "error": f"File size exceeds the maximum allowed size of {settings.FILE_SIZE_LIMIT} bytes.",
            "status": False,
        }

    @pytest.mark.django_db
    def test_bulk_project_asset_binds_comment_description_to_comment(self, session_client, workspace, project, comment):
        asset = FileAsset.objects.create(
            attributes={"name": "weekly-report.md", "type": "text/markdown", "size": 933429},
            asset=f"{workspace.id}/weekly-report.md",
            size=933429,
            workspace=workspace,
            project=project,
            created_by=comment.actor,
            entity_type=FileAsset.EntityTypeContext.COMMENT_DESCRIPTION,
            is_uploaded=True,
        )
        url = self.get_project_bulk_asset_url(workspace.slug, project.id, comment.id)

        response = session_client.post(url, {"asset_ids": [str(asset.id)]}, format="json")

        assert response.status_code == status.HTTP_204_NO_CONTENT
        asset.refresh_from_db()
        assert asset.comment_id == comment.id
        assert asset.issue_id is None
        assert asset.entity_type == FileAsset.EntityTypeContext.COMMENT_DESCRIPTION
