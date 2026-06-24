# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Thin wrappers that re-expose plane.app views under the plane.api URL namespace.
# The only change is swapping BaseSessionAuthentication for APIKeyAuthentication
# so that requests authenticated via X-API-Key header are accepted.

from plane.api.middleware.api_authentication import APIKeyAuthentication
from plane.app.views.cycle.base import CycleViewSet as _CycleViewSet
from plane.app.views.cycle.base import CycleProgressEndpoint as _CycleProgressEndpoint
from plane.app.views.cycle.base import CycleDateCheckEndpoint as _CycleDateCheckEndpoint
from plane.app.views.search.issue import IssueSearchEndpoint as _IssueSearchEndpoint
from plane.app.views.workspace.favorite import WorkspaceFavoriteEndpoint as _WorkspaceFavoriteEndpoint


class CycleV1ViewSet(_CycleViewSet):
    authentication_classes = [APIKeyAuthentication]


class CycleProgressV1Endpoint(_CycleProgressEndpoint):
    authentication_classes = [APIKeyAuthentication]


class CycleDateCheckV1Endpoint(_CycleDateCheckEndpoint):
    authentication_classes = [APIKeyAuthentication]


class IssueSearchV1Endpoint(_IssueSearchEndpoint):
    authentication_classes = [APIKeyAuthentication]


class WorkspaceFavoriteV1Endpoint(_WorkspaceFavoriteEndpoint):
    authentication_classes = [APIKeyAuthentication]
