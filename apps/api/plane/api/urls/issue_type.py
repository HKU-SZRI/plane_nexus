# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.urls import path

from plane.api.views import (
    IssueTypeListCreateAPIEndpoint,
    IssueTypeDetailAPIEndpoint,
    ProjectIssueTypeListCreateAPIEndpoint,
    ProjectIssueTypeDetailAPIEndpoint,
)

urlpatterns = [
    path(
        "workspaces/<str:slug>/issue-types/",
        IssueTypeListCreateAPIEndpoint.as_view(http_method_names=["get", "post"]),
        name="issue-types",
    ),
    path(
        "workspaces/<str:slug>/issue-types/<uuid:issue_type_id>/",
        IssueTypeDetailAPIEndpoint.as_view(http_method_names=["get", "patch", "delete"]),
        name="issue-types",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/issue-types/",
        ProjectIssueTypeListCreateAPIEndpoint.as_view(http_method_names=["get", "post"]),
        name="project-issue-types",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/issue-types/<uuid:pk>/",
        ProjectIssueTypeDetailAPIEndpoint.as_view(http_method_names=["get", "patch", "delete"]),
        name="project-issue-types",
    ),
]
