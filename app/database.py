import json
import math
import os
import threading
from pathlib import Path
from typing import List, Optional
from sqlmodel import Field, Session, SQLModel, create_engine, select
from sqlalchemy import update as sql_update
_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
_DB_PATH = Path(os.environ.get("AETHERSWAP_DB_PATH") or (_CONFIG_DIR / "app.db"))
_TRANSACTIONS_JSON = _CONFIG_DIR / "transactions.json"
_TRANSACTIONS_BAK = _CONFIG_DIR / "transactions.json.bak"
_WILSON_Z = 1.96
def _compute_wilson_score(positive_rate, total_reviews):
    # Wilson Score 置信下界，review少的游戏即使满分也会被降权
    # 参考: https://www.evanmiller.org/how-not-to-sort-by-average-rating.html
    n = total_reviews or 0
    if n <= 0 or positive_rate is None:
        return 0.0
    p = positive_rate / 100.0
    z = _WILSON_Z
    z2 = z * z
    numerator = p + z2 / (2 * n) - z * math.sqrt((p * (1 - p) + z2 / (4 * n)) / n)
    denominator = 1 + z2 / n
    return max(0.0, numerator / denominator)
class Purchase(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = ""
    goods_id: int = 0
    price: float = 0.0
    at: float = 0.0
    market_price: Optional[float] = None
    sale_price: Optional[float] = None
    sold_at: Optional[float] = None
    pending_receipt: Optional[bool] = None
    assetid: Optional[str] = None
    listing: Optional[bool] = None
    listing_status: Optional[str] = None
    external_order_id: Optional[str] = Field(default=None, index=True)
    source: str = "legacy"
    order_status: Optional[str] = None
    current_market_price: Optional[float] = None
    current_price_updated_at: Optional[float] = None
    received_at: Optional[float] = None
    tradable_at: Optional[float] = None
    account_id: str = Field(default="", index=True)

class PurchaseOrder(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    external_order_id: str = Field(index=True, unique=True)
    name: str = ""
    goods_id: int = 0
    quantity: int = 1
    unit_price: float = 0.0
    total_price: float = 0.0
    status: str = "awaiting_payment"
    source: str = "buff"
    created_at: float = 0.0
    updated_at: float = 0.0
    user_confirmed_at: Optional[float] = None
    paid_at: Optional[float] = None
    received_at: Optional[float] = None
    error: Optional[str] = None
    account_id: str = Field(default="", index=True)
class Sale(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = ""
    goods_id: int = 0
    price: float = 0.0
    at: float = 0.0
    assetid: Optional[str] = None
    account_id: str = Field(default="", index=True)

class SteamAccountRuntime(SQLModel, table=True):
    account_id: str = Field(primary_key=True)
    cookies: str = ""
    session_id: str = ""
    steam_id: str = ""
    shared_secret: str = ""
    identity_secret: str = ""
    device_id: str = ""
    auto_confirm_enabled: bool = False
    auto_sell_enabled: bool = False
    updated_at: float = 0.0

class InventoryOwnershipOverride(SQLModel, table=True):
    account_id: str = Field(primary_key=True)
    assetid: str = Field(primary_key=True)
    mode: str = "personal"
    market_hash_name: str = ""
    updated_at: float = 0.0
class ItemNameId(SQLModel, table=True):
    market_hash_name: str = Field(primary_key=True)
    item_nameid: str
class SteamDealGame(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    app_id: str = Field(index=True, unique=True)
    name: str = ""
    name_en: str = ""
    banner_url: str = ""
    positive_rate: Optional[float] = None   
    total_reviews: int = 0
    discount_percent: int = 0               
    deal_status: Optional[str] = None       
    price_cn: Optional[str] = None
    price_ru: Optional[str] = None
    price_kz: Optional[str] = None
    price_ua: Optional[str] = None
    price_pk: Optional[str] = None
    price_tr: Optional[str] = None
    price_ar: Optional[str] = None
    price_az: Optional[str] = None
    price_vn: Optional[str] = None
    price_id: Optional[str] = None
    price_in: Optional[str] = None
    price_br: Optional[str] = None
    price_cl: Optional[str] = None
    price_jp: Optional[str] = None
    price_hk: Optional[str] = None
    price_ph: Optional[str] = None
    original_cn: Optional[str] = None
    discount_cn: Optional[str] = None
    discount_ru: Optional[str] = None
    discount_kz: Optional[str] = None
    discount_ua: Optional[str] = None
    discount_pk: Optional[str] = None
    discount_tr: Optional[str] = None
    discount_ar: Optional[str] = None
    discount_az: Optional[str] = None
    discount_vn: Optional[str] = None
    discount_id: Optional[str] = None
    discount_in: Optional[str] = None
    discount_br: Optional[str] = None
    discount_cl: Optional[str] = None
    discount_jp: Optional[str] = None
    discount_hk: Optional[str] = None
    discount_ph: Optional[str] = None
    fetched_at: float = 0.0                 
    wilson_score: Optional[float] = None    
_engine = None
_engine_lock = threading.Lock()
def get_engine():
    global _engine
    if _engine is not None:
        return _engine
    with _engine_lock:
        if _engine is not None:
            return _engine
        _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(
            f"sqlite:///{_DB_PATH}",
            echo=False,
            connect_args={"check_same_thread": False},
        )
        return _engine
def get_session() -> Session:
    return Session(get_engine())

def _current_account_id(account_id: Optional[str] = None) -> str:
    if account_id is not None:
        return str(account_id).strip()
    try:
        from app.accounts import get_current_id
        return str(get_current_id() or "").strip()
    except Exception:
        return ""
def init_db() -> None:
    """Create all tables if they don't exist, and run lightweight migrations."""
    from sqlalchemy import text as sa_text
    engine = get_engine()
    SQLModel.metadata.create_all(engine)
    with engine.connect() as conn:
        try:
            conn.execute(sa_text("ALTER TABLE steamdealgame ADD COLUMN wilson_score REAL"))
            conn.commit()
        except Exception:
            pass  
    with engine.connect() as conn:
        try:
            conn.execute(sa_text(
                "ALTER TABLE steamaccountruntime "
                "ADD COLUMN auto_sell_enabled BOOLEAN DEFAULT 0"
            ))
            # Existing accounts with tracked purchases keep their prior selling behavior.
            # Empty/new accounts remain protected until the user explicitly opts in.
            conn.execute(sa_text(
                "UPDATE steamaccountruntime SET auto_sell_enabled = 1 "
                "WHERE account_id IN ("
                "SELECT DISTINCT account_id FROM purchase "
                "WHERE account_id IS NOT NULL AND account_id != ''"
                ")"
            ))
            conn.commit()
        except Exception:
            pass
    purchase_columns = {
        "external_order_id": "TEXT",
        "source": "TEXT DEFAULT 'legacy'",
        "order_status": "TEXT",
        "current_market_price": "REAL",
        "current_price_updated_at": "REAL",
        "received_at": "REAL",
        "tradable_at": "REAL",
        "account_id": "TEXT DEFAULT ''",
    }
    with engine.connect() as conn:
        for column, sql_type in purchase_columns.items():
            try:
                conn.execute(sa_text(f"ALTER TABLE purchase ADD COLUMN {column} {sql_type}"))
                conn.commit()
            except Exception:
                pass
    for table_name in ("purchaseorder", "sale"):
        with engine.connect() as conn:
            try:
                conn.execute(sa_text(f"ALTER TABLE {table_name} ADD COLUMN account_id TEXT DEFAULT ''"))
                conn.commit()
            except Exception:
                pass
    current_account_id = _current_account_id()
    if current_account_id:
        with engine.connect() as conn:
            for table_name in ("purchase", "purchaseorder", "sale"):
                conn.execute(
                    sa_text(f"UPDATE {table_name} SET account_id = :account_id WHERE account_id IS NULL OR account_id = ''"),
                    {"account_id": current_account_id},
                )
                conn.execute(sa_text(
                    f"CREATE INDEX IF NOT EXISTS ix_{table_name}_account_id ON {table_name} (account_id)"
                ))
            conn.commit()
    with engine.connect() as conn:
        rows = conn.execute(
            sa_text("SELECT id, positive_rate, total_reviews FROM steamdealgame WHERE wilson_score IS NULL")
        ).fetchall()
        if rows:
            for row in rows:
                ws = _compute_wilson_score(row[1], row[2])
                conn.execute(
                    sa_text("UPDATE steamdealgame SET wilson_score = :ws WHERE id = :id"),
                    {"ws": ws, "id": row[0]},
                )
            conn.commit()
def _purchase_from_dict(d: dict) -> Purchase:
    return Purchase(
        name=d.get("name", ""),
        goods_id=int(d.get("goods_id", 0) or 0),
        price=float(d.get("price", 0)),
        at=float(d.get("at", 0)),
        market_price=float(d["market_price"]) if d.get("market_price") is not None else None,
        sale_price=float(d["sale_price"]) if d.get("sale_price") is not None else None,
        sold_at=float(d["sold_at"]) if d.get("sold_at") is not None else None,
        pending_receipt=bool(d["pending_receipt"]) if d.get("pending_receipt") is not None else None,
        assetid=str(d["assetid"]) if d.get("assetid") is not None else None,
        listing=bool(d["listing"]) if d.get("listing") is not None else None,
        listing_status=str(d["listing_status"]) if d.get("listing_status") is not None else None,
        external_order_id=str(d["external_order_id"]) if d.get("external_order_id") is not None else None,
        source=str(d.get("source") or "legacy"),
        order_status=str(d["order_status"]) if d.get("order_status") is not None else None,
        current_market_price=float(d["current_market_price"]) if d.get("current_market_price") is not None else None,
        current_price_updated_at=float(d["current_price_updated_at"]) if d.get("current_price_updated_at") is not None else None,
        received_at=float(d["received_at"]) if d.get("received_at") is not None else None,
        tradable_at=float(d["tradable_at"]) if d.get("tradable_at") is not None else None,
        account_id=_current_account_id(d.get("account_id") or None),
    )
def _sale_from_dict(d: dict) -> Sale:
    return Sale(
        name=d.get("name", ""),
        goods_id=int(d.get("goods_id", 0) or 0),
        price=float(d.get("price", 0)),
        at=float(d.get("at", 0)),
        assetid=str(d["assetid"]) if d.get("assetid") is not None else None,
        account_id=_current_account_id(d.get("account_id") or None),
    )
def _purchase_to_dict(p: Purchase) -> dict:
    d = {
        "_db_id": p.id,  
        "name": p.name,
        "goods_id": p.goods_id,
        "price": p.price,
        "at": p.at,
        "account_id": p.account_id,
    }
    if p.market_price is not None:
        d["market_price"] = p.market_price
    if p.sale_price is not None:
        d["sale_price"] = p.sale_price
    if p.sold_at is not None:
        d["sold_at"] = p.sold_at
    if p.pending_receipt is not None:
        d["pending_receipt"] = p.pending_receipt
    if p.assetid is not None:
        d["assetid"] = p.assetid
    if p.listing is not None:
        d["listing"] = p.listing
    if p.listing_status is not None:
        d["listing_status"] = p.listing_status
    if p.external_order_id is not None:
        d["external_order_id"] = p.external_order_id
    d["source"] = p.source or "legacy"
    if p.order_status is not None:
        d["order_status"] = p.order_status
    if p.current_market_price is not None:
        d["current_market_price"] = p.current_market_price
    if p.current_price_updated_at is not None:
        d["current_price_updated_at"] = p.current_price_updated_at
    if p.received_at is not None:
        d["received_at"] = p.received_at
    if p.tradable_at is not None:
        d["tradable_at"] = p.tradable_at
    return d
def _sale_to_dict(s: Sale) -> dict:
    d = {
        "name": s.name,
        "goods_id": s.goods_id,
        "price": s.price,
        "at": s.at,
        "account_id": s.account_id,
    }
    if s.assetid is not None:
        d["assetid"] = s.assetid
    return d
def migrate_from_json() -> bool:
    """
    One-time migration: read transactions.json → insert into SQLite →
    rename JSON to .bak.  Returns True if migration happened.
    """
    if not _TRANSACTIONS_JSON.exists():
        return False
    with get_session() as session:
        existing = session.exec(select(Purchase).limit(1)).first()
        if existing is not None:
            return False
    try:
        with open(_TRANSACTIONS_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return False
    purchases = data.get("purchases", [])
    sales = data.get("sales", [])
    with get_session() as session:
        for p in purchases:
            session.add(_purchase_from_dict(p))
        for s in sales:
            session.add(_sale_from_dict(s))
        session.commit()
    try:
        if _TRANSACTIONS_BAK.exists():
            os.remove(str(_TRANSACTIONS_BAK))
        os.rename(str(_TRANSACTIONS_JSON), str(_TRANSACTIONS_BAK))
    except OSError:
        pass
    return True
_PURCHASE_UPDATABLE = frozenset({
    "name", "price", "goods_id", "market_price", "sale_price",
    "sold_at", "pending_receipt", "assetid", "listing", "listing_status",
    "external_order_id", "source", "order_status", "current_market_price",
    "current_price_updated_at", "received_at", "tradable_at",
})
_SALE_UPDATABLE = frozenset({"name", "price", "goods_id", "assetid", "at"})

def db_get_account_runtime(account_id: str) -> Optional[dict]:
    aid = _current_account_id(account_id)
    if not aid:
        return None
    with get_session() as session:
        row = session.get(SteamAccountRuntime, aid)
        if row is None:
            return None
        return {
            "account_id": row.account_id,
            "cookies": row.cookies or "",
            "session_id": row.session_id or "",
            "steam_id": row.steam_id or "",
            "shared_secret": row.shared_secret or "",
            "identity_secret": row.identity_secret or "",
            "device_id": row.device_id or "",
            "auto_confirm_enabled": bool(row.auto_confirm_enabled),
            "auto_sell_enabled": bool(row.auto_sell_enabled),
            "updated_at": float(row.updated_at or 0),
        }

def db_has_any_account_runtime() -> bool:
    with get_session() as session:
        return session.exec(select(SteamAccountRuntime.account_id).limit(1)).first() is not None

def db_upsert_account_runtime(account_id: str, data: dict) -> dict:
    import time
    aid = _current_account_id(account_id)
    if not aid:
        raise ValueError("account_id is required")
    allowed = {
        "cookies", "session_id", "steam_id", "shared_secret",
        "identity_secret", "device_id", "auto_confirm_enabled", "auto_sell_enabled",
    }
    with get_session() as session:
        row = session.get(SteamAccountRuntime, aid) or SteamAccountRuntime(account_id=aid)
        for key in allowed:
            if key in data:
                value = data[key]
                if key in {"auto_confirm_enabled", "auto_sell_enabled"}:
                    value = bool(value)
                else:
                    value = str(value or "").strip()
                setattr(row, key, value)
        row.updated_at = time.time()
        session.add(row)
        session.commit()
        session.refresh(row)
    return db_get_account_runtime(aid) or {"account_id": aid}

def db_account_record_counts(account_id: str) -> dict:
    from sqlalchemy import func
    aid = _current_account_id(account_id)
    with get_session() as session:
        return {
            "purchases": int(session.exec(select(func.count()).select_from(Purchase).where(Purchase.account_id == aid)).one()),
            "orders": int(session.exec(select(func.count()).select_from(PurchaseOrder).where(PurchaseOrder.account_id == aid)).one()),
            "sales": int(session.exec(select(func.count()).select_from(Sale).where(Sale.account_id == aid)).one()),
        }

def db_get_inventory_ownership_overrides(account_id: Optional[str] = None) -> dict[str, str]:
    aid = _current_account_id(account_id)
    if not aid:
        return {}
    with get_session() as session:
        rows = session.exec(
            select(InventoryOwnershipOverride).where(
                InventoryOwnershipOverride.account_id == aid
            )
        ).all()
        return {str(row.assetid): str(row.mode) for row in rows}

def db_set_inventory_ownership_override(
    assetid: str,
    mode: str,
    market_hash_name: str = "",
    account_id: Optional[str] = None,
) -> Optional[dict]:
    import time

    aid = _current_account_id(account_id)
    asset = str(assetid or "").strip()
    normalized = str(mode or "").strip().lower()
    if not aid:
        raise ValueError("未选择当前账号")
    if not asset:
        raise ValueError("assetid 不能为空")
    if normalized not in {"auto", "personal", "managed"}:
        raise ValueError("库存归属须为 auto、personal 或 managed")
    with get_session() as session:
        row = session.get(InventoryOwnershipOverride, (aid, asset))
        if normalized == "auto":
            if row is not None:
                session.delete(row)
                session.commit()
            return None
        if row is None:
            row = InventoryOwnershipOverride(account_id=aid, assetid=asset)
        row.mode = normalized
        row.market_hash_name = str(market_hash_name or "").strip()
        row.updated_at = time.time()
        session.add(row)
        session.commit()
        session.refresh(row)
        return {
            "account_id": row.account_id,
            "assetid": row.assetid,
            "mode": row.mode,
            "market_hash_name": row.market_hash_name,
            "updated_at": row.updated_at,
        }

def db_append_purchase(p: dict) -> None:
    if not _current_account_id(p.get("account_id") or None):
        raise ValueError("未选择当前账号")
    with get_session() as session:
        session.add(_purchase_from_dict(p))
        session.commit()

def db_upsert_purchase_order(order: dict) -> dict:
    import time
    external_order_id = str(order.get("external_order_id") or "").strip()
    if not external_order_id:
        raise ValueError("external_order_id is required")
    now = time.time()
    account_id = _current_account_id(order.get("account_id"))
    if not account_id:
        raise ValueError("current account is required")
    with get_session() as session:
        row = session.exec(
            select(PurchaseOrder).where(
                PurchaseOrder.external_order_id == external_order_id,
                PurchaseOrder.account_id == account_id,
            )
        ).first()
        if row is None:
            row = PurchaseOrder(
                external_order_id=external_order_id,
                created_at=float(order.get("created_at") or now),
                account_id=account_id,
            )
        for key in (
            "name", "goods_id", "quantity", "unit_price", "total_price",
            "status", "source", "user_confirmed_at", "paid_at", "received_at", "error",
        ):
            if key in order:
                setattr(row, key, order[key])
        row.updated_at = now
        session.add(row)
        session.commit()
        session.refresh(row)
        return {
            "id": row.id,
            "external_order_id": row.external_order_id,
            "status": row.status,
            "total_price": row.total_price,
            "account_id": row.account_id,
        }

def db_update_purchase_order(
    external_order_id: str,
    data: dict,
    expected_statuses: Optional[set[str]] = None,
) -> bool:
    import time
    account_id = _current_account_id()
    if not account_id:
        return False
    with get_session() as session:
        values = {
            key: data[key]
            for key in ("status", "user_confirmed_at", "paid_at", "received_at", "error")
            if key in data
        }
        values["updated_at"] = time.time()
        statement = sql_update(PurchaseOrder).where(
            PurchaseOrder.external_order_id == str(external_order_id),
            PurchaseOrder.account_id == account_id,
        )
        if expected_statuses is not None:
            normalized = {str(status) for status in expected_statuses}
            if not normalized:
                return False
            statement = statement.where(PurchaseOrder.status.in_(normalized))
        result = session.exec(statement.values(**values))
        session.commit()
        return bool(result.rowcount)

def db_replace_cancelled_order_with_paid_purchase(
    old_external_order_id: str,
    new_external_order_id: str,
    unit_price: float,
    market_price: Optional[float] = None,
) -> dict:
    """Atomically cancel an unresolved order and record its manually paid replacement."""
    import time

    old_order_id = str(old_external_order_id or "").strip()
    new_order_id = str(new_external_order_id or "").strip()
    if not old_order_id or not new_order_id:
        raise ValueError("旧订单号和新订单号均不能为空")
    if old_order_id == new_order_id:
        raise ValueError("新订单号不能与已取消的旧订单号相同")
    price = round(float(unit_price or 0), 2)
    if price <= 0:
        raise ValueError("实际支付单价须大于 0")
    purchase_market_price = None
    if market_price is not None:
        purchase_market_price = round(float(market_price or 0), 2)
        if purchase_market_price <= 0:
            raise ValueError("购入市场价须大于 0")

    now = time.time()
    account_id = _current_account_id()
    if not account_id:
        raise ValueError("未选择当前账号")
    with get_session() as session:
        old_order = session.exec(
            select(PurchaseOrder).where(
                PurchaseOrder.external_order_id == old_order_id,
                PurchaseOrder.account_id == account_id,
            )
        ).first()
        if old_order is None:
            old_order = session.exec(
                select(PurchaseOrder).where(
                    PurchaseOrder.external_order_id == old_order_id,
                    (PurchaseOrder.account_id == "") | PurchaseOrder.account_id.is_(None),
                )
            ).first()
            if old_order is not None:
                old_order.account_id = account_id
        if old_order is None:
            raise ValueError("旧订单不存在")
        if old_order.status not in {
            "awaiting_payment", "user_confirmed", "payment_unconfirmed", "needs_review",
        }:
            raise ValueError("旧订单当前不是待支付或待核对状态")
        existing_order = session.exec(
            select(PurchaseOrder).where(PurchaseOrder.external_order_id == new_order_id)
        ).first()
        existing_purchase = session.exec(
            select(Purchase).where(Purchase.external_order_id == new_order_id)
        ).first()
        if existing_order is not None or existing_purchase is not None:
            raise ValueError("新订单号已存在，未执行重复登记")

        quantity = max(1, int(old_order.quantity or 1))
        total_price = round(price * quantity, 2)
        old_order.status = "cancelled"
        old_order.updated_at = now
        old_order.error = f"BUFF 旧订单已取消；已登记手工换单 {new_order_id}"
        session.add(old_order)

        replacement = PurchaseOrder(
            external_order_id=new_order_id,
            name=old_order.name,
            goods_id=int(old_order.goods_id or 0),
            quantity=quantity,
            unit_price=price,
            total_price=total_price,
            status="awaiting_ship",
            source="buff_manual_replacement",
            created_at=now,
            updated_at=now,
            user_confirmed_at=now,
            paid_at=now,
            error=f"手工重新下单并付款，替代已取消订单 {old_order_id}",
            account_id=account_id,
        )
        session.add(replacement)
        for _ in range(quantity):
            session.add(Purchase(
                name=old_order.name,
                goods_id=int(old_order.goods_id or 0),
                price=price,
                at=now,
                market_price=purchase_market_price,
                pending_receipt=True,
                external_order_id=new_order_id,
                source="manual_replacement",
                order_status="awaiting_ship",
                account_id=account_id,
            ))
        session.commit()
        return {
            "old_external_order_id": old_order_id,
            "new_external_order_id": new_order_id,
            "quantity": quantity,
            "unit_price": price,
            "total_price": total_price,
            "market_price": purchase_market_price,
            "status": "awaiting_ship",
        }

def db_get_purchase_orders(account_id: Optional[str] = None) -> list:
    aid = _current_account_id(account_id)
    if not aid:
        return []
    with get_session() as session:
        rows = session.exec(
            select(PurchaseOrder).where(PurchaseOrder.account_id == aid).order_by(PurchaseOrder.id)
        ).all()
        return [
            {
                "id": row.id,
                "external_order_id": row.external_order_id,
                "name": row.name,
                "goods_id": row.goods_id,
                "quantity": row.quantity,
                "unit_price": row.unit_price,
                "total_price": row.total_price,
                "status": row.status,
                "source": row.source,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
                "user_confirmed_at": row.user_confirmed_at,
                "paid_at": row.paid_at,
                "received_at": row.received_at,
                "error": row.error,
                "account_id": row.account_id,
            }
            for row in rows
        ]

def db_update_current_prices(prices: dict, updated_at: float) -> int:
    changed = 0
    account_id = _current_account_id()
    if not account_id:
        return 0
    with get_session() as session:
        rows = session.exec(select(Purchase).where(Purchase.account_id == account_id)).all()
        for row in rows:
            if row.sale_price is not None and float(row.sale_price or 0) > 0:
                continue
            price = prices.get((row.name or "").strip())
            if price is None:
                continue
            row.current_market_price = round(float(price), 2)
            row.current_price_updated_at = float(updated_at)
            session.add(row)
            changed += 1
        if changed:
            session.commit()
    return changed
def db_get_purchases(account_id: Optional[str] = None) -> list:
    aid = _current_account_id(account_id)
    if not aid:
        return []
    with get_session() as session:
        rows = session.exec(select(Purchase).where(Purchase.account_id == aid).order_by(Purchase.id)).all()
        return [_purchase_to_dict(r) for r in rows]
def db_append_sale(s: dict) -> None:
    if not _current_account_id(s.get("account_id") or None):
        raise ValueError("未选择当前账号")
    with get_session() as session:
        session.add(_sale_from_dict(s))
        session.commit()
def db_get_sales(account_id: Optional[str] = None) -> list:
    aid = _current_account_id(account_id)
    if not aid:
        return []
    with get_session() as session:
        rows = session.exec(select(Sale).where(Sale.account_id == aid).order_by(Sale.id)).all()
        return [_sale_to_dict(r) for r in rows]
def db_clear_transactions() -> None:
    from sqlmodel import delete as sql_delete
    account_id = _current_account_id()
    if not account_id:
        return
    with get_session() as session:
        session.exec(sql_delete(Purchase).where(Purchase.account_id == account_id))
        session.exec(sql_delete(Sale).where(Sale.account_id == account_id))
        session.commit()
def db_replace_transactions(purchases: list, sales: list) -> None:
    from sqlmodel import delete as sql_delete
    account_id = _current_account_id()
    if not account_id:
        raise ValueError("未选择当前账号")
    with get_session() as session:
        session.exec(sql_delete(Purchase).where(Purchase.account_id == account_id))
        session.exec(sql_delete(Sale).where(Sale.account_id == account_id))
        for p in purchases:
            session.add(_purchase_from_dict(p))
        for s in sales:
            session.add(_sale_from_dict(s))
        session.commit()
def db_delete_purchase(idx: int) -> bool:
    """Delete purchase by positional index (0-based, ordered by id)."""
    account_id = _current_account_id()
    with get_session() as session:
        rows = session.exec(select(Purchase).where(Purchase.account_id == account_id).order_by(Purchase.id)).all()
        if 0 <= idx < len(rows):
            session.delete(rows[idx])
            session.commit()
            return True
    return False
def db_delete_sale(idx: int) -> bool:
    account_id = _current_account_id()
    with get_session() as session:
        rows = session.exec(select(Sale).where(Sale.account_id == account_id).order_by(Sale.id)).all()
        if 0 <= idx < len(rows):
            session.delete(rows[idx])
            session.commit()
            return True
    return False
def db_update_purchase(idx: int, data: dict) -> bool:
    """按位置索引更新（兼容旧接口，UI 路由使用）。"""
    account_id = _current_account_id()
    with get_session() as session:
        rows = session.exec(select(Purchase).where(Purchase.account_id == account_id).order_by(Purchase.id)).all()
        if 0 <= idx < len(rows):
            row = rows[idx]
            for k, v in data.items():
                if k in _PURCHASE_UPDATABLE:
                    setattr(row, k, v)
            session.add(row)
            session.commit()
            return True
    return False
def db_update_purchase_by_id(db_id: int, data: dict) -> bool:
    """按主键 ID 更新，O(1) 操作，推荐内部 worker 使用。"""
    if not db_id:
        return False
    with get_session() as session:
        row = session.get(Purchase, db_id)
        if row is None or row.account_id != _current_account_id():
            return False
        for k, v in data.items():
            if k in _PURCHASE_UPDATABLE:
                setattr(row, k, v)
        session.add(row)
        session.commit()
        return True
def db_delete_purchase_by_id(db_id: int) -> bool:
    """按主键 ID 删除，O(1) 操作。"""
    if not db_id:
        return False
    with get_session() as session:
        row = session.get(Purchase, db_id)
        if row is None or row.account_id != _current_account_id():
            return False
        session.delete(row)
        session.commit()
        return True
def db_update_sale(idx: int, data: dict) -> bool:
    account_id = _current_account_id()
    with get_session() as session:
        rows = session.exec(select(Sale).where(Sale.account_id == account_id).order_by(Sale.id)).all()
        if 0 <= idx < len(rows):
            row = rows[idx]
            for k, v in data.items():
                if k in _SALE_UPDATABLE:
                    setattr(row, k, v)
            session.add(row)
            session.commit()
            return True
    return False
def db_delete_sale_by_id(db_id: int) -> bool:
    """Delete by primary ID, O(1) operation."""
    if not db_id:
        return False
    with get_session() as session:
        row = session.get(Sale, db_id)
        if row is None or row.account_id != _current_account_id():
            return False
        session.delete(row)
        session.commit()
        return True
def db_get_item_nameid(market_hash_name: str) -> Optional[str]:
    with get_session() as session:
        item = session.exec(
            select(ItemNameId).where(ItemNameId.market_hash_name == market_hash_name)
        ).first()
        return item.item_nameid if item else None
def db_set_item_nameid(market_hash_name: str, item_nameid: str) -> None:
    with get_session() as session:
        item = session.exec(
            select(ItemNameId).where(ItemNameId.market_hash_name == market_hash_name)
        ).first()
        if item:
            item.item_nameid = item_nameid
        else:
            item = ItemNameId(market_hash_name=market_hash_name, item_nameid=item_nameid)
        session.add(item)
        session.commit()
_REGION_CODES = [
    "cn", "ru", "kz", "ua", "pk", "tr", "ar", "az",
    "vn", "id", "in", "br", "cl", "jp", "hk", "ph",
]
def _game_row_to_dict(r: SteamDealGame) -> dict:  # Refactored: was copy-pasted verbatim into both db_get_steam_deals and db_get_steam_deals_by_app_ids
    d = {
        "app_id": r.app_id,
        "name": r.name,
        "name_en": r.name_en,
        "banner_url": r.banner_url,
        "positive_rate": r.positive_rate,
        "total_reviews": r.total_reviews,
        "discount_percent": r.discount_percent,
        "deal_status": r.deal_status,
        "fetched_at": r.fetched_at,
        "prices": {},
        "discounts": {},
        "original_cn": r.original_cn,
    }
    for rc in _REGION_CODES:
        d["prices"][rc] = getattr(r, f"price_{rc}", None)
        d["discounts"][rc] = getattr(r, f"discount_{rc}", None)
    return d
def db_upsert_steam_deal(data: dict) -> None:
    """Insert or update a SteamDealGame by app_id."""
    data = dict(data)  
    data["wilson_score"] = _compute_wilson_score(
        data.get("positive_rate"), data.get("total_reviews")
    )
    with get_session() as session:
        existing = session.exec(
            select(SteamDealGame).where(SteamDealGame.app_id == str(data["app_id"]))
        ).first()
        if existing:
            for k, v in data.items():
                if k != "id" and hasattr(existing, k):
                    setattr(existing, k, v)
            session.add(existing)
        else:
            game = SteamDealGame(**{k: v for k, v in data.items() if hasattr(SteamDealGame, k)})
            session.add(game)
        session.commit()
def db_get_steam_deals(
    offset: int = 0,
    limit: int = 30,
    search: str = "",
    sort_by: str = "discount_percent",
    sort_dir: str = "asc",
    compare_region: str = "",
    deal_status_filter: str = "",
) -> list:
    """Paginated query with optional search and sorting."""
    from sqlmodel import col, text as sql_text, or_
    with get_session() as session:
        stmt = select(SteamDealGame)
        if search:
            stmt = stmt.where(
                or_(
                    col(SteamDealGame.name).contains(search),
                    col(SteamDealGame.name_en).contains(search)
                )
            )
        if deal_status_filter and deal_status_filter != "全部状态":
            stmt = stmt.where(SteamDealGame.deal_status == deal_status_filter)
        order_col = None
        if sort_by == "positive_rate":
            order_col = SteamDealGame.positive_rate
        elif sort_by == "total_reviews":
            order_col = SteamDealGame.total_reviews
        elif sort_by == "discount_percent":
            order_col = SteamDealGame.discount_percent
        elif sort_by == "name":
            order_col = SteamDealGame.name
        elif sort_by in ("default_recommend", "price_diff", "discount_abs", "region_value"):
            # price_diff/discount_abs 这俩路由层已经走内存排序了
            # 这里是 search+filter 组合时的回退，必须加分页防止全表返回
            stmt = stmt.order_by(col(SteamDealGame.wilson_score).desc())
            stmt = stmt.offset(offset).limit(limit)
        else:
            stmt = stmt.order_by(col(SteamDealGame.wilson_score).desc())
            stmt = stmt.offset(offset).limit(limit)
        if order_col is not None:
            if sort_dir == "desc":
                stmt = stmt.order_by(col(order_col).desc())
            else:
                stmt = stmt.order_by(col(order_col).asc())
            stmt = stmt.offset(offset).limit(limit)
        rows = session.exec(stmt).all()
        return [_game_row_to_dict(r) for r in rows]
def db_get_steam_deals_count(search: str = "") -> int:
    from sqlmodel import col, func, or_
    with get_session() as session:
        stmt = select(func.count()).select_from(SteamDealGame)
        if search:
            stmt = stmt.where(
                or_(
                    col(SteamDealGame.name).contains(search),
                    col(SteamDealGame.name_en).contains(search)
                )
            )
        return session.exec(stmt).one()
def db_get_steam_deals_last_update() -> Optional[float]:
    from sqlmodel import func
    with get_session() as session:
        result = session.exec(
            select(func.max(SteamDealGame.fetched_at))
        ).first()
        return result if result else None
def db_clear_steam_deals() -> None:
    from sqlmodel import delete as sql_delete
    with get_session() as session:
        session.exec(sql_delete(SteamDealGame))
        session.commit()
def db_get_steam_deals_price_snapshot() -> list:
    """Lightweight fetch: only price-related columns for ALL games.
    Used to build an in-memory sort index (price_diff / discount_abs) without
    the cost of fetching every column for 20 000+ rows. Returns a list of
    plain dicts with keys: app_id, original_cn, price_<cc> for each region.
    """
    from sqlalchemy import text as sa_text
    price_cols = ", ".join(["app_id", "original_cn"] + [f"price_{rc}" for rc in _REGION_CODES])
    with get_engine().connect() as conn:
        rows = conn.execute(sa_text(f"SELECT {price_cols} FROM steamdealgame")).fetchall()
    result = []
    for row in rows:
        d = {"app_id": row[0], "original_cn": row[1]}
        for i, rc in enumerate(_REGION_CODES):
            d[f"price_{rc}"] = row[2 + i]
        result.append(d)
    return result
def db_get_steam_deals_review_snapshot() -> list:
    """Lightweight fetch: only app_id and total_reviews for ALL games.
    Used to filter games with >= 2000 reviews for region_value sort mode.
    """
    from sqlalchemy import text as sa_text
    with get_engine().connect() as conn:
        rows = conn.execute(sa_text("SELECT app_id, total_reviews FROM steamdealgame")).fetchall()
    return [{"app_id": row[0], "total_reviews": row[1]} for row in rows]
def db_get_steam_deals_by_app_ids(app_ids: List[str]) -> list:
    """Fetch full game data for a specific ordered list of app_ids.
    Only fetches the rows listed in app_ids and preserves the given order.
    Used after the sort index resolves which 30 games to show on this page.
    """
    if not app_ids:
        return []
    from sqlmodel import col
    with get_session() as session:
        rows = session.exec(
            select(SteamDealGame).where(col(SteamDealGame.app_id).in_(app_ids))
        ).all()
        id_to_row = {r.app_id: _game_row_to_dict(r) for r in rows}
        return [id_to_row[aid] for aid in app_ids if aid in id_to_row]
