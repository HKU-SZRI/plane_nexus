# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import pytest
from django.utils import timezone
from rest_framework import status

from plane.db.models import Issue, IssueAssignee, Project, ProjectMember, State, StateGroup


@pytest.mark.contract
@pytest.mark.django_db
def test_project_members_include_workload_counts(api_key_client, create_user, workspace):
    project = Project.objects.create(
        name="Workload project",
        identifier="WORKLOAD",
        workspace=workspace,
        created_by=create_user,
    )
    ProjectMember.objects.create(project=project, member=create_user, workspace=workspace, role=20)

    states = {
        group: State.objects.create(
            name=group.title(),
            color="#60646C",
            group=group,
            project=project,
            workspace=workspace,
        )
        for group in (
            StateGroup.BACKLOG,
            StateGroup.UNSTARTED,
            StateGroup.STARTED,
            StateGroup.COMPLETED,
        )
    }
    for sequence_id, group in enumerate(states, start=1):
        issue = Issue.objects.create(
            name=f"{group} issue",
            project=project,
            workspace=workspace,
            state=states[group],
            sequence_id=sequence_id,
            created_by=create_user,
        )
        issue.assignees.add(create_user)

    removed_assignment_issue = Issue.objects.create(
        name="Removed assignment issue",
        project=project,
        workspace=workspace,
        state=states[StateGroup.STARTED],
        sequence_id=5,
        created_by=create_user,
    )
    removed_assignment_issue.assignees.add(create_user)
    IssueAssignee.objects.filter(
        issue=removed_assignment_issue,
        assignee=create_user,
    ).update(deleted_at=timezone.now())

    response = api_key_client.get(
        f"/api/v1/workspaces/{workspace.slug}/projects/{project.id}/members/"
    )

    assert response.status_code == status.HTTP_200_OK
    member = response.json()[0]
    assert member["started_issues"] == 1
    assert member["unstarted_issues"] == 2
