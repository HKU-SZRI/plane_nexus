# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.urls import path

from plane.api.views.cycle import (
    CycleListCreateAPIEndpoint,
    CycleDetailAPIEndpoint,
    CycleIssueListCreateAPIEndpoint,
    CycleIssueDetailAPIEndpoint,
    TransferCycleIssueAPIEndpoint,
    CycleArchiveUnarchiveAPIEndpoint,
)
from plane.api.views.compat import (
    CycleV1ViewSet,
    CycleProgressV1Endpoint,
    CycleDateCheckV1Endpoint,
)

urlpatterns = [
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/cycles/",
        CycleV1ViewSet.as_view({"get": "list", "post": "create"}),
        name="cycles",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/cycles/<uuid:pk>/",
        CycleV1ViewSet.as_view({"get": "retrieve", "patch": "partial_update", "delete": "destroy"}),
        name="cycles",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/cycles/<uuid:cycle_id>/cycle-issues/",
        CycleIssueListCreateAPIEndpoint.as_view(http_method_names=["get", "post"]),
        name="cycle-issues",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/cycles/<uuid:cycle_id>/cycle-issues/<uuid:issue_id>/",
        CycleIssueDetailAPIEndpoint.as_view(http_method_names=["get", "delete"]),
        name="cycle-issues",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/cycles/<uuid:cycle_id>/transfer-issues/",
        TransferCycleIssueAPIEndpoint.as_view(http_method_names=["post"]),
        name="transfer-issues",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/cycles/<uuid:cycle_id>/archive/",
        CycleArchiveUnarchiveAPIEndpoint.as_view(http_method_names=["post"]),
        name="cycle-archive-unarchive",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/archived-cycles/",
        CycleArchiveUnarchiveAPIEndpoint.as_view(http_method_names=["get"]),
        name="cycle-archive-unarchive",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/archived-cycles/<uuid:cycle_id>/unarchive/",
        CycleArchiveUnarchiveAPIEndpoint.as_view(http_method_names=["delete"]),
        name="cycle-archive-unarchive",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/cycles/<uuid:cycle_id>/progress/",
        CycleProgressV1Endpoint.as_view(),
        name="cycle-progress",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/cycles/date-check/",
        CycleDateCheckV1Endpoint.as_view(),
        name="cycle-date-check",
    ),
]
