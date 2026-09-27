"""Inventory routes."""
from datetime import datetime, timezone

from fastapi import APIRouter
from pydantic import BaseModel
from app.state import (
    get_inventory,
    get_purchases,
    is_steam_background_allowed,
    log,
    set_inventory,
    update_purchase_by_id,
)
from app.inventory_cs2 import scan_cs2_inventory
from app.pipeline import run_sell_phase_on_inventory_update
from app.config_loader import get_steam_credentials, load_app_config_validated
from app.shared_market import get_steam_smart_price_cny, batch_fetch_prices
router = APIRouter()


class InventoryOwnershipBody(BaseModel):
    mode: str


def _current_account_summary() -> dict:
    from app.accounts import get_current_account
    account = get_current_account() or {}
    return {
        "id": account.get("id") or "",
        "name": account.get("display_name") or account.get("username") or account.get("steam_id") or "未选择账号",
        "steam_id": account.get("steam_id") or "",
    }


def _inventory_response(items=None, **extra) -> dict:
    inventory_items = get_inventory() if items is None else items
    purchases = get_purchases() or []
    from app.inventory_ownership import annotate_inventory_ownership
    annotate_inventory_ownership(inventory_items, purchases)
    _annotate_inventory_listing_state(inventory_items, purchases)
    payload = {"items": inventory_items, "account": _current_account_summary()}
    payload.update(extra)
    return payload


def _annotate_inventory_listing_state(items: list, purchases: list) -> None:
    purchase_by_assetid = {
        str(purchase.get("assetid") or "").strip(): purchase
        for purchase in (purchases or [])
        if str(purchase.get("assetid") or "").strip()
    }
    for item in items or []:
        purchase = purchase_by_assetid.get(str(item.get("assetid") or "").strip()) or {}
        item["listing"] = bool(purchase.get("listing"))
        item["listing_status"] = purchase.get("listing_status")


@router.post("/api/inventory/{assetid}/ownership")
def api_set_inventory_ownership(assetid: str, body: InventoryOwnershipBody):
    from app.database import db_set_inventory_ownership_override
    from app.pipeline import is_pipeline_running

    if is_pipeline_running():
        return {"ok": False, "error": "任务运行中不能修改库存归属，请先停止任务"}
    normalized = str(body.mode or "").strip().lower()
    if normalized not in {"auto", "personal", "managed"}:
        return {"ok": False, "error": "库存归属须为自动判断、个人保护或自动托管"}
    item = next(
        (
            row for row in (get_inventory() or [])
            if str(row.get("assetid") or "").strip() == str(assetid or "").strip()
        ),
        None,
    )
    if item is None:
        return {"ok": False, "error": "当前账号库存中找不到该 assetid，请先刷新库存"}
    name = (item.get("market_hash_name") or item.get("name") or "").strip()
    try:
        db_set_inventory_ownership_override(assetid, normalized, name)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "assetid": str(assetid),
        "mode": normalized,
        "warning": (
            "该物品没有本地购入记录；策略3无法核算购入比例，因此仍会跳过自动出售"
            if normalized == "managed" and not any(
                str(p.get("assetid") or "").strip() == str(assetid or "").strip()
                for p in (get_purchases() or [])
            ) else ""
        ),
    }


def _sync_inventory_metadata(items: list, purchase_by_assetid: dict) -> int:
    import time

    changed = 0
    now = time.time()
    for item in items:
        assetid = str(item.get("assetid") or "").strip()
        purchase = purchase_by_assetid.get(assetid)
        if not purchase or not purchase.get("_db_id"):
            continue
        updates = {}
        cooldown_at = float(item.get("cooldown_at") or 0)
        local_cooldown = float(purchase.get("tradable_at") or 0)
        cooldown_text = str(item.get("cooldown_text") or "").strip()
        if cooldown_at > 0 and cooldown_at != local_cooldown:
            updates["tradable_at"] = cooldown_at
            updates["order_status"] = "trade_locked" if cooldown_at > now else "received"
        elif cooldown_text and not item.get("can_trade"):
            if purchase.get("order_status") != "trade_locked":
                updates["order_status"] = "trade_locked"
        elif not cooldown_at and item.get("can_trade"):
            if local_cooldown > now:
                updates["tradable_at"] = None
            if purchase.get("order_status") == "trade_locked":
                updates["order_status"] = "received"
        if updates and update_purchase_by_id(int(purchase["_db_id"]), updates):
            purchase.update(updates)
            changed += 1
    return changed
def _get_steam_smart_price_cny(session, market_hash_name: str, app_id: int = 730):
    return get_steam_smart_price_cny(session, market_hash_name, app_id=app_id)
def _enrich_inventory_with_steam_prices(items: list, old_items: list) -> None:
    """Fill lowest_price on inventory items using shared batch_fetch_prices."""
    old_price_by_name: dict = {}
    for it in (old_items or []):
        name = (it.get("market_hash_name") or it.get("name") or "").strip()
        p = it.get("lowest_price")
        if name and p is not None and float(p) > 0:
            if name not in old_price_by_name:
                old_price_by_name[name] = float(p)
    purchases = get_purchases() or []
    purchase_by_assetid = {
        str(p.get("assetid") or "").strip(): p
        for p in purchases
        if str(p.get("assetid") or "").strip()
    }
    purchase_by_name = {
        str(p.get("name") or "").strip(): p
        for p in purchases
        if str(p.get("name") or "").strip()
    }
    _sync_inventory_metadata(items, purchase_by_assetid)
    for it in items:
        name = (it.get("market_hash_name") or it.get("name") or "").strip()
        asset_purchase = purchase_by_assetid.get(str(it.get("assetid") or "").strip()) or {}
        price_purchase = asset_purchase or purchase_by_name.get(name) or {}
        local_price = price_purchase.get("current_market_price")
        if local_price is None:
            local_price = price_purchase.get("market_price")
        it["lowest_price"] = old_price_by_name.get(name, local_price or 0)
        if not it.get("cooldown_at") and asset_purchase.get("tradable_at"):
            cooldown_at = float(asset_purchase["tradable_at"])
            it["cooldown_at"] = cooldown_at
            it["cooldown_at_iso"] = datetime.fromtimestamp(
                cooldown_at,
                tz=timezone.utc,
            ).isoformat().replace("+00:00", "Z")
    names = set()
    for it in items:
        name = (it.get("market_hash_name") or it.get("name") or "").strip()
        if name:
            names.add(name)
    if not names:
        return
    prices = batch_fetch_prices(names)
    for it in items:
        name = (it.get("market_hash_name") or it.get("name") or "").strip()
        if name in prices:
            it["lowest_price"] = prices[name]
def _try_steam_auto_relogin():
    from app.services.steam_auth import try_steam_auto_relogin
    return try_steam_auto_relogin(force_login=True)
def _api_inventory_impl(refresh: bool = False, trigger_sell: bool = False):
    if refresh or not get_inventory():
        if not is_steam_background_allowed():
            return _inventory_response()
        ok, items, err = scan_cs2_inventory()
        if not ok and err and "登录已过期" in err:
            success, status, msg = _try_steam_auto_relogin()
            if status == "busy":
                import time as _time
                log("inventory: 检测到另一个自动登录正在进行，等待完成后重试库存…", "info", category="steam")
                for _wait in range(7):
                    _time.sleep(5)
                    ok2, items2, err2 = scan_cs2_inventory()
                    if ok2:
                        old = get_inventory()
                        _enrich_inventory_with_steam_prices(items2, old)
                        set_inventory(items2)
                        if trigger_sell:
                            run_sell_phase_on_inventory_update(items2)
                        log("inventory: 等待后库存获取成功", "info", category="steam")
                        return _inventory_response()
                    if not err2 or "登录已过期" not in err2:
                        break
                log("inventory: 等待其他登录完成超时，返回缓存库存", "warn", category="steam")
                return _inventory_response()
            if success:
                import time as _time
                log("auto_relogin: 登录成功，等待 Steam 服务端会话生效 (8s)…", "info", category="steam")
                _time.sleep(8)
                ok, items, err = scan_cs2_inventory()
                if ok:
                    old = get_inventory()
                    _enrich_inventory_with_steam_prices(items, old)
                    set_inventory(items)
                    if trigger_sell:
                        run_sell_phase_on_inventory_update(items)
                    return _inventory_response()
                if err and "登录已过期" in err:
                    log("auto_relogin: 首次重试仍过期，再等 7 秒…", "info", category="steam")
                    _time.sleep(7)
                    ok, items, err = scan_cs2_inventory()
                    if ok:
                        old = get_inventory()
                        _enrich_inventory_with_steam_prices(items, old)
                        set_inventory(items)
                        if trigger_sell:
                            run_sell_phase_on_inventory_update(items)
                        return _inventory_response()
                log(f"auto_relogin: 登录成功但库存获取仍失败: {err}，返回缓存", "warn", category="steam")
                return _inventory_response()
            out = {"items": [], "error": err, "auth_expired": True}
            if status == "need_2fa":
                out["auth_expired_reason"] = "need_2fa"
                out["error"] = "需要二次验证（验证码），请到库存页手动重新登录 Steam"
            elif status == "no_creds":
                out["auth_expired_reason"] = "no_creds"
            return _inventory_response([], **{key: value for key, value in out.items() if key != "items"})
        if not ok:
            out = {"items": [], "error": err}
            if err and ("登录已过期" in err or "未配置" in err):
                out["auth_expired"] = True
            return _inventory_response([], **{key: value for key, value in out.items() if key != "items"})
        old = get_inventory()
        _enrich_inventory_with_steam_prices(items, old)
        set_inventory(items)
        if trigger_sell:
            run_sell_phase_on_inventory_update(items)
    return _inventory_response()


@router.get("/api/inventory")
def api_inventory(refresh: bool = False, trigger_sell: bool = False):
    if not refresh and get_inventory():
        return _inventory_response()
    from app.account_operations import AccountOperationConflict, account_operation
    try:
        with account_operation("库存刷新"):
            return _api_inventory_impl(refresh=refresh, trigger_sell=trigger_sell)
    except AccountOperationConflict as exc:
        return _inventory_response(error=str(exc))
@router.get("/api/market-prices")
def api_market_prices():
    """统一批量市场价查询接口.
    一次性查出库存（lowest_price）和持有饰品（current_market_price）所需的全部
    唯一物品名称，每个名称只发一次 Steam API 请求，然后返回给前端同时刷新两个视图。
    """
    if not is_steam_background_allowed():
        return {"prices": {}, "error": "Steam 后台请求不可用"}
    from app.state import get_purchases
    inv_items = get_inventory() or []
    inv_names = {
        (it.get("market_hash_name") or it.get("name") or "").strip()
        for it in inv_items
    }
    purchases = get_purchases() or []
    holdings_names = {
        (p.get("name") or "").strip()
        for p in purchases
        if not (p.get("sale_price") is not None and float(p.get("sale_price") or 0) > 0)
    }
    all_names = {n for n in (inv_names | holdings_names) if n}
    if not all_names:
        return {"prices": {}}
    prices = batch_fetch_prices(all_names)
    import time
    updated_at = time.time()
    from app.database import db_update_current_prices
    updated_records = db_update_current_prices(prices, updated_at)
    return {
        "prices": prices,
        "updated_at": updated_at,
        "updated_names": len(prices),
        "updated_records": updated_records,
    }

def _api_sync_receipts_impl():
    """Process existing incoming trades and refresh local receipt state only."""
    if not is_steam_background_allowed():
        return {"ok": False, "error": "Steam 后台请求当前不可用"}
    from app.account_scope import validate_current_account_identity
    identity_ok, identity_error = validate_current_account_identity()
    if not identity_ok:
        return {"ok": False, "error": f"账号身份保护: {identity_error}"}
    from app.receive_flow import try_receive_once, reconcile_pending_purchases_from_inventory
    from app.state import get_purchases, update_purchase, update_purchase_by_id
    from app.config_loader import get_buff_credentials
    from app.order_state import reconcile_orders_from_local_records
    received = try_receive_once(
        get_purchases,
        update_purchase,
        lambda: (get_buff_credentials() or {}).get("cookies", ""),
        get_steam_credentials,
        scan_inventory=scan_cs2_inventory,
        update_purchase_by_id=update_purchase_by_id,
    )
    ok, items, err = scan_cs2_inventory()
    inventory_reconcile = {"matched": 0, "ambiguous": 0}
    if ok:
        old = get_inventory()
        _enrich_inventory_with_steam_prices(items, old)
        set_inventory(items)
        inventory_reconcile = reconcile_pending_purchases_from_inventory(
            get_purchases,
            items,
            update_purchase,
            update_purchase_by_id=update_purchase_by_id,
        )
        received += int(inventory_reconcile.get("matched") or 0)
    reconciled = reconcile_orders_from_local_records()
    unresolved = sum(
        1 for purchase in (get_purchases() or [])
        if purchase.get("pending_receipt") and not purchase.get("assetid")
    )
    return {
        "ok": bool(ok),
        "received": int(received or 0),
        "inventory_matched": int(inventory_reconcile.get("matched") or 0),
        "ambiguous": int(inventory_reconcile.get("ambiguous") or 0),
        "unresolved": int(unresolved),
        "items": get_inventory(),
        "orders_changed": reconciled.get("changed", 0),
        "error": None if ok else err,
    }


@router.post("/api/inventory/sync-receipts")
def api_sync_receipts():
    from app.account_operations import AccountOperationConflict, account_operation
    try:
        with account_operation("手动收货同步"):
            return _api_sync_receipts_impl()
    except AccountOperationConflict as exc:
        return {"ok": False, "error": str(exc)}
