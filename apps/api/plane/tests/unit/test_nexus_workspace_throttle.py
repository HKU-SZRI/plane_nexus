from types import SimpleNamespace

import pytest
from rest_framework.throttling import AnonRateThrottle

from plane.app.views.nexus_onboarding import NexusWorkspaceThrottle


@pytest.mark.unit
def test_only_valid_internal_key_bypasses_anonymous_throttle(monkeypatch):
    monkeypatch.setenv("PLANE_NEXUS_ONBOARDING_KEY", "secret")
    monkeypatch.setattr(AnonRateThrottle, "allow_request", lambda self, request, view: False)
    throttle = NexusWorkspaceThrottle()

    assert throttle.allow_request(SimpleNamespace(headers={"X-Internal-Key": "secret"}), None)
    assert not throttle.allow_request(SimpleNamespace(headers={"X-Internal-Key": "wrong"}), None)
    assert not throttle.allow_request(SimpleNamespace(headers={}), None)
