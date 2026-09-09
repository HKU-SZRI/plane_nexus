# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
import hashlib
import hmac
import json
import time
from collections.abc import Mapping
from uuid import UUID

# Django imports
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from django.db.models import Q

# Third party imports
import jwt
from rest_framework import authentication
from rest_framework.exceptions import AuthenticationFailed

# Module imports
from plane.db.models import APIToken, Project, User


TRUSTED_NEXUS_AUTH_HEADER = "X-Nexus-Trust-Token"
TRUSTED_NEXUS_JWT_ALGORITHM = "RS256"
TRUSTED_NEXUS_RESERVED_ATTRIBUTION_FIELDS = frozenset(
    {"actor", "actor_id", "created_by", "created_by_id", "updated_by", "updated_by_id"}
)


def _trusted_nexus_public_keys():
    return tuple(
        key
        for key in (
            settings.NEXUS_TRUSTED_JWT_PUBLIC_KEY,
            settings.NEXUS_TRUSTED_JWT_PREVIOUS_PUBLIC_KEY,
        )
        if key
    )


def _request_body(request) -> bytes:
    body = request.body
    return body if isinstance(body, bytes) else bytes(body or b"")


def _reject_reserved_attribution_fields(request, body: bytes) -> None:
    payload = None
    if body:
        try:
            payload = json.loads(body)
        except (TypeError, ValueError, UnicodeDecodeError):
            payload = request.data
    pending = [payload]
    while pending:
        value = pending.pop()
        if isinstance(value, Mapping):
            if TRUSTED_NEXUS_RESERVED_ATTRIBUTION_FIELDS.intersection(value):
                raise AuthenticationFailed("Trusted Nexus requests cannot override audit attribution")
            pending.extend(value.values())
        elif isinstance(value, (list, tuple)):
            pending.extend(value)


def _decode_trusted_nexus_token(token: str) -> dict:
    public_keys = _trusted_nexus_public_keys()
    if not public_keys:
        raise AuthenticationFailed("Trusted Nexus authentication is not configured")

    required_claims = [
        "actor_plane_id",
        "aud",
        "body_sha256",
        "exp",
        "iat",
        "iss",
        "jti",
        "method",
        "path",
        "query",
        "workspace_slug",
    ]
    last_error = None
    for public_key in public_keys:
        try:
            return jwt.decode(
                token,
                public_key,
                algorithms=[TRUSTED_NEXUS_JWT_ALGORITHM],
                audience=settings.NEXUS_TRUSTED_JWT_AUDIENCE,
                issuer=settings.NEXUS_TRUSTED_JWT_ISSUER,
                leeway=settings.NEXUS_TRUSTED_JWT_CLOCK_SKEW_SECONDS,
                options={"require": required_claims},
            )
        except (jwt.PyJWTError, TypeError, ValueError) as exc:
            last_error = exc
    raise AuthenticationFailed("Trusted Nexus token is invalid") from last_error


class APIKeyAuthentication(authentication.BaseAuthentication):
    """
    Authentication with an API Key
    """

    www_authenticate_realm = "api"
    media_type = "application/json"
    auth_header_name = "X-Api-Key"

    def get_api_token(self, request):
        return request.headers.get(self.auth_header_name)

    def validate_api_token(self, token):
        try:
            api_token = APIToken.objects.get(
                Q(Q(expired_at__gt=timezone.now()) | Q(expired_at__isnull=True)),
                token=token,
                is_active=True,
            )
        except APIToken.DoesNotExist:
            raise AuthenticationFailed("Given API token is not valid")

        # save api token last used
        api_token.last_used = timezone.now()
        api_token.save(update_fields=["last_used"])
        return (api_token.user, api_token.token)

    def authenticate(self, request):
        token = self.get_api_token(request=request)
        if not token:
            return None

        if request.headers.get(TRUSTED_NEXUS_AUTH_HEADER):
            raise AuthenticationFailed("API key and trusted Nexus credentials cannot be combined")

        # Validate the API token
        user, token = self.validate_api_token(token)
        return user, token


class InternalServiceAuthentication(authentication.BaseAuthentication):
    """Authenticate a one-time, operation-bound token issued by Nexus."""

    def authenticate(self, request):
        token = request.headers.get(TRUSTED_NEXUS_AUTH_HEADER)
        if not token:
            return None

        if request.headers.get(APIKeyAuthentication.auth_header_name):
            raise AuthenticationFailed("API key and trusted Nexus credentials cannot be combined")
        claims = _decode_trusted_nexus_token(token)
        body = _request_body(request)
        expected_body_hash = hashlib.sha256(body).hexdigest()

        parser_context = request.parser_context or {}
        request_kwargs = parser_context.get("kwargs") or {}
        workspace_slug = str(request_kwargs.get("slug") or "")
        claim_method = claims.get("method")
        claim_path = claims.get("path")
        claim_query = claims.get("query")
        claim_workspace_slug = claims.get("workspace_slug")
        claim_body_hash = claims.get("body_sha256")
        if not isinstance(claim_method, str) or not hmac.compare_digest(claim_method, request.method.upper()):
            raise AuthenticationFailed("Trusted Nexus token method does not match the request")
        if not isinstance(claim_path, str) or not hmac.compare_digest(claim_path, request.path):
            raise AuthenticationFailed("Trusted Nexus token path does not match the request")
        raw_query = request.META.get("QUERY_STRING", "")
        if not isinstance(claim_query, str) or not hmac.compare_digest(claim_query, raw_query):
            raise AuthenticationFailed("Trusted Nexus token query does not match the request")
        if not isinstance(claim_workspace_slug, str) or not hmac.compare_digest(claim_workspace_slug, workspace_slug):
            raise AuthenticationFailed("Trusted Nexus token workspace does not match the request")
        if not isinstance(claim_body_hash, str) or not hmac.compare_digest(claim_body_hash, expected_body_hash):
            raise AuthenticationFailed("Trusted Nexus token body does not match the request")

        actor_plane_id = claims.get("actor_plane_id")
        jti = claims.get("jti")
        issued_at = claims.get("iat")
        expires_at = claims.get("exp")
        if not isinstance(actor_plane_id, str) or not actor_plane_id:
            raise AuthenticationFailed("Trusted Nexus token actor is invalid")
        try:
            actor_uuid = UUID(actor_plane_id)
        except (TypeError, ValueError) as exc:
            raise AuthenticationFailed("Trusted Nexus token actor is invalid") from exc
        if not isinstance(jti, str) or not 1 <= len(jti) <= 128:
            raise AuthenticationFailed("Trusted Nexus token identifier is invalid")
        if (
            isinstance(issued_at, bool)
            or isinstance(expires_at, bool)
            or not isinstance(issued_at, (int, float))
            or not isinstance(expires_at, (int, float))
        ):
            raise AuthenticationFailed("Trusted Nexus token timestamps are invalid")
        max_ttl = settings.NEXUS_TRUSTED_JWT_MAX_TTL_SECONDS
        if expires_at <= issued_at or expires_at - issued_at > max_ttl:
            raise AuthenticationFailed("Trusted Nexus token lifetime is invalid")

        project_id = request_kwargs.get("project_id")
        view = parser_context.get("view")
        if project_id is None and getattr(view, "model", None) is Project:
            project_id = request_kwargs.get("pk")
        if (
            project_id is not None
            and not Project.objects.filter(id=project_id, workspace__slug=workspace_slug).exists()
        ):
            raise AuthenticationFailed("Trusted Nexus request project does not belong to the workspace")

        _reject_reserved_attribution_fields(request, body)

        user = User.objects.filter(id=actor_uuid, is_active=True).first()
        if user is None:
            raise AuthenticationFailed("Trusted Nexus token actor does not exist or is inactive")

        replay_digest = hashlib.sha256(f"{claims['iss']}:{jti}".encode()).hexdigest()
        replay_key = f"nexus-trusted-token:{replay_digest}"
        replay_timeout = max(
            1,
            min(
                max_ttl + settings.NEXUS_TRUSTED_JWT_CLOCK_SKEW_SECONDS,
                int(expires_at - time.time()) + settings.NEXUS_TRUSTED_JWT_CLOCK_SKEW_SECONDS,
            ),
        )
        try:
            first_use = cache.add(replay_key, True, timeout=replay_timeout)
        except Exception as exc:
            raise AuthenticationFailed("Trusted Nexus replay protection is unavailable") from exc
        if not first_use:
            raise AuthenticationFailed("Trusted Nexus token has already been used")

        request.is_trusted_nexus_call = True
        request.trusted_nexus_claims = claims
        return user, token


API_AUTHENTICATION_CLASSES = (APIKeyAuthentication, InternalServiceAuthentication)
