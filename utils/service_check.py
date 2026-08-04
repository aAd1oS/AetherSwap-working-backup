"""Service-level connectivity checks with explicit routing policy."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Iterable

import requests

from utils.buff_protection import BUFF_MIN_INTERVAL_SECONDS
from utils.proxy_manager import get_proxy_manager
from utils.throttle import get_throttle
from utils.direct_http import direct_get

SERVICE_CHECKS = (
    ("steam_community", "Steam Community", "https://steamcommunity.com/market/", "steam_proxy"),
    ("steam_store", "Steam Store", "https://store.steampowered.com/", "steam_proxy"),
    ("buff_direct", "BUFF 直连", "https://buff.163.com/", "direct"),
    ("steamdt", "SteamDT", "https://www.steamdt.com/", "direct"),
)


def _check_one(spec: tuple, timeout: float) -> Dict[str, Any]:
    service_id, label, url, route = spec
    started = time.monotonic()
    status_code = None
    error = ""
    rate_limited = False
    try:
        if route == "steam_proxy":
            proxies = get_proxy_manager().get_steam_proxies()
            throttle_domain = "steamcommunity.com" if service_id == "steam_community" else "store.steampowered.com"
            get_throttle().wait(throttle_domain, 2.0)
            route_label = "Steam 代理池" if proxies else "直连（代理池未启用）"
        else:
            route_label = "直连"
            if service_id == "buff_direct":
                get_throttle().wait("buff.163.com", BUFF_MIN_INTERVAL_SECONDS)
        request_fn = requests.get if route == "steam_proxy" else direct_get
        request_kwargs = {
            "headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
            "timeout": timeout,
            "allow_redirects": True,
        }
        if route == "steam_proxy":
            request_kwargs["proxies"] = proxies
        response = request_fn(url, **request_kwargs)
        status_code = response.status_code
        rate_limited = status_code == 429
        ok = 200 <= status_code < 400
        if not ok:
            error = f"HTTP {status_code}"
    except Exception as exc:
        ok = False
        route_label = "Steam 代理池" if route == "steam_proxy" else "直连"
        error = f"{type(exc).__name__}: {str(exc)[:160]}"
    return {
        "id": service_id,
        "label": label,
        "url": url,
        "route": route_label,
        "ok": ok,
        "http_status": status_code,
        "rate_limited": rate_limited,
        "latency_ms": round((time.monotonic() - started) * 1000, 1),
        "error": error,
    }


def check_services(timeout: float = 8.0, specs: Iterable[tuple] = SERVICE_CHECKS) -> Dict[str, Any]:
    selected = tuple(specs)
    with ThreadPoolExecutor(max_workers=len(selected) or 1) as executor:
        results = list(executor.map(lambda spec: _check_one(spec, timeout), selected))
    return {"ok": all(result["ok"] for result in results), "services": results}
