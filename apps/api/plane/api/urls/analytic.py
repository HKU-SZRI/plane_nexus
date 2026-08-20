# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only

from django.urls import path

from plane.api.views.compat import (
    AdvanceAnalyticsChartV1Endpoint,
    AdvanceAnalyticsStatsV1Endpoint,
    AdvanceAnalyticsV1Endpoint,
    ProjectStatsV1Endpoint,
)


urlpatterns = [
    path(
        "workspaces/<str:slug>/project-stats/",
        ProjectStatsV1Endpoint.as_view(http_method_names=["get"]),
        name="project-analytics",
    ),
    path(
        "workspaces/<str:slug>/advance-analytics/",
        AdvanceAnalyticsV1Endpoint.as_view(http_method_names=["get"]),
        name="advance-analytics",
    ),
    path(
        "workspaces/<str:slug>/advance-analytics-stats/",
        AdvanceAnalyticsStatsV1Endpoint.as_view(http_method_names=["get"]),
        name="advance-analytics-stats",
    ),
    path(
        "workspaces/<str:slug>/advance-analytics-charts/",
        AdvanceAnalyticsChartV1Endpoint.as_view(http_method_names=["get"]),
        name="advance-analytics-chart",
    ),
]
