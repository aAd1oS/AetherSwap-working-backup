from app import receive_flow


def test_detected_offer_is_marked_awaiting_manual_accept_when_auto_accept_fails(monkeypatch):
    purchase = {
        "_db_id": 7,
        "name": "R8 Revolver | Banana Cannon (Well-Worn)",
        "goods_id": 921516,
        "at": 100.0,
        "pending_receipt": True,
        "order_status": "awaiting_ship",
        "assetid": None,
    }
    updates = []
    monkeypatch.setattr(receive_flow, "fetch_buff_steam_trade", lambda _cookies: (
        True,
        [{
            "tradeofferid": "12345",
            "created_at": 101,
            "items": [{
                "goods_id": 921516,
                "market_hash_name": purchase["name"],
                "assetid": "source-asset",
            }],
        }],
        "",
    ))
    monkeypatch.setattr(receive_flow, "accept_steam_trade_offer", lambda *_args: False)

    received = receive_flow.try_receive_once(
        get_purchases=lambda: [dict(purchase)],
        update_purchase=lambda _idx, _data: False,
        get_buff_cookies=lambda: "session=ok",
        get_steam_credentials=lambda: {
            "cookies": "sessionid=sid; steamLoginSecure=secure",
            "session_id": "sid",
        },
        update_purchase_by_id=lambda db_id, data: updates.append((db_id, data)) or True,
    )

    assert received == 0
    assert updates == [(7, {"order_status": "awaiting_trade"})]


def test_manual_accept_inventory_snapshot_backfills_assetid_without_new_trade_request():
    purchase = {
        "_db_id": 7,
        "name": "R8 Revolver | Banana Cannon (Well-Worn)",
        "goods_id": 921516,
        "at": 100.0,
        "pending_receipt": True,
        "order_status": "awaiting_trade",
        "assetid": None,
    }
    updates = []

    result = receive_flow.reconcile_pending_purchases_from_inventory(
        get_purchases=lambda: [dict(purchase)],
        inventory_items=[{
            "assetid": "new-asset-1",
            "market_hash_name": purchase["name"],
            "cooldown_at": 9999999999,
        }],
        update_purchase=lambda _idx, _data: False,
        update_purchase_by_id=lambda db_id, data: updates.append((db_id, data)) or True,
    )

    assert result == {"matched": 1, "ambiguous": 0}
    assert updates[0][0] == 7
    assert updates[0][1]["assetid"] == "new-asset-1"
    assert updates[0][1]["pending_receipt"] is False
    assert updates[0][1]["order_status"] == "trade_locked"


def test_inventory_fallback_does_not_guess_between_duplicate_untracked_items():
    purchase = {
        "_db_id": 7,
        "name": "Same Item",
        "at": 100.0,
        "pending_receipt": True,
        "assetid": None,
    }
    updates = []

    result = receive_flow.reconcile_pending_purchases_from_inventory(
        get_purchases=lambda: [dict(purchase)],
        inventory_items=[
            {"assetid": "a", "market_hash_name": "Same Item"},
            {"assetid": "b", "market_hash_name": "Same Item"},
        ],
        update_purchase=lambda _idx, data: updates.append(data) or True,
    )

    assert result == {"matched": 0, "ambiguous": 1}
    assert updates == []
