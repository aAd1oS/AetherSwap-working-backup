"""
Steam authentication service – extracted from api.py.
Contains steampy-based login automation, profile fetching,
and auto-relogin logic.
"""
import re
import threading
import time
from typing import Optional, Tuple
from urllib.parse import urljoin, urlparse
import urllib3
import requests as _req
from app.state import log
from app.config_loader import (
    get_steam_credentials,
    load_app_config_validated,
    update_steam_creds,
)
from app.accounts import (
    get_account,
    get_current_account,
    get_profile_dir,
    set_current,
    update_account,
)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
def _steam_request_proxies() -> Optional[dict]:
    from utils.proxy_manager import get_proxy_manager
    proxies = get_proxy_manager().get_steam_proxies()
    return proxies if proxies and any(proxies.values()) else None


def _configure_steam_session(session) -> None:
    from steam.session import configure_steam_session_routing

    configure_steam_session_routing(session, proxies=_steam_request_proxies())
    session.verify = False


_STEAM_RATE_LIMITED_MESSAGE = (
    "Steam Community 请求频率受限（HTTP 429），Cookie 已保留且不代表过期，"
    "请停止重复验证，等待约30分钟后再试"
)


def _load_steam_auth_cookies(session, cookie_dict: dict) -> None:
    """Scope saved cookies so JWT Set-Cookie can replace the old Community value."""
    cookie_setter = getattr(session.cookies, "set", None)
    if not callable(cookie_setter):
        session.cookies.update(cookie_dict)
        return
    for name, value in cookie_dict.items():
        domain = ".steampowered.com" if name.lower().startswith("steamrefresh") else ".steamcommunity.com"
        cookie_setter(name, value, domain=domain, path="/")


def _check_steam_market_session(session, steam_id: str = "") -> Tuple[str, str]:
    """Use the actual downstream market page when the profile JWT flow loops."""
    try:
        response = session.get(
            "https://steamcommunity.com/market/",
            timeout=12,
            allow_redirects=False,
        )
    except Exception as exc:
        return "unavailable", f"Steam 市场登录态请求失败: {type(exc).__name__}"
    if response.status_code == 429:
        return "rate_limited", "Steam Community Market HTTP 429"
    if response.status_code in (401, 403):
        return "invalid", f"Steam Community Market HTTP {response.status_code}"
    if response.status_code < 200 or response.status_code >= 300:
        return "unavailable", f"Steam Community Market HTTP {response.status_code}"

    body = (getattr(response, "text", "") or "")[:500000]
    body_lower = body.lower()
    if "g_steamid = false" in body_lower or 'id="login_form"' in body_lower:
        return "invalid", "Steam Community Market 页面显示未登录"
    steam_id_match = re.search(r'g_steamID\s*=\s*"(\d+)"', body, flags=re.IGNORECASE)
    if steam_id_match:
        market_steam_id = steam_id_match.group(1)
        if steam_id and market_steam_id != str(steam_id):
            return "invalid", "Steam Community Market 登录账号与当前账号不一致"
        return "valid", "Steam Community Market 登录态验证通过"
    if re.search(r"g_rgWalletInfo\s*=\s*\{", body, flags=re.IGNORECASE):
        return "valid", "Steam Community Market 钱包登录态验证通过"
    return "unavailable", "Steam Community Market 未返回明确登录状态"


def _check_steam_cookies(cookie_str: str, steam_id: str = "") -> Tuple[str, str]:
    """Validate the Community session used by inventory and market operations."""
    cookie_dict = {}
    for part in (cookie_str or "").split(";"):
        s = part.strip()
        if "=" in s:
            k, _, v = s.partition("=")
            cookie_dict[k.strip()] = v.strip()
    if not cookie_dict.get("steamLoginSecure"):
        return "invalid", "缺少 steamLoginSecure"

    session = _req.Session()
    _configure_steam_session(session)
    _load_steam_auth_cookies(session, cookie_dict)
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    })
    try:
        response = session.get(
            "https://steamcommunity.com/my/profile",
            timeout=12,
            allow_redirects=False,
        )
        final_url = (response.url or "").lower()
        body_head = (getattr(response, "text", "") or "")[:20000].lower()
        if response.status_code == 429:
            return "rate_limited", "Steam Community HTTP 429"
        if response.status_code in (301, 302, 303, 307, 308):
            location = (getattr(response, "headers", {}) or {}).get("Location", "")
            redirect_url = urljoin(response.url or "https://steamcommunity.com/my/profile", location)
            parsed = urlparse(redirect_url)
            host = (parsed.hostname or "").lower()
            path = parsed.path or "/"
            target_label = f"{parsed.scheme or '?'}://{host or '?'}{path}"
            community_hosts = {"steamcommunity.com", "www.steamcommunity.com"}
            steam_login_hosts = community_hosts | {
                "store.steampowered.com",
                "login.steampowered.com",
            }
            if (
                parsed.scheme == "https"
                and host == "login.steampowered.com"
                and path.rstrip("/").lower() == "/jwt/refresh"
            ):
                refresh_response = session.get(
                    redirect_url,
                    timeout=12,
                    allow_redirects=False,
                )
                if refresh_response.status_code == 429:
                    return "rate_limited", "Steam 登录令牌刷新 HTTP 429"
                if refresh_response.status_code in (401, 403):
                    return "invalid", f"Steam 登录令牌刷新 HTTP {refresh_response.status_code}"
                if refresh_response.status_code not in (301, 302, 303, 307, 308):
                    return "unavailable", f"Steam 登录令牌刷新 HTTP {refresh_response.status_code}"

                refreshed_location = (getattr(refresh_response, "headers", {}) or {}).get("Location", "")
                refreshed_url = urljoin(refresh_response.url or redirect_url, refreshed_location)
                refreshed = urlparse(refreshed_url)
                refreshed_host = (refreshed.hostname or "").lower()
                refreshed_path = refreshed.path or "/"
                refreshed_label = f"{refreshed.scheme or '?'}://{refreshed_host or '?'}{refreshed_path}"
                if (
                    refreshed_host == "login.steampowered.com"
                    and refreshed_path.rstrip("/").lower() == "/jwt/refresh"
                ):
                    market_status, market_reason = _check_steam_market_session(session, steam_id)
                    if market_status != "unavailable":
                        return market_status, market_reason
                    return "unavailable", f"Steam 登录令牌刷新出现循环；{market_reason}"
                if "login" in refreshed_path.lower() and refreshed_host in steam_login_hosts:
                    return "invalid", "Steam Community 跳转登录页"
                if refreshed_host not in community_hosts or refreshed.scheme not in {"http", "https"}:
                    return "unavailable", f"Steam 登录令牌刷新返回异常目标（目标={refreshed_label}）"
                if refreshed_path.rstrip("/").lower() == "/my/profile":
                    confirm_response = session.get(
                        refreshed_url,
                        timeout=12,
                        allow_redirects=False,
                    )
                    if confirm_response.status_code == 429:
                        return "rate_limited", "Steam 登录令牌刷新确认 HTTP 429"
                    if confirm_response.status_code in (401, 403):
                        return "invalid", f"Steam 登录令牌刷新确认 HTTP {confirm_response.status_code}"
                    if 200 <= confirm_response.status_code < 300:
                        confirm_url = (confirm_response.url or "").lower()
                        confirm_body = (getattr(confirm_response, "text", "") or "")[:20000].lower()
                        if "login" in confirm_url or 'id="login_form"' in confirm_body:
                            return "invalid", "Steam Community 跳转登录页"
                        return "valid", "Steam Community 登录令牌刷新并验证成功"
                    if confirm_response.status_code not in (301, 302, 303, 307, 308):
                        return "unavailable", f"Steam 登录令牌刷新确认 HTTP {confirm_response.status_code}"

                    confirm_location = (getattr(confirm_response, "headers", {}) or {}).get("Location", "")
                    confirm_url = urljoin(confirm_response.url or refreshed_url, confirm_location)
                    confirmed = urlparse(confirm_url)
                    confirmed_host = (confirmed.hostname or "").lower()
                    confirmed_path = confirmed.path or "/"
                    confirmed_label = f"{confirmed.scheme or '?'}://{confirmed_host or '?'}{confirmed_path}"
                    if (
                        confirmed_host == "login.steampowered.com"
                        and confirmed_path.rstrip("/").lower() == "/jwt/refresh"
                    ):
                        market_status, market_reason = _check_steam_market_session(session, steam_id)
                        if market_status != "unavailable":
                            return market_status, market_reason
                        return "unavailable", f"Steam 登录令牌刷新出现循环；{market_reason}"
                    if "login" in confirmed_path.lower() and confirmed_host in steam_login_hosts:
                        return "invalid", "Steam Community 跳转登录页"
                    if confirmed_host not in community_hosts or confirmed.scheme not in {"http", "https"}:
                        return "unavailable", f"Steam 登录令牌刷新确认返回异常目标（目标={confirmed_label}）"
                    confirmed_profile = re.fullmatch(
                        r"/profiles/(\d+)/?",
                        confirmed_path,
                        flags=re.IGNORECASE,
                    )
                    if confirmed_profile:
                        redirected_steam_id = confirmed_profile.group(1)
                        if steam_id and redirected_steam_id != str(steam_id):
                            return "invalid", "Steam Community 登录账号与当前账号不一致"
                        return "valid", "Steam Community 登录令牌刷新并验证成功"
                    if re.fullmatch(r"/id/[^/]+/?", confirmed_path, flags=re.IGNORECASE):
                        return "valid", "Steam Community 登录令牌刷新并验证成功"
                    return "unavailable", f"Steam 登录令牌刷新确认返回无法识别的目标（目标={confirmed_label}）"
                refreshed_profile = re.fullmatch(
                    r"/profiles/(\d+)/?",
                    refreshed_path,
                    flags=re.IGNORECASE,
                )
                if refreshed_profile:
                    redirected_steam_id = refreshed_profile.group(1)
                    if steam_id and redirected_steam_id != str(steam_id):
                        return "invalid", "Steam Community 登录账号与当前账号不一致"
                    return "valid", "Steam Community 登录令牌刷新并验证成功"
                if re.fullmatch(r"/id/[^/]+/?", refreshed_path, flags=re.IGNORECASE):
                    return "valid", "Steam Community 登录令牌刷新并验证成功"
                return "unavailable", f"Steam 登录令牌刷新返回无法识别的目标（目标={refreshed_label}）"
            if "login" in path.lower() and host in steam_login_hosts:
                return "invalid", "Steam Community 跳转登录页"
            if host not in community_hosts or parsed.scheme not in {"http", "https"}:
                return "unavailable", f"Steam Community 返回异常跨站重定向（目标={target_label}）"
            profile_match = re.fullmatch(r"/profiles/(\d+)/?", path, flags=re.IGNORECASE)
            if profile_match:
                redirected_steam_id = profile_match.group(1)
                if steam_id and redirected_steam_id != str(steam_id):
                    return "invalid", "Steam Community 登录账号与当前账号不一致"
                return "valid", "Steam Community 已重定向到当前账号主页"
            if re.fullmatch(r"/id/[^/]+/?", path, flags=re.IGNORECASE):
                return "valid", "Steam Community 已重定向到当前账号主页"
            return "unavailable", "Steam Community 返回无法识别的重定向"
        if "login" in final_url or 'id="login_form"' in body_head:
            return "invalid", "Steam Community 跳转登录页"
        if response.status_code in (401, 403):
            return "invalid", f"Steam Community HTTP {response.status_code}"
        if response.status_code < 200 or response.status_code >= 300:
            return "unavailable", f"Steam Community HTTP {response.status_code}"
        return "valid", "Steam Community 市场登录态验证通过"
    except Exception as exc:
        return "unavailable", f"Steam Community 请求失败: {type(exc).__name__}"


def _verify_steam_cookies_valid(cookie_str: str, steam_id: str = "") -> bool:
    """Compatibility wrapper for callers that only need a boolean result."""
    status, _ = _check_steam_cookies(cookie_str, steam_id)
    return status == "valid"
def fetch_steam_profile_via_api(steam_id: str, cookies_str: str) -> tuple:
    if not steam_id:
        return "", ""
    display_name, avatar_url = "", ""
    session = _req.Session()
    _configure_steam_session(session)
    cookie_dict = {}
    for part in (cookies_str or "").split(";"):
        s = part.strip()
        if "=" in s:
            k, _, v = s.partition("=")
            cookie_dict[k.strip()] = v.strip()
    session.cookies.update(cookie_dict)
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    })
    try:
        r = session.get(f"https://steamcommunity.com/miniprofile/{int(steam_id) - 76561197960265728}/json", timeout=15)
        if r.status_code == 200:
            data = r.json()
            display_name = (data.get("persona_name") or "").strip()
            avatar_url = (data.get("avatar_url") or "").strip()
            if avatar_url and not avatar_url.startswith("http"):
                avatar_url = "https://avatars.steamstatic.com/" + avatar_url
            if avatar_url and "_medium" in avatar_url:
                avatar_url = avatar_url.replace("_medium", "_full")
    except Exception:
        pass
    if display_name and avatar_url:
        return display_name, avatar_url
    try:
        r = session.get(f"https://steamcommunity.com/profiles/{steam_id}", params={"xml": "1"}, timeout=15)
        if r.status_code == 200 and r.text:
            if not display_name:
                name_m = re.search(r"<steamID><!\[CDATA\[(.+?)\]\]></steamID>", r.text)
                if name_m:
                    display_name = name_m.group(1).strip()
            if not avatar_url:
                avatar_m = re.search(r"<avatarFull><!\[CDATA\[(.+?)\]\]></avatarFull>", r.text)
                if avatar_m:
                    avatar_url = avatar_m.group(1).strip()
    except Exception:
        pass
    if display_name and avatar_url:
        return display_name, avatar_url
    try:
        r = session.get(f"https://steamcommunity.com/profiles/{steam_id}", timeout=15)
        if r.status_code == 200 and r.text:
            html = r.text
            if not display_name:
                for pat in [
                    r'class="actual_persona_name"[^>]*>([^<]+)<',
                    r'"personaname"\s*:\s*"([^"]+)"',
                    r'<title>Steam Community :: (.+?)</title>',
                ]:
                    m = re.search(pat, html)
                    if m:
                        display_name = m.group(1).strip()
                        break
            if not avatar_url:
                for pat in [
                    r'class="playerAvatarAutoSizeInner"[^>]*>\s*<img[^>]+src="([^"]+)"',
                    r'"avatarfull"\s*:\s*"([^"]+)"',
                    r'property="og:image"[^>]+content="([^"]+)"',
                ]:
                    m = re.search(pat, html)
                    if m:
                        avatar_url = m.group(1).strip().replace("\\/", "/")
                        break
    except Exception:
        pass
    return display_name, avatar_url
def _get_shared_secret() -> str:
    try:
        cfg = load_app_config_validated()
        raw = ((cfg.get("steam_guard") or {}).get("shared_secret") or "").strip()
        if raw:
            return re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m.group(1), 16)), raw)
        return ""
    except Exception:
        return ""
def _build_steam_guard_dict(cur: dict, cfg: dict) -> Optional[dict]:
    """Build the steam_guard dict that steampy SteamClient.login() expects.
    steampy accepts either a path to a .maFile or a dict with these fields:
    {
        "steamid": "...",
        "shared_secret": "...",
        "identity_secret": "...",
        "device_id": "...",
    }
    We assemble this from the app config and account info.
    """
    shared_secret = ((cfg.get("steam_guard") or {}).get("shared_secret") or "").strip()
    if shared_secret:
        shared_secret = re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m.group(1), 16)), shared_secret)
    identity_secret = ((cfg.get("steam_confirm") or {}).get("identity_secret") or "").strip()
    device_id       = ((cfg.get("steam_confirm") or {}).get("device_id") or "").strip()
    steam_id        = (cur.get("steam_id") or "").strip()
    if not shared_secret:
        return None  
    return {
        "steamid": steam_id,
        "shared_secret": shared_secret,
        "identity_secret": identity_secret,
        "device_id": device_id,
    }
def _short_error_detail(exc: Exception, limit: int = 220) -> str:
    detail = str(exc).strip()
    detail = re.sub(r"\s+", " ", detail)
    if len(detail) > limit:
        detail = detail[: limit - 3] + "..."
    return detail
def _classify_steam_login_exception(exc: Exception) -> str:
    detail = _short_error_detail(exc)
    err = detail.lower()
    network_markers = (
        "max retries exceeded",
        "newconnectionerror",
        "failed to establish a new connection",
        "connection refused",
        "connection reset",
        "connection aborted",
        "name resolution",
        "temporary failure in name resolution",
        "getaddrinfo failed",
        "timed out",
        "read timed out",
        "connect timeout",
    )
    request_network_types = (
        _req.exceptions.ConnectionError,
        _req.exceptions.Timeout,
        _req.exceptions.ProxyError,
    )
    if isinstance(exc, request_network_types) or any(m in err for m in network_markers):
        return (
            "network_error: Steam 登录网络连接失败，程序没有成功连上 "
            "steamcommunity.com:443；这不是账号密码或 Steam Guard 错误。"
            "请检查本机直连、加速器/代理、DNS 或稍后重试。"
            f" 原始错误: {detail}"
        )
    if isinstance(exc, _req.exceptions.SSLError) or "ssl" in err or "certificate" in err:
        return (
            "network_error: Steam 登录 HTTPS/SSL 握手失败；通常是代理、加速器、"
            "证书拦截或本机网络环境导致。"
            f" 原始错误: {detail}"
        )
    return detail[:120]
def _do_steampy_login(username: str, password: str, steam_guard_dict: Optional[dict]) -> Tuple[bool, str, dict]:
    """Core Steam login using steampy's SteamClient with JWT/Protobuf protocol.
    Uses class-level requests.Session.request monkey-patch to bypass SSL
    verification for ALL internal steampy requests (including those made
    by LoginExecutor), exactly matching the user's reference implementation.
    Returns (ok, error_code, cookie_dict).
    """
    import json
    import requests as _req
    import requests.utils as rutils
    import urllib3
    urllib3.disable_warnings()
    _old_request = _req.Session.request
    def _bypass_ssl(self, method, url, **kwargs):
        kwargs['verify'] = False
        kwargs['proxies'] = _steam_request_proxies()
        return _old_request(self, method, url, **kwargs)
    _req.Session.request = _bypass_ssl
    try:
        from steampy.client import SteamClient
        sg_str = json.dumps(steam_guard_dict) if steam_guard_dict else None
        client = SteamClient(api_key="", username=username, password=password,
                             steam_guard=sg_str)
        client._session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Sec-Ch-Ua-Platform': '"Windows"',
            'Origin': 'https://steamcommunity.com',
            'Referer': 'https://steamcommunity.com/',
            'Accept-Language': 'zh-CN,zh;q=0.9',
        })
        client.login()
        if not client.is_session_alive():
            return False, 'session_dead', {}
        comm_cookies = client._session.cookies.get_dict(domain='steamcommunity.com')
        store_cookies = client._session.cookies.get_dict(domain='store.steampowered.com')
        merged = {**store_cookies, **comm_cookies}
        if not merged.get('steamLoginSecure'):
            merged = rutils.dict_from_cookiejar(client._session.cookies)
        return True, '', merged
    except Exception as e:
        err = str(e).lower()
        if isinstance(e, KeyError) and e.args == ("refresh_token",):
            return False, 'need_2fa', {}
        network_error = _classify_steam_login_exception(e)
        if network_error.startswith("network_error:"):
            return False, network_error, {}
        if 'invalid' in err or 'incorrect' in err or 'wrong' in err or 'bad credentials' in err or 'client_id' in err or 'client id' in err:
            return False, 'wrong_creds', {}
        if 'two-factor' in err or 'twofactor' in err or '2fa' in err or 'guard' in err:
            return False, 'need_2fa', {}
        if 'captcha' in err:
            return False, 'captcha', {}
        if 'expecting value' in err or 'no response' in err:
            return False, 'ip_blocked: Steam API无响应，请尝试重启加速器或更换IP', {}
        return False, network_error, {}
    finally:
        _req.Session.request = _old_request
def steam_id_from_cookie_str(cookie_str: str) -> str:
    slc = ""
    for part in (cookie_str or "").split(";"):
        if "=" not in part:
            continue
        key, _, value = part.partition("=")
        if key.strip().lower() == "steamloginsecure":
            slc = value.strip()
            break
    if "%7C%7C" in slc:
        return slc.split("%7C%7C", 1)[0].strip()
    elif "||" in slc:
        return slc.split("||", 1)[0].strip()
    return slc.strip() if slc.strip().isdigit() else ""
def _extract_creds_from_cookie_dict(cookie_dict: dict) -> Tuple[str, str, str]:
    """From a cookie dict return (cookie_str, session_id, steam_id)."""
    cookie_str = "; ".join(f"{k}={v}" for k, v in cookie_dict.items())
    session_id = cookie_dict.get("sessionid", "")
    steam_id = steam_id_from_cookie_str(cookie_str)
    return cookie_str, session_id, steam_id
_auto_relogin_lock = threading.Lock()
_auto_relogin_last_success = 0.0
def try_steam_auto_relogin() -> tuple:
    global _auto_relogin_last_success
    if not _auto_relogin_lock.acquire(blocking=False):
        log("auto_relogin: 另一个自动登录正在进行，跳过", "info", category="steam")
        if time.time() - _auto_relogin_last_success < 30:
            return True, "auto_ok", "另一个自动登录刚刚完成"
        return False, "busy", "另一个自动登录正在进行"
    try:
        return _try_steam_auto_relogin_impl()
    finally:
        _auto_relogin_lock.release()
def _try_steam_auto_relogin_impl() -> tuple:
    global _auto_relogin_last_success
    cur = get_current_account()
    if not cur:
        log("auto_relogin: 未设置当前账号", "warn", category="steam")
        return False, "no_account", "未设置当前 Steam 账号，无法自动登录"
    account_id = cur.get("id")
    username = (cur.get("username") or "").strip()
    password = (cur.get("password") or "").strip()
    if not username or not password:
        log("auto_relogin: 无账号或密码", "warn", category="steam")
        return False, "no_creds", "未保存账号或密码，无法自动登录"
    set_current(account_id)
    existing = get_steam_credentials()
    existing_cookies = existing.get("cookies") or existing.get("cookie") or ""
    if existing_cookies and "steamLoginSecure" in existing_cookies:
        expected_steam_id = str(cur.get("steam_id") or "").strip()
        existing_steam_id = str(existing.get("steam_id") or steam_id_from_cookie_str(existing_cookies) or "").strip()
        can_reuse_existing = True
        if expected_steam_id and existing_steam_id and expected_steam_id != existing_steam_id:
            can_reuse_existing = False
            log(
                "auto_relogin: 现有 Steam Cookie 属于其他账号，跳过复用并重新登录当前账号",
                "info",
                category="steam",
            )
        elif existing_steam_id and not expected_steam_id:
            can_reuse_existing = False
            log(
                "auto_relogin: 当前账号尚未绑定 steam_id，跳过复用旧 Cookie 并重新登录以绑定账号",
                "info",
                category="steam",
            )
        if can_reuse_existing:
            log("auto_relogin: 检测到现有 steamLoginSecure cookie，用 HTTP API 验证是否仍有效…", "info", category="steam")
            cookie_status, cookie_reason = _check_steam_cookies(existing_cookies)
            if cookie_status == "valid":
                log("auto_relogin: HTTP 验证通过，Cookie 仍有效，无需重新登录", "info", category="steam")
                _auto_relogin_last_success = time.time()
                return True, "auto_ok", "Cookie 验证有效，无需重新登录"
            if cookie_status == "rate_limited":
                log(f"auto_relogin: {cookie_reason}，保留现有 Cookie 并停止重登", "warn", category="steam")
                return False, "rate_limited", _STEAM_RATE_LIMITED_MESSAGE
            if cookie_status == "unavailable":
                message = f"Steam 暂时无法验证（{cookie_reason}），Cookie 已保留且不代表过期，请稍后再试"
                log(f"auto_relogin: {message}", "warn", category="steam")
                return False, "temporarily_unavailable", message
            log("auto_relogin: HTTP 验证显示现有 cookie 已过期，继续密码登录", "info", category="steam")
    log("auto_relogin: 开始自动登录…", "info", category="steam")
    cfg = load_app_config_validated()
    steam_guard_dict = _build_steam_guard_dict(cur, cfg)
    if steam_guard_dict:
        log("auto_relogin: 已检测到 shared_secret，将自动处理 2FA", "info", category="steam")
    else:
        log("auto_relogin: 未配置 shared_secret，以无 2FA 方式尝试登录", "info", category="steam")
    ok, err_code, cookie_dict = _do_steampy_login(username, password, steam_guard_dict)
    if ok and cookie_dict.get("steamLoginSecure"):
        cookie_str, session_id, steam_id = _extract_creds_from_cookie_dict(cookie_dict)
        update_steam_creds(cookie_str, session_id or "")
        try:
            dn, av = fetch_steam_profile_via_api(steam_id or cur.get("steam_id", ""), cookie_str)
            update_account(account_id,
                           steam_id=steam_id or cur.get("steam_id", ""),
                           display_name=dn or cur.get("display_name", ""),
                           avatar_url=av or cur.get("avatar_url", ""))
        except Exception:
            pass
        log("auto_relogin: 登录成功", "info", category="steam")
        _auto_relogin_last_success = time.time()
        return True, "auto_ok", "已自动登录并更新凭证"
    if err_code == "wrong_creds":
        log("auto_relogin: 账号或密码错误", "warn", category="steam")
        try:
            from app.notify import notify_manual_intervention_required
            notify_manual_intervention_required("Steam", "系统保存的账号或密码不正确，登录被拒绝，请立刻前往修改密码并手动干预登录")
        except Exception:
            pass
        return False, "wrong_creds", "账号或密码错误"
    if err_code == "need_2fa":
        log("auto_relogin: 需要 2FA 但无 shared_secret 或令牌有误", "warn", category="steam")
        try:
            from app.notify import notify_manual_intervention_required
            notify_manual_intervention_required("Steam", "账号需要 2FA 验证，但 shared_secret 未配置或格式有误，请前往设置页补充 Steam Guard 密钥")
        except Exception:
            pass
        return False, "need_2fa", "需要二次验证且未配置 shared_secret，请配置后重试"
    if err_code == "captcha":
        log("auto_relogin: Steam 要求人机验证（Captcha），自动登录暂时失败", "warn", category="steam")
        return False, "captcha", "Steam 触发了人机验证，请稍后重试或手动登录"
    if err_code.startswith("network_error:"):
        msg = err_code.split(": ", 1)[1] if ": " in err_code else err_code
        log(f"auto_relogin: {msg}", "warn", category="steam")
        return False, "network_error", msg
    log(f"auto_relogin: 登录失败 – {err_code}", "warn", category="steam")
    return False, "error", (err_code or "自动登录失败，请检查网络或手动重登")
def verify_steam_auto_login(account_id: str) -> dict:
    acc = get_account(account_id)
    if not acc:
        return {"ok": False, "status": "no_account", "message": "账号不存在"}
    set_current(account_id)

    existing = get_steam_credentials()
    existing_cookies = existing.get("cookies") or existing.get("cookie") or ""
    expected_steam_id = str(acc.get("steam_id") or "").strip()
    cookie_steam_id = steam_id_from_cookie_str(existing_cookies)
    cookie_matches_account = (
        not expected_steam_id
        or not cookie_steam_id
        or expected_steam_id == cookie_steam_id
    )
    if (
        existing_cookies
        and "steamLoginSecure" in existing_cookies
        and cookie_matches_account
    ):
        cookie_status, cookie_reason = _check_steam_cookies(existing_cookies, expected_steam_id)
        if cookie_status == "valid":
            try:
                steam_id = cookie_steam_id or expected_steam_id
                dn, av = fetch_steam_profile_via_api(steam_id, existing_cookies)
                update_account(
                    account_id,
                    steam_id=steam_id,
                    display_name=dn or acc.get("display_name", ""),
                    avatar_url=av or acc.get("avatar_url", ""),
                )
            except Exception:
                pass
            return {"ok": True, "status": "cookie_ok", "message": "Steam Cookie 验证有效"}
        if cookie_status == "rate_limited":
            return {"ok": False, "status": "rate_limited", "message": _STEAM_RATE_LIMITED_MESSAGE}
        if cookie_status == "unavailable":
            return {
                "ok": False,
                "status": "temporarily_unavailable",
                "message": f"Steam 暂时无法验证（{cookie_reason}），Cookie 已保留且不代表过期，请稍后再试",
            }

    cfg = load_app_config_validated()
    steam_guard_dict = _build_steam_guard_dict(acc, cfg)
    if not steam_guard_dict:
        return {"ok": False, "status": "need_2fa", "message": "请在弹出的 Steam 浏览器中完成登录确认"}

    username = (acc.get("username") or "").strip()
    password = (acc.get("password") or "").strip()
    if not username or not password:
        return {"ok": False, "status": "no_creds", "message": "未保存账号或密码，无法验证"}
    ok, err_code, cookie_dict = _do_steampy_login(username, password, steam_guard_dict)
    if ok and cookie_dict.get("steamLoginSecure"):
        cookie_str, session_id, steam_id = _extract_creds_from_cookie_dict(cookie_dict)
        update_steam_creds(cookie_str, session_id or "")
        cur_acc = get_account(account_id)
        if cur_acc:
            try:
                dn, av = fetch_steam_profile_via_api(steam_id or "", cookie_str)
                update_account(account_id,
                               steam_id=steam_id or cur_acc.get("steam_id", ""),
                               display_name=dn or cur_acc.get("display_name", ""),
                               avatar_url=av or cur_acc.get("avatar_url", ""))
            except Exception:
                pass
        return {"ok": True, "status": "auto_ok", "message": "可自动登录"}
    if err_code == "need_2fa":
        return {"ok": False, "status": "need_2fa", "message": "需要二次验证，请配置 shared_secret 后重试"}
    if err_code == "wrong_creds":
        return {"ok": False, "status": "wrong_creds", "message": "账号或密码错误"}
    if err_code == "captcha":
        return {"ok": False, "status": "captcha", "message": "Steam 触发了人机验证，请稍后重试"}
    if err_code.startswith("network_error:"):
        msg = err_code.split(": ", 1)[1] if ": " in err_code else err_code
        return {"ok": False, "status": "network_error", "message": msg}
    return {"ok": False, "status": "error", "message": err_code or "验证失败"}
