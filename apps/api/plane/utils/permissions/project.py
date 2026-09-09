# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Compatibility exports for the canonical project permissions."""

from plane.app.permissions.project import (
    ProjectAdminPermission,
    ProjectBasePermission,
    ProjectEntityPermission,
    ProjectLitePermission,
    ProjectMemberPermission,
)

__all__ = [
    "ProjectAdminPermission",
    "ProjectBasePermission",
    "ProjectEntityPermission",
    "ProjectLitePermission",
    "ProjectMemberPermission",
]
