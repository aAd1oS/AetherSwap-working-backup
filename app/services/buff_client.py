from typing import Any, Dict, List, Optional, Tuple, Union
from app.services.retry import with_retry
from buff.buyer import (
    BuffAuthExpired,
    BuffBuyer,
    BuffVerificationRequired,
    PAY_METHOD_ALIPAY,
    PAY_METHOD_WECHAT,
    BuffOrderOutcomeUnknown,
)
from utils.buff_protection import BuffProtectionError
from app.services.buff_balance import record_buff_balance_preview
buff_timeout = 15
buff_retry_attempts = 2
def count_lowest_price_orders(orders: List[dict]) -> Tuple[float, int]:
    if not orders:
        return 0.0, 0
    valid_prices = []
    for o in orders:
        try:
            p = float(o.get("price", 0))
        except (ValueError, TypeError):
            continue
        if p > 0:
            valid_prices.append(p)
    if not valid_prices:
        return 0.0, 0
    lowest = min(valid_prices)
    count = sum(1 for price in valid_prices if abs(price - lowest) < 1e-6)
    return lowest, count
def first_order_at_price(orders: List[dict], price: float) -> Optional[dict]:
    for o in orders:
        try:
            p = float(o.get("price", 0))
        except (ValueError, TypeError):
            continue
        if abs(p - price) < 1e-6:
            return o
    return None
class BuffClient:
    def __init__(
        self,
        cookies: str,
        pay_method: str = "alipay",
        balance_fallback_method: str = "wechat",
        timeout_sec: int = buff_timeout,
        steam_id: str = "",
    ) -> None:
        payment_mode = (pay_method or "alipay").strip().lower()
        fallback_mode = (balance_fallback_method or "wechat").strip().lower()
        if fallback_mode not in {"alipay", "wechat"}:
            fallback_mode = "wechat"
        manual_mode = fallback_mode if payment_mode == "balance_first" else payment_mode
        pm = PAY_METHOD_WECHAT if manual_mode == "wechat" else PAY_METHOD_ALIPAY
        self._buyer = BuffBuyer(cookies, pay_method=pm)
        self._pay_method = payment_mode
        self._fallback_payment_mode = fallback_mode
        self._timeout = timeout_sec
        self._steam_id = str(steam_id or "").strip()
    @property
    def payment_mode(self) -> str:
        return self._pay_method
    @property
    def fallback_payment_mode(self) -> str:
        return self._fallback_payment_mode
    def get_sell_orders(self, goods_id: int, game: str = "csgo") -> Optional[list]:
        return self._buyer.get_sell_orders(goods_id, game)
    def get_goods_steam_price_cny(self, search_name: str, game: str = "csgo") -> Optional[float]:
        return self._buyer.get_goods_steam_price_cny(search_name, game)
    def get_available_funds_once(self) -> Dict[str, Any]:
        return self._buyer.get_available_funds(timeout=self._timeout)
    def ask_seller_to_send(self, bill_order_id_or_ids: Union[str, List[str]], game: str = "csgo") -> bool:
        return self._buyer.ask_seller_to_send(bill_order_id_or_ids, game)
    @with_retry(max_attempts=buff_retry_attempts, fatal_exceptions=(BuffAuthExpired, BuffVerificationRequired, BuffProtectionError))
    def lock_and_get_pay_url(
        self,
        game: str,
        goods_id: int,
        sell_order_id: str,
        price: str,
    ) -> Dict[str, Any]:
        return self._buyer.lock_and_get_pay_url(game, goods_id, sell_order_id, price)

    def lock_manual_order_once(self, game: str, goods_id: int, sell_order_id: str, price: str) -> Dict[str, Any]:
        result = self._buyer.lock_order_once(
            game, goods_id, sell_order_id, price, self._buyer.pay_method,
            self._steam_id, timeout=self._timeout,
        )
        if not result.get("success"):
            return result
        order_id = str(result.get("order_id") or "")
        try:
            pay = self._buyer.get_manual_pay_url_once(game, order_id, timeout=self._timeout)
        except (BuffAuthExpired, BuffVerificationRequired, BuffProtectionError):
            raise
        except Exception as exc:
            return {
                "success": True, "order_id": order_id, "outcome_unknown": True,
                "msg": f"订单已创建但支付链接请求未知: {type(exc).__name__}: {exc}",
            }
        if not pay.get("success"):
            return {
                "success": True, "order_id": order_id, "outcome_unknown": True,
                "msg": f"订单已创建但支付链接不可用: {pay.get('msg') or '未返回链接'}",
            }
        return {
            "success": True,
            "order_id": order_id,
            "pay_url": pay.get("pay_url") or "",
            "pay_type": "wechat" if self._buyer.pay_method == PAY_METHOD_WECHAT else "alipay",
        }
    def preview_balance_payment_once(
        self,
        game: str,
        goods_id: int,
        sell_order_id: str,
        price: str,
    ) -> Dict[str, Any]:
        result = self._buyer.preview_balance_payment(
            game,
            goods_id,
            sell_order_id,
            price,
            self._steam_id,
            timeout=self._timeout,
        )
        record_buff_balance_preview(
            result,
            game=game,
            goods_id=goods_id,
            sell_order_id=sell_order_id,
            price=price,
        )
        return result
    @with_retry(max_attempts=buff_retry_attempts, fatal_exceptions=(BuffAuthExpired, BuffVerificationRequired, BuffProtectionError))
    def preview_balance_payment(
        self,
        game: str,
        goods_id: int,
        sell_order_id: str,
        price: str,
    ) -> Dict[str, Any]:
        return self.preview_balance_payment_once(
            game,
            goods_id,
            sell_order_id,
            price,
        )
    def lock_balance_order_once(
        self,
        game: str,
        goods_id: int,
        sell_order_id: str,
        price: str,
        pay_method,
    ) -> Dict[str, Any]:
        return self._buyer.lock_order_once(
            game,
            goods_id,
            sell_order_id,
            price,
            pay_method,
            self._steam_id,
            timeout=self._timeout,
        )
    def pay_bill_order_once(self, order_id: str) -> Dict[str, Any]:
        return self._buyer.pay_bill_order_once(order_id, timeout=self._timeout)
    def get_bill_order_info_once(self, order_id: str) -> Dict[str, Any]:
        return self._buyer.get_bill_order_info_once(order_id, timeout=self._timeout)
    @with_retry(max_attempts=buff_retry_attempts, fatal_exceptions=(BuffAuthExpired, BuffVerificationRequired, BuffProtectionError))
    def try_batch_buy(
        self,
        goods_id: int,
        game: str,
        orders: List[dict],
        unit_price: float,
        num: int,
    ) -> Optional[Dict[str, Any]]:
        if num < 1 or self._buyer.pay_method != PAY_METHOD_WECHAT:
            return None
        batch_id = self._buyer.batch_buy_create(goods_id, unit_price, num, game)
        if not batch_id:
            return None
        pay_url = self._buyer.batch_buy_wx_qrcode(batch_id, game)
        if not pay_url:
            return None
        return {
            "success": True,
            "pay_url": pay_url,
            "pay_type": "wechat",
            "batch_id": batch_id,
            "unit_price": unit_price,
            "num": num,
            "total_price": unit_price * num,
        }

    def try_batch_buy_once(
        self, goods_id: int, game: str, orders: List[dict], unit_price: float, num: int
    ) -> Optional[Dict[str, Any]]:
        if num < 1 or self._buyer.pay_method != PAY_METHOD_WECHAT:
            return None
        created = self._buyer.batch_buy_create_once(goods_id, unit_price, num, game)
        if not created.get("success"):
            return created
        batch_id = created["batch_id"]
        pay_url = self._buyer.batch_buy_wx_qrcode(batch_id, game)
        if not pay_url:
            return {
                "success": True, "batch_id": batch_id, "outcome_unknown": True,
                "msg": f"批量订单 {batch_id} 已创建，但支付链接不可用",
            }
        return {
            "success": True, "pay_url": pay_url, "pay_type": "wechat",
            "batch_id": batch_id, "unit_price": unit_price, "num": num,
            "total_price": unit_price * num,
        }
    @with_retry(max_attempts=buff_retry_attempts, fatal_exceptions=(BuffAuthExpired, BuffVerificationRequired, BuffProtectionError))
    def batch_buy_find_and_finalize(
        self,
        goods_id: int,
        game: str,
        max_price: float,
        num: int,
        batch_id: str,
    ) -> List[Dict[str, Any]]:
        orders = self.get_sell_orders(goods_id, game)
        if not orders:
            return []
        matched = []
        for o in orders:
            if len(matched) >= num:
                break
            try:
                p = float(o.get("price", 0))
            except (ValueError, TypeError):
                continue
            if p <= max_price:
                bill_order_id = self._buyer.batch_buy_finalize(
                    game, goods_id, str(o.get("id", "")), str(o.get("price", "")), batch_id
                )
                if bill_order_id:
                    matched.append({"id": o.get("id"), "price": p, "bill_order_id": bill_order_id})
        return matched

    def batch_buy_find_and_finalize_once(
        self, goods_id: int, game: str, max_price: float, num: int, batch_id: str
    ) -> List[Dict[str, Any]]:
        orders = self.get_sell_orders(goods_id, game) or []
        matched = []
        attempted = set()
        for order in orders:
            if len(matched) >= num:
                break
            sell_order_id = str(order.get("id") or "").strip()
            if not sell_order_id or sell_order_id in attempted:
                continue
            try:
                price = float(order.get("price") or 0)
            except (TypeError, ValueError):
                continue
            if price > max_price:
                continue
            attempted.add(sell_order_id)
            result = self._buyer.batch_buy_finalize_once(
                game, goods_id, sell_order_id, str(order.get("price") or ""), batch_id
            )
            if result.get("success"):
                matched.append({"id": sell_order_id, "price": price, "bill_order_id": result["order_id"]})
        return matched
def create_buff_client_from_config(
    credentials: dict,
    config: dict,
    steam_credentials: Optional[dict] = None,
) -> BuffClient:
    cookies = credentials.get("cookies", "")
    buff_cfg = config.get("buff", {})
    pay_method = buff_cfg.get("pay_method", "alipay")
    fallback_method = buff_cfg.get("balance_fallback_method", "wechat")
    steam_id = str((steam_credentials or {}).get("steam_id") or "").strip()
    return BuffClient(
        cookies,
        pay_method=pay_method,
        balance_fallback_method=fallback_method,
        timeout_sec=buff_timeout,
        steam_id=steam_id,
    )
