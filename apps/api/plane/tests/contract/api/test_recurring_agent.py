"""Database-backed contract tests for internal scheduling identity provisioning."""

import os
import uuid
from unittest.mock import patch

from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from plane.db.models import Project, ProjectMember, User, Workspace, WorkspaceMember


class AgentFixture:
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.key = patch.dict(os.environ, {"PLANE_NEXUS_ONBOARDING_KEY": "test-internal-key"})
        self.key.start()
        self.addCleanup(self.key.stop)
        owner = User.objects.create(
            username=uuid.uuid4().hex, email=f"{uuid.uuid4()}@example.test", is_bot=True
        )
        self.workspace = Workspace.objects.create(name="Test", slug="agent-test", owner=owner)
        self.project = Project.objects.create(
            name="Recurring", identifier="REC", workspace=self.workspace
        )
        self.client = APIClient()
        self.client.credentials(HTTP_X_INTERNAL_KEY="test-internal-key")
        self.path = "/api/internal/nexus/recurring-agent/"

    def provision(self, **changes):
        return self.client.post(self.path, {
            "workspace_slug": self.workspace.slug, "project_id": str(self.project.id), **changes,
        }, format="json")


class RecurringAgentTests(AgentFixture, TestCase):
    def test_idempotent_bot_has_no_login_or_admin_access(self):
        first, second = self.provision(), self.provision()
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(first.data, second.data)
        bot = User.objects.get(id=first.data["user_id"])
        self.assertTrue(bot.is_bot)
        self.assertEqual(bot.display_name, "Nexus Agent")
        self.assertFalse(bot.has_usable_password())
        self.assertFalse(bot.is_superuser or bot.is_staff)
        self.assertEqual(WorkspaceMember.objects.get(member=bot).role, 15)
        self.assertEqual(ProjectMember.objects.get(member=bot).role, 15)
        other = Project.objects.create(name="Other", identifier="OTH", workspace=self.workspace)
        third = self.provision(project_id=str(other.id))
        self.assertEqual(third.data["user_id"], first.data["user_id"])
        self.assertEqual(ProjectMember.objects.filter(member=bot).count(), 2)

    def test_trusted_generated_work_item_is_created_by_agent(self):
        import hashlib
        import json
        import time
        import jwt
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from django.test import override_settings
        from plane.db.models import Issue

        agent = self.provision().data["user_id"]
        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public = private.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode()
        path = f"/api/v1/workspaces/{self.workspace.slug}/projects/{self.project.id}/work-items/"
        body = json.dumps({"name": "Scheduled by Agent", "external_source": "nexus_recurring",
                           "external_id": str(uuid.uuid4())}).encode()
        now = int(time.time())
        token = jwt.encode({
            "actor_plane_id": agent, "aud": "plane", "iss": "nexus",
            "iat": now, "exp": now + 60, "jti": uuid.uuid4().hex,
            "method": "POST", "path": path, "query": "",
            "workspace_slug": self.workspace.slug,
            "body_sha256": hashlib.sha256(body).hexdigest(),
        }, private, algorithm="RS256")
        client = APIClient()
        client.credentials(HTTP_X_NEXUS_TRUST_TOKEN=token)
        with (
            override_settings(NEXUS_TRUSTED_JWT_PUBLIC_KEY=public, APP_BASE_URL="http://plane.test"),
            patch("plane.api.middleware.api_authentication.cache.add", return_value=True),
            patch("plane.app.views.issue.base.issue_activity.delay"),
            patch("plane.app.views.issue.base.model_activity.delay"),
            patch("plane.app.views.issue.base.issue_description_version_task.delay"),
        ):
            response = client.generic("POST", path, body, content_type="application/json")
        self.assertEqual(response.status_code, 201, response.data)
        issue = Issue.objects.get(id=response.data["id"])
        self.assertEqual(str(issue.created_by_id), agent)
        self.assertNotEqual(issue.created_by_id, self.workspace.owner_id)

    def test_workspace_isolation(self):
        first = self.provision()
        other = Workspace.objects.create(name="Other", slug="other", owner=self.workspace.owner)
        project = Project.objects.create(name="Other", identifier="OTH", workspace=other)
        self.assertEqual(self.provision(project_id=str(project.id)).status_code, 404)
        second = self.provision(workspace_slug=other.slug, project_id=str(project.id))
        self.assertEqual(second.status_code, 200, second.data)
        self.assertNotEqual(first.data["user_id"], second.data["user_id"])

    def test_unauthenticated_requests_cannot_provision(self):
        self.client.credentials()
        self.assertEqual(self.provision().status_code, 403)
        self.assertFalse(User.objects.filter(bot_type="nexus_recurring").exists())

    def test_caller_cannot_choose_identity_or_roles(self):
        self.assertEqual(self.provision(role=20).status_code, 400)
        self.assertFalse(User.objects.filter(bot_type="nexus_recurring").exists())

    def test_archived_project_does_not_gain_bot(self):
        self.project.archived_at = timezone.now()
        self.project.save()
        self.assertEqual(self.provision().status_code, 409)
        self.assertFalse(User.objects.filter(bot_type="nexus_recurring").exists())

    def test_deactivated_account_is_not_reactivated(self):
        bot = User.objects.get(id=self.provision().data["user_id"])
        bot.is_active = False
        bot.save()
        self.assertEqual(self.provision().status_code, 403)
        bot.refresh_from_db()
        self.assertFalse(bot.is_active)

    def test_removed_membership_is_not_recreated(self):
        bot_id = self.provision().data["user_id"]
        member = ProjectMember.objects.get(member_id=bot_id)
        member.deleted_at = timezone.now()
        member.save()
        self.assertEqual(self.provision().status_code, 403)
        self.assertFalse(ProjectMember.objects.filter(member_id=bot_id).exists())

    def test_deactivated_workspace_membership_is_not_reactivated(self):
        bot_id = self.provision().data["user_id"]
        member = WorkspaceMember.objects.get(member_id=bot_id)
        member.is_active = False
        member.save()
        self.assertEqual(self.provision().status_code, 403)
        member.refresh_from_db()
        self.assertFalse(member.is_active)

    def test_email_collision_does_not_adopt_human(self):
        email = f"nexus-recurring-{self.workspace.id}@agents.nexus.invalid"
        human = User.objects.create(username=uuid.uuid4().hex, email=email)
        self.assertEqual(self.provision().status_code, 409)
        human.refresh_from_db()
        self.assertFalse(human.is_bot)


class RecurringAgentConcurrencyTests(AgentFixture, TransactionTestCase):
    def test_parallel_first_requests_share_one_account(self):
        from concurrent.futures import ThreadPoolExecutor
        from django.db import close_old_connections

        payload = {"workspace_slug": self.workspace.slug, "project_id": str(self.project.id)}
        def request():
            close_old_connections()
            try:
                client = APIClient()
                client.credentials(HTTP_X_INTERNAL_KEY="test-internal-key")
                response = client.post(self.path, payload, format="json")
                return response.status_code, response.data
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: request(), range(2)))
        self.assertEqual([code for code, _ in results], [200, 200], results)
        self.assertEqual(results[0][1]["user_id"], results[1][1]["user_id"])
        self.assertEqual(User.objects.filter(bot_type="nexus_recurring").count(), 1)
