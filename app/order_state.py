"""Persistent purchase-order state and spending guards."""
from datetime import datetime, time as datetime_time, timedelta
from typing import Optional

from app.database import (
    db_get_purchase_orders,
    db_get_purchases,
    db_update_purchase_order,
)


BLOCKING_PAYMENT_STATUSES = frozenset({
    "awaiting_payment",
    "user_confirmed",
    "payment_unconfirmed",
    "needs_review",
})

ORDER_STATUS_LABELS = {
    "awaiting_payment": "待支付",
    "user_confirmed": "用户已确认付款",
    "payment_unconfirmed": "付款待核对",
    "awaiting_ship": "待卖家发送 Steam 报价",
    "awaiting_trade": "报价已发送，待在 Steam 接受",
    "received": "已入库",
    "trade_locked": "交易冷却中",
    "tradable": "可上架",
    "listed": "出售中",
    "sold": "已售出",
    "cancelled": "已取消",
    "refunded": "已退款",
    "failed": "失败",
    "needs_review": "需要人工处理",
}


def get_blocking_payment_orders() -> list:
    return [
        order for order in db_get_purchase_orders()
        if order.get("status") in BLOCKING_PAYMENT_STATUSES
    ]


def _local_day_bounds(now: Optional[datetime] = None) -> tuple[float, float]:
    current = now or datetime.now()
    start = datetime.combine(current.date(), datetime_time.min)
    end = start + timedelta(days=1)
    return start.timestamp(), end.timestamp()


def get_daily_budget_summary(target: float, now: Optional[datetime] = None) -> dict:
    start_ts, end_ts = _local_day_bounds(now)
    purchases = [
        row for row in db_get_purchases()
        if start_ts <= float(row.get("at") or 0) < end_ts
        and row.get("source") != "manual"
    ]
    confirmed = round(sum(float(row.get("price") or 0) for row in purchases), 2)
    linked_order_ids = {
        str(row.get("external_order_id"))
        for row in purchases if row.get("external_order_id")
    }
    reserved_orders = [
        order for order in db_get_purchase_orders()
        if start_ts <= float(order.get("created_at") or 0) < end_ts
        and order.get("status") in BLOCKING_PAYMENT_STATUSES
        and str(order.get("external_order_id") or "") not in linked_order_ids
    ]
    reserved = round(sum(float(order.get("total_price") or 0) for order in reserved_orders), 2)
    used = round(confirmed + reserved, 2)
    return {
        "target": round(float(target), 2),
        "confirmed": confirmed,
        "reserved": reserved,
        "used": used,
        "remaining": round(max(0.0, float(target) - used), 2),
        "blocking_orders": reserved_orders,
        "date": datetime.fromtimestamp(start_ts).date().isoformat(),
    }


def derive_purchase_status(purchase: dict) -> str:
    if purchase.get("sale_price") is not None and float(purchase.get("sale_price") or 0) > 0:
        return "sold"
    if purchase.get("listing"):
        return "listed"
    if purchase.get("pending_receipt"):
        explicit_status = purchase.get("order_status")
        if explicit_status in {"awaiting_ship", "awaiting_trade", "needs_review"}:
            return explicit_status
        return "awaiting_trade"
    if purchase.get("assetid"):
        tradable_at = float(purchase.get("tradable_at") or 0)
        if tradable_at > datetime.now().timestamp():
            return "trade_locked"
        return "received"
    return purchase.get("order_status") or "needs_review"


def reconcile_orders_from_local_records() -> dict:
    purchases = db_get_purchases()
    grouped: dict[str, list] = {}
    for purchase in purchases:
        order_id = str(purchase.get("external_order_id") or "").strip()
        if order_id:
            grouped.setdefault(order_id, []).append(purchase)

    changed = 0
    for order in db_get_purchase_orders():
        order_id = str(order.get("external_order_id") or "")
        records = grouped.get(order_id) or []
        if not records:
            continue
        statuses = [derive_purchase_status(row) for row in records]
        if statuses and all(status == "sold" for status in statuses):
            status = "sold"
        elif "listed" in statuses:
            status = "listed"
        elif "trade_locked" in statuses:
            status = "trade_locked"
        elif any(status == "received" for status in statuses):
            status = "received"
        elif "awaiting_trade" in statuses:
            status = "awaiting_trade"
        else:
            status = order.get("status") or "needs_review"
        if status != order.get("status"):
            data = {"status": status, "error": None}
            if status in {"received", "trade_locked", "listed", "sold"}:
                data["received_at"] = min(
                    [float(row.get("received_at") or 0) for row in records if row.get("received_at")]
                    or [datetime.now().timestamp()]
                )
            if db_update_purchase_order(order_id, data):
                changed += 1
    return {"changed": changed, "orders": db_get_purchase_orders()}
