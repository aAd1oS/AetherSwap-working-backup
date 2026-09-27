from pathlib import Path

import pytest

from app import pipeline
from app.routes import transactions
from app.services import buff_balance


def test_stats_total_investment_includes_unsold_purchases(monkeypatch):
    monkeypatch.setattr(
        transactions,
        "get_purchases",
        lambda: [
            {"price": 10.0},
            {"price": 15.0, "sale_price": 23.0},
            {"price": "invalid", "sale_price": None},
            {"price": -2.0, "sale_price": 0},
        ],
    )
    monkeypatch.setattr(
        transactions,
        "get_buff_balance",
        lambda: {"has_value": False, "balance": None},
    )
    monkeypatch.setattr(
        transactions,
        "load_app_config_validated",
        lambda: {"pipeline": {"resell_ratio": 0.85}},
    )

    result = transactions.api_stats()

    assert result["total_invested"] == pytest.approx(25.0)
    assert result["total_purchased"] == pytest.approx(25.0)
    assert result["total_sold"] == pytest.approx(23.0)
    assert result["total_sold_after_tax"] == pytest.approx(20.0)
    assert result["total_sold_cost"] == pytest.approx(15.0)
    assert result["total_profit"] == pytest.approx(5.0)
    assert result["total_self_use_profit"] == pytest.approx(5.0)
    assert result["total_conversion_profit"] == pytest.approx(2.0)
    assert result["resell_ratio"] == pytest.approx(0.85)
    assert result["discount_ratio"] == pytest.approx(0.75)


def test_buff_balance_cache_records_insufficient_preview_and_confirmed_spend(
    monkeypatch,
    tmp_path,
):
    cache_path = tmp_path / "buff_balance_cache.json"
    monkeypatch.setattr(buff_balance, "_CACHE_FILE", cache_path)

    observed = buff_balance.record_buff_balance_preview(
        {
            "usable": False,
            "balance_status": "unavailable",
            "reported_balance": 7.73,
        },
        game="csgo",
        goods_id=921516,
        sell_order_id="sell-1",
        price="9.50",
    )

    assert observed["has_value"] is True
    assert observed["balance"] == pytest.approx(7.73)
    assert observed["estimated"] is False
    assert buff_balance.get_buff_balance_probe() == {
        "game": "csgo",
        "goods_id": 921516,
        "sell_order_id": "sell-1",
        "price": "9.50",
    }

    after_payment = buff_balance.record_confirmed_buff_spend(2.57)

    assert after_payment["balance"] == pytest.approx(5.16)
    assert after_payment["estimated"] is True
    assert cache_path.exists()


def test_buff_credential_update_invalidation_preserves_probe_but_drops_balance(
    tmp_path,
    monkeypatch,
):
    cache_path = tmp_path / "buff_balance_cache.json"
    monkeypatch.setattr(buff_balance, "_CACHE_FILE", cache_path)

    buff_balance.record_buff_balance_preview(
        {"reported_balance": 7.73},
        game="csgo",
        goods_id=921516,
        sell_order_id="sell-1",
        price="9.50",
    )

    buff_balance.invalidate_buff_balance_observation()

    assert buff_balance.get_buff_balance()["has_value"] is False
    assert buff_balance.get_buff_balance_probe() == {
        "game": "csgo",
        "goods_id": 921516,
        "sell_order_id": "sell-1",
        "price": "9.50",
    }


def test_order_specific_zero_does_not_overwrite_last_trusted_balance(monkeypatch, tmp_path):
    monkeypatch.setattr(buff_balance, "_CACHE_FILE", tmp_path / "buff_balance_cache.json")
    trusted = buff_balance.record_buff_balance_preview(
        {
            "usable": True,
            "balance": 15.11,
            "balance_observation_trustworthy": True,
        },
        game="csgo",
        goods_id=921516,
        sell_order_id="trusted-order",
        price="2.20",
    )

    rejected = buff_balance.record_buff_balance_preview(
        {
            "usable": False,
            "balance_status": "unavailable",
            "reported_balance": 0.0,
            "balance_observation_trustworthy": False,
            "balance_observation_reason": "订单支付通道错误，不能代表账号余额",
        },
        game="csgo",
        goods_id=956462,
        sell_order_id="unsupported-order",
        price="2.22",
    )

    assert trusted["balance"] == pytest.approx(15.11)
    assert rejected["observation_accepted"] is False
    assert rejected["observed_balance"] == pytest.approx(0.0)
    assert rejected["balance"] == pytest.approx(15.11)
    assert rejected["uncertain"] is True
    assert "不能代表账号余额" in rejected["uncertainty_reason"]
    assert buff_balance.get_buff_balance()["balance"] == pytest.approx(15.11)


def test_order_specific_positive_error_balance_does_not_overwrite_confirmed_estimate(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(buff_balance, "_CACHE_FILE", tmp_path / "buff_balance_cache.json")
    buff_balance.record_buff_balance_amount(350.0, source="account_asset")
    after_payment = buff_balance.record_confirmed_buff_spend(16.07)

    rejected = buff_balance.record_buff_balance_preview(
        {
            "usable": False,
            "balance_status": "unavailable",
            "reported_balance": 0.39,
            "balance_observation_trustworthy": False,
            "balance_observation_reason": "order-specific channel has a platform error",
        },
        game="csgo",
        goods_id=929010,
        sell_order_id="unsupported-order",
        price="11.89",
    )

    assert after_payment["balance"] == pytest.approx(333.93)
    assert rejected["observation_accepted"] is False
    assert rejected["observed_balance"] == pytest.approx(0.39)
    assert rejected["balance"] == pytest.approx(333.93)
    assert rejected["uncertain"] is True
    assert buff_balance.get_buff_balance()["balance"] == pytest.approx(333.93)


def test_manual_balance_refresh_is_blocked_while_pipeline_runs(monkeypatch):
    monkeypatch.setattr(transactions, "get_status", lambda: {"status": "running"})
    monkeypatch.setattr(
        transactions,
        "get_buff_balance",
        lambda: {"has_value": True, "balance": 7.73},
    )

    result = transactions.api_refresh_buff_balance()

    assert result["ok"] is False
    assert "任务正在运行" in result["error"]
    assert result["buff_balance"]["balance"] == pytest.approx(7.73)


def test_manual_balance_refresh_uses_single_account_asset_request(monkeypatch):
    class FakeClient:
        def __init__(self):
            self.calls = []

        def get_available_funds_once(self):
            self.calls.append("account_asset")
            return {"ok": True, "balance": 7.73, "source_field": "cash_amount_outer"}

    client = FakeClient()
    monkeypatch.setattr(transactions, "get_status", lambda: {"status": "stopped"})
    monkeypatch.setattr(transactions, "get_buff_balance", lambda: {"has_value": False})
    monkeypatch.setattr(
        transactions,
        "get_buff_credentials",
        lambda: {"cookies": "session=test; csrf_token=test"},
    )
    monkeypatch.setattr(
        transactions,
        "get_steam_credentials",
        lambda: {"steam_id": "76561198000000000"},
    )
    monkeypatch.setattr(transactions, "load_app_config_validated", lambda: {"buff": {"game": "csgo"}})
    monkeypatch.setattr(transactions, "create_buff_client_from_config", lambda *_args: client)
    monkeypatch.setattr(
        transactions,
        "record_buff_balance_amount",
        lambda amount, **_kwargs: {
            "has_value": True,
            "balance": amount,
            "estimated": False,
            "stale": False,
        },
    )
    monkeypatch.setattr(transactions, "log", lambda *_args, **_kwargs: None)

    result = transactions.api_refresh_buff_balance()

    assert result["ok"] is True
    assert result["buff_balance"]["balance"] == pytest.approx(7.73)
    assert client.calls == ["account_asset"]


def test_manual_balance_refresh_accepts_account_level_zero(monkeypatch):
    class FakeClient:
        def get_available_funds_once(self):
            return {"ok": True, "balance": 0.0, "source_field": "cash_amount_outer"}

    monkeypatch.setattr(transactions, "get_status", lambda: {"status": "stopped"})
    monkeypatch.setattr(
        transactions,
        "get_buff_balance",
        lambda: {"has_value": True, "balance": 15.11},
    )
    monkeypatch.setattr(
        transactions,
        "get_buff_credentials",
        lambda: {"cookies": "session=test; csrf_token=test"},
    )
    monkeypatch.setattr(
        transactions,
        "get_steam_credentials",
        lambda: {"steam_id": "76561198000000000"},
    )
    monkeypatch.setattr(transactions, "load_app_config_validated", lambda: {})
    monkeypatch.setattr(
        transactions,
        "create_buff_client_from_config",
        lambda *_args: FakeClient(),
    )
    monkeypatch.setattr(
        transactions,
        "record_buff_balance_amount",
        lambda amount, **_kwargs: {
            "has_value": True,
            "balance": amount,
            "uncertain": False,
            "observation_accepted": True,
        },
    )

    result = transactions.api_refresh_buff_balance()

    assert result["ok"] is True
    assert result["buff_balance"]["balance"] == pytest.approx(0.0)


def test_manual_balance_refresh_does_not_report_stale_cache_as_success(monkeypatch):
    class FakeClient:
        def get_available_funds_once(self):
            return {"ok": False, "balance": None, "reason": "asset response unavailable"}

    cached = {"has_value": True, "balance": 0.39, "source": "purchase_preview"}
    monkeypatch.setattr(transactions, "get_status", lambda: {"status": "stopped"})
    monkeypatch.setattr(transactions, "get_buff_balance", lambda: cached)
    monkeypatch.setattr(
        transactions,
        "get_buff_credentials",
        lambda: {"cookies": "session=test; csrf_token=test"},
    )
    monkeypatch.setattr(transactions, "get_steam_credentials", lambda: {})
    monkeypatch.setattr(transactions, "load_app_config_validated", lambda: {})
    monkeypatch.setattr(
        transactions,
        "create_buff_client_from_config",
        lambda *_args: FakeClient(),
    )
    monkeypatch.setattr(
        transactions,
        "record_buff_balance_amount",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("failed observation must not be persisted")
        ),
    )

    result = transactions.api_refresh_buff_balance()

    assert result["ok"] is False
    assert result["error"] == "asset response unavailable"
    assert result["buff_balance"] == cached


def test_retry_balance_refresh_uses_one_account_asset_request(monkeypatch):
    class FakeContext:
        def __init__(self):
            self.logs = []
            self.statuses = []

        def debug(self, message):
            self.logs.append(("debug", message))

        def log(self, message, level, category=None):
            self.logs.append((level, message, category))

        def set_status(self, status, stage, **kwargs):
            self.statuses.append((status, stage, kwargs))

    class FakeClient:
        def __init__(self):
            self.calls = []

        def get_available_funds_once(self):
            self.calls.append("account_asset")
            return {"ok": True, "balance": 17.73}

    ctx = FakeContext()
    client = FakeClient()
    monkeypatch.setattr(
        pipeline,
        "get_buff_balance",
        lambda: {"has_value": True, "balance": 7.73},
    )
    monkeypatch.setattr(
        pipeline,
        "record_buff_balance_amount",
        lambda amount, **_kwargs: {"has_value": True, "balance": amount},
    )

    refreshed = pipeline._refresh_buff_balance_after_retry(ctx, client, "balance")

    assert refreshed is True
    assert client.calls == ["account_asset"]
    assert any(stage == "REFRESHING_BUFF_BALANCE" for _, stage, _ in ctx.statuses)
    assert any("7.73 -> 17.73" in entry[1] for entry in ctx.logs)


def test_retry_balance_refresh_does_not_query_for_manual_payment_mode(monkeypatch):
    class UnexpectedClient:
        def get_available_funds_once(self):
            raise AssertionError("manual payment mode must not query BUFF balance")

    assert pipeline._refresh_buff_balance_after_retry(object(), UnexpectedClient(), "wechat") is False


def test_retry_balance_refresh_accepts_account_level_zero(monkeypatch):
    class FakeContext:
        def __init__(self):
            self.logs = []

        def debug(self, message):
            self.logs.append(message)

        def log(self, message, *_args, **_kwargs):
            self.logs.append(message)

        def set_status(self, *_args, **_kwargs):
            return None

    class FakeClient:
        def get_available_funds_once(self):
            return {"ok": True, "balance": 0.0}

    ctx = FakeContext()
    monkeypatch.setattr(
        pipeline,
        "get_buff_balance",
        lambda: {"has_value": True, "balance": 15.11, "uncertain": True},
    )
    recorded = []
    monkeypatch.setattr(
        pipeline,
        "record_buff_balance_amount",
        lambda amount, **_kwargs: recorded.append(amount) or {"has_value": True, "balance": amount},
    )

    refreshed = pipeline._refresh_buff_balance_after_retry(ctx, FakeClient(), "balance")

    assert refreshed is True
    assert recorded == [0.0]
    assert any("0.00" in message for message in ctx.logs)


def test_retry_wait_completes_before_balance_refresh(monkeypatch):
    events = []

    class FakeContext:
        def wait_retry(self, seconds):
            events.append(("wait", seconds))
            return False

    monkeypatch.setattr(
        pipeline,
        "_refresh_buff_balance_after_retry",
        lambda *_args: events.append(("refresh", None)),
    )

    stopped = pipeline._wait_retry_and_refresh_buff_balance(
        FakeContext(),
        300,
        object(),
        "balance",
    )

    assert stopped is False
    assert events == [("wait", 300), ("refresh", None)]


def test_dashboard_exposes_balance_refresh_total_investment_and_full_scale():
    root = Path(__file__).resolve().parents[1]
    html = (root / "web" / "index.html").read_text(encoding="utf-8")
    main_js = (root / "web" / "js" / "main.js").read_text(encoding="utf-8")
    settings_js = (root / "web" / "js" / "settings.js").read_text(encoding="utf-8")

    assert "总投入" in html
    assert "总出售金额（含税）" in html
    assert "营收" not in html
    assert 'id="stat-total-sold-after-tax"' in html
    assert "已出售商品购入价" in html
    assert "折换收益" in html
    assert "自用收益" in html
    assert 'id="stat-conversion-profit"' in html
    assert 'id="stat-total-sold-cost"' in html
    assert 'id="stat-buff-balance"' in html
    assert 'id="btn-refresh-buff-balance"' in html
    assert 'value="1.0" selected>100% (默认)' in html
    assert 'API + "/buff/balance/refresh"' in main_js
    assert "s.total_invested ?? s.total_purchased" in main_js
    assert "s.total_sold_cost ?? 0" in main_js
    assert "s.total_sold_after_tax ?? 0" in main_js
    assert "s.total_conversion_profit" in main_js
    assert "s.total_self_use_profit ?? s.total_profit" in main_js
    assert "buffBalance.uncertain" in main_js
    assert "本次订单通道未采信" in main_js
    assert 'sys.ui_scale || "1.0"' in settings_js


def test_default_ui_scale_is_full_size():
    from app.config_schema import DEFAULTS

    assert DEFAULTS["system"]["ui_scale"] == "1.0"
