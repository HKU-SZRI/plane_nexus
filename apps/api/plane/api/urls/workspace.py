# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.urls import path

from plane.api.views.compat import (
    MarkAllReadNotificationV1ViewSet,
    NotificationV1ViewSet,
    UnreadNotificationV1Endpoint,
    WorkspaceFavoriteV1Endpoint,
)

urlpatterns = [
    path(
        "workspaces/<str:slug>/users/notifications/",
        NotificationV1ViewSet.as_view({"get": "list"}),
        name="notifications",
    ),
    path(
        "workspaces/<str:slug>/users/notifications/<uuid:pk>/",
        NotificationV1ViewSet.as_view({"get": "retrieve", "patch": "partial_update", "delete": "destroy"}),
        name="notification-detail",
    ),
    path(
        "workspaces/<str:slug>/users/notifications/<uuid:pk>/read/",
        NotificationV1ViewSet.as_view({"post": "mark_read", "delete": "mark_unread"}),
        name="notification-read",
    ),
    path(
        "workspaces/<str:slug>/users/notifications/<uuid:pk>/archive/",
        NotificationV1ViewSet.as_view({"post": "archive", "delete": "unarchive"}),
        name="notification-archive",
    ),
    path(
        "workspaces/<str:slug>/users/notifications/unread/",
        UnreadNotificationV1Endpoint.as_view(http_method_names=["get"]),
        name="unread-notifications",
    ),
    path(
        "workspaces/<str:slug>/users/notifications/mark-all-read/",
        MarkAllReadNotificationV1ViewSet.as_view({"post": "create"}),
        name="mark-all-read-notifications",
    ),
    path(
        "workspaces/<str:slug>/user-favorites/",
        WorkspaceFavoriteV1Endpoint.as_view(),
        name="user-favorites",
    ),
    path(
        "workspaces/<str:slug>/user-favorites/<uuid:favorite_id>/",
        WorkspaceFavoriteV1Endpoint.as_view(),
        name="user-favorites-detail",
    ),
]
