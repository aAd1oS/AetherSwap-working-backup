import time
from typing import Any, Callable, Dict, List, Optional, Tuple
import requests
from utils.delay import jittered_sleep
from utils.buff_protection import BUFF_MIN_INTERVAL_SECONDS, BuffProtectionError, get_buff_request_protection
from utils.throttle import get_throttle
from utils.direct_http import direct_get
def steam_request(retries: int, fn: Callable[[], requests.Response]) -> requests.Response:
    last_exc: Optional[Exception] = None
    attempts = max(1, int(retries) or 1)
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as e:
            last_exc = e
            if attempt + 1 < attempts:
                jittered_sleep(1)
    raise last_exc
try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass
BUFF_STEAM_TRADE_URL = "https://buff.163.com/api/market/steam_trade"
STEAM_ACCEPT_REFERER = "https://steamcommunity.com/tradeoffer/{trade_offer_id}/"
STEAM_ACCEPT_URL = "https://steamcommunity.com/tradeoffer/{trade_offer_id}/accept"
def _cookies_str_to_dict(cookie_str: str) -> Dict[str, str]:
    out = {}
    for part in (cookie_str or "").split(";"):
        s = part.strip()
        if "=" in s:
            k, _, v = s.partition("=")
            out[k.strip()] = v.strip()
    return out
def fetch_buff_steam_trade(buff_cookies: str) -> Tuple[bool, List[Dict[str, Any]], str]:
    try:
        cookies = _cookies_str_to_dict(buff_cookies)
        if not cookies:
            return False, [], "未配置 Buff cookies"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36",
            "Referer": "https://buff.163.com/market/buy_order/to_receive?game=csgo",
            "X-Requested-With": "XMLHttpRequest",
        }
        protection = get_buff_request_protection()
        protection.before_request()
        get_throttle().wait("buff.163.com", BUFF_MIN_INTERVAL_SECONDS)
        try:
            r = direct_get(BUFF_STEAM_TRADE_URL, headers=headers, cookies=cookies, verify=False, timeout=10)
        except requests.RequestException as exc:
            protection.record_network_failure(exc)
            raise
        if r.status_code == 429:
            protection.record_response(r.status_code, {})
        try:
            data = r.json() if r.text else {}
        except (ValueError, TypeError):
            return False, [], f"JSON 解析失败, status={r.status_code}"
        protection.record_response(r.status_code, data)
        if data.get("code") != "OK":
            return False, [], data.get("msg", "请求失败")
        raw = data.get("data") or []
        if not isinstance(raw, list):
            return False, [], "数据格式异常"
        pending = []
        for x in raw:
            if x.get("state") != 1 or not x.get("tradeofferid"):
                continue
            created_at = int(x.get("created_at", 0)) if x.get("created_at") is not None else 0
            goods_list = x.get("items_to_trade") or []
            if not goods_list:
                continue
            items_in_trade = []
            for g in goods_list:
                asset_id = str(g.get("assetid", ""))
                gid = str(g.get("goods_id", ""))
                goods_id_buff = None
                if gid and gid != "0":
                    try:
                        goods_id_buff = int(gid)
                    except (ValueError, TypeError):
                        pass
                info = (x.get("goods_infos") or {}).get(gid) or {}
                if isinstance(info, dict):
                    item_name = info.get("name", "未知物品") or "未知物品"
                    market_hash_name = (info.get("market_hash_name") or "").strip()
                else:
                    item_name = "未知物品"
                    market_hash_name = ""
                items_in_trade.append({
                    "assetid": asset_id,
                    "name": item_name,
                    "market_hash_name": market_hash_name,
                    "goods_id": goods_id_buff,
                })
            pending.append({
                "tradeofferid": x.get("tradeofferid"),
                "created_at": created_at,
                "items": items_in_trade,
            })
        return True, pending, ""
    except BuffProtectionError:
        raise
    except Exception as e:
        return False, [], str(e)[:120]
def accept_steam_trade_offer(trade_offer_id: str, steam_cookies: Dict[str, str]) -> bool:
    from utils.proxy_manager import get_proxy_manager
    pm = get_proxy_manager()
    try:
        url = STEAM_ACCEPT_URL.format(trade_offer_id=trade_offer_id)
        referer = STEAM_ACCEPT_REFERER.format(trade_offer_id=trade_offer_id)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36",
            "Origin": "https://steamcommunity.com",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Referer": referer,
        }
        session_id = steam_cookies.get("sessionid", "").strip()
        data = {
            "sessionid": session_id,
            "serverid": "1",
            "tradeofferid": str(trade_offer_id),
            "partner": "",
            "captcha": "",
        }
        context = {"attempt": 0}
        def _call():
            context["attempt"] += 1
            proxies = pm.get_steam_proxies()
            return requests.post(url, headers=headers, cookies=steam_cookies, proxies=proxies, data=data, verify=False, timeout=15)
        r = steam_request(3, _call)
        if r.status_code != 200:
            return False
        raw_text = (r.text or "").strip()
        if not raw_text:
            return False
        try:
            body = r.json()
        except Exception:
            return False
        if not isinstance(body, dict):
            return False
        if body.get("tradeid"):
            return True
        if body.get("strError"):
            return False
        return "tradeid" in body or body.get("success") == 1
    except Exception:
        return False
def _match_purchase_for_item(
    item: dict,
    pending_purchases: List[dict],
    assigned_db_ids: set,
) -> Optional[dict]:
    """Return a purchase only when this item has exactly one eligible match."""
    goods_id = item.get("goods_id")
    item_name = (item.get("market_hash_name") or item.get("name") or "").strip()
    candidates: List[dict] = []
    for purchase in pending_purchases:
        db_id = purchase.get("_db_id")
        if not db_id or db_id in assigned_db_ids or purchase.get("assetid"):
            continue
        if goods_id is not None:
            try:
                if int(goods_id) == int(purchase.get("goods_id")):
                    candidates.append(purchase)
            except (ValueError, TypeError):
                pass
            continue
        if item_name and (purchase.get("name") or "").strip() == item_name:
            candidates.append(purchase)
    return candidates[0] if len(candidates) == 1 else None


_ALLOWED_RECEIPT_STATES = frozenset({"awaiting_ship", "awaiting_trade"})


def _plan_trade_offer_matches(
    items: List[dict],
    pending_purchases: List[dict],
    expected_account_id: str = "",
) -> Optional[List[Tuple[dict, dict]]]:
    """Build a complete one-to-one match before any state or network action."""
    if not isinstance(items, list) or not items:
        return None
    assigned_db_ids: set = set()
    pairs: List[Tuple[dict, dict]] = []
    for item in items:
        purchase = _match_purchase_for_item(item, pending_purchases, assigned_db_ids)
        if purchase is None:
            return None
        account_id = str(purchase.get("account_id") or "")
        if expected_account_id and account_id and account_id != expected_account_id:
            return None
        if str(purchase.get("order_status") or "") not in _ALLOWED_RECEIPT_STATES:
            return None
        assigned_db_ids.add(purchase["_db_id"])
        pairs.append((purchase, item))
    return pairs



def reconcile_pending_purchases_from_inventory(
    get_purchases: Callable[[], List[dict]],
    inventory_items: List[dict],
    update_purchase: Callable[[int, dict], bool],
    update_purchase_by_id: Optional[Callable[[int, dict], bool]] = None,
) -> dict:
    """Match manually accepted trades using an inventory snapshot already fetched by the caller."""
    purchases = list(get_purchases() or [])
    inventory_by_assetid = {
        str(item.get("assetid") or "").strip(): item
        for item in (inventory_items or [])
        if str(item.get("assetid") or "").strip()
    }
    matched = 0
    for positional_idx, purchase in enumerate(purchases):
        assetid = str(purchase.get("assetid") or "").strip()
        if not purchase.get("pending_receipt") or not assetid:
            continue
        item = inventory_by_assetid.get(assetid)
        purchase_name = (purchase.get("name") or "").strip().casefold()
        inventory_name = (
            (item or {}).get("market_hash_name") or (item or {}).get("name") or ""
        ).strip().casefold()
        if not item or not purchase_name or purchase_name != inventory_name:
            continue
        tradable_at = item.get("cooldown_at")
        now_ts = time.time()
        data = {
            "pending_receipt": False,
            "received_at": float(purchase.get("received_at") or now_ts),
            "tradable_at": tradable_at,
            "order_status": (
                "trade_locked"
                if tradable_at and float(tradable_at) > now_ts
                else "received"
            ),
        }
        db_id = int(purchase.get("_db_id") or 0)
        if update_purchase_by_id and db_id:
            updated = update_purchase_by_id(db_id, data)
        else:
            updated = update_purchase(positional_idx, data)
        if updated:
            purchase.update(data)
            matched += 1

    pending_by_name: Dict[str, List[Tuple[int, dict]]] = {}
    used_assetids = {
        str(p.get("assetid") or "").strip()
        for p in purchases if str(p.get("assetid") or "").strip()
    }
    for idx, purchase in enumerate(purchases):
        if not purchase.get("pending_receipt") or purchase.get("assetid"):
            continue
        name = (purchase.get("name") or "").strip().casefold()
        if not name:
            continue
        pending_by_name.setdefault(name, []).append((idx, purchase))

    inventory_by_name: Dict[str, List[dict]] = {}
    for item in inventory_items or []:
        assetid = str(item.get("assetid") or "").strip()
        name = (item.get("market_hash_name") or item.get("name") or "").strip().casefold()
        if not assetid or assetid in used_assetids or not name:
            continue
        inventory_by_name.setdefault(name, []).append(item)

    ambiguous = 0
    for name, pending_rows in pending_by_name.items():
        candidates = inventory_by_name.get(name) or []
        if not candidates:
            continue
        if len(candidates) != len(pending_rows):
            ambiguous += len(pending_rows)
            continue
        pending_rows.sort(key=lambda pair: (
            float(pair[1].get("at") or 0),
            int(pair[1].get("_db_id") or 0),
        ))
        candidates.sort(key=lambda item: str(item.get("assetid") or ""))
        for (positional_idx, purchase), item in zip(pending_rows, candidates):
            assetid = str(item.get("assetid") or "").strip()
            tradable_at = item.get("cooldown_at")
            now_ts = time.time()
            order_status = (
                "trade_locked"
                if tradable_at and float(tradable_at) > now_ts
                else "received"
            )
            data = {
                "assetid": assetid,
                "pending_receipt": False,
                "received_at": now_ts,
                "tradable_at": tradable_at,
                "order_status": order_status,
            }
            db_id = int(purchase.get("_db_id") or 0)
            if update_purchase_by_id and db_id:
                updated = update_purchase_by_id(db_id, data)
            else:
                updated = update_purchase(positional_idx, data)
            if updated:
                used_assetids.add(assetid)
                matched += 1
    return {"matched": matched, "ambiguous": ambiguous}

def try_receive_once(
    get_purchases: Callable[[], List[dict]],
    update_purchase: Callable[[int, dict], bool],
    get_buff_cookies: Callable[[], str],
    get_steam_credentials: Callable[[], dict],
    scan_inventory: Optional[Callable[[], Tuple[bool, List[dict], str]]] = None,
    update_purchase_by_id: Optional[Callable[[int, dict], bool]] = None,
) -> int:
    """Accept pending Buff→Steam trade offers and update purchase records.
    Uses ``update_purchase_by_id`` (O(1), keyed on SQLite primary key) when
    available to avoid the race condition where positional indices shift
    between the time they are read and when the update is applied.
    Falls back to positional ``update_purchase`` only if``update_purchase_by_id``
    is not supplied (backward-compatibility).
    """
    from app.accounts import get_current_id
    expected_account_id = str(get_current_id() or "")
    purchases = get_purchases()
    pending_records: List[dict] = [
        p for p in purchases
        if p.get("pending_receipt") and not p.get("assetid") and p.get("_db_id")
    ]
    if not pending_records:
        return 0
    buff_cookies = get_buff_cookies()
    steam_cred = get_steam_credentials()
    steam_cookies_str = steam_cred.get("cookies") or ""
    steam_cookies = _cookies_str_to_dict(steam_cookies_str)
    session_id = (steam_cred.get("session_id") or "").strip()
    if session_id:
        steam_cookies["sessionid"] = session_id
    if not steam_cookies.get("sessionid") or not steam_cookies.get("steamLoginSecure"):
        return 0
    ok, pending_tasks, err = fetch_buff_steam_trade(buff_cookies)
    if not ok or not pending_tasks:
        return 0
    pending_tasks = sorted(pending_tasks, key=lambda t: (t.get("created_at") or 0, t.get("tradeofferid") or ""))
    inventory_before_ids: Optional[set] = None
    if scan_inventory:
        ok_before, inventory_before, _ = scan_inventory()
        if not ok_before:
            return 0
        inventory_before_ids = {
            str(item.get("assetid") or "").strip()
            for item in (inventory_before or [])
            if str(item.get("assetid") or "").strip()
        }
    received = 0
    claimed_db_ids: set = set()
    def _do_update(db_id: int, positional_idx: int, data: dict) -> bool:
        """Update a purchase record, preferring _db_id-based O(1) update."""
        if update_purchase_by_id and db_id:
            return update_purchase_by_id(db_id, data)
        return update_purchase(positional_idx, data)
    for task in pending_tasks:
        offer_id = task.get("tradeofferid")
        if not offer_id:
            continue
        offer_items = task.get("items") or []
        eligible_records = [
            row for row in pending_records
            if row.get("_db_id") not in claimed_db_ids
        ]
        planned_pairs = _plan_trade_offer_matches(
            offer_items, eligible_records, expected_account_id
        )
        if planned_pairs is None:
            for row in eligible_records:
                candidate_match = False
                for item in offer_items:
                    if item.get("goods_id") is not None:
                        try:
                            candidate_match = int(row.get("goods_id")) == int(item.get("goods_id"))
                        except (TypeError, ValueError):
                            candidate_match = False
                    else:
                        item_name = (item.get("market_hash_name") or item.get("name") or "").strip()
                        candidate_match = bool(item_name and (row.get("name") or "").strip() == item_name)
                    if candidate_match:
                        break
                if not candidate_match:
                    continue
                db_id = int(row.get("_db_id") or 0)
                positional_idx = next(
                    (i for i, purchase in enumerate(purchases) if purchase.get("_db_id") == db_id),
                    -1,
                )
                if positional_idx >= 0:
                    _do_update(db_id, positional_idx, {"order_status": "needs_review"})
                claimed_db_ids.add(db_id)
            continue
        claimed_db_ids.update(pair[0]["_db_id"] for pair in planned_pairs)
        updates_ok = True
        for matched, _item in planned_pairs:
            db_id = matched.get("_db_id") or 0
            positional_idx = next(
                (i for i, p in enumerate(purchases) if p.get("_db_id") == db_id),
                -1,
            )
            if positional_idx < 0 or not _do_update(
                db_id,
                positional_idx,
                {"order_status": "awaiting_trade"},
            ):
                updates_ok = False
                break
            matched["order_status"] = "awaiting_trade"
        if not updates_ok:
            continue
        if expected_account_id:
            from app.account_operations import assert_current_account
            assert_current_account(expected_account_id)
        if not accept_steam_trade_offer(str(offer_id), steam_cookies):
            continue
        received += 1
        if scan_inventory:
            jittered_sleep(2)
        purchases = get_purchases()
        pending_records = [
            p for p in purchases
            if p.get("pending_receipt") and not p.get("assetid") and p.get("_db_id")
        ]
        pending_by_id = {p["_db_id"]: p for p in pending_records}
        pairs = [
            (pending_by_id[planned["_db_id"]], item)
            for planned, item in planned_pairs
            if planned.get("_db_id") in pending_by_id
        ]
        pairs.sort(key=lambda x: (x[0].get("at") or 0, x[0].get("_db_id") or 0))
        already_used = {str(p.get("assetid")) for p in get_purchases() if p.get("assetid")}
        inv_by_name: Dict[str, List[dict]] = {}
        inventory_diff_available = False
        if scan_inventory:
            ok_inv, inv_list, _ = scan_inventory()
            if not ok_inv:
                return received
            current_inventory_ids = {
                str(inv_item.get("assetid") or "").strip()
                for inv_item in (inv_list or [])
                if str(inv_item.get("assetid") or "").strip()
            }
            new_assetids = current_inventory_ids - (inventory_before_ids or set())
            inventory_before_ids = current_inventory_ids
            inventory_diff_available = bool(new_assetids)
            if new_assetids:
                for inv_item in inv_list:
                    aid = str(inv_item.get("assetid") or "")
                    if not aid or aid not in new_assetids or aid in already_used:
                        continue
                    mhn = (inv_item.get("market_hash_name") or "").strip()
                    if mhn:
                        inv_by_name.setdefault(mhn, []).append(inv_item)
                for mhn in inv_by_name:
                    inv_by_name[mhn].sort(key=lambda x: x.get("assetid") or "")
        for purchase_rec, it in pairs:
            mhn = (it.get("market_hash_name") or "").strip()
            our_assetid = None
            matched_inventory_item = None
            if mhn and inv_by_name.get(mhn):
                for inv_item in inv_by_name[mhn][:]:
                    aid = str(inv_item.get("assetid") or "")
                    if aid in already_used:
                        continue
                    our_assetid = aid
                    matched_inventory_item = inv_item
                    already_used.add(aid)
                    inv_by_name[mhn].remove(inv_item)
                    break
            if our_assetid:
                db_id = purchase_rec.get("_db_id") or 0
                pos_idx = next(
                    (i for i, p in enumerate(purchases) if p.get("_db_id") == db_id),
                    -1,
                )
                tradable_at = None
                if matched_inventory_item:
                    tradable_at = matched_inventory_item.get("cooldown_at")
                now_ts = time.time()
                order_status = "trade_locked" if tradable_at and float(tradable_at) > now_ts else "received"
                try:
                    _do_update(db_id, pos_idx, {
                        "assetid": our_assetid,
                        "pending_receipt": False,
                        "received_at": now_ts,
                        "tradable_at": tradable_at,
                        "order_status": order_status,
                    })
                    already_used.add(our_assetid)
                except Exception as exc:
                    from app.database import DuplicateAssetIdError
                    if not isinstance(exc, DuplicateAssetIdError):
                        raise
                    _do_update(db_id, pos_idx, {
                        "order_status": "needs_review",
                    })
        if scan_inventory and not inventory_diff_available:
            return received
        jittered_sleep(1)
    return received
