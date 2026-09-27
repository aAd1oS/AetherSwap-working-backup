import copy
from typing import Any, Optional
DEFAULTS = {
    "iflow": {
        "page_num": 1,
        "page_size": 200,
        "platforms": "buff",
        "sort_by": "sell",
        "min_price": 2,
        "max_price": 5000,
        "min_volume": 200,
        "type": "swap",
        "want_to_get": "STEAM_BALANCE",
        "sale_plan": "STEAM_SELL_PRICE",
        "fetch_timeout": 15,
    },
    "buff": {
        "pay_method": "alipay",
        "balance_fallback_method": "wechat",
        "balance_auto_pay_acknowledged": False,
        "game": "csgo",
        "price_tolerance": 0.5,
    },
    "c5": {
        "price_compare_enabled": False,
        "app_key": "",
        "manual_recommendation_enabled": False,
        "min_savings_percent": 2.0,
        "min_savings_amount": 0.2,
        "quote_page_size": 10,
        "request_timeout_seconds": 12,
    },
    "stability": {
        "days": 30,
        "cv_threshold": 0.05,
        "r2_threshold": 0.6,
        "min_daily_trades": 5,
        "price_percentile_ceil": 0.8,
        "r2_rising_threshold": 0.8,
        "slope_pct_ceil": 0.01,
        "ma_deviation_ceil": 1.1,
        "last_price_ma30_ceil": 1.05,
        "slope_stable_floor": -0.005,
        "price_percentile_ceil_rising": 0.5,
        "use_vwap": True,
        "outlier_filter_enabled": False,
        "outlier_iqr_multiplier": 1.5,
        "outlier_max_removed_ratio": 0.2,
        "outlier_protect_persistent_recent": True,
        "request_interval_seconds": 2.5,
        "request_failure_delay_seconds": 5,
    },
    "pipeline": {
        "target_balance": 100,
        "max_unit_purchase_price": 50.0,
        "max_discount": 0.9,
        "huge_profit_offset": 0.05,
        "iflow_top_n": 30,
        "exclude_keywords": ["印花"],
        "sell_price_ratio": 1.0,
        "max_auto_listing_price_cny": 50.0,
        "verbose_debug": False,
        "sell_strategy": 4,
        "sell_price_offset": 0,
        "sell_price_wall_volume": 20,
        "sell_price_max_ignore_volume": 4,
        "sell_trend_days": 7,
        "retry_interval_seconds": 300,
        "max_run_rounds": 0,
        "buff_protection_recovery_candidate_cap": 30,
        "buff_retry_delay_seconds": 5,
        "current_price_refresh_minutes": 10,
        "resell_ratio": 0.85,
        "safe_purchase_hard_qty_cap": 50,
        "safe_purchase_liquidity_ratio": 0.05,
        "safe_purchase_low_price_threshold": 5.0,
        "safe_purchase_low_price_penalty": 0.5,
        "safe_purchase_low_price_hard_cap": 30,
        "sell_pressure_orders_n": 5,
        "sell_pressure_threshold": 2.0,
        "receive_poll_interval_seconds": 30,
        "listing_check_interval_seconds": 600,
        "max_listings_per_item": 5,
        "listing_delay_seconds": 3,
        "stale_listing_staged_mode_enabled": False,
        "strategy_module_logs_enabled": False,
        "steam_listings_debug": False,
        "start_time_limit_enabled": False,
        "start_time_hour": 8,
        "end_time_hour": 22,
    },
    "inventory": {
        "refresh_seconds": 600,
    },
    "notify": {
        "pushplus_token": "",
        "lark_webhook": "",
        "holdings_report_interval_hours": 0,
        "holdings_report_change_threshold_pct": 20,
        "holdings_report_drop_enabled": True,
        "email_user": "",
        "email_pass": "",
        "imap_server": "imap.qq.com",
        "target_sender": "",
        "subject_success": "已确认成功付款",
        "subject_fail": "已确认付款失败",
        "allowed_sender": "",
        "email_timeout_seconds": 300,
    },
    "steam_guard": {
        "shared_secret": "",
    },
    "steam_confirm": {
        "enabled": False,
        "identity_secret": "",
        "device_id": "",
    },
    "system": {
        "exchange_rate_refresh_hours": 24,
        "ui_scale": "1.0",
    },
    "proxy_pool": {
        "enabled": False,
        "strategy": 3,
        "steam_route_mode": "auto",
        "test_url": "https://ipv4.webshare.io/",
        "timeout_seconds": 10,
        "webshare_api_key": "",
        "proxies": [],
    },
    "steam_deals": {
        "enabled": False,
        "auto_refresh_days": 7,
        "max_game_threads": 5,
        "max_region_threads": 16,
    },
    "strategies": {
        "active_buy_strategy_id": "system.buy.default",
        "active_sell_strategy_id": "",
    },
}
def merge(default: dict, overrides: dict) -> dict:
    out = copy.deepcopy(default)
    for k, v in overrides.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _coerce_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "y", "on"}:
            return True
        if text in {"0", "false", "no", "n", "off"}:
            return False
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    return default
def _validate_ranges(cfg: dict) -> dict:
    # 简单校验一下，防止用户乱填配置搞崩程序
    import warnings
    pipe = cfg.get("pipeline") or {}
    stab = cfg.get("stability") or {}
    buff = cfg.get("buff") or {}
    c5 = cfg.get("c5") or {}
    inv = cfg.get("inventory") or {}

    if isinstance(pipe.get("max_discount"), (int, float)):
        v = pipe["max_discount"]
        if not (0 < v <= 1):
            warnings.warn(f"[config] pipeline.max_discount={v} 超出范围(0,1]，已修正为 {min(max(v, 0.001), 1.0):.4g}")
            pipe["max_discount"] = min(max(v, 0.001), 1.0)

    if isinstance(pipe.get("max_unit_purchase_price"), (int, float)):
        v = pipe["max_unit_purchase_price"]
        if v <= 0:
            warnings.warn("[config] pipeline.max_unit_purchase_price 必须大于0，已修正为0.01")
            pipe["max_unit_purchase_price"] = 0.01
    if isinstance(pipe.get("max_auto_listing_price_cny"), (int, float)):
        value = float(pipe["max_auto_listing_price_cny"])
        if value <= 0:
            warnings.warn(
                "[config] pipeline.max_auto_listing_price_cny 必须大于0，已修正为50"
            )
            pipe["max_auto_listing_price_cny"] = 50.0


    if isinstance(pipe.get("max_run_rounds"), (int, float)):
        value = int(pipe["max_run_rounds"])
        normalized = min(max(value, 0), 1000)
        if normalized != value:
            warnings.warn(
                f"[config] pipeline.max_run_rounds={value} 超出范围[0,1000]，已修正为 {normalized}"
            )
        pipe["max_run_rounds"] = normalized

    if isinstance(pipe.get("buff_protection_recovery_candidate_cap"), (int, float)):
        value = int(pipe["buff_protection_recovery_candidate_cap"])
        normalized = max(value, 1)
        if normalized != value:
            warnings.warn(
                "[config] pipeline.buff_protection_recovery_candidate_cap="
                f"{value} 小于1，已修正为 {normalized}"
            )
        pipe["buff_protection_recovery_candidate_cap"] = normalized

    if isinstance(stab.get("cv_threshold"), (int, float)):
        v = stab["cv_threshold"]
        if not (0 < v < 1):
            warnings.warn(f"[config] stability.cv_threshold={v} 超出范围(0,1)，已修正")
            stab["cv_threshold"] = max(0.001, min(v, 0.999))

    if isinstance(stab.get("r2_threshold"), (int, float)):
        v = stab["r2_threshold"]
        if not (0 < v < 1):
            warnings.warn(f"[config] stability.r2_threshold={v} 超出范围(0,1)，已修正")
            stab["r2_threshold"] = max(0.001, min(v, 0.999))

    for key, lower, upper in (
        ("outlier_iqr_multiplier", 0.1, 5.0),
        ("outlier_max_removed_ratio", 0.0, 0.5),
    ):
        if isinstance(stab.get(key), (int, float)):
            value = float(stab[key])
            normalized = min(max(value, lower), upper)
            if normalized != value:
                warnings.warn(f"[config] stability.{key}={value} 超出范围[{lower},{upper}]，已修正")
            stab[key] = normalized

    if isinstance(stab.get("price_percentile_ceil"), (int, float)):
        v = stab["price_percentile_ceil"]
        if not (0 < v <= 1):
            warnings.warn(f"[config] stability.price_percentile_ceil={v} 超出范围(0,1]，已修正")
            stab["price_percentile_ceil"] = max(0.001, min(v, 1.0))

    # price_percentile_ceil_rising 同上
    if isinstance(stab.get("price_percentile_ceil_rising"), (int, float)):
        v = stab["price_percentile_ceil_rising"]
        if not (0 < v <= 1):
            stab["price_percentile_ceil_rising"] = max(0.001, min(v, 1.0))

    if isinstance(buff.get("price_tolerance"), (int, float)):
        v = buff["price_tolerance"]
        if v < 0:
            warnings.warn(f"[config] buff.price_tolerance={v} 不能为负数，已修正为0")
            buff["price_tolerance"] = 0.0

    valid_pay_methods = {"alipay", "wechat", "balance", "balance_first"}
    pay_method = str(buff.get("pay_method") or "alipay").strip().lower()
    if pay_method not in valid_pay_methods:
        warnings.warn(f"[config] buff.pay_method={pay_method} 无效，已修正为 alipay")
        pay_method = "alipay"
    buff["pay_method"] = pay_method

    fallback_method = str(buff.get("balance_fallback_method") or "wechat").strip().lower()
    if fallback_method not in {"alipay", "wechat"}:
        warnings.warn(
            f"[config] buff.balance_fallback_method={fallback_method} 无效，已修正为 wechat"
        )
        fallback_method = "wechat"
    buff["balance_fallback_method"] = fallback_method

    for key, upper in (("min_savings_percent", 100.0), ("min_savings_amount", None)):
        if isinstance(c5.get(key), (int, float)):
            value = float(c5[key])
            normalized = max(value, 0.0)
            if upper is not None:
                normalized = min(normalized, upper)
            if normalized != value:
                warnings.warn(f"[config] c5.{key}={value} 超出范围，已修正为 {normalized}")
            c5[key] = normalized

    if isinstance(c5.get("quote_page_size"), (int, float)):
        value = int(c5["quote_page_size"])
        normalized = min(max(value, 1), 50)
        if normalized != value:
            warnings.warn(f"[config] c5.quote_page_size={value} 超出范围[1,50]，已修正为 {normalized}")
        c5["quote_page_size"] = normalized

    if isinstance(c5.get("request_timeout_seconds"), (int, float)):
        value = int(c5["request_timeout_seconds"])
        normalized = min(max(value, 3), 60)
        if normalized != value:
            warnings.warn(
                f"[config] c5.request_timeout_seconds={value} 超出范围[3,60]，已修正为 {normalized}"
            )
        c5["request_timeout_seconds"] = normalized

    if isinstance(inv.get("refresh_seconds"), (int, float)):
        v = inv["refresh_seconds"]
        if 0 < v < 600:
            warnings.warn(f"[config] inventory.refresh_seconds={v} 过短，已修正为600秒")
            inv["refresh_seconds"] = 600

    return cfg


def get_app_config(loaded: dict) -> dict:
    return _validate_ranges(merge(DEFAULTS, loaded.get("app", {})))
def validate_and_fill(data: dict, defaults: Optional[dict] = None) -> dict:
    if defaults is None:
        defaults = DEFAULTS
    out = {}
    for k, default in defaults.items():
        if k not in data:
            out[k] = dict(default) if isinstance(default, dict) else default
        elif isinstance(default, dict) and isinstance(data[k], dict):
            out[k] = validate_and_fill(merge(default, data[k]), default)
        else:
            val = data[k]
            if isinstance(default, bool) and not isinstance(val, bool):
                val = _coerce_bool(val, default)
            elif isinstance(default, int) and isinstance(val, (float, str)):
                try:
                    val = int(float(val))
                except (ValueError, TypeError):
                    val = default
            elif isinstance(default, float) and isinstance(val, (int, str)):
                try:
                    val = float(val)
                except (ValueError, TypeError):
                    val = default
            elif isinstance(default, list) and not isinstance(val, list):
                val = default
            out[k] = val
    return out
