"""One-request Steam price-history probe for an isolated Steam++ test."""

from __future__ import annotations

import csv
import io
import json
import socket
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import requests
import urllib3

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config_loader import get_steam_credentials
from steam.session import parse_cookies

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ITEM_NAME = "Dual Berettas | Cobalt Quartz (Factory New)"
BLOCKING_PROCESS_NAMES = {
    "clash-verge.exe",
    "verge-mihomo.exe",
    "uu.exe",
    "uu_ball.exe",
    "uu_cloudsyn.exe",
    "uu_launcher.exe",
}
SAFE_RESPONSE_HEADERS = (
    "server",
    "via",
    "retry-after",
    "content-type",
    "content-length",
    "location",
    "x-cache",
    "x-cache-hits",
    "x-served-by",
    "x-timer",
    "x-request-id",
    "x-eresult",
    "cf-ray",
)


def _running_process_names() -> set[str]:
    completed = subprocess.run(
        ["tasklist", "/fo", "csv", "/nh"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    names: set[str] = set()
    for row in csv.reader(io.StringIO(completed.stdout)):
        if row:
            names.add(row[0].strip().casefold())
    return names


def _classify(status_code: int, body: str, location: str = "") -> str:
    body_lower = (body or "").casefold()
    location_lower = (location or "").casefold()
    if status_code == 429:
        return "HTTP 429 confirmed: routing works, but this endpoint or upstream is rate-limited."
    if status_code in (301, 302, 303, 307, 308) and "login" in location_lower:
        return "Redirected to Steam login: the saved Community Cookie is not accepted."
    if status_code == 200:
        try:
            payload = json.loads(body)
        except (TypeError, ValueError):
            payload = None
        if isinstance(payload, dict) and payload.get("success") is True and "prices" in payload:
            return "SUCCESS: Steam price history returned valid data."
        if "login" in body_lower:
            return "HTTP 200 but login content was returned: Cookie is not accepted."
        return "HTTP 200 but the response is not valid price-history JSON."
    return f"HTTP {status_code}: inspect the safe headers and body preview below."


def _write(lines: list[str], value: str = "") -> None:
    lines.append(value)
    print(value)


def main() -> int:
    lines: list[str] = []
    _write(lines, "AetherSwap Steam history one-request probe")
    _write(lines, f"Time: {datetime.now().isoformat(timespec='seconds')}")
    _write(lines, "Safety: Cookie values are never printed; exactly one HTTP request is allowed.")

    process_names = _running_process_names()
    blockers = sorted(BLOCKING_PROCESS_NAMES & process_names)
    if blockers:
        _write(lines, "ABORTED: conflicting network processes are still running: " + ", ".join(blockers))
        _write(lines, "Fully exit Clash/VPN and UU, keep only Steam++ acceleration, then run again.")
        return _save(lines, 2)
    if "steam++.accelerator.exe" not in process_names:
        _write(lines, "ABORTED: Steam++.Accelerator.exe is not running.")
        return _save(lines, 2)

    try:
        addresses = sorted({item[4][0] for item in socket.getaddrinfo("steamcommunity.com", 443)})
    except OSError as exc:
        _write(lines, f"ABORTED: steamcommunity.com DNS resolution failed: {type(exc).__name__}: {exc}")
        return _save(lines, 2)
    _write(lines, "steamcommunity.com resolves to: " + ", ".join(addresses))
    if "127.0.0.1" not in addresses:
        _write(lines, "ABORTED: Steam++ local reverse-proxy routing is not active (127.0.0.1 missing).")
        return _save(lines, 2)

    credentials = get_steam_credentials()
    cookies = parse_cookies(credentials.get("cookies", ""))
    if not cookies.get("steamLoginSecure") or not cookies.get("sessionid"):
        _write(lines, "ABORTED: saved Steam Cookie lacks steamLoginSecure or sessionid.")
        return _save(lines, 2)

    encoded = quote(ITEM_NAME, safe="")
    url = f"https://steamcommunity.com/market/pricehistory/?appid=730&market_hash_name={encoded}"
    referer = f"https://steamcommunity.com/market/listings/730/{encoded}"
    session = requests.Session()
    session.trust_env = False
    session.verify = False
    session.cookies.update(cookies)
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": referer,
        }
    )

    _write(lines, "Sending the single HTTP request now...")
    try:
        response = session.get(url, timeout=20, allow_redirects=False)
    except requests.RequestException as exc:
        _write(lines, f"REQUEST ERROR: {type(exc).__name__}: {str(exc)[:500]}")
        return _save(lines, 1)
    finally:
        session.close()

    body = response.text or ""
    _write(lines, f"Status: HTTP {response.status_code}")
    _write(lines, f"Final URL: {response.url}")
    for header in SAFE_RESPONSE_HEADERS:
        value = response.headers.get(header)
        if value:
            _write(lines, f"Header {header}: {value[:500]}")
    preview = " ".join(body[:800].split())
    _write(lines, "Body preview: " + (preview or "<empty>"))
    _write(lines, "Conclusion: " + _classify(response.status_code, body, response.headers.get("location", "")))
    return _save(lines, 0 if response.status_code == 200 else 1)


def _save(lines: list[str], exit_code: int) -> int:
    log_dir = ROOT / "log"
    log_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output = log_dir / f"steam_history_probe_{stamp}.txt"
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Saved: {output}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())