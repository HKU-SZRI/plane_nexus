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
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from plane.api.middleware.api_authentication import (
    APIKeyAuthentication,
    InternalServiceAuthentication,
)
from plane.tests.factories import ProjectFactory, UserFactory, WorkspaceFactory


@pytest.fixture(scope="module")
def signing_keys():
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


def make_request(
    *,
    method="POST",
    path="/api/v1/workspaces/acme/projects/",
    query="",
    body=b"{}",
    token=None,
    kwargs=None,
):
    headers = {}
    if token is not None:
        headers["HTTP_X_NEXUS_TRUST_TOKEN"] = token
    django_request = APIRequestFactory().generic(
        method,
        f"{path}?{query}" if query else path,
        data=body,
        content_type="application/json",
        **headers,
    )
    return Request(
        django_request,
        parser_context={"kwargs": {"slug": "acme"} if kwargs is None else kwargs},
    )


def make_token(
    private_key,
    actor_id,
    *,
    method="POST",
    path="/api/v1/workspaces/acme/projects/",
    query="",
    body=b"{}",
    **overrides,
):
    now = int(time.time())
    claims = {
        "actor_plane_id": str(actor_id),
        "aud": "plane",
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "exp": now + 60,
        "iat": now,
        "iss": "nexus",
        "jti": uuid4().hex,
        "method": method,
        "path": path,
        "query": query,
        "workspace_slug": "acme",
    }
    claims.update(overrides)
    return jwt.encode(claims, private_key, algorithm="RS256")


@pytest.mark.unit
@pytest.mark.django_db
class TestInternalServiceAuthentication:
    def test_authenticates_actor_and_marks_exact_request(self, signing_keys):
        private_key, public_key = signing_keys
        actor = UserFactory()
        body = json.dumps({"name": "Trusted work item"}, separators=(",", ":")).encode()
        token = make_token(private_key, actor.id, body=body)
        request = make_request(body=body, token=token)

        with (
            override_settings(NEXUS_TRUSTED_JWT_PUBLIC_KEY=public_key.decode()),
            patch("plane.api.middleware.api_authentication.cache.add", return_value=True) as cache_add,
        ):
            user, returned_token = InternalServiceAuthentication().authenticate(request)

        assert user == actor
        assert returned_token == token
        assert request.is_trusted_nexus_call is True
        assert request.trusted_nexus_claims["actor_plane_id"] == str(actor.id)
        cache_add.assert_called_once()

    @pytest.mark.parametrize(
        ("claim_overrides", "request_overrides", "message"),
        [
            ({"method": "PATCH"}, {}, "method"),
            ({"path": "/api/v1/workspaces/acme/projects/wrong/"}, {}, "path"),
            ({"query": "page=2"}, {}, "query"),
            ({"workspace_slug": "other"}, {}, "workspace"),
            ({"body_sha256": hashlib.sha256(b'{"name":"different"}').hexdigest()}, {}, "body"),
        ],
    )
    def test_rejects_request_binding_mismatch(self, signing_keys, claim_overrides, request_overrides, message):
        private_key, public_key = signing_keys
        actor = UserFactory()
        token = make_token(private_key, actor.id, **claim_overrides)
        request = make_request(token=token, **request_overrides)

        with (
            override_settings(NEXUS_TRUSTED_JWT_PUBLIC_KEY=public_key.decode()),
            patch("plane.api.middleware.api_authentication.cache.add") as cache_add,
            pytest.raises(AuthenticationFailed, match=message),
        ):
            InternalServiceAuthentication().authenticate(request)

        cache_add.assert_not_called()
        assert not hasattr(request, "is_trusted_nexus_call")

    def test_rejects_replayed_jti(self, signing_keys):
        private_key, public_key = signing_keys
        actor = UserFactory()
        token = make_token(private_key, actor.id)

        with (
            override_settings(NEXUS_TRUSTED_JWT_PUBLIC_KEY=public_key.decode()),
            patch("plane.api.middleware.api_authentication.cache.add", side_effect=[True, False]),
        ):
            InternalServiceAuthentication().authenticate(make_request(token=token))
            with pytest.raises(AuthenticationFailed, match="already been used"):
                InternalServiceAuthentication().authenticate(make_request(token=token))

    def test_authenticates_safe_method(self, signing_keys):
        private_key, public_key = signing_keys
        actor = UserFactory()
        token = make_token(private_key, actor.id, method="GET", body=b"")

        with (
            override_settings(NEXUS_TRUSTED_JWT_PUBLIC_KEY=public_key.decode()),
            patch("plane.api.middleware.api_authentication.cache.add", return_value=True),
        ):
            user, returned_token = InternalServiceAuthentication().authenticate(
                make_request(method="GET", body=b"", token=token)
            )

        assert user == actor
        assert returned_token == token

    def test_authenticates_non_workspace_endpoint_with_empty_workspace_claim(self, signing_keys):
        private_key, public_key = signing_keys
        actor = UserFactory()
        path = "/api/v1/users/me/"
        token = make_token(
            private_key,
            actor.id,
            method="GET",
            path=path,
            body=b"",
            workspace_slug="",
        )

        with (
            override_settings(NEXUS_TRUSTED_JWT_PUBLIC_KEY=public_key.decode()),
            patch("plane.api.middleware.api_authentication.cache.add", return_value=True),
        ):
            user, returned_token = InternalServiceAuthentication().authenticate(
                make_request(method="GET", path=path, body=b"", token=token, kwargs={})
            )

        assert user == actor
        assert returned_token == token

    @pytest.mark.parametrize(
        "payload",
        [
            {"actor": str(uuid4())},
            {"created_by": str(uuid4())},
            {"updated_by_id": str(uuid4())},
            {"items": [{"created_by": str(uuid4())}]},
        ],
    )
    def test_rejects_audit_attribution_fields(self, signing_keys, payload):
        private_key, public_key = signing_keys
        actor = UserFactory()
        body = json.dumps({"name": "Trusted work item", **payload}).encode()
        token = make_token(private_key, actor.id, body=body)

        with (
            override_settings(NEXUS_TRUSTED_JWT_PUBLIC_KEY=public_key.decode()),
            patch("plane.api.middleware.api_authentication.cache.add") as cache_add,
            pytest.raises(AuthenticationFailed, match="audit attribution"),
        ):
            InternalServiceAuthentication().authenticate(make_request(body=body, token=token))

        cache_add.assert_not_called()

    def test_rejects_audit_attribution_fields_in_multipart_body(self, signing_keys):
        private_key, public_key = signing_keys
        actor = UserFactory()
        django_request = APIRequestFactory().post(
            "/api/v1/workspaces/acme/assets/",
            {"name": "attachment.txt", "created_by": str(uuid4())},
            format="multipart",
        )
        body = django_request.body
        django_request.META["HTTP_X_NEXUS_TRUST_TOKEN"] = make_token(
            private_key,
            actor.id,
            path="/api/v1/workspaces/acme/assets/",
            body=body,
        )
        request = Request(
            django_request,
            parsers=[MultiPartParser(), FormParser()],
            parser_context={"kwargs": {"slug": "acme"}},
        )

        with (
            override_settings(NEXUS_TRUSTED_JWT_PUBLIC_KEY=public_key.decode()),
            patch("plane.api.middleware.api_authentication.cache.add") as cache_add,
            pytest.raises(AuthenticationFailed, match="audit attribution"),
        ):
            InternalServiceAuthentication().authenticate(request)

        cache_add.assert_not_called()

    def test_rejects_missing_or_inactive_actor(self, signing_keys):
        private_key, public_key = signing_keys
        inactive_actor = UserFactory(is_active=False)
        token = make_token(private_key, inactive_actor.id)

        with (
            override_settings(NEXUS_TRUSTED_JWT_PUBLIC_KEY=public_key.decode()),
            pytest.raises(AuthenticationFailed, match="does not exist or is inactive"),
        ):
            InternalServiceAuthentication().authenticate(make_request(token=token))

    def test_rejects_malformed_actor_id(self, signing_keys):
        private_key, public_key = signing_keys
        token = make_token(private_key, "not-a-uuid")

        with (
            override_settings(NEXUS_TRUSTED_JWT_PUBLIC_KEY=public_key.decode()),
            pytest.raises(AuthenticationFailed, match="actor is invalid"),
        ):
            InternalServiceAuthentication().authenticate(make_request(token=token))

    def test_rejects_project_from_another_workspace(self, signing_keys):
        private_key, public_key = signing_keys
        actor = UserFactory()
        other_workspace = WorkspaceFactory(owner=actor, slug="other-workspace")
        project = ProjectFactory(workspace=other_workspace)
        path = f"/api/v1/workspaces/acme/projects/{project.id}/work-items/"
        token = make_token(private_key, actor.id, path=path)

        with (
            override_settings(NEXUS_TRUSTED_JWT_PUBLIC_KEY=public_key.decode()),
            pytest.raises(AuthenticationFailed, match="does not belong"),
        ):
            InternalServiceAuthentication().authenticate(
                make_request(
                    path=path,
                    token=token,
                    kwargs={"slug": "acme", "project_id": project.id},
                )
            )

    def test_missing_public_key_disables_authentication(self, signing_keys):
        private_key, _ = signing_keys
        actor = UserFactory()
        token = make_token(private_key, actor.id)

        with (
            override_settings(
                NEXUS_TRUSTED_JWT_PUBLIC_KEY="",
                NEXUS_TRUSTED_JWT_PREVIOUS_PUBLIC_KEY="",
            ),
            pytest.raises(AuthenticationFailed, match="not configured"),
        ):
            InternalServiceAuthentication().authenticate(make_request(token=token))

    @pytest.mark.parametrize(
        "claim_overrides",
        [
            {"iss": "not-nexus"},
            {"aud": "not-plane"},
            {"iat": int(time.time()) - 20, "exp": int(time.time()) - 10},
        ],
    )
    def test_rejects_invalid_issuer_audience_or_expiry(self, signing_keys, claim_overrides):
        private_key, public_key = signing_keys
        actor = UserFactory()
        token = make_token(private_key, actor.id, **claim_overrides)

        with (
            override_settings(NEXUS_TRUSTED_JWT_PUBLIC_KEY=public_key.decode()),
            pytest.raises(AuthenticationFailed, match="token is invalid"),
        ):
            InternalServiceAuthentication().authenticate(make_request(token=token))

    def test_rejects_wrong_signature(self, signing_keys):
        _, public_key = signing_keys
        actor = UserFactory()
        unrelated_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        token = make_token(unrelated_private_key, actor.id)

        with (
            override_settings(NEXUS_TRUSTED_JWT_PUBLIC_KEY=public_key.decode()),
            pytest.raises(AuthenticationFailed, match="token is invalid"),
        ):
            InternalServiceAuthentication().authenticate(make_request(token=token))

    def test_previous_public_key_is_accepted_during_rotation(self, signing_keys):
        private_key, previous_public_key = signing_keys
        actor = UserFactory()
        token = make_token(private_key, actor.id)

        with (
            override_settings(
                NEXUS_TRUSTED_JWT_PUBLIC_KEY="invalid-current-key",
                NEXUS_TRUSTED_JWT_PREVIOUS_PUBLIC_KEY=previous_public_key.decode(),
            ),
            patch("plane.api.middleware.api_authentication.cache.add", return_value=True),
        ):
            user, _ = InternalServiceAuthentication().authenticate(make_request(token=token))

        assert user == actor

    def test_rejects_token_lifetime_above_limit(self, signing_keys):
        private_key, public_key = signing_keys
        actor = UserFactory()
        now = int(time.time())
        token = make_token(private_key, actor.id, iat=now, exp=now + 61)

        with (
            override_settings(NEXUS_TRUSTED_JWT_PUBLIC_KEY=public_key.decode()),
            pytest.raises(AuthenticationFailed, match="lifetime"),
        ):
            InternalServiceAuthentication().authenticate(make_request(token=token))

    def test_rejects_mixed_credentials_before_api_key_lookup(self, signing_keys):
        private_key, _ = signing_keys
        actor = UserFactory()
        token = make_token(private_key, actor.id)
        request = make_request(token=token)
        request._request.META["HTTP_X_API_KEY"] = "api-key"

        with pytest.raises(AuthenticationFailed, match="cannot be combined"):
            InternalServiceAuthentication().authenticate(request)
        with pytest.raises(AuthenticationFailed, match="cannot be combined"):
            APIKeyAuthentication().authenticate(request)
