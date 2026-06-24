# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.urls import path

from plane.api.views.compat import WorkspaceFavoriteV1Endpoint

urlpatterns = [
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
