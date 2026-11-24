"""MCP Events regressions: cursor correctness, gaps, caps and atomicity."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from common import global_config
from db.models.events import EventLog, EventSubscription
from services import InvalidArgument
from services.events import emit
from services.events.catalog import APPROVAL_RESOLVED
from services.events.store import prune, purge_user
from tests.test_events import (
    _env,
    _feedback,
    _poll,
    _Receiver,
    _secret,
    _subscribe,
)
from tests.test_template import TestTemplate
from tests.test_webhooks import _mock_http


class TestReviewFixes(TestTemplate):
    """Regression tests for the issues found in review."""

    def test_cursor_is_per_user_and_dense(self):
        with _env() as factory:
            _feedback(user="a")
            _feedback(user="b")
            _feedback(user="a", i=1)
            with factory() as s:
                seqs = [
                    r.user_seq
                    for r in s.query(EventLog)
                    .filter_by(user_id="a")
                    .order_by(EventLog.seq)
                ]
            assert seqs == [1, 2]  # another user's emit never consumes a's cursor

    def test_future_cursor_is_refused(self):
        with _env(), pytest.raises(InvalidArgument):
            _poll(cursor="999")

    def test_fully_pruned_history_is_truncated(self):
        with _env() as factory:
            start = _poll()["cursor"]
            _feedback()
            with factory() as s:
                s.query(EventLog).update(
                    {"created_at": datetime.now(UTC) - timedelta(days=30)}
                )
                s.commit()
            prune()
            assert _poll(cursor=start)["truncated"] is True

    def test_max_age_only_looks_at_this_users_events(self):
        with _env() as factory:
            start = _poll()["cursor"]
            _feedback(user="other")
            with factory() as s:
                s.query(EventLog).update(
                    {"created_at": datetime.now(UTC) - timedelta(hours=1)}
                )
                s.commit()
            assert _poll(cursor=start, max_age_ms=1000)["truncated"] is False

    def test_refresh_does_not_renew_verification(self):
        secret = _secret()
        receiver = _Receiver(secret)
        with _env() as factory, _mock_http(receiver):
            _subscribe(secret=secret)
            with factory() as s:
                first = s.query(EventSubscription).one().verified_at
            _subscribe(secret=secret)
            with factory() as s:
                assert s.query(EventSubscription).one().verified_at == first
            challenges = [r for r in receiver.requests if b"verification" in r.content]
            assert len(challenges) == 1
            with factory() as s:  # past the cache window: challenged again
                s.query(EventSubscription).update(
                    {"verified_at": datetime.now(UTC) - timedelta(days=2)}
                )
                s.commit()
            _subscribe(secret=secret)
            challenges = [r for r in receiver.requests if b"verification" in r.content]
            assert len(challenges) == 2

    def test_subscription_cap(self):
        secret = _secret()
        with (
            _env(),
            _mock_http(_Receiver(secret)),
            patch.object(global_config.events, "max_subscriptions_per_user", 1),
        ):
            _subscribe(secret=secret)
            _subscribe(secret=secret)  # a refresh is not a new one
            with pytest.raises(InvalidArgument, match="At most 1"):
                _subscribe(secret=secret, url="https://hooks.example.com/other")

    def test_capped_replay_reports_the_gap(self):
        secret = _secret()
        with (
            _env(),
            _mock_http(_Receiver(secret)),
            patch("services.events.webhooks._REPLAY_LIMIT", 2),
        ):
            start = _poll()["cursor"]
            for i in range(3):
                _feedback(i=i)
            assert _subscribe(secret=secret, cursor=start)["truncated"] is True

    def test_approval_and_its_event_commit_together(self):
        from services.approvals_svc import deny, get_approval_view  # noqa: PLC0415
        from tests.test_approvals import _danger_tool, _held, _set_mode  # noqa: PLC0415

        with _env(), _danger_tool() as entry:
            _set_mode("u", "browser")
            view = _held(entry, "u")
            with (
                patch("services.approvals_svc.emit", side_effect=RuntimeError("db")),
                pytest.raises(RuntimeError),
            ):
                deny("u", view.id)
            assert get_approval_view("u", view.id).status == "pending"

    def test_delivery_never_crosses_users(self):
        from services.events.webhooks import deliverable  # noqa: PLC0415

        secret = _secret()
        with _env() as factory, _mock_http(_Receiver(secret)):
            sub_id = _subscribe(secret=secret)["id"]
            other_event = _feedback(user="someone-else")
            with factory() as s:
                assert deliverable(s, sub_id, other_event) is None

    def test_mcp_retry_schedule_fits_the_spec_window(self):
        from services.webhook_delivery_svc import _CHANNELS  # noqa: PLC0415

        policy = _CHANNELS["mcp_event"].policy
        delays = [policy.delay_s(n) for n in range(1, policy.max_attempts())]
        assert 3 <= policy.max_attempts() <= 5
        assert 10 * 60 <= sum(delays) <= 15 * 60

    def test_gmail_purge_does_not_skip_other_types(self):
        with _env():
            start = _poll(name=APPROVAL_RESOLVED)["cursor"]
            emit("u", "gmail.message_received", {"messageId": "m1"})
            emit(
                "u",
                APPROVAL_RESOLVED,
                {"approvalId": "a", "tool": "t", "status": "denied"},
            )
            emit("u", "gmail.message_received", {"messageId": "m2"})
            purge_user("u", "gmail.message_received")
            got = _poll(name=APPROVAL_RESOLVED, cursor=start)
            assert [e["data"]["approvalId"] for e in got["events"]] == ["a"]
