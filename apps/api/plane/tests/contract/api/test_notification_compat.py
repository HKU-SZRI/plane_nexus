# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only

import pytest
from django.urls import resolve
from rest_framework import status

from plane.api.middleware.api_authentication import APIKeyAuthentication
from plane.api.views.compat import NotificationV1ViewSet, UnreadNotificationV1Endpoint
from plane.db.models import Notification


@pytest.fixture
def notifications(db, workspace, create_user):
    regular = Notification.objects.create(
        workspace=workspace,
        entity_name="issue",
        title="Work item updated",
        sender="issue_updated",
        receiver=create_user,
        triggered_by=create_user,
    )
    mention = Notification.objects.create(
        workspace=workspace,
        entity_name="issue",
        title="You were mentioned",
        sender="issue_mentioned",
        receiver=create_user,
        triggered_by=create_user,
    )
    return regular, mention


@pytest.mark.contract
@pytest.mark.django_db
class TestNotificationCompatResponses:
    @pytest.mark.parametrize(
        ("suffix", "view_class"),
        [
            ("", NotificationV1ViewSet),
            ("unread/", UnreadNotificationV1Endpoint),
        ],
    )
    def test_routes_use_api_key_compat_views(self, workspace, suffix, view_class):
        match = resolve(f"/api/v1/workspaces/{workspace.slug}/users/notifications/{suffix}")
        resolved_view_class = getattr(match.func, "cls", getattr(match.func, "view_class", None))

        assert resolved_view_class is view_class
        assert view_class.authentication_classes == [APIKeyAuthentication]

    def test_paginated_notifications_response_matches_app_api(
        self, session_client, api_key_client, workspace, notifications
    ):
        path = f"workspaces/{workspace.slug}/users/notifications/"
        params = {
            "snoozed": "false",
            "archived": "false",
            "per_page": 300,
            "cursor": "300:0:0",
        }

        app_response = session_client.get(f"/api/{path}", params)
        v1_response = api_key_client.get(f"/api/v1/{path}", params)

        assert app_response.status_code == status.HTTP_200_OK
        assert v1_response.status_code == status.HTTP_200_OK
        assert v1_response.json() == app_response.json()

    def test_unread_notifications_response_matches_app_api(
        self, session_client, api_key_client, workspace, notifications
    ):
        path = f"workspaces/{workspace.slug}/users/notifications/unread/"

        app_response = session_client.get(f"/api/{path}")
        v1_response = api_key_client.get(f"/api/v1/{path}")

        assert app_response.status_code == status.HTTP_200_OK
        assert v1_response.status_code == status.HTTP_200_OK
        assert v1_response.json() == app_response.json()
        assert v1_response.json() == {
            "total_unread_notifications_count": 1,
            "mention_unread_notifications_count": 1,
        }
