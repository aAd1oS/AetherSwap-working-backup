from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from analysis import stability
from app import sell_pipeline
from app.services import workers


def _history(prices):
    start = datetime.now() - timedelta(days=len(prices) - 1)
    return [
        [(start + timedelta(days=index)).strftime("%b %d %Y %H") + ": +0", price, "20"]
        for index, price in enumerate(prices)
    ]


def _outlier_report(prices, **overrides):
    options = {
        "days": len(prices) + 1,
        "min_daily_trades": 0,
        "cv_threshold": 1,
        "price_percentile_ceil": 1,
        "price_percentile_ceil_rising": 1,
        "ma_deviation_ceil": 999,
        "last_price_ma30_ceil": 999,
        "outlier_filter_enabled": True,
    }
    options.update(overrides)
    return stability.analyze_by_time(_history(prices), **options)


def test_robust_shadow_metrics_distinguish_isolated_and_persistent_highs():
    isolated = stability._robust_shadow_metrics([10, 10, 10, 10, 10, 20, 10])
    persistent = stability._robust_shadow_metrics([10, 10, 10, 10, 12, 12, 12])

    assert isolated["robust_cv"] == 0
    assert isolated["spike_persistence"] == 0.3333
    assert abs(isolated["theil_sen_slope"]) < 0.01
    assert persistent["spike_persistence"] == 1.0
    assert persistent["theil_sen_slope"] > 0
    assert persistent["spearman_rho"] > 0.8


def test_shadow_metrics_do_not_change_buy_acceptance(monkeypatch):
    history = _history([10.00, 10.02, 10.01, 10.03, 10.02, 10.01, 10.02])
    baseline = stability.analyze_by_time(
        history,
        days=7,
        min_daily_trades=0,
        cv_threshold=1,
        price_percentile_ceil=1,
        price_percentile_ceil_rising=1,
        ma_deviation_ceil=999,
        last_price_ma30_ceil=999,
    )
    monkeypatch.setattr(
        stability,
        "_robust_shadow_metrics",
        lambda _values: {
            "mad": 999,
            "robust_cv": 999,
            "theil_sen_slope": -999,
            "spearman_rho": -1,
            "spike_persistence": 1,
            "spike_threshold": 0,
        },
    )
    shadow_changed = stability.analyze_by_time(
        history,
        days=7,
        min_daily_trades=0,
        cv_threshold=1,
        price_percentile_ceil=1,
        price_percentile_ceil_rising=1,
        ma_deviation_ceil=999,
        last_price_ma30_ceil=999,
    )

    assert shadow_changed["is_stable"] == baseline["is_stable"]
    assert shadow_changed["status"] == baseline["status"]
    assert shadow_changed["msg"] == baseline["msg"]
    assert shadow_changed["robust_cv"] == 999


def test_historical_analysis_excludes_future_rows():
    as_of = datetime(2026, 8, 24, 2)
    history = [
        ["Aug 20 2026 02: +0", 10, "20"],
        ["Aug 21 2026 02: +0", 10, "20"],
        ["Aug 22 2026 02: +0", 10, "20"],
        ["Aug 23 2026 02: +0", 10, "20"],
        ["Aug 24 2026 02: +0", 11, "20"],
        ["Aug 25 2026 02: +0", 99, "20"],
    ]

    report = stability.analyze_by_time(
        history,
        days=5,
        as_of=as_of,
        min_daily_trades=0,
        cv_threshold=1,
        price_percentile_ceil=1,
        price_percentile_ceil_rising=1,
        ma_deviation_ceil=999,
        last_price_ma30_ceil=999,
    )

    assert report["raw_count"] == 5
    assert report["last_price"] == 11


def test_iqr_filter_removes_isolated_spike_from_trend_and_ema():
    report = _outlier_report([10, 10, 10, 40, 10, 10, 10])

    assert report["outlier_filter_status"] == "PASS"
    assert report["raw_count"] == 7
    assert report["clean_count"] == 6
    assert report["outlier_removed_count"] == 1
    assert report["ma7"] == 10.0
    assert report["slope"] == 0.0


def test_iqr_filter_rejects_recent_persistent_shift_without_erasing_it():
    report = _outlier_report([10] * 10 + [30, 30, 30])

    assert report["outlier_filter_status"] == "REJECT"
    assert report["outlier_filter_reason"] == "persistent_shift"
    assert report["persistent_shift_detected"] is True
    assert report["outlier_removed_count"] == 0
    assert report["is_stable"] is False


def test_iqr_filter_rejects_when_too_many_points_would_be_removed():
    report = _outlier_report([30, 10, 30, 10, 30] + [10] * 8)

    assert report["outlier_filter_status"] == "REJECT"
    assert report["outlier_filter_reason"] == "excessive_outliers"
    assert report["outlier_removed_ratio"] == 0.2308
    assert report["outlier_removed_count"] == 0
    assert report["is_stable"] is False


def test_iqr_filter_disabled_preserves_legacy_daily_series():
    report = _outlier_report(
        [10, 10, 10, 40, 10, 10, 10],
        outlier_filter_enabled=False,
    )

    assert report["outlier_filter_status"] == "DISABLED"
    assert report["clean_count"] == report["raw_count"] == 7
    assert report["ma7"] != 10.0


def test_queue_and_wait_estimates_are_conservative():
    orders = [(10.00, 3), (10.10, 4), (10.50, 8)]

    assert sell_pipeline._queue_ahead_at_price(orders, 10.10) == 7
    assert sell_pipeline._estimate_wait_hours(7, 14) == 12.0
    assert sell_pipeline._estimate_wait_hours(7, 0) is None


def test_short_wait_blocks_only_downward_stale_reprice():
    result = sell_pipeline._apply_stale_wait_gate(10.50, 10.10, "wall moved", 7, 14)

    assert result["status"] == "hold"
    assert result["estimated_wait_hours"] == 12.0
    assert "72" in result["reason"]


def test_long_or_unknown_wait_preserves_existing_strategy_decision():
    long_wait = sell_pipeline._apply_stale_wait_gate(10.50, 10.10, "wall moved", 100, 20)
    unknown = sell_pipeline._apply_stale_wait_gate(10.50, 10.10, "wall moved", 100, None)
    higher_price = sell_pipeline._apply_stale_wait_gate(10.50, 10.80, "wall moved", 1, 100)

    assert long_wait["status"] == "allow"
    assert long_wait["estimated_wait_hours"] == 120.0
    assert unknown["status"] == "allow"
    assert unknown["estimated_wait_hours"] is None
    assert higher_price["status"] == "allow"


def test_normal_listing_reuses_orderbook_without_history_request():
    item = {
        "name": "AK-47 | Redline",
        "market_hash_name": "AK-47 | Redline",
        "assetid": "asset-1",
        "can_sell": True,
        "appid": 730,
        "contextid": "2",
        "ownership_mode": "managed",
    }
    ctx = MagicMock()
    ctx.is_stop_requested.return_value = False
    with patch(
        "app.sell_pipeline.get_sell_orders_cny",
        return_value={"sell_orders": [(30.0, 2), (31.0, 3)]},
    ), patch(
        "app.sell_pipeline.compute_smart_list_price",
        return_value=(30.0, "wall"),
    ), patch(
        "app.sell_pipeline._steam_recent_market_activity",
        side_effect=AssertionError("normal listing must not fetch history"),
    ), patch(
        "app.sell_pipeline.get_current_account",
        return_value={"id": "account-1"},
    ):
        plan = sell_pipeline._build_listing_plan(
            ctx=ctx,
            cfg={"pipeline": {}},
            session=MagicMock(),
            sellable=[item],
            sell_strategy=1,
            pipeline_cfg={},
            purchases_snapshot=[],
            ok_listings=False,
            active_listing_ids=set(),
            listing_assetid_to_name={},
            assetid_to_name_map={},
            account_currency="CNY",
            rate_map={},
        )

    assert len(plan) == 1
    assert plan[0]["queue_ahead"] == 2
    assert plan[0]["estimated_wait_hours"] is None


def test_stale_market_summary_contains_queue_volume_and_wait():
    summary = workers._format_listing_market_metrics({
        "queue_ahead": 18,
        "average_daily_volume": 12.5,
        "estimated_wait_hours": 34.6,
    })

    assert "18" in summary
    assert "12.5" in summary
    assert "34.6" in summary

def test_recent_market_activity_calculates_volume_without_extra_dependency():
    start = datetime.now() - timedelta(days=6)
    history = [
        [(start + timedelta(days=index)).strftime("%b %d %Y %H") + ": +0", 10 + index * 0.1, "10"]
        for index in range(7)
    ]
    with patch(
        "app.services.steam_client.SteamClient.fetch_history",
        return_value={"history": history, "currency": "CNY"},
    ):
        activity = sell_pipeline._steam_recent_market_activity("AK-47 | Redline", trend_days=7)

    assert activity is not None
    assert activity["total_volume"] == 70
    assert activity["average_daily_volume"] == 10.0


def test_stale_lark_notice_contains_market_wait_summary(monkeypatch):
    row = {
        "_db_id": 1,
        "account_id": "account-1",
        "assetid": "asset-1",
        "name": "AK-47 | Redline",
        "listing": True,
        "listed_at": 100.0,
        "listing_price": 20.0,
        "listing_review_after": 200.0,
        "stale_listing_notified_at": None,
        "pending_receipt": False,
        "order_status": "received",
        "sale_price": None,
    }
    updates = []
    notices = []
    monkeypatch.setattr(
        workers,
        "update_purchase_by_id",
        lambda _db_id, data: updates.append(data.copy()) or row.update(data) or True,
    )
    monkeypatch.setattr(
        "app.inventory_ownership.db_get_inventory_ownership_overrides",
        lambda: {},
    )

    result = workers._review_stale_listings(
        [row],
        {"asset-1"},
        {"asset-1": "AK-47 | Redline"},
        {"notify": {"lark_webhook": "https://example.invalid/hook"}},
        MagicMock(),
        now=300.0,
        evaluate_fn=lambda *_args: {
            "status": "hold",
            "reason": "预计等待较短，继续等待",
            "queue_ahead": 18,
            "average_daily_volume": 12.5,
            "estimated_wait_hours": 34.6,
        },
        notify_fn=lambda webhook, title, content: notices.append(
            (webhook, title, content)
        ) or True,
    )

    assert result["notified"] == 1
    assert notices
    assert "18" in notices[0][2]
    assert "12.5" in notices[0][2]
    assert "34.6" in notices[0][2]

def test_personal_protection_is_debug_only_in_listing_plan():
    item = {
        "name": "AWP | Worm God",
        "market_hash_name": "AWP | Worm God",
        "assetid": "personal-1",
        "can_sell": True,
        "appid": 730,
        "contextid": "2",
        "ownership_mode": "personal",
    }
    ctx = MagicMock()
    ctx.is_stop_requested.return_value = False

    plan = sell_pipeline._build_listing_plan(
        ctx=ctx,
        cfg={"pipeline": {}},
        session=MagicMock(),
        sellable=[item],
        sell_strategy=3,
        pipeline_cfg={},
        purchases_snapshot=[],
        ok_listings=False,
        active_listing_ids=set(),
        listing_assetid_to_name={},
        assetid_to_name_map={},
        account_currency="CNY",
        rate_map={},
    )

    assert plan == []
    ctx.debug.assert_called_once()
    assert "个人保护库存" in ctx.debug.call_args.args[0]
    assert not any(
        "个人保护库存" in call.args[0]
        for call in ctx.log.call_args_list
        if call.args
    )


def test_sellable_ownership_summary_matches_listing_rules():
    items = [
        {
            "name": "Personal",
            "market_hash_name": "Personal",
            "assetid": "p1",
            "ownership_mode": "personal",
        },
        {
            "name": "Managed",
            "market_hash_name": "Managed",
            "assetid": "m1",
            "ownership_mode": "managed",
        },
        {
            "name": "Tracked",
            "market_hash_name": "Tracked",
            "assetid": "t1",
            "ownership_mode": "",
        },
    ]
    purchases = [{"assetid": "t1", "name": "Tracked"}]

    managed, personal = sell_pipeline._count_sellable_ownership(items, purchases)

    assert managed == 2
    assert personal == 1


def test_listing_plan_summary_is_compact_but_keeps_important_names():
    plan = [
        {"name": f"Item {index}", "list_price": index + 1}
        for index in range(12)
    ]

    summary = sell_pipeline._format_listing_plan_summary(plan)

    assert "Item 0@1.00" in summary
    assert "Item 9@10.00" in summary
    assert "Item 10" not in summary
    assert "共 12 件" in summary
