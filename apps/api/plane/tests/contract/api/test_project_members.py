# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import pytest
from rest_framework import status

from plane.db.models import Issue, Project, ProjectMember, State, StateGroup


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

    response = api_key_client.get(
        f"/api/v1/workspaces/{workspace.slug}/projects/{project.id}/members/"
    )

    assert response.status_code == status.HTTP_200_OK
    member = response.json()[0]
    assert member["started_issues"] == 1
    assert member["unstarted_issues"] == 2
