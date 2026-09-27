from datetime import datetime

from app import order_state


def test_daily_budget_survives_restart_by_reading_persisted_rows(monkeypatch):
    now = datetime(2026, 8, 4, 12, 0, 0)
    today = now.replace(hour=9).timestamp()
    yesterday = datetime(2026, 8, 3, 23, 59, 0).timestamp()
    monkeypatch.setattr(order_state, "db_get_purchases", lambda: [
        {"price": 8.25, "at": today},
        {"price": 15.0, "at": today, "source": "manual"},
        {"price": 99.0, "at": yesterday},
    ])
    monkeypatch.setattr(order_state, "db_get_purchase_orders", lambda: [])

    summary = order_state.get_daily_budget_summary(30, now=now)

    assert summary["confirmed"] == 8.25
    assert summary["remaining"] == 21.75


def test_unresolved_order_reserves_budget_without_double_counting(monkeypatch):
    now = datetime(2026, 8, 4, 12, 0, 0)
    today = now.replace(hour=10).timestamp()
    monkeypatch.setattr(order_state, "db_get_purchases", lambda: [
        {"price": 5.0, "at": today, "external_order_id": "paid-1"},
    ])
    monkeypatch.setattr(order_state, "db_get_purchase_orders", lambda: [
        {"external_order_id": "paid-1", "total_price": 5.0, "created_at": today, "status": "user_confirmed"},
        {"external_order_id": "pending-2", "total_price": 7.5, "created_at": today, "status": "awaiting_payment"},
    ])

    summary = order_state.get_daily_budget_summary(30, now=now)

    assert summary["confirmed"] == 5.0
    assert summary["reserved"] == 7.5
    assert summary["remaining"] == 17.5


def test_only_payment_uncertainty_blocks_new_orders(monkeypatch):
    monkeypatch.setattr(order_state, "db_get_purchase_orders", lambda: [
        {"external_order_id": "a", "status": "awaiting_payment"},
        {"external_order_id": "b", "status": "awaiting_ship"},
        {"external_order_id": "c", "status": "needs_review"},
    ])

    assert [row["external_order_id"] for row in order_state.get_blocking_payment_orders()] == ["a", "c"]


def test_purchase_status_prefers_terminal_and_inventory_states():
    assert order_state.derive_purchase_status({"sale_price": 10, "listing": True}) == "sold"
    assert order_state.derive_purchase_status({"listing": True}) == "listed"
    assert order_state.derive_purchase_status({"pending_receipt": True}) == "awaiting_trade"
    assert order_state.derive_purchase_status({
        "pending_receipt": True,
        "order_status": "awaiting_ship",
    }) == "awaiting_ship"
    assert order_state.derive_purchase_status({
        "pending_receipt": True,
        "order_status": "awaiting_trade",
    }) == "awaiting_trade"
    assert order_state.derive_purchase_status({"assetid": "1"}) == "received"
    assert order_state.derive_purchase_status({
        "assetid": "2",
        "order_status": "trade_locked",
    }) == "trade_locked"


def test_reconcile_does_not_revive_terminal_order(monkeypatch):
    updates = []
    monkeypatch.setattr(order_state, "db_get_purchases", lambda: [{
        "external_order_id": "cancelled-1",
        "assetid": "123",
    }])
    monkeypatch.setattr(order_state, "db_get_purchase_orders", lambda: [{
        "external_order_id": "cancelled-1",
        "status": "cancelled",
    }])
    monkeypatch.setattr(
        order_state,
        "db_update_purchase_order",
        lambda *args, **kwargs: updates.append((args, kwargs)) or True,
    )

    result = order_state.reconcile_orders_from_local_records()

    assert result["changed"] == 0
    assert updates == []


def test_reconcile_checks_every_manual_order_and_keeps_batch_intermediate(monkeypatch):
    orders = [
        {"external_order_id": "single", "status": "user_confirmed", "source": "buff_manual"},
        {"external_order_id": "batch", "status": "user_confirmed", "source": "buff_batch"},
    ]
    updates = []
    finalized = []
    monkeypatch.setattr(order_state, "db_get_purchase_orders", lambda: orders)
    monkeypatch.setattr(
        order_state,
        "db_update_purchase_order",
        lambda order_id, data, expected_statuses=None: updates.append((order_id, data)) or True,
    )
    monkeypatch.setattr(
        order_state,
        "db_finalize_purchase_order_payment",
        lambda order_id, user_confirmed=False: finalized.append(order_id) or {"created": 1},
    )

    class Client:
        def get_bill_order_info_once(self, _order_id):
            return {"data": {"items": [{"state": "PAID", "state_text": "等待卖家发货"}]}}

    result = order_state.reconcile_manual_payment_orders(Client())

    assert result["checked_count"] == 2
    assert result["changed_count"] == 2
    assert finalized == ["single"]
    assert updates[0][0] == "batch"
    assert updates[0][1]["status"] == "platform_confirmed"


def test_reconcile_propagates_purchase_needs_review_without_erasing_reason(monkeypatch):
    updates = []
    monkeypatch.setattr(order_state, "db_get_purchases", lambda: [{
        "external_order_id": "order-1",
        "pending_receipt": True,
        "order_status": "needs_review",
    }])
    monkeypatch.setattr(order_state, "db_get_purchase_orders", lambda: [{
        "external_order_id": "order-1",
        "status": "awaiting_trade",
        "error": "报价无法唯一匹配",
    }])
    monkeypatch.setattr(
        order_state,
        "db_update_purchase_order",
        lambda order_id, data: updates.append((order_id, data)) or True,
    )

    order_state.reconcile_orders_from_local_records()

    assert updates == [("order-1", {"status": "needs_review"})]
