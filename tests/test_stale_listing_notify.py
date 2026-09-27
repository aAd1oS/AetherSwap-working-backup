def _purchase(**overrides):
    row = {
        "_db_id": 1,
        "assetid": "asset-1",
        "name": "Test Item",
        "listing": True,
        "listed_at": 100.0,
        "listing_price": 12.34,
        "stale_listing_notified_at": None,
        "pending_receipt": False,
        "order_status": "received",
        "sale_price": None,
    }
    row.update(overrides)
    return row


def test_old_active_listing_starts_clock_without_immediate_alert(monkeypatch):
    from app.services import workers

    updates = []
    monkeypatch.setattr(workers, "update_purchase_by_id", lambda db_id, data: updates.append((db_id, data)) or True)
    monkeypatch.setattr("app.inventory_ownership.db_get_inventory_ownership_overrides", lambda: {})
    sent = []
    result = workers._check_stale_listing_notifications(
        [_purchase(listed_at=None)], {"asset-1"}, {"lark_webhook": "https://example.invalid"},
        now=1000, send_fn=lambda *args: sent.append(args) or True,
    )
    assert result == {"started": 1, "notified": 0, "failed": 0}
    assert updates == [(1, {"listed_at": 1000.0})]
    assert sent == []


def test_managed_listing_notifies_once_at_24_hour_boundary(monkeypatch):
    from app.services import workers

    updates = []
    monkeypatch.setattr(workers, "update_purchase_by_id", lambda db_id, data: updates.append((db_id, data)) or True)
    monkeypatch.setattr("app.inventory_ownership.db_get_inventory_ownership_overrides", lambda: {})
    sent = []
    now = 100 + 24 * 60 * 60
    result = workers._check_stale_listing_notifications(
        [_purchase()], {"asset-1"}, {"lark_webhook": "https://open.larksuite.com/open-apis/bot/v2/hook/test"},
        now=now, send_fn=lambda *args: sent.append(args) or True,
    )
    assert result["notified"] == 1
    assert updates[-1] == (1, {"stale_listing_notified_at": float(now)})
    assert len(sent) == 1


def test_notification_failure_retries_later(monkeypatch):
    from app.services import workers

    monkeypatch.setattr(workers, "update_purchase_by_id", lambda *_args: True)
    monkeypatch.setattr("app.inventory_ownership.db_get_inventory_ownership_overrides", lambda: {})
    result = workers._check_stale_listing_notifications(
        [_purchase()], {"asset-1"}, {"lark_webhook": "configured"},
        now=100 + 24 * 60 * 60, send_fn=lambda *_args: False,
    )
    assert result["failed"] == 1
    assert result["notified"] == 0


def test_personal_override_never_notifies(monkeypatch):
    from app.services import workers

    monkeypatch.setattr(workers, "update_purchase_by_id", lambda *_args: True)
    monkeypatch.setattr("app.inventory_ownership.db_get_inventory_ownership_overrides", lambda: {"asset-1": "personal"})
    sent = []
    result = workers._check_stale_listing_notifications(
        [_purchase()], {"asset-1"}, {"lark_webhook": "configured"},
        now=100 + 48 * 60 * 60, send_fn=lambda *args: sent.append(args) or True,
    )
    assert result["notified"] == 0
    assert sent == []


def test_non_active_or_already_notified_listing_is_ignored(monkeypatch):
    from app.services import workers

    monkeypatch.setattr(workers, "update_purchase_by_id", lambda *_args: True)
    monkeypatch.setattr("app.inventory_ownership.db_get_inventory_ownership_overrides", lambda: {})
    sent = []
    row = _purchase(stale_listing_notified_at=500)
    result = workers._check_stale_listing_notifications(
        [row], {"asset-1"}, {"lark_webhook": "configured"},
        now=100 + 48 * 60 * 60, send_fn=lambda *args: sent.append(args) or True,
    )
    assert result["notified"] == 0
    result = workers._check_stale_listing_notifications(
        [_purchase()], set(), {"lark_webhook": "configured"},
        now=100 + 48 * 60 * 60, send_fn=lambda *args: sent.append(args) or True,
    )
    assert result["notified"] == 0
    assert sent == []
