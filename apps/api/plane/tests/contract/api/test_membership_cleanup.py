"""Membership shutdown receipts and assignment cleanup against the real ORM."""

import uuid

from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient
from plane.db.models import (
    Issue,
    IssueActivity,
    IssueAssignee,
    NexusMembershipCleanup,
    Project,
    ProjectMember,
    User,
    Workspace,
    WorkspaceMember,
)
from plane.tests.contract.api.test_recurring_agent import AgentFixture


class CleanupFixture(AgentFixture):
    def setUp(self):
        super().setUp()
        self.path = "/api/internal/nexus/membership-cleanup/"
        self.person = User.objects.create(username=uuid.uuid4().hex, email=f"{uuid.uuid4()}@example.test")
        self.membership = WorkspaceMember.objects.create(workspace=self.workspace, member=self.person, role=15)
        ProjectMember.objects.create(workspace=self.workspace, project=self.project, member=self.person, role=15)
        self.issue = Issue.objects.create(
            name="Shared item", project=self.project, workspace=self.workspace, created_by=self.workspace.owner
        )
        Issue.objects.filter(id=self.issue.id).update(created_by=self.workspace.owner)
        self.issue.refresh_from_db()
        IssueAssignee.objects.create(
            workspace=self.workspace, project=self.project, issue=self.issue, assignee=self.person
        )
        IssueAssignee.objects.create(
            workspace=self.workspace, project=self.project, issue=self.issue, assignee=self.workspace.owner
        )
        self.payload = {
            "operation_id": str(uuid.uuid4()),
            "workspace_slug": self.workspace.slug,
            "plane_id": str(self.person.id),
            "actor_id": str(self.workspace.owner_id),
            "reason": "Security incident",
            "deactivate": True,
        }

    def cleanup(self, **changes):
        return self.client.post(self.path, {**self.payload, **changes}, format="json")


class MembershipCleanupTests(CleanupFixture, TestCase):
    def test_state_reconciliation_uses_private_key_and_does_not_write(self):
        query = {"workspace_slug": self.workspace.slug, "plane_id": str(self.person.id)}
        before = self.client.get(self.path, query)
        self.assertEqual(before.status_code, 200, before.data)
        self.assertTrue(before.data["is_active"])
        self.assertEqual(NexusMembershipCleanup.objects.count(), 0)
        self.assertEqual(IssueActivity.objects.count(), 0)
        self.assertEqual(self.cleanup().status_code, 200)
        after = self.client.get(self.path, query)
        self.assertEqual(after.status_code, 200)
        self.assertFalse(after.data["is_active"])

    def test_private_batch_budget_does_not_disable_anonymous_limits(self):
        query = {"workspace_slug": self.workspace.slug, "plane_id": str(self.person.id)}
        for _ in range(31):
            self.assertEqual(self.client.get(self.path, query).status_code, 200)
        self.client.credentials()
        self.assertEqual([self.client.get(self.path, query).status_code for _ in range(30)], [403] * 30)
        self.assertEqual(self.client.get(self.path, query).status_code, 429)

    def test_state_lookup_requires_private_key_and_exact_workspace(self):
        query = {"workspace_slug": self.workspace.slug, "plane_id": str(self.person.id)}
        self.client.credentials()
        self.assertEqual(self.client.get(self.path, query).status_code, 403)
        self.client.credentials(HTTP_X_INTERNAL_KEY="test-internal-key")
        self.assertEqual(self.client.get(self.path, {**query, "workspace_slug": "missing"}).status_code, 404)
        self.assertEqual(self.client.get(self.path, {**query, "plane_id": "invalid"}).status_code, 400)

    def test_emergency_cleanup_retains_other_assignees_creator_and_history(self):
        response = self.cleanup()
        self.assertEqual(response.status_code, 200, response.data)
        self.membership.refresh_from_db()
        self.assertFalse(self.membership.is_active)
        self.assertFalse(ProjectMember.objects.get(member=self.person, project=self.project).is_active)
        self.issue.refresh_from_db()
        self.assertEqual(self.issue.created_by_id, self.workspace.owner_id)
        self.assertEqual(
            list(IssueAssignee.objects.filter(issue=self.issue).values_list("assignee_id", flat=True)),
            [self.workspace.owner_id],
        )
        event = IssueActivity.objects.get(issue=self.issue)
        self.assertEqual(event.actor_id, self.workspace.owner_id)
        self.assertEqual(event.old_identifier, self.person.id)
        self.assertIn(self.payload["operation_id"], event.comment)
        self.assertEqual(response.data["unassigned_work_item_ids"], [str(self.issue.id)])
        self.assertEqual(NexusMembershipCleanup.objects.count(), 1)
        # Soft-deleted link remains available for audit/history.
        self.assertEqual(IssueAssignee.all_objects.filter(issue=self.issue).count(), 2)

    def test_repeated_request_returns_receipt_without_duplicate_activity(self):
        first = self.cleanup()
        second = self.cleanup(deactivate=False)
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(first.data, second.data)
        self.assertEqual(IssueActivity.objects.filter(issue=self.issue).count(), 1)

    def test_inactive_cleanup_does_not_deactivate_active_member(self):
        response = self.cleanup(deactivate=False)
        self.assertEqual(response.status_code, 409)
        self.membership.refresh_from_db()
        self.assertTrue(self.membership.is_active)
        self.assertEqual(NexusMembershipCleanup.objects.count(), 0)
        self.assertEqual(IssueAssignee.objects.filter(issue=self.issue).count(), 2)

    def test_same_user_in_another_workspace_is_untouched(self):
        other = Workspace.objects.create(name="Other", slug="other", owner=self.workspace.owner)
        membership = WorkspaceMember.objects.create(workspace=other, member=self.person, role=15)
        project = Project.objects.create(name="Other", identifier="OTH", workspace=other)
        pm = ProjectMember.objects.create(workspace=other, project=project, member=self.person, role=15)
        issue = Issue.objects.create(workspace=other, project=project, name="Other")
        IssueAssignee.objects.create(workspace=other, project=project, issue=issue, assignee=self.person)
        self.assertEqual(self.cleanup().status_code, 200)
        membership.refresh_from_db()
        pm.refresh_from_db()
        self.assertTrue(membership.is_active and pm.is_active)
        self.assertEqual(IssueAssignee.objects.filter(issue=issue, assignee=self.person).count(), 1)
        self.assertEqual(IssueActivity.objects.filter(issue=issue).count(), 0)

    def test_reactivated_user_cannot_be_affected_by_old_receipt(self):
        self.assertEqual(self.cleanup().status_code, 200)
        self.membership.is_active = True
        self.membership.save()
        self.assertEqual(self.cleanup().status_code, 409)
        self.membership.refresh_from_db()
        self.assertTrue(self.membership.is_active)

    def test_operation_id_cannot_be_reused_for_changed_request(self):
        self.assertEqual(self.cleanup().status_code, 200)
        self.assertEqual(self.cleanup(reason="Different command").status_code, 409)
        self.assertEqual(NexusMembershipCleanup.objects.count(), 1)

    def test_unauthorized_and_invalid_requests_leave_data_intact(self):
        self.client.credentials()
        self.assertEqual(self.cleanup().status_code, 403)
        self.client.credentials(HTTP_X_INTERNAL_KEY="test-internal-key")
        self.assertEqual(self.cleanup(role=20).status_code, 400)
        self.assertEqual(self.cleanup(reason="").status_code, 400)
        self.assertEqual(self.cleanup(workspace_slug="missing").status_code, 404)
        self.assertEqual(NexusMembershipCleanup.objects.count(), 0)


class MembershipCleanupConcurrencyTests(CleanupFixture, TransactionTestCase):
    def test_parallel_retries_share_receipt_and_activity(self):
        from concurrent.futures import ThreadPoolExecutor
        from django.db import close_old_connections

        def request():
            close_old_connections()
            try:
                client = APIClient()
                client.credentials(HTTP_X_INTERNAL_KEY="test-internal-key")
                response = client.post(self.path, self.payload, format="json")
                return response.status_code, response.data
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: request(), range(2)))
        self.assertEqual([code for code, _ in results], [200, 200], results)
        self.assertEqual(results[0][1], results[1][1])
        self.assertEqual(NexusMembershipCleanup.objects.count(), 1)
        self.assertEqual(IssueActivity.objects.filter(issue=self.issue).count(), 1)
