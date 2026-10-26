"""Checks over the whole run: no provider key ever reached the fake, no secret was logged by any service, and the
live rag-app stack (when this machine runs one) is exactly as it was when the session started."""

import httpx
import pytest
from conftest import LIVE_BASELINE
from harness import compose, leaked_secrets, live_containers


def test_every_model_call_went_to_the_fake_with_the_placeholder_key(fake) -> None:
    calls = fake.log()
    assert calls, "the suite made no model call"
    assert {call["auth"] for call in calls} == {"placeholder"}
    assert {call["kind"] for call in calls} >= {"chat", "embedding"}


def test_no_secret_in_the_service_logs() -> None:
    """test_14 checks the logs of the containers it replaces before replacing them."""
    logs = compose("logs", "--no-color", timeout=120)
    assert logs
    leaked = leaked_secrets(logs)
    assert not leaked, f"secrets found in the stack logs: {leaked}"


def test_live_stack_is_untouched() -> None:
    if not LIVE_BASELINE:
        pytest.skip("no live rag-app stack on this machine")
    assert live_containers() == LIVE_BASELINE
    assert httpx.get("http://localhost:8080/api/health", timeout=10).status_code == 200
