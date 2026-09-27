"""Read-only C5 OpenAPI client used by price comparison.

This client intentionally exposes no order, payment, cancellation, or Steam
trade methods. The app-key is only sent as a query parameter and is never
included in exceptions or logs.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import threading
import time
from typing import Any, Optional
from urllib.parse import urlparse

import requests


BASE_URL = "https://openapi.c5game.com"
_RATE_LOCK = threading.Lock()
_LAST_REQUEST_AT: dict[str, float] = {}


class C5ApiError(RuntimeError):
    pass


@dataclass(frozen=True)
class C5Balance:
    money_amount: float
    deposit_amount: float
    trade_settle_amount: float
    credit_money: float
    credit_deposit: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class C5ExecutableQuote:
    market_hash_name: str
    product_id: str
    price: float
    delivery: Optional[int]
    asset_id: str
    reference_link: str
    listing_count: int

    def to_dict(self) -> dict:
        return asdict(self)


def _positive_float(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if parsed > 0 else 0.0


def _safe_c5_link(value: str) -> str:
    raw = str(value or "").strip()
    try:
        parsed = urlparse(raw)
    except Exception:
        return ""
    if parsed.scheme == "https" and parsed.hostname in {"www.c5game.com", "c5game.com"}:
        return raw
    return ""


def _wait_for_rate_limit(bucket: str, minimum_interval: float) -> None:
    with _RATE_LOCK:
        now = time.monotonic()
        wait_for = minimum_interval - (now - _LAST_REQUEST_AT.get(bucket, 0.0))
        if wait_for > 0:
            time.sleep(wait_for)
        _LAST_REQUEST_AT[bucket] = time.monotonic()


class C5ReadOnlyClient:
    def __init__(
        self,
        app_key: str,
        timeout: float = 12,
        session: Optional[requests.Session] = None,
    ) -> None:
        self._app_key = str(app_key or "").strip()
        self._timeout = max(float(timeout or 12), 3.0)
        self._session = session or requests.Session()

    @property
    def configured(self) -> bool:
        return bool(self._app_key)

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[dict] = None,
        rate_bucket: str,
        minimum_interval: float,
    ) -> Any:
        if not self._app_key:
            raise C5ApiError("尚未配置当前账号的 C5 app-key")
        _wait_for_rate_limit(rate_bucket, minimum_interval)
        try:
            response = self._session.request(
                method,
                f"{BASE_URL}/{path.lstrip('/')}",
                params={"app-key": self._app_key},
                json=json_body,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Accept-Encoding": "gzip, br, zstd, deflate",
                    "User-Agent": "AetherSwap-C5-ReadOnly/2",
                },
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise C5ApiError(f"C5 OpenAPI 网络请求失败（{type(exc).__name__}）") from None
        if response.status_code != 200:
            raise C5ApiError(f"C5 OpenAPI HTTP {response.status_code}")
        try:
            payload = response.json()
        except (TypeError, ValueError):
            raise C5ApiError("C5 OpenAPI 返回了非 JSON 数据") from None
        if not isinstance(payload, dict):
            raise C5ApiError("C5 OpenAPI 返回结构异常")
        if payload.get("success") is not True:
            code = payload.get("errorCodeStr") or payload.get("errorCode") or "unknown"
            message = str(payload.get("errorMsg") or "接口拒绝请求").strip()
            if self._app_key:
                message = message.replace(self._app_key, "***")
            raise C5ApiError(f"C5 OpenAPI 业务失败 code={code}: {message[:200]}")
        return payload.get("data")

    def get_balance(self) -> C5Balance:
        data = self._request(
            "GET",
            "/merchant/account/v2/balance",
            rate_bucket="balance",
            minimum_interval=1.0,
        )
        if not isinstance(data, dict):
            raise C5ApiError("C5 余额接口未返回有效数据")
        return C5Balance(
            money_amount=_positive_float(data.get("moneyAmount")),
            deposit_amount=_positive_float(data.get("depositAmount")),
            trade_settle_amount=_positive_float(data.get("tradeSettleAmount")),
            credit_money=_positive_float(data.get("creditMoney")),
            credit_deposit=_positive_float(data.get("creditDeposit")),
        )

    def get_executable_quote(
        self,
        market_hash_name: str,
        *,
        page_size: int = 10,
        reference_link: str = "",
    ) -> C5ExecutableQuote:
        name = str(market_hash_name or "").strip()
        if not name:
            raise C5ApiError("缺少物品 marketHashName")
        size = min(max(int(page_size or 10), 1), 50)
        data = self._request(
            "POST",
            "/merchant/market/v2/products/list",
            json_body={
                "marketHashName": name,
                "appId": 730,
                "pageNum": 1,
                "pageSize": size,
            },
            rate_bucket="products",
            minimum_interval=0.2,
        )
        rows = data.get("list") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise C5ApiError("C5 在售接口未返回卖单列表")
        valid: list[tuple[float, dict]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            price = _positive_float(row.get("price"))
            product_id = str(row.get("productId") or "").strip()
            if price > 0 and product_id:
                valid.append((price, row))
        if not valid:
            raise C5ApiError("C5 当前没有可核验的有效在售卖单")
        price, row = min(valid, key=lambda value: value[0])
        asset_info = row.get("assetInfo") if isinstance(row.get("assetInfo"), dict) else {}
        delivery = row.get("delivery")
        try:
            delivery = int(delivery) if delivery is not None else None
        except (TypeError, ValueError):
            delivery = None
        return C5ExecutableQuote(
            market_hash_name=name,
            product_id=str(row.get("productId") or ""),
            price=price,
            delivery=delivery,
            asset_id=str(asset_info.get("assetId") or ""),
            reference_link=_safe_c5_link(reference_link) or "https://www.c5game.com/",
            listing_count=len(valid),
        )
