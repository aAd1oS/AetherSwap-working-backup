import time


def test_business_success_evidence_is_saved_on_the_selected_account(monkeypatch):
    from app.services import steam_auth

    saved = {}
    monkeypatch.setattr(steam_auth, "get_account", lambda account_id: {"id": account_id})
    monkeypatch.setattr(
        steam_auth,
        "update_account",
        lambda account_id, **kwargs: saved.update({"account_id": account_id, **kwargs}),
    )

    steam_auth.record_steam_session_success("Steam 在售列表", "account-b")

    assert saved["account_id"] == "account-b"
    assert saved["steam_session_last_ok_source"] == "Steam 在售列表"
    assert saved["steam_session_last_ok_at"] > 0
    assert saved["steam_session_last_issue_status"] == ""


def test_recent_business_success_has_priority_over_temporary_network_issue(monkeypatch):
    from app import account_scope

    now = time.time()
    monkeypatch.setattr(
        account_scope,
        "get_account",
        lambda account_id: {
            "id": account_id,
            "steam_id": "76561198000000000",
            "steam_session_last_ok_at": now - 60,
            "steam_session_last_ok_source": "Steam 钱包与结算币种",
            "steam_session_last_issue_at": now,
            "steam_session_last_issue_status": "unavailable",
            "steam_session_last_issue": "ConnectTimeout",
        },
    )
    monkeypatch.setattr(
        account_scope,
        "ensure_account_runtime",
        lambda account_id: {
            "cookies": "steamLoginSecure=76561198000000000%7C%7Ctoken",
            "steam_id": "76561198000000000",
        },
    )
    monkeypatch.setattr(account_scope, "db_account_record_counts", lambda account_id: {})

    status = account_scope.get_account_runtime_status("account-a")

    assert status["steam_session"]["status"] == "valid"
    assert status["steam_session"]["last_issue_status"] == "unavailable"


def test_explicit_invalid_result_after_last_success_requires_login(monkeypatch):
    from app import account_scope

    now = time.time()
    monkeypatch.setattr(
        account_scope,
        "get_account",
        lambda account_id: {
            "id": account_id,
            "steam_id": "76561198000000000",
            "steam_session_last_ok_at": now - 60,
            "steam_session_last_issue_at": now,
            "steam_session_last_issue_status": "invalid",
        },
    )
    monkeypatch.setattr(
        account_scope,
        "ensure_account_runtime",
        lambda account_id: {
            "cookies": "steamLoginSecure=76561198000000000%7C%7Ctoken",
            "steam_id": "76561198000000000",
        },
    )
    monkeypatch.setattr(account_scope, "db_account_record_counts", lambda account_id: {})

    status = account_scope.get_account_runtime_status("account-a")

    assert status["steam_session"]["status"] == "invalid"
