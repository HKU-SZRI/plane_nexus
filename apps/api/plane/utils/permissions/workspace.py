# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Compatibility exports for the canonical workspace permissions."""

from plane.app.permissions.workspace import (
    WorkSpaceAdminPermission,
    WorkSpaceBasePermission,
    WorkspaceEntityPermission,
    WorkspaceOwnerPermission,
    WorkspaceUserPermission,
    WorkspaceViewerPermission,
)

__all__ = [
    "WorkSpaceAdminPermission",
    "WorkSpaceBasePermission",
    "WorkspaceEntityPermission",
    "WorkspaceOwnerPermission",
    "WorkspaceUserPermission",
    "WorkspaceViewerPermission",
]
