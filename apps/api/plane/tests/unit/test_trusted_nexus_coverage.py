# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from types import SimpleNamespace

import pytest
from django.urls import URLPattern, URLResolver, get_resolver

from plane.api.middleware.api_authentication import API_AUTHENTICATION_CLASSES
from plane.api.rate_limit import TrustedNexusRateThrottle
from plane.app.permissions import ROLE, allow_permission, is_trusted_nexus_call
from plane.app.permissions.page import ProjectPagePermission as AppProjectPagePermission
from plane.app.permissions.project import (
    ProjectAdminPermission as AppProjectAdminPermission,
    ProjectBasePermission as AppProjectBasePermission,
    ProjectEntityPermission as AppProjectEntityPermission,
    ProjectLitePermission as AppProjectLitePermission,
    ProjectMemberPermission as AppProjectMemberPermission,
)
from plane.app.permissions.workspace import (
    WorkSpaceAdminPermission as AppWorkSpaceAdminPermission,
    WorkSpaceBasePermission as AppWorkSpaceBasePermission,
    WorkspaceEntityPermission as AppWorkspaceEntityPermission,
    WorkspaceOwnerPermission as AppWorkspaceOwnerPermission,
    WorkspaceUserPermission as AppWorkspaceUserPermission,
    WorkspaceViewerPermission as AppWorkspaceViewerPermission,
)
from plane.utils.permissions.page import ProjectPagePermission as UtilsProjectPagePermission
from plane.utils.permissions.project import (
    ProjectAdminPermission as UtilsProjectAdminPermission,
    ProjectBasePermission as UtilsProjectBasePermission,
    ProjectEntityPermission as UtilsProjectEntityPermission,
    ProjectLitePermission as UtilsProjectLitePermission,
    ProjectMemberPermission as UtilsProjectMemberPermission,
)
from plane.utils.permissions.workspace import (
    WorkSpaceAdminPermission as UtilsWorkSpaceAdminPermission,
    WorkSpaceBasePermission as UtilsWorkSpaceBasePermission,
    WorkspaceEntityPermission as UtilsWorkspaceEntityPermission,
    WorkspaceOwnerPermission as UtilsWorkspaceOwnerPermission,
    WorkspaceUserPermission as UtilsWorkspaceUserPermission,
    WorkspaceViewerPermission as UtilsWorkspaceViewerPermission,
)


def iter_url_patterns(patterns, prefix=""):
    for pattern in patterns:
        route = f"{prefix}{pattern.pattern}"
        if isinstance(pattern, URLResolver):
            yield from iter_url_patterns(pattern.url_patterns, route)
        elif isinstance(pattern, URLPattern):
            yield route, pattern


@pytest.mark.unit
def test_every_v1_route_uses_shared_authentication_stack():
    resolved_views = []
    for route, pattern in iter_url_patterns(get_resolver().url_patterns):
        if not route.startswith("api/v1/"):
            continue
        view_class = getattr(pattern.callback, "cls", getattr(pattern.callback, "view_class", None))
        assert view_class is not None, f"Could not inspect /{route}"
        # DRF routers add read-only discovery roots. They are not Plane API
        # operations and are not Plane API operations.
        if view_class.__name__ == "APIRootView":
            continue
        resolved_views.append((route, view_class))

    assert resolved_views
    mismatches = [
        (route, view_class.__name__, view_class.authentication_classes)
        for route, view_class in resolved_views
        if tuple(view_class.authentication_classes) != API_AUTHENTICATION_CLASSES
    ]
    assert mismatches == []
    throttle_mismatches = [
        (route, view_class.__name__)
        for route, view_class in resolved_views
        if TrustedNexusRateThrottle not in view_class.throttle_classes
    ]
    assert throttle_mismatches == []


@pytest.mark.unit
@pytest.mark.parametrize(
    ("app_class", "utils_class"),
    [
        (AppProjectPagePermission, UtilsProjectPagePermission),
        (AppProjectAdminPermission, UtilsProjectAdminPermission),
        (AppProjectBasePermission, UtilsProjectBasePermission),
        (AppProjectEntityPermission, UtilsProjectEntityPermission),
        (AppProjectLitePermission, UtilsProjectLitePermission),
        (AppProjectMemberPermission, UtilsProjectMemberPermission),
        (AppWorkSpaceAdminPermission, UtilsWorkSpaceAdminPermission),
        (AppWorkSpaceBasePermission, UtilsWorkSpaceBasePermission),
        (AppWorkspaceEntityPermission, UtilsWorkspaceEntityPermission),
        (AppWorkspaceOwnerPermission, UtilsWorkspaceOwnerPermission),
        (AppWorkspaceUserPermission, UtilsWorkspaceUserPermission),
        (AppWorkspaceViewerPermission, UtilsWorkspaceViewerPermission),
    ],
)
def test_utils_permissions_reexport_canonical_app_classes(app_class, utils_class):
    assert utils_class is app_class


@pytest.mark.unit
@pytest.mark.parametrize(
    "permission_class",
    [
        AppProjectPagePermission,
        AppProjectAdminPermission,
        AppProjectBasePermission,
        AppProjectEntityPermission,
        AppProjectLitePermission,
        AppProjectMemberPermission,
        AppWorkSpaceAdminPermission,
        AppWorkSpaceBasePermission,
        AppWorkspaceEntityPermission,
        AppWorkspaceOwnerPermission,
        AppWorkspaceUserPermission,
        AppWorkspaceViewerPermission,
    ],
)
def test_permission_classes_bypass_only_after_trusted_authentication(permission_class):
    for method in ("GET", "POST", "PATCH", "DELETE"):
        trusted_request = SimpleNamespace(method=method, is_trusted_nexus_call=True)
        assert permission_class().has_permission(trusted_request, SimpleNamespace()) is True


@pytest.mark.unit
def test_trusted_nexus_throttle_is_actor_scoped_and_noops_for_other_requests():
    throttle = TrustedNexusRateThrottle()
    actor_id = "9e471df3-c91b-4c5e-a47a-f5d0d21b8336"
    trusted_request = SimpleNamespace(
        is_trusted_nexus_call=True,
        trusted_nexus_claims={"actor_plane_id": actor_id},
    )

    assert actor_id in throttle.get_cache_key(trusted_request, SimpleNamespace())
    assert throttle.get_cache_key(SimpleNamespace(), SimpleNamespace()) is None


@pytest.mark.unit
def test_allow_permission_bypasses_roles_for_all_trusted_requests():
    called = []

    @allow_permission([ROLE.ADMIN])
    def view(_instance, _request):
        called.append(True)
        return "ok"

    trusted_write = SimpleNamespace(method="POST", is_trusted_nexus_call=True)
    assert view(None, trusted_write) == "ok"
    assert called == [True]

    trusted_read = SimpleNamespace(method="GET", is_trusted_nexus_call=True)
    assert is_trusted_nexus_call(trusted_read) is True
    assert view(None, trusted_read) == "ok"
    assert called == [True, True]
