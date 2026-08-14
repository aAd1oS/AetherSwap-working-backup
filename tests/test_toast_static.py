from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_toast_supports_close_button_and_custom_duration():
    utils = (ROOT / "web" / "js" / "utils.js").read_text(encoding="utf-8")
    accounts = (ROOT / "web" / "js" / "accounts.js").read_text(encoding="utf-8")
    css = (ROOT / "web" / "css" / "components.css").read_text(encoding="utf-8")

    assert 'class="toast-close"' in utils
    assert "clearTimeout(exitTimer)" in utils
    assert "duration = 6000" in utils
    assert "12000" in accounts
    assert "--toast-duration" in css
