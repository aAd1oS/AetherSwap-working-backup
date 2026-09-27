from pathlib import Path


def test_order_center_exposes_only_state_safe_actions(monkeypatch):
    from app.routes import transactions

    monkeypatch.setattr(transactions, "reconcile_orders_from_local_records", lambda: {})
    monkeypatch.setattr(
        transactions,
        "load_app_config_validated",
        lambda: {"pipeline": {"target_balance": 100}},
    )
    monkeypatch.setattr(transactions, "get_daily_budget_summary", lambda _target: {})
    monkeypatch.setattr(transactions, "db_get_purchase_orders", lambda: [
        {"external_order_id": "pending", "status": "awaiting_payment", "source": "buff_manual"},
        {"external_order_id": "clicked", "status": "user_confirmed", "source": "buff_manual"},
        {"external_order_id": "paid", "status": "platform_confirmed", "source": "buff_batch"},
        {"external_order_id": "trade", "status": "awaiting_trade", "source": "buff_manual"},
    ])

    result = transactions.api_orders()
    orders = {row["external_order_id"]: row for row in result["orders"]}

    assert result["attention_count"] == 4
    assert orders["pending"]["available_actions"] == ["cancel", "replace_paid"]
    assert orders["clicked"]["available_actions"] == ["replace_paid"]
    assert orders["paid"]["available_actions"] == []
    assert orders["trade"]["available_actions"] == []
    assert orders["trade"]["needs_attention"] is True


def test_order_center_frontend_has_read_only_reconcile_entrypoint():
    root = Path(__file__).resolve().parents[1]
    html = (root / "web" / "index.html").read_text(encoding="utf-8")
    js = (root / "web" / "js" / "transactions.js").read_text(encoding="utf-8")

    assert 'id="btn-order-reconcile"' in html
    assert 'API + "/orders/reconcile"' in js
    assert "available_actions" in js
