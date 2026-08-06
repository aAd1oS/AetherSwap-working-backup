from pathlib import Path

import pytest
import requests

from app import pipeline_steps
from app.config_schema import DEFAULTS
from app.services import buff_balance
from app.services.buff_client import create_buff_client_from_config
from buff.buyer import (
    BuffBuyer,
    BuffOrderOutcomeUnknown,
    PAY_METHOD_ALIPAY,
    PAY_METHOD_WECHAT,
)


def _preview_response(method: dict) -> dict:
    return {"code": "OK", "data": {"pay_methods": [method]}}


def _usable_balance_method(**overrides) -> dict:
    method = {
        "value": 80,
        "name": "BUFF可用资金",
        "btn_clickable": True,
        "enough": True,
        "real_enough": True,
        "balance": "7.73",
        "free_password": True,
    }
    method.update(overrides)
    return method


def test_balance_preview_uses_dynamic_method_value(monkeypatch):
    buyer = BuffBuyer("csrf_token=test; session=test")
    monkeypatch.setattr(
        buyer,
        "_make_request",
        lambda *_args, **_kwargs: _preview_response(_usable_balance_method(value=63)),
    )

    result = buyer.preview_balance_payment(
        "csgo", 921516, "sell-order", "2.27", "76561190000000000"
    )

    assert result["usable"] is True
    assert result["pay_method"] == 63
    assert result["balance"] == pytest.approx(7.73)
    assert result["free_password"] is True
    assert result["balance_status"] == "available"


def test_balance_preview_skips_stale_zero_channel_and_uses_available_balance(monkeypatch):
    buyer = BuffBuyer("csrf_token=test; session=test")
    monkeypatch.setattr(
        buyer,
        "_make_request",
        lambda *_args, **_kwargs: {
            "code": "OK",
            "data": {
                "pay_methods": [
                    _usable_balance_method(
                        value=63,
                        btn_clickable=False,
                        enough=False,
                        real_enough=False,
                        balance="0.00",
                        error="该订单不支持支付宝余额",
                    ),
                    _usable_balance_method(value=80, balance="7.73"),
                ]
            },
        },
    )

    result = buyer.preview_balance_payment(
        "csgo", 921516, "sell-order", "2.57", "76561190000000000"
    )

    assert result["usable"] is True
    assert result["pay_method"] == 80
    assert result["balance"] == pytest.approx(7.73)
    assert result["candidate_count"] == 2
    assert any("编号=63" in summary for summary in result["candidate_summaries"])


def test_balance_preview_marks_order_specific_zero_as_untrustworthy(monkeypatch):
    buyer = BuffBuyer("csrf_token=test; session=test")
    monkeypatch.setattr(
        buyer,
        "_make_request",
        lambda *_args, **_kwargs: _preview_response(
            _usable_balance_method(
                value=63,
                btn_clickable=False,
                enough=False,
                real_enough=False,
                balance="0.00",
                error="该订单不支持支付宝余额",
            )
        ),
    )

    result = buyer.preview_balance_payment(
        "csgo", 956462, "sell-order", "2.22", "76561190000000000"
    )

    assert result["usable"] is False
    assert result["reported_balance"] == pytest.approx(0.0)
    assert result["balance_observation_trustworthy"] is False
    assert "不能代表账号可用资金" in result["balance_observation_reason"]


def test_balance_preview_reports_each_channel_when_all_are_unusable(monkeypatch):
    buyer = BuffBuyer("csrf_token=test; session=test")
    monkeypatch.setattr(
        buyer,
        "_make_request",
        lambda *_args, **_kwargs: {
            "code": "OK",
            "data": {
                "pay_methods": [
                    _usable_balance_method(value=63, balance="0.00", enough=False),
                    _usable_balance_method(value=80, balance="7.73", btn_clickable=False),
                ]
            },
        },
    )

    result = buyer.preview_balance_payment(
        "csgo", 921516, "sell-order", "2.57", "76561190000000000"
    )

    assert result["usable"] is False
    assert result["balance_status"] == "unavailable"
    assert "编号=63 余额=0.00" in result["reason"]
    assert "编号=80 余额=7.73" in result["reason"]


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"btn_clickable": False}, "按钮不可用"),
        ({"enough": False}, "余额不足"),
        ({"real_enough": False}, "实际可用余额不足"),
        ({"balance": "1.00"}, "小于订单金额"),
        ({"balance": None}, "未返回可核对的余额"),
        ({"error": "该订单不可用"}, "该订单不可用"),
    ],
)
def test_balance_preview_rejects_ambiguous_or_unusable_method(monkeypatch, overrides, reason):
    buyer = BuffBuyer("csrf_token=test; session=test")
    monkeypatch.setattr(
        buyer,
        "_make_request",
        lambda *_args, **_kwargs: _preview_response(_usable_balance_method(**overrides)),
    )

    result = buyer.preview_balance_payment(
        "csgo", 921516, "sell-order", "2.27", "76561190000000000"
    )

    assert result["usable"] is False
    assert reason in result["reason"]


def test_balance_preview_does_not_trust_method_number_without_exact_name(monkeypatch):
    buyer = BuffBuyer("csrf_token=test; session=test")
    monkeypatch.setattr(
        buyer,
        "_make_request",
        lambda *_args, **_kwargs: _preview_response(
            _usable_balance_method(value=80, name="银行卡快捷支付")
        ),
    )

    result = buyer.preview_balance_payment(
        "csgo", 921516, "sell-order", "2.27", "76561190000000000"
    )

    assert result["usable"] is False
    assert "未提供 BUFF可用资金" in result["reason"]


def test_balance_preview_keeps_reported_balance_when_order_costs_more(monkeypatch):
    buyer = BuffBuyer("csrf_token=test; session=test")
    monkeypatch.setattr(
        buyer,
        "_make_request",
        lambda *_args, **_kwargs: _preview_response(
            _usable_balance_method(balance="7.73")
        ),
    )

    result = buyer.preview_balance_payment(
        "csgo",
        921516,
        "sell-order",
        "9.50",
        "76561198000000000",
    )

    assert result["usable"] is False
    assert result["balance_status"] == "unavailable"
    assert result["reported_balance"] == pytest.approx(7.73)
    assert result["balance"] == pytest.approx(7.73)
    assert result["balance_observation_trustworthy"] is True
    assert "小于订单金额 9.50" in result["reason"]


def test_balance_lock_network_unknown_is_not_retried(monkeypatch):
    buyer = BuffBuyer("csrf_token=test; session=test")
    calls = []

    def fail_once(*_args, **_kwargs):
        calls.append("post")
        raise requests.Timeout("uncertain")

    monkeypatch.setattr(buyer, "_make_request", fail_once)

    with pytest.raises(BuffOrderOutcomeUnknown):
        buyer.lock_order_once(
            "csgo", 921516, "sell-order", "2.27", 80, "76561190000000000"
        )

    assert calls == ["post"]


def test_factory_passes_steam_id_and_preserves_balance_mode():
    client = create_buff_client_from_config(
        {"cookies": "csrf_token=test; session=test"},
        {"buff": {"pay_method": "balance"}},
        {"steam_id": "76561190000000000"},
    )

    assert client.payment_mode == "balance"
    assert client._steam_id == "76561190000000000"


def test_single_balance_preview_records_reusable_probe(monkeypatch, tmp_path):
    client = create_buff_client_from_config(
        {"cookies": "csrf_token=test; session=test"},
        {"buff": {"pay_method": "balance"}},
        {"steam_id": "76561190000000000"},
    )
    calls = []
    monkeypatch.setattr(buff_balance, "_CACHE_FILE", tmp_path / "buff_balance.json")
    monkeypatch.setattr(
        client._buyer,
        "preview_balance_payment",
        lambda *args, **kwargs: (
            calls.append((args, kwargs))
            or {"usable": True, "balance": 7.73}
        ),
    )

    result = client.preview_balance_payment_once(
        "csgo",
        921516,
        "sell-order",
        "2.57",
    )

    assert result["balance"] == pytest.approx(7.73)
    assert len(calls) == 1
    assert buff_balance.get_buff_balance_probe() == {
        "game": "csgo",
        "goods_id": 921516,
        "sell_order_id": "sell-order",
        "price": "2.57",
    }


@pytest.mark.parametrize(
    ("fallback", "expected_method"),
    [("wechat", PAY_METHOD_WECHAT), ("alipay", PAY_METHOD_ALIPAY)],
)
def test_factory_smart_balance_uses_configured_manual_fallback(fallback, expected_method):
    client = create_buff_client_from_config(
        {"cookies": "csrf_token=test; session=test"},
        {
            "buff": {
                "pay_method": "balance_first",
                "balance_fallback_method": fallback,
            }
        },
        {"steam_id": "76561190000000000"},
    )

    assert client.payment_mode == "balance_first"
    assert client.fallback_payment_mode == fallback
    assert client._buyer.pay_method == expected_method


class _SmartBalanceClient:
    payment_mode = "balance_first"
    fallback_payment_mode = "wechat"

    def __init__(self, preview):
        self.preview = preview
        self.events = []

    def preview_balance_payment(self, *_args):
        self.events.append("preview")
        return self.preview

    def lock_and_get_pay_url(self, *_args):
        self.events.append("manual_lock")
        return {
            "success": True,
            "pay_url": "https://pay.invalid/manual",
            "pay_type": self.fallback_payment_mode,
            "order_id": "manual-order",
        }


def _run_smart_decision(monkeypatch, client, logs):
    monkeypatch.setattr(
        pipeline_steps,
        "_do_wait_payment_and_append",
        lambda *_args, **_kwargs: 2.57,
    )
    config = {
        "buff": {
            "game": "csgo",
            "price_tolerance": 0.5,
            "balance_auto_pay_acknowledged": True,
        },
        "pipeline": {},
        "_strategy_runtime": {
            "buy": {
                "enabled_modules": ["action.buff_lock_pay"],
                "params": {},
            }
        },
    }
    item = {
        "goods_id": 921516,
        "name": "Test Item",
        "steam_market_name": "Test Item",
        "min_price": 2.57,
        "_buff_sell_orders": [{"id": "sell-order", "price": "2.57"}],
    }
    return pipeline_steps.lock_and_confirm_payment(
        client,
        item,
        config,
        target_balance=10.0,
        acc=0.0,
        set_pending_payment=lambda *_args: None,
        wait_payment_confirm=lambda *_args, **_kwargs: True,
        confirm_payment=lambda *_args: None,
        is_stop_requested=lambda: False,
        append_purchase=lambda *_args: None,
        log_fn=lambda message, level: logs.append((message, level)),
    )


def test_smart_balance_uses_balance_when_preview_covers_order(monkeypatch):
    preview = {
        "usable": True,
        "balance_status": "available",
        "pay_method": 80,
        "balance": 7.73,
        "free_password": True,
    }
    client = _SmartBalanceClient(preview)
    logs = []
    captured = {}

    def fake_balance_purchase(*_args, **kwargs):
        captured["preview"] = kwargs.get("preview")
        client.events.append("balance_pay")
        return 2.57

    monkeypatch.setattr(pipeline_steps, "_execute_balance_purchase", fake_balance_purchase)

    result = _run_smart_decision(monkeypatch, client, logs)

    assert result == pytest.approx(2.57)
    assert client.events == ["preview", "balance_pay"]
    assert captured["preview"] is preview
    assert any("可覆盖本笔 2.57 元" in message for message, _ in logs)


def test_smart_balance_falls_back_to_manual_when_balance_is_explicitly_unavailable(monkeypatch):
    client = _SmartBalanceClient({
        "usable": False,
        "balance_status": "unavailable",
        "reason": "通道余额 1.00 小于订单金额 2.57",
    })
    logs = []

    result = _run_smart_decision(monkeypatch, client, logs)

    assert result == pytest.approx(2.57)
    assert client.events == ["preview", "manual_lock"]
    assert any("整笔改用微信手动支付" in message for message, _ in logs)
    assert any("不拆分付款" in message for message, _ in logs)


def test_smart_balance_does_not_fallback_when_preview_is_indeterminate(monkeypatch):
    client = _SmartBalanceClient({
        "usable": False,
        "balance_status": "indeterminate",
        "reason": "购买预览返回 TEMPORARY_ERROR",
    })
    logs = []

    result = _run_smart_decision(monkeypatch, client, logs)

    assert result is pipeline_steps.SKIP_BALANCE_UNAVAILABLE
    assert client.events == ["preview"]
    assert any("不会自动改用其他方式" in message for message, _ in logs)


class _BalanceClient:
    payment_mode = "balance"

    def __init__(self, *, pay_result=None, order_info=None, lock_error=None, preview=None):
        self.pay_result = pay_result or {"code": "OK", "data": {"auto_pay": True}}
        self.order_info = order_info or {
            "code": "OK",
            "data": {"items": [{"state": "PAYING", "state_text": "等待付款"}]},
        }
        self.lock_error = lock_error
        self.preview = preview or {
            "usable": True,
            "pay_method": 80,
            "balance": 7.73,
            "free_password": True,
        }
        self.events = []
        self.pay_calls = 0

    def preview_balance_payment(self, *_args):
        self.events.append("preview")
        return self.preview

    def lock_balance_order_once(self, *_args):
        self.events.append("lock")
        if self.lock_error:
            raise self.lock_error
        return {"success": True, "order_id": "bill-1"}

    def pay_bill_order_once(self, _order_id):
        self.events.append("pay")
        self.pay_calls += 1
        if isinstance(self.pay_result, Exception):
            raise self.pay_result
        return self.pay_result

    def get_bill_order_info_once(self, _order_id):
        self.events.append("query")
        return self.order_info

    def ask_seller_to_send(self, *_args):
        self.events.append("ask")
        return True


def _run_balance_helper(monkeypatch, client):
    upserts = []
    updates = []
    purchases = []

    def upsert(order):
        upserts.append(dict(order))
        client.events.append(f"persist:{order['status']}")
        return order

    monkeypatch.setattr(pipeline_steps, "db_upsert_purchase_order", upsert)
    monkeypatch.setattr(
        pipeline_steps,
        "db_update_purchase_order",
        lambda order_id, data, expected_statuses=None: updates.append(
            (order_id, dict(data), expected_statuses)
        ) or True,
    )
    result = pipeline_steps._execute_balance_purchase(
        client,
        {"name": "Test Item", "steam_market_name": "Test Item"},
        {"buff": {"balance_auto_pay_acknowledged": True}},
        2.27,
        921516,
        {"id": "sell-order", "price": "2.27"},
        "csgo",
        lambda purchase: purchases.append(dict(purchase)),
        lambda *_args: None,
        market_price=3.75,
    )
    return result, upserts, updates, purchases


def test_balance_payment_persists_order_before_single_payment(monkeypatch):
    client = _BalanceClient()

    result, upserts, updates, purchases = _run_balance_helper(monkeypatch, client)

    assert result == pytest.approx(2.27)
    assert client.pay_calls == 1
    assert client.events.index("persist:awaiting_payment") < client.events.index("pay")
    assert upserts[0]["external_order_id"] == "bill-1"
    assert upserts[0]["source"] == "buff_balance"
    assert updates[-1][1]["status"] == "awaiting_ship"
    assert updates[-1][2] == {"awaiting_payment"}
    assert len(purchases) == 1
    assert purchases[0]["price"] == pytest.approx(2.27)
    assert purchases[0]["market_price"] == pytest.approx(3.75)
    assert purchases[0]["source"] == "auto_balance"
    assert purchases[0]["order_status"] == "awaiting_ship"


def test_balance_payment_timeout_blocks_without_purchase(monkeypatch):
    client = _BalanceClient(pay_result=requests.Timeout("unknown"))

    result, _upserts, updates, purchases = _run_balance_helper(monkeypatch, client)

    assert result is pipeline_steps.PAYMENT_REVIEW_REQUIRED
    assert client.pay_calls == 1
    assert purchases == []
    assert updates[-1][1]["status"] == "payment_unconfirmed"
    assert "不会自动重试" in updates[-1][1]["error"]


def test_balance_lock_unknown_creates_local_blocker(monkeypatch):
    client = _BalanceClient(lock_error=BuffOrderOutcomeUnknown("unknown"))

    result, upserts, _updates, purchases = _run_balance_helper(monkeypatch, client)

    assert result is pipeline_steps.PAYMENT_REVIEW_REQUIRED
    assert client.pay_calls == 0
    assert purchases == []
    assert upserts[0]["external_order_id"].startswith("buff-unknown-")
    assert upserts[0]["status"] == "payment_unconfirmed"


def test_balance_preview_unavailable_never_locks(monkeypatch):
    client = _BalanceClient(preview={"usable": False, "reason": "余额不足"})

    result, upserts, _updates, purchases = _run_balance_helper(monkeypatch, client)

    assert result is pipeline_steps.SKIP_BALANCE_UNAVAILABLE
    assert client.events == ["preview"]
    assert upserts == []
    assert purchases == []


def test_order_query_can_confirm_payment_after_page_pay_error(monkeypatch):
    client = _BalanceClient(
        pay_result={"code": "Error", "error": "temporary"},
        order_info={
            "code": "OK",
            "data": {"items": [{"state": "PAYING", "state_text": "等待你发起报价"}]},
        },
    )

    result, _upserts, updates, purchases = _run_balance_helper(monkeypatch, client)

    assert result == pytest.approx(2.27)
    assert client.pay_calls == 1
    assert updates[-1][1]["status"] == "awaiting_ship"
    assert len(purchases) == 1


def test_balance_mode_defaults_to_unacknowledged_and_ui_exposes_opt_in():
    assert DEFAULTS["buff"]["balance_auto_pay_acknowledged"] is False
    root = Path(__file__).resolve().parent.parent
    html = (root / "web" / "index.html").read_text(encoding="utf-8")
    js = (root / "web" / "js" / "settings.js").read_text(encoding="utf-8")
    assert '<option value="balance">' in html
    assert '<option value="balance_first">' in html
    assert 'id="cfg-balance-fallback-method"' in html
    assert 'id="cfg-balance-auto-pay-acknowledged"' in html
    assert "balance_fallback_method" in js
    assert "balance_auto_pay_acknowledged" in js
