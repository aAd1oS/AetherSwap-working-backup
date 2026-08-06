import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.pipeline_steps import (
    _adjust_ref_price_for_daily_high,
    _compute_sell_pressure_from_orders,
    filter_iflow_rows,
    pick_stable_item,
)


def _行(name="Test Item", min_price="10.00", platform="https://buff.163.com/goods/12345", **kw):
    """构造一条最小化 iflow 行，省得每次都写全"""
    row = SimpleNamespace(
        name=name,
        min_price=min_price,
        sell_ratio="0.85",
        buy_ratio="0.80",
        safe_buy_ratio="0.78",
        recent_ratio="0.82",
        platform=platform,
        steam_link="",
        volume="500",
    )
    for k, v in kw.items():
        setattr(row, k, v)
    return row


_基础配置 = {
    "pipeline": {"exclude_keywords": ["印花", "胶囊"], "iflow_top_n": 0},
    "iflow": {"sort_by": "sell"},
}

# ── 卖压计算测试 ──────────────────────────────────────────────────────────

def test_卖压_空订单返回None():
    assert _compute_sell_pressure_from_orders([], 100) is None


def test_卖压_日销量为零返回None():
    orders = [(5.0, 10), (5.1, 5)]
    assert _compute_sell_pressure_from_orders(orders, 0) is None


def test_卖压_大量挂单卖压高():
    # 200件挂单，日销10，压力比 = 20，稳妥超过阈值
    orders = [(5.0, 200)]
    pressure = _compute_sell_pressure_from_orders(orders, 10, n_orders=1)
    assert pressure is not None
    assert pressure > 1.0


def test_卖压_少量挂单卖压低():
    orders = [(5.0, 1), (5.1, 1)]
    pressure = _compute_sell_pressure_from_orders(orders, 1000, n_orders=5)
    assert pressure is not None
    assert pressure < 1.0


def test_卖压_价格断层识别():
    # 3件挂5元，然后跳到10元才有大单——属于薄壁情况，应该降权
    orders_断层 = [(5.0, 3), (10.0, 50)]
    orders_正常 = [(5.0, 25), (5.1, 25)]
    p1 = _compute_sell_pressure_from_orders(orders_断层, 100, n_orders=5)
    p2 = _compute_sell_pressure_from_orders(orders_正常, 100, n_orders=5)
    assert p1 is not None and p2 is not None
    # 薄壁的卖压应该比正常的低（被打折了）
    assert p1 < p2


# ── iflow行过滤测试 ────────────────────────────────────────────────────────

def test_过滤_正常行通过():
    rows = [_行(name="AWP | Dragon Lore")]
    result = filter_iflow_rows(rows, _基础配置)
    assert len(result) == 1


def test_过滤_关键词命中被去掉():
    rows = [
        _行(name="印花 | 某队伍"),
        _行(name="胶囊 | 某队伍"),
        _行(name="AWP | 正常枪"),
    ]
    result = filter_iflow_rows(rows, _基础配置)
    assert len(result) == 1
    assert result[0]["name"] == "AWP | 正常枪"


def test_过滤_价格非正被去掉():
    rows = [
        _行(name="负价格", min_price="-1"),
        _行(name="零价格", min_price="0"),
        _行(name="正常价格", min_price="10.0"),
    ]
    result = filter_iflow_rows(rows, _基础配置)
    assert len(result) == 1
    assert result[0]["name"] == "正常价格"


def test_过滤_非buff链接被去掉():
    rows = [
        _行(name="c5game的", platform="https://c5game.com/item/999"),
        _行(name="buff的"),
    ]
    result = filter_iflow_rows(rows, _基础配置)
    assert len(result) == 1
    assert result[0]["name"] == "buff的"


def test_过滤_topN限制数量():
    rows = [_行(name=f"物品{i}") for i in range(20)]
    cfg = {**_基础配置, "pipeline": {**_基础配置["pipeline"], "iflow_top_n": 5}}
    result = filter_iflow_rows(rows, cfg)
    assert len(result) <= 5


def test_filter_excludes_previous_round_before_candidate_cap():
    rows = [
        _行(
            name=f"Item {index}",
            platform=f"https://buff.163.com/goods/{1000 + index}",
        )
        for index in range(10)
    ]
    cfg = {
        **_基础配置,
        "pipeline": {**_基础配置["pipeline"], "iflow_top_n": 5},
    }

    result = filter_iflow_rows(
        rows,
        cfg,
        exclude_goods_ids={1000, 1001, 1002, 1003, 1004},
    )

    assert [row["goods_id"] for row in result] == [1005, 1006, 1007, 1008, 1009]


def test_filter_applies_local_rejections_before_candidate_cap():
    rows = [
        _行(
            name=f"blocked {index}",
            platform=f"https://buff.163.com/goods/{1100 + index}",
        )
        for index in range(5)
    ] + [
        _行(
            name=f"Eligible {index}",
            platform=f"https://buff.163.com/goods/{1200 + index}",
        )
        for index in range(5)
    ]
    cfg = {
        "pipeline": {"exclude_keywords": ["blocked"], "iflow_top_n": 5},
        "iflow": {"sort_by": "sell", "min_volume": 200},
    }

    result = filter_iflow_rows(rows, cfg)

    assert [row["goods_id"] for row in result] == [1200, 1201, 1202, 1203, 1204]


def test_过滤_goods_id从url解析():
    rows = [_行(platform="https://buff.163.com/goods/98765")]
    result = filter_iflow_rows(rows, _基础配置)
    assert result[0]["goods_id"] == 98765


def test_过滤_本地再次执行最低成交量门槛():
    rows = [
        _行(name="成交量不足", volume="199"),
        _行(name="刚好达标", volume="200"),
        _行(name="成交量更高", volume="500"),
    ]
    cfg = {
        **_基础配置,
        "iflow": {**_基础配置["iflow"], "min_volume": 200},
    }

    result = filter_iflow_rows(rows, cfg)

    assert [row["name"] for row in result] == ["刚好达标", "成交量更高"]


def test_过滤_未显式配置成交量时使用默认门槛200():
    rows = [
        _行(name="默认门槛未达标", volume="199"),
        _行(name="默认门槛达标", volume="200"),
    ]

    result = filter_iflow_rows(rows, _基础配置)

    assert [row["name"] for row in result] == ["默认门槛达标"]


def test_过滤_成交量缺失或异常时按零处理并拒绝():
    rows = [
        _行(name="缺失成交量", volume=None),
        _行(name="异常成交量", volume="unknown"),
    ]
    cfg = {
        **_基础配置,
        "iflow": {**_基础配置["iflow"], "min_volume": 200},
    }

    assert filter_iflow_rows(rows, cfg) == []


def test_过滤_最低成交量设为零时关闭本地门槛():
    rows = [_行(name="零成交量", volume="0")]
    cfg = {
        **_基础配置,
        "iflow": {**_基础配置["iflow"], "min_volume": 0},
    }

    result = filter_iflow_rows(rows, cfg)

    assert [row["name"] for row in result] == ["零成交量"]


def test_过滤_最低成交量配置异常时安全回退到200():
    rows = [
        _行(name="异常配置下未达标", volume="199"),
        _行(name="异常配置下达标", volume="200"),
    ]
    cfg = {
        **_基础配置,
        "iflow": {**_基础配置["iflow"], "min_volume": "invalid"},
    }

    result = filter_iflow_rows(rows, cfg)

    assert [row["name"] for row in result] == ["异常配置下达标"]


def test_pick_records_only_candidates_that_reach_precheck(monkeypatch):
    filtered = [
        {"goods_id": 1, "name": "Previous round", "min_price": 2.0, "daily_volume": 500},
        {"goods_id": 2, "name": "Rejected now", "min_price": 2.1, "daily_volume": 500},
        {"goods_id": 3, "name": "Selected now", "min_price": 2.2, "daily_volume": 500},
    ]
    attempted = set()

    monkeypatch.setattr(
        "app.pipeline_steps.is_strategy_module_enabled",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        "app.pipeline_steps._fetch_steam_sell_data",
        lambda *_args, **_kwargs: {"smart_price": 3.0, "sell_orders": []},
    )
    monkeypatch.setattr(
        "app.pipeline_steps._passes_custom_buy_modules",
        lambda item, *_args, **_kwargs: item["goods_id"] == 3,
    )

    chosen, failed = pick_stable_item(
        filtered,
        {"stability": {"request_interval_seconds": 0}},
        object(),
        object(),
        lambda: False,
        exclude_goods_ids={1},
        attempted_goods_ids=attempted,
    )

    assert chosen["goods_id"] == 3
    assert failed == {2}
    assert attempted == {2, 3}


def _steam_history_rows(prices):
    now = datetime.now()
    return [
        [(now - timedelta(hours=i + 1)).strftime("%b %d %Y %H"), price, "1"]
        for i, price in enumerate(prices)
    ]


def test_daily_high_adjustment_uses_trimmed_average(monkeypatch):
    history = _steam_history_rows([3.50, 3.54, 3.60, 3.65, 3.72, 11.45])

    class DummySteamClient:
        def fetch_history(self, market_hash_name, app_id=730, return_currency=False):
            return {"history": history, "currency": "CNY"}

    monkeypatch.setattr("app.pipeline_steps.SteamClient", DummySteamClient)

    adjusted = _adjust_ref_price_for_daily_high(
        "Test Item",
        3.69,
        {"pipeline": {"usd_to_cny": 7.2}},
        lambda _msg, _level: None,
    )

    assert adjusted == pytest.approx(3.65875)
    assert adjusted < 3.69


def test_daily_high_adjustment_never_raises_reference_price(monkeypatch):
    history = _steam_history_rows([1.0, 2.0, 8.0, 10.0, 10.0, 20.0])

    class DummySteamClient:
        def fetch_history(self, market_hash_name, app_id=730, return_currency=False):
            return {"history": history, "currency": "CNY"}

    monkeypatch.setattr("app.pipeline_steps.SteamClient", DummySteamClient)

    adjusted = _adjust_ref_price_for_daily_high(
        "Test Item",
        7.0,
        {"pipeline": {"usd_to_cny": 7.2}},
        lambda _msg, _level: None,
    )

    assert adjusted == 7.0

def test_daily_high_adjustment_reuses_provided_steam_client(monkeypatch):
    history = _steam_history_rows([3.50, 3.54, 3.60, 3.65, 3.72, 11.45])

    class ProvidedSteamClient:
        def __init__(self):
            self.calls = 0

        def fetch_history(self, market_hash_name, app_id=730, return_currency=False):
            self.calls += 1
            return {"history": history, "currency": "CNY"}

    provided = ProvidedSteamClient()
    monkeypatch.setattr(
        "app.pipeline_steps.SteamClient",
        lambda: (_ for _ in ()).throw(AssertionError("must reuse provided client")),
    )

    adjusted = _adjust_ref_price_for_daily_high(
        "Test Item",
        3.69,
        {"pipeline": {"usd_to_cny": 7.2}},
        lambda _msg, _level: None,
        steam_client=provided,
    )

    assert adjusted == pytest.approx(3.65875)
    assert provided.calls == 1
