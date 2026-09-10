# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import pytest

from plane.db.models import (
    FileAsset,
    Issue,
    IssueActivity,
    IssueAssignee,
    IssueComment,
    IssueLink,
    Project,
    ProjectMember,
    State,
    User,
)


@pytest.fixture
def other_member(db):
    """A second user who is a member of the source project only."""
    user = User.objects.create(email="other-member@plane.so", username="other-member", first_name="Other")
    user.set_password("test-password")
    user.save()
    return user


@pytest.fixture
def source_project(db, workspace, create_user, other_member):
    project = Project.objects.create(
        name="Source Project",
        identifier="SRC",
        workspace=workspace,
        created_by=create_user,
    )
    ProjectMember.objects.create(project=project, member=create_user, role=20, is_active=True)
    ProjectMember.objects.create(project=project, member=other_member, role=15, is_active=True)
    State.objects.create(project=project, name="Backlog", color="#000", group="backlog", default=True)
    State.objects.create(project=project, name="Done", color="#000", group="completed")
    return project


@pytest.fixture
def target_project(db, workspace, create_user):
    project = Project.objects.create(
        name="Target Project",
        identifier="TGT",
        workspace=workspace,
        created_by=create_user,
    )
    ProjectMember.objects.create(project=project, member=create_user, role=20, is_active=True)
    State.objects.create(project=project, name="Backlog", color="#000", group="backlog", default=True)
    State.objects.create(project=project, name="Done", color="#000", group="completed")
    return project


@pytest.fixture
def source_issue(db, source_project, other_member, create_user):
    issue = Issue.objects.create(
        name="Cross-project work item",
        project=source_project,
        created_by=create_user,
        description_html="<p>original description</p>",
    )
    IssueAssignee.objects.create(issue=issue, project=source_project, assignee=other_member)
    FileAsset.objects.create(
        asset="issues/some-upload.png",
        entity_type=FileAsset.EntityTypeContext.ISSUE_ATTACHMENT,
        workspace=source_project.workspace,
        project=source_project,
        issue=issue,
        user=create_user,
        is_uploaded=True,
        size=123,
    )
    IssueComment.objects.create(
        project=source_project,
        issue=issue,
        comment_html="<p>a comment</p>",
        actor=other_member,
    )
    IssueActivity.objects.create(
        project=source_project,
        issue=issue,
        verb="updated",
        field="priority",
        old_value="none",
        new_value="high",
        actor=create_user,
    )
    return issue


@pytest.mark.contract
@pytest.mark.django_db
class TestIssueTransfer:
    def test_transfer_creates_new_issue_and_closes_original(
        self, api_key_client, source_project, target_project, source_issue, other_member
    ):
        old_activity_count = IssueActivity.objects.filter(issue=source_issue).count()

        url = (
            f"/api/v1/workspaces/{source_project.workspace.slug}/projects/{source_project.id}"
            f"/work-items/{source_issue.id}/transfer/"
        )
        response = api_key_client.post(url, {"target_project_id": str(target_project.id)}, format="json")

        assert response.status_code == 201, response.data
        new_issue = Issue.objects.get(pk=response.data["id"])
        assert new_issue.project_id == target_project.id
        assert new_issue.description_html == source_issue.description_html

        source_issue.refresh_from_db()
        assert source_issue.state.group == "completed"
        assert source_issue.completed_at is not None

        # Assignee carries over even though other_member isn't a member of the target project.
        assert set(IssueAssignee.objects.filter(issue=new_issue).values_list("assignee_id", flat=True)) == {
            other_member.id
        }
        assert not ProjectMember.objects.filter(project=target_project, member=other_member).exists()

        # Attachment duplicated onto the new issue, same underlying storage key.
        new_assets = list(FileAsset.objects.filter(issue=new_issue))
        assert len(new_assets) == 1
        assert new_assets[0].asset.name == "issues/some-upload.png"

        # The move is linked bidirectionally: new issue points back to the
        # source, and the source points forward to the new issue.
        source_links = list(IssueLink.objects.filter(issue=new_issue))
        assert len(source_links) == 1
        assert str(source_issue.id) in source_links[0].url

        old_links = list(IssueLink.objects.filter(issue=source_issue))
        assert len(old_links) == 1
        assert str(new_issue.id) in old_links[0].url

        # Comment duplicated with content + original actor preserved.
        new_comments = list(IssueComment.objects.filter(issue=new_issue))
        assert len(new_comments) == 1
        assert new_comments[0].comment_html == "<p>a comment</p>"
        assert new_comments[0].actor_id == other_member.id

        # Original issue keeps its full history plus a "transferred out" entry
        # and a "state changed to Done" entry (right after it, since closing
        # the issue happens via a direct .save() that skips the normal
        # activity-logging update path); the new issue gets a full copy of
        # that history plus its own "transferred in" entry.
        assert IssueActivity.objects.filter(issue=source_issue).count() == old_activity_count + 2
        assert IssueActivity.objects.filter(issue=new_issue).count() == old_activity_count + 1
        assert IssueActivity.objects.filter(issue=new_issue, verb="updated", field="priority").exists()

        old_transfer_activity = IssueActivity.objects.get(issue=source_issue, verb="transferred")
        new_transfer_activity = IssueActivity.objects.get(issue=new_issue, verb="transferred")
        assert old_transfer_activity.field == "project"
        assert old_transfer_activity.new_identifier == new_issue.id
        assert old_transfer_activity.new_value == "Target Project"
        assert new_transfer_activity.field == "project"
        assert new_transfer_activity.old_identifier == source_issue.id
        assert new_transfer_activity.old_value == "Source Project"

        state_change_activity = IssueActivity.objects.get(issue=source_issue, verb="updated", field="state")
        assert state_change_activity.old_value == "Backlog"
        assert state_change_activity.new_value == "Done"
        assert state_change_activity.created_at >= old_transfer_activity.created_at

    def test_transfer_rejects_same_project(self, api_key_client, source_project, source_issue):
        url = (
            f"/api/v1/workspaces/{source_project.workspace.slug}/projects/{source_project.id}"
            f"/work-items/{source_issue.id}/transfer/"
        )
        response = api_key_client.post(url, {"target_project_id": str(source_project.id)}, format="json")
        assert response.status_code == 400

    def test_transfer_rejects_unknown_target_project(self, api_key_client, source_project, source_issue):
        import uuid

        url = (
            f"/api/v1/workspaces/{source_project.workspace.slug}/projects/{source_project.id}"
            f"/work-items/{source_issue.id}/transfer/"
        )
        response = api_key_client.post(url, {"target_project_id": str(uuid.uuid4())}, format="json")
        assert response.status_code == 404
