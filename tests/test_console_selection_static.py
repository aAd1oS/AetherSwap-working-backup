from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_console_text_is_selectable_and_auto_scroll_respects_selection():
    css = (ROOT / "web" / "css" / "components.css").read_text(encoding="utf-8")
    script = (ROOT / "web" / "js" / "console.js").read_text(encoding="utf-8")

    assert ".console-body,\n.console-body *" in css
    assert "cursor: text;" in css
    assert "user-select: text;" in css
    assert "-webkit-user-select: text;" in css
    assert "let logTextDragActive = false;" in script
    assert "function hasLogTextSelection(out)" in script
    assert "!logTextDragActive && !hasLogTextSelection(out)" in script
    assert script.count("if (_shouldAutoScroll(out)) out.scrollTop = out.scrollHeight;") == 2
    assert "if (autoScroll) out.scrollTop = out.scrollHeight;" not in script


def test_console_only_highlights_major_business_successes_in_green():
    css = (ROOT / "web" / "css" / "components.css").read_text(encoding="utf-8")
    script = (ROOT / "web" / "js" / "console.js").read_text(encoding="utf-8")

    assert ".log-line-success" in css
    assert "color: var(--success);" in css
    assert "function _isMajorBusinessSuccess(message)" in script
    assert '"[Buff余额] 自动支付已确认成功"' in script
    assert '"已确认付款 本笔="' in script
    assert '"本次共成功购买"' in script
    assert '"[出售] 已上架"' in script
    assert '"拉取历史价格成功"' not in script
    assert 'function _lineClasses(entry)' in script
    assert '_levelClass(entry.level || "info", entry.msg || "")' in script
    assert '_roundSummaryClass(entry.category || "")' in script
    assert 'span.className = _lineClasses(l);' in script
