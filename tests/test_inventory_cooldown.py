from datetime import datetime, timezone
from pathlib import Path

from app.inventory_cs2 import _parse_cooldown, _parse_wear


def test_parse_cooldown_supports_current_steam_date_tag():
    raw = (
        "This item is trade-protected and cannot be consumed, modified, "
        "or transferred until [date]1786442400[/date]"
    )

    text, timestamp = _parse_cooldown([{"value": raw}])

    assert text == raw
    assert timestamp == 1786442400
    assert datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat() == "2026-08-11T10:00:00+00:00"


def test_parse_cooldown_keeps_legacy_gmt_format_compatible():
    raw = (
        "This item is trade-protected and cannot be transferred "
        "until Aug 11, 2026 (10:00:00) GMT"
    )

    _, timestamp = _parse_cooldown([{"value": raw}])

    assert timestamp == 1786442400


def test_parse_cooldown_supports_tradable_after_without_trade_protected_text():
    raw = "Tradable After Aug 11, 2026 (10:00:00) GMT"

    text, timestamp = _parse_cooldown([{"value": raw}])

    assert text == raw
    assert timestamp == 1786442400


def test_unparsed_cooldown_is_fail_closed(monkeypatch):
    from app import inventory_cs2

    monkeypatch.setattr(inventory_cs2, "get_steam_credentials", lambda: {
        "steam_id": "111", "cookies": "sessionid=test",
    })
    monkeypatch.setattr(inventory_cs2, "create_market_session", lambda *_args: object())
    monkeypatch.setattr(inventory_cs2, "fetch_cs2_inventory", lambda *_args: {
        "assets": [{"assetid": "1", "classid": "10", "instanceid": "0", "appid": 730, "contextid": "2"}],
        "descriptions": [{
            "classid": "10", "instanceid": "0", "name": "Item",
            "market_hash_name": "Item", "marketable": 1, "tradable": 1,
            "owner_descriptions": [{"value": "This item is trade-protected and cannot be transferred until someday GMT"}],
        }],
    })

    ok, items, _error = inventory_cs2.scan_cs2_inventory()

    assert ok is True
    assert items[0]["cooldown_text"]
    assert items[0]["cooldown_at"] is None
    assert items[0]["can_trade"] is False
    assert items[0]["can_sell"] is False


def test_parse_wear_prefers_inventory_description_and_falls_back_to_market_name():
    description = {
        "descriptions": [
            {"name": "exterior_wear", "value": "Exterior: Minimal Wear"},
        ]
    }

    assert _parse_wear(description, "MP7 | Just Smile (Factory New)") == "Minimal Wear"
    assert _parse_wear({}, "MP7 | Just Smile (Factory New)") == "Factory New"


def test_inventory_enrichment_uses_current_account_local_price_and_unlock_time(monkeypatch):
    from app.routes import inventory

    items = [{
        "assetid": "asset-1",
        "name": "MP7 | Just Smile",
        "market_hash_name": "MP7 | Just Smile (Minimal Wear)",
        "cooldown_at": None,
    }]
    monkeypatch.setattr(inventory, "get_purchases", lambda: [{
        "assetid": "asset-1",
        "name": "MP7 | Just Smile (Minimal Wear)",
        "current_market_price": 9.9,
        "tradable_at": 1786442400,
    }])
    monkeypatch.setattr(inventory, "batch_fetch_prices", lambda names: {})

    inventory._enrich_inventory_with_steam_prices(items, [])

    assert items[0]["lowest_price"] == 9.9
    assert items[0]["cooldown_at"] == 1786442400
    assert items[0]["cooldown_at_iso"] == "2026-08-11T10:00:00Z"


def test_inventory_scan_uses_current_account_scoped_credentials(monkeypatch):
    from app import inventory_cs2

    captured = {}
    monkeypatch.setattr(inventory_cs2, "get_steam_credentials", lambda: {
        "steam_id": "small-account",
        "cookies": "sessionid=small",
    })
    monkeypatch.setattr(
        inventory_cs2,
        "create_market_session",
        lambda cookies, steam_id: captured.update({"cookies": cookies, "steam_id": steam_id}) or object(),
    )
    monkeypatch.setattr(
        inventory_cs2,
        "fetch_cs2_inventory",
        lambda session, steam_id: {
            "assets": [{"assetid": "1", "classid": "10", "instanceid": "0", "appid": 730, "contextid": "2"}],
            "descriptions": [{
                "classid": "10",
                "instanceid": "0",
                "name": "MP7 | Just Smile",
                "market_hash_name": "MP7 | Just Smile (Minimal Wear)",
                "marketable": 1,
                "tradable": 1,
            }],
        },
    )

    ok, items, error = inventory_cs2.scan_cs2_inventory()

    assert ok is True
    assert error == ""
    assert captured == {"cookies": "sessionid=small", "steam_id": "small-account"}
    assert items[0]["market_hash_name"] == "MP7 | Just Smile (Minimal Wear)"


def test_inventory_cooldown_is_written_back_to_matching_purchase(monkeypatch):
    from app.routes import inventory

    updates = []
    monkeypatch.setattr(
        inventory,
        "update_purchase_by_id",
        lambda db_id, data: updates.append((db_id, data)) or True,
    )
    purchase = {"_db_id": 7, "assetid": "asset-7", "tradable_at": None, "order_status": "received"}

    changed = inventory._sync_inventory_metadata(
        [{"assetid": "asset-7", "cooldown_at": 9999999999, "can_trade": False}],
        {"asset-7": purchase},
    )

    assert changed == 1
    assert updates == [(7, {"tradable_at": 9999999999.0, "order_status": "trade_locked"})]
    assert purchase["tradable_at"] == 9999999999.0


def test_unparsed_inventory_cooldown_still_marks_purchase_locked(monkeypatch):
    from app.routes import inventory

    updates = []
    monkeypatch.setattr(
        inventory,
        "update_purchase_by_id",
        lambda db_id, data: updates.append((db_id, data)) or True,
    )
    purchase = {"_db_id": 8, "assetid": "asset-8", "tradable_at": None, "order_status": "received"}

    changed = inventory._sync_inventory_metadata(
        [{"assetid": "asset-8", "cooldown_at": 0, "cooldown_text": "Trade protected", "can_trade": False}],
        {"asset-8": purchase},
    )

    assert changed == 1
    assert updates == [(8, {"order_status": "trade_locked"})]


def test_inventory_unlock_time_uses_fixed_local_format():
    script = (Path(__file__).resolve().parents[1] / "web" / "js" / "main.js").read_text(encoding="utf-8")

    assert "function formatInventoryUnlockTime(d)" in script
    assert "displayTime = formatInventoryUnlockTime(d);" in script
    assert "const fullName = (it.market_hash_name || it.name || \"\").trim();" in script
    assert 'row.querySelectorAll("td")[7]' in script


def test_inventory_listing_state_uses_exact_assetid():
    from app.routes.inventory import _annotate_inventory_listing_state

    items = [{"assetid": "listed"}, {"assetid": "personal"}]
    purchases = [{"assetid": "listed", "listing": True, "listing_status": None}]

    _annotate_inventory_listing_state(items, purchases)

    assert items[0]["listing"] is True
    assert items[1]["listing"] is False


def test_inventory_cooldown_fallback_never_spreads_to_same_name_personal_items(monkeypatch):
    from app.routes import inventory as inventory_route

    purchase = {
        "assetid": "purchased-new-asset",
        "name": "Fracture Case",
        "tradable_at": 9999999999,
        "market_price": 5.0,
    }
    items = [
        {"assetid": "old-personal-asset", "market_hash_name": "Fracture Case"},
        {"assetid": "purchased-new-asset", "market_hash_name": "Fracture Case"},
    ]
    monkeypatch.setattr(inventory_route, "get_purchases", lambda: [purchase])
    monkeypatch.setattr(inventory_route, "batch_fetch_prices", lambda _names: {})
    monkeypatch.setattr(inventory_route, "_sync_inventory_metadata", lambda *_args: 0)

    inventory_route._enrich_inventory_with_steam_prices(items, [])

    assert items[0].get("cooldown_at") is None
    assert items[0]["lowest_price"] == 5.0
    assert items[1]["cooldown_at"] == 9999999999


def test_inventory_ui_prioritizes_active_listing_state():
    script = (Path(__file__).resolve().parents[1] / "web" / "js" / "main.js").read_text(encoding="utf-8")

    assert "const isListed = Boolean(it.listing);" in script
    assert "已上架出售中" in script
    assert "市场托管中" in script
