from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_manual_inventory_refresh_does_not_request_sell_phase():
    main_js = (ROOT / "web" / "js" / "main.js").read_text(encoding="utf-8")
    settings_js = (ROOT / "web" / "js" / "settings.js").read_text(encoding="utf-8")

    assert 'refreshInventory(true, false)' in main_js
    assert 'refreshInventory(true, true)' in settings_js


def test_inventory_route_requires_explicit_sell_trigger():
    source = (ROOT / "app" / "routes" / "inventory.py").read_text(encoding="utf-8")

    assert "def api_inventory(refresh: bool = False, trigger_sell: bool = False)" in source
    assert "if trigger_sell:" in source


def test_removed_four_service_check_has_no_ui_or_route_references():
    proxy_route = (ROOT / "app" / "routes" / "proxy.py").read_text(encoding="utf-8")
    proxy_js = (ROOT / "web" / "js" / "proxy.js").read_text(encoding="utf-8")
    index_html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")

    combined = proxy_route + proxy_js + index_html
    assert "/api/network/services/check" not in combined
    assert "checkNetworkServices" not in combined
    assert "btn-service-check" not in combined
