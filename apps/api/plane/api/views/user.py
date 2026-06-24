# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Third party imports
from rest_framework import status
from rest_framework.response import Response
from drf_spectacular.utils import OpenApiResponse

# Module imports
from plane.api.serializers import UserLiteSerializer
from plane.api.views.base import BaseAPIView
from plane.db.models import User, APIToken
from plane.utils.openapi.decorators import user_docs
from plane.utils.openapi import USER_EXAMPLE


class AdminUserApiTokenEndpoint(BaseAPIView):
    """Service-token only: provision a personal API token for any user."""

    def post(self, request, user_id):
        # Only service tokens (is_service=True) may call this endpoint
        caller_token = APIToken.objects.filter(token=request.auth, is_service=True).first()
        if not caller_token:
            return Response({"error": "Service token required"}, status=status.HTTP_403_FORBIDDEN)

        target_user = User.objects.filter(pk=user_id).first()
        if not target_user:
            return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)

        # get_or_create so re-calling is idempotent
        api_token, created = APIToken.objects.get_or_create(
            user=target_user,
            label="nexus-provisioned",
            defaults={"user_type": 0, "is_service": True},
        )

        return Response(
            {"token": api_token.token, "user_id": str(user_id), "created": created},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class UserEndpoint(BaseAPIView):
    serializer_class = UserLiteSerializer
    model = User

    @user_docs(
        operation_id="get_current_user",
        summary="Get current user",
        description="Retrieve the authenticated user's profile information including basic details.",
        responses={
            200: OpenApiResponse(
                description="Current user profile",
                response=UserLiteSerializer,
                examples=[USER_EXAMPLE],
            ),
        },
    )
    def get(self, request):
        """Get current user

        Retrieve the authenticated user's profile information including basic details.
        Returns user data based on the current authentication context.
        """
        serializer = UserLiteSerializer(request.user)
        return Response(serializer.data, status=status.HTTP_200_OK)
