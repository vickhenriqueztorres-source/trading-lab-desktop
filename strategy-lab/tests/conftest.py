"""Strategy Lab test boundaries."""

import os
import socket

import pytest


@pytest.fixture(autouse=True)
def no_network(monkeypatch, request):
    if request.node.get_closest_marker("staging"):
        staging_url = os.environ.get("SUPABASE_STAGING_DB_URL", "")
        prod_ref = os.environ.get("SUPABASE_PROD_REF", "")
        if not staging_url:
            pytest.skip("SUPABASE_STAGING_DB_URL is required for staging tests")
        if prod_ref and prod_ref in staging_url:
            pytest.fail("Refusing to run staging tests against production ref")
        return

    def denied(*args, **kwargs):
        raise AssertionError("Network is forbidden in the Strategy Lab test suite")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
