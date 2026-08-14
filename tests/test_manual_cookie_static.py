from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_steam_manual_cookie_ui_defers_verification_and_inventory_refresh():
    source = (ROOT / "web" / "js" / "accounts.js").read_text(encoding="utf-8")
    start = source.index("async function promptManualCookieLogin")
    end = source.index("async function openBrowserAndLogin", start)
    manual_cookie_flow = source[start:end]

    assert "仅保存，暂不联网验证" in manual_cookie_flow
    assert "verification_deferred" in manual_cookie_flow
    assert "refreshAccounts" in manual_cookie_flow
    assert "refreshInventory" not in manual_cookie_flow


def test_browser_relogin_defers_inventory_and_profile_requests():
    accounts_source = (ROOT / "web" / "js" / "accounts.js").read_text(encoding="utf-8")
    start = accounts_source.index("async function finishRelogin")
    browser_finish = accounts_source[start:]
    assert "refreshAccounts" in browser_finish
    assert "refreshInventory(true)" not in browser_finish

    auth_source = (ROOT / "app" / "routes" / "auth.py").read_text(encoding="utf-8")
    start = auth_source.index("def _relogin_worker")
    end = auth_source.index("def _relogin_start", start)
    browser_worker = auth_source[start:end]
    assert "fetch_steam_profile_via_api" not in browser_worker
    assert "steamcommunity.com/my/" in browser_worker


def test_current_account_has_explicit_steam_relogin_entry():
    source = (ROOT / "web" / "js" / "accounts.js").read_text(encoding="utf-8")

    assert 'id="btn-acc-relogin"' in source
    assert "更新 Steam 信息" in source
    assert 'showReloginModal("steam")' in source
    assert 'el("relogin-btn-open")' in source
