"""Account-scoped inventory ownership classification."""
from __future__ import annotations

from typing import Iterable

from app.database import db_get_inventory_ownership_overrides


_NON_MANAGED_ORDER_STATUSES = {
    "awaiting_payment",
    "user_confirmed",
    "payment_unconfirmed",
    "awaiting_ship",
    "awaiting_trade",
    "cancelled",
    "refunded",
    "failed",
    "needs_review",
    "sold",
}


def _is_completed_purchase(purchase: dict) -> bool:
    if not str(purchase.get("assetid") or "").strip():
        return False
    if purchase.get("pending_receipt"):
        return False
    try:
        if float(purchase.get("sale_price") or 0) > 0:
            return False
    except (TypeError, ValueError):
        return False
    status = str(purchase.get("order_status") or "").strip().lower()
    return status not in _NON_MANAGED_ORDER_STATUSES


def annotate_inventory_ownership(items: list, purchases: Iterable[dict]) -> list:
    tracked_assetids = {
        str(purchase.get("assetid") or "").strip()
        for purchase in (purchases or [])
        if _is_completed_purchase(purchase)
    }
    overrides = db_get_inventory_ownership_overrides()
    for item in items or []:
        assetid = str(item.get("assetid") or "").strip()
        override = overrides.get(assetid)
        if override in {"personal", "managed"}:
            mode = override
            source = "manual"
        elif assetid and assetid in tracked_assetids:
            mode = "managed"
            source = "purchase"
        else:
            mode = "personal"
            source = "default"
        item["ownership_mode"] = mode
        item["ownership_source"] = source
        item["managed_by_aetherswap"] = mode == "managed"
    return items
