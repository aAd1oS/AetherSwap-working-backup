"""
Background worker threads – extracted from api.py.
Contains holdings report, receive, listing check, exchange rate,
and account region sync workers.
"""
import json
import time
from pathlib import Path
from typing import Optional
from app.state import (
    get_purchases,
    get_sales,
    get_state,
    is_steam_background_allowed,
    log,
    set_inventory,
    update_purchase,
    update_purchase_by_id,
)
from app.config_loader import (
    get_buff_credentials,
    get_steam_credentials,
    load_app_config_validated,
)
from app.notify import send_configured_notification, send_lark, build_holdings_report_content, compute_holdings_stats
from app.inventory_cs2 import scan_cs2_inventory
from app.accounts import get_current_account, update_account
_HOLDINGS_REPORT_LAST_FILE = Path(__file__).resolve().parent.parent.parent / "config" / "holdings_report_last.json"
_HOLDINGS_REPORT_WAIT_INTERVAL = 60
_HOLDINGS_REPORT_WAIT_MAX = 30 * 60
_worker_alert_last: dict = {}  
_WORKER_ALERT_COOLDOWN = 3600  
_STALE_LISTING_SECONDS = 24 * 60 * 60
_PENDING_CONFIRMATION_STATUS = "pending_confirmation"
_PENDING_CONFIRMATION_TIMEOUT_SECONDS = 30 * 60
_PENDING_CONFIRMATION_ALERT_KEY = "pending_confirmation_timeout"


def _check_pending_confirmation_timeouts(
    purchases: list,
    notify_cfg: dict,
    *,
    now: Optional[float] = None,
    send_fn=None,
) -> dict:
    """Alert once when a listing remains unconfirmed for 30 minutes."""
    current_time = float(now if now is not None else time.time())
    result = {"notified": 0, "failed": 0}
    for row in purchases or []:
        db_id = int(row.get("_db_id") or 0)
        listed_at = float(row.get("listed_at") or 0)
        if (
            not db_id
            or row.get("listing_status") != _PENDING_CONFIRMATION_STATUS
            or listed_at <= 0
            or current_time - listed_at < _PENDING_CONFIRMATION_TIMEOUT_SECONDS
            or row.get("last_listing_advice_key") == _PENDING_CONFIRMATION_ALERT_KEY
        ):
            continue
        name = row.get("name") or "未命名商品"
        assetid = str(row.get("assetid") or "")
        content = (
            f"{name} 的 Steam 上架已等待移动确认超过 30 分钟，assetid={assetid}。"
            "系统保持 pending_confirmation，不会自动重上架或改写为失败；请进入操作记录人工处理。"
        )
        if send_fn is None:
            sent, _channel = send_configured_notification(
                notify_cfg, "Steam 上架确认超时", content
            )
        else:
            sent = bool(send_fn(notify_cfg, "Steam 上架确认超时", content))
        if not sent:
            result["failed"] += 1
            continue
        if update_purchase_by_id(
            db_id, {"last_listing_advice_key": _PENDING_CONFIRMATION_ALERT_KEY}
        ):
            result["notified"] += 1
    return result

def _check_stale_listing_notifications(
    purchases: list,
    active_ids: set,
    notify_cfg: dict,
    *,
    now: Optional[float] = None,
    send_fn=None,
    listing_assetid_to_name: Optional[dict] = None,
) -> dict:
    """Start clocks in tests; production performs the full 24-hour strategy review."""
    if now is None and send_fn is None:
        cfg = load_app_config_validated()
        review_result = _review_stale_listings(
            purchases,
            active_ids,
            listing_assetid_to_name or {},
            cfg,
            get_state(),
        )
        if review_result.get("relist_items"):
            from app.sell_pipeline import run_sell_phase_on_inventory_update
            run_sell_phase_on_inventory_update(
                review_result["relist_items"],
                delay_seconds=1.5,
            )
        return review_result
    current_time = float(now if now is not None else time.time())
    active = {str(value or "").strip() for value in (active_ids or set())}
    active_rows = [
        row for row in (purchases or [])
        if row.get("listing")
        and not row.get("sold_at")
        and str(row.get("assetid") or "").strip() in active
    ]
    if not active_rows:
        return {"started": 0, "notified": 0, "failed": 0}
    ownership_items = [
        {"assetid": str(row.get("assetid") or ""), "name": row.get("name") or ""}
        for row in active_rows
    ]
    from app.inventory_ownership import annotate_inventory_ownership
    annotate_inventory_ownership(ownership_items, purchases)
    ownership = {item["assetid"]: item.get("ownership_mode") for item in ownership_items}
    webhook = str((notify_cfg or {}).get("lark_webhook") or "").strip()
    sender = send_fn or send_lark
    result = {"started": 0, "notified": 0, "failed": 0}
    for row in active_rows:
        db_id = int(row.get("_db_id") or 0)
        if not db_id:
            continue
        assetid = str(row.get("assetid") or "").strip()
        listed_at = float(row.get("listed_at") or 0)
        if listed_at <= 0:
            if update_purchase_by_id(db_id, {"listed_at": current_time}):
                result["started"] += 1
            continue
        if ownership.get(assetid) != "managed":
            continue
        if current_time - listed_at < _STALE_LISTING_SECONDS:
            continue
        if row.get("stale_listing_notified_at") or not webhook:
            continue
        price_text = (
            f"，上架价 {float(row.get('listing_price')):.2f}"
            if row.get("listing_price") is not None else ""
        )
        content = (
            f"系统托管商品 {row.get('name') or '未命名商品'} 已连续上架满 24 小时仍未售出。"
            f"assetid={assetid}{price_text}。本次仅提醒，不会自动下架或改价。"
        )
        if sender(webhook, "Steam 商品上架满 24 小时", content):
            if update_purchase_by_id(db_id, {"stale_listing_notified_at": current_time}):
                result["notified"] += 1
        else:
            result["failed"] += 1
    return result
def _worker_alert(worker_name: str, error: Exception) -> None:
    """发送后台任务告警，每个 worker 每小时至多发一次。"""
    now = time.time()
    last = _worker_alert_last.get(worker_name, 0.0)
    if now - last < _WORKER_ALERT_COOLDOWN:
        return
    try:
        cfg = load_app_config_validated()
        notify_cfg = cfg.get("notify") or {}
        if not (notify_cfg.get("lark_webhook") or notify_cfg.get("pushplus_token")):
            return
        msg = str(error)[:200] if error else "未知异常"
        sent, _channel = send_configured_notification(
            notify_cfg,
            f"[Worker异常] {worker_name}",
            f"后台任务 <b>{worker_name}</b> 发生异常，已自动重试。<br>错误信息：{msg}",
        )
        if not sent:
            return
        _worker_alert_last[worker_name] = now
        log(f"[{worker_name}] 异常告警已发送", "info", category="alert")
    except Exception:
        pass
def _load_last_pl_pct() -> Optional[float]:
    try:
        if _HOLDINGS_REPORT_LAST_FILE.exists():
            with open(_HOLDINGS_REPORT_LAST_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                v = data.get("pl_pct")
                return float(v) if v is not None else None
    except Exception:
        pass
    return None
def _save_last_pl_pct(pl_pct: float) -> None:
    try:
        _HOLDINGS_REPORT_LAST_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_HOLDINGS_REPORT_LAST_FILE, "w", encoding="utf-8") as f:
            json.dump({"pl_pct": pl_pct}, f)
    except Exception:
        pass
def _enrich_purchases_with_current_prices(transactions: list) -> None:
    from app.shared_market import batch_fetch_prices
    purchases = [t for t in transactions if t.get("type") == "purchase"]
    if not purchases:
        return
    names: set = set()
    for t in purchases:
        if t.get("sale_price") is not None:
            continue
        name = (t.get("name") or "").strip()
        if name:
            names.add(name)
    if not names:
        return
    prices = batch_fetch_prices(names)
    for t in purchases:
        name = (t.get("name") or "").strip()
        if name in prices and t.get("sale_price") is None:
            t["current_market_price"] = prices[name]
def run_holdings_report_once(force: bool = False) -> bool:
    if not force and not is_steam_background_allowed():
        return False
    cfg = load_app_config_validated()
    notify_cfg = cfg.get("notify") or {}
    if not (notify_cfg.get("lark_webhook") or notify_cfg.get("pushplus_token")):
        return False
    resell_ratio = float(cfg.get("pipeline", {}).get("resell_ratio", 0.85))
    if resell_ratio <= 0:
        resell_ratio = 0.85
    def _build_out():
        purchases = get_purchases()
        out = []
        for i, p in enumerate(purchases):
            row = {"type": "purchase", "idx": i, "name": p.get("name", ""), "price": float(p.get("price", 0)), "market_price": p.get("market_price"), "sale_price": p.get("sale_price")}
            if row["market_price"] is not None:
                row["market_price"] = round(float(row["market_price"]), 2)
            out.append(row)
        return out, purchases
    def _enriched_holdings(out):
        _enrich_purchases_with_current_prices(out)
        return [t for t in out if t.get("type") == "purchase" and (t.get("sale_price") is None or float(t.get("sale_price", 0) or 0) <= 0)]
    out, purchases = _build_out()
    holdings = [p for p in purchases if not (p.get("sale_price") is not None and float(p.get("sale_price", 0) or 0) > 0)]
    if not holdings:
        return False
    holdings_enriched = _enriched_holdings(out)
    if not holdings_enriched:
        return False
    all_have_price = all(t.get("current_market_price") is not None for t in holdings_enriched)
    if not all_have_price:
        return False
    _, total_mp, _, _, pl_pct, _ = compute_holdings_stats(holdings_enriched, resell_ratio)
    if not force:
        drop_threshold_pct = float(notify_cfg.get("holdings_report_change_threshold_pct", 20) or 20)
        last_pl_pct = _load_last_pl_pct()
        if last_pl_pct is None:
            if pl_pct is not None:
                _save_last_pl_pct(pl_pct)
            return False
        if pl_pct is None:
            return False
        drop = last_pl_pct - pl_pct  
        if drop < drop_threshold_pct:
            return False
    content = build_holdings_report_content(holdings_enriched, resell_ratio)
    ok, _channel = send_configured_notification(
        notify_cfg,
        "持有饰品紧急回报" if not force else "持有饰品定时回报",
        content,
    )
    if ok and pl_pct is not None:
        _save_last_pl_pct(pl_pct)
    return ok
def holdings_report_worker() -> None:
    """定时回报 worker（holdings_report_interval_hours > 0 才运行）.
    定时回报不受跌幅限制，每隔设定小时强制推送一次。
    紧急回报另由 run_holdings_report_once(force=False) 负责（在有新市场价时自动触发）。
    """
    first_run = True
    while True:
        try:
            cfg = load_app_config_validated()
            n = cfg.get("notify") or {}
            interval_h = int(n.get("holdings_report_interval_hours", 0) or 0)
            if interval_h <= 0:
                first_run = True
                time.sleep(3600)
                continue
            if first_run:
                first_run = False
                time.sleep(60)
            else:
                time.sleep(interval_h * 3600)
            while not is_steam_background_allowed():
                time.sleep(60)
            run_holdings_report_once(force=True)
        except Exception:
            time.sleep(60)
_EXCHANGE_RATE_FILE = Path(__file__).resolve().parent.parent.parent / "config" / "exchange_rate.json"
def _fetch_exchange_rates(base: str = "CNY", targets: Optional[list] = None) -> Optional[dict]:
    try:
        import requests
        from utils.proxy_manager import get_proxy_manager
        pm = get_proxy_manager()
        proxies = pm.get_proxies_for_request()
        url = f"https://open.er-api.com/v6/latest/{base}"
        from app.state import log
        if proxies:
            log(f"exchange_rate: 正在使用代理 {proxies.get('http')} 访问: {url}", "debug", category="exchange_rate")
        r = requests.get(url, timeout=10, proxies=proxies)
        data = r.json()
        if data.get("result") != "success":
            return None
        all_rates = data.get("rates") or {}
        if not targets:
            return {}  
        out = {}
        for code in targets:
            if code not in all_rates:
                continue
            rate_val = all_rates[code]
            if not rate_val:
                continue
            out[code] = 1.0 / float(rate_val)
        return out or None
    except Exception:
        return None
def _save_exchange_rates(rates: dict, base: str = "CNY") -> None:
    try:
        from datetime import datetime, timezone
        _EXCHANGE_RATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "base": base,
            "rates": rates,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        with open(_EXCHANGE_RATE_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception:
        return
def exchange_rate_worker() -> None:
    while True:
        try:
            cfg = load_app_config_validated()
            sys_cfg = cfg.get("system") or {}
            interval_h = float(sys_cfg.get("exchange_rate_refresh_hours") or 0)
            if interval_h <= 0:
                log("exchange_rate: 已关闭, system.exchange_rate_refresh_hours<=0", "debug", category="exchange_rate")
                time.sleep(3600)
                continue
            targets = [
                "USD", "INR", "RUB", "HKD", "EUR",
                "KZT", "UAH", "PKR", "TRY", "ARS", "AZN",
                "VND", "IDR", "BRL", "CLP", "JPY", "PHP",
            ]
            log(f"exchange_rate: 开始获取, 间隔={interval_h} 小时, 目标={','.join(targets)}", "debug", category="exchange_rate")
            rates = _fetch_exchange_rates("CNY", targets)
            if rates is not None:
                _save_exchange_rates(rates, "CNY")
                preview = ", ".join(f"{k}={v:.4f}" for k, v in rates.items())
                log(f"exchange_rate: 已更新 {len(rates)} 个币种: {preview}", "debug", category="exchange_rate")
            else:
                log("exchange_rate: 获取失败或无有效结果", "error", category="exchange_rate")
            time.sleep(max(1, int(interval_h * 3600)))
        except Exception as e:
            log(f"exchange_rate: worker 异常 {type(e).__name__}: {e}, 5 分钟后重试", "error", category="exchange_rate")
            time.sleep(300)
def receive_worker() -> None:
    from app.receive_flow import try_receive_once
    while True:
        operation_token = None
        try:
            cfg = load_app_config_validated()
            interval = max(10, int(cfg.get("pipeline", {}).get("receive_poll_interval_seconds", 30) or 30))
            time.sleep(interval)
            if not is_steam_background_allowed():
                continue
            purchases = get_purchases()
            if not any(p.get("pending_receipt") and not p.get("assetid") for p in purchases):
                continue
            from app.account_operations import begin_account_operation
            operation_token, _operation_account_id = begin_account_operation("自动收货")
            from app.account_scope import validate_current_account_identity
            identity_ok, _ = validate_current_account_identity()
            if not identity_ok:
                continue
            n = try_receive_once(
                get_purchases,
                update_purchase,
                lambda: (get_buff_credentials() or {}).get("cookies", ""),
                get_steam_credentials,
                scan_inventory=scan_cs2_inventory,
                update_purchase_by_id=update_purchase_by_id,
            )
            if n > 0:
                log(f"receive_worker: 本轮收取到 {n} 件物品", "info", category="receive")
        except Exception as e:
            log(f"receive_worker 异常 {type(e).__name__}: {e}", "error", category="receive")
            _worker_alert("receive_worker", e)
            time.sleep(60)
        finally:
            if operation_token:
                from app.account_operations import end_account_operation
                end_account_operation(operation_token)
def _partition_listing_visibility(listing_idx: list, active_ids: set) -> tuple:
    """Keep pending confirmations out of sold/missing reconciliation."""
    active = {str(value or "").strip() for value in (active_ids or set())}
    confirmed_pending = []
    pending_missing = []
    missing = []
    for entry in listing_idx:
        _index, row = entry
        assetid = str(row.get("assetid") or "").strip()
        is_pending = row.get("listing_status") == _PENDING_CONFIRMATION_STATUS
        if assetid in active:
            if is_pending:
                confirmed_pending.append(entry)
            continue
        if is_pending:
            pending_missing.append(entry)
        else:
            missing.append(entry)
    return confirmed_pending, pending_missing, missing

def listing_check_worker() -> None:
    from app.steam_listings import fetch_my_listings, fetch_my_history_sold
    first_check = True
    while True:
        operation_token = None
        try:
            cfg = load_app_config_validated()
            interval = max(60, int(cfg.get("pipeline", {}).get("listing_check_interval_seconds", 600) or 600))
            if first_check:
                first_check = False
            else:
                time.sleep(interval)
            if not is_steam_background_allowed():
                continue
            from app.account_operations import begin_account_operation
            operation_token, _operation_account_id = begin_account_operation("在售同步")
            purchases = get_purchases()
            pending_relist = [
                row for row in purchases
                if row.get("listing_status") in _STALE_RELIST_STATUSES
            ]
            if pending_relist:
                ok_pending, pending_items, pending_error = scan_cs2_inventory()
                if ok_pending:
                    set_inventory(pending_items)
                    pending_result = _sync_stale_relist_records(
                        purchases,
                        pending_items,
                    )
                    if pending_result.get("relist_items"):
                        from app.sell_pipeline import run_sell_phase_on_inventory_update
                        run_sell_phase_on_inventory_update(
                            pending_result["relist_items"],
                            delay_seconds=1.5,
                        )
                    purchases = get_purchases()
                else:
                    log(
                        f"[listing_check] pending stale relist assetid sync failed: {pending_error}",
                        "warn",
                        category="delist",
                    )
            listing_idx = [(i, p) for i, p in enumerate(purchases) if p.get("listing") and p.get("assetid")]
            if not listing_idx:
                continue
            cred = get_steam_credentials()
            cookies = cred.get("cookies") or ""
            if not cookies:
                continue
            pipeline_cfg = cfg.get("pipeline") or {}
            steam_debug = bool(pipeline_cfg.get("steam_listings_debug") or pipeline_cfg.get("verbose_debug"))
            debug_fn = (lambda m: log(m, "debug", category="steam")) if steam_debug else None
            ok, active_ids, err, listing_assetid_to_name = fetch_my_listings(cookies, debug_fn=debug_fn)
            if not ok:
                continue
            reminder_result = _check_stale_listing_notifications(
                purchases,
                active_ids,
                cfg.get("notify") or {},
                listing_assetid_to_name=listing_assetid_to_name,
            )
            if reminder_result.get("notified"):
                log(
                    f"[listing_check] 已发送 {reminder_result['notified']} 条连续上架 24 小时提醒",
                    "info",
                    category="steam",
                )
            confirmed_pending, pending_missing, not_in_active = _partition_listing_visibility(
                listing_idx,
                active_ids,
            )
            for index, purchase in confirmed_pending:
                db_id = purchase.get("_db_id")
                if db_id:
                    update_purchase_by_id(db_id, {"listing_status": None})
                else:
                    update_purchase(index, {"listing_status": None})
            if confirmed_pending:
                log(
                    f"[listing_check] {len(confirmed_pending)} 件待确认商品已进入 Steam 活跃在售",
                    "info",
                    category="steam",
                )
            pending_timeout_result = _check_pending_confirmation_timeouts(
                [row for _index, row in pending_missing],
                cfg.get("notify") or {},
            )
            if pending_timeout_result.get("notified"):
                log(
                    f"[listing_check] 已发送 {pending_timeout_result['notified']} 条待确认超时提醒",
                    "warn",
                    category="steam",
                )
            if steam_debug and pending_missing:
                log(
                    f"[listing_check] {len(pending_missing)} 件商品仍等待 Steam 市场确认，保留本地状态且不重复上架",
                    "debug",
                    category="steam",
                )
            if steam_debug and not_in_active:
                log(f"[listing_check] 本地 {len(listing_idx)} 条在售, Steam 活跃 {len(active_ids)}, 可能已售 {len(not_in_active)} 条", "debug", category="steam")
            if not not_in_active:
                continue
            ok2, sold_map, _ = fetch_my_history_sold(cookies, debug_fn=debug_fn)
            seen_aids = set()
            sold_updates = 0
            for _i, p in not_in_active:
                aid = str(p.get("assetid") or "")
                if not aid or aid in seen_aids:
                    continue
                seen_aids.add(aid)
                db_id = p.get("_db_id")
                sale_price_rounded = round(sold_map[aid], 2) if (ok2 and aid in sold_map) else None
                if db_id:
                    if sale_price_rounded is not None:
                        sold_at = time.time()
                        update_purchase_by_id(db_id, {"sale_price": sale_price_rounded, "sold_at": sold_at, "listing": False, "listing_status": None, "listed_at": None, "listing_price": None, "stale_listing_notified_at": None, "listing_review_after": None})
                        sold_updates += 1
                    else:
                        update_purchase_by_id(db_id, {"listing": False, "listing_status": "error", "listed_at": None, "listing_price": None, "stale_listing_notified_at": None, "listing_review_after": None})
                else:
                    current = get_purchases()
                    matched = [j for j, q in enumerate(current) if str(q.get("assetid") or "") == aid]
                    if sale_price_rounded is not None:
                        sold_at = time.time()
                        for idx in matched:
                            update_purchase(idx, {"sale_price": sale_price_rounded, "sold_at": sold_at, "listing": False, "listing_status": None, "listed_at": None, "listing_price": None, "stale_listing_notified_at": None, "listing_review_after": None})
                        if matched:
                            sold_updates += 1
                    else:
                        for idx in matched:
                            update_purchase(idx, {"listing": False, "listing_status": "error", "listed_at": None, "listing_price": None, "stale_listing_notified_at": None, "listing_review_after": None})
            if sold_updates > 0:
                log(f"[listing_check] 确认售出 {sold_updates} 件，刷新库存并触发自动补挂", "info", category="steam")
                ok_inv, inv_items, inv_err = scan_cs2_inventory()
                if ok_inv:
                    set_inventory(inv_items)
                    from app.sell_pipeline import run_sell_phase_on_inventory_update
                    run_sell_phase_on_inventory_update(inv_items)
                else:
                    log(f"[listing_check] 售出后刷新库存失败，暂不补挂: {inv_err}", "warn", category="steam")
        except Exception as e:
            log(f"listing_check_worker 异常 {type(e).__name__}: {e}", "error", category="steam")
            _worker_alert("listing_check_worker", e)
            time.sleep(60)
        finally:
            if operation_token:
                from app.account_operations import end_account_operation
                end_account_operation(operation_token)
def _currency_code_from_price_text(text: str) -> str:
    s = text or ""
    if "¥" in s or "￥" in s or "CNY" in s or "RMB" in s:
        return "CNY"
    if "HK" in s and "$" in s:
        return "HKD"
    if "₹" in s:
        return "INR"
    if "₽" in s:
        return "RUB"
    if "€" in s:
        return "EUR"
    if "USD" in s or "US$" in s:
        return "USD"
    if "$" in s:
        return "USD"
    return "CNY"
def _detect_account_currency_from_history() -> Optional[str]:
    try:
        import requests
        from bs4 import BeautifulSoup
        cred = get_steam_credentials()
        cookies_str = cred.get("cookies") or ""
        if not cookies_str:
            return None
        cookies_dict = {}
        for part in cookies_str.split(";"):
            s = part.strip()
            if "=" in s:
                k, _, v = s.partition("=")
                cookies_dict[k.strip()] = v.strip()
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36",
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "X-Requested-With": "XMLHttpRequest",
        }
        params = {"query": "", "start": 0, "count": 50, "contextid": 2, "appid": 730}
        from app.steam_listings import MYHISTORY_RENDER_URL
        from utils.proxy_manager import get_proxy_manager
        pm = get_proxy_manager()
        proxies = pm.get_steam_proxies()
        if proxies:
            log(f"[account_sync] _detect_account_currency: 使用代理 {proxies.get('http')}", "debug", category="proxy")
        r = requests.get(MYHISTORY_RENDER_URL, params=params, headers=headers, cookies=cookies_dict, proxies=proxies, timeout=25)
        if r.status_code != 200:
            return None
        data = r.json() if r.text else {}
        if not data.get("success"):
            return None
        html = data.get("results_html") or ""
        if not html:
            return None
        soup = BeautifulSoup(html, "html.parser")
        price_el = soup.find("span", class_="market_listing_price")
        if not price_el:
            return None
        price_text = (price_el.get_text(" ", strip=True) or "").strip()
        if not price_text:
            return None
        return _currency_code_from_price_text(price_text)
    except Exception:
        return None
def _sync_account_profile_and_region(acc: dict) -> None:
    from app.services.steam_auth import fetch_steam_profile_via_api
    account_id = acc.get("id")
    cred = get_steam_credentials()
    cred_steam_id = (cred.get("steam_id") or "").strip()
    acc_steam_id = (acc.get("steam_id") or "").strip()
    cookies_str = cred.get("cookies") or ""
    if cred_steam_id and not acc_steam_id:
        update_account(account_id, steam_id=cred_steam_id)
        acc_steam_id = cred_steam_id
        log(f"account_sync: 从 credentials 同步 steam_id={cred_steam_id}", "info", category="account")
    steam_id = acc_steam_id or cred_steam_id
    if steam_id and cookies_str and (not acc.get("display_name") or not acc.get("avatar_url")):
        display_name, avatar_url = fetch_steam_profile_via_api(steam_id, cookies_str)
        if display_name or avatar_url:
            updates = {}
            if display_name and not acc.get("display_name"):
                updates["display_name"] = display_name
            if avatar_url and not acc.get("avatar_url"):
                updates["avatar_url"] = avatar_url
            if updates:
                update_account(account_id, **updates)
                log(f"account_sync: 已获取 Steam 资料 name={display_name or '(无)'} avatar={'(有)' if avatar_url else '(无)'}", "info", category="account")
        else:
            log("account_sync: 未能获取 Steam 资料（网络异常或 Cookie 失效）", "debug", category="account")

def sync_account_region_worker() -> None:
    try:
        acc = get_current_account()
        if not acc:
            log("account_region: 无当前账号，跳过同步", "debug", category="account")
            return
        log(
            f"account_region: 开始同步 account_id={acc.get('id')} username={acc.get('username') or ''} steam_id={acc.get('steam_id') or ''}",
            "debug",
            category="account",
        )
        _sync_account_profile_and_region(acc)
        
        from app.services.account_region import refresh_account_region_currency
        result = refresh_account_region_currency(acc.get("id"), skip_unconfigured=True)
        if not result.get("ok"):
            if result.get("skipped"):
                log(
                    f"account_region: {result.get('error') or '尚未完成 Steam 登录配置'}",
                    "debug",
                    category="account",
                )
                return
            log(
                f"account_region: 同步失败，已暂停自动出售安全许可: {result.get('error') or '未知原因'}",
                "error",
                category="account",
            )
            return
        log(
            f"account_region: 同步完成 account_id={acc.get('id')} "
            f"币种={result.get('currency_code')} 派生地区={result.get('region_code')}",
            "debug",
            category="account",
        )
    except Exception as e:
        log(f"account_region: 同步异常 {str(e)[:120]}", "error", category="account")
        return
def session_keepalive_worker() -> None:
    from app.services.steam_auth import try_steam_auto_relogin
    from app.services.buff_auth import try_buff_auto_relogin
    first_run = True
    while True:
        try:
            cfg = load_app_config_validated()
            sys_cfg = cfg.get("system") or {}
            interval_h = float(sys_cfg.get("session_keepalive_hours", 4.0))
            if interval_h <= 0:
                log("keepalive: 已关闭, system.session_keepalive_hours<=0", "debug", category="keepalive")
                time.sleep(3600)
                continue
            if first_run:
                first_run = False
                time.sleep(300) 
            else:
                time.sleep(interval_h * 3600)
            while not is_steam_background_allowed():
                time.sleep(60)
            log("keepalive: 开始本轮定期后台会话保活 (Steam & Buff)...", "info", category="keepalive")
            buff_ok, buff_status, buff_msg = try_buff_auto_relogin()
            if not buff_ok:
                log(f"keepalive: Buff 保活失败: {buff_msg}", "warn", category="keepalive")
            else:
                log(f"keepalive: Buff 保活成功: {buff_msg}", "info", category="keepalive")
            time.sleep(10) 
            steam_ok, steam_status, steam_msg = try_steam_auto_relogin()
            if steam_status == "verification_deferred":
                log(f"keepalive: Steam Cookie 已保留: {steam_msg}", "info", category="keepalive")
            elif not steam_ok:
                log(f"keepalive: Steam 保活失败: {steam_msg}", "warn", category="keepalive")
            else:
                log(f"keepalive: Steam 保活成功: {steam_msg}", "info", category="keepalive")
            log("keepalive: 本轮后台会话保活已完成", "info", category="keepalive")
        except Exception as e:
            log(f"keepalive: worker 异常 {e}, 15 分钟后重试", "error", category="keepalive")
            time.sleep(900)

_STALE_LISTING_REVIEW_LIMIT = 5
_STALE_LISTING_REVIEW_SECONDS = 10 * 60
_STALE_LISTING_RETRY_SECONDS = 60 * 60
_STALE_RELIST_STATUSES = {
    "stale_relist_pending",
    "stale_relist_assetid_pending",
    "stale_relist_assetid_ambiguous",
}


def _format_listing_market_metrics(evaluation: dict) -> str:
    parts = []
    queue = evaluation.get("queue_ahead")
    daily = evaluation.get("average_daily_volume")
    wait_hours = evaluation.get("estimated_wait_hours")
    if queue is not None:
        parts.append(f"当前价前方约 {int(queue)} 件")
    if daily is not None:
        parts.append(f"近期日均成交约 {float(daily):.1f} 件")
    if wait_hours is not None:
        parts.append(f"预计等待约 {float(wait_hours):.1f} 小时")
    return "；".join(parts)


def _listing_advice_key(status: str, evaluation: dict) -> str:
    """Return a stable key for the recommendation, excluding noisy metrics/reasons."""
    normalized = str(status or "hold").strip().lower() or "hold"
    if normalized != "reprice":
        return normalized
    try:
        proposed_price = round(float(evaluation.get("proposed_price") or 0), 2)
    except (TypeError, ValueError):
        proposed_price = 0.0
    return f"reprice:{proposed_price:.2f}"


def _send_stale_listing_notice(
    notify_cfg: dict,
    title: str,
    content: str,
    send_fn=None,
) -> bool:
    webhook = str((notify_cfg or {}).get("lark_webhook") or "").strip()
    if not webhook:
        return False
    return bool((send_fn or send_lark)(webhook, title, content))


def _sync_stale_relist_records(purchases: list, inventory_items: list) -> dict:
    """Resolve only stale-relist records; never guess between same-name assets."""
    from app.database import DuplicateAssetIdError
    from app.steam_delist import resolve_delisted_assetid_from_inventory

    result = {"resolved": 0, "pending": 0, "ambiguous": 0, "relist_items": []}
    for row in purchases or []:
        if row.get("listing_status") not in _STALE_RELIST_STATUSES:
            continue
        db_id = int(row.get("_db_id") or 0)
        if not db_id:
            continue
        resolution = resolve_delisted_assetid_from_inventory(
            row,
            inventory_items,
            purchases,
        )
        status = resolution.get("status")
        if status == "resolved":
            assetid = str(resolution.get("assetid") or "").strip()
            try:
                if not update_purchase_by_id(
                    db_id,
                    {"assetid": assetid, "listing_status": None},
                ):
                    result["pending"] += 1
                    continue
            except DuplicateAssetIdError:
                update_purchase_by_id(
                    db_id,
                    {"listing_status": "stale_relist_assetid_ambiguous"},
                )
                result["ambiguous"] += 1
                continue
            result["resolved"] += 1
            item = resolution.get("item")
            if isinstance(item, dict) and item.get("can_sell"):
                result["relist_items"].append(item)
        elif status == "ambiguous":
            update_purchase_by_id(
                db_id,
                {"listing_status": "stale_relist_assetid_ambiguous"},
            )
            result["ambiguous"] += 1
        else:
            update_purchase_by_id(
                db_id,
                {"listing_status": "stale_relist_assetid_pending"},
            )
            result["pending"] += 1
    return result


def _review_stale_listings(
    purchases: list,
    active_ids: set,
    listing_assetid_to_name: dict,
    cfg: dict,
    state,
    *,
    now: Optional[float] = None,
    evaluate_fn=None,
    delist_fn=None,
    scan_fn=None,
    notify_fn=None,
) -> dict:
    """Review stale managed listings and delist only when the active strategy reprices."""
    from app.inventory_ownership import annotate_inventory_ownership
    from app.sell_pipeline import evaluate_stale_listing_reprice
    from app.steam_delist import delist_item

    current_time = float(now if now is not None else time.time())
    active = {str(value or "").strip() for value in (active_ids or set())}
    evaluator = evaluate_fn or evaluate_stale_listing_reprice
    delister = delist_fn or delist_item
    scanner = scan_fn or scan_cs2_inventory
    result = {
        "started": 0,
        "reviewed": 0,
        "unchanged": 0,
        "held": 0,
        "delisted": 0,
        "failed": 0,
        "notified": 0,
        "relist_items": [],
    }
    active_rows = [
        row for row in (purchases or [])
        if row.get("listing")
        and not row.get("sold_at")
        and str(row.get("assetid") or "").strip() in active
    ]
    ownership_items = [
        {"assetid": str(row.get("assetid") or ""), "name": row.get("name") or ""}
        for row in active_rows
    ]
    annotate_inventory_ownership(ownership_items, purchases)
    ownership = {item["assetid"]: item.get("ownership_mode") for item in ownership_items}

    reviewed_count = 0
    staged_mode = bool(
        (cfg.get("pipeline") or {}).get("stale_listing_staged_mode_enabled", False)
    )
    for row in active_rows:
        db_id = int(row.get("_db_id") or 0)
        assetid = str(row.get("assetid") or "").strip()
        if not db_id or ownership.get(assetid) != "managed":
            continue
        listed_at = float(row.get("listed_at") or 0)
        if listed_at <= 0:
            updates = {
                "listed_at": current_time,
                "listing_review_after": current_time + _STALE_LISTING_SECONDS,
            }
            if update_purchase_by_id(db_id, updates):
                result["started"] += 1
            continue
        review_after = float(
            row.get("listing_review_after") or (listed_at + _STALE_LISTING_SECONDS)
        )
        if current_time < review_after or reviewed_count >= _STALE_LISTING_REVIEW_LIMIT:
            continue
        reviewed_count += 1
        result["reviewed"] += 1
        listing_age_hours = max(0.0, (current_time - listed_at) / 3600.0)
        stage_hours = 72 if listing_age_hours >= 72 else 48 if listing_age_hours >= 48 else 24
        review_label = f"{stage_hours}h分段复评" if staged_mode else "24h复评"

        try:
            evaluation_row = dict(row)
            evaluation_row["_listing_age_hours"] = listing_age_hours
            evaluation = evaluator(
                cfg,
                state,
                evaluation_row,
                active,
                listing_assetid_to_name,
            )
        except Exception as exc:
            evaluation = {
                "status": "hold",
                "reason": f"复评异常: {type(exc).__name__}: {str(exc)[:120]}",
            }
        status = str(evaluation.get("status") or "hold")
        name = (row.get("market_hash_name") or row.get("name") or "未命名商品").strip()
        market_summary = _format_listing_market_metrics(evaluation)
        log(
            f"[{review_label}] {name}: {evaluation.get('reason') or '无明确原因'}"
            + (f"；{market_summary}" if market_summary else ""),
            "info",
            category="sell",
        )
        if status != "reprice":
            next_review = current_time + _STALE_LISTING_REVIEW_SECONDS
            update_purchase_by_id(db_id, {"listing_review_after": next_review})
            if status == "unchanged":
                result["unchanged"] += 1
                title = "Steam 挂单复评：价格不变"
            else:
                result["held"] += 1
                title = "Steam 挂单复评：继续持有"
            content = (
                f"{name} 已进入 {review_label}。当前策略决定不下架；"
                f"原因：{evaluation.get('reason') or '安全条件未通过'}。"
                + (f"市场估算：{market_summary}。" if market_summary else "")
                + "将在 10 分钟后再次复评；建议不变时不再重复通知。"
            )
            advice_key = _listing_advice_key(status, evaluation)
            advice_changed = str(row.get("last_listing_advice_key") or "") != advice_key
            if advice_changed and _send_stale_listing_notice(
                cfg.get("notify") or {}, title, content, notify_fn
            ):
                result["notified"] += 1
                update_purchase_by_id(
                    db_id,
                    {
                        "stale_listing_notified_at": current_time,
                        "last_listing_advice_key": advice_key,
                    },
                )
            continue

        proposed_price = float(evaluation.get("proposed_price") or 0)
        old_price = float(evaluation.get("current_price") or row.get("listing_price") or 0)

        def delist_log(message: str, level: str = "info") -> None:
            log(f"[{review_label}] {message}", level, category="delist")

        ok, new_assetid, error = delister(assetid, name, log_fn=delist_log)
        if not ok:
            update_purchase_by_id(
                db_id,
                {"listing_review_after": current_time + _STALE_LISTING_RETRY_SECONDS},
            )
            result["failed"] += 1
            content = (
                f"{name} 的当前策略价由 {old_price:.2f} 变为 {proposed_price:.2f}，"
                f"但下架未明确成功：{error or '未知原因'}。本地在售状态保持不变，"
                + (f"市场估算：{market_summary}。" if market_summary else "")
                + "将在 1 小时后重试复评。"
            )
            advice_key = _listing_advice_key(status, evaluation)
            advice_changed = str(row.get("last_listing_advice_key") or "") != advice_key
            if advice_changed and _send_stale_listing_notice(
                cfg.get("notify") or {},
                "Steam 挂单复评：下架失败",
                content,
                notify_fn,
            ):
                result["notified"] += 1
                update_purchase_by_id(
                    db_id,
                    {
                        "stale_listing_notified_at": current_time,
                        "last_listing_advice_key": advice_key,
                    },
                )
            continue

        updates = {
            "listing": False,
            "listing_status": (
                "stale_relist_pending"
                if new_assetid
                else "stale_relist_assetid_pending"
            ),
            "listed_at": None,
            "listing_price": None,
            "stale_listing_notified_at": None,
            "listing_review_after": None,
            "last_listing_advice_key": None,
        }
        if new_assetid:
            updates["assetid"] = str(new_assetid)
        try:
            update_purchase_by_id(db_id, updates)
        except Exception as exc:
            from app.database import DuplicateAssetIdError

            if not isinstance(exc, DuplicateAssetIdError):
                raise
            update_purchase_by_id(
                db_id,
                {
                    "listing": False,
                    "listing_status": "stale_relist_assetid_ambiguous",
                    "listed_at": None,
                    "listing_price": None,
                    "stale_listing_notified_at": None,
                    "listing_review_after": None,
                    "last_listing_advice_key": None,
                },
            )
        result["delisted"] += 1

        ok_inventory, inventory_items, inventory_error = scanner()
        sync_result = {"resolved": 0, "pending": 1, "ambiguous": 0, "relist_items": []}
        if ok_inventory:
            set_inventory(inventory_items)
            sync_result = _sync_stale_relist_records(
                state.get_purchases(),
                inventory_items,
            )
            result["relist_items"].extend(sync_result.get("relist_items") or [])
        else:
            log(
                f"[{review_label}] 下架成功，但库存刷新失败，等待后台补全 assetid: {inventory_error}",
                "warn",
                category="delist",
            )

        sync_text = (
            "assetid 已唯一同步，商品将交回当前出售策略重新上架"
            if sync_result.get("resolved")
            else "assetid 尚未唯一确认，已停止自动重挂并等待后续同步"
        )
        content = (
            f"{name} 的当前策略价由 {old_price:.2f} 变为 {proposed_price:.2f}，"
            f"已明确下架成功；{sync_text}。"
            + (f"市场估算：{market_summary}。" if market_summary else "")
        )
        if _send_stale_listing_notice(
            cfg.get("notify") or {},
            "Steam 挂单复评：已按策略下架",
            content,
            notify_fn,
        ):
            result["notified"] += 1
    return result
