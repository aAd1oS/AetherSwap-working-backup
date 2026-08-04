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
