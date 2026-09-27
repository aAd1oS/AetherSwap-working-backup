"""Transaction routes (purchases, sales, stats, delist, sync)."""
import math
from typing import Optional
from fastapi import APIRouter
from pydantic import BaseModel
from app.state import (
    append_purchase,
    delete_purchase,
    delete_purchase_by_id,
    delete_sale,
    delete_sale_by_id,
    get_purchases,
    get_sales,
    get_status,
    is_steam_background_allowed,
    log,
    reload_transactions,
    set_buff_auth_expired,
    set_buff_verification_required,
    update_purchase,
    update_purchase_by_id,
    update_sale,
)
from app.config_loader import (
    get_buff_credentials,
    get_steam_credentials,
    load_app_config_validated,
)
from app.shared_market import get_steam_smart_price_cny, batch_fetch_prices
from app.database import (
    db_get_purchase_orders,
    db_replace_cancelled_order_with_paid_purchase,
    db_update_purchase_order,
)
from buff.buyer import BuffAuthExpired, BuffVerificationRequired
from utils.buff_protection import BuffProtectionError
from app.services.buff_balance import (
    get_buff_balance,
    record_buff_balance_amount,
)
from app.services.buff_client import create_buff_client_from_config
from app.order_state import (
    BLOCKING_PAYMENT_STATUSES,
    ORDER_STATUS_LABELS,
    derive_purchase_status,
    get_daily_budget_summary,
    reconcile_manual_payment_orders,
    reconcile_orders_from_local_records,
)
router = APIRouter()


def _positive_amount(value) -> float:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return 0.0
    return amount if math.isfinite(amount) and amount > 0 else 0.0


class AddPurchaseBody(BaseModel):
    name: str = ""
    price: float = 0
    quantity: int = 1
    goods_id: Optional[int] = None
    steam_link: Optional[str] = None
    assetid: Optional[str] = None
class TransactionUpdateBody(BaseModel):
    type: str = "purchase"
    idx: int = 0
    db_id: Optional[int] = None  
    name: Optional[str] = None
    price: Optional[float] = None
    goods_id: Optional[int] = None
    market_price: Optional[float] = None
    sale_price: Optional[float] = None
    pending_receipt: Optional[bool] = None
    assetid: Optional[str] = None
    listing: Optional[bool] = None

class ReplacePaidOrderBody(BaseModel):
    new_external_order_id: str
    unit_price: float
    market_price: Optional[float] = None
def _name_from_steam_link(steam_link: str) -> Optional[str]:
    from steam.client import resolve_market_hash_name_from_listing_url
    from utils.proxy_manager import get_proxy_manager
    url = (steam_link or "").strip()
    if not url:
        return None
    pm = get_proxy_manager()
    for attempt in range(3):
        proxies = pm.get_steam_proxies()
        name = resolve_market_hash_name_from_listing_url(url, proxies=proxies)
        if name:
            return name
    return None
def _get_steam_smart_price_cny(session, market_hash_name: str, app_id: int = 730) -> Optional[float]:
    return get_steam_smart_price_cny(session, market_hash_name, app_id=app_id)
def _fetch_steam_lowest_cny(market_hash_name: str, app_id: int = 730) -> Optional[float]:
    name = (market_hash_name or "").strip()
    if not name:
        return None
    prices = batch_fetch_prices({name}, app_id=app_id)
    return prices.get(name)
def _enrich_purchases_with_current_prices(transactions: list) -> None:
    """Fill current_market_price on unsold purchase records using shared batch_fetch_prices."""
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
@router.get("/api/purchases")
def api_purchases():
    return {"purchases": get_purchases()}
@router.post("/api/purchase")
def api_add_purchase(body: AddPurchaseBody):
    name = (body.name or "").strip()
    steam_link = (body.steam_link or "").strip()
    if steam_link:
        extracted = _name_from_steam_link(steam_link)
        if extracted:
            name = extracted
    if not name:
        return {"ok": False, "error": "请填写物品名称或有效的 Steam 市场链接"}
    if body.price <= 0:
        return {"ok": False, "error": "价格须大于 0"}
    qty = max(1, int(body.quantity)) if body.quantity is not None else 1
    goods_id = int(body.goods_id) if body.goods_id is not None else 0
    import time
    now = time.time()
    price = round(float(body.price), 2)
    market_price = _fetch_steam_lowest_cny(name)
    assetid_val = (body.assetid or "").strip() or None
    for _ in range(qty):
        rec = {"name": name, "goods_id": goods_id, "price": price, "at": now, "source": "manual"}
        if market_price is not None and market_price > 0:
            rec["market_price"] = round(float(market_price), 2)
        if assetid_val is not None:
            rec["assetid"] = assetid_val
        append_purchase(rec)
    return {"ok": True, "added": qty}
@router.get("/api/transactions")
def api_transactions(enrich_current_price: bool = False):
    purchases = get_purchases()
    sales = get_sales()
    out = []
    for i, p in enumerate(purchases):
        row = {"type": "purchase", "idx": i, "name": p.get("name", ""), "goods_id": p.get("goods_id", ""), "price": float(p.get("price", 0)), "at": p.get("at", 0)}
        if p.get("_db_id"):
            row["db_id"] = p.get("_db_id")  
        mp = p.get("market_price")
        if mp is not None:
            row["market_price"] = round(float(mp), 2)
        sp = p.get("sale_price")
        if sp is not None:
            row["sale_price"] = round(float(sp), 2)
        sa = p.get("sold_at")
        if sa is not None:
            row["sold_at"] = float(sa)
        if p.get("pending_receipt") is not None:
            row["pending_receipt"] = bool(p.get("pending_receipt"))
        if p.get("assetid") is not None:
            row["assetid"] = p.get("assetid") if isinstance(p.get("assetid"), str) else str(p.get("assetid"))
        if p.get("listing") is not None:
            row["listing"] = bool(p.get("listing"))
        if p.get("listing_status") is not None:
            row["listing_status"] = p.get("listing_status")
        if p.get("external_order_id") is not None:
            row["external_order_id"] = p.get("external_order_id")
        row["source"] = p.get("source") or "legacy"
        row["order_status"] = derive_purchase_status(p)
        row["order_status_label"] = ORDER_STATUS_LABELS.get(row["order_status"], row["order_status"])
        if p.get("current_market_price") is not None:
            row["current_market_price"] = round(float(p.get("current_market_price")), 2)
        if p.get("current_price_updated_at") is not None:
            row["current_price_updated_at"] = float(p.get("current_price_updated_at"))
        if p.get("received_at") is not None:
            row["received_at"] = float(p.get("received_at"))
        if p.get("listed_at") is not None:
            row["listed_at"] = float(p.get("listed_at"))
        if p.get("listing_price") is not None:
            row["listing_price"] = round(float(p.get("listing_price")), 2)
        if p.get("tradable_at") is not None:
            row["tradable_at"] = float(p.get("tradable_at"))
        out.append(row)
    for i, s in enumerate(sales):
        row = {"type": "sale", "idx": i, "name": s.get("name", ""), "goods_id": s.get("goods_id", ""), "price": float(s.get("price", 0)), "at": s.get("at", 0), "assetid": s.get("assetid") or ""}
        if s.get("_db_id"):
            row["db_id"] = s.get("_db_id")
        out.append(row)
    out.sort(key=lambda x: x["at"], reverse=True)
    if enrich_current_price and is_steam_background_allowed():
        _enrich_purchases_with_current_prices(out)
    cfg = load_app_config_validated().get("pipeline", {})
    resell_ratio = float(cfg.get("resell_ratio", 0.85))
    if resell_ratio <= 0:
        resell_ratio = 0.85
    from app.accounts import get_current_account
    account = get_current_account() or {}
    return {
        "transactions": out,
        "resell_ratio": resell_ratio,
        "account": {
            "id": account.get("id") or "",
            "name": account.get("display_name") or account.get("username") or account.get("steam_id") or "未选择账号",
            "steam_id": account.get("steam_id") or "",
        },
    }

@router.get("/api/orders")
def api_orders():
    reconcile_orders_from_local_records()
    cfg = load_app_config_validated().get("pipeline", {})
    target = float(cfg.get("target_balance", 100) or 100)
    orders = db_get_purchase_orders()
    attention_hints = {
        "awaiting_payment": "等待用户付款或平台取消",
        "user_confirmed": "用户已确认；请用平台复核确认真实付款结果",
        "platform_confirmed": "平台已确认；批量订单仍需继续核销",
        "payment_unconfirmed": "平台付款结果不明确，禁止自动重试",
        "needs_review": "自动链路已失败关闭，请人工核对",
        "awaiting_trade": "报价已唯一匹配；若自动接受失败，请到 Steam 手动接受",
    }
    for order in orders:
        status = str(order.get("status") or "")
        source = str(order.get("source") or "")
        order["status_label"] = ORDER_STATUS_LABELS.get(status, status)
        order["blocking"] = status in BLOCKING_PAYMENT_STATUSES
        order["needs_attention"] = order["blocking"] or status == "awaiting_trade"
        order["attention_hint"] = attention_hints.get(status, "")
        actions = []
        if status in {"awaiting_payment", "payment_unconfirmed", "needs_review"} and not (
            source == "buff_balance" and status == "awaiting_payment"
        ):
            actions.append("cancel")
        if status in {"awaiting_payment", "user_confirmed", "payment_unconfirmed", "needs_review"}:
            actions.append("replace_paid")
        order["available_actions"] = actions
    return {
        "orders": list(reversed(orders)),
        "attention_count": sum(bool(order["needs_attention"]) for order in orders),
        "budget": get_daily_budget_summary(target),
    }


@router.post("/api/orders/reconcile")
def api_reconcile_payment_orders():
    credentials = get_buff_credentials() or {}
    if not (credentials.get("cookies") or "").strip():
        return {"ok": False, "error": "当前账号没有可用的 BUFF Cookie"}
    try:
        config = load_app_config_validated()
        client = create_buff_client_from_config(
            credentials,
            config,
            steam_credentials=get_steam_credentials(),
        )
        result = reconcile_manual_payment_orders(client)
        reconcile_orders_from_local_records()
        return {"ok": True, **result}
    except (BuffAuthExpired, BuffVerificationRequired, BuffProtectionError) as exc:
        return {"ok": False, "error": str(exc) or type(exc).__name__}
    except Exception as exc:
        return {"ok": False, "error": f"平台复核失败: {type(exc).__name__}: {exc}"}


@router.post("/api/order/{external_order_id}/cancel")
def api_cancel_unresolved_order(external_order_id: str):
    order = next(
        (row for row in db_get_purchase_orders() if row.get("external_order_id") == external_order_id),
        None,
    )
    if order is None:
        return {"ok": False, "error": "订单不存在"}
    if order.get("status") not in {"awaiting_payment", "payment_unconfirmed", "needs_review"}:
        return {"ok": False, "error": "该订单不能直接取消；请先用平台复核确认真实状态"}
    if order.get("source") == "buff_balance" and order.get("status") == "awaiting_payment":
        return {"ok": False, "error": "余额订单正在自动扣款，当前不能取消；请等待结果明确后再处理"}
    expected_status = str(order.get("status") or "")
    ok = db_update_purchase_order(external_order_id, {
        "status": "cancelled",
        "error": "用户在确认平台订单已取消后手动解除阻断",
    }, expected_statuses={expected_status})
    return {
        "ok": ok,
        "error": None if ok else "订单状态刚刚发生变化，请刷新后重新核对",
    }

@router.post("/api/order/{external_order_id}/replace-paid")
def api_replace_cancelled_order_with_paid_order(
    external_order_id: str,
    body: ReplacePaidOrderBody,
):
    try:
        result = db_replace_cancelled_order_with_paid_purchase(
            external_order_id,
            body.new_external_order_id,
            body.unit_price,
            body.market_price,
        )
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    log(
        f"订单修正: 旧单 {external_order_id} 已取消，已登记手工付款新单 "
        f"{result['new_external_order_id']}，金额={result['total_price']:.2f}",
        "info",
        category="buff",
    )
    return {"ok": True, "order": result}

@router.get("/api/order/{external_order_id}/current-market-price")
def api_replacement_order_current_market_price(external_order_id: str):
    order = next(
        (row for row in db_get_purchase_orders() if row.get("external_order_id") == external_order_id),
        None,
    )
    if order is None:
        return {"ok": False, "error": "订单不存在"}
    if order.get("status") not in BLOCKING_PAYMENT_STATUSES:
        return {"ok": False, "error": "该订单当前不是待支付或待核对状态"}
    if not is_steam_background_allowed():
        return {"ok": False, "error": "Steam 后台请求当前不可用，请停止任务后重试"}
    market_price = _fetch_steam_lowest_cny(order.get("name") or "")
    if market_price is None or float(market_price) <= 0:
        return {"ok": False, "error": "暂时无法获取该商品的 Steam 当前市场价"}
    return {
        "ok": True,
        "name": order.get("name") or "",
        "market_price": round(float(market_price), 2),
    }
@router.delete("/api/transaction")
def api_delete_transaction(type: str = "purchase", idx: int = 0, db_id: int = 0):
    if type == "purchase":
        ok = delete_purchase_by_id(db_id) if db_id else delete_purchase(idx)
    elif type == "sale":
        ok = delete_sale_by_id(db_id) if db_id else delete_sale(idx)
    else:
        return {"ok": False, "error": "type 须为 purchase 或 sale"}
    return {"ok": ok, "error": None if ok else "记录不存在或索引无效"} if ok else {"ok": False, "error": "记录不存在或索引无效"}
@router.put("/api/transaction")
def api_update_transaction(body: TransactionUpdateBody):
    data: dict = {}
    if body.name is not None:
        data["name"] = body.name
    if body.price is not None:
        data["price"] = round(float(body.price), 2)
    if body.goods_id is not None:
        data["goods_id"] = int(body.goods_id)
    if body.market_price is not None:
        if float(body.market_price) > 0:
            data["market_price"] = round(float(body.market_price), 2)
        else:
            data["market_price"] = None
    if body.sale_price is not None:
        if float(body.sale_price) > 0:
            data["sale_price"] = round(float(body.sale_price), 2)
            data["sold_at"] = __import__("time").time()
        else:
            data["sale_price"] = None
            data["sold_at"] = None
    if body.pending_receipt is not None:
        data["pending_receipt"] = bool(body.pending_receipt)
    if body.assetid is not None:
        data["assetid"] = body.assetid if body.assetid else None
    if body.listing is not None:
        data["listing"] = bool(body.listing)
        if not body.listing:
            data["listing_status"] = None
    ok = False
    if body.type == "purchase":
        ok = update_purchase_by_id(body.db_id, data) if body.db_id else update_purchase(body.idx, data)
    elif body.type == "sale":
        ok = update_sale(body.idx, data)  
    else:
        return {"ok": False, "error": "type 须为 purchase 或 sale"}
    return {"ok": ok, "error": None if ok else "更新失败（记录不存在或无效）"} if ok else {"ok": False, "error": "更新失败"}
@router.get("/api/stats")
def api_stats():
    purchases = get_purchases()
    pipeline_cfg = load_app_config_validated().get("pipeline", {})
    resell_ratio = max(0.01, min(1.0, float(pipeline_cfg.get("resell_ratio", 0.85) or 0.85)))
    total_invested = sum(_positive_amount(p.get("price")) for p in purchases)
    sold_purchases = [p for p in purchases if _positive_amount(p.get("sale_price")) > 0]
    total_sold = sum(_positive_amount(p.get("sale_price")) for p in sold_purchases)
    total_sold_after_tax = total_sold / 1.15
    total_sold_cost = sum(_positive_amount(p.get("price")) for p in sold_purchases)
    ratio_sum = 0.0
    ratio_count = 0
    total_self_use_profit = 0.0
    total_conversion_profit = 0.0
    for p in sold_purchases:
        after_tax = _positive_amount(p.get("sale_price")) / 1.15
        cost = _positive_amount(p.get("price"))
        total_self_use_profit += after_tax - cost
        total_conversion_profit += after_tax * resell_ratio - cost
        if after_tax > 0 and cost > 0:
            ratio_sum += cost / after_tax
            ratio_count += 1
    discount_ratio = (ratio_sum / ratio_count) if ratio_count > 0 else None
    return {
        "total_invested": round(total_invested, 2),
        "total_purchased": round(total_invested, 2),
        "total_sold": round(total_sold, 2),
        "total_sold_after_tax": round(total_sold_after_tax, 2),
        "total_sold_cost": round(total_sold_cost, 2),
        "total_profit": round(total_self_use_profit, 2),
        "total_self_use_profit": round(total_self_use_profit, 2),
        "total_conversion_profit": round(total_conversion_profit, 2),
        "resell_ratio": resell_ratio,
        "discount_ratio": round(discount_ratio, 4) if discount_ratio is not None else None,
        "buff_balance": get_buff_balance(),
    }


@router.post("/api/buff/balance/refresh")
def api_refresh_buff_balance():
    cached = get_buff_balance()
    if get_status().get("status") == "running":
        return {
            "ok": False,
            "error": "任务正在运行，余额会在购买预检时自动更新；为避免增加 BUFF 请求，本次未刷新",
            "buff_balance": cached,
        }

    buff_credentials = get_buff_credentials() or {}
    steam_credentials = get_steam_credentials() or {}
    if not str(buff_credentials.get("cookies") or "").strip():
        return {"ok": False, "error": "尚未配置 BUFF Cookie", "buff_balance": cached}

    try:
        client = create_buff_client_from_config(
            buff_credentials,
            load_app_config_validated(),
            steam_credentials,
        )
        observation = client.get_available_funds_once()
        if observation.get("ok") is not True or observation.get("balance") is None:
            return {
                "ok": False,
                "error": observation.get("reason") or "BUFF 账户资产接口未返回可识别余额",
                "buff_balance": cached,
            }
        balance = record_buff_balance_amount(
            observation.get("balance"),
            source="account_asset",
        )
        if not balance.get("has_value"):
            return {
                "ok": False,
                "error": observation.get("reason") or "BUFF 账户资产接口未返回可识别余额",
                "buff_balance": balance,
            }
        log(
            f"BUFF 可用资金已手动刷新: {float(balance['balance']):.2f}",
            "info",
            category="buff",
        )
        return {"ok": True, "buff_balance": balance}
    except BuffAuthExpired:
        set_buff_auth_expired(True)
        return {
            "ok": False,
            "error": "BUFF 登录已过期，请更新 Cookie 后重试",
            "buff_balance": cached,
        }
    except BuffVerificationRequired as exc:
        set_buff_verification_required(True, str(exc))
        return {
            "ok": False,
            "error": f"BUFF 需要先完成页面验证: {exc}",
            "buff_balance": cached,
        }
    except BuffProtectionError as exc:
        return {
            "ok": False,
            "error": f"BUFF 请求保护暂时阻止刷新: {exc}",
            "buff_balance": cached,
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": f"BUFF 余额刷新失败: {type(exc).__name__}: {exc}",
            "buff_balance": cached,
        }
@router.post("/api/purchase/{idx}/delist")
def api_delist_purchase(idx: int):
    from app.account_operations import AccountOperationConflict, account_operation
    from app.steam_delist import delist_item
    purchases = get_purchases()
    if idx < 0 or idx >= len(purchases):
        return {"ok": False, "error": "索引无效"}
    p = purchases[idx]
    if not p.get("listing"):
        return {"ok": False, "error": "该记录非出售中状态"}
    assetid = str(p.get("assetid") or "").strip()
    if not assetid:
        return {"ok": False, "error": "无 assetid"}
    name = (p.get("name") or "").strip()
    def log_fn(msg: str, level: str = "info"):
        log(msg, level, category="delist")
    try:
        with account_operation("手动下架"):
            ok, new_assetid, err = delist_item(assetid, name, log_fn=log_fn)
    except AccountOperationConflict as exc:
        return {"ok": False, "error": str(exc)}
    if not ok:
        log(err or "下架失败", "error", category="delist")
        return {"ok": False, "error": err}
    updates = {
        "listing": False,
        "listing_status": None if new_assetid else "assetid_pending",
        "listed_at": None,
        "listing_price": None,
        "stale_listing_notified_at": None,
        "listing_review_after": None,
    }
    if new_assetid:
        updates["assetid"] = new_assetid
    try:
        update_purchase(idx, updates)
    except Exception as exc:
        from app.database import DuplicateAssetIdError
        if isinstance(exc, DuplicateAssetIdError):
            return {"ok": False, "error": str(exc)}
        raise
    out = {"ok": True, "assetid": new_assetid or assetid}
    if new_assetid is None:
        out["message"] = "下架成功，但未检测到新 assetid，正自动尝试同步补全..."
        log_fn("未检测到新 assetid，开始自动同步售出/持有", "info")
        from app.sync_sold import run_sync_sold_from_history
        try:
            ok_s, res_s = run_sync_sold_from_history(log_fn=log_fn)
            if ok_s:
                reload_transactions()
                pur = get_purchases()
                if 0 <= idx < len(pur):
                    auto_assetid = pur[idx].get("assetid")
                    if auto_assetid:
                        out["assetid"] = auto_assetid
                        out["message"] = f"自动同步成功，已补全 assetid: {auto_assetid}"
                        update_purchase(idx, {"assetid": auto_assetid, "listing": False, "listing_status": None})
        except Exception as e:
            log_fn(f"自动同步失败: {e}", "error")
    return out
@router.post("/api/sync_sold_from_history")
def api_sync_sold_from_history():
    from app.sync_sold import run_sync_sold_from_history
    def log_fn(msg: str, level: str = "info"):
        log(msg, level, category="sync_sold")
    try:
        ok, result = run_sync_sold_from_history(log_fn=log_fn)
        if not ok:
            return {"ok": False, "error": result.get("error", "同步失败")}
        reload_transactions()
        return {
            "ok": True,
            "updated": result.get("updated", 0),
            "filled": result.get("filled", 0),
            "sold_count": result.get("sold_count", 0),
        }
    except Exception as e:
        log(str(e), "error", category="sync_sold")
        return {"ok": False, "error": str(e)[:200]}
@router.post("/api/repair_error_records")
def api_repair_error_records():
    from app.repair_error_records import run as run_repair
    def log_fn(msg: str, level: str = "info"):
        log(msg, level, category="repair")
    try:
        ok, result = run_repair(log_fn=log_fn)
        if not ok:
            return {"ok": False, "error": result.get("error", "紧急修复失败")}
        reload_transactions()
        return {
            "ok": True,
            "filled": result.get("filled", 0),
            "missing": result.get("missing", 0),
            "total": result.get("total", 0),
            "preserved_sold": result.get("preserved_sold", 0),
            "changed": result.get("changed", 0),
        }
    except Exception as e:
        log(str(e), "error", category="repair")
        return {"ok": False, "error": str(e)[:200]}
