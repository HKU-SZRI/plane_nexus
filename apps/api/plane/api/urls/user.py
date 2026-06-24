# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.urls import path

from plane.api.views import UserEndpoint, AdminUserApiTokenEndpoint

urlpatterns = [
    path(
        "users/me/",
        UserEndpoint.as_view(http_method_names=["get"]),
        name="users",
    ),
    path(
        "users/<uuid:user_id>/api-tokens/",
        AdminUserApiTokenEndpoint.as_view(http_method_names=["post"]),
        name="admin-user-api-tokens",
    ),
]
