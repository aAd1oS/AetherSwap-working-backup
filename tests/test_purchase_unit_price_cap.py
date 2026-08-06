from app import pipeline_steps


class _NoLockClient:
    payment_mode = "balance"

    def __init__(self):
        self.preview_calls = 0

    def preview_balance_payment(self, *_args):
        self.preview_calls += 1
        raise AssertionError("单价超限时不应进入购买预览或锁单")


def test_actual_buff_price_over_unit_cap_never_reaches_lock():
    client = _NoLockClient()
    logs = []
    config = {
        "buff": {"game": "csgo", "price_tolerance": 1.0},
        "pipeline": {"max_unit_purchase_price": 2.50},
        "_strategy_runtime": {
            "buy": {
                "enabled_modules": ["guard.max_unit_purchase_price", "action.buff_lock_pay"],
                "params": {
                    "guard.max_unit_purchase_price": {"max_unit_purchase_price": 2.50},
                },
            }
        },
    }
    item = {
        "goods_id": 921516,
        "name": "Test Item",
        "steam_market_name": "Test Item",
        "min_price": 2.40,
        "_buff_sell_orders": [{"id": "sell-order", "price": "2.57"}],
    }

    result = pipeline_steps.lock_and_confirm_payment(
        client,
        item,
        config,
        target_balance=10.0,
        acc=0.0,
        set_pending_payment=lambda *_args: None,
        wait_payment_confirm=lambda *_args: None,
        confirm_payment=lambda *_args: None,
        is_stop_requested=lambda: False,
        append_purchase=lambda *_args: None,
        log_fn=lambda message, level: logs.append((message, level)),
    )

    assert result is pipeline_steps.SKIP_NO_FAILED
    assert client.preview_calls == 0
    assert any("实际单价 2.57" in message and "最高购买价 2.50" in message for message, _ in logs)
