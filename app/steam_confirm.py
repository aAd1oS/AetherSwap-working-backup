import base64
import hashlib
import hmac
import struct
import time
from typing import Dict, List, Tuple, Union
import requests
from steam.session import configure_steam_session_routing
import urllib3
import urllib.parse
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
def _cookies_to_dict(cookie_str: str) -> dict:
    """将 Cookie 字符串转换为字典"""
    out = {}
    for part in (cookie_str or "").split(";"):
        s = part.strip()
        if "=" in s:
            k, _, v = s.partition("=")
            out[k.strip()] = v.strip()
    return out
class SteamConfirmer:
    def __init__(self, identity_secret: str, device_id: str, steam_id: str, cookies: Union[str, dict]) -> None:
        raw_secret = (identity_secret or "").strip()
        raw_secret = raw_secret.replace("\\u002B", "+").replace("\\u002b", "+")
        raw_secret = raw_secret.replace("\u002B", "+").replace("\u002b", "+")
        raw_secret = raw_secret.replace("\\/", "/")
        self.identity_secret = raw_secret
        self.device_id = urllib.parse.unquote((device_id or "").strip())
        self.steam_id = str(steam_id or "").strip()
        self.session = configure_steam_session_routing(requests.Session())
        self.session.verify = False
        if isinstance(cookies, str):
            ck_dict = _cookies_to_dict(cookies)
        else:
            ck_dict = cookies or {}
        self.session.cookies.update(ck_dict)
        self.session.headers.update({
            "User-Agent": "Steam Mobile/10372190 CFNetwork/3860.100.1 Darwin/25.0.0",
            "Accept": "application/json, text/plain, */*",
        })
    def _signature(self, tag: str, timestamp: int) -> str:
        secret_bytes = base64.b64decode(self.identity_secret)
        time_bytes = struct.pack(">Q", int(timestamp))
        if tag:
            time_bytes += tag.encode("ascii", errors="ignore")
        mac = hmac.new(secret_bytes, time_bytes, hashlib.sha1).digest()
        return base64.b64encode(mac).decode("utf-8")
    def get_confirmations(self) -> Tuple[bool, List[dict], str]:
        ts = int(time.time())
        try:
            sig = self._signature("conf", ts)
        except Exception as e:
            return False, [], f"签名失败: {e}"
        params = {
            "p": self.device_id,
            "a": self.steam_id,
            "k": sig,
            "t": ts,
            "m": "react",
            "tag": "conf",
        }
        url = "https://steamcommunity.com/mobileconf/getlist"
        try:
            r = self.session.get(url, params=params, timeout=20)
            try:
                data = r.json()
            except (ValueError, TypeError):
                return False, [], f"JSON 解析失败, status={r.status_code}, body={r.text[:200]}"
            if not data.get("success"):
                return False, [], str(data)
            return True, data.get("conf", []), ""
        except Exception as e:
            return False, [], str(e)
    def _accept_selected_batch(self, conf_list: List[dict]) -> Tuple[bool, int, str]:
        if not conf_list:
            return True, 0, ""
        ts = int(time.time())
        try:
            sig = self._signature("accept", ts)
        except Exception as e:
            return False, 0, f"签名失败: {e}"
        params = {
            "p": self.device_id,
            "a": self.steam_id,
            "k": sig,
            "t": ts,
            "m": "react",
            "tag": "accept",
            "op": "allow",
        }
        multipart = []
        for c in conf_list:
            multipart.append(("cid[]", (None, str(c.get("id")))))
            multipart.append(("ck[]", (None, str(c.get("nonce")))))
        url = "https://steamcommunity.com/mobileconf/multiajaxop"
        try:
            r = self.session.post(url, params=params, files=multipart, timeout=25)
            try:
                data = r.json()
            except (ValueError, TypeError):
                return False, 0, f"JSON 解析失败, status={r.status_code}, body={r.text[:200]}"
            if not data.get("success"):
                return False, 0, str(data)
            return True, len(conf_list), ""
        except Exception as e:
            return False, 0, str(e)

    def accept_all(self, conf_list: List[dict]) -> Tuple[bool, int, str]:
        """Legacy bulk-confirm entry point; production use is intentionally blocked."""
        return False, 0, "accept_all 已禁用；必须先严格选择本轮唯一确认"

    def accept_selected(self, conf_list: List[dict]) -> Tuple[bool, int, str]:
        """Accept only confirmations already selected by the caller."""
        return self._accept_selected_batch(conf_list)


def _confirmation_id(conf: dict) -> str:
    return str((conf or {}).get("id") or "").strip()


def _confirmation_creator_id(conf: dict) -> str:
    return str(
        (conf or {}).get("creator_id")
        or (conf or {}).get("creatorid")
        or (conf or {}).get("listing_id")
        or ""
    ).strip()


def _is_market_listing_confirmation(conf: dict) -> bool:
    raw_type = (conf or {}).get("type")
    if str(raw_type or "").strip() == "3":
        return True
    type_name = str((conf or {}).get("type_name") or (conf or {}).get("typeName") or "").lower()
    return "market" in type_name and "sell" in type_name


def select_new_listing_confirmations(
    before: List[dict],
    after: List[dict],
    successful_listings: List[dict],
) -> Tuple[List[dict], str]:
    """Fail closed when new confirmations cannot be tied to this listing batch."""
    before_ids = {_confirmation_id(conf) for conf in before if _confirmation_id(conf)}
    new_confirmations = [
        conf for conf in after
        if _confirmation_id(conf) and _confirmation_id(conf) not in before_ids
    ]
    new_market = [conf for conf in new_confirmations if _is_market_listing_confirmation(conf)]
    expected = [row for row in successful_listings if row.get("requires_confirmation") is True]
    if not expected:
        return [], "本轮没有明确要求确认的成功上架"

    selected = []
    used_ids = set()
    exact_possible = all(str(row.get("listing_id") or "").strip() for row in expected)
    if exact_possible:
        for row in expected:
            listing_id = str(row.get("listing_id") or "").strip()
            matches = [
                conf for conf in new_market
                if _confirmation_creator_id(conf) == listing_id
                and _confirmation_id(conf) not in used_ids
            ]
            if len(matches) != 1:
                return [], f"listing_id={listing_id} 的市场确认无法唯一匹配"
            selected.append(matches[0])
            used_ids.add(_confirmation_id(matches[0]))
        return selected, ""

    all_success_require_confirmation = bool(successful_listings) and all(
        row.get("requires_confirmation") is True for row in successful_listings
    )
    if (
        all_success_require_confirmation
        and len(new_confirmations) == len(new_market)
        and len(new_market) == len(successful_listings)
    ):
        return new_market, ""
    return [], "新增确认的类型或数量无法与本轮上架唯一对应"
def auto_confirm_once(identity_secret: str, device_id: str, steam_id: str, cookies: Union[str, dict]) -> Tuple[bool, int, str]:
    bot = SteamConfirmer(identity_secret, device_id, steam_id, cookies)
    ok, confs, err = bot.get_confirmations()
    if not ok:
        return False, 0, f"获取列表失败: {err}"
    ok2, n, err2 = bot.accept_all(confs)
    if not ok2:
        return False, 0, f"确认操作失败: {err2}"
    return True, n, ""
