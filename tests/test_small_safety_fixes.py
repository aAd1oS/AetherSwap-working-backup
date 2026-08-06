import pytest
from sqlmodel import SQLModel, Session, create_engine, select

from app import database
from app.database import PurchaseOrder
from app.notify import send_pushplus
from app.routes import transactions
from app.services.buff_client import count_lowest_price_orders


@pytest.fixture
def isolated_order_db(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'safety-orders.db'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(database, "_engine", engine)
    return engine


def test_pushplus_uses_https(monkeypatch):
    called = {}

    class Response:
        status_code = 200

    def fake_post(url, **kwargs):
        called["url"] = url
        called["kwargs"] = kwargs
        return Response()

    monkeypatch.setattr("app.notify.requests.post", fake_post)

    assert send_pushplus("token", "title", "content") is True
    assert called["url"] == "https://www.pushplus.plus/send"


def test_lowest_price_ignores_missing_and_non_positive_orders():
    lowest, count = count_lowest_price_orders([
        {},
        {"price": None},
        {"price": "0"},
        {"price": "2.30"},
        {"price": 2.30},
        {"price": "2.50"},
    ])

    assert lowest == pytest.approx(2.30)
    assert count == 2
    assert count_lowest_price_orders([{}, {"price": 0}, {"price": "bad"}]) == (0.0, 0)


def test_conditional_order_update_does_not_revive_cancelled_order(isolated_order_db):
    with Session(isolated_order_db) as session:
        session.add(PurchaseOrder(
            external_order_id="order-1",
            status="cancelled",
            created_at=100.0,
            updated_at=100.0,
        ))
        session.commit()

    changed = database.db_update_purchase_order(
        "order-1",
        {"status": "awaiting_ship"},
        expected_statuses={"awaiting_payment"},
    )

    assert changed is False
    with Session(isolated_order_db) as session:
        order = session.exec(
            select(PurchaseOrder).where(PurchaseOrder.external_order_id == "order-1")
        ).one()
    assert order.status == "cancelled"


def test_balance_order_cannot_be_cancelled_while_auto_payment_is_running(monkeypatch):
    monkeypatch.setattr(transactions, "db_get_purchase_orders", lambda: [{
        "external_order_id": "balance-1",
        "status": "awaiting_payment",
        "source": "buff_balance",
    }])
    monkeypatch.setattr(
        transactions,
        "db_update_purchase_order",
        lambda *_args, **_kwargs: pytest.fail("must not cancel an in-flight balance payment"),
    )

    result = transactions.api_cancel_unresolved_order("balance-1")

    assert result["ok"] is False
    assert "正在自动扣款" in result["error"]

