# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from datetime import timedelta

import pytest
from django.urls import resolve
from django.utils import timezone
from rest_framework import status

from plane.api.middleware.api_authentication import APIKeyAuthentication
from plane.api.views.compat import (
    CycleArchiveUnarchiveV1Endpoint,
    IssueArchiveV1ViewSet,
    ModuleArchiveUnarchiveV1Endpoint,
)
from plane.db.models import Cycle, Issue, Module, Project, ProjectMember


@pytest.fixture
def project(db, workspace, create_user):
    project = Project.objects.create(
        name="Archive compatibility project",
        identifier="ARCH",
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
def archived_issue(db, project, create_user):
    return Issue.objects.create(
        name="Archived work item",
        workspace=project.workspace,
        project=project,
        archived_at=timezone.now().date(),
        created_by=create_user,
    )


@pytest.fixture
def archived_cycle(db, project, create_user):
    now = timezone.now()
    return Cycle.objects.create(
        name="Archived cycle",
        workspace=project.workspace,
        project=project,
        owned_by=create_user,
        start_date=now - timedelta(days=14),
        end_date=now - timedelta(days=7),
        archived_at=now,
        created_by=create_user,
    )


@pytest.fixture
def archived_module(db, project, create_user):
    return Module.objects.create(
        name="Archived module",
        workspace=project.workspace,
        project=project,
        status="completed",
        archived_at=timezone.now(),
        created_by=create_user,
    )


def assert_matching_get_responses(session_client, api_key_client, path, params=None):
    app_response = session_client.get(f"/api/{path}", params or {})
    v1_response = api_key_client.get(f"/api/v1/{path}", params or {})

    assert app_response.status_code == status.HTTP_200_OK
    assert v1_response.status_code == status.HTTP_200_OK
    assert v1_response.json() == app_response.json()


@pytest.mark.contract
@pytest.mark.django_db
class TestArchiveCompatResponses:
    @pytest.mark.parametrize(
        ("path", "view_class", "method"),
        [
            (
                "workspaces/{workspace_slug}/projects/{project_id}/archived-issues/",
                IssueArchiveV1ViewSet,
                "get",
            ),
            (
                "workspaces/{workspace_slug}/projects/{project_id}/cycles/{cycle_id}/archive/",
                CycleArchiveUnarchiveV1Endpoint,
                "delete",
            ),
            (
                "workspaces/{workspace_slug}/projects/{project_id}/modules/{module_id}/archive/",
                ModuleArchiveUnarchiveV1Endpoint,
                "delete",
            ),
        ],
    )
    def test_archive_routes_use_app_compat_views(
        self,
        workspace,
        project,
        archived_cycle,
        archived_module,
        path,
        view_class,
        method,
    ):
        resolved_path = path.format(
            workspace_slug=workspace.slug,
            project_id=project.id,
            cycle_id=archived_cycle.id,
            module_id=archived_module.id,
        )
        match = resolve(f"/api/v1/{resolved_path}")
        resolved_view_class = getattr(match.func, "cls", getattr(match.func, "view_class", None))

        assert resolved_view_class is view_class
        if hasattr(match.func, "actions"):
            assert method in match.func.actions
        else:
            assert method in match.func.view_initkwargs["http_method_names"]
        assert view_class.authentication_classes == [APIKeyAuthentication]

    def test_archived_work_items_response_matches_app_api(
        self, session_client, api_key_client, workspace, project, archived_issue
    ):
        path = f"workspaces/{workspace.slug}/projects/{project.id}/archived-issues/"
        params = {
            "order_by": "sort_order",
            "sub_issue": "true",
            "filters": "{}",
            "layout": "list",
            "cursor": "100:0:0",
            "per_page": 100,
        }

        assert_matching_get_responses(session_client, api_key_client, path, params)

    def test_archived_cycles_response_matches_app_api(
        self, session_client, api_key_client, workspace, project, archived_cycle
    ):
        path = f"workspaces/{workspace.slug}/projects/{project.id}/archived-cycles/"

        assert_matching_get_responses(session_client, api_key_client, path)

    def test_archived_modules_response_matches_app_api(
        self, session_client, api_key_client, workspace, project, archived_module
    ):
        path = f"workspaces/{workspace.slug}/projects/{project.id}/archived-modules/"

        assert_matching_get_responses(session_client, api_key_client, path)

    def test_cycle_restore_matches_app_api(self, session_client, api_key_client, workspace, project, archived_cycle):
        path = f"workspaces/{workspace.slug}/projects/{project.id}/cycles/{archived_cycle.id}/archive/"

        app_response = session_client.delete(f"/api/{path}")
        archived_cycle.archived_at = timezone.now()
        archived_cycle.save(update_fields=["archived_at"])
        v1_response = api_key_client.delete(f"/api/v1/{path}")

        assert app_response.status_code == status.HTTP_204_NO_CONTENT
        assert v1_response.status_code == app_response.status_code
        assert v1_response.content == app_response.content
        archived_cycle.refresh_from_db()
        assert archived_cycle.archived_at is None

    def test_module_restore_matches_app_api(self, session_client, api_key_client, workspace, project, archived_module):
        path = f"workspaces/{workspace.slug}/projects/{project.id}/modules/{archived_module.id}/archive/"

        app_response = session_client.delete(f"/api/{path}")
        archived_module.archived_at = timezone.now()
        archived_module.save(update_fields=["archived_at"])
        v1_response = api_key_client.delete(f"/api/v1/{path}")

        assert app_response.status_code == status.HTTP_204_NO_CONTENT
        assert v1_response.status_code == app_response.status_code
        assert v1_response.content == app_response.content
        archived_module.refresh_from_db()
        assert archived_module.archived_at is None
