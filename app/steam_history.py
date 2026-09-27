"""Fail-closed Steam market history pagination and amount parsing."""
import json
import re
from pathlib import Path
from typing import Callable, Dict, Optional, Set, Tuple

from bs4 import BeautifulSoup


HISTORY_PAGE_SIZE = 100
HISTORY_MAX_PAGES = 40
STEAM_MARKET_GROSS_FACTOR = 1.15
_HOVER_PATTERN = re.compile(
    r"CreateItemHoverFromContainer\s*\(\s*g_rgAssets\s*,\s*'(history_row_\d+_\d+)_name'\s*,\s*(\d+)\s*,\s*'(\d+)'\s*,\s*'(\d+)'"
)


def _load_rate_map() -> Dict[str, float]:
    try:
        path = Path(__file__).resolve().parent.parent / "config" / "exchange_rate.json"
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        rates = data.get("rates") if isinstance(data, dict) else None
        if isinstance(rates, dict):
            return {
                str(code).upper(): float(rate)
                for code, rate in rates.items()
                if isinstance(rate, (int, float)) and float(rate) > 0
            }
    except Exception:
        pass
    return {}


def _currency_code(text: str, page_currency: Optional[str] = None) -> str:
    value = text or ""
    if page_currency:
        return page_currency
    if "HK" in value and "$" in value:
        return "HKD"
    markers = (
        (("CNY", "RMB"), "CNY"),
        (("₹", "INR"), "INR"),
        (("₽", "RUB"), "RUB"),
        (("€", "EUR"), "EUR"),
        (("£", "GBP"), "GBP"),
        (("₩", "KRW"), "KRW"),
        (("₺", "TRY"), "TRY"),
        (("₴", "UAH"), "UAH"),
        (("₫", "VND"), "VND"),
        (("₱", "PHP"), "PHP"),
        (("₸", "KZT"), "KZT"),
        (("R$", "BRL"), "BRL"),
        (("USD", "US$"), "USD"),
    )
    for aliases, code in markers:
        if any(alias in value for alias in aliases):
            return code
    return "UNKNOWN"


def _amount(text: str) -> Optional[float]:
    match = re.search(r"\d[\d\s.,]*", (text or "").replace("\xa0", " "))
    if not match:
        return None
    token = re.sub(r"\s+", "", match.group(0)).rstrip(".,")
    if "," in token and "." in token:
        decimal = "," if token.rfind(",") > token.rfind(".") else "."
        thousands = "." if decimal == "," else ","
        token = token.replace(thousands, "").replace(decimal, ".")
    elif "," in token:
        parts = token.split(",")
        token = "".join(parts) if len(parts) == 2 and len(parts[-1]) == 3 else ".".join(parts)
    elif token.count(".") > 1:
        parts = token.split(".")
        token = "".join(parts) if all(len(part) == 3 for part in parts[1:]) else "".join(parts[:-1]) + "." + parts[-1]
    try:
        return float(token)
    except (TypeError, ValueError):
        return None


def _total_count(data: dict) -> Optional[int]:
    for key in ("total_count", "totalCount", "total"):
        try:
            value = int(data.get(key))
            if value >= 0:
                return value
        except (TypeError, ValueError):
            pass
    return None


def _parse_page(data: dict, rate_map: Dict[str, float]) -> tuple[Dict[str, dict], Set[str], list[str]]:
    page_currency = None
    try:
        from steam.market_orders import _STEAM_CURRENCY_CODES
        page_currency = _STEAM_CURRENCY_CODES.get(int(data.get("wallet_currency") or data.get("eCurrency") or 0))
    except (TypeError, ValueError):
        pass
    row_to_assetid = {
        match.group(1): str(match.group(4))
        for match in _HOVER_PATTERN.finditer(data.get("hovers") or "")
    }
    sales: Dict[str, dict] = {}
    row_ids: Set[str] = set()
    errors: list[str] = []
    soup = BeautifulSoup(data.get("results_html") or "", "html.parser")
    for row in soup.find_all("div", class_="market_listing_row"):
        row_id = row.get("id") or ""
        if not row_id.startswith("history_row_"):
            continue
        row_ids.add(row_id)
        status = row.find("div", class_="market_listing_listed_date_combined")
        status_text = status.get_text(strip=True) if status else ""
        if not any(label in status_text for label in ("Sold", "已售出", "出售")):
            continue
        assetid = row_to_assetid.get(row_id)
        if not assetid:
            fallback = re.search(r"assetid[\"']?\s*[:=]\s*[\"']?(\d+)[\"']?", str(row), re.I)
            if fallback:
                assetid = fallback.group(1)
        price = row.find("span", class_="market_listing_price")
        raw_text = price.get_text() if price else ""
        raw_amount = _amount(raw_text)
        currency = _currency_code(raw_text, page_currency=page_currency)
        if not assetid or raw_amount is None or raw_amount <= 0:
            errors.append(f"{row_id or '未知行'} 缺少可用 assetid 或金额")
            continue
        if currency == "UNKNOWN":
            errors.append(f"{row_id} 无法识别币种: {raw_text.strip()}")
            continue
        rate = 1.0 if currency == "CNY" else rate_map.get(currency)
        if not rate:
            errors.append(f"{row_id} 缺少 {currency} 兑 CNY 汇率")
            continue
        seller_net_cny = round(raw_amount * float(rate), 2)
        sales[str(assetid)] = {
            "assetid": str(assetid),
            "display_amount": round(raw_amount, 2),
            "display_currency": currency,
            "seller_net_cny": seller_net_cny,
            "gross_sale_price_cny": round(seller_net_cny * STEAM_MARKET_GROSS_FACTOR, 2),
            "price_basis": "steam_history_seller_net",
            "gross_factor": STEAM_MARKET_GROSS_FACTOR,
        }
    return sales, row_ids, errors


def fetch_my_history_sales(cookies, debug_fn: Optional[Callable[[str], None]] = None) -> Tuple[bool, Dict[str, dict], str]:
    from app import steam_listings

    try:
        parsed_cookies = steam_listings._cookies_to_dict(cookies)
        if not parsed_cookies.get("steamLoginSecure"):
            return False, {}, "未登录 Steam"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/116.0.0.0 Safari/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "X-Requested-With": "XMLHttpRequest",
        }
        sales: Dict[str, dict] = {}
        seen_rows: Set[str] = set()
        rate_map = _load_rate_map()
        start = 0
        total = None
        for page_number in range(1, HISTORY_MAX_PAGES + 1):
            params = {"query": "", "start": start, "count": HISTORY_PAGE_SIZE, "contextid": 2, "appid": 730}
            response = steam_listings._get_with_retry(
                steam_listings.MYHISTORY_RENDER_URL,
                params,
                headers,
                parsed_cookies,
                debug_fn,
            )
            if response.status_code != 200:
                return False, {}, f"历史第 {page_number} 页 HTTP {response.status_code}"
            data = response.json() if response.text else {}
            if not data.get("success"):
                return False, {}, data.get("message", f"历史第 {page_number} 页请求失败")
            if total is None:
                total = _total_count(data)
            page_sales, row_ids, errors = _parse_page(data, rate_map)
            if errors:
                return False, {}, "；".join(errors[:3])
            if page_number > 1 and row_ids and row_ids.issubset(seen_rows):
                return False, {}, "Steam 历史分页未前进，已停止以避免使用不完整数据"
            seen_rows.update(row_ids)
            for assetid, record in page_sales.items():
                sales.setdefault(assetid, record)
            if not row_ids:
                break
            start += HISTORY_PAGE_SIZE
            if total is not None and start >= total:
                break
            if total is None and len(row_ids) < HISTORY_PAGE_SIZE:
                break
        else:
            if total is None or start < total:
                return False, {}, f"Steam 历史超过安全上限 {HISTORY_MAX_PAGES} 页，未使用不完整数据"
        if debug_fn:
            debug_fn(f"[myhistory] 完成 {len(seen_rows)} 行，售出 {len(sales)} 条")
        return True, sales, ""
    except Exception as exc:
        return False, {}, str(exc)[:120]


def fetch_my_history_sold(cookies, debug_fn: Optional[Callable[[str], None]] = None) -> Tuple[bool, Dict[str, float], str]:
    ok, sales, error = fetch_my_history_sales(cookies, debug_fn=debug_fn)
    if not ok:
        return False, {}, error
    return True, {
        assetid: float(record["gross_sale_price_cny"])
        for assetid, record in sales.items()
    }, ""
