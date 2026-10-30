"""Current lifecycle and wait messages take precedence over earlier review notes."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.chat import request_view


@pytest.mark.parametrize(
    "status,reason,waiting,expected",
    [
        ("Cancelled", None, False, "Request cancelled"),
        ("Stopped", "DeadlineExpired", False, "This request expired"),
        ("Waiting", None, True, "Waiting for model budget"),
        ("Queued", None, False, "The owner requested changes"),
        ("Stopped", "CorrectionLimit", False, "The owner requested changes"),
    ],
)
def test_review_message_does_not_hide_current_lifecycle(monkeypatch, status, reason, waiting, expected):
    row = SimpleNamespace(
        id="request",
        user_id="owner",
        status=status,
        kind="Training",
        revisions=2,
        result={"review_message": "The owner requested changes.", "stop_reason": reason},
    )
    work = SimpleNamespace(role="Coach", wait_reason="BudgetRequired") if waiting else None
    db = Mock()
    db.get.return_value = SimpleNamespace(role="Owner")
    db.scalar.side_effect = [work, work]
    monkeypatch.setattr("app.chat.view", lambda _: {})
    assert request_view(db, row)["status_message"].startswith(expected)
