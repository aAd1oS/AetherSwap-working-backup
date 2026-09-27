import ipaddress
import socket
import threading
import time
from typing import Optional

import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
MARKET_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.5845.97 Safari/537.36",
    "X-Requested-With": "XMLHttpRequest",
}

DIRECT_PROXY_BYPASS = {"http": "", "https": "", "all": ""}
_LOCAL_ACCELERATOR_CACHE_TTL = 3.0
_local_accelerator_cache = {}
_local_accelerator_cache_at = 0.0
_local_accelerator_lock = threading.Lock()
def parse_cookies(cookie_str: str) -> dict:
    out = {}
    for item in cookie_str.split(";"):
        s = item.strip()
        if "=" in s:
            k, _, v = s.partition("=")
            out[k.strip()] = v.strip()
    return out


def get_local_steam_accelerator_status(
    host: str = "steamcommunity.com",
    port: int = 443,
    *,
    force: bool = False,
) -> dict:
    """Detect a usable local Steam reverse proxy without contacting Steam."""
    global _local_accelerator_cache, _local_accelerator_cache_at
    now = time.monotonic()
    with _local_accelerator_lock:
        if (
            not force
            and _local_accelerator_cache
            and now - _local_accelerator_cache_at < _LOCAL_ACCELERATOR_CACHE_TTL
        ):
            return dict(_local_accelerator_cache)

    addresses = []
    try:
        addresses = sorted({
            entry[4][0]
            for entry in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        })
        loopbacks = [
            address
            for address in addresses
            if ipaddress.ip_address(address).is_loopback
        ]
    except (OSError, ValueError) as exc:
        result = {
            "active": False,
            "reason": "dns_error",
            "detail": type(exc).__name__,
            "addresses": [],
        }
    else:
        if not loopbacks:
            result = {
                "active": False,
                "reason": "dns_not_loopback",
                "detail": "Steam domain is not mapped to localhost",
                "addresses": addresses,
            }
        else:
            listener = ""
            for address in loopbacks:
                try:
                    with socket.create_connection((address, port), timeout=0.35):
                        listener = address
                        break
                except OSError:
                    continue
            result = {
                "active": bool(listener),
                "reason": "ready" if listener else "listener_unavailable",
                "detail": (
                    f"local listener ready at {listener}:{port}"
                    if listener
                    else f"no local listener on port {port}"
                ),
                "addresses": loopbacks,
            }

    with _local_accelerator_lock:
        _local_accelerator_cache = dict(result)
        _local_accelerator_cache_at = now
    return result


def _steam_local_accelerator_active(host: str = "steamcommunity.com") -> bool:
    return bool(get_local_steam_accelerator_status(host).get("active"))


def direct_proxy_bypass() -> dict:
    """Explicitly block requests from inheriting HTTP(S)_PROXY variables."""
    return dict(DIRECT_PROXY_BYPASS)


def configure_steam_session_routing(
    session: requests.Session,
    proxies: Optional[dict] = None,
) -> requests.Session:
    if proxies is None:
        from utils.proxy_manager import get_proxy_manager
        proxies = get_proxy_manager().get_steam_proxies()
    explicit_bypass = (
        bool(proxies)
        and not any((proxies or {}).values())
        and any(value == "" for value in (proxies or {}).values())
    )
    proxies = {
        key: value
        for key, value in (proxies or {}).items()
        if value
    }
    local_accelerator = not proxies and _steam_local_accelerator_active()
    # Project proxies win. A local Steam reverse proxy must bypass stale
    # HTTP(S)_PROXY variables; otherwise inherit the active system route.
    session.trust_env = not bool(proxies) and not local_accelerator and not explicit_bypass
    session.proxies.update(proxies)
    return session

def create_market_session(
    cookies_raw: str,
    steam_id: str,
    *,
    headers: Optional[dict] = None,
    verify: bool = False,
) -> requests.Session:
    session = configure_steam_session_routing(requests.Session())
    session.verify = verify
    session.cookies.update(parse_cookies(cookies_raw))
    h = {**MARKET_HEADERS, "Referer": f"https://steamcommunity.com/profiles/{steam_id}/inventory/"}
    if headers:
        h.update(headers)
    session.headers.update(h)
    return session
