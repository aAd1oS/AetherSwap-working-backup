import pytest
from sqlmodel import SQLModel, Session, create_engine, select

from app import database
from app.database import Purchase, PurchaseOrder
from app.routes import transactions


@pytest.fixture
def isolated_order_db(tmp_path, monkeypatch):
    from app import accounts

    monkeypatch.setattr(accounts, "_ACCOUNTS_FILE", tmp_path / "accounts.json")
    monkeypatch.setattr(accounts, "_cache", {
        "accounts": [{"id": "replacement-test-account"}],
        "current_id": "replacement-test-account",
    })
    engine = create_engine(
        f"sqlite:///{tmp_path / 'orders.db'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(database, "_engine", engine)
    return engine


def _seed_unresolved_order(engine, order_id="old-order"):
    with Session(engine) as session:
        session.add(PurchaseOrder(
            external_order_id=order_id,
            name="R8 Revolver | Banana Cannon (Well-Worn)",
            goods_id=921516,
            quantity=1,
            unit_price=2.27,
            total_price=2.27,
            status="payment_unconfirmed",
            source="buff",
            created_at=100.0,
            updated_at=100.0,
        ))
        session.commit()


def test_replacement_atomically_cancels_old_order_and_records_paid_purchase(isolated_order_db):
    _seed_unresolved_order(isolated_order_db)

    result = database.db_replace_cancelled_order_with_paid_purchase(
        "old-order", "new-order", 2.27, 3.75,
    )

    assert result["new_external_order_id"] == "new-order"
    assert result["total_price"] == 2.27
    with Session(isolated_order_db) as session:
        old_order = session.exec(
            select(PurchaseOrder).where(PurchaseOrder.external_order_id == "old-order")
        ).one()
        new_order = session.exec(
            select(PurchaseOrder).where(PurchaseOrder.external_order_id == "new-order")
        ).one()
        purchases = session.exec(
            select(Purchase).where(Purchase.external_order_id == "new-order")
        ).all()

    assert old_order.status == "cancelled"
    assert "new-order" in (old_order.error or "")
    assert new_order.status == "awaiting_ship"
    assert new_order.paid_at is not None
    assert new_order.source == "buff_manual_replacement"
    assert new_order.account_id == "replacement-test-account"
    assert len(purchases) == 1
    assert purchases[0].price == 2.27
    assert purchases[0].market_price == 3.75
    assert purchases[0].pending_receipt is True
    assert purchases[0].source == "manual_replacement"
    assert purchases[0].account_id == "replacement-test-account"
    assert result["market_price"] == 3.75


def test_duplicate_replacement_order_rolls_back_without_unlocking_old_order(isolated_order_db):
    _seed_unresolved_order(isolated_order_db)
    with Session(isolated_order_db) as session:
        session.add(PurchaseOrder(
            external_order_id="new-order",
            status="awaiting_ship",
            created_at=101.0,
            updated_at=101.0,
        ))
        session.commit()

    with pytest.raises(ValueError, match="新订单号已存在"):
        database.db_replace_cancelled_order_with_paid_purchase(
            "old-order", "new-order", 2.27,
        )

    with Session(isolated_order_db) as session:
        old_order = session.exec(
            select(PurchaseOrder).where(PurchaseOrder.external_order_id == "old-order")
        ).one()
        purchases = session.exec(
            select(Purchase).where(Purchase.external_order_id == "new-order")
        ).all()

    assert old_order.status == "payment_unconfirmed"
    assert purchases == []


def test_replacement_purchase_counts_toward_daily_budget(monkeypatch):
    from datetime import datetime
    from app import order_state

    now = datetime(2026, 8, 5, 20, 0, 0)
    bought_at = now.replace(hour=19).timestamp()
    monkeypatch.setattr(order_state, "db_get_purchases", lambda: [{
        "price": 2.27,
        "at": bought_at,
        "source": "manual_replacement",
        "external_order_id": "new-order",
    }])
    monkeypatch.setattr(order_state, "db_get_purchase_orders", lambda: [{
        "external_order_id": "new-order",
        "total_price": 2.27,
        "created_at": bought_at,
        "status": "awaiting_ship",
    }])

    summary = order_state.get_daily_budget_summary(10, now=now)

    assert summary["confirmed"] == 2.27
    assert summary["reserved"] == 0
    assert summary["remaining"] == 7.73


def test_current_market_price_preview_is_read_only(monkeypatch):
    order = {
        "external_order_id": "old-order",
        "name": "R8 Revolver | Banana Cannon (Well-Worn)",
        "status": "payment_unconfirmed",
    }
    monkeypatch.setattr(transactions, "db_get_purchase_orders", lambda: [order])
    monkeypatch.setattr(transactions, "is_steam_background_allowed", lambda: True)
    monkeypatch.setattr(transactions, "_fetch_steam_lowest_cny", lambda name: 3.75)

    result = transactions.api_replacement_order_current_market_price("old-order")

    assert result == {
        "ok": True,
        "name": order["name"],
        "market_price": 3.75,
    }
    assert order["status"] == "payment_unconfirmed"
