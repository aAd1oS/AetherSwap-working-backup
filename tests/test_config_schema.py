import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config_schema import DEFAULTS, _validate_ranges, merge, validate_and_fill


# ── merge() 深度合并测试 ──────────────────────────────────────────────────

def test_merge_顶层覆盖():
    result = merge({"a": 1, "b": 2}, {"b": 99})
    assert result["a"] == 1
    assert result["b"] == 99


def test_merge_深层合并不影响未指定key():
    # 用户只改了max_discount，target_balance不能消失
    defaults = {"pipeline": {"target_balance": 100, "max_discount": 0.9}}
    result = merge(defaults, {"pipeline": {"max_discount": 0.85}})
    assert result["pipeline"]["target_balance"] == 100
    assert result["pipeline"]["max_discount"] == 0.85


def test_merge_非dict会整体覆盖():
    result = merge({"x": {"a": 1}}, {"x": 42})
    assert result["x"] == 42


def test_merge_不修改原始defaults():
    defaults = {"a": {"b": 1}}
    merge(defaults, {"a": {"b": 99}})
    assert defaults["a"]["b"] == 1  # 原始不能被污染


# ── validate_and_fill() 类型转换测试 ─────────────────────────────────────

def test_validate_字符串转float():
    result = validate_and_fill({"pipeline": {"max_discount": "0.7"}}, DEFAULTS)
    assert isinstance(result["pipeline"]["max_discount"], float)
    assert result["pipeline"]["max_discount"] == 0.7


def test_validate_字符串转int():
    result = validate_and_fill({"pipeline": {"iflow_top_n": "30"}}, DEFAULTS)
    assert result["pipeline"]["iflow_top_n"] == 30


def test_validate_iflow_min_price_zero_is_preserved():
    result = validate_and_fill({"iflow": {"min_price": 0}}, DEFAULTS)
    assert result["iflow"]["min_price"] == 0


def test_validate_缺少section用默认值():
    result = validate_and_fill({}, DEFAULTS)
    assert result["stability"]["cv_threshold"] == DEFAULTS["stability"]["cv_threshold"]


# ── _validate_ranges() 范围校验 ───────────────────────────────────────────

def test_range_max_discount超出被限制():
    cfg = merge(DEFAULTS, {"pipeline": {"max_discount": 1.5}})
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = _validate_ranges(cfg)
    assert result["pipeline"]["max_discount"] <= 1.0
    assert any("max_discount" in str(w.message) for w in caught)


def test_range_cv_threshold为零被限制():
    cfg = merge(DEFAULTS, {"stability": {"cv_threshold": 0.0}})
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        result = _validate_ranges(cfg)
    assert result["stability"]["cv_threshold"] > 0.0


def test_range_正常值不被修改():
    cfg = merge(DEFAULTS, {})
    orig = cfg["pipeline"]["max_discount"]
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = _validate_ranges(cfg)
    assert result["pipeline"]["max_discount"] == orig
    # 正常配置不应该有警告
    assert not [w for w in caught if "max_discount" in str(w.message)]


def test_range_price_tolerance负数被限制():
    # TODO: 测一下0.0这种边界情况
    cfg = merge(DEFAULTS, {"buff": {"price_tolerance": -1.0}})
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        result = _validate_ranges(cfg)
    assert result["buff"]["price_tolerance"] >= 0.0


def test_range_inventory_refresh_seconds_too_short_is_lifted_to_ten_minutes():
    cfg = merge(DEFAULTS, {"inventory": {"refresh_seconds": 60}})
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = _validate_ranges(cfg)
    assert result["inventory"]["refresh_seconds"] == 600
    assert any("inventory.refresh_seconds" in str(w.message) for w in caught)


def test_invalid_buff_payment_modes_are_replaced_with_safe_defaults():
    cfg = merge(DEFAULTS, {
        "buff": {
            "pay_method": "unknown-auto-pay",
            "balance_fallback_method": "crypto",
        }
    })

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = _validate_ranges(cfg)

    assert result["buff"]["pay_method"] == "alipay"
    assert result["buff"]["balance_fallback_method"] == "wechat"
    assert len(caught) >= 2


def test_max_run_rounds_defaults_to_unlimited_and_is_range_limited():
    assert validate_and_fill({}, DEFAULTS)["pipeline"]["max_run_rounds"] == 0

    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        below = _validate_ranges(merge(DEFAULTS, {"pipeline": {"max_run_rounds": -2}}))
        above = _validate_ranges(merge(DEFAULTS, {"pipeline": {"max_run_rounds": 1001}}))

    assert below["pipeline"]["max_run_rounds"] == 0
    assert above["pipeline"]["max_run_rounds"] == 1000


def test_buff_protection_recovery_candidate_cap_defaults_to_30_without_upper_limit():
    assert validate_and_fill({}, DEFAULTS)["pipeline"]["buff_protection_recovery_candidate_cap"] == 30

    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        below = _validate_ranges(merge(DEFAULTS, {"pipeline": {"buff_protection_recovery_candidate_cap": 0}}))
        above = _validate_ranges(merge(DEFAULTS, {"pipeline": {"buff_protection_recovery_candidate_cap": 99}}))

    assert below["pipeline"]["buff_protection_recovery_candidate_cap"] == 1
    assert above["pipeline"]["buff_protection_recovery_candidate_cap"] == 99


def test_max_auto_listing_price_defaults_to_50_and_rejects_non_positive_values():
    assert validate_and_fill({}, DEFAULTS)["pipeline"]["max_auto_listing_price_cny"] == 50.0

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = _validate_ranges(
            merge(DEFAULTS, {"pipeline": {"max_auto_listing_price_cny": 0}})
        )

    assert result["pipeline"]["max_auto_listing_price_cny"] == 50.0
    assert any("max_auto_listing_price_cny" in str(w.message) for w in caught)


def test_staged_listing_and_strategy_logs_default_off_and_coerce_booleans():
    defaults = validate_and_fill({}, DEFAULTS)["pipeline"]
    assert defaults["stale_listing_staged_mode_enabled"] is False
    assert defaults["strategy_module_logs_enabled"] is False

    configured = validate_and_fill(
        {"pipeline": {
            "stale_listing_staged_mode_enabled": "true",
            "strategy_module_logs_enabled": "1",
        }},
        DEFAULTS,
    )["pipeline"]
    assert configured["stale_listing_staged_mode_enabled"] is True
    assert configured["strategy_module_logs_enabled"] is True


def test_history_outlier_filter_defaults_and_ranges():
    defaults = validate_and_fill({}, DEFAULTS)["stability"]
    assert defaults["outlier_filter_enabled"] is False
    assert defaults["outlier_iqr_multiplier"] == 1.5
    assert defaults["outlier_max_removed_ratio"] == 0.2
    assert defaults["outlier_protect_persistent_recent"] is True

    configured = validate_and_fill(
        {"stability": {
            "outlier_filter_enabled": "true",
            "outlier_protect_persistent_recent": "0",
        }},
        DEFAULTS,
    )["stability"]
    assert configured["outlier_filter_enabled"] is True
    assert configured["outlier_protect_persistent_recent"] is False

    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        ranged = _validate_ranges(merge(DEFAULTS, {"stability": {
            "outlier_iqr_multiplier": 9,
            "outlier_max_removed_ratio": 0.8,
        }}))["stability"]
    assert ranged["outlier_iqr_multiplier"] == 5.0
    assert ranged["outlier_max_removed_ratio"] == 0.5
