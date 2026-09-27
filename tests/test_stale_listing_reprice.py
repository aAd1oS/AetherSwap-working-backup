from unittest.mock import MagicMock

from app.database import Purchase, _purchase_to_dict
from app.steam_delist import resolve_delisted_assetid_from_inventory
from app.sell_pipeline import _apply_stale_wait_gate


def _purchase(**overrides):
    row = {
        "_db_id": 1,
        "account_id": "account-1",
        "assetid": "old-asset",
        "name": "AK-47 | Redline",
        "listing": True,
        "listed_at": 100.0,
        "listing_price": 20.0,
        "listing_review_after": 200.0,
        "stale_listing_notified_at": None,
        "last_listing_advice_key": None,
        "pending_receipt": False,
        "order_status": "received",
        "sale_price": None,
    }
    row.update(overrides)
    return row


def _inventory(assetid, *, can_sell=True):
    return {
        "assetid": assetid,
        "name": "AK-47 | Redline",
        "market_hash_name": "AK-47 | Redline",
        "can_sell": can_sell,
        "appid": 730,
        "contextid": "2",
    }


def test_purchase_dict_preserves_listing_review_and_advice_state():
    row = _purchase_to_dict(
        Purchase(
            id=1,
            name="AK-47 | Redline",
            goods_id=7,
            price=18.0,
            at=100.0,
            listing_review_after=900.0,
            last_listing_advice_key="hold",
        )
    )

    assert row["listing_review_after"] == 900.0
    assert row["last_listing_advice_key"] == "hold"


def test_assetid_resolution_uses_only_unique_unclaimed_candidate():
    row = _purchase()
    other = _purchase(_db_id=2, assetid="claimed")
    result = resolve_delisted_assetid_from_inventory(
        row,
        [_inventory("claimed"), _inventory("new-asset")],
        [row, other],
    )
    assert result["status"] == "resolved"
    assert result["assetid"] == "new-asset"


def test_assetid_resolution_refuses_same_name_ambiguity():
    row = _purchase()
    result = resolve_delisted_assetid_from_inventory(
        row,
        [_inventory("new-a"), _inventory("new-b")],
        [row],
    )
    assert result["status"] == "ambiguous"
    assert set(result["candidate_assetids"]) == {"new-a", "new-b"}


def test_stale_review_keeps_listing_when_strategy_price_is_unchanged(monkeypatch):
    from app.services import workers

    row = _purchase()
    updates = []

    def update(db_id, data):
        assert db_id == 1
        row.update(data)
        updates.append(data.copy())
        return True

    monkeypatch.setattr(workers, "update_purchase_by_id", update)
    monkeypatch.setattr(
        "app.inventory_ownership.db_get_inventory_ownership_overrides",
        lambda: {},
    )
    delist = MagicMock()
    result = workers._review_stale_listings(
        [row],
        {"old-asset"},
        {"old-asset": "AK-47 | Redline"},
        {"notify": {}},
        MagicMock(),
        now=300.0,
        evaluate_fn=lambda *_args: {
            "status": "unchanged",
            "reason": "same price",
            "current_price": 20.0,
            "proposed_price": 20.0,
        },
        delist_fn=delist,
    )
    assert result["unchanged"] == 1
    assert result["delisted"] == 0
    assert row["listing"] is True
    assert row["listing_review_after"] == 300.0 + 10 * 60
    delist.assert_not_called()


def test_stale_review_rechecks_every_ten_minutes_and_notifies_only_on_advice_change(monkeypatch):
    from app.services import workers

    row = _purchase()

    def update(_db_id, data):
        row.update(data)
        return True

    monkeypatch.setattr(workers, "update_purchase_by_id", update)
    monkeypatch.setattr(
        "app.inventory_ownership.db_get_inventory_ownership_overrides",
        lambda: {},
    )
    notify = MagicMock(return_value=True)

    def review(now, status="hold"):
        return workers._review_stale_listings(
            [row],
            {"old-asset"},
            {"old-asset": "AK-47 | Redline"},
            {"notify": {"lark_webhook": "https://example.invalid/hook"}},
            MagicMock(),
            now=now,
            evaluate_fn=lambda *_args: {
                "status": status,
                "reason": "metrics changed but advice did not",
                "current_price": 20.0,
                "proposed_price": 20.0,
                "queue_ahead": now,
            },
            delist_fn=MagicMock(),
            notify_fn=notify,
        )

    first = review(300.0)
    second = review(900.0)
    changed = review(1500.0, status="unchanged")

    assert first["notified"] == 1
    assert second["reviewed"] == 1
    assert second["notified"] == 0
    assert changed["notified"] == 1
    assert notify.call_count == 2
    assert row["listing_review_after"] == 1500.0 + 10 * 60
    assert row["last_listing_advice_key"] == "unchanged"


def test_stale_review_delists_syncs_unique_assetid_and_requeues(monkeypatch):
    from app.services import workers

    row = _purchase()
    state = MagicMock()
    state.get_purchases.return_value = [row]

    def update(db_id, data):
        assert db_id == 1
        row.update(data)
        return True

    monkeypatch.setattr(workers, "update_purchase_by_id", update)
    monkeypatch.setattr(
        "app.inventory_ownership.db_get_inventory_ownership_overrides",
        lambda: {},
    )
    item = _inventory("new-asset")
    result = workers._review_stale_listings(
        [row],
        {"old-asset"},
        {"old-asset": "AK-47 | Redline"},
        {"notify": {}},
        state,
        now=300.0,
        evaluate_fn=lambda *_args: {
            "status": "reprice",
            "reason": "wall moved",
            "current_price": 20.0,
            "proposed_price": 19.5,
        },
        delist_fn=lambda *_args, **_kwargs: (True, "new-asset", None),
        scan_fn=lambda: (True, [item], ""),
    )
    assert result["delisted"] == 1
    assert result["failed"] == 0
    assert result["relist_items"] == [item]
    assert row["listing"] is False
    assert row["assetid"] == "new-asset"
    assert row["listing_status"] is None
    assert row["listing_price"] is None
    assert row["listing_review_after"] is None


def test_stale_review_preserves_local_listing_when_delist_is_not_explicit_success(monkeypatch):
    from app.services import workers

    row = _purchase()
    monkeypatch.setattr(
        workers,
        "update_purchase_by_id",
        lambda _db_id, data: row.update(data) or True,
    )
    monkeypatch.setattr(
        "app.inventory_ownership.db_get_inventory_ownership_overrides",
        lambda: {},
    )
    result = workers._review_stale_listings(
        [row],
        {"old-asset"},
        {"old-asset": "AK-47 | Redline"},
        {"notify": {}},
        MagicMock(),
        now=300.0,
        evaluate_fn=lambda *_args: {
            "status": "reprice",
            "current_price": 20.0,
            "proposed_price": 19.5,
        },
        delist_fn=lambda *_args, **_kwargs: (False, None, "HTTP 200 but success=false"),
    )
    assert result["failed"] == 1
    assert row["listing"] is True
    assert row["assetid"] == "old-asset"
    assert row["listing_review_after"] == 300.0 + 60 * 60


def test_staged_reprice_uses_stricter_wait_thresholds_earlier():
    at_24h = _apply_stale_wait_gate(
        20.0, 19.5, "wall moved", 200, 100,
        listing_age_hours=24, staged_mode=True,
    )
    at_48h = _apply_stale_wait_gate(
        20.0, 19.5, "wall moved", 200, 100,
        listing_age_hours=48, staged_mode=True,
    )

    assert at_24h["status"] == "hold"
    assert at_24h["stage_hours"] == 24
    assert at_24h["wait_threshold_hours"] == 72.0
    assert at_48h["status"] == "allow"
    assert at_48h["stage_hours"] == 48


def test_staged_reprice_fails_closed_without_volume_and_never_raises_price():
    missing_volume = _apply_stale_wait_gate(
        20.0, 19.5, "wall moved", 200, None,
        listing_age_hours=72, staged_mode=True,
    )
    raised_price = _apply_stale_wait_gate(
        20.0, 20.5, "wall moved", 200, 100,
        listing_age_hours=72, staged_mode=True,
    )

    assert missing_volume["status"] == "hold"
    assert "缺少可靠成交量" in missing_volume["reason"]
    assert raised_price["status"] == "hold"
    assert "不自动上调" in raised_price["reason"]
