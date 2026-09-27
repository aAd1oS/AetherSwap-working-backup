import sqlite3

import pytest


def _isolated_db(tmp_path, monkeypatch):
    from app import accounts, database

    monkeypatch.setattr(accounts, "_ACCOUNTS_FILE", tmp_path / "accounts.json")
    monkeypatch.setattr(accounts, "_cache", None)
    monkeypatch.setattr(database, "_DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(database, "_engine", None)
    account = accounts.add_account(username="test", steam_id="111")
    database.init_db()
    return account, database


def test_nonempty_assetid_is_unique_per_account(tmp_path, monkeypatch):
    account, database = _isolated_db(tmp_path, monkeypatch)
    database.db_append_purchase({"name": "one", "assetid": "123", "account_id": account["id"]})

    with pytest.raises(database.DuplicateAssetIdError, match="123"):
        database.db_append_purchase({"name": "two", "assetid": "123", "account_id": account["id"]})

    database.db_append_purchase({"name": "blank-one", "assetid": ""})
    database.db_append_purchase({"name": "blank-two", "assetid": None})
    assert len(database.db_get_purchases()) == 3


def test_conflicting_edit_keeps_original_assetid(tmp_path, monkeypatch):
    _account, database = _isolated_db(tmp_path, monkeypatch)
    database.db_append_purchase({"name": "one", "assetid": "111"})
    database.db_append_purchase({"name": "two", "assetid": "222"})
    second = database.db_get_purchases()[1]

    with pytest.raises(database.DuplicateAssetIdError, match="111"):
        database.db_update_purchase_by_id(second["_db_id"], {"assetid": "111"})

    assert database.db_get_purchases()[1]["assetid"] == "222"


def test_conflicting_legacy_migration_stops_without_deleting_rows(tmp_path, monkeypatch):
    from app import accounts, database

    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE purchase (id INTEGER PRIMARY KEY, account_id TEXT, assetid TEXT)")
        conn.execute("INSERT INTO purchase (account_id, assetid) VALUES ('a', 'same')")
        conn.execute("INSERT INTO purchase (account_id, assetid) VALUES ('a', 'same')")
    monkeypatch.setattr(accounts, "_ACCOUNTS_FILE", tmp_path / "accounts.json")
    monkeypatch.setattr(accounts, "_cache", None)
    monkeypatch.setattr(database, "_DB_PATH", db_path)
    monkeypatch.setattr(database, "_engine", None)

    with pytest.raises(RuntimeError, match="迁移已停止"):
        database.init_db()

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM purchase").fetchone()[0] == 2


def test_payment_finalization_is_atomic_and_idempotent(tmp_path, monkeypatch):
    _account, database = _isolated_db(tmp_path, monkeypatch)
    database.db_upsert_purchase_order({
        "external_order_id": "order-1",
        "name": "Item",
        "goods_id": 7,
        "quantity": 2,
        "unit_price": 3.5,
        "total_price": 7.0,
        "status": "awaiting_payment",
        "source": "buff_manual",
    })

    first = database.db_finalize_purchase_order_payment("order-1", user_confirmed=True, paid_at=100)
    second = database.db_finalize_purchase_order_payment("order-1", user_confirmed=True, paid_at=101)

    assert first["created"] == 2
    assert second["created"] == 0
    assert len(database.db_get_purchases()) == 2
    order = database.db_get_purchase_orders()[0]
    assert order["status"] == "awaiting_ship"
    assert order["paid_at"] == 100


def test_payment_without_purchase_rows_stays_platform_confirmed(tmp_path, monkeypatch):
    _account, database = _isolated_db(tmp_path, monkeypatch)
    database.db_upsert_purchase_order({
        "external_order_id": "batch-1",
        "name": "Item",
        "quantity": 2,
        "status": "awaiting_payment",
        "source": "buff_batch",
    })

    result = database.db_finalize_purchase_order_payment(
        "batch-1", user_confirmed=True, create_purchases=False,
    )

    assert result["created"] == 0
    assert database.db_get_purchase_orders()[0]["status"] == "platform_confirmed"
    assert database.db_get_purchases() == []
