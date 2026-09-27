import sys
import warnings
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_load_app_config_validated_applies_range_validation(monkeypatch):
    from app import config_loader

    monkeypatch.setattr(config_loader, "_config_cache", {})
    monkeypatch.setattr(config_loader, "_config_cache_ts", 0.0)
    monkeypatch.setattr(config_loader, "load_app_config", lambda: {"pipeline": {"max_discount": 9}})

    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        cfg = config_loader.load_app_config_validated()

    assert cfg["pipeline"]["max_discount"] == 1.0


def test_save_app_config_validated_applies_range_validation(monkeypatch):
    from app import config_loader

    saved = {}
    monkeypatch.setattr(config_loader, "save_app_config", lambda data: saved.update(data))

    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        config_loader.save_app_config_validated({"buff": {"price_tolerance": -1}})

    assert saved["buff"]["price_tolerance"] == 0.0


def test_save_app_config_keeps_c5_key_out_of_global_file(monkeypatch):
    from app import account_scope, config_loader

    saved_global = {}
    saved_account = {}
    monkeypatch.setattr(config_loader, "save_app_config", lambda data: saved_global.update(data))
    monkeypatch.setattr(
        account_scope,
        "save_account_config",
        lambda data: saved_account.update(data),
    )

    config_loader.save_app_config_validated({"c5": {"app_key": "account-secret"}})

    assert saved_account["c5"]["app_key"] == "account-secret"
    assert saved_global["c5"]["app_key"] == ""


def test_update_steam_creds_logs_success_without_sensitive_values(monkeypatch):
    from app import account_scope, config_loader, state

    saved = []
    logs = []
    cookie = "sessionid=session-secret; steamLoginSecure=login-secret"

    monkeypatch.setattr(
        account_scope,
        "update_account_steam_credentials",
        lambda cookies, session_id, steam_id, account_id=None: saved.append(
            (cookies, session_id, steam_id, account_id)
        ),
    )
    monkeypatch.setattr(
        state,
        "log",
        lambda message, level="info", category="", flow_id="": logs.append(
            (message, level, category)
        ),
    )

    config_loader.update_steam_creds(
        cookie,
        "session-secret",
        "76561198000000000",
        account_id="account-1",
    )

    assert saved == [(cookie, "session-secret", "76561198000000000", "account-1")]
    assert len(logs) == 1
    message, level, category = logs[0]
    assert "Steam 凭证更新成功" in message
    assert "敏感值未写入日志" in message
    assert level == "info"
    assert category == "steam"
    assert "session-secret" not in message
    assert "login-secret" not in message
    assert "76561198000000000" not in message


def test_update_steam_creds_logs_failure_without_exception_detail(monkeypatch):
    from app import account_scope, config_loader, state

    logs = []

    def fail_update(*args, **kwargs):
        raise OSError("disk error mentions login-secret")

    monkeypatch.setattr(account_scope, "update_account_steam_credentials", fail_update)
    monkeypatch.setattr(
        state,
        "log",
        lambda message, level="info", category="", flow_id="": logs.append(
            (message, level, category)
        ),
    )

    with pytest.raises(OSError, match="disk error"):
        config_loader.update_steam_creds(
            "steamLoginSecure=login-secret",
            "session-secret",
            account_id="account-1",
        )

    assert len(logs) == 1
    message, level, category = logs[0]
    assert "Steam 凭证更新失败" in message
    assert "OSError" in message
    assert "敏感值未写入日志" in message
    assert level == "error"
    assert category == "steam"
    assert "login-secret" not in message
    assert "session-secret" not in message


def test_update_buff_creds_invalidates_old_balance_after_save(monkeypatch):
    from app import account_scope, config_loader
    from app.services import buff_balance

    calls = []
    monkeypatch.setattr(
        account_scope,
        "update_account_buff_credentials",
        lambda cookies, account_id=None: calls.append(("save", cookies, account_id)),
    )
    monkeypatch.setattr(
        buff_balance,
        "invalidate_buff_balance_observation",
        lambda: calls.append(("invalidate",)),
    )

    config_loader.update_buff_creds("session=new-secret", account_id="account-1")

    assert calls == [
        ("save", "session=new-secret", "account-1"),
        ("invalidate",),
    ]
