import json
from unittest.mock import MagicMock, patch


class _Context:
    def __init__(self, purchases=None):
        self.messages = []
        self.debug_messages = []
        self.status = None
        self.state = MagicMock()
        self.state.get_purchases.return_value = list(purchases or [])

    def log(self, message, level="info", **_kwargs):
        self.messages.append((level, message))

    def debug(self, message, **_kwargs):
        self.debug_messages.append(message)

    def is_stop_requested(self):
        return False

    def set_status(self, *args, **kwargs):
        self.status = (args, kwargs)


def _listing_entry():
    return {
        "it": {"appid": 730, "contextid": "2", "assetid": "asset-1"},
        "list_price": 3.8,
        "reason": "test",
        "price_cents": 330,
        "name": "SG 553 | Dragon Tech",
        "aid": "asset-1",
        "account_id": "account-a",
        "max_auto_listing_price_cny": 50.0,
    }


def test_chinese_existing_pending_listing_is_recorded_without_new_sale_event(monkeypatch):
    from app import sell_pipeline

    response = {
        "success": False,
        "message": "您已上架该物品并正等待确认。请确认或撤下现有的上架物品。",
    }
    recorded = []
    monkeypatch.setattr(
        sell_pipeline,
        "list_item",
        lambda *_args, **_kwargs: {"text": json.dumps(response, ensure_ascii=False)},
    )
    monkeypatch.setattr(sell_pipeline, "get_current_account", lambda: {"id": "account-a"})
    monkeypatch.setattr("app.account_operations.assert_current_account", lambda _account_id: None)
    monkeypatch.setattr(
        sell_pipeline,
        "_record_listing_success",
        lambda *args, **kwargs: recorded.append((args, kwargs)),
    )

    listed = sell_pipeline._submit_listings(
        _Context(), [_listing_entry()], MagicMock(), "session", 0,
    )

    assert listed == [{
        "assetid": "asset-1",
        "listing_id": "",
        "requires_confirmation": True,
        "created": False,
    }]
    assert recorded
    assert recorded[0][1]["requires_confirmation"] is True
    assert recorded[0][1]["record_event"] is False


def test_locally_listed_purchase_is_not_planned_again():
    from app import sell_pipeline

    item = {
        "name": "Gamma 2 Case",
        "market_hash_name": "Gamma 2 Case",
        "assetid": "asset-2",
        "ownership_mode": "managed",
        "can_sell": True,
    }
    purchase = {
        "assetid": "asset-2",
        "name": "Gamma 2 Case",
        "market_hash_name": "Gamma 2 Case",
        "listing": True,
        "listing_status": "pending_confirmation",
        "price": 20.0,
    }
    with patch("app.sell_pipeline.get_sell_orders_cny") as get_orders:
        result = sell_pipeline._build_listing_plan(
            ctx=_Context(),
            cfg={"pipeline": {}},
            session=MagicMock(),
            sellable=[item],
            sell_strategy=3,
            pipeline_cfg={},
            purchases_snapshot=[purchase],
            ok_listings=True,
            active_listing_ids=set(),
            listing_assetid_to_name={},
            assetid_to_name_map={},
            account_currency="CNY",
            rate_map={},
        )
    assert result == []
    get_orders.assert_not_called()


def test_confirmation_selector_retries_once_then_accepts_exact_match(monkeypatch):
    from app import sell_pipeline

    calls = []

    class Bot:
        def get_confirmations(self):
            calls.append("get")
            if len(calls) == 1:
                return True, [{"id": "trade", "nonce": "n1", "type": 2}], ""
            return True, [{
                "id": "market",
                "nonce": "n2",
                "type": 3,
                "creator_id": "listing-1",
            }], ""

        def accept_selected(self, selected):
            calls.append(("accept", [row["id"] for row in selected]))
            return True, len(selected), ""

    sleeps = []
    monkeypatch.setattr(sell_pipeline, "jittered_sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(sell_pipeline, "load_app_config_validated", lambda: {"notify": {}})
    ctx = _Context([{"_db_id": 7, "assetid": "asset-1", "listing": True}])

    sell_pipeline._auto_confirm_listings(
        ctx,
        (Bot(), []),
        [{
            "assetid": "asset-1",
            "listing_id": "listing-1",
            "requires_confirmation": True,
            "created": True,
        }],
    )

    assert calls == ["get", "get", ("accept", ["market"])]
    assert len(sleeps) == 2
    ctx.state.update_purchase_by_id.assert_called_once_with(7, {"listing_status": None})


def test_pending_confirmation_is_not_treated_as_missing_active_listing():
    from app.services.workers import _partition_listing_visibility

    listing_idx = [
        (0, {"assetid": "pending-missing", "listing_status": "pending_confirmation"}),
        (1, {"assetid": "pending-active", "listing_status": "pending_confirmation"}),
        (2, {"assetid": "regular-missing", "listing_status": None}),
    ]

    confirmed, pending, missing = _partition_listing_visibility(
        listing_idx, {"pending-active"},
    )

    assert [row[1]["assetid"] for row in confirmed] == ["pending-active"]
    assert [row[1]["assetid"] for row in pending] == ["pending-missing"]
    assert [row[1]["assetid"] for row in missing] == ["regular-missing"]

def test_pending_confirmation_timeout_notifies_once_and_keeps_state(monkeypatch):
    from app.services import workers

    updates = []
    sent = []
    monkeypatch.setattr(
        workers,
        "update_purchase_by_id",
        lambda db_id, data: updates.append((db_id, data)) or True,
    )
    row = {
        "_db_id": 7,
        "name": "Gamma 2 Case",
        "assetid": "asset-7",
        "listing": True,
        "listing_status": "pending_confirmation",
        "listed_at": 100.0,
        "last_listing_advice_key": None,
    }

    result = workers._check_pending_confirmation_timeouts(
        [row],
        {"lark_webhook": "configured"},
        now=100 + 30 * 60,
        send_fn=lambda *args: sent.append(args) or True,
    )

    assert result == {"notified": 1, "failed": 0}
    assert updates == [(7, {"last_listing_advice_key": "pending_confirmation_timeout"})]
    assert row["listing_status"] == "pending_confirmation"
    assert len(sent) == 1


def test_pending_confirmation_timeout_waits_and_retries_failed_notification(monkeypatch):
    from app.services import workers

    updates = []
    monkeypatch.setattr(
        workers,
        "update_purchase_by_id",
        lambda db_id, data: updates.append((db_id, data)) or True,
    )
    row = {
        "_db_id": 8,
        "listing_status": "pending_confirmation",
        "listed_at": 100.0,
    }

    early = workers._check_pending_confirmation_timeouts(
        [row], {}, now=100 + 29 * 60, send_fn=lambda *_args: True
    )
    failed = workers._check_pending_confirmation_timeouts(
        [row], {}, now=100 + 30 * 60, send_fn=lambda *_args: False
    )

    assert early == {"notified": 0, "failed": 0}
    assert failed == {"notified": 0, "failed": 1}
    assert updates == []


def test_pending_confirmation_timeout_ignores_already_notified_or_resolved(monkeypatch):
    from app.services import workers

    monkeypatch.setattr(
        workers,
        "update_purchase_by_id",
        lambda *_args: (_ for _ in ()).throw(AssertionError("不应重复写入")),
    )
    rows = [
        {
            "_db_id": 9,
            "listing_status": "pending_confirmation",
            "listed_at": 100.0,
            "last_listing_advice_key": "pending_confirmation_timeout",
        },
        {"_db_id": 10, "listing_status": None, "listed_at": 100.0},
    ]

    result = workers._check_pending_confirmation_timeouts(
        rows,
        {},
        now=100 + 60 * 60,
        send_fn=lambda *_args: (_ for _ in ()).throw(AssertionError("不应重复通知")),
    )

    assert result == {"notified": 0, "failed": 0}
