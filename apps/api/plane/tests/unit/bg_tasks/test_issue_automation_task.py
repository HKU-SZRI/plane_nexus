from unittest.mock import patch

import pytest

from plane.bgtasks.issue_automation_task import archive_cancelled_issues, archive_old_issues
from plane.db.models import Issue, Project, State


@pytest.fixture
def project(db, workspace, create_user):
    return Project.objects.create(
        name="Automation project",
        identifier="AUTO",
        workspace=workspace,
        created_by=create_user,
    )


@pytest.fixture
def cancelled_state(db, project):
    return State.objects.create(
        name="Cancelled",
        color="#9AA4BC",
        group="cancelled",
        workspace=project.workspace,
        project=project,
    )


@pytest.fixture
def completed_state(db, project):
    return State.objects.create(
        name="Done",
        color="#46A758",
        group="completed",
        workspace=project.workspace,
        project=project,
    )


def create_issue(project, state, created_by, name):
    return Issue.objects.create(
        name=name,
        workspace=project.workspace,
        project=project,
        state=state,
        created_by=created_by,
    )


@pytest.mark.django_db
def test_cancelled_work_items_are_archived_without_an_age_threshold(
    project, cancelled_state, completed_state, create_user
):
    cancelled_issue = create_issue(project, cancelled_state, create_user, "Cancelled now")
    completed_issue = create_issue(project, completed_state, create_user, "Recently completed")

    with patch("plane.bgtasks.issue_automation_task.issue_activity.delay") as activity_delay:
        archive_cancelled_issues()

    cancelled_issue.refresh_from_db()
    completed_issue.refresh_from_db()
    assert cancelled_issue.archived_at is not None
    assert completed_issue.archived_at is None
    activity_delay.assert_called_once()


@pytest.mark.django_db
def test_cancelled_work_items_are_not_archived_when_automation_is_disabled(
    project, cancelled_state, create_user
):
    project.auto_archive_cancelled_issues = False
    project.save(update_fields=["auto_archive_cancelled_issues"])
    cancelled_issue = create_issue(project, cancelled_state, create_user, "Keep cancelled")

    with patch("plane.bgtasks.issue_automation_task.issue_activity.delay") as activity_delay:
        archive_cancelled_issues()

    cancelled_issue.refresh_from_db()
    assert cancelled_issue.archived_at is None
    activity_delay.assert_not_called()


@pytest.mark.django_db
def test_age_based_archive_no_longer_archives_cancelled_work_items(project, cancelled_state, create_user):
    project.archive_in = 1
    project.auto_archive_cancelled_issues = False
    project.save(update_fields=["archive_in", "auto_archive_cancelled_issues"])
    cancelled_issue = create_issue(project, cancelled_state, create_user, "Old cancelled")
    Issue.objects.filter(id=cancelled_issue.id).update(updated_at="2020-01-01T00:00:00Z")

    with patch("plane.bgtasks.issue_automation_task.issue_activity.delay") as activity_delay:
        archive_old_issues()

    cancelled_issue.refresh_from_db()
    assert cancelled_issue.archived_at is None
    activity_delay.assert_not_called()
