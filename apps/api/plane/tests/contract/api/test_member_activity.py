# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.utils import timezone

from plane.db.models import (
    Issue,
    IssueActivity,
    IssueAssignee,
    Project,
    ProjectMember,
    State,
    User,
    Workspace,
    WorkspaceMember,
)

pytestmark = [pytest.mark.contract, pytest.mark.django_db]


@pytest.fixture(autouse=True)
def isolated_infrastructure(settings):
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    with patch("celery.app.task.Task.delay"):
        yield


@pytest.fixture
def evidence(workspace, create_user):
    end = timezone.now()
    start = end - timedelta(days=5)
    WorkspaceMember.objects.filter(workspace=workspace).update(created_at=start - timedelta(days=20))
    alice = User.objects.create(username="alice", email="alice@example.com", display_name="Alice")
    bob = User.objects.create(username="bob", email="bob@example.com", display_name="Bob")
    for user in (alice, bob):
        membership = WorkspaceMember.objects.create(workspace=workspace, member=user, role=15)
        WorkspaceMember.objects.filter(id=membership.id).update(created_at=start - timedelta(days=20))
    project = Project.objects.create(name="Activity", identifier="ACT", workspace=workspace)
    state = State.objects.create(name="Started", group="started", color="#60646C", project=project, workspace=workspace)
    issue = Issue.objects.create(name="Evidence", project=project, workspace=workspace, state=state)
    IssueAssignee.objects.create(issue=issue, assignee=bob, project=project, workspace=workspace)
    return {
        "workspace": workspace,
        "admin": create_user,
        "alice": alice,
        "bob": bob,
        "project": project,
        "issue": issue,
        "payload": {"start_at": start.isoformat(), "end_at": end.isoformat()},
        "start": start,
        "end": end,
    }


def activity(evidence, actor=None, field="state", at=None, **kwargs):
    row = IssueActivity.objects.create(
        actor=actor or evidence["alice"],
        project=evidence["project"],
        workspace=evidence["workspace"],
        issue=evidence["issue"],
        field=field,
        **kwargs,
    )
    IssueActivity.objects.filter(id=row.id).update(created_at=at or evidence["start"] + timedelta(hours=1))
    return row


def report(client, evidence, **kwargs):
    return client.post(
        f"/api/v1/workspaces/{evidence['workspace'].slug}/member-activity-summary/",
        {**evidence["payload"], **kwargs},
        format="json",
    )


def by_member(response):
    assert response.status_code == 200, response.content
    return {row["member_id"]: row for row in response.json()["members"]}


def test_v1_matches_internal_contract_and_uses_actor_not_assignee(api_key_client, session_client, evidence):
    activity(evidence, field="comment")
    v1 = report(api_key_client, evidence)
    internal = session_client.post(
        f"/api/workspaces/{evidence['workspace'].slug}/member-activity-summary/",
        evidence["payload"],
        format="json",
    )
    assert internal.status_code == 200, internal.content
    assert v1.json() == internal.json()
    rows = by_member(v1)
    assert rows[str(evidence["alice"].id)]["activity_count_in_window"] == 1
    assert rows[str(evidence["bob"].id)]["activity_count_in_window"] == 0
    assert rows[str(evidence["bob"].id)]["last_activity_at"] is None
    assert rows[str(evidence["bob"].id)]["open_work_item_count"] == 1
    assert rows[str(evidence["alice"].id)]["last_activity_identifier"].startswith("ACT-")


def test_date_boundaries_and_light_activity(api_key_client, evidence):
    activity(evidence, at=evidence["start"] - timedelta(microseconds=1))
    activity(evidence, field="comment", at=evidence["start"])
    activity(evidence, field="reaction", at=evidence["start"] + timedelta(hours=2))
    activity(evidence, at=evidence["end"])
    row = by_member(report(api_key_client, evidence))[str(evidence["alice"].id)]
    assert row["activity_count_in_window"] == 1
    row = by_member(report(api_key_client, evidence, include_light_activity=True))[str(evidence["alice"].id)]
    assert row["activity_count_in_window"] == 2


def test_inactive_bots_deleted_members_excluded_but_new_and_never_active_present(api_key_client, evidence):
    workspace = evidence["workspace"]
    for label, attrs, membership_attrs in (
        ("bot", {"is_bot": True}, {}),
        ("disabled_user", {"is_active": False}, {}),
        ("disabled_membership", {}, {"is_active": False}),
        ("deleted_membership", {}, {"deleted_at": evidence["start"]}),
    ):
        user = User.objects.create(username=label, email=f"{label}@example.com", **attrs)
        membership = WorkspaceMember.objects.create(
            workspace=workspace, member=user, **{"role": 15, **membership_attrs}
        )
        WorkspaceMember.all_objects.filter(id=membership.id).update(created_at=evidence["start"] - timedelta(days=20))
    new = User.objects.create(username="new", email="new@example.com")
    membership = WorkspaceMember.objects.create(workspace=workspace, member=new, role=15)
    WorkspaceMember.objects.filter(id=membership.id).update(created_at=evidence["end"] - timedelta(days=1))
    rows = by_member(report(api_key_client, evidence))
    assert set(rows) == {str(evidence[name].id) for name in ("admin", "alice", "bob")} | {str(new.id)}


def test_member_and_inactive_admin_cannot_read_global_activity(api_key_client, evidence, create_user):
    WorkspaceMember.objects.filter(workspace=evidence["workspace"], member=create_user).update(role=15)
    assert report(api_key_client, evidence).status_code == 403
    WorkspaceMember.objects.filter(workspace=evidence["workspace"], member=create_user).update(role=20, is_active=False)
    assert report(api_key_client, evidence).status_code == 403


def test_cross_workspace_project_rejected(api_key_client, evidence):
    other = Workspace.objects.create(name="Other", slug="other", owner=evidence["admin"])
    project = Project.objects.create(name="Other", identifier="OTH", workspace=other)
    assert report(api_key_client, evidence, project_id=str(project.id)).status_code == 404
    assert report(api_key_client, evidence, project_id=str(uuid4())).status_code == 404


def test_project_scope_uses_current_project_members_and_does_not_leak_other_activities(api_key_client, evidence):
    membership = ProjectMember.objects.create(
        project=evidence["project"], workspace=evidence["workspace"], member=evidence["alice"], role=15
    )
    ProjectMember.objects.filter(id=membership.id).update(created_at=evidence["start"] - timedelta(days=1))
    activity(evidence)
    rows = by_member(report(api_key_client, evidence, project_id=str(evidence["project"].id)))
    assert set(rows) == {str(evidence["alice"].id)}


def test_archiving_project_does_not_erase_recent_evidence(api_key_client, evidence):
    activity(evidence)
    Project.objects.filter(id=evidence["project"].id).update(archived_at=timezone.now())
    row = by_member(report(api_key_client, evidence))[str(evidence["alice"].id)]
    assert row["activity_count_in_window"] == 1


def test_removed_assignment_is_not_counted(api_key_client, evidence):
    IssueAssignee.objects.filter(issue=evidence["issue"]).update(deleted_at=timezone.now())
    rows = by_member(report(api_key_client, evidence))
    assert rows[str(evidence["bob"].id)]["open_work_item_count"] == 0


def test_description_activity_timestamp_can_move_into_window(api_key_client, evidence):
    row = activity(evidence, field="description", at=evidence["start"] - timedelta(days=1))
    assert by_member(report(api_key_client, evidence))[str(evidence["alice"].id)]["activity_count_in_window"] == 0
    IssueActivity.objects.filter(id=row.id).update(created_at=evidence["start"] + timedelta(hours=1))
    assert by_member(report(api_key_client, evidence))[str(evidence["alice"].id)]["activity_count_in_window"] == 1


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"start_at": "bad", "end_at": "bad"},
        {"start_at": "2026-10-19T00:00:00Z", "end_at": "2026-10-01T00:00:00Z"},
        {"start_at": "2026-01-01T00:00:00Z", "end_at": "2026-10-01T00:00:00Z"},
    ],
)
def test_bad_window_rejected(api_key_client, evidence, payload):
    response = api_key_client.post(
        f"/api/v1/workspaces/{evidence['workspace'].slug}/member-activity-summary/",
        payload,
        format="json",
    )
    assert response.status_code == 400


def test_project_observation_period_starts_when_joining_project(api_key_client, evidence):
    membership = ProjectMember.objects.create(
        project=evidence["project"], workspace=evidence["workspace"], member=evidence["alice"], role=15
    )
    joined = evidence["start"] + timedelta(days=1)
    ProjectMember.objects.filter(id=membership.id).update(created_at=joined)
    rows = by_member(report(api_key_client, evidence, project_id=str(evidence["project"].id)))
    from django.utils.dateparse import parse_datetime

    assert parse_datetime(rows[str(evidence["alice"].id)]["membership_started_at"]) == joined


def test_active_guest_is_included_despite_plane_role(api_key_client, evidence):
    user = User.objects.create(username="guest", email="guest@example.com")
    membership = WorkspaceMember.objects.create(workspace=evidence["workspace"], member=user, role=5)
    WorkspaceMember.objects.filter(id=membership.id).update(created_at=evidence["start"] - timedelta(days=10))
    assert str(user.id) in by_member(report(api_key_client, evidence))
