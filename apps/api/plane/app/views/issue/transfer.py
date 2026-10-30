# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.db import transaction
from django.utils import timezone

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.db.models import (
    FileAsset,
    Issue,
    IssueActivity,
    IssueAssignee,
    IssueComment,
    IssueLink,
    Project,
    State,
)
from plane.utils.host import base_host

from .. import BaseAPIView


def _issue_url(request, workspace_slug, project_id, issue_id):
    return f"{base_host(request=request, is_app=True)}/{workspace_slug}/projects/{project_id}/issues/{issue_id}"


class IssueTransferEndpoint(BaseAPIView):
    """Move an issue to another project.

    Creates a new issue in the target project carrying over description,
    assignees, attachments, comments and activity history, then marks the
    original issue Completed with a "transferred" IssueActivity entry
    (field="project", old_identifier/new_identifier) linking to the new one;
    the new issue gets its own reciprocal entry plus a full copy of the
    original's activity/comment history so its timeline reads as continuous,
    and both issues get an IssueLink pointing at the other. The web app
    renders the activity entries via IssueTransferActivity
    (issue-activity/activity/actions/transfer.tsx), registered for
    field="project" in activity-list.tsx.

    ponytail: no membership/role check on who can trigger this or on the
    target project (product decision for now) — add allow_permission once
    that's needed. Labels/cycles/modules/sub-issues aren't carried over since
    those are project-scoped objects the target project won't have matching
    rows for; state/estimate/type are re-resolved to the target project's
    defaults for the same reason.
    """

    def post(self, request, slug, project_id, issue_id):
        if request.data.get("preserve_identity") is True:
            return self.move_preserving_identity(request, slug, project_id, issue_id)
        target_project_id = request.data.get("target_project_id")
        if not target_project_id:
            return Response({"error": "target_project_id is required"}, status=status.HTTP_400_BAD_REQUEST)
        if str(target_project_id) == str(project_id):
            return Response(
                {"error": "target_project_id must be different from the issue's current project"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        old_issue = Issue.objects.filter(workspace__slug=slug, project_id=project_id, pk=issue_id).first()
        if not old_issue:
            return Response({"error": "The required object does not exist."}, status=status.HTTP_404_NOT_FOUND)

        target_project = Project.objects.filter(workspace__slug=slug, pk=target_project_id).first()
        if not target_project:
            return Response(
                {"error": "target project does not exist in this workspace"}, status=status.HTTP_404_NOT_FOUND
            )

        old_url = _issue_url(request, slug, project_id, old_issue.id)
        old_project_name = old_issue.project.name

        with transaction.atomic():
            new_issue = Issue.objects.create(
                project=target_project,
                name=old_issue.name,
                description_json=old_issue.description_json,
                description_html=old_issue.description_html,
                description_binary=old_issue.description_binary,
                priority=old_issue.priority,
                start_date=old_issue.start_date,
                target_date=old_issue.target_date,
                point=old_issue.point,
                is_draft=old_issue.is_draft,
            )
            new_url = _issue_url(request, slug, target_project.id, new_issue.id)

            # Record the move as regular issue links on both sides, so it
            # shows up in the UI (not just buried in the activity log) and
            # is easy to jump between for tracking.
            IssueLink.objects.create(
                project=target_project,
                issue=new_issue,
                title=f"Source: {old_issue.project.identifier}-{old_issue.sequence_id}",
                url=old_url,
            )
            IssueLink.objects.create(
                project=old_issue.project,
                issue=old_issue,
                title=f"Moved to: {target_project.identifier}-{new_issue.sequence_id}",
                url=new_url,
            )

            # Assignees carry over as-is; not required to be members of the
            # target project (they'll show on the issue but won't gain
            # project access from this alone).
            assignee_ids = list(IssueAssignee.objects.filter(issue=old_issue).values_list("assignee_id", flat=True))
            IssueAssignee.objects.bulk_create(
                [
                    IssueAssignee(
                        issue=new_issue,
                        project=target_project,
                        workspace=target_project.workspace,
                        assignee_id=aid,
                    )
                    for aid in assignee_ids
                ]
            )

            # Attachments: point a new FileAsset row at the same underlying
            # storage object instead of re-uploading the file.
            for old_asset in FileAsset.objects.filter(
                issue=old_issue, entity_type=FileAsset.EntityTypeContext.ISSUE_ATTACHMENT
            ):
                FileAsset.objects.create(
                    asset=old_asset.asset.name,
                    attributes=old_asset.attributes,
                    entity_type=old_asset.entity_type,
                    entity_identifier=str(new_issue.id),
                    user=old_asset.user,
                    workspace=target_project.workspace,
                    project=target_project,
                    issue=new_issue,
                    size=old_asset.size,
                    is_uploaded=old_asset.is_uploaded,
                    storage_metadata=old_asset.storage_metadata,
                )

            # Comments: created one by one (not bulk_create) because
            # IssueComment.save() manages a linked Description row as a side
            # effect. created_at is fixed up afterwards since .save() always
            # stamps auto_now_add fields to "now".
            comment_id_map = {}
            for old_comment in IssueComment.objects.filter(issue=old_issue).order_by("created_at"):
                new_comment = IssueComment(
                    project=target_project,
                    workspace=target_project.workspace,
                    issue=new_issue,
                    comment_json=old_comment.comment_json,
                    comment_html=old_comment.comment_html,
                    attachments=old_comment.attachments,
                    actor=old_comment.actor,
                    access=old_comment.access,
                    edited_at=old_comment.edited_at,
                )
                new_comment.save()
                IssueComment.objects.filter(pk=new_comment.pk).update(created_at=old_comment.created_at)
                comment_id_map[old_comment.id] = new_comment.id

            # Activity history: duplicated onto the new issue. The original
            # issue's own activity rows are left untouched.
            old_activities = list(IssueActivity.objects.filter(issue=old_issue).order_by("created_at"))
            new_activities = IssueActivity.objects.bulk_create(
                [
                    IssueActivity(
                        project=target_project,
                        workspace=target_project.workspace,
                        issue=new_issue,
                        verb=activity.verb,
                        field=activity.field,
                        old_value=activity.old_value,
                        new_value=activity.new_value,
                        comment=activity.comment,
                        attachments=activity.attachments,
                        issue_comment_id=comment_id_map.get(activity.issue_comment_id),
                        actor=activity.actor,
                        old_identifier=activity.old_identifier,
                        new_identifier=activity.new_identifier,
                        epoch=activity.epoch,
                    )
                    for activity in old_activities
                ]
            )
            for activity, new_activity in zip(old_activities, new_activities):
                IssueActivity.objects.filter(pk=new_activity.pk).update(created_at=activity.created_at)

            # Close the original.
            completed_state = (
                State.objects.filter(project=old_issue.project, group="completed").order_by("sequence").first()
            )
            previous_state_name = old_issue.state.name if old_issue.state else None
            if completed_state:
                old_issue.state = completed_state
            old_issue.save()

            now_epoch = timezone.now().timestamp()
            IssueActivity.objects.create(
                project=old_issue.project,
                issue=old_issue,
                verb="transferred",
                field="project",
                old_value=old_project_name,
                new_value=target_project.name,
                comment=f"Moved to {target_project.name}: {new_url}",
                actor=request.user,
                new_identifier=new_issue.id,
                epoch=now_epoch,
            )
            # Recorded explicitly since the direct .save() above bypasses the
            # normal update path that would otherwise log this state change;
            # created right after the "transferred" entry so it reads as the
            # next event in the original issue's timeline.
            if completed_state:
                IssueActivity.objects.create(
                    project=old_issue.project,
                    issue=old_issue,
                    verb="updated",
                    field="state",
                    old_value=previous_state_name,
                    new_value=completed_state.name,
                    actor=request.user,
                    epoch=now_epoch,
                )
            IssueActivity.objects.create(
                project=target_project,
                issue=new_issue,
                verb="transferred",
                field="project",
                old_value=old_project_name,
                new_value=target_project.name,
                comment=f"Moved from {old_project_name}: {old_url}",
                actor=request.user,
                old_identifier=old_issue.id,
                epoch=now_epoch,
            )

        return Response(
            {
                "id": str(new_issue.id),
                "project_id": str(target_project.id),
                "sequence_id": new_issue.sequence_id,
                "url": new_url,
                "old_issue": {
                    "id": str(old_issue.id),
                    "url": old_url,
                    "state": old_issue.state.name if old_issue.state else None,
                },
            },
            status=status.HTTP_201_CREATED,
        )


    def get(self, request, slug, project_id, issue_id):
        return Response({"identity_preserving_move": True})

    def move_preserving_identity(self, request, slug, project_id, issue_id):
        """Idempotent in-place move for Nexus; legacy transfer remains unchanged."""
        from django.db import connection, models
        from plane.db.models import ProjectMember, WorkspaceMember, IssueSequence, IssueLabel, Label, CycleIssue, ModuleIssue
        from plane.utils.uuid import convert_uuid_to_integer

        target_id = request.data.get("target_project_id")
        if not target_id or str(target_id) == str(project_id):
            return Response({"error": "A different target project is required"}, status=400)
        with transaction.atomic():
            item = Issue.objects.select_for_update().filter(pk=issue_id, workspace__slug=slug).first()
            target = Project.objects.filter(pk=target_id, workspace__slug=slug).first()
            if not item or not target:
                return Response({"error": "Work item or target not found"}, status=404)
            if not WorkspaceMember.objects.filter(
                workspace_id=item.workspace_id, member=request.user, is_active=True
            ).exists():
                return Response({"error": "Active workspace membership required"}, status=403)
            source_membership = ProjectMember.objects.filter(
                project_id=project_id, member=request.user, is_active=True
            ).first()
            if not source_membership or (
                source_membership.role < 20 and item.created_by_id != request.user.id
            ):
                return Response({"error": "Source work item management permission required"}, status=403)
            if not ProjectMember.objects.filter(
                project=target, member=request.user, is_active=True, role__gte=15
            ).exists():
                return Response({"error": "Target creation permission required"}, status=403)
            if item.archived_at or item.project.archived_at or target.archived_at:
                return Response({"error": "Archived resources are read-only"}, status=409)
            if str(item.project_id) == str(target_id):
                return Response({"id": str(item.id), "project_id": str(target.id),
                                 "sequence_id": item.sequence_id, "identity_preserved": True})
            if str(item.project_id) != str(project_id):
                return Response({"error": "Work item was moved elsewhere"}, status=409)
            state = None
            if item.state_id:
                state = State.objects.filter(project=target, name=item.state.name, group=item.state.group).first()
                if not state:
                    return Response({"error": "Target requires a matching status name and group"}, status=409)
            # Allocate a new project display number; the immutable Work Item ID is retained.
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", [convert_uuid_to_integer(target.id)])
            number = (IssueSequence.objects.filter(project=target).aggregate(
                largest=models.Max("sequence"))["largest"] or 0) + 1
            if CycleIssue.objects.filter(issue=item).exists() or ModuleIssue.objects.filter(issue=item).exists():
                return Response({"error": "Remove project-scoped cycle/module membership before moving"}, status=409)
            labels = list(IssueLabel.objects.filter(issue=item).select_related("label"))
            target_labels = {label.name: label for label in Label.objects.filter(project=target)}
            if any(link.label.name not in target_labels for link in labels):
                return Response({"error": "Target requires matching labels"}, status=409)
            if request.data.get("validate_only") is True:
                return Response({"valid": True, "identity_preserving_move": True})
            old_project = item.project_id
            Issue.objects.filter(pk=item.pk).update(
                project=target, state=state, sequence_id=number, updated_at=timezone.now()
            )
            IssueSequence.objects.create(project=target, workspace=target.workspace, issue=item, sequence=number)
            # Child rows keep their IDs, actors, timestamps, and underlying assets.
            # Historical sequence allocations must remain attached to their original project.
            for relation in Issue._meta.related_objects:
                model = relation.related_model
                field = relation.field
                if model in (Issue, IssueSequence) or not hasattr(model, "project_id"):
                    continue
                if field.name != "issue":
                    continue
                model.objects.filter(**{field.name: item}).update(project=target)
            for link in labels:
                IssueLabel.objects.filter(pk=link.pk).update(label=target_labels[link.label.name])
            active = WorkspaceMember.objects.filter(workspace=target.workspace, is_active=True).values_list("member_id", flat=True)
            eligible = set(str(uid) for uid in ProjectMember.objects.filter(
                project=target, is_active=True, member_id__in=active).values_list("member_id", flat=True))
            if "eligible_assignee_ids" in request.data:
                eligible &= set(request.data["eligible_assignee_ids"])
            removed = list(IssueAssignee.objects.filter(issue=item).exclude(assignee_id__in=eligible).values_list("assignee_id",flat=True))
            IssueAssignee.objects.filter(issue=item).exclude(assignee_id__in=eligible).delete()
            if removed:
                IssueActivity.objects.create(project=target,workspace=target.workspace,issue=item,actor=request.user,
                    verb="updated",field="assignees",old_value=",".join(str(uid) for uid in removed),
                    new_value="",comment="Removed ineligible assignees on move",epoch=timezone.now().timestamp())
            IssueActivity.objects.create(
                project=target, workspace=target.workspace, issue=item, actor=request.user,
                verb="updated", field="project", old_value=str(old_project), new_value=str(target.id),
                old_identifier=old_project, new_identifier=target.id, epoch=timezone.now().timestamp(),
                comment="Moved without changing work item identity",
            )
            return Response({"id": str(item.id), "project_id": str(target.id),
                             "sequence_id": number, "identity_preserved": True})
