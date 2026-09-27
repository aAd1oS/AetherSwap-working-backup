"""Account-scoped Steam credentials layered under the existing clients."""
from __future__ import annotations

from copy import deepcopy
import time
from typing import Optional

from app.accounts import get_account, get_current_id, list_accounts, update_account
from app.database import (
    db_account_record_counts,
    db_get_account_runtime,
    db_has_any_account_buff_credentials,
    db_has_any_account_runtime,
    db_upsert_account_runtime,
)


def _account_id(account_id: Optional[str] = None) -> str:
    return str(account_id if account_id is not None else (get_current_id() or "")).strip()


def _steam_id_from_cookies(cookies: str) -> str:
    for part in str(cookies or "").split(";"):
        name, sep, value = part.strip().partition("=")
        if sep and name.lower() == "steamloginsecure":
            value = value.strip()
            if "%7C%7C" in value:
                return value.split("%7C%7C", 1)[0].strip()
            if "||" in value:
                return value.split("||", 1)[0].strip()
            if value.isdigit():
                return value
    return ""


def ensure_account_runtime(account_id: Optional[str] = None) -> Optional[dict]:
    """Create one runtime row without ever borrowing another account's secrets."""
    aid = _account_id(account_id)
    if not aid:
        return None
    try:
        existing = db_get_account_runtime(aid)
    except Exception:
        from app.database import init_db
        init_db()
        existing = db_get_account_runtime(aid)
    if existing is not None:
        return existing

    if not db_has_any_account_runtime():
        _migrate_legacy_account_runtimes()
        existing = db_get_account_runtime(aid)
        if existing is not None:
            return existing
    account = get_account(aid) or {}
    return db_upsert_account_runtime(aid, {
        "steam_id": str(account.get("steam_id") or "").strip(),
    })


def _migrate_legacy_account_runtimes() -> None:
    """Split the old global Cookie and token settings without guessing identity."""
    if db_has_any_account_runtime():
        return
    from config import get_buff, get_steam, load_app_config

    accounts = list_accounts()
    current_id = _account_id()
    legacy_steam = get_steam() or {}
    legacy_buff = get_buff() or {}
    legacy_config = load_app_config() or {}
    guard = legacy_config.get("steam_guard") or {}
    confirm = legacy_config.get("steam_confirm") or {}
    cookies = legacy_steam.get("cookies") or legacy_steam.get("cookie") or ""
    legacy_steam_id = str(
        legacy_steam.get("steam_id") or _steam_id_from_cookies(cookies) or ""
    ).strip()
    credential_account_id = next(
        (
            str(account.get("id") or "")
            for account in accounts
            if legacy_steam_id and str(account.get("steam_id") or "").strip() == legacy_steam_id
        ),
        current_id,
    )

    # Token settings historically followed the selected account, while the
    # global Cookie can be proven to belong to another account by SteamID.
    if current_id:
        current = get_account(current_id) or {}
        current_data = {
            "steam_id": current.get("steam_id") or "",
            "shared_secret": guard.get("shared_secret") or "",
            "identity_secret": confirm.get("identity_secret") or "",
            "device_id": confirm.get("device_id") or "",
            "auto_confirm_enabled": bool(confirm.get("enabled", False)),
        }
        # A global BUFF session cannot be matched to a Steam account. It is
        # only safe to migrate automatically when exactly one account exists.
        if len(accounts) == 1:
            current_data["buff_cookies"] = legacy_buff.get("cookies") or ""
        db_upsert_account_runtime(current_id, current_data)
    if credential_account_id:
        credential_account = get_account(credential_account_id) or {}
        db_upsert_account_runtime(credential_account_id, {
            "cookies": cookies,
            "session_id": legacy_steam.get("session_id") or "",
            "steam_id": legacy_steam_id or credential_account.get("steam_id") or "",
        })


def get_account_steam_credentials(account_id: Optional[str] = None) -> dict:
    runtime = ensure_account_runtime(account_id)
    if runtime is None:
        return {}
    return {
        "cookies": runtime.get("cookies", ""),
        "session_id": runtime.get("session_id", ""),
        "steam_id": runtime.get("steam_id", ""),
    }


def get_account_buff_credentials(account_id: Optional[str] = None) -> dict:
    aid = _account_id(account_id)
    runtime = ensure_account_runtime(aid)
    if runtime is None:
        from config import get_buff
        return get_buff() or {}
    cookies = str(runtime.get("buff_cookies") or "").strip()
    if not cookies and len(list_accounts()) == 1 and not db_has_any_account_buff_credentials():
        from config import get_buff
        legacy_cookies = str((get_buff() or {}).get("cookies") or "").strip()
        if legacy_cookies:
            runtime = db_upsert_account_runtime(aid, {"buff_cookies": legacy_cookies})
            cookies = str(runtime.get("buff_cookies") or "").strip()
    return {"cookies": cookies}


def update_account_buff_credentials(
    cookies: str,
    account_id: Optional[str] = None,
) -> None:
    aid = _account_id(account_id)
    if not aid:
        raise ValueError("未选择当前 Steam 账号")
    ensure_account_runtime(aid)
    db_upsert_account_runtime(aid, {"buff_cookies": cookies})


def get_account_c5_credentials(account_id: Optional[str] = None) -> dict:
    runtime = ensure_account_runtime(account_id) or {}
    return {"app_key": str(runtime.get("c5_app_key") or "").strip()}


def update_account_c5_credentials(
    app_key: str,
    account_id: Optional[str] = None,
) -> None:
    aid = _account_id(account_id)
    if not aid:
        raise ValueError("未选择当前 Steam 账号")
    ensure_account_runtime(aid)
    db_upsert_account_runtime(aid, {"c5_app_key": app_key})


def update_account_steam_credentials(
    cookies: str,
    session_id: str,
    steam_id: Optional[str] = None,
    account_id: Optional[str] = None,
) -> None:
    aid = _account_id(account_id)
    if not aid:
        raise ValueError("未选择当前Steam账号")
    parsed_steam_id = str(steam_id or _steam_id_from_cookies(cookies) or "").strip()
    current = ensure_account_runtime(aid) or {}
    effective_steam_id = parsed_steam_id or str(current.get("steam_id") or "").strip()
    db_upsert_account_runtime(aid, {
        "cookies": cookies,
        "session_id": session_id,
        "steam_id": effective_steam_id,
    })
    if effective_steam_id:
        update_account(aid, steam_id=effective_steam_id)


def overlay_account_config(config: dict, account_id: Optional[str] = None) -> dict:
    result = deepcopy(config)
    runtime = ensure_account_runtime(account_id)
    if runtime is None:
        return result
    guard = dict(result.get("steam_guard") or {})
    confirm = dict(result.get("steam_confirm") or {})
    guard["shared_secret"] = runtime.get("shared_secret", "")
    confirm.update({
        "identity_secret": runtime.get("identity_secret", ""),
        "device_id": runtime.get("device_id", ""),
        "enabled": bool(runtime.get("auto_confirm_enabled", False)),
    })
    result["steam_guard"] = guard
    result["steam_confirm"] = confirm
    c5 = dict(result.get("c5") or {})
    c5["app_key"] = runtime.get("c5_app_key", "")
    result["c5"] = c5
    return result


def save_account_config(config: dict, account_id: Optional[str] = None) -> None:
    aid = _account_id(account_id)
    if not aid:
        return
    guard = config.get("steam_guard") or {}
    confirm = config.get("steam_confirm") or {}
    c5 = config.get("c5") or {}
    ensure_account_runtime(aid)
    db_upsert_account_runtime(aid, {
        "shared_secret": guard.get("shared_secret") or "",
        "identity_secret": confirm.get("identity_secret") or "",
        "device_id": confirm.get("device_id") or "",
        "auto_confirm_enabled": bool(confirm.get("enabled", False)),
        "c5_app_key": c5.get("app_key") or "",
    })


def get_account_runtime_status(account_id: str) -> dict:
    aid = _account_id(account_id)
    account = get_account(aid) or {}
    buff_credentials = get_account_buff_credentials(aid)
    runtime = ensure_account_runtime(aid) or {}
    expected_steam_id = str(account.get("steam_id") or runtime.get("steam_id") or "").strip()
    cookie_steam_id = _steam_id_from_cookies(runtime.get("cookies", ""))
    identity_matches = not expected_steam_id or not cookie_steam_id or expected_steam_id == cookie_steam_id
    last_ok_at = float(account.get("steam_session_last_ok_at") or 0)
    last_issue_at = float(account.get("steam_session_last_issue_at") or 0)
    last_issue_status = str(account.get("steam_session_last_issue_status") or "").strip()
    if not runtime.get("cookies"):
        session_status = "unconfigured"
    elif not identity_matches:
        session_status = "invalid"
    elif last_issue_status == "invalid" and last_issue_at >= last_ok_at:
        session_status = "invalid"
    elif last_ok_at > 0 and time.time() - last_ok_at <= 24 * 3600:
        session_status = "valid"
    elif last_ok_at > 0:
        session_status = "stale"
    elif last_issue_status in {"rate_limited", "unavailable"}:
        session_status = last_issue_status
    else:
        session_status = "pending"
    return {
        "account_id": aid,
        "has_cookie": bool(runtime.get("cookies")),
        "has_session_id": bool(runtime.get("session_id")),
        "has_buff_cookie": bool(buff_credentials.get("cookies")),
        "has_c5_app_key": bool(runtime.get("c5_app_key")),
        "has_shared_secret": bool(runtime.get("shared_secret")),
        "has_identity_secret": bool(runtime.get("identity_secret")),
        "has_device_id": bool(runtime.get("device_id")),
        "auto_confirm_enabled": bool(runtime.get("auto_confirm_enabled", False)),
        "auto_sell_enabled": bool(runtime.get("auto_sell_enabled", False)),
        "expected_steam_id": expected_steam_id,
        "cookie_steam_id": cookie_steam_id,
        "identity_matches": identity_matches,
        "steam_session": {
            "status": session_status,
            "last_ok_at": last_ok_at,
            "last_ok_source": str(account.get("steam_session_last_ok_source") or ""),
            "last_issue_at": last_issue_at,
            "last_issue_status": last_issue_status,
            "last_issue": str(account.get("steam_session_last_issue") or ""),
        },
        "records": db_account_record_counts(aid),
    }


def set_account_auto_sell_enabled(account_id: str, enabled: bool) -> dict:
    aid = _account_id(account_id)
    if not aid or not get_account(aid):
        raise ValueError("账号不存在")
    ensure_account_runtime(aid)
    return db_upsert_account_runtime(aid, {"auto_sell_enabled": bool(enabled)})


def validate_current_account_identity() -> tuple[bool, str]:
    aid = _account_id()
    if not aid:
        return False, "未选择当前Steam账号"
    status = get_account_runtime_status(aid)
    if not status.get("has_cookie"):
        return False, "当前账号尚未配置自己的Steam Cookie"
    expected = status.get("expected_steam_id") or ""
    actual = status.get("cookie_steam_id") or ""
    if expected and actual and expected != actual:
        return False, f"当前账号SteamID与Cookie所属SteamID不一致（{expected} != {actual}）"
    if not status.get("identity_matches", True):
        return False, "当前账号Steam身份校验未通过"
    return True, ""
