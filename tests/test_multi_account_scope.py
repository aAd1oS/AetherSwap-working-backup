import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _isolated_store(tmp_path, monkeypatch):
    from app import accounts, database
    import config

    monkeypatch.setattr(accounts, "_ACCOUNTS_FILE", tmp_path / "accounts.json")
    monkeypatch.setattr(accounts, "_cache", None)
    monkeypatch.setattr(database, "_DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(database, "_engine", None)
    monkeypatch.setattr(config, "get_steam", lambda: {
        "cookies": "sessionid=a; steamLoginSecure=111%7C%7Ctoken-a",
        "session_id": "a",
        "steam_id": "111",
    })
    monkeypatch.setattr(config, "load_app_config", lambda: {
        "steam_guard": {"shared_secret": "shared-a"},
        "steam_confirm": {
            "identity_secret": "identity-a",
            "device_id": "android:a",
            "enabled": True,
        },
    })
    return accounts, database


def test_account_runtime_never_reuses_another_accounts_secrets(tmp_path, monkeypatch):
    accounts, database = _isolated_store(tmp_path, monkeypatch)
    from app.account_scope import (
        ensure_account_runtime,
        get_account_steam_credentials,
        overlay_account_config,
        update_account_steam_credentials,
    )

    account_a = accounts.add_account(username="a", steam_id="111")
    database.init_db()
    runtime_a = ensure_account_runtime(account_a["id"])
    assert runtime_a["steam_id"] == "111"
    assert runtime_a["shared_secret"] == "shared-a"
    assert "steamLoginSecure=111" in runtime_a["cookies"]

    account_b = accounts.add_account(username="b", steam_id="222")
    accounts.set_current(account_b["id"])
    runtime_b = ensure_account_runtime(account_b["id"])
    assert runtime_b["steam_id"] == "222"
    assert runtime_b["cookies"] == ""
    assert runtime_b["shared_secret"] == ""
    assert get_account_steam_credentials()["cookies"] == ""
    assert overlay_account_config({"steam_guard": {"shared_secret": "legacy"}})["steam_guard"]["shared_secret"] == ""

    update_account_steam_credentials(
        "sessionid=b; steamLoginSecure=222%7C%7Ctoken-b", "b", "222"
    )
    assert get_account_steam_credentials()["steam_id"] == "222"
    accounts.set_current(account_a["id"])
    assert get_account_steam_credentials()["steam_id"] == "111"


def test_legacy_cookie_is_assigned_to_matching_account_not_current(tmp_path, monkeypatch):
    accounts, database = _isolated_store(tmp_path, monkeypatch)
    from app.account_scope import ensure_account_runtime

    current = accounts.add_account(username="current", steam_id="222")
    matching = accounts.add_account(username="cookie-owner", steam_id="111")
    accounts.set_current(current["id"])
    database.init_db()

    current_runtime = ensure_account_runtime(current["id"])
    matching_runtime = ensure_account_runtime(matching["id"])
    assert current_runtime["cookies"] == ""
    assert current_runtime["shared_secret"] == "shared-a"
    assert "steamLoginSecure=111" in matching_runtime["cookies"]
    assert matching_runtime["shared_secret"] == ""


def test_transactions_are_filtered_and_updated_by_current_account(tmp_path, monkeypatch):
    accounts, database = _isolated_store(tmp_path, monkeypatch)
    account_a = accounts.add_account(username="a", steam_id="111")
    database.init_db()
    account_b = accounts.add_account(username="b", steam_id="222")

    accounts.set_current(account_a["id"])
    database.db_append_purchase({"name": "item-a", "price": 1, "at": 1})
    purchase_a = database.db_get_purchases()[0]
    database.db_upsert_purchase_order({"external_order_id": "order-a", "name": "item-a"})
    database.db_append_sale({"name": "sold-a", "price": 2, "at": 2})

    accounts.set_current(account_b["id"])
    database.db_append_purchase({"name": "item-b", "price": 3, "at": 3})
    assert [row["name"] for row in database.db_get_purchases()] == ["item-b"]
    assert database.db_get_purchase_orders() == []
    assert database.db_get_sales() == []
    assert database.db_update_purchase_by_id(purchase_a["_db_id"], {"name": "wrong"}) is False

    accounts.set_current(account_a["id"])
    assert [row["name"] for row in database.db_get_purchases()] == ["item-a"]
    assert database.db_get_purchase_orders()[0]["external_order_id"] == "order-a"
    assert database.db_get_sales()[0]["name"] == "sold-a"
    assert database.db_update_purchase_by_id(purchase_a["_db_id"], {"name": "item-a-updated"}) is True


def test_legacy_rows_are_assigned_to_current_account(tmp_path, monkeypatch):
    accounts, database = _isolated_store(tmp_path, monkeypatch)
    account = accounts.add_account(username="legacy", steam_id="111")
    database.init_db()
    with database.get_engine().begin() as conn:
        from sqlalchemy import text
        conn.execute(text(
            "INSERT INTO purchase (name, goods_id, price, at, source, account_id) "
            "VALUES ('legacy-item', 1, 1.0, 1.0, 'legacy', '')"
        ))
    database.init_db()
    rows = database.db_get_purchases()
    assert len(rows) == 1
    assert rows[0]["account_id"] == account["id"]


def test_switch_resets_account_view_and_loads_target_runtime(tmp_path, monkeypatch):
    accounts_store, database = _isolated_store(tmp_path, monkeypatch)
    from app.account_scope import ensure_account_runtime
    from app.routes import accounts as account_routes
    from app import pipeline
    from app.state import get_inventory, set_inventory, set_pending_payment

    account_a = accounts_store.add_account(username="a", steam_id="111")
    database.init_db()
    ensure_account_runtime(account_a["id"])
    account_b = accounts_store.add_account(username="b", steam_id="222")
    ensure_account_runtime(account_b["id"])
    set_inventory([{"assetid": "old-account-item"}])
    set_pending_payment(None)
    monkeypatch.setattr(pipeline, "is_pipeline_running", lambda: False)

    result = account_routes._activate_account(account_b["id"])
    assert result["ok"] is True
    assert accounts_store.get_current_id() == account_b["id"]
    assert get_inventory() == []
    assert result["runtime"]["has_cookie"] is False


def test_inventory_cache_is_hidden_after_account_changes(tmp_path, monkeypatch):
    accounts_store, _database = _isolated_store(tmp_path, monkeypatch)
    from app.state import State

    account_a = accounts_store.add_account(username="a", steam_id="111")
    account_b = accounts_store.add_account(username="b", steam_id="222")
    accounts_store.set_current(account_a["id"])
    state = State()
    state.set_inventory([{"assetid": "account-a-item"}])

    accounts_store.set_current(account_b["id"])

    assert state.get_inventory() == []


def test_switch_is_blocked_while_pipeline_runs(tmp_path, monkeypatch):
    accounts_store, database = _isolated_store(tmp_path, monkeypatch)
    from app.account_scope import ensure_account_runtime
    from app.routes import accounts as account_routes
    from app import pipeline

    account_a = accounts_store.add_account(username="a", steam_id="111")
    database.init_db()
    ensure_account_runtime(account_a["id"])
    account_b = accounts_store.add_account(username="b", steam_id="222")
    monkeypatch.setattr(pipeline, "is_pipeline_running", lambda: True)

    result = account_routes._activate_account(account_b["id"])
    assert result["ok"] is False
    assert accounts_store.get_current_id() == account_a["id"]
    assert "停止任务" in result["error"]


def test_identity_guard_rejects_missing_or_mismatched_cookie(tmp_path, monkeypatch):
    accounts_store, database = _isolated_store(tmp_path, monkeypatch)
    from app.account_scope import (
        ensure_account_runtime,
        update_account_steam_credentials,
        validate_current_account_identity,
    )

    account = accounts_store.add_account(username="b", steam_id="222")
    database.init_db()
    # Consume legacy migration under a separate matching account first.
    legacy = accounts_store.add_account(username="legacy", steam_id="111")
    accounts_store.set_current(legacy["id"])
    ensure_account_runtime(legacy["id"])
    accounts_store.set_current(account["id"])
    ensure_account_runtime(account["id"])
    assert validate_current_account_identity()[0] is False

    update_account_steam_credentials(
        "sessionid=x; steamLoginSecure=333%7C%7Cwrong", "x", "333", account_id=account["id"]
    )
    accounts_store.update_account(account["id"], steam_id="222")
    ok, error = validate_current_account_identity()
    assert ok is False
    assert "不一致" in error


def test_account_ui_exposes_scoped_runtime_without_secret_values():
    root = Path(__file__).resolve().parent.parent
    accounts_js = (root / "web" / "js" / "accounts.js").read_text(encoding="utf-8")
    settings_js = (root / "web" / "js" / "settings.js").read_text(encoding="utf-8")
    html = (root / "web" / "index.html").read_text(encoding="utf-8")
    assert "runtime.has_cookie" in accounts_js
    assert "window.location.reload()" in accounts_js
    assert "cfg-steam-account-label" in settings_js
    assert "cfg-steam-account-label" in html


def test_auto_sell_is_account_scoped_and_defaults_off(tmp_path, monkeypatch):
    accounts, database = _isolated_store(tmp_path, monkeypatch)
    from app.account_scope import ensure_account_runtime, set_account_auto_sell_enabled

    account_a = accounts.add_account(username="a", steam_id="111")
    database.init_db()
    account_b = accounts.add_account(username="b", steam_id="222")
    ensure_account_runtime(account_b["id"])

    assert database.db_get_account_runtime(account_b["id"])["auto_sell_enabled"] is False
    set_account_auto_sell_enabled(account_a["id"], True)
    assert database.db_get_account_runtime(account_a["id"])["auto_sell_enabled"] is True
    assert database.db_get_account_runtime(account_b["id"])["auto_sell_enabled"] is False


def test_inventory_ownership_override_is_account_scoped(tmp_path, monkeypatch):
    accounts, database = _isolated_store(tmp_path, monkeypatch)
    from app.inventory_ownership import annotate_inventory_ownership

    account_a = accounts.add_account(username="a", steam_id="111")
    database.init_db()
    account_b = accounts.add_account(username="b", steam_id="222")

    accounts.set_current(account_a["id"])
    database.db_set_inventory_ownership_override("asset-1", "managed", "AWP | Worm God")
    items_a = [{"assetid": "asset-1"}, {"assetid": "asset-2"}]
    annotate_inventory_ownership(items_a, [])
    assert items_a[0]["ownership_mode"] == "managed"
    assert items_a[0]["ownership_source"] == "manual"
    assert items_a[1]["ownership_mode"] == "personal"

    accounts.set_current(account_b["id"])
    items_b = [{"assetid": "asset-1"}]
    annotate_inventory_ownership(items_b, [])
    assert items_b[0]["ownership_mode"] == "personal"

    accounts.set_current(account_a["id"])
    database.db_set_inventory_ownership_override("asset-1", "auto")
    items_auto = [{"assetid": "asset-1"}]
    annotate_inventory_ownership(items_auto, [{"assetid": "asset-1"}])
    assert items_auto[0]["ownership_mode"] == "managed"
    assert items_auto[0]["ownership_source"] == "purchase"
