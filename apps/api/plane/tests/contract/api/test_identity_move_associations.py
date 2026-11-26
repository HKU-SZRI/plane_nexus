import uuid

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from plane.db.models import (
    Cycle,
    CycleIssue,
    Issue,
    IssueAssignee,
    IssueRelation,
    Label,
    Module,
    ModuleIssue,
    Project,
    ProjectMember,
    State,
    User,
    Workspace,
    WorkspaceMember,
)


class IdentityMoveAssociationTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create(
            username=uuid.uuid4().hex, email=f"{uuid.uuid4()}@example.test"
        )
        self.workspace = Workspace.objects.create(name="Move", slug=uuid.uuid4().hex, owner=self.owner)
        self.source = Project.objects.create(name="Source", identifier="SRC", workspace=self.workspace)
        self.target = Project.objects.create(name="Target", identifier="DST", workspace=self.workspace)
        WorkspaceMember.objects.create(workspace=self.workspace, member=self.owner, role=15)
        for project in (self.source, self.target):
            ProjectMember.objects.create(
                workspace=self.workspace, project=project, member=self.owner, role=20
            )
        self.source_state = State.objects.create(
            project=self.source, name="Ready", group="unstarted"
        )
        State.objects.create(project=self.target, name="Ready", group="unstarted")
        self.item = Issue.objects.create(
            project=self.source,
            workspace=self.workspace,
            name="Keep identity",
            state=self.source_state,
            created_by=self.owner,
        )
        Issue.objects.filter(pk=self.item.pk).update(created_by=self.owner)
        self.item.refresh_from_db()
        self.source_cycle = Cycle.objects.create(
            project=self.source, workspace=self.workspace, name="Sprint 4", owned_by=self.owner
        )
        self.target_cycle = Cycle.objects.create(
            project=self.target, workspace=self.workspace, name="Sprint 4", owned_by=self.owner
        )
        self.source_module = Module.objects.create(
            project=self.source, workspace=self.workspace, name="Q4 plan"
        )
        self.target_module = Module.objects.create(
            project=self.target, workspace=self.workspace, name="Q4 plan"
        )
        self.cycle_link = CycleIssue.objects.create(
            project=self.source, workspace=self.workspace, issue=self.item, cycle=self.source_cycle
        )
        self.module_link = ModuleIssue.objects.create(
            project=self.source, workspace=self.workspace, issue=self.item, module=self.source_module
        )
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.path = (
            f"/api/v1/workspaces/{self.workspace.slug}/projects/{self.source.id}"
            f"/work-items/{self.item.id}/transfer/"
        )

    def test_move_preserves_work_item_and_maps_cycle_and_module_links(self):
        payload = {"target_project_id": str(self.target.id), "preserve_identity": True}
        preview = self.client.post(self.path, payload | {"validate_only": True}, format="json")
        self.assertEqual(preview.status_code, 200, preview.data)
        self.assertEqual(preview.data["identity_preserving_move"], True)
        self.item.refresh_from_db()
        self.assertEqual(self.item.project_id, self.source.id)

        response = self.client.post(self.path, payload, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.item.refresh_from_db()
        self.cycle_link.refresh_from_db()
        self.module_link.refresh_from_db()
        self.assertEqual(self.item.project_id, self.target.id)
        self.assertEqual(self.item.created_by_id, self.owner.id)
        self.assertEqual(self.cycle_link.project_id, self.target.id)
        self.assertEqual(self.cycle_link.cycle_id, self.target_cycle.id)
        self.assertEqual(self.module_link.project_id, self.target.id)
        self.assertEqual(self.module_link.module_id, self.target_module.id)
        self.assertEqual(CycleIssue.objects.filter(issue=self.item).count(), 1)
        self.assertEqual(ModuleIssue.objects.filter(issue=self.item).count(), 1)

    def test_missing_matching_cycle_rejects_move_without_losing_association(self):
        self.target_cycle.delete()
        response = self.client.post(
            self.path,
            {"target_project_id": str(self.target.id), "preserve_identity": True},
            format="json",
        )
        self.assertEqual(response.status_code, 409, response.data)
        self.item.refresh_from_db()
        self.cycle_link.refresh_from_db()
        self.assertEqual(self.item.project_id, self.source.id)
        self.assertEqual(self.cycle_link.cycle_id, self.source_cycle.id)

    def test_deleted_related_item_is_not_an_active_relation(self):
        related = Issue.objects.create(
            project=self.target, workspace=self.workspace, name="Related item", created_by=self.owner
        )
        relation = IssueRelation.objects.create(
            project=self.source, workspace=self.workspace, issue=self.item,
            related_issue=related, relation_type="relates_to",
        )
        path = (
            f"/api/v1/workspaces/{self.workspace.slug}/projects/{self.source.id}"
            f"/work-items/{self.item.id}/relations/"
        )
        before = self.client.get(path)
        self.assertEqual(before.status_code, 200, before.data)
        self.assertIn(str(related.id), str(before.data))
        Issue.objects.filter(pk=related.pk).update(deleted_at=timezone.now())
        after = self.client.get(path)
        self.assertEqual(after.status_code, 200, after.data)
        self.assertNotIn(str(related.id), str(after.data))
        self.assertTrue(IssueRelation.objects.filter(pk=relation.pk).exists())

    def test_relation_creation_rejects_inaccessible_target_without_leaking_details(self):
        related = Issue.objects.create(
            project=self.target, workspace=self.workspace, name="Protected target", created_by=self.owner
        )
        ProjectMember.objects.filter(project=self.target, member=self.owner).update(is_active=False)
        paths = [
            f"/api/workspaces/{self.workspace.slug}/projects/{self.source.id}/issues/{self.item.id}/issue-relation/",
            f"/api/v1/workspaces/{self.workspace.slug}/projects/{self.source.id}/work-items/{self.item.id}/relations/",
        ]
        for path in paths:
            response = self.client.post(
                path, {"relation_type": "relates_to", "issues": [str(related.id)]}, format="json"
            )
            self.assertIn(response.status_code, (403, 404), response.data)
            self.assertNotIn("Protected target", str(response.data))
            self.assertNotIn(str(related.id), str(response.data))
            self.assertFalse(IssueRelation.objects.filter(issue=self.item, related_issue=related).exists())

    def test_relation_creation_accepts_targets_visible_in_other_projects(self):
        paths = [
            f"/api/workspaces/{self.workspace.slug}/projects/{self.source.id}/issues/{self.item.id}/issue-relation/",
            f"/api/v1/workspaces/{self.workspace.slug}/projects/{self.source.id}/work-items/{self.item.id}/relations/",
        ]
        for path in paths:
            related = Issue.objects.create(
                project=self.target, workspace=self.workspace, name="Visible target", created_by=self.owner
            )
            response = self.client.post(
                path, {"relation_type": "relates_to", "issues": [str(related.id)]}, format="json"
            )
            self.assertEqual(response.status_code, 201, response.data)
            self.assertTrue(IssueRelation.objects.filter(issue=self.item, related_issue=related).exists())

    def test_guest_cannot_read_or_link_other_persons_issue_in_private_project(self):
        other = User.objects.create(username=uuid.uuid4().hex, email=f"{uuid.uuid4()}@example.test")
        related = Issue.objects.create(project=self.target, workspace=self.workspace, name="Guest protected")
        Issue.objects.filter(pk=related.pk).update(created_by=other)
        Project.objects.filter(pk=self.target.pk).update(guest_view_all_features=False)
        ProjectMember.objects.filter(project=self.target, member=self.owner).update(role=5)
        IssueRelation.objects.create(
            project=self.source, workspace=self.workspace, issue=self.item,
            related_issue=related, relation_type="relates_to",
        )
        paths = [
            f"/api/workspaces/{self.workspace.slug}/projects/{self.source.id}/issues/{self.item.id}/issue-relation/",
            f"/api/v1/workspaces/{self.workspace.slug}/projects/{self.source.id}/work-items/{self.item.id}/relations/",
        ]
        for path in paths:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, response.data)
            self.assertNotIn(str(related.id), str(response.data))
            response = self.client.post(
                path, {"relation_type": "blocked_by", "issues": [str(related.id)]}, format="json"
            )
            self.assertEqual(response.status_code, 404, response.data)
            self.assertNotIn("Guest protected", str(response.data))
        remove_path = (
            f"/api/workspaces/{self.workspace.slug}/projects/{self.source.id}"
            f"/issues/{self.item.id}/remove-relation/"
        )
        response = self.client.post(remove_path, {"related_issue": str(related.id)}, format="json")
        self.assertEqual(response.status_code, 404, response.data)
        self.assertEqual(IssueRelation.objects.filter(issue=self.item, related_issue=related).count(), 1)
        Issue.objects.filter(pk=self.item.pk).update(parent=related)
        detail_paths = [
            f"/api/workspaces/{self.workspace.slug}/projects/{self.source.id}/issues/{self.item.id}/",
            f"/api/v1/workspaces/{self.workspace.slug}/projects/{self.source.id}/work-items/{self.item.id}/",
        ]
        for path in detail_paths:
            response = self.client.get(path, {"expand": "parent"})
            self.assertEqual(response.status_code, 200, response.data)
            self.assertNotIn(str(related.id), str(response.data))
            self.assertNotIn("Guest protected", str(response.data))

    def test_archived_project_relations_remain_readable_to_members(self):
        related = Issue.objects.create(project=self.target, workspace=self.workspace, name="Archived context item")
        IssueRelation.objects.create(
            project=self.source, workspace=self.workspace, issue=self.item,
            related_issue=related, relation_type="relates_to",
        )
        Project.objects.filter(pk=self.target.pk).update(archived_at=timezone.now())
        paths = [
            f"/api/workspaces/{self.workspace.slug}/projects/{self.source.id}/issues/{self.item.id}/issue-relation/",
            f"/api/v1/workspaces/{self.workspace.slug}/projects/{self.source.id}/work-items/{self.item.id}/relations/",
        ]
        for path in paths:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, response.data)
            self.assertIn(str(related.id), str(response.data))

    def test_relation_source_must_belong_to_requested_project(self):
        path = (
            f"/api/v1/workspaces/{self.workspace.slug}/projects/{self.target.id}"
            f"/work-items/{self.item.id}/relations/"
        )
        response = self.client.get(path)
        self.assertEqual(response.status_code, 404, response.data)

    def test_related_issue_list_excludes_soft_deleted_assignees(self):
        other = User.objects.create(username=uuid.uuid4().hex, email=f"{uuid.uuid4()}@example.test")
        WorkspaceMember.objects.create(workspace=self.workspace, member=other, role=15)
        ProjectMember.objects.create(workspace=self.workspace, project=self.target, member=other, role=15)
        related = Issue.objects.create(project=self.target, workspace=self.workspace, name="Related assignees")
        for person in (self.owner, other):
            IssueAssignee.objects.create(
                workspace=self.workspace, project=self.target, issue=related, assignee=person
            )
        IssueAssignee.objects.filter(issue=related, assignee=other).update(deleted_at=timezone.now())
        IssueRelation.objects.create(
            project=self.source, workspace=self.workspace, issue=self.item,
            related_issue=related, relation_type="relates_to",
        )
        path = (
            f"/api/workspaces/{self.workspace.slug}/projects/{self.source.id}"
            f"/issues/{self.item.id}/issue-relation/"
        )
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200, response.data)
        row = next(item for item in response.data["relates_to"] if str(item["id"]) == str(related.id))
        self.assertEqual({str(value) for value in row["assignee_ids"]}, {str(self.owner.id)})

    def test_issue_reference_protection_does_not_hide_label_parent(self):
        from plane.api.serializers.issue import LabelSerializer

        parent = Label.objects.create(project=self.source, workspace=self.workspace, name="Parent label")
        child = Label.objects.create(project=self.source, workspace=self.workspace, name="Child label", parent=parent)
        self.assertEqual(str(LabelSerializer(child).data["parent"]), str(parent.id))
