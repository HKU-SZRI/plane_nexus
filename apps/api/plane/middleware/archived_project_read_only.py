"""Reject API writes to archived projects while preserving reads and restore."""

import json
import re

from django.http import JsonResponse

from plane.db.models import Project


PROJECT_PATH = re.compile(r"/api(?:/v1)?/workspaces/[^/]+/projects/([^/]+)(?:/|$)")
READ_METHODS = {"GET", "HEAD", "OPTIONS"}


class ArchivedProjectReadOnlyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        if request.method in READ_METHODS:
            return None
        match = PROJECT_PATH.match(request.path)
        if not match:
            return None
        project_id = match.group(1)
        # DELETE on the project's own archive route is the supported restore path.
        suffix = request.path[match.end():].strip("/")
        if request.method == "DELETE" and suffix == "archive":
            return None
        if suffix.endswith("/transfer") and request.body:
            try:
                target_id = json.loads(request.body).get("target_project_id")
            except (ValueError, AttributeError):
                target_id = None
            if target_id:
                target = Project.objects.filter(pk=target_id).only("archived_at").first()
                if target and target.archived_at:
                    return JsonResponse({"error": "Archived projects are read-only"}, status=409)
        project = Project.objects.filter(pk=project_id).only("archived_at").first()
        if not project or not project.archived_at:
            return None
        return JsonResponse({"error": "Archived projects are read-only"}, status=409)
