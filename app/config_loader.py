import threading
from copy import deepcopy
import time as _time
from app.config_schema import DEFAULTS, _validate_ranges, merge, validate_and_fill
from config import (
    get_buff,
    load_app_config,
    save_app_config,
    update_buff_credentials,
)
_config_cache: dict = {}
_config_cache_ts: float = 0.0
_CONFIG_CACHE_TTL = 5.0  
_config_cache_lock = threading.Lock()
_config_cache_account_id: str = ""
def _invalidate_config_cache() -> None:
    global _config_cache, _config_cache_ts, _config_cache_account_id
    with _config_cache_lock:
        _config_cache = {}
        _config_cache_ts = 0.0
        _config_cache_account_id = ""
def get_steam_credentials() -> dict:
    from app.account_scope import get_account_steam_credentials
    return get_account_steam_credentials()
def get_buff_credentials() -> dict:
    return get_buff()
def update_steam_creds(cookies: str, session_id: str, steam_id: str = None, account_id: str = None) -> None:
    from app.account_scope import update_account_steam_credentials
    update_account_steam_credentials(cookies, session_id, steam_id, account_id=account_id)
def update_buff_creds(cookies: str) -> None:
    update_buff_credentials(cookies)
def load_app_config_validated() -> dict:
    global _config_cache, _config_cache_ts, _config_cache_account_id
    from app.accounts import get_current_id
    from app.account_scope import overlay_account_config
    account_id = str(get_current_id() or "")
    now = _time.monotonic()
    with _config_cache_lock:
        if _config_cache and _config_cache_account_id == account_id and (now - _config_cache_ts) < _CONFIG_CACHE_TTL:
            return _config_cache
        raw = load_app_config()
        result = _validate_ranges(validate_and_fill(merge(DEFAULTS, overlay_account_config(raw, account_id))))
        _config_cache = result
        _config_cache_ts = now
        _config_cache_account_id = account_id
        return result
def save_app_config_validated(data: dict) -> None:
    from app.account_scope import save_account_config
    filled = _validate_ranges(validate_and_fill(merge(DEFAULTS, data)))
    save_account_config(filled)
    global_config = deepcopy(filled)
    global_config.setdefault("steam_guard", {})["shared_secret"] = ""
    global_config.setdefault("steam_confirm", {}).update({
        "identity_secret": "",
        "device_id": "",
        "enabled": False,
    })
    save_app_config(global_config)
    _invalidate_config_cache()  

def invalidate_config_cache() -> None:
    _invalidate_config_cache()
