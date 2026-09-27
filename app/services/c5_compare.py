"""Read-only C5 reference-price comparison.

This module deliberately has no C5 credentials, order, payment, or trade-offer
capabilities.  Version one only compares a SteamDT C5 reference with the live
BUFF price while the existing BUFF purchase path remains authoritative.
"""
from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Any, Optional

from app.services.c5_client import C5ApiError, C5ExecutableQuote, C5ReadOnlyClient


@dataclass(frozen=True)
class C5ShadowComparison:
    buff_price: float
    c5_reference_price: float
    savings_amount: float
    savings_percent: float
    cheaper_platform: str
    reference_link: str = ""
    observed_at: str = ""


@dataclass(frozen=True)
class C5ManualRecommendation:
    market_hash_name: str
    buff_price: float
    c5_price: float
    savings_amount: float
    savings_percent: float
    reference_link: str
    product_id: str
    delivery: Optional[int]
    listing_count: int


_NOTIFY_LOCK = threading.Lock()
_LAST_NOTIFY_AT: dict[tuple[str, str], float] = {}
_NOTIFY_COOLDOWN_SECONDS = 1800


def compare_c5_reference(
    buff_price: Any,
    item: dict,
    config: dict,
) -> Optional[C5ShadowComparison]:
    if not bool(((config or {}).get("c5") or {}).get("price_compare_enabled", False)):
        return None
    try:
        buff = float(buff_price or 0)
        c5 = float((item or {}).get("c5_reference_price") or 0)
    except (TypeError, ValueError):
        return None
    if buff <= 0 or c5 <= 0:
        return None

    savings = buff - c5
    if abs(savings) < 0.005:
        cheaper = "same"
    elif savings > 0:
        cheaper = "c5"
    else:
        cheaper = "buff"
    return C5ShadowComparison(
        buff_price=buff,
        c5_reference_price=c5,
        savings_amount=savings,
        savings_percent=(savings / buff) * 100,
        cheaper_platform=cheaper,
        reference_link=str((item or {}).get("c5_reference_link") or ""),
        observed_at=str((item or {}).get("c5_reference_update_time") or ""),
    )


def format_c5_shadow_log(buff_price: Any, item: dict, config: dict) -> Optional[str]:
    if not bool(((config or {}).get("c5") or {}).get("price_compare_enabled", False)):
        return None
    result = compare_c5_reference(buff_price, item, config)
    if result is None:
        return "[C5比价·观察] 本件未匹配到有效 C5 参考价；继续使用原 BUFF 流程"
    if result.cheaper_platform == "c5":
        verdict = f"C5 参考价低 {result.savings_amount:.2f} 元（{result.savings_percent:.2f}%）"
    elif result.cheaper_platform == "buff":
        verdict = f"BUFF 实价低 {-result.savings_amount:.2f} 元（{-result.savings_percent:.2f}%）"
    else:
        verdict = "两者价格基本相同"
    return (
        f"[C5比价·观察] BUFF实价={result.buff_price:.2f} "
        f"C5参考价={result.c5_reference_price:.2f}，{verdict}；"
        "本版不会在 C5 下单，继续使用原 BUFF 流程"
    )


def fetch_c5_executable_quote(item: dict, config: dict) -> Optional[C5ExecutableQuote]:
    c5 = (config or {}).get("c5") or {}
    if not bool(c5.get("price_compare_enabled", False)):
        return None
    app_key = str(c5.get("app_key") or "").strip()
    if not app_key:
        raise C5ApiError("当前账号未配置 C5 app-key")
    name = str((item or {}).get("steam_market_name") or (item or {}).get("name") or "").strip()
    client = C5ReadOnlyClient(
        app_key,
        timeout=float(c5.get("request_timeout_seconds", 12) or 12),
    )
    return client.get_executable_quote(
        name,
        page_size=int(c5.get("quote_page_size", 10) or 10),
        reference_link=str((item or {}).get("c5_reference_link") or ""),
    )


def evaluate_c5_manual_recommendation(
    *,
    quote: Optional[C5ExecutableQuote],
    buff_price: float,
    steam_reference_price: Optional[float],
    remaining_budget: float,
    max_unit_price: Optional[float],
    max_discount: Optional[float],
    config: dict,
) -> Optional[C5ManualRecommendation]:
    c5 = (config or {}).get("c5") or {}
    if quote is None or not bool(c5.get("manual_recommendation_enabled", False)):
        return None
    if quote.price <= 0 or buff_price <= quote.price:
        return None
    savings_amount = buff_price - quote.price
    savings_percent = (savings_amount / buff_price) * 100
    if savings_amount + 1e-9 < float(c5.get("min_savings_amount", 0.2) or 0):
        return None
    if savings_percent + 1e-9 < float(c5.get("min_savings_percent", 2.0) or 0):
        return None
    if quote.price > max(float(remaining_budget or 0), 0):
        return None
    if max_unit_price is not None and quote.price > max_unit_price + 1e-9:
        return None
    if max_discount is not None:
        reference = float(steam_reference_price or 0)
        if reference <= 0 or (quote.price / reference) * 1.15 >= float(max_discount):
            return None
    return C5ManualRecommendation(
        market_hash_name=quote.market_hash_name,
        buff_price=float(buff_price),
        c5_price=float(quote.price),
        savings_amount=savings_amount,
        savings_percent=savings_percent,
        reference_link=quote.reference_link,
        product_id=quote.product_id,
        delivery=quote.delivery,
        listing_count=quote.listing_count,
    )


def notify_c5_manual_recommendation(
    recommendation: C5ManualRecommendation,
    config: dict,
) -> tuple[bool, str]:
    from app.accounts import get_current_id
    from app.notify import send_configured_notification

    account_id = str(get_current_id() or "")
    key = (account_id, recommendation.market_hash_name.casefold())
    now = time.time()
    with _NOTIFY_LOCK:
        last_at = _LAST_NOTIFY_AT.get(key, 0)
        if now - last_at < _NOTIFY_COOLDOWN_SECONDS:
            return True, "cooldown"

    delivery = "未知" if recommendation.delivery is None else str(recommendation.delivery)
    content = (
        f"物品：{recommendation.market_hash_name}<br/>"
        f"C5 最低可核验卖单：{recommendation.c5_price:.2f} 元<br/>"
        f"BUFF 当前最低价：{recommendation.buff_price:.2f} 元<br/>"
        f"预计节省：{recommendation.savings_amount:.2f} 元"
        f"（{recommendation.savings_percent:.2f}%）<br/>"
        f"C5 卖单编号：{recommendation.product_id}<br/>"
        f"发货类型原始值：{delivery}<br/>"
        f"本次只暂停该件 BUFF 锁单，不会自动在 C5 购买。"
        f"<br/><a href=\"{recommendation.reference_link}\">前往 C5 手动核验</a>"
    )
    success, channel = send_configured_notification(
        (config or {}).get("notify") or {},
        "C5 发现更低可执行价格",
        content,
    )
    if success:
        with _NOTIFY_LOCK:
            _LAST_NOTIFY_AT[key] = now
    return success, channel
