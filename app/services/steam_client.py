import time
from typing import Any, Dict, List, Optional, Union
from app.services.retry import with_retry
from app.config_loader import get_steam_credentials, load_app_config_validated
from steam.client import (
    fetch_history as _fetch_history,
    market_hash_name_from_listing_url as _market_hash_name_from_listing_url,
)
steam_timeout = 15
steam_retry_attempts = 2
_history_cache: Dict[str, tuple] = {}
_history_cache_ttl = 300
_history_cache_max = 200
class SteamClient:
    def __init__(
        self,
        timeout_sec: int = steam_timeout,
        cache_ttl: int = _history_cache_ttl,
        cache_max: int = _history_cache_max,
    ) -> None:
        self._timeout = timeout_sec
        self._cache_ttl = cache_ttl
        self._cache_max = cache_max
        self.last_error = ""
        self.consecutive_failures = 0
        self.pause_current_scan = False
    def fetch_history(
        self,
        market_hash_name: str,
        app_id: int = 730,
        return_currency: bool = False,
    ) -> Union[Optional[List], Optional[Dict]]:
        self.last_error = ""
        self.pause_current_scan = False
        key = f"{market_hash_name}:{app_id}:{return_currency}"
        now = time.time()
        if key in _history_cache:
            data, ts = _history_cache[key]
            if now - ts < self._cache_ttl:
                self.consecutive_failures = 0
                return data
        result = self._fetch_history_impl(market_hash_name, app_id, return_currency)
        if result is not None:
            if len(_history_cache) >= self._cache_max:
                oldest = min(_history_cache.items(), key=lambda x: x[1][1])
                del _history_cache[oldest[0]]
            _history_cache[key] = (result, now)
        return result
    def _fetch_history_impl(
        self,
        market_hash_name: str,
        app_id: int,
        return_currency: bool,
    ) -> Union[Optional[List], Optional[Dict]]:
        cred = get_steam_credentials()
        cookies = (cred.get("cookies") or "").strip() or None
        cfg = load_app_config_validated()
        from utils.proxy_manager import get_proxy_manager
        pm = get_proxy_manager()
        for attempt in range(steam_retry_attempts):
            failed = (attempt > 0)
            proxies = pm.get_steam_proxies()
            if proxies is None and not pm.is_proxy_enabled():
                proxy_url = cfg.get("steam", {}).get("proxy")
                proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
            result, error = _fetch_history(
                market_hash_name,
                app_id=app_id,
                timeout=self._timeout,
                return_currency=return_currency,
                cookies=cookies,
                proxies=proxies,
                return_error=True,
            )
            if result is not None:
                self.last_error = ""
                self.consecutive_failures = 0
                self.pause_current_scan = False
                return result
            self.last_error = error or "Steam 历史价格接口返回空数据"
            self.consecutive_failures += 1
            from app.state import log
            level = "warn" if "HTTP 429" in self.last_error else "debug"
            log(
                f"[SteamClient] 历史数据请求失败 "
                f"(attempt={attempt+1}/{steam_retry_attempts}) "
                f"route={'project_proxy' if proxies else 'system'}: {self.last_error}",
                level,
                category="proxy",
            )
            if "HTTP 429" in self.last_error:
                self.pause_current_scan = True
                break
            if attempt < steam_retry_attempts - 1:
                from utils.delay import jittered_sleep
                jittered_sleep(1.0)
        if self.consecutive_failures >= steam_retry_attempts * 2:
            self.pause_current_scan = True
        return None
    @staticmethod
    def market_hash_name_from_listing_url(url: str) -> Optional[str]:
        return _market_hash_name_from_listing_url(url)
def create_steam_client(config: Optional[dict] = None) -> SteamClient:
    timeout = 15
    if config:
        timeout = int(config.get("steam", {}).get("timeout", steam_timeout))
    return SteamClient(timeout_sec=timeout)
