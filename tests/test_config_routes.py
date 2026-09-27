import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_full_import_uses_validated_config_save(monkeypatch):
    from app.routes import config

    calls = {"validated": None, "raw": False}
    monkeypatch.setattr(config, "save_app_config_validated", lambda data: calls.__setitem__("validated", data))
    monkeypatch.setattr(config, "save_credentials", lambda data: None)
    monkeypatch.setattr(config, "replace_transactions", lambda purchases, sales: None)
    monkeypatch.setattr(config, "accounts_replace_all", lambda data: None)
    monkeypatch.setattr(config, "replace_log", lambda data: None)

    body = config.ImportFullBody(app_config={"pipeline": {"max_discount": 9}})
    result = config.api_import_full(body)

    assert result["ok"] is True
    assert calls["validated"] == {"pipeline": {"max_discount": 9}}


def test_full_export_includes_account_scoped_credentials(monkeypatch):
    from app.routes import config
    from app import database

    monkeypatch.setattr(config, "load_app_config", lambda: {})
    monkeypatch.setattr(config, "get_all_credentials", lambda: {"buff": {"cookies": "legacy"}})
    monkeypatch.setattr(config, "get_purchases", lambda: [])
    monkeypatch.setattr(config, "get_sales", lambda: [])
    monkeypatch.setattr(config, "list_accounts", lambda: [{"id": "account-a"}])
    monkeypatch.setattr(config, "get_log", lambda *_args: [])
    monkeypatch.setattr("app.accounts.get_current_id", lambda: "account-a")
    monkeypatch.setattr(database, "db_export_account_runtimes", lambda: [{
        "account_id": "account-a",
        "cookies": "steam-cookie-a",
        "buff_cookies": "buff-cookie-a",
    }])

    result = config.api_export_full()

    assert result["version"] == 2
    assert result["account_runtimes"][0]["buff_cookies"] == "buff-cookie-a"


def test_full_import_restores_only_known_account_runtimes(monkeypatch):
    from app.routes import config
    from app import database

    calls = {"accounts": None, "runtimes": None}
    monkeypatch.setattr(config, "accounts_replace_all", lambda data: calls.__setitem__("accounts", data))
    monkeypatch.setattr(config, "list_accounts", lambda: [{"id": "account-a"}])
    monkeypatch.setattr(config, "replace_transactions", lambda *_args: None)
    monkeypatch.setattr(config, "replace_log", lambda *_args: None)
    monkeypatch.setattr("app.config_loader.invalidate_config_cache", lambda: None)
    monkeypatch.setattr(
        database,
        "db_import_account_runtimes",
        lambda rows, allowed_account_ids=None: calls.__setitem__(
            "runtimes", (rows, allowed_account_ids)
        ),
    )
    body = config.ImportFullBody(
        accounts={"accounts": [{"id": "account-a"}], "current_id": "account-a"},
        account_runtimes=[{"account_id": "account-a", "buff_cookies": "buff-cookie-a"}],
    )

    result = config.api_import_full(body)

    assert result["ok"] is True
    assert calls["accounts"] == body.accounts
    assert calls["runtimes"][0] == body.account_runtimes
    assert calls["runtimes"][1] == {"account-a"}
