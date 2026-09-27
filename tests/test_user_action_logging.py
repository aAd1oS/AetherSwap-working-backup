from pathlib import Path

from app.routes import status


ROOT = Path(__file__).resolve().parents[1]


def test_user_action_log_records_start_and_concise_result(monkeypatch):
    entries = []
    monkeypatch.setattr(
        "app.state.log",
        lambda message, level="info", **kwargs: entries.append((message, level, kwargs)),
    )

    started = status.api_log_user_action(
        status.UserActionLogBody(action="sync_receipts", phase="start")
    )
    finished = status.api_log_user_action(
        status.UserActionLogBody(
            action="sync_receipts",
            phase="result",
            ok=False,
            partial=True,
            summary="失败 1 件，原因：库存里已有同名物品，assetid 获取冲突",
        )
    )

    assert started == {"ok": True}
    assert finished == {"ok": True}
    assert entries[0] == (
        "用户操作：刷新入库信息",
        "info",
        {"category": "user_action"},
    )
    assert entries[1][1:] == ("warn", {"category": "user_result_partial"})
    assert "assetid 获取冲突" in entries[1][0]


def test_user_action_log_rejects_unknown_action(monkeypatch):
    entries = []
    monkeypatch.setattr("app.state.log", lambda *args, **kwargs: entries.append(args))

    result = status.api_log_user_action(
        status.UserActionLogBody(action="arbitrary_client_message")
    )

    assert result == {"ok": False, "error": "不支持的用户操作类型"}
    assert entries == []


def test_user_action_frontend_categories_and_manual_calls_are_wired():
    main_js = (ROOT / "web" / "js" / "main.js").read_text(encoding="utf-8")
    console_js = (ROOT / "web" / "js" / "console.js").read_text(encoding="utf-8")
    css = (ROOT / "web" / "css" / "components.css").read_text(encoding="utf-8")
    index_html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")

    assert 'recordUserAction("sync_receipts")' in main_js
    assert "库存里已有同名物品，assetid 获取冲突" in main_js
    assert "user_result_success" in console_js
    assert ".log-user-action" in css
    assert '/js/console.js?v=2' in index_html
    assert '/js/main.js?v=13' in index_html
    assert '/css/components.css?v=3' in index_html
