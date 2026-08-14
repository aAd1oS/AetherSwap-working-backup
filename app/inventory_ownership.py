"""Account-scoped inventory ownership classification."""
from __future__ import annotations

from typing import Iterable

from app.database import db_get_inventory_ownership_overrides


def annotate_inventory_ownership(items: list, purchases: Iterable[dict]) -> list:
    tracked_assetids = {
        str(purchase.get("assetid") or "").strip()
        for purchase in (purchases or [])
        if str(purchase.get("assetid") or "").strip()
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
