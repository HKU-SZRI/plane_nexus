# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only

import pytest
from django.urls import resolve
from django.utils import timezone
from freezegun import freeze_time
from rest_framework import status

from plane.api.middleware.api_authentication import APIKeyAuthentication
from plane.api.views.compat import (
    MarkAllReadNotificationV1ViewSet,
    NotificationV1ViewSet,
    UnreadNotificationV1Endpoint,
)
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
            ("mark-all-read/", MarkAllReadNotificationV1ViewSet),
            ("unread/", UnreadNotificationV1Endpoint),
        ],
    )
    def test_routes_use_api_key_compat_views(self, workspace, suffix, view_class):
        match = resolve(f"/api/v1/workspaces/{workspace.slug}/users/notifications/{suffix}")
        resolved_view_class = getattr(match.func, "cls", getattr(match.func, "view_class", None))

        assert resolved_view_class is view_class
        assert view_class.authentication_classes == [APIKeyAuthentication]

    @pytest.mark.parametrize(
        ("suffix", "methods"),
        [
            ("{notification_id}/", {"get", "patch", "delete"}),
            ("{notification_id}/read/", {"post", "delete"}),
            ("{notification_id}/archive/", {"post", "delete"}),
        ],
    )
    def test_notification_action_routes_use_compat_view(self, workspace, notifications, suffix, methods):
        path = suffix.format(notification_id=notifications[0].id)
        match = resolve(f"/api/v1/workspaces/{workspace.slug}/users/notifications/{path}")
        resolved_view_class = getattr(match.func, "cls", getattr(match.func, "view_class", None))

        assert resolved_view_class is NotificationV1ViewSet
        assert methods.issubset(match.func.actions)

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

    @freeze_time("2026-08-24T08:00:00Z")
    def test_mark_read_and_unread_responses_match_app_api(
        self, session_client, api_key_client, workspace, notifications
    ):
        notification = notifications[0]
        path = f"workspaces/{workspace.slug}/users/notifications/{notification.id}/read/"

        app_read_response = session_client.post(f"/api/{path}")
        Notification.objects.filter(id=notification.id).update(read_at=None)
        v1_read_response = api_key_client.post(f"/api/v1/{path}")

        assert app_read_response.status_code == status.HTTP_200_OK
        assert v1_read_response.status_code == app_read_response.status_code
        assert v1_read_response.json() == app_read_response.json()

        Notification.objects.filter(id=notification.id).update(read_at=timezone.now())
        app_unread_response = session_client.delete(f"/api/{path}")
        Notification.objects.filter(id=notification.id).update(read_at=timezone.now())
        v1_unread_response = api_key_client.delete(f"/api/v1/{path}")

        assert app_unread_response.status_code == status.HTTP_200_OK
        assert v1_unread_response.status_code == app_unread_response.status_code
        assert v1_unread_response.json() == app_unread_response.json()
        assert v1_unread_response.json()["read_at"] is None

    @freeze_time("2026-08-24T08:00:00Z")
    def test_snooze_response_matches_app_api(self, session_client, api_key_client, workspace, notifications):
        notification = notifications[0]
        path = f"workspaces/{workspace.slug}/users/notifications/{notification.id}/"
        payload = {"snoozed_till": "2026-08-25T08:00:00Z"}

        app_response = session_client.patch(f"/api/{path}", payload, format="json")
        Notification.objects.filter(id=notification.id).update(snoozed_till=None)
        v1_response = api_key_client.patch(f"/api/v1/{path}", payload, format="json")

        assert app_response.status_code == status.HTTP_200_OK
        assert v1_response.status_code == app_response.status_code
        assert v1_response.json() == app_response.json()

    @freeze_time("2026-08-24T08:00:00Z")
    def test_mark_all_read_response_matches_app_api(self, session_client, api_key_client, workspace, notifications):
        path = f"workspaces/{workspace.slug}/users/notifications/mark-all-read/"
        payload = {"snoozed": False, "archived": False, "type": "all"}

        app_response = session_client.post(f"/api/{path}", payload, format="json")
        Notification.objects.filter(id__in=[notification.id for notification in notifications]).update(read_at=None)
        v1_response = api_key_client.post(f"/api/v1/{path}", payload, format="json")

        assert app_response.status_code == status.HTTP_200_OK
        assert v1_response.status_code == app_response.status_code
        assert v1_response.json() == app_response.json() == {"message": "Successful"}
        assert not Notification.objects.filter(
            id__in=[notification.id for notification in notifications], read_at__isnull=True
        ).exists()
