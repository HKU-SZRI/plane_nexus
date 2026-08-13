# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Thin wrappers that re-expose plane.app views under the plane.api URL namespace.
# The main change is swapping BaseSessionAuthentication for APIKeyAuthentication
# so that requests authenticated via X-API-Key header are accepted. IssueV1ViewSet
# additionally shims the wire contract back to what the public /api/v1/ docs
# promise (plane-mcp-server and other API-key clients are built against that
# contract, not plane.app's internal one) — see _normalize_write_fields below.

import re

from django.contrib.postgres.aggregates import ArrayAgg
from django.contrib.postgres.fields import ArrayField
from django.db.models import Q, UUIDField, Value
from django.db.models.functions import Coalesce
from rest_framework import status
from rest_framework.response import Response

from plane.api.middleware.api_authentication import APIKeyAuthentication
from plane.api.views.issue import IssueSearchEndpoint as _WorkItemSearchEndpoint
from plane.app.views.asset.v2 import WorkspaceFileAssetEndpoint as _WorkspaceFileAssetEndpoint
from plane.app.views.asset.v2 import ProjectAssetEndpoint as _ProjectAssetEndpoint
from plane.app.views.asset.v2 import ProjectBulkAssetEndpoint as _ProjectBulkAssetEndpoint
from plane.app.views.cycle.base import CycleViewSet as _CycleViewSet
from plane.app.views.cycle.base import CycleDateCheckEndpoint as _CycleDateCheckEndpoint
from plane.app.views.cycle.base import CycleProgressEndpoint as _CycleProgressEndpoint
from plane.app.views.estimate.base import BulkEstimatePointEndpoint as _BulkEstimatePointEndpoint
from plane.app.views.issue.base import IssueDetailIdentifierEndpoint as _IssueDetailIdentifierEndpoint
from plane.app.views.issue.base import ProjectUserDisplayPropertyEndpoint as _ProjectUserDisplayPropertyEndpoint
from plane.app.views.issue.base import IssueViewSet as _IssueViewSet
from plane.app.views.issue.link import IssueLinkViewSet as _IssueLinkViewSet
from plane.app.views.issue.reaction import IssueReactionViewSet as _IssueReactionViewSet
from plane.app.views.issue.relation import IssueRelationViewSet as _IssueRelationViewSet
from plane.app.views.issue.sub_issue import SubIssuesEndpoint as _SubIssuesEndpoint
from plane.app.views.issue.version import WorkItemDescriptionVersionEndpoint as _WorkItemDescriptionVersionEndpoint
from plane.app.views.module.base import ModuleViewSet as _ModuleViewSet
from plane.app.views.module.issue import ModuleIssueViewSet as _ModuleIssueViewSet
from plane.app.views.page.base import PagesDescriptionViewSet as _PagesDescriptionViewSet
from plane.app.views.page.base import PageViewSet as _PageViewSet
from plane.app.views.project.base import ProjectIdentifierEndpoint as _ProjectIdentifierEndpoint
from plane.app.views.search.issue import IssueSearchEndpoint as _IssueSearchEndpoint
from plane.app.views.state.base import StateViewSet as _StateViewSet
from plane.app.views.view.base import IssueViewViewSet as _IssueViewViewSet
from plane.app.views.workspace.favorite import WorkspaceFavoriteEndpoint as _WorkspaceFavoriteEndpoint
from plane.db.models import Issue, Label

class _PaginatedListShimMixin:
    """Wrap flat-array list responses in the public API's pagination envelope.

    plane.app list views return a bare JSON array, but the public /api/v1/
    docs (and every SDK model named Paginated*Response, all of whose
    pagination fields are required) promise `{"results": [...], ...}`.
    nexus-ui already accepts both shapes (unwrapPaginatedResults), so the
    envelope is the safe common contract. Single-page semantics: everything
    the upstream view returned is one page.
    """

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        if response.status_code == status.HTTP_200_OK and isinstance(response.data, list):
            items = response.data
            response.data = {
                "results": items,
                "count": len(items),
                "total_count": len(items),
                "total_results": len(items),
                "total_pages": 1,
                "next_cursor": "",
                "prev_cursor": "",
                "next_page_results": False,
                "prev_page_results": False,
            }
        return response


class CycleV1ViewSet(_PaginatedListShimMixin, _CycleViewSet):
    authentication_classes = [APIKeyAuthentication]


class CycleProgressV1Endpoint(_CycleProgressEndpoint):
    authentication_classes = [APIKeyAuthentication]


class CycleDateCheckV1Endpoint(_CycleDateCheckEndpoint):
    authentication_classes = [APIKeyAuthentication]


class IssueSearchV1Endpoint(_IssueSearchEndpoint):
    authentication_classes = [APIKeyAuthentication]


class WorkspaceFavoriteV1Endpoint(_WorkspaceFavoriteEndpoint):
    authentication_classes = [APIKeyAuthentication]


class IssueV1ViewSet(_IssueViewSet):
    """plane.app.IssueViewSet under /api/v1/, API-key authenticated.

    plane.app's own serializer (IssueCreateSerializer) uses assignee_ids/label_ids
    and partial_update() always returns 204 No Content — that's fine for the web
    app, which never reads those responses, but it breaks API-key clients (e.g.
    plane-mcp-server) built against the public API's documented assignees/labels
    field names and JSON-body responses. Translate on the way in, backfill a body
    on the way out, so both contracts keep working through one shared code path.
    """

    authentication_classes = [APIKeyAuthentication]

    _WRITE_FIELD_ALIASES = {"assignees": "assignee_ids", "labels": "label_ids"}

    def _normalize_write_fields(self, request) -> None:
        data = request.data
        if not isinstance(data, dict):
            return
        for public_name, internal_name in self._WRITE_FIELD_ALIASES.items():
            if public_name in data and internal_name not in data:
                data[internal_name] = data.pop(public_name)

    def create(self, request, slug, project_id):
        self._normalize_write_fields(request)
        return super().create(request, slug=slug, project_id=project_id)

    def partial_update(self, request, slug, project_id, pk=None):
        self._normalize_write_fields(request)
        response = super().partial_update(request, slug=slug, project_id=project_id, pk=pk)
        if response.status_code == status.HTTP_204_NO_CONTENT:
            return self.retrieve(request, slug=slug, project_id=project_id, pk=pk)
        return response

    def retrieve(self, request, slug, project_id, pk=None):
        response = super().retrieve(request, slug=slug, project_id=project_id, pk=pk)
        # The public API's WorkItemDetail model reads assignees as a list of user
        # objects (`assignees: list[UserLite]`), but plane.app's serializer only
        # emits the flat `assignee_ids` list — client SDKs built against the
        # public docs (e.g. plane-mcp-server's "read current assignees, then
        # add/remove one" tools) silently see an empty list without this mirror.
        if response.status_code == status.HTTP_200_OK and isinstance(response.data, dict):
            assignee_ids = response.data.get("assignee_ids") or []
            response.data.setdefault("assignees", [{"id": uid} for uid in assignee_ids])
            # Same mirror for labels, needed by manage_work_item_label's
            # read-modify-write. The SDK's Label model requires `name`, so a
            # bare-id mirror won't validate — fetch names in one query.
            label_ids = response.data.get("label_ids") or []
            if "labels" not in response.data:
                response.data["labels"] = (
                    list(Label.objects.filter(id__in=label_ids).values("id", "name"))
                    if label_ids
                    else []
                )
        return response


class ModuleIssueV1ViewSet(_ModuleIssueViewSet):
    authentication_classes = [APIKeyAuthentication]


class ModuleV1ViewSet(_PaginatedListShimMixin, _ModuleViewSet):
    authentication_classes = [APIKeyAuthentication]


class IssueViewV1ViewSet(_IssueViewViewSet):
    authentication_classes = [APIKeyAuthentication]


class PageV1ViewSet(_PaginatedListShimMixin, _PageViewSet):
    authentication_classes = [APIKeyAuthentication]


class PageDescriptionV1ViewSet(_PagesDescriptionViewSet):
    authentication_classes = [APIKeyAuthentication]


class ProjectAssetV1Endpoint(_ProjectAssetEndpoint):
    authentication_classes = [APIKeyAuthentication]


class ProjectBulkAssetV1Endpoint(_ProjectBulkAssetEndpoint):
    authentication_classes = [APIKeyAuthentication]


class WorkspaceFileAssetV1Endpoint(_WorkspaceFileAssetEndpoint):
    authentication_classes = [APIKeyAuthentication]


class ProjectIdentifierV1Endpoint(_ProjectIdentifierEndpoint):
    authentication_classes = [APIKeyAuthentication]


class ProjectUserDisplayPropertyV1Endpoint(_ProjectUserDisplayPropertyEndpoint):
    authentication_classes = [APIKeyAuthentication]


class StateV1ViewSet(_StateViewSet):
    authentication_classes = [APIKeyAuthentication]


class BulkEstimatePointV1Endpoint(_BulkEstimatePointEndpoint):
    authentication_classes = [APIKeyAuthentication]


class WorkItemDescriptionVersionV1Endpoint(_WorkItemDescriptionVersionEndpoint):
    authentication_classes = [APIKeyAuthentication]


class IssueRelationV1ViewSet(_IssueRelationViewSet):
    authentication_classes = [APIKeyAuthentication]


class IssueLinkV1ViewSet(_IssueLinkViewSet):
    authentication_classes = [APIKeyAuthentication]


class IssueReactionV1ViewSet(_IssueReactionViewSet):
    authentication_classes = [APIKeyAuthentication]


class SubIssuesV1Endpoint(_SubIssuesEndpoint):
    authentication_classes = [APIKeyAuthentication]


class IssueDetailIdentifierV1Endpoint(_IssueDetailIdentifierEndpoint):
    authentication_classes = [APIKeyAuthentication]


class WorkItemSearchV1Endpoint(_WorkItemSearchEndpoint):
    """GET /work-items/search/, reimplemented on top of the same query the
    upstream IssueSearchEndpoint.get() runs, plus:

    - A `q` alias for the `search` query param: the endpoint's own OpenAPI
      docs (SEARCH_PARAMETER_REQUIRED) name the param `search`, but
      plane-mcp-server's SDK sends `q` (plane/api/work_items/base.py:
      `search_params = {"q": query}`) — every call silently matched zero
      results. Neither bug is caused by this repo's routing.
    - A string-cast `sequence_id`: it's a Django IntegerField, the SDK's
      WorkItemSearchItem.sequence_id is typed `str` — parsing failed.
    - Actual `fields=`/`expand=` support. WorkItemSearchItem is
      `extra="allow"`, so any additional keys we add just ride along
      un-validated; nothing here needs a matching typed field on the SDK
      side. `fields` accepts a whitelist of plain Issue columns; `expand`
      accepts state/project/assignees/labels as nested objects. Both were
      silently ignored (upstream never read request.query_params for them).
    """

    _EXTRA_FIELD_WHITELIST = {
        "priority", "type_id", "description_html", "created_at", "updated_at",
        "start_date", "target_date", "sort_order",
    }

    def get(self, request, slug):
        query_params = request.query_params
        query = query_params.get("search") or query_params.get("q")
        limit = query_params.get("limit", 10)
        workspace_search = query_params.get("workspace_search", "false")
        project_id = query_params.get("project_id", False)
        requested_fields = {f.strip() for f in (query_params.get("fields") or "").split(",") if f.strip()}
        requested_expand = {f.strip() for f in (query_params.get("expand") or "").split(",") if f.strip()}

        if not query:
            return Response({"issues": []}, status=status.HTTP_200_OK)

        search_fields = ["name", "sequence_id", "project__identifier"]
        q = Q()
        for field in search_fields:
            if field == "sequence_id":
                # Match whole integers only (exclude decimal numbers)
                for sequence_id in re.findall(r"\b\d+\b", query):
                    q |= Q(**{"sequence_id": sequence_id})
            else:
                q |= Q(**{f"{field}__icontains": query})

        issues = Issue.issue_objects.filter(
            q,
            project__project_projectmember__member=request.user,
            project__project_projectmember__is_active=True,
            project__archived_at__isnull=True,
            workspace__slug=slug,
        )
        if workspace_search == "false" and project_id:
            issues = issues.filter(project_id=project_id)

        value_fields = ["name", "id", "sequence_id", "project__identifier", "project_id", "workspace__slug"]
        value_fields += sorted(requested_fields & self._EXTRA_FIELD_WHITELIST)

        if "state" in requested_expand or "state" in requested_fields:
            value_fields += ["state_id", "state__name", "state__group"]
        if "project" in requested_expand or "project" in requested_fields:
            value_fields += ["project__name"]

        empty_uuid_array = Value([], output_field=ArrayField(UUIDField()))
        if "assignees" in requested_expand:
            issues = issues.annotate(
                _assignee_ids=Coalesce(
                    ArrayAgg("assignees__id", distinct=True, filter=Q(assignees__id__isnull=False)),
                    empty_uuid_array,
                )
            )
            value_fields.append("_assignee_ids")
        if "labels" in requested_expand:
            issues = issues.annotate(
                _label_ids=Coalesce(
                    ArrayAgg("labels__id", distinct=True, filter=Q(labels__id__isnull=False)),
                    empty_uuid_array,
                )
            )
            value_fields.append("_label_ids")

        issue_results = list(issues.distinct().values(*value_fields)[: int(limit)])
        for row in issue_results:
            row["sequence_id"] = str(row["sequence_id"])
            if "state__name" in row:
                row["state"] = {
                    "id": row.pop("state_id", None),
                    "name": row.pop("state__name", None),
                    "group": row.pop("state__group", None),
                }
            if "project__name" in row:
                row["project"] = {
                    "id": row.get("project_id"),
                    "identifier": row.get("project__identifier"),
                    "name": row.pop("project__name"),
                }
            if "_assignee_ids" in row:
                row["assignees"] = [{"id": uid} for uid in row.pop("_assignee_ids")]
            if "_label_ids" in row:
                row["labels"] = [{"id": lid} for lid in row.pop("_label_ids")]

        return Response({"issues": issue_results}, status=status.HTTP_200_OK)
