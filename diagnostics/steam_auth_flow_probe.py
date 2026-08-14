from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urljoin, urlparse

import requests


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config_loader import get_steam_credentials
from app.accounts import get_current_account, get_profile_dir
from app.services.steam_auth import _load_steam_auth_cookies
from steam.session import configure_steam_session_routing, parse_cookies


ALLOWED_HOSTS = {
    "steamcommunity.com",
    "www.steamcommunity.com",
    "login.steampowered.com",
    "store.steampowered.com",
}
REDIRECT_CODES = {301, 302, 303, 307, 308}


def safe_target(url: str) -> str:
    parsed = urlparse(url or "")
    query_names = sorted({name for name, _ in parse_qsl(parsed.query, keep_blank_values=True)})
    query_hint = f"?keys={','.join(query_names)}" if query_names else ""
    return f"{parsed.scheme or '?'}://{parsed.hostname or '?'}{parsed.path or '/'}{query_hint}"


def cookie_header_names(header: str) -> str:
    names = []
    for part in (header or "").split(";"):
        if "=" not in part:
            continue
        name = part.split("=", 1)[0].strip()
        if name and name not in names:
            names.append(name)
    return ", ".join(names) if names else "<none>"


def cookie_inventory(session: requests.Session) -> str:
    entries = sorted({f"{cookie.name}@{cookie.domain or '<hostless>'}" for cookie in session.cookies})
    return ", ".join(entries) if entries else "<none>"


def response_cookie_inventory(response: requests.Response) -> str:
    entries = sorted({f"{cookie.name}@{cookie.domain or '<hostless>'}" for cookie in response.cookies})
    return ", ".join(entries) if entries else "<none>"


def raw_set_cookie_inventory(response: requests.Response) -> str:
    raw_headers = getattr(getattr(response, "raw", None), "headers", None)
    values = []
    if raw_headers is not None:
        if hasattr(raw_headers, "getlist"):
            values = list(raw_headers.getlist("Set-Cookie"))
        elif hasattr(raw_headers, "get_all"):
            values = list(raw_headers.get_all("Set-Cookie") or [])
    if not values:
        combined = (getattr(response, "headers", {}) or {}).get("Set-Cookie", "")
        if combined:
            values = [combined]

    entries = []
    for value in values:
        first = str(value).split(";", 1)[0]
        name = first.split("=", 1)[0].strip() if "=" in first else "<malformed>"
        domain_match = re.search(r";\s*Domain=([^;,]+)", str(value), flags=re.IGNORECASE)
        domain = domain_match.group(1).strip() if domain_match else "<host-only>"
        flags = []
        if re.search(r";\s*Secure(?:;|$)", str(value), flags=re.IGNORECASE):
            flags.append("Secure")
        if re.search(r";\s*HttpOnly(?:;|$)", str(value), flags=re.IGNORECASE):
            flags.append("HttpOnly")
        same_site = re.search(r";\s*SameSite=([^;,]+)", str(value), flags=re.IGNORECASE)
        if same_site:
            flags.append(f"SameSite={same_site.group(1).strip()}")
        suffix = f" [{', '.join(flags)}]" if flags else ""
        entries.append(f"{name}@{domain}{suffix}")
    return ", ".join(entries) if entries else "<none>"


def request_cookie_inventory(response: requests.Response) -> str:
    request = getattr(response, "request", None)
    headers = getattr(request, "headers", {}) or {}
    return cookie_header_names(headers.get("Cookie", ""))


def append(lines: list[str], message: str) -> None:
    lines.append(message)
    print(message)


def main() -> int:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output = ROOT / "log" / f"steam_auth_flow_probe_{timestamp}.txt"
    output.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    append(lines, "AetherSwap Steam auth-flow probe")
    append(lines, f"Time: {datetime.now().isoformat(timespec='seconds')}")
    append(lines, "Safety: no cookie values, query values, response bodies, or JWT values are logged.")
    append(lines, "Request cap: four bounded profile-flow GETs plus one market GET.")
    append(lines, f"Project root: {ROOT}")

    current_account = get_current_account() or {}
    account_id = str(current_account.get("id") or "")
    profile_dir = get_profile_dir(account_id) if account_id else None
    append(lines, f"Current account id: {account_id or '<none>'}")
    append(lines, f"Expected browser profile: {profile_dir or '<none>'}")
    append(lines, f"Expected browser profile exists: {bool(profile_dir and profile_dir.exists())}")

    raw_cookies = get_steam_credentials().get("cookies", "")
    cookie_dict = parse_cookies(raw_cookies)
    if not cookie_dict.get("steamLoginSecure"):
        append(lines, "ABORT: saved credentials do not contain steamLoginSecure.")
        output.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return 2

    session = configure_steam_session_routing(requests.Session())
    session.verify = False
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    })
    _load_steam_auth_cookies(session, cookie_dict)
    append(lines, f"Session trust_env: {session.trust_env}")
    append(lines, f"Initial cookie names/domains: {cookie_inventory(session)}")

    current_url = "https://steamcommunity.com/my/profile"
    for step in range(1, 5):
        append(lines, "")
        append(lines, f"Profile step {step}: GET {safe_target(current_url)}")
        parsed = urlparse(current_url)
        if parsed.scheme != "https" or (parsed.hostname or "").lower() not in ALLOWED_HOSTS:
            append(lines, "STOP: target is not an approved HTTPS Steam host.")
            break
        try:
            response = session.get(current_url, timeout=15, allow_redirects=False)
        except Exception as exc:
            append(lines, f"ERROR: {type(exc).__name__}: {str(exc)[:180]}")
            break
        append(lines, f"Status: HTTP {response.status_code}")
        append(lines, f"Server: {response.headers.get('Server', '<none>')}")
        append(lines, f"Request Cookie names: {request_cookie_inventory(response)}")
        append(lines, f"Raw Set-Cookie names/domains: {raw_set_cookie_inventory(response)}")
        append(lines, f"Response Set-Cookie names/domains: {response_cookie_inventory(response)}")
        append(lines, f"Session cookie names/domains now: {cookie_inventory(session)}")
        if response.status_code not in REDIRECT_CODES:
            break
        location = response.headers.get("Location", "")
        if not location:
            append(lines, "STOP: redirect has no Location header.")
            break
        current_url = urljoin(response.url or current_url, location)
        append(lines, f"Redirect target: {safe_target(current_url)}")

    append(lines, "")
    append(lines, "Market check: GET https://steamcommunity.com/market/")
    try:
        market = session.get(
            "https://steamcommunity.com/market/",
            timeout=15,
            allow_redirects=False,
        )
        append(lines, f"Market status: HTTP {market.status_code}")
        append(lines, f"Market server: {market.headers.get('Server', '<none>')}")
        append(lines, f"Market request Cookie names: {request_cookie_inventory(market)}")
        append(lines, f"Market raw Set-Cookie names/domains: {raw_set_cookie_inventory(market)}")
        location = market.headers.get("Location", "")
        append(lines, f"Market redirect target: {safe_target(urljoin(market.url, location)) if location else '<none>'}")
        append(lines, f"Market Set-Cookie names/domains: {response_cookie_inventory(market)}")
        append(lines, f"Final session cookie names/domains: {cookie_inventory(session)}")
    except Exception as exc:
        append(lines, f"Market ERROR: {type(exc).__name__}: {str(exc)[:180]}")

    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    append(lines, "")
    print(f"Saved: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
