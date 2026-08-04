import ipaddress
import socket
from typing import Optional

import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
MARKET_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.5845.97 Safari/537.36",
    "X-Requested-With": "XMLHttpRequest",
}
def parse_cookies(cookie_str: str) -> dict:
    out = {}
    for item in cookie_str.split(";"):
        s = item.strip()
        if "=" in s:
            k, _, v = s.partition("=")
            out[k.strip()] = v.strip()
    return out


def _steam_local_accelerator_active(host: str = "steamcommunity.com") -> bool:
    """Return whether Steam traffic is redirected to a local reverse proxy."""
    try:
        addresses = {
            entry[4][0]
            for entry in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        }
        return any(ipaddress.ip_address(address).is_loopback for address in addresses)
    except (OSError, ValueError):
        return False


def configure_steam_session_routing(
    session: requests.Session,
    proxies: Optional[dict] = None,
) -> requests.Session:
    if proxies is None:
        from utils.proxy_manager import get_proxy_manager
        proxies = get_proxy_manager().get_steam_proxies()
    proxies = {
        key: value
        for key, value in (proxies or {}).items()
        if value
    }
    local_accelerator = not proxies and _steam_local_accelerator_active()
    # Project proxies win. A local Steam reverse proxy must bypass stale
    # HTTP(S)_PROXY variables; otherwise inherit the active system route.
    session.trust_env = not bool(proxies) and not local_accelerator
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
