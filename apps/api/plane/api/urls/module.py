# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.urls import path

from plane.api.views import (
    ModuleIssueListCreateAPIEndpoint,
    ModuleIssueDetailAPIEndpoint,
)
from plane.api.views.compat import ModuleArchiveUnarchiveV1Endpoint, ModuleV1ViewSet

urlpatterns = [
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/modules/",
        ModuleV1ViewSet.as_view({"get": "list", "post": "create"}),
        name="modules",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/modules/<uuid:pk>/",
        ModuleV1ViewSet.as_view({"get": "retrieve", "patch": "partial_update", "delete": "destroy"}),
        name="modules-detail",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/modules/<uuid:module_id>/module-issues/",
        ModuleIssueListCreateAPIEndpoint.as_view(http_method_names=["get", "post"]),
        name="module-issues",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/modules/<uuid:module_id>/module-issues/<uuid:issue_id>/",
        ModuleIssueDetailAPIEndpoint.as_view(http_method_names=["delete"]),
        name="module-issues-detail",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/modules/<uuid:module_id>/archive/",
        ModuleArchiveUnarchiveV1Endpoint.as_view(http_method_names=["post", "delete"]),
        name="module-archive",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/archived-modules/",
        ModuleArchiveUnarchiveV1Endpoint.as_view(http_method_names=["get"]),
        name="module-archive-list",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/archived-modules/<uuid:pk>/",
        ModuleArchiveUnarchiveV1Endpoint.as_view(http_method_names=["get"]),
        name="module-archive-detail",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/archived-modules/<uuid:module_id>/unarchive/",
        ModuleArchiveUnarchiveV1Endpoint.as_view(http_method_names=["delete"]),
        name="module-unarchive",
    ),
]
