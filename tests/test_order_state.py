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
