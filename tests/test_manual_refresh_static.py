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


def test_analytics_tab_refreshes_current_account_transactions_and_scripts_are_cache_busted():
    main_js = (ROOT / "web" / "js" / "main.js").read_text(encoding="utf-8")
    index_html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")

    assert 'if (name === "analytics") refreshTransactions();' in main_js
    assert 'if (name === "analytics") refreshAnalytics();' not in main_js
    assert '/js/transactions.js?v=2' in index_html
    assert '/js/main.js?v=10' in index_html


def test_analytics_exposes_per_sale_purchase_and_sale_prices():
    root = Path(__file__).resolve().parents[1]
    html = (root / "web" / "index.html").read_text(encoding="utf-8")
    main_js = (root / "web" / "js" / "main.js").read_text(encoding="utf-8")

    assert 'id="analytics-sold-table"' in html
    assert "已出售明细" in html
    assert "购入价" in html
    assert "实际出售价" in html
    assert 'Number(item.sale_price) > 0' in main_js
    assert 'Number(item.price) || 0' in main_js
    assert '/js/main.js?v=10' in html


def test_analytics_summary_separates_unsold_and_sold_items():
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    main_js = (ROOT / "web" / "js" / "main.js").read_text(encoding="utf-8")

    assert 'id="analytics-summary-counts"' in html
    assert 'const status = sold ? "sold" : "unsold";' in main_js
    assert 'renderGroup("未出售"' in main_js
    assert 'renderGroup("已出售"' in main_js


def test_inventory_and_delist_use_account_scoped_steam_credentials():
    inventory_source = (ROOT / "app" / "inventory_cs2.py").read_text(encoding="utf-8")
    delist_source = (ROOT / "app" / "steam_delist.py").read_text(encoding="utf-8")

    assert "from app.config_loader import get_steam_credentials" in inventory_source
    assert "from app.config_loader import get_steam_credentials" in delist_source
    assert "from config import get_steam" not in inventory_source
    assert "from config import get_steam" not in delist_source


def test_holdings_price_refresh_shows_success_timestamp():
    main_js = (ROOT / "web" / "js" / "main.js").read_text(encoding="utf-8")
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")

    assert 'id="holdings-price-updated-at"' in html
    assert "Number(d.updated_at)" in main_js
    assert "现价更新：${updatedAt.toLocaleString()}" in main_js


def test_inventory_labels_exact_tracked_assets_and_personal_items():
    route = (ROOT / "app" / "routes" / "inventory.py").read_text(encoding="utf-8")
    ownership = (ROOT / "app" / "inventory_ownership.py").read_text(encoding="utf-8")
    main_js = (ROOT / "web" / "js" / "main.js").read_text(encoding="utf-8")

    assert 'item["managed_by_aetherswap"]' in ownership
    assert '"/api/inventory/{assetid}/ownership"' in route
    assert "自动托管" in main_js
    assert "个人保护" in main_js
    assert "inventory-ownership-select" in main_js
    assert '+ "/ownership"' in main_js
