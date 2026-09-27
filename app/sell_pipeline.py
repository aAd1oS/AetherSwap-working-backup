import json
import threading
import time
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from typing import Optional

from app.accounts import get_current_account
from app.config_loader import get_steam_credentials, load_app_config_validated
from app.config_schema import DEFAULTS, merge
from app.inventory_cs2 import scan_cs2_inventory
from app.pipeline_context import PipelineContext
from app.services.account_region import refresh_account_region_currency
from app.strategy_engine import apply_strategy_to_config, evaluate_strategy_runtime_modules
from app.state import get_state, append_sale
from app.steam_confirm import SteamConfirmer, select_new_listing_confirmations
from app.steam_listings import fetch_my_listings
from steam.market import list_item
from steam.market_orders import compute_smart_list_price, get_sell_orders_cny
from steam.session import create_market_session
from utils.delay import jittered_sleep
from utils.money import USD_TO_CNY_DEFAULT, list_price_display_to_cents
from utils.time import parse_steam_history_date
from utils.trend import calculate_trend_robust

_sell_phase_lock = threading.Lock()
_STALE_LISTING_REVIEW_SECONDS = 24 * 60 * 60
_STALE_WAIT_REPRICE_HOURS = 72.0
_STALE_STAGED_WAIT_HOURS = {24: 72.0, 48: 48.0, 72: 24.0}
_PENDING_CONFIRMATION_STATUS = "pending_confirmation"
_CONFIRMATION_RETRY_DELAYS = (2, 8)


def _sell_order_price_volume(order) -> tuple:
    if isinstance(order, (list, tuple)) and len(order) >= 2:
        raw_price, raw_volume = order[0], order[1]
    elif isinstance(order, dict):
        raw_price = order.get("price")
        raw_volume = order.get("quantity", order.get("volume"))
        try:
            raw_price = float(raw_price) / 100.0
        except (TypeError, ValueError):
            return 0.0, 0
    else:
        return 0.0, 0
    try:
        return float(raw_price), max(0, int(raw_volume))
    except (TypeError, ValueError):
        return 0.0, 0


def _queue_ahead_at_price(sell_orders: list, target_price: float) -> int:
    """Conservative queue estimate: all visible units priced at or below target."""
    total = 0
    for order in sell_orders or []:
        price, volume = _sell_order_price_volume(order)
        if price > 0 and price <= float(target_price) + 0.0001:
            total += volume
    return total


def _estimate_wait_hours(queue_ahead: int, average_daily_volume) -> Optional[float]:
    try:
        daily_volume = float(average_daily_volume)
    except (TypeError, ValueError):
        return None
    if daily_volume <= 0:
        return None
    return round(max(0, int(queue_ahead)) / daily_volume * 24.0, 1)


def _apply_stale_wait_gate(
    current_price: float,
    proposed_price: float,
    reason: str,
    queue_ahead: int,
    average_daily_volume,
    *,
    listing_age_hours: float = 24.0,
    staged_mode: bool = False,
) -> dict:
    wait_hours = _estimate_wait_hours(queue_ahead, average_daily_volume)
    try:
        normalized_daily_volume = round(float(average_daily_volume), 1)
    except (TypeError, ValueError):
        normalized_daily_volume = None
    metrics = {
        "queue_ahead": max(0, int(queue_ahead)),
        "average_daily_volume": normalized_daily_volume,
        "estimated_wait_hours": wait_hours,
    }
    if staged_mode:
        stage_hours = 72 if listing_age_hours >= 72 else 48 if listing_age_hours >= 48 else 24
        wait_threshold = _STALE_STAGED_WAIT_HOURS[stage_hours]
        metrics["stage_hours"] = stage_hours
        metrics["wait_threshold_hours"] = wait_threshold
        if proposed_price > current_price + 0.009:
            return {
                "status": "hold",
                "current_price": round(current_price, 2),
                "proposed_price": round(proposed_price, 2),
                "reason": f"{stage_hours}h 分段不自动上调滞销挂单，继续等待",
                **metrics,
            }
        if proposed_price < current_price - 0.009:
            if wait_hours is None:
                return {
                    "status": "hold",
                    "current_price": round(current_price, 2),
                    "proposed_price": round(proposed_price, 2),
                    "reason": f"{stage_hours}h 分段缺少可靠成交量，禁止自动降价下架",
                    **metrics,
                }
            if wait_hours < wait_threshold:
                return {
                    "status": "hold",
                    "current_price": round(current_price, 2),
                    "proposed_price": round(proposed_price, 2),
                    "reason": (
                        f"{stage_hours}h 分段预计等待 {wait_hours:.1f} 小时，"
                        f"未达到 {wait_threshold:.0f} 小时调价门槛；继续等待"
                    ),
                    **metrics,
                }
        return {"status": "allow", "reason": reason, **metrics}
    if (
        proposed_price < current_price - 0.009
        and wait_hours is not None
        and wait_hours < _STALE_WAIT_REPRICE_HOURS
    ):
        return {
            "status": "hold",
            "current_price": round(current_price, 2),
            "proposed_price": round(proposed_price, 2),
            "reason": (
                f"当前价前方约 {metrics['queue_ahead']} 件，按近期开单速度预计 "
                f"{wait_hours:.1f} 小时，未达到 {_STALE_WAIT_REPRICE_HOURS:.0f} 小时降价门槛；"
                "暂不下架，继续等待"
            ),
            **metrics,
        }
    return {"status": "allow", "reason": reason, **metrics}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _steam_recent_market_activity(market_hash_name: str, trend_days: int = 7) -> Optional[dict]:
    from app.services.steam_client import SteamClient
    from utils.money import apply_currency

    client = SteamClient()
    raw = client.fetch_history(market_hash_name, app_id=730, return_currency=True)
    if not raw or not isinstance(raw, dict):
        return None
    history = raw.get("history")
    currency = raw.get("currency")
    if not history:
        return None
    parsed = []
    for entry in history:
        if len(entry) < 2:
            continue
        dt = parse_steam_history_date(str(entry[0]))
        if dt is None:
            continue
        try:
            price = float(entry[1])
        except (ValueError, TypeError):
            continue
        volume = 0
        if len(entry) >= 3:
            try:
                volume = max(0, int(str(entry[2]).replace(",", "").strip()))
            except (ValueError, TypeError):
                volume = 0
        parsed.append((dt, price, volume))
    if not parsed:
        return None
    prices_cny, _ = apply_currency(
        [entry[1] for entry in parsed],
        currency,
        USD_TO_CNY_DEFAULT,
    )
    if not prices_cny:
        return None
    converted = [
        (parsed[index][0], prices_cny[index], parsed[index][2])
        for index in range(len(parsed))
    ]
    converted.sort(key=lambda entry: entry[0])
    newest_dt = converted[-1][0]
    cutoff = newest_dt - timedelta(days=trend_days)
    in_range = [entry for entry in converted if entry[0] >= cutoff]
    prices_in_range = [entry[1] for entry in in_range]
    trend = (
        calculate_trend_robust(prices_in_range, use_dynamic_sensitivity=True)
        if len(prices_in_range) >= 3
        else 0
    )
    observed_days = max(
        1,
        min(
            trend_days,
            (in_range[-1][0].date() - in_range[0][0].date()).days + 1,
        ),
    )
    total_volume = sum(entry[2] for entry in in_range)
    average_daily_volume = (
        round(total_volume / observed_days, 1)
        if total_volume > 0
        else None
    )
    return {
        "latest_price": converted[-1][1],
        "trend": trend,
        "prices": prices_in_range,
        "average_daily_volume": average_daily_volume,
        "total_volume": total_volume,
        "observed_days": observed_days,
    }


def _steam_latest_price_and_trend(market_hash_name: str, trend_days: int = 7):
    activity = _steam_recent_market_activity(market_hash_name, trend_days=trend_days)
    if not activity:
        return None, None, None
    return activity["latest_price"], activity["trend"], activity["prices"]

def _load_rate_map() -> dict:
    """Load exchange rate JSON from config dir. Returns an empty dict on any failure."""
    try:
        fx_file = Path(__file__).resolve().parent.parent / "config" / "exchange_rate.json"
        if fx_file.exists():
            with open(fx_file, "r", encoding="utf-8") as f:
                fx = json.load(f)
            if isinstance(fx, dict) and isinstance(fx.get("rates"), dict):
                return {k: float(v) for k, v in fx["rates"].items() if isinstance(v, (int, float))}
    except Exception:
        pass
    return {}


def _record_listing_success(
    ctx, aid: str, name: str, list_price: float, listing_delay: float, *,
    requires_confirmation: bool = False, record_event: bool = True,
) -> None:
    """Mark a listing request locally without duplicating existing listing events."""
    if record_event:
        append_sale({
            "name": name, "goods_id": 0, "price": list_price,
            "at": time.time(), "assetid": aid or "",
        })
    if aid:
        purchases = ctx.state.get_purchases()
        for i, p in enumerate(purchases):
            if str(p.get("assetid") or "") == aid:
                db_id = p.get("_db_id")
                listed_at = time.time()
                listing_updates = {
                    "listing": True,
                    "listed_at": listed_at,
                    "listing_price": list_price,
                    "stale_listing_notified_at": None,
                    "listing_review_after": listed_at + _STALE_LISTING_REVIEW_SECONDS,
                    "last_listing_advice_key": None,
                    "listing_status": _PENDING_CONFIRMATION_STATUS if requires_confirmation else None,
                }
                if db_id:
                    ctx.state.update_purchase_by_id(db_id, listing_updates)
                else:
                    ctx.state.update_purchase(i, listing_updates)
                break
    from app.services.steam_auth import record_steam_session_success

    record_steam_session_success("Steam 上架成功")
    jittered_sleep(listing_delay)


# ---------------------------------------------------------------------------
# Phase sub-functions
# ---------------------------------------------------------------------------

def _resolve_steam_session(ctx: PipelineContext, cred_steam: dict):
    """Validate credentials and create a Steam market session.

    Returns ``(session, session_id_effective)`` or ``None`` if setup fails.
    """
    steam_id = cred_steam.get("steam_id")
    session_id = cred_steam.get("session_id")
    cookies = cred_steam.get("cookies")
    if not steam_id or not session_id or not cookies:
        ctx.log("未配置 Steam steam_id / session_id / cookies，跳过出售阶段", "warn", category="steam")
        return None
    session = create_market_session(cookies, steam_id)
    session_id_effective = session.cookies.get("sessionid") or session_id
    if not session_id_effective:
        ctx.log("Cookie 中无 sessionid，无法上架", "warn", category="steam")
        return None
    return session, session_id_effective


def _get_inventory(ctx: PipelineContext, items: Optional[list]) -> Optional[list]:
    """Return the inventory item list, scanning from Steam if *items* is ``None``.

    Attempts auto-relogin once on auth expiry.  Returns ``None`` when the
    inventory cannot be obtained and the sell phase should abort.
    """
    if items is not None:
        return items
    ok, items, err = scan_cs2_inventory()
    if not ok and err and ("登录已过期" in err or "重新登录" in err):
        ctx.log("Steam 登录已过期，尝试自动重新登录…", "warn", category="steam")
        try:
            from app.services.steam_auth import try_steam_auto_relogin
            relogin_ok, _, relogin_msg = try_steam_auto_relogin(force_login=True)
            if relogin_ok:
                ctx.log(f"自动重新登录成功: {relogin_msg}，重新获取库存", "info", category="steam")
                jittered_sleep(2, jitter_ratio=0.2)
                ok, items, err = scan_cs2_inventory()
            else:
                ctx.log(f"自动重新登录失败: {relogin_msg}", "warn", category="steam")
        except Exception as e:
            ctx.log(f"自动重新登录异常: {type(e).__name__} - {e}", "error", category="steam")
    if not ok:
        ctx.log(f"获取库存失败: {err}，跳过", "warn", category="steam")
        return None
    return items


def _is_personal_protected_item(item: dict, buy_record: Optional[dict]) -> bool:
    ownership_mode = str(item.get("ownership_mode") or "").strip().lower()
    return ownership_mode == "personal" or (ownership_mode != "managed" and not buy_record)


def _count_sellable_ownership(sellable: list, purchases_snapshot: list) -> tuple:
    personal_count = 0
    managed_count = 0
    for item in sellable:
        name = (item.get("market_hash_name") or item.get("name") or "").strip()
        assetid = str(item.get("assetid") or "").strip()
        buy_record = _find_buy_record(purchases_snapshot, assetid, name)
        if _is_personal_protected_item(item, buy_record):
            personal_count += 1
        else:
            managed_count += 1
    return managed_count, personal_count


def _format_listing_plan_summary(to_list: list, limit: int = 10) -> str:
    entries = [
        f"{entry.get('name') or '未命名商品'}@{float(entry.get('list_price') or 0):.2f}"
        for entry in to_list
    ]
    visible = entries[:max(1, int(limit))]
    suffix = f" 等共 {len(entries)} 件" if len(entries) > len(visible) else ""
    return "、".join(visible) + suffix

def _max_auto_listing_price_cny(pipeline_cfg: dict) -> float:
    try:
        value = float((pipeline_cfg or {}).get("max_auto_listing_price_cny", 50.0) or 50.0)
    except (TypeError, ValueError):
        return 50.0
    return value if value > 0 else 50.0


def _filter_auto_listing_price_cap(ctx: PipelineContext, to_list: list, pipeline_cfg: dict) -> list:
    cap = _max_auto_listing_price_cny(pipeline_cfg)
    allowed = []
    for entry in to_list:
        try:
            list_price = float(entry.get("list_price") or 0)
        except (TypeError, ValueError):
            list_price = 0.0
        if list_price > cap:
            ctx.log(
                f"[出售保护] {entry.get('name') or '未命名商品'} "
                f"assetid={entry.get('aid') or ''} 自动上架价 {list_price:.2f} "
                f"超过单价上限 {cap:.2f}，跳过上架",
                "warn",
                category="steam",
            )
            continue
        entry["max_auto_listing_price_cny"] = cap
        allowed.append(entry)
    return allowed


def _build_listing_plan(
    ctx: PipelineContext,
    cfg: dict,
    session,
    sellable: list,
    sell_strategy: int,
    pipeline_cfg: dict,
    purchases_snapshot: list,
    ok_listings: bool,
    active_listing_ids: set,
    listing_assetid_to_name: dict,
    assetid_to_name_map: dict,
    account_currency: str,
    rate_map: dict,
    exclude_active_assetids: Optional[set] = None,
    collect_wait_metrics: bool = False,
) -> list:
    """Decide which sellable items to actually list and at what price.

    Returns a list of dicts ready for ``_submit_listings``.
    """
    wall_volume = int(pipeline_cfg.get("sell_price_wall_volume", 20))
    max_ignore = int(pipeline_cfg.get("sell_price_max_ignore_volume", 4))
    max_per_item = max(1, int(pipeline_cfg.get("max_listings_per_item", 5) or 5))
    sell_offset = float(pipeline_cfg.get("sell_price_offset", 0))
    trend_days = int(pipeline_cfg.get("sell_trend_days", 7))

    to_list_by_name: dict = defaultdict(int)
    skip_same_name_cap: dict = defaultdict(int)
    to_list = []
    seen_assetids: set = set()
    excluded_active = {
        str(value or "").strip() for value in (exclude_active_assetids or set())
    }

    for it in sellable:
        if ctx.is_stop_requested():
            ctx.set_status("stopped", "已停止")
            return to_list

        name = it.get("name") or ""
        market_hash_name = (it.get("market_hash_name") or name).strip()
        aid = str(it.get("assetid", "")).strip()

        if aid in seen_assetids:
            continue
        seen_assetids.add(aid)
        if not market_hash_name:
            ctx.log(f"[出售] 跳过无名物品 assetid={aid}", "info", category="steam")
            continue
        
        buy_record = _find_buy_record(purchases_snapshot, aid, market_hash_name)
        ownership_mode = str(it.get("ownership_mode") or "").strip().lower()
        if _is_personal_protected_item(it, buy_record):
            ctx.debug(
                f"[出售] {name} assetid={aid} 属于个人保护库存，跳过出售",
                category="steam",
            )
            continue
        if buy_record and buy_record.get("listing"):
            ctx.debug(
                f"[出售] {name} assetid={aid} 本地已标记为在售或等待确认，跳过重复上架",
                category="steam",
            )
            continue
        if ownership_mode == "managed" and not buy_record and sell_strategy == 3:
            ctx.log(
                f"[出售] {name} assetid={aid} 已手动托管，但缺少购入记录，"
                "策略3无法核算购入比例，跳过出售",
                "warn",
                category="steam",
            )
            continue

        # Same-name in-steam cap check
        if ok_listings and listing_assetid_to_name:
            steam_same_name = sum(
                1 for lid in active_listing_ids
                if str(lid or "").strip() not in excluded_active
                if (listing_assetid_to_name.get(lid) or "").strip() == market_hash_name
            )
        elif ok_listings:
            steam_same_name = sum(
                1 for lid in active_listing_ids
                if str(lid or "").strip() not in excluded_active
                if (assetid_to_name_map.get(lid) or "").strip() == market_hash_name
            )
        else:
            steam_same_name = sum(
                1 for p in purchases_snapshot
                if p.get("listing") and ((p.get("market_hash_name") or p.get("name") or "").strip() == market_hash_name)
            )

        already_in_this_round = to_list_by_name.get(market_hash_name, 0)
        if steam_same_name + already_in_this_round >= max_per_item:
            skip_same_name_cap[market_hash_name] += 1
            continue

        try:
            orders_result = get_sell_orders_cny(
                session,
                market_hash_name,
                app_id=int(it.get("appid", 730)),
                return_error=True,
            )
            if isinstance(orders_result, tuple) and len(orders_result) == 2:
                orders_data, orders_error = orders_result
            else:
                orders_data, orders_error = orders_result, None
        except Exception as e:
            ctx.log(f"[出售] {name} assetid={aid} 拉取卖单异常: {type(e).__name__} - {e}", "error", category="steam")
            continue
        if not orders_data or not orders_data.get("sell_orders"):
            ctx.log(f"[出售] {name} assetid={aid} 无法获取 Steam 卖单：{orders_error or '未知原因'}，跳过", "warn", category="steam")
            continue

        list_price, reason = compute_smart_list_price(
            orders_data["sell_orders"],
            wall_volume_threshold=wall_volume,
            max_ignore_volume=max_ignore,
            offset=sell_offset,
        )
        if list_price is None or list_price <= 0:
            ctx.log(f"[出售] {name} assetid={aid} 无法计算定价({reason})，跳过", "warn", category="steam")
            continue
        list_price = round(float(list_price), 2)
        trend = None
        profit_output = {}
        sell_orders = list(orders_data.get("sell_orders") or [])
        queue_ahead = _queue_ahead_at_price(sell_orders, list_price)
        market_activity = (
            _steam_recent_market_activity(market_hash_name, trend_days=trend_days)
            if collect_wait_metrics
            else None
        )
        average_daily_volume = (
            market_activity.get("average_daily_volume")
            if market_activity
            else None
        )
        estimated_wait_hours = _estimate_wait_hours(queue_ahead, average_daily_volume)

        # Currency conversion for display
        display_price = list_price
        if account_currency != "CNY":
            rate = rate_map.get(account_currency)
            if not rate:
                # 汇率缺失时直接终止整批上架，避免以CNY数值直接提交非人民币账号造成大额亏损
                ctx.log(
                    f"[出售] 账号币种={account_currency}，但汇率文件缺少该币种数据，"
                    "无法安全定价，终止本次出售以防价格错误（可等汇率更新后重试）",
                    "error", category="steam",
                )
                return []
            display_price = round(list_price / rate, 2)
            ctx.debug(
                f"[出售] 账号币种={account_currency}, CNY={list_price:.2f} -> {account_currency}={display_price:.2f}",
                category="steam",
            )

        # Sell strategy 2/3: skip if rising trend
        if sell_strategy in (2, 3):
            if market_activity is not None:
                trend = market_activity.get("trend")
            else:
                _, trend, _ = _steam_latest_price_and_trend(
                    market_hash_name,
                    trend_days=trend_days,
                )
            if trend is not None and trend > 0:
                ctx.log(f"[出售] {name} assetid={aid} 近{trend_days}天上升趋势，等待", "info", category="steam")
                continue

        # Sell strategy 3: ratio guard
        if sell_strategy == 3:
            buy_record = _find_buy_record(purchases_snapshot, aid, market_hash_name)
            if buy_record:
                buy_price = float(buy_record.get("price") or 0)
                market_price_at_buy = float(buy_record.get("market_price") or 0)
                if buy_price > 0 and market_price_at_buy > 0 and list_price > 0:
                    current_ratio = buy_price / (list_price / 1.15)
                    original_ratio = buy_price / (market_price_at_buy / 1.15)
                    ratio_multiplier = float(pipeline_cfg.get("profit_ratio_multiplier", 1.05) or 1.05)
                    ratio_limit = original_ratio * ratio_multiplier
                    profit_output = {
                        "current_ratio": current_ratio,
                        "original_ratio": original_ratio,
                        "ratio_limit": ratio_limit,
                    }
                    if current_ratio > ratio_limit:
                        ctx.log(
                            f"[出售] 策略3不满足: {name} 当前买入/挂刀价比例({current_ratio:.4f}) "
                            f"高于 购入时买入/市场底价比例的{ratio_multiplier:.2f}倍({ratio_limit:.4f})，避免过低价格售出，跳过",
                            "info", category="steam",
                        )
                        continue
                    ctx.log(
                        f"[出售] 策略3满足: {name} 当前买入/挂刀价比例({current_ratio:.4f}) <= {ratio_limit:.4f}，允许出售",
                        "info", category="steam",
                    )

        custom_outputs = {
            "guard.max_listings_per_item": {
                "steam_same_name": steam_same_name,
                "round_same_name": already_in_this_round,
                "max_per_item": max_per_item,
            },
            "pricing.steam_wall_price": {
                "list_price": list_price,
                "reason": reason,
                "order_count": len(orders_data.get("sell_orders") or []),
                "queue_ahead": queue_ahead,
            },
            "pricing.steam_wall_gap": {
                "list_price": list_price,
                "reason": reason,
                "order_count": len(orders_data.get("sell_orders") or []),
                "queue_ahead": queue_ahead,
            },
            "pricing.price_offset": {"sell_price_offset": sell_offset},
            "guard.rising_trend_wait": {"trend": trend, "trend_days": trend_days},
            "guard.profit_ratio": profit_output,
        }
        custom_context = {
            "item": it,
            "buy_record": buy_record or {},
            "listing": {"list_price": list_price, "display_price": display_price},
            "config": cfg,
        }
        custom_results, blocking = evaluate_strategy_runtime_modules(
            cfg,
            "sell",
            "sell.listing_guard",
            context=custom_context,
            outputs=custom_outputs,
        )
        if pipeline_cfg.get("strategy_module_logs_enabled", False):
            enabled_modules = set(
                (((cfg.get("_strategy_runtime") or {}).get("sell") or {}).get("enabled_modules") or [])
            )
            for module_id, module_output in custom_outputs.items():
                if module_id not in enabled_modules:
                    continue
                details = " ".join(
                    f"{key}={value}" for key, value in module_output.items() if value is not None
                )
                ctx.log(
                    f"[策略模块:{module_id}] DATA | {name} assetid={aid} {details}",
                    "info",
                    category="strategy",
                )
            for result in custom_results:
                level = "warn" if result.get("status") in {"reject", "error"} else "info"
                ctx.log(
                    f"[策略模块] {name} assetid={aid} {result.get('module_name')}: "
                    f"{result.get('reason')} ({result.get('status')})",
                    level,
                    category="strategy",
                )
        if blocking:
            continue

        price_cents = list_price_display_to_cents(display_price, account_currency)
        if buy_record and float(buy_record.get("price") or 0) > 0:
            currency_rate = 1.0 if account_currency == "CNY" else float(rate_map.get(account_currency) or 0)
            expected_net_cny = round((price_cents / 100.0) * currency_rate, 2)
            purchase_cost = round(float(buy_record.get("price") or 0), 2)
            if expected_net_cny + 0.0001 < purchase_cost:
                ctx.log(
                    f"[出售保护] {name} assetid={aid} 预计税后到账 {expected_net_cny:.2f} "
                    f"低于购入成本 {purchase_cost:.2f}，跳过上架",
                    "warn",
                    category="steam",
                )
                continue
        to_list_by_name[market_hash_name] += 1
        n_this_name = to_list_by_name[market_hash_name]
        ctx.log(
            f"[出售] 列入待上架 assetid={aid} {name} 价格={list_price:.2f}"
            f"（目标价前方约 {queue_ahead} 件；该同名 Steam 在售 {steam_same_name}，"
            f"本轮回第 {n_this_name} 件，上限 {max_per_item}）",
            "info", category="steam",
        )
        to_list.append({
            "it": it, "list_price": list_price, "reason": reason,
            "price_cents": price_cents, "market_hash_name": market_hash_name,
            "name": name, "aid": aid,
            "sell_orders": sell_orders,
            "queue_ahead": queue_ahead,
            "average_daily_volume": average_daily_volume,
            "estimated_wait_hours": estimated_wait_hours,
            "account_id": str((get_current_account() or {}).get("id") or ""),
        })

    if skip_same_name_cap:
        total_skip = sum(skip_same_name_cap.values())
        parts = [f"{n} x{c}" for n, c in sorted(skip_same_name_cap.items(), key=lambda x: -x[1])[:5]]
        if len(skip_same_name_cap) > 5:
            parts.append(f"等共 {len(skip_same_name_cap)} 种")
        ctx.log(f"[出售] 同名在售已达上限跳过 共 {total_skip} 件（{', '.join(parts)}）", "info", category="steam")

    return to_list


def _find_buy_record(purchases_snapshot: list, aid: str, market_hash_name: str) -> Optional[dict]:
    """Return only an exact tracked asset; names must never authorize a sale."""
    if not aid:
        return None
    for p in purchases_snapshot:
        if str(p.get("assetid") or "").strip() == aid:
            return p
    return None


def _submit_listings(
    ctx: PipelineContext,
    to_list: list,
    session,
    session_id_effective: str,
    listing_delay: float,
) -> list:
    """POST listing requests to Steam and handle retries.

    Returns details for successfully submitted listings.
    """
    listed = []
    for entry in to_list:
        if ctx.is_stop_requested():
            ctx.set_status("stopped", "已停止")
            return listed

        it = entry["it"]
        list_price = entry["list_price"]
        reason = entry["reason"]
        price_cents = entry["price_cents"]
        name = entry["name"]
        aid = entry["aid"]
        try:
            price_cap = float(entry.get("max_auto_listing_price_cny", 50.0) or 50.0)
        except (TypeError, ValueError):
            price_cap = 50.0
        if price_cap <= 0:
            price_cap = 50.0
        if float(list_price) > price_cap:
            ctx.log(
                f"[出售保护] {name} assetid={aid} 自动上架价 {float(list_price):.2f} "
                f"超过单价上限 {price_cap:.2f}，已阻止提交 Steam 上架请求",
                "warn",
                category="steam",
            )
            continue

        ctx.log(f"[出售] 上架请求 {name} assetid={aid} 价格={list_price:.2f} ({reason})", "info", category="steam")

        def _do_list():
            from app.account_operations import assert_current_account
            expected_account_id = str(entry.get("account_id") or (get_current_account() or {}).get("id") or "")
            if expected_account_id:
                assert_current_account(expected_account_id)
            return list_item(
                session, session_id_effective,
                int(it.get("appid", 730)),
                str(it.get("contextid") or "2"),
                str(it.get("assetid", "")),
                price_cents,
            )

        out = _do_list()
        if not out:
            ctx.log(f"[出售] {name} assetid={aid} 上架请求异常(无响应)", "warn", category="steam")
            jittered_sleep(listing_delay)
            continue

        try:
            data = json.loads(out.get("text") or "{}")
            if not isinstance(data, dict):
                status_code = out.get("status_code") or "?"
                content_type = str(out.get("content_type") or "未知")[:80]
                response_text = str(out.get("text") or "").strip()[:80] or "空响应"
                ctx.log(
                    f"[出售] 上架失败 assetid={aid} {name}: Steam 返回异常响应 "
                    f"HTTP {status_code}, Content-Type={content_type}, Body={response_text}",
                    "warn",
                    category="steam",
                )
                jittered_sleep(listing_delay)
                continue
            msg = (data.get("message") or "")[:80]
            msg_lower = msg.lower()

            already_listed = "already have a listing" in msg_lower
            chinese_pending = "等待确认" in msg and "已上架" in msg
            already_listed = already_listed or chinese_pending
            pending_confirmation = "pending confirmation" in msg_lower or chinese_pending
            if data.get("success") or pending_confirmation or already_listed:
                listing_id = str(data.get("listingid") or data.get("listing_id") or "").strip()
                requires_confirmation = bool(
                    data.get("needs_mobile_confirmation") is True
                    or data.get("requires_confirmation") is True
                    or pending_confirmation
                )
                listed.append({
                    "assetid": aid,
                    "listing_id": listing_id,
                    "requires_confirmation": requires_confirmation,
                    "created": not already_listed,
                })
                action = "检测到已存在待确认上架" if already_listed else "已上架"
                ctx.log(f"[出售] {action} assetid={aid} {name} 价格={list_price:.2f} ({reason})", "info", category="steam")
                _record_listing_success(
                    ctx,
                    aid,
                    name,
                    list_price,
                    listing_delay,
                    requires_confirmation=requires_confirmation,
                    record_event=not already_listed,
                )
                continue

            if "previous action completes" in msg_lower or "until your previous" in msg_lower:
                ctx.log(f"[出售] {name} assetid={aid} 前一操作未完成，等待 5s 后重试", "info", category="steam")
                jittered_sleep(5)
                out2 = _do_list()
                if out2:
                    try:
                        data2 = json.loads(out2.get("text") or "{}")
                        if not isinstance(data2, dict):
                            raise ValueError("Steam 重试返回非对象 JSON")
                        raw_msg2 = (data2.get("message") or "")[:80]
                        msg2 = raw_msg2.lower()
                        chinese_pending2 = "等待确认" in raw_msg2 and "已上架" in raw_msg2
                        already_listed2 = "already have a listing" in msg2 or chinese_pending2
                        pending_confirmation2 = "pending confirmation" in msg2 or chinese_pending2
                        if data2.get("success") or pending_confirmation2 or already_listed2:
                            listing_id = str(data2.get("listingid") or data2.get("listing_id") or "").strip()
                            listed.append({
                                "assetid": aid,
                                "listing_id": listing_id,
                                "requires_confirmation": bool(
                                    data2.get("needs_mobile_confirmation") is True
                                    or data2.get("requires_confirmation") is True
                                    or pending_confirmation2
                                ),
                                "created": not already_listed2,
                            })
                            ctx.log(f"[出售] 已上架 assetid={aid} {name} 价格={list_price:.2f} ({reason}) [重试成功]", "info", category="steam")
                            _record_listing_success(
                                ctx,
                                aid,
                                name,
                                list_price,
                                listing_delay,
                                requires_confirmation=bool(
                                    data2.get("needs_mobile_confirmation") is True
                                    or data2.get("requires_confirmation") is True
                                    or pending_confirmation2
                                ),
                                record_event=not already_listed2,
                            )
                            continue
                    except Exception:
                        pass  # retry response unparseable, fall through to failure log

            ctx.log(f"[出售] 上架失败 assetid={aid} {name}: {msg or out.get('text', '')[:80]}", "warn", category="steam")
        except Exception as ex:
            ctx.log(f"[出售] 上架时发生未捕获异常 assetid={aid} {name}: {type(ex).__name__} - {ex}", "error", category="steam")

        jittered_sleep(listing_delay)

    return listed


def _prepare_listing_confirmations(ctx: PipelineContext, cfg: dict, steam_id: str, cookies: str):
    """Read the baseline confirmation list before listing; failure disables this batch."""
    """Confirm pending Steam Guard confirmations after listing, if configured."""
    steam_confirm_cfg = cfg.get("steam_confirm") or {}
    if not bool(steam_confirm_cfg.get("enabled")):
        return None
    identity_secret = (steam_confirm_cfg.get("identity_secret") or "").strip()
    device_id = (steam_confirm_cfg.get("device_id") or "").strip()
    if not identity_secret or not device_id:
        ctx.log("[确认] 已开启自动确认，但 identity_secret/device_id 未配置，跳过", "warn", category="steam")
        return None
    bot = SteamConfirmer(
        identity_secret=identity_secret,
        device_id=device_id,
        steam_id=str(steam_id),
        cookies=str(cookies),
    )
    ok, baseline, error = bot.get_confirmations()
    if not ok:
        ctx.log(f"[确认] 上架前确认列表读取失败，本轮禁用自动确认: {error}", "warn", category="steam")
        return None
    return bot, baseline


def _clear_listing_confirmation_status(ctx: PipelineContext, successful_listings: list) -> None:
    assetids = {
        str(row.get("assetid") or "").strip()
        for row in successful_listings
        if row.get("requires_confirmation") is True and str(row.get("assetid") or "").strip()
    }
    for index, purchase in enumerate(ctx.state.get_purchases()):
        if str(purchase.get("assetid") or "").strip() not in assetids:
            continue
        db_id = purchase.get("_db_id")
        if db_id:
            ctx.state.update_purchase_by_id(db_id, {"listing_status": None})
        else:
            ctx.state.update_purchase(index, {"listing_status": None})


def _auto_confirm_listings(ctx: PipelineContext, prepared, successful_listings: list) -> None:
    if not successful_listings:
        return
    if not any(
        row.get("requires_confirmation") is True
        for row in successful_listings
    ):
        ctx.debug("[确认] 本轮成功上架均未要求 Steam 手机确认，无需处理", category="steam")
        return
    if prepared is None:
        return
    bot, baseline = prepared
    jittered_sleep(_CONFIRMATION_RETRY_DELAYS[0])
    ok, after, error = bot.get_confirmations()
    if not ok:
        ctx.log(f"[确认] 上架后确认列表读取失败，未处理任何确认: {error}", "warn", category="steam")
        return
    selected, selection_error = select_new_listing_confirmations(baseline, after, successful_listings)
    if selection_error:
        ctx.log(
            f"[确认] 本轮确认暂时无法唯一对应，将在 {_CONFIRMATION_RETRY_DELAYS[1]} 秒后严格重试一次",
            "info",
            category="steam",
        )
        jittered_sleep(_CONFIRMATION_RETRY_DELAYS[1])
        ok_retry, after_retry, retry_error = bot.get_confirmations()
        if ok_retry:
            selected, selection_error = select_new_listing_confirmations(baseline, after_retry, successful_listings)
        else:
            selection_error = f"确认列表重试失败: {retry_error}"
    if selection_error:
        ctx.log(f"[确认] 未处理任何确认: {selection_error}", "warn", category="steam")
        from app.notify import send_lark
        webhook = str((load_app_config_validated().get("notify") or {}).get("lark_webhook") or "").strip()
        if webhook:
            send_lark(webhook, "Steam 自动确认已转人工", selection_error)
        return
    okc, n, errc = bot.accept_selected(selected)
    if okc:
        _clear_listing_confirmation_status(ctx, successful_listings)
        ctx.log(f"[确认] 已定向确认本轮市场上架 {n} 项", "info", category="steam")
    else:
        ctx.log(f"[确认] 定向确认失败: {errc}", "warn", category="steam")
        from app.notify import send_lark
        webhook = str((load_app_config_validated().get("notify") or {}).get("lark_webhook") or "").strip()
        if webhook:
            send_lark(
                webhook, "Steam 上架确认需要人工处理", errc or "Steam 自动确认未返回明确成功结果"
            )


# ---------------------------------------------------------------------------
# Sell phase orchestrator
# ---------------------------------------------------------------------------

def _run_sell_phase_impl(cfg: dict, state, flow_id: str, items: Optional[list] = None) -> None:
    cfg = apply_strategy_to_config(cfg, "sell")
    pipeline_cfg = cfg.get("pipeline", {})
    verbose = bool(pipeline_cfg.get("verbose_debug", False))
    ctx = PipelineContext(state, flow_id, verbose=verbose)
    sell_strategy = int(pipeline_cfg.get("sell_strategy", 1))

    if sell_strategy == 4:
        ctx.log("策略4 暂停自动出售，跳过", "info")
        return

    from app.account_scope import validate_current_account_identity
    identity_ok, identity_error = validate_current_account_identity()
    if not identity_ok:
        ctx.log(f"[出售] 账号身份保护: {identity_error}，已跳过", "error", category="account")
        return
    account = get_current_account()
    if account is None:
        ctx.log("[出售] 无有效账号（可能刚执行了出厂重置），跳过本次出售", "warn", category="steam")
        return
    from app.account_scope import get_account_runtime_status
    account_runtime = get_account_runtime_status(str(account.get("id") or ""))
    if not account_runtime.get("auto_sell_enabled", False):
        ctx.log("[出售] 当前账号已启用个人库存保护，自动出售未开启", "info", category="account")
        return
    cred_steam = get_steam_credentials()
    session_result = _resolve_steam_session(ctx, cred_steam)
    if session_result is None:
        return
    session, session_id_effective = session_result

    items = _get_inventory(ctx, items)
    if items is None:
        return

    sellable = [it for it in items if it.get("can_sell")]
    if not sellable:
        ctx.log("当前无可出售物品", "info", category="steam")
        return

    listing_delay = max(1, int(pipeline_cfg.get("listing_delay_seconds", 3) or 3))
    steam_debug = bool(pipeline_cfg.get("verbose_debug") or pipeline_cfg.get("steam_listings_debug"))
    debug_fn = (lambda m: ctx.log(m, "debug", category="steam")) if steam_debug else None

    ok_listings, active_listing_ids, err_listings, listing_assetid_to_name = fetch_my_listings(
        cred_steam.get("cookies"), debug_fn=debug_fn
    )
    if ok_listings:
        ctx.log(f"[出售] Steam 在售列表拉取成功，共 {len(active_listing_ids)} 个在售", "info", category="steam")
    else:
        ctx.log(f"[出售] Steam 在售列表拉取失败: {err_listings or '未知'}，同名在售校验将按本地购买记录", "warn", category="steam")

    purchases_snapshot = ctx.state.get_purchases()
    from app.inventory_ownership import annotate_inventory_ownership
    annotate_inventory_ownership(items, purchases_snapshot)
    account_currency_cached = (account.get("currency_code") or "").strip().upper()
    region_check = refresh_account_region_currency(
        account.get("id"),
        cookies_raw=cred_steam.get("cookies", ""),
    )
    if not region_check.get("ok"):
        ctx.log(
            "[出售] 无法实时确认 Steam 账号结算币种，已拒绝上架，"
            f"防止跨区价格误卖。原因: {region_check.get('error') or '未知原因'}",
            "error",
            category="steam",
        )
        return
    account_currency = (region_check.get("currency_code") or "").strip().upper()
    account_region = (region_check.get("region_code") or "").strip().upper()
    if not account_currency:
        ctx.log(
            "[出售] Steam 未返回结算币种，已拒绝上架，防止跨区价格误卖。",
            "error",
            category="steam",
        )
        return
    if account_currency_cached and account_currency_cached != account_currency:
        ctx.log(
            f"[出售] 检测到账号币种变化: 缓存={account_currency_cached} 实时={account_currency}，"
            "已更新并使用实时币种定价。",
            "warn",
            category="steam",
        )
    if account_region:
        ctx.debug(
            f"[出售] 账号地区按结算币种派生: 币种={account_currency} 地区={account_region}",
            category="steam",
        )
    rate_map = _load_rate_map()

    assetid_to_name_map = {
        str(p.get("assetid") or "").strip(): (p.get("market_hash_name") or p.get("name") or "").strip()
        for p in purchases_snapshot if str(p.get("assetid") or "").strip()
    }

    managed_count, personal_count = _count_sellable_ownership(
        sellable,
        purchases_snapshot,
    )
    ctx.log(
        f"[出售扫描] 策略{sell_strategy}：Steam 可出售 {len(sellable)} 件，"
        f"其中自动托管 {managed_count} 件、个人保护 {personal_count} 件"
        f"（智能定价：墙+断层；上架间隔={listing_delay}s）",
        "info",
        category="steam",
    )

    to_list = _build_listing_plan(
        ctx, cfg, session, sellable, sell_strategy, pipeline_cfg,
        purchases_snapshot, ok_listings, active_listing_ids,
        listing_assetid_to_name, assetid_to_name_map, account_currency, rate_map,
    )
    to_list = _filter_auto_listing_price_cap(ctx, to_list, pipeline_cfg)

    if not to_list:
        ctx.log(
            f"[出售汇总] 扫描 {len(sellable)} 件：个人保护 {personal_count} 件，"
            f"自动托管 {managed_count} 件；计划上架 0 件，"
            f"自动托管未进入计划 {managed_count} 件",
            "info",
            category="steam",
        )
        return

    confirmation_scope = _prepare_listing_confirmations(
        ctx, cfg, cred_steam.get("steam_id", ""), cred_steam.get("cookies", "")
    )
    ctx.log(f"[出售] 开始上架 {len(to_list)} 件", "info", category="steam")
    listed = _submit_listings(ctx, to_list, session, session_id_effective, listing_delay)

    if listed:
        ctx.log(f"[出售] 本轮回共上架 {len(listed)} 件，等待下一轮", "info", category="steam")
        _auto_confirm_listings(ctx, confirmation_scope, listed)

    failed_count = max(0, len(to_list) - len(listed))
    not_planned_count = max(0, managed_count - len(to_list))
    ctx.log(
        f"[出售汇总] 扫描 {len(sellable)} 件：个人保护 {personal_count} 件，"
        f"自动托管 {managed_count} 件；计划上架 {len(to_list)} 件，"
        f"成功 {len(listed)} 件，失败 {failed_count} 件，"
        f"自动托管未进入计划 {not_planned_count} 件；"
        f"计划商品：{_format_listing_plan_summary(to_list)}",
        "info",
        category="steam",
    )


def _run_sell_phase(cfg: dict, state, flow_id: str, items: Optional[list] = None) -> None:
    if not _sell_phase_lock.acquire(blocking=False):
        state.log("[出售] 已有出售任务在执行，本次跳过", "info", category="steam", flow_id=flow_id)
        return
    try:
        from app.account_operations import account_operation
        with account_operation("出售任务"):
            _run_sell_phase_impl(cfg, state, flow_id, items)
    except Exception as exc:
        from app.account_operations import AccountOperationConflict
        if isinstance(exc, AccountOperationConflict):
            state.log(f"[出售] {exc}", "warn", category="account", flow_id=flow_id)
        else:
            raise
    finally:
        _sell_phase_lock.release()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_sell_phase_on_inventory_update(items: list, *, delay_seconds: float = 0.0) -> None:
    cfg = merge(DEFAULTS, load_app_config_validated())
    state = get_state()

    def runner() -> None:
        if delay_seconds > 0:
            time.sleep(float(delay_seconds))
        _run_sell_phase(cfg, state, "inventory", items=items)

    t = threading.Thread(target=runner, daemon=True)
    t.start()

def evaluate_stale_listing_reprice(
    cfg: dict,
    state,
    purchase: dict,
    active_listing_ids: set,
    listing_assetid_to_name: Optional[dict] = None,
) -> dict:
    """Run the active sell strategy for one listed item without submitting it."""
    cfg = apply_strategy_to_config(cfg, "sell")
    pipeline_cfg = cfg.get("pipeline", {})
    ctx = PipelineContext(
        state,
        "stale-listing-review",
        verbose=bool(pipeline_cfg.get("verbose_debug", False)),
    )
    sell_strategy = int(pipeline_cfg.get("sell_strategy", 1))
    if sell_strategy == 4:
        return {"status": "hold", "reason": "策略4已暂停自动出售"}

    assetid = str(purchase.get("assetid") or "").strip()
    name = (purchase.get("market_hash_name") or purchase.get("name") or "").strip()
    current_price = float(purchase.get("listing_price") or 0)
    if not assetid or not name:
        return {"status": "hold", "reason": "缺少 assetid 或市场名称"}
    if current_price <= 0:
        return {"status": "hold", "reason": "缺少当前上架价，无法安全比较"}

    from app.account_scope import get_account_runtime_status, validate_current_account_identity

    identity_ok, identity_error = validate_current_account_identity()
    if not identity_ok:
        return {"status": "hold", "reason": f"账号身份保护: {identity_error}"}
    account = get_current_account()
    if account is None:
        return {"status": "hold", "reason": "当前没有有效 Steam 账号"}
    account_id = str(account.get("id") or "")
    purchase_account_id = str(purchase.get("account_id") or "")
    if purchase_account_id and purchase_account_id != account_id:
        return {"status": "hold", "reason": "记录账号与当前账号不一致"}
    if not get_account_runtime_status(account_id).get("auto_sell_enabled", False):
        return {"status": "hold", "reason": "当前账号未开启自动出售"}

    cred_steam = get_steam_credentials()
    session_result = _resolve_steam_session(ctx, cred_steam)
    if session_result is None:
        return {"status": "hold", "reason": "Steam 会话不可用"}
    session, _session_id = session_result

    region_check = refresh_account_region_currency(
        account_id,
        cookies_raw=cred_steam.get("cookies", ""),
    )
    if not region_check.get("ok"):
        return {
            "status": "hold",
            "reason": f"无法实时确认结算币种: {region_check.get('error') or '未知原因'}",
        }
    account_currency = (region_check.get("currency_code") or "").strip().upper()
    if not account_currency:
        return {"status": "hold", "reason": "Steam 未返回结算币种"}

    purchases_snapshot = state.get_purchases()
    item = {
        "name": name,
        "market_hash_name": name,
        "assetid": assetid,
        "can_sell": True,
        "appid": 730,
        "contextid": "2",
        "ownership_mode": "managed",
    }
    active_ids = {str(value or "").strip() for value in (active_listing_ids or set())}
    listing_names = dict(listing_assetid_to_name or {})
    assetid_to_name_map = {
        str(row.get("assetid") or "").strip():
        (row.get("market_hash_name") or row.get("name") or "").strip()
        for row in purchases_snapshot
        if str(row.get("assetid") or "").strip()
    }
    plan = _build_listing_plan(
        ctx,
        cfg,
        session,
        [item],
        sell_strategy,
        pipeline_cfg,
        purchases_snapshot,
        True,
        active_ids,
        listing_names,
        assetid_to_name_map,
        account_currency,
        _load_rate_map(),
        exclude_active_assetids={assetid},
        collect_wait_metrics=True,
    )
    if not plan:
        return {"status": "hold", "reason": "当前出售策略选择继续等待或安全条件未通过"}

    candidate = plan[0]
    proposed_price = round(float(candidate.get("list_price") or 0), 2)
    if proposed_price <= 0:
        return {"status": "hold", "reason": "当前出售策略未生成有效价格"}
    current_queue = _queue_ahead_at_price(
        candidate.get("sell_orders") or [],
        current_price,
    )
    wait_gate = _apply_stale_wait_gate(
        current_price,
        proposed_price,
        candidate.get("reason") or "当前策略建议改价",
        current_queue,
        candidate.get("average_daily_volume"),
        listing_age_hours=float(purchase.get("_listing_age_hours") or 24.0),
        staged_mode=bool(pipeline_cfg.get("stale_listing_staged_mode_enabled", False)),
    )
    if wait_gate["status"] == "hold":
        return wait_gate
    market_metrics = {
        "queue_ahead": wait_gate.get("queue_ahead"),
        "average_daily_volume": wait_gate.get("average_daily_volume"),
        "estimated_wait_hours": wait_gate.get("estimated_wait_hours"),
    }
    if abs(proposed_price - round(current_price, 2)) < 0.009:
        return {
            "status": "unchanged",
            "current_price": round(current_price, 2),
            "proposed_price": proposed_price,
            "reason": candidate.get("reason") or "当前策略价格未变化",
            **market_metrics,
        }
    return {
        "status": "reprice",
        "current_price": round(current_price, 2),
        "proposed_price": proposed_price,
        "reason": candidate.get("reason") or "当前策略建议改价",
        **market_metrics,
    }
