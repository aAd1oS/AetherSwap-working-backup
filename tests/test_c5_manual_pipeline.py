from app import pipeline_steps
from app.services.c5_client import C5ExecutableQuote


class _NoC5OrBuffOrderClient:
    payment_mode = "wechat"

    def lock_manual_order_once(self, *_args, **_kwargs):
        raise AssertionError("C5 manual recommendation must not lock a BUFF order")


def test_c5_manual_recommendation_returns_skip_before_buff_lock(monkeypatch):
    quote = C5ExecutableQuote(
        market_hash_name="Recoil Case",
        product_id="c5-product",
        price=9.0,
        delivery=1,
        asset_id="asset",
        reference_link="https://www.c5game.com/csgo/recoil",
        listing_count=3,
    )
    monkeypatch.setattr(pipeline_steps, "fetch_c5_executable_quote", lambda *_args: quote)
    monkeypatch.setattr(
        pipeline_steps,
        "notify_c5_manual_recommendation",
        lambda *_args: (True, "Lark"),
    )
    monkeypatch.setattr(
        pipeline_steps,
        "_adjust_ref_price_for_daily_high",
        lambda _name, reference, *_args, **_kwargs: reference,
    )
    monkeypatch.setattr(pipeline_steps, "get_purchases", lambda: [])
    logs = []
    result = pipeline_steps.lock_and_confirm_payment(
        _NoC5OrBuffOrderClient(),
        {
            "goods_id": 123,
            "name": "Recoil Case",
            "steam_market_name": "Recoil Case",
            "daily_volume": 1000,
            "min_price": 10.0,
            "_buff_sell_orders": [{"id": "buff-order", "price": "10.0"}],
            "_steam_sell_data": {"smart_price": 20.0, "sell_orders": []},
        },
        {
            "buff": {"game": "csgo", "price_tolerance": 0.5},
            "c5": {
                "price_compare_enabled": True,
                "manual_recommendation_enabled": True,
                "min_savings_percent": 2.0,
                "min_savings_amount": 0.2,
            },
            "pipeline": {"max_unit_purchase_price": 50.0, "max_discount": 0.8},
        },
        target_balance=20.0,
        acc=0.0,
        set_pending_payment=lambda *_args: None,
        wait_payment_confirm=lambda *_args: None,
        confirm_payment=lambda *_args: None,
        is_stop_requested=lambda: False,
        append_purchase=lambda *_args: None,
        log_fn=lambda message, level: logs.append((message, level)),
    )

    assert result is pipeline_steps.SKIP_C5_MANUAL_RECOMMENDED
    assert any("本件不锁 BUFF 订单" in message for message, _level in logs)
