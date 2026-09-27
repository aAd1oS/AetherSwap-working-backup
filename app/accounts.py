import json
import threading
import uuid
from pathlib import Path
from typing import Any, List, Optional
_ACCOUNTS_FILE = Path(__file__).resolve().parent.parent / "config" / "accounts.json"
_cache: Optional[dict] = None
_lock = threading.RLock()
def _load() -> dict:
    global _cache
    with _lock:
        if _cache is not None:
            return _cache
        if _ACCOUNTS_FILE.exists():
            try:
                with open(_ACCOUNTS_FILE, "r", encoding="utf-8") as f:
                    _cache = json.load(f)
            except Exception:
                _cache = {"accounts": [], "current_id": None}
        else:
            _cache = {"accounts": [], "current_id": None}
        return _cache
def _save(data: dict) -> None:
    global _cache
    with _lock:
        _ACCOUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp_path = _ACCOUNTS_FILE.with_suffix(".json.tmp")
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        temp_path.replace(_ACCOUNTS_FILE)
        _cache = data
def list_accounts() -> List[dict]:
    data = _load()
    return data.get("accounts", [])
def get_current_id() -> Optional[str]:
    return _load().get("current_id")
def get_current_account() -> Optional[dict]:
    accs = list_accounts()
    cid = get_current_id()
    if not cid:
        return accs[0] if accs else None
    return next((a for a in accs if a.get("id") == cid), accs[0] if accs else None)
def get_account(account_id: str) -> Optional[dict]:
    return next((a for a in list_accounts() if a.get("id") == account_id), None)
def add_account(username: str = "", password: str = "", steam_id: str = "", display_name: str = "", avatar_url: str = "") -> dict:
    with _lock:
        data = _load()
        accs = data.get("accounts", [])
        aid = str(uuid.uuid4())[:8]
        acc = {
            "id": aid,
            "username": (username or "").strip(),
            "password": (password or "").strip(),
            "steam_id": (steam_id or "").strip(),
            "display_name": (display_name or "").strip(),
            "avatar_url": (avatar_url or "").strip(),
        }
        accs.append(acc)
        if not data.get("current_id"):
            data["current_id"] = aid
        data["accounts"] = accs
        _save(data)
        return acc
def update_account(account_id: str, **kwargs: Any) -> Optional[dict]:
    with _lock:
        data = _load()
        accs = data.get("accounts", [])
        allowed = (
            "username",
            "password",
            "steam_id",
            "display_name",
            "avatar_url",
            "currency_code",
            "region_code",
            "region_check_ok",
            "region_check_error",
            "region_checked_at",
            "currency_checked_at",
            "wallet_currency_id",
            "steam_session_last_ok_at",
            "steam_session_last_ok_source",
            "steam_session_last_issue_at",
            "steam_session_last_issue_status",
            "steam_session_last_issue",
        )
        for a in accs:
            if a.get("id") == account_id:
                for k, v in kwargs.items():
                    if k in allowed:
                        a[k] = (v or "").strip() if isinstance(v, str) else v
                _save(data)
                return a
    return None
def delete_account(account_id: str) -> bool:
    with _lock:
        data = _load()
        accs = [a for a in data.get("accounts", []) if a.get("id") != account_id]
        if len(accs) == len(data.get("accounts", [])):
            return False
        data["accounts"] = accs
        if data.get("current_id") == account_id:
            data["current_id"] = accs[0]["id"] if accs else None
        _save(data)
        return True
def set_current(account_id: str) -> bool:
    with _lock:
        data = _load()
        if not any(a.get("id") == account_id for a in data.get("accounts", [])):
            return False
        data["current_id"] = account_id
        _save(data)
        return True
def replace_all(data: dict) -> None:
    payload = {
        "accounts": list(data.get("accounts", [])),
        "current_id": data.get("current_id"),
    }
    _save(payload)
def get_profile_dir(account_id: Optional[str] = None) -> Path:
    base = Path(__file__).resolve().parent.parent / "config" / "playwright_steam"
    if account_id:
        return base / account_id
    cur = get_current_account()
    if cur:
        return base / cur.get("id", "default")
    return base / "default"

def get_buff_profile_dir(account_id: Optional[str] = None) -> Path:
    base = Path(__file__).resolve().parent.parent / "config" / "playwright_buff"
    if account_id:
        return base / account_id
    cur = get_current_account()
    if cur:
        return base / cur.get("id", "default")
    return base / "default"
