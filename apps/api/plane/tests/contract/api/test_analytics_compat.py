# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only

import pytest
from django.urls import resolve
from rest_framework import status

from plane.api.middleware.api_authentication import API_AUTHENTICATION_CLASSES
from plane.api.views.compat import (
    AdvanceAnalyticsChartV1Endpoint,
    AdvanceAnalyticsStatsV1Endpoint,
    AdvanceAnalyticsV1Endpoint,
    ProjectStatsV1Endpoint,
)


@pytest.mark.contract
@pytest.mark.django_db
class TestAnalyticsCompatRoutes:
    @pytest.mark.parametrize(
        ("suffix", "view_class"),
        [
            ("project-stats/", ProjectStatsV1Endpoint),
            ("advance-analytics/", AdvanceAnalyticsV1Endpoint),
            ("advance-analytics-stats/", AdvanceAnalyticsStatsV1Endpoint),
            ("advance-analytics-charts/", AdvanceAnalyticsChartV1Endpoint),
        ],
    )
    def test_routes_use_api_key_compat_views(self, workspace, suffix, view_class):
        match = resolve(f"/api/v1/workspaces/{workspace.slug}/{suffix}")
        resolved_view_class = getattr(match.func, "cls", getattr(match.func, "view_class", None))

        assert resolved_view_class is view_class
        assert view_class.authentication_classes == API_AUTHENTICATION_CLASSES

    @pytest.mark.parametrize(
        ("path", "params"),
        [
            ("project-stats/", {"fields": "total_issues,completed_issues"}),
            ("advance-analytics/", {"tab": "work-items"}),
            ("advance-analytics-stats/", {"type": "work-items"}),
            (
                "advance-analytics-charts/",
                {"type": "custom-work-items", "x_axis": "PRIORITY", "y_axis": "WORK_ITEM_COUNT"},
            ),
        ],
    )
    def test_v1_responses_match_session_api(self, session_client, api_key_client, workspace, path, params):
        app_response = session_client.get(f"/api/workspaces/{workspace.slug}/{path}", params)
        v1_response = api_key_client.get(f"/api/v1/workspaces/{workspace.slug}/{path}", params)

        assert app_response.status_code == status.HTTP_200_OK
        assert v1_response.status_code == status.HTTP_200_OK
        assert v1_response.json() == app_response.json()
