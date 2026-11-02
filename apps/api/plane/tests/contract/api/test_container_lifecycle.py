"""Active workspace membership is required for effective container membership."""
from unittest.mock import patch
from django.test import TestCase
from rest_framework.test import APIClient
from plane.db.models import ProjectMember, WorkspaceMember
from plane.tests.contract.api.test_membership_cleanup import CleanupFixture


class ContainerMembershipTests(CleanupFixture, TestCase):
    def setUp(self):
        super().setUp()
        WorkspaceMember.objects.create(workspace=self.workspace, member=self.workspace.owner, role=20)
        ProjectMember.objects.create(workspace=self.workspace, project=self.project, member=self.workspace.owner, role=20)
        self.client = APIClient()
        self.client.force_authenticate(user=self.workspace.owner)
        self.path = f"/api/v1/workspaces/{self.workspace.slug}/projects/{self.project.id}/members/"

    def test_inactive_workspace_member_is_not_an_effective_container_member(self):
        self.membership.is_active = False
        self.membership.save(update_fields=["is_active"])
        response = self.client.get(self.path)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertNotIn(str(self.person.id), {row["id"] for row in response.data})
        # Historical row remains available for audit.
        self.assertTrue(ProjectMember.objects.get(project=self.project, member=self.person).is_active)

    def test_inactive_workspace_member_cannot_reactivate_container_membership(self):
        self.membership.is_active = False
        self.membership.save(update_fields=["is_active"])
        ProjectMember.objects.filter(project=self.project, member=self.person).update(is_active=False)
        response = self.client.post(self.path, {"member": str(self.person.id), "role": 20}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(ProjectMember.objects.get(project=self.project, member=self.person).is_active)

    def test_inactive_workspace_member_cannot_create_container_membership(self):
        self.membership.is_active = False
        self.membership.save(update_fields=["is_active"])
        ProjectMember.objects.filter(project=self.project, member=self.person).delete(soft=False)
        response = self.client.post(self.path, {"member": str(self.person.id), "role": 15}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(ProjectMember.objects.filter(project=self.project, member=self.person).exists())

    @patch("plane.api.views.member.webhook_activity.delay")
    @patch("plane.api.views.member.model_activity.delay")
    def test_active_workspace_member_can_reactivate_existing_container_row(self, *_):
        row = ProjectMember.objects.get(project=self.project, member=self.person)
        row.is_active = False
        row.save(update_fields=["is_active"])
        response = self.client.post(self.path, {"member": str(self.person.id), "role": 15}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(str(response.data["id"]), str(row.id))
        row.refresh_from_db()
        self.assertTrue(row.is_active)
