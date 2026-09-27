from unittest.mock import MagicMock
from pathlib import Path
import requests
import pytest


def test_state_rejects_late_or_wrong_order_confirmation():
    from app.state import State

    state = State()
    state.set_pending_payment({"order_id": "order-b", "pay_url": "https://pay.invalid"})

    assert state.confirm_payment("order-a", True) is False
    assert state.confirm_payment("order-b", True) is True
    assert state.wait_payment_confirm("order-b", timeout_seconds=0.01) is True


def test_platform_paid_state_completes_manual_wait(monkeypatch):
    from app import pipeline_steps

    updates = []
    monkeypatch.setattr(pipeline_steps, "db_upsert_purchase_order", lambda row: row)
    monkeypatch.setattr(
        pipeline_steps,
        "db_update_purchase_order",
        lambda order_id, data, expected_statuses=None: updates.append((order_id, data)) or True,
    )
    client = MagicMock()
    client.get_bill_order_info_once.return_value = {
        "data": {"items": [{"state": "PAID", "state_text": "等待卖家发货"}]}
    }
    pending = []

    result = pipeline_steps._do_payment_notify_and_wait(
        client,
        {"name": "Item", "goods_id": 1},
        {"notify": {"email_timeout_seconds": 165}},
        2.0, 1, "https://pay.invalid", "wechat", "order-1", 0.0,
        pending.append,
        lambda *_args, **_kwargs: None,
        lambda *_args, **_kwargs: True,
        lambda: False,
        None,
    )

    assert result is True
    assert client.get_bill_order_info_once.call_count == 1
    assert pending[-1] is None
    assert any(data.get("status") == "platform_confirmed" for _order_id, data in updates)


def test_negative_user_answer_becomes_needs_review_without_platform_cancel(monkeypatch):
    from app import pipeline_steps

    updates = []
    monkeypatch.setattr(pipeline_steps, "db_upsert_purchase_order", lambda row: row)
    monkeypatch.setattr(
        pipeline_steps,
        "db_update_purchase_order",
        lambda order_id, data, expected_statuses=None: updates.append(data) or True,
    )
    client = MagicMock()
    client.get_bill_order_info_once.return_value = {
        "data": {"items": [{"state": "WAIT_PAY", "state_text": "等待付款"}]}
    }

    result = pipeline_steps._do_payment_notify_and_wait(
        client,
        {"name": "Item", "goods_id": 1},
        {"notify": {"email_timeout_seconds": 165}},
        2.0, 1, "https://pay.invalid", "wechat", "order-1", 0.0,
        lambda _value: None,
        lambda *_args, **_kwargs: False,
        lambda *_args, **_kwargs: True,
        lambda: False,
        None,
    )

    assert result is False
    assert updates[-1]["status"] == "needs_review"


def test_explicit_platform_cancel_releases_order(monkeypatch):
    from app import pipeline_steps

    updates = []
    monkeypatch.setattr(pipeline_steps, "db_upsert_purchase_order", lambda row: row)
    monkeypatch.setattr(
        pipeline_steps,
        "db_update_purchase_order",
        lambda order_id, data, expected_statuses=None: updates.append(data) or True,
    )
    client = MagicMock()
    client.get_bill_order_info_once.return_value = {
        "data": {"items": [{"state": "CANCELLED", "state_text": "已取消"}]}
    }

    pipeline_steps._do_payment_notify_and_wait(
        client,
        {"name": "Item", "goods_id": 1},
        {"notify": {"email_timeout_seconds": 165}},
        2.0, 1, "https://pay.invalid", "wechat", "order-1", 0.0,
        lambda _value: None,
        lambda *_args, **_kwargs: None,
        lambda *_args, **_kwargs: True,
        lambda: False,
        None,
    )

    assert updates[-1]["status"] == "cancelled"


def test_manual_payment_window_is_capped_at_165_seconds():
    from app.pipeline_steps import MANUAL_PAYMENT_MAX_SECONDS

    assert MANUAL_PAYMENT_MAX_SECONDS == 165


def test_frontend_submits_and_checks_bound_order_id():
    root = Path(__file__).resolve().parents[1]
    settings = (root / "web" / "js" / "settings.js").read_text(encoding="utf-8")
    main = (root / "web" / "js" / "main.js").read_text(encoding="utf-8")

    assert "JSON.stringify({ order_id: orderId, ok })" in settings
    assert "if (!result?.ok)" in settings
    assert "box.dataset.orderId = p.order_id" in main


def test_manual_lock_network_unknown_is_submitted_once(monkeypatch):
    from buff.buyer import BuffBuyer, BuffOrderOutcomeUnknown, PAY_METHOD_WECHAT

    buyer = BuffBuyer("csrf_token=test; session=test", pay_method=PAY_METHOD_WECHAT)
    calls = []

    def fail(*_args, **_kwargs):
        calls.append("post")
        raise requests.Timeout("uncertain")

    monkeypatch.setattr(buyer, "_make_request", fail)
    with pytest.raises(BuffOrderOutcomeUnknown):
        buyer.lock_order_once("csgo", 1, "sell-1", "2.00", PAY_METHOD_WECHAT, "111")
    assert calls == ["post"]


def test_batch_create_and_finalize_unknown_are_each_submitted_once(monkeypatch):
    from buff.buyer import BuffBuyer, BuffOrderOutcomeUnknown, PAY_METHOD_WECHAT

    buyer = BuffBuyer("csrf_token=test; session=test", pay_method=PAY_METHOD_WECHAT)
    calls = []

    def fail(_method, url, **_kwargs):
        calls.append(url)
        raise requests.Timeout("uncertain")

    monkeypatch.setattr(buyer, "_make_request", fail)
    with pytest.raises(BuffOrderOutcomeUnknown):
        buyer.batch_buy_create_once(1, 2.0, 2)
    with pytest.raises(BuffOrderOutcomeUnknown):
        buyer.batch_buy_finalize_once("csgo", 1, "sell-1", "2.0", "batch-1")
    assert len(calls) == 2


def test_user_click_does_not_claim_platform_payment(monkeypatch):
    from app import pipeline_steps

    updates = []
    monkeypatch.setattr(pipeline_steps, "db_upsert_purchase_order", lambda row: row)
    monkeypatch.setattr(
        pipeline_steps,
        "db_update_purchase_order",
        lambda order_id, data, expected_statuses=None: updates.append((order_id, data)) or True,
    )
    client = MagicMock()
    client.get_bill_order_info_once.return_value = {
        "data": {"items": [{"state": "WAIT_PAY", "state_text": "等待付款"}]}
    }

    result = pipeline_steps._do_payment_notify_and_wait(
        client,
        {"name": "Item", "goods_id": 1},
        {"notify": {"email_timeout_seconds": 165}},
        2.0, 1, "https://pay.invalid", "wechat", "order-1", 0.0,
        lambda _value: None,
        lambda *_args, **_kwargs: True,
        lambda *_args, **_kwargs: True,
        lambda: False,
        None,
    )

    assert result is False
    assert updates[-1][1]["status"] == "user_confirmed"
    assert "等待 BUFF 平台状态确认" in updates[-1][1]["error"]
