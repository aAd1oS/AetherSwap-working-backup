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


def test_auto_receive_binds_only_asset_added_after_trade(monkeypatch):
    purchase = {
        "_db_id": 7,
        "name": "Recoil Case",
        "goods_id": 900464,
        "at": 100.0,
        "pending_receipt": True,
        "order_status": "awaiting_ship",
        "assetid": None,
    }
    updates = []
    snapshots = iter([
        (True, [{"assetid": "old-personal", "market_hash_name": "Recoil Case"}], ""),
        (True, [
            {"assetid": "old-personal", "market_hash_name": "Recoil Case"},
            {
                "assetid": "new-traded",
                "market_hash_name": "Recoil Case",
                "cooldown_at": 9999999999,
            },
        ], ""),
    ])
    monkeypatch.setattr(receive_flow, "fetch_buff_steam_trade", lambda _cookies: (
        True,
        [{
            "tradeofferid": "12345",
            "created_at": 101,
            "items": [{
                "goods_id": 900464,
                "market_hash_name": "Recoil Case",
                "assetid": "seller-side-asset",
            }],
        }],
        "",
    ))
    monkeypatch.setattr(receive_flow, "accept_steam_trade_offer", lambda *_args: True)
    monkeypatch.setattr(receive_flow, "jittered_sleep", lambda *_args: None)

    received = receive_flow.try_receive_once(
        get_purchases=lambda: [dict(purchase)],
        update_purchase=lambda *_args: False,
        get_buff_cookies=lambda: "session=ok",
        get_steam_credentials=lambda: {
            "cookies": "sessionid=sid; steamLoginSecure=secure",
            "session_id": "sid",
        },
        scan_inventory=lambda: next(snapshots),
        update_purchase_by_id=lambda db_id, data: updates.append((db_id, data)) or True,
    )

    assert received == 1
    assert updates[0] == (7, {"order_status": "awaiting_trade"})
    assert updates[1][0] == 7
    assert updates[1][1]["assetid"] == "new-traded"
    assert updates[1][1]["order_status"] == "trade_locked"
    assert all(update[1].get("assetid") != "old-personal" for update in updates)
    assert all(update[1].get("assetid") != "seller-side-asset" for update in updates)


def test_auto_receive_does_not_guess_when_new_inventory_item_is_not_visible(monkeypatch):
    purchase = {
        "_db_id": 8,
        "name": "Fracture Case",
        "goods_id": 781534,
        "at": 100.0,
        "pending_receipt": True,
        "order_status": "awaiting_ship",
        "assetid": None,
    }
    updates = []
    old_inventory = [{"assetid": "old-personal", "market_hash_name": "Fracture Case"}]
    snapshots = iter([(True, old_inventory, ""), (True, old_inventory, "")])
    monkeypatch.setattr(receive_flow, "fetch_buff_steam_trade", lambda _cookies: (
        True,
        [{
            "tradeofferid": "12345",
            "created_at": 101,
            "items": [{
                "goods_id": 781534,
                "market_hash_name": "Fracture Case",
                "assetid": "seller-side-asset",
            }],
        }],
        "",
    ))
    monkeypatch.setattr(receive_flow, "accept_steam_trade_offer", lambda *_args: True)
    monkeypatch.setattr(receive_flow, "jittered_sleep", lambda *_args: None)

    received = receive_flow.try_receive_once(
        get_purchases=lambda: [dict(purchase)],
        update_purchase=lambda *_args: False,
        get_buff_cookies=lambda: "session=ok",
        get_steam_credentials=lambda: {
            "cookies": "sessionid=sid; steamLoginSecure=secure",
            "session_id": "sid",
        },
        scan_inventory=lambda: next(snapshots),
        update_purchase_by_id=lambda db_id, data: updates.append((db_id, data)) or True,
    )

    assert received == 1
    assert updates == [(8, {"order_status": "awaiting_trade"})]


def test_pending_record_with_exact_asset_and_name_becomes_received():
    purchase = {
        "_db_id": 9,
        "name": "SG 553 | Dragon Tech (Field-Tested)",
        "pending_receipt": True,
        "order_status": "awaiting_trade",
        "assetid": "new-asset",
    }
    updates = []

    result = receive_flow.reconcile_pending_purchases_from_inventory(
        get_purchases=lambda: [dict(purchase)],
        inventory_items=[{
            "assetid": "new-asset",
            "market_hash_name": purchase["name"],
            "cooldown_at": 9999999999,
        }],
        update_purchase=lambda *_args: False,
        update_purchase_by_id=lambda db_id, data: updates.append((db_id, data)) or True,
    )

    assert result == {"matched": 1, "ambiguous": 0}
    assert updates[0][0] == 9
    assert updates[0][1]["pending_receipt"] is False
    assert updates[0][1]["order_status"] == "trade_locked"


def test_pending_record_does_not_claim_asset_with_different_name():
    purchase = {
        "_db_id": 10,
        "name": "Paris 2023 Anubis Souvenir Package",
        "pending_receipt": True,
        "order_status": "awaiting_trade",
        "assetid": "shared-asset",
    }
    updates = []

    result = receive_flow.reconcile_pending_purchases_from_inventory(
        get_purchases=lambda: [dict(purchase)],
        inventory_items=[{
            "assetid": "shared-asset",
            "market_hash_name": "Paris 2023 Mirage Souvenir Package",
        }],
        update_purchase=lambda *_args: False,
        update_purchase_by_id=lambda db_id, data: updates.append((db_id, data)) or True,
    )

    assert result == {"matched": 0, "ambiguous": 0}
    assert updates == []

def _pending_purchase(db_id, goods_id, name, **overrides):
    row = {
        "_db_id": db_id,
        "goods_id": goods_id,
        "name": name,
        "pending_receipt": True,
        "order_status": "awaiting_ship",
        "assetid": None,
    }
    row.update(overrides)
    return row


def test_trade_offer_plan_accepts_complete_unique_bijection():
    purchases = [
        _pending_purchase(1, 100, "One"),
        _pending_purchase(2, 200, "Two"),
    ]
    pairs = receive_flow._plan_trade_offer_matches(
        [{"goods_id": 100}, {"goods_id": 200}], purchases
    )

    assert [pair[0]["_db_id"] for pair in pairs] == [1, 2]


def test_trade_offer_plan_rejects_extra_or_duplicate_offer_items():
    purchase = _pending_purchase(1, 100, "One")

    assert receive_flow._plan_trade_offer_matches(
        [{"goods_id": 100}, {"goods_id": 999}], [purchase]
    ) is None
    assert receive_flow._plan_trade_offer_matches(
        [{"goods_id": 100}, {"goods_id": 100}], [purchase]
    ) is None


def test_trade_offer_plan_rejects_ambiguous_or_fuzzy_name_matches():
    ambiguous = [
        _pending_purchase(1, 100, "Same"),
        _pending_purchase(2, 100, "Same"),
    ]

    assert receive_flow._plan_trade_offer_matches([{"goods_id": 100}], ambiguous) is None
    assert receive_flow._plan_trade_offer_matches(
        [{"goods_id": None, "market_hash_name": "Same"}], ambiguous
    ) is None
    assert receive_flow._plan_trade_offer_matches(
        [{"goods_id": None, "market_hash_name": "Same"}],
        [_pending_purchase(3, None, "Same Item")],
    ) is None


def test_trade_offer_plan_rejects_wrong_account_or_invalid_state():
    assert receive_flow._plan_trade_offer_matches(
        [{"goods_id": 100}],
        [_pending_purchase(1, 100, "One", account_id="other")],
        expected_account_id="current",
    ) is None
    assert receive_flow._plan_trade_offer_matches(
        [{"goods_id": 100}],
        [_pending_purchase(1, 100, "One", order_status="received")],
    ) is None


def test_partial_trade_offer_never_updates_state_or_calls_steam(monkeypatch):
    purchase = _pending_purchase(1, 100, "One")
    updates = []
    accepted = []
    monkeypatch.setattr(receive_flow, "fetch_buff_steam_trade", lambda _cookies: (
        True,
        [{
            "tradeofferid": "offer",
            "created_at": 101,
            "items": [{"goods_id": 100}, {"goods_id": 999}],
        }],
        "",
    ))
    monkeypatch.setattr(
        receive_flow,
        "accept_steam_trade_offer",
        lambda *_args: accepted.append(True) or True,
    )

    received = receive_flow.try_receive_once(
        get_purchases=lambda: [dict(purchase)],
        update_purchase=lambda *_args: False,
        get_buff_cookies=lambda: "session=ok",
        get_steam_credentials=lambda: {
            "cookies": "sessionid=sid; steamLoginSecure=secure",
            "session_id": "sid",
        },
        update_purchase_by_id=lambda db_id, data: updates.append((db_id, data)) or True,
    )

    assert received == 0
    assert updates == [(1, {"order_status": "needs_review"})]
    assert accepted == []


def test_two_offers_cannot_claim_the_same_purchase_in_one_run(monkeypatch):
    purchase = _pending_purchase(1, 100, "One")
    updates = []
    accepted = []
    monkeypatch.setattr(receive_flow, "fetch_buff_steam_trade", lambda _cookies: (
        True,
        [
            {"tradeofferid": "offer-1", "created_at": 101, "items": [{"goods_id": 100}]},
            {"tradeofferid": "offer-2", "created_at": 102, "items": [{"goods_id": 100}]},
        ],
        "",
    ))
    monkeypatch.setattr(
        receive_flow,
        "accept_steam_trade_offer",
        lambda offer_id, _cookies: accepted.append(offer_id) or True,
    )

    received = receive_flow.try_receive_once(
        get_purchases=lambda: [dict(purchase)],
        update_purchase=lambda *_args: False,
        get_buff_cookies=lambda: "session=ok",
        get_steam_credentials=lambda: {
            "cookies": "sessionid=sid; steamLoginSecure=secure",
            "session_id": "sid",
        },
        update_purchase_by_id=lambda db_id, data: updates.append((db_id, data)) or True,
    )

    assert received == 1
    assert accepted == ["offer-1"]
    assert updates == [(1, {"order_status": "awaiting_trade"})]
