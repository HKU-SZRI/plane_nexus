# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import hashlib
import json
import time
from unittest.mock import patch
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APIClient

from plane.db.models import APIToken, Issue, WorkspaceMemberInvite
from plane.tests.factories import (
    ProjectFactory,
    ProjectMemberFactory,
    UserFactory,
    WorkspaceFactory,
    WorkspaceMemberFactory,
)


@pytest.fixture(scope="module")
def nexus_signing_keys():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return (
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ),
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ),
    )


def sign_request(private_key, actor_id, path, body, *, method="POST", workspace_slug="trusted-workspace"):
    now = int(time.time())
    return jwt.encode(
        {
            "actor_plane_id": str(actor_id),
            "aud": "plane",
            "body_sha256": hashlib.sha256(body).hexdigest(),
            "exp": now + 60,
            "iat": now,
            "iss": "nexus",
            "jti": uuid4().hex,
            "method": method,
            "path": path,
            "query": "",
            "workspace_slug": workspace_slug,
        },
        private_key,
        algorithm="RS256",
    )


@pytest.mark.contract
@pytest.mark.django_db
def test_trusted_outsider_can_read_workspace_projects(nexus_signing_keys):
    private_key, public_key = nexus_signing_keys
    owner = UserFactory(username=f"owner-{uuid4().hex}")
    actor = UserFactory(username=f"actor-{uuid4().hex}")
    workspace = WorkspaceFactory(owner=owner, slug="trusted-read-workspace")
    project = ProjectFactory(workspace=workspace, created_by=owner, updated_by=owner)
    api_token = APIToken.objects.create(user=actor, token=f"outsider-{uuid4().hex}")
    path = f"/api/v1/workspaces/{workspace.slug}/projects/"

    outsider_client = APIClient()
    outsider_client.credentials(HTTP_X_API_KEY=api_token.token)
    denied_response = outsider_client.get(path)
    assert denied_response.status_code == status.HTTP_403_FORBIDDEN

    trusted_client = APIClient()
    trusted_client.credentials(
        HTTP_X_NEXUS_TRUST_TOKEN=sign_request(
            private_key,
            actor.id,
            path,
            b"",
            method="GET",
            workspace_slug=workspace.slug,
        )
    )
    with (
        override_settings(NEXUS_TRUSTED_JWT_PUBLIC_KEY=public_key.decode()),
        patch("plane.api.middleware.api_authentication.cache.add", return_value=True),
    ):
        response = trusted_client.get(path)

    assert response.status_code == status.HTTP_200_OK
    assert any(str(item["id"]) == str(project.id) for item in response.json()["results"])


@pytest.mark.contract
@pytest.mark.django_db
def test_trusted_guest_can_create_work_item_with_real_actor(nexus_signing_keys):
    private_key, public_key = nexus_signing_keys
    owner = UserFactory(username=f"owner-{uuid4().hex}")
    actor = UserFactory(username=f"actor-{uuid4().hex}")
    workspace = WorkspaceFactory(owner=owner, slug="trusted-workspace")
    WorkspaceMemberFactory(workspace=workspace, member=actor, role=5)
    project = ProjectFactory(workspace=workspace, created_by=owner, updated_by=owner)
    ProjectMemberFactory(project=project, member=actor, role=5)
    api_token = APIToken.objects.create(user=actor, token=f"guest-{uuid4().hex}")

    path = f"/api/v1/workspaces/{workspace.slug}/projects/{project.id}/work-items/"
    body = json.dumps({"name": "Created through trusted Nexus"}, separators=(",", ":")).encode()

    guest_client = APIClient()
    guest_client.credentials(HTTP_X_API_KEY=api_token.token)
    denied_response = guest_client.generic("POST", path, body, content_type="application/json")
    assert denied_response.status_code == status.HTTP_403_FORBIDDEN

    trusted_client = APIClient()
    trusted_client.credentials(HTTP_X_NEXUS_TRUST_TOKEN=sign_request(private_key, actor.id, path, body))
    with (
        override_settings(NEXUS_TRUSTED_JWT_PUBLIC_KEY=public_key.decode()),
        patch("plane.api.middleware.api_authentication.cache.add", return_value=True),
        patch("plane.app.views.issue.base.issue_activity.delay"),
        patch("plane.app.views.issue.base.model_activity.delay"),
        patch("plane.app.views.issue.base.issue_description_version_task.delay"),
    ):
        response = trusted_client.generic("POST", path, body, content_type="application/json")

    assert response.status_code == status.HTTP_201_CREATED
    issue = Issue.objects.get(id=response.json()["id"])
    assert issue.workspace_id == workspace.id
    assert issue.project_id == project.id
    assert issue.created_by_id == actor.id


@pytest.mark.contract
@pytest.mark.django_db
def test_trusted_guest_bypasses_inline_creator_check_on_delete(nexus_signing_keys):
    private_key, public_key = nexus_signing_keys
    owner = UserFactory(username=f"owner-{uuid4().hex}")
    actor = UserFactory(username=f"actor-{uuid4().hex}")
    workspace = WorkspaceFactory(owner=owner, slug="trusted-delete-workspace")
    WorkspaceMemberFactory(workspace=workspace, member=actor, role=5)
    project = ProjectFactory(workspace=workspace, created_by=owner, updated_by=owner)
    ProjectMemberFactory(project=project, member=actor, role=5)
    issue = Issue.objects.create(name="Owned by someone else", project=project, created_by=owner)
    api_token = APIToken.objects.create(user=actor, token=f"guest-{uuid4().hex}")

    path = f"/api/v1/workspaces/{workspace.slug}/projects/{project.id}/issues/{issue.id}/"
    guest_client = APIClient()
    guest_client.credentials(HTTP_X_API_KEY=api_token.token)
    denied_response = guest_client.delete(path)
    assert denied_response.status_code == status.HTTP_403_FORBIDDEN

    trusted_client = APIClient()
    trusted_client.credentials(
        HTTP_X_NEXUS_TRUST_TOKEN=sign_request(
            private_key,
            actor.id,
            path,
            b"",
            method="DELETE",
            workspace_slug=workspace.slug,
        )
    )
    with (
        override_settings(NEXUS_TRUSTED_JWT_PUBLIC_KEY=public_key.decode()),
        patch("plane.api.middleware.api_authentication.cache.add", return_value=True),
        patch("plane.app.views.issue.base.issue_activity.delay"),
        patch("plane.app.views.issue.base.webhook_activity.delay"),
        patch("plane.db.mixins.soft_delete_related_objects.delay"),
    ):
        response = trusted_client.delete(path)

    assert response.status_code == status.HTTP_204_NO_CONTENT


@pytest.mark.contract
@pytest.mark.django_db
@pytest.mark.parametrize(
    ("resource", "payload", "expected_status"),
    [
        ("cycles", {"name": "Trusted cycle"}, status.HTTP_201_CREATED),
        ("modules", {"name": "Trusted module"}, status.HTTP_201_CREATED),
        (
            "states",
            {"name": "Trusted state", "color": "#123456"},
            status.HTTP_200_OK,
        ),
        ("pages", {"name": "Trusted page"}, status.HTTP_201_CREATED),
    ],
)
def test_trusted_guest_can_create_compat_resources(nexus_signing_keys, resource, payload, expected_status):
    private_key, public_key = nexus_signing_keys
    owner = UserFactory(username=f"owner-{uuid4().hex}")
    actor = UserFactory(username=f"actor-{uuid4().hex}")
    workspace = WorkspaceFactory(owner=owner, slug=f"trusted-{resource}-workspace")
    WorkspaceMemberFactory(workspace=workspace, member=actor, role=5)
    project = ProjectFactory(workspace=workspace, created_by=owner, updated_by=owner)
    ProjectMemberFactory(project=project, member=actor, role=5)
    api_token = APIToken.objects.create(user=actor, token=f"guest-{uuid4().hex}")
    path = f"/api/v1/workspaces/{workspace.slug}/projects/{project.id}/{resource}/"
    body = json.dumps(payload, separators=(",", ":")).encode()

    guest_client = APIClient()
    guest_client.credentials(HTTP_X_API_KEY=api_token.token)
    denied_response = guest_client.generic("POST", path, body, content_type="application/json")
    assert denied_response.status_code == status.HTTP_403_FORBIDDEN

    trusted_client = APIClient()
    trusted_client.credentials(
        HTTP_X_NEXUS_TRUST_TOKEN=sign_request(
            private_key,
            actor.id,
            path,
            body,
            workspace_slug=workspace.slug,
        )
    )
    with (
        override_settings(NEXUS_TRUSTED_JWT_PUBLIC_KEY=public_key.decode()),
        patch("plane.api.middleware.api_authentication.cache.add", return_value=True),
        patch("plane.app.views.cycle.base.model_activity.delay"),
        patch("plane.app.views.module.base.model_activity.delay"),
        patch("plane.app.views.page.base.page_transaction.delay"),
    ):
        response = trusted_client.generic("POST", path, body, content_type="application/json")

    assert response.status_code == expected_status


@pytest.mark.contract
@pytest.mark.django_db
def test_trusted_guest_can_use_base_viewset_invitation_route(nexus_signing_keys):
    private_key, public_key = nexus_signing_keys
    owner = UserFactory(username=f"owner-{uuid4().hex}")
    actor = UserFactory(username=f"actor-{uuid4().hex}")
    workspace = WorkspaceFactory(owner=owner, slug="trusted-invite-workspace")
    WorkspaceMemberFactory(workspace=workspace, member=actor, role=5)
    api_token = APIToken.objects.create(user=actor, token=f"guest-{uuid4().hex}")
    path = f"/api/v1/workspaces/{workspace.slug}/invitations/"
    email = f"invite-{uuid4().hex}@example.com"
    body = json.dumps({"email": email, "role": 5}, separators=(",", ":")).encode()

    guest_client = APIClient()
    guest_client.credentials(HTTP_X_API_KEY=api_token.token)
    denied_response = guest_client.generic("POST", path, body, content_type="application/json")
    assert denied_response.status_code == status.HTTP_403_FORBIDDEN

    trusted_client = APIClient()
    trusted_client.credentials(
        HTTP_X_NEXUS_TRUST_TOKEN=sign_request(
            private_key,
            actor.id,
            path,
            body,
            workspace_slug=workspace.slug,
        )
    )
    with (
        override_settings(NEXUS_TRUSTED_JWT_PUBLIC_KEY=public_key.decode()),
        patch("plane.api.middleware.api_authentication.cache.add", return_value=True),
    ):
        response = trusted_client.generic("POST", path, body, content_type="application/json")

    assert response.status_code == status.HTTP_201_CREATED
    invitation = WorkspaceMemberInvite.objects.get(email=email, workspace=workspace)
    assert invitation.created_by_id == actor.id
