from pathlib import Path

import pytest

from app.config_schema import DEFAULTS, _validate_ranges, validate_and_fill
from app.services.c5_client import C5ExecutableQuote
from app.services.c5_compare import (
    compare_c5_reference,
    evaluate_c5_manual_recommendation,
    format_c5_shadow_log,
)
from app.services.iflow_client import fetch_iflow_rows
from app.pipeline_steps import filter_iflow_rows
from steamdt.models import SteamDTRow


class _Protection:
    @staticmethod
    def effective_candidate_cap(_recovery_cap, configured_top_n):
        return configured_top_n or 30


def _config(enabled: bool) -> dict:
    return {
        "iflow": {
            "page_num": 1,
            "page_size": 200,
            "platforms": "buff",
            "sort_by": "sell",
            "min_price": 2,
            "max_price": 5000,
            "min_volume": 0,
            "type": "swap",
            "want_to_get": "STEAM_BALANCE",
            "sale_plan": "STEAM_SELL_PRICE",
        },
        "pipeline": {
            "iflow_top_n": 30,
            "buff_protection_recovery_candidate_cap": 30,
            "exclude_keywords": [],
        },
        "c5": {"price_compare_enabled": enabled},
    }


def _buff_row() -> SteamDTRow:
    return SteamDTRow(
        name="Recoil Case",
        volume="1000",
        min_price="1.94",
        platform="https://buff.163.com/goods/123",
        steam_link="https://steamcommunity.com/market/listings/730/Recoil%20Case",
    )


def _c5_row() -> SteamDTRow:
    return SteamDTRow(
        name="Recoil Case",
        volume="1000",
        min_price="1.90",
        platform="https://www.c5game.com/csgo/Recoil%20Case",
        update_time="2026-08-23 23:23:00",
    )


def test_c5_compare_defaults_to_disabled():
    assert DEFAULTS["c5"]["price_compare_enabled"] is False
    assert DEFAULTS["c5"]["manual_recommendation_enabled"] is False
    assert DEFAULTS["c5"]["app_key"] == ""


def test_c5_config_ranges_are_normalized():
    with pytest.warns(UserWarning):
        config = _validate_ranges(validate_and_fill({
            "c5": {
                "min_savings_percent": -1,
                "min_savings_amount": -2,
                "quote_page_size": 999,
                "request_timeout_seconds": 1,
            }
        }, DEFAULTS))
    assert config["c5"]["min_savings_percent"] == 0
    assert config["c5"]["min_savings_amount"] == 0
    assert config["c5"]["quote_page_size"] == 50
    assert config["c5"]["request_timeout_seconds"] == 3
    assert validate_and_fill({}, DEFAULTS)["c5"]["price_compare_enabled"] is False


def test_disabled_keeps_single_buff_fetch(monkeypatch):
    calls = []

    def fake_fetch(_self, params, headless=True):
        calls.append((params["platforms"], headless))
        return [_buff_row()]

    monkeypatch.setattr("app.services.iflow_client.get_buff_request_protection", lambda: _Protection())
    monkeypatch.setattr("app.services.iflow_client.IflowClient.fetch", fake_fetch)

    rows = fetch_iflow_rows(_config(False))

    assert len(rows) == 1
    assert calls == [("buff", True)]
    assert rows[0].c5_reference_price == 0


def test_enabled_fetches_c5_separately_without_replacing_buff_candidates(monkeypatch):
    calls = []

    def fake_fetch(_self, params, headless=True):
        calls.append((params["platforms"], headless))
        return [_c5_row()] if params["platforms"] == "c5" else [_buff_row()]

    monkeypatch.setattr("app.services.iflow_client.get_buff_request_protection", lambda: _Protection())
    monkeypatch.setattr("app.services.iflow_client.IflowClient.fetch", fake_fetch)

    rows = fetch_iflow_rows(_config(True))

    assert len(rows) == 1
    assert rows[0].platform == "https://buff.163.com/goods/123"
    assert rows[0].min_price == "1.94"
    assert rows[0].c5_reference_price == pytest.approx(1.90)
    assert rows[0].c5_reference_update_time == "2026-08-23 23:23:00"
    assert calls == [("buff", True), ("c5", True)]


def test_filter_preserves_c5_reference_as_optional_metadata():
    row = _buff_row()
    row.c5_reference_price = 1.90
    row.c5_reference_link = "https://www.c5game.com/csgo/Recoil%20Case"
    row.c5_reference_update_time = "2026-08-23 23:23:00"

    result = filter_iflow_rows([row], _config(True))

    assert result[0]["goods_id"] == 123
    assert result[0]["c5_reference_price"] == pytest.approx(1.90)
    assert result[0]["c5_reference_link"].startswith("https://www.c5game.com/")


def test_shadow_comparison_reports_but_does_not_choose_an_order_route():
    item = {
        "c5_reference_price": 1.90,
        "c5_reference_link": "https://www.c5game.com/csgo/Recoil%20Case",
    }

    result = compare_c5_reference(1.94, item, _config(True))
    message = format_c5_shadow_log(1.94, item, _config(True))

    assert result is not None
    assert result.cheaper_platform == "c5"
    assert result.savings_amount == pytest.approx(0.04)
    assert "C5 参考价低" in message
    assert "不会在 C5 下单" in message


def test_disabled_shadow_comparison_produces_no_log():
    item = {"c5_reference_price": 1.90}
    assert compare_c5_reference(1.94, item, _config(False)) is None
    assert format_c5_shadow_log(1.94, item, _config(False)) is None


def test_settings_exposes_read_only_c5_toggle():
    root = Path(__file__).resolve().parents[1]
    html = (root / "web" / "index.html").read_text(encoding="utf-8")
    js = (root / "web" / "js" / "settings.js").read_text(encoding="utf-8")

    assert 'id="cfg-c5-price-compare-enabled"' in html
    assert 'id="cfg-c5-app-key"' in html
    assert 'id="cfg-c5-manual-recommendation-enabled"' in html
    assert 'id="btn-check-c5"' in html
    assert "price_compare_enabled" in js
    assert "manual_recommendation_enabled" in js


def test_manual_recommendation_requires_both_savings_thresholds_and_safety_checks():
    quote = C5ExecutableQuote(
        market_hash_name="Recoil Case",
        product_id="p-1",
        price=9.0,
        delivery=1,
        asset_id="a-1",
        reference_link="https://www.c5game.com/csgo/recoil",
        listing_count=5,
    )
    config = {
        "c5": {
            "manual_recommendation_enabled": True,
            "min_savings_percent": 5,
            "min_savings_amount": 0.5,
        }
    }
    result = evaluate_c5_manual_recommendation(
        quote=quote,
        buff_price=10.0,
        steam_reference_price=20.0,
        remaining_budget=20.0,
        max_unit_price=15.0,
        max_discount=0.8,
        config=config,
    )
    assert result is not None
    assert result.savings_amount == pytest.approx(1.0)

    assert evaluate_c5_manual_recommendation(
        quote=quote,
        buff_price=10.0,
        steam_reference_price=20.0,
        remaining_budget=8.0,
        max_unit_price=15.0,
        max_discount=0.8,
        config=config,
    ) is None
