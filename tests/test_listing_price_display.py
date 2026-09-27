from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_transaction_holdings_expose_listing_price_and_compare_it_with_market(monkeypatch):
    from app import accounts
    from app.routes import transactions

    monkeypatch.setattr(transactions, "get_purchases", lambda: [{
        "name": "AK-47 | Redline",
        "price": 10,
        "at": 1,
        "listing": True,
        "listing_price": 12.34,
        "current_market_price": 11.11,
    }])
    monkeypatch.setattr(transactions, "get_sales", lambda: [])
    monkeypatch.setattr(transactions, "load_app_config_validated", lambda: {"pipeline": {}})
    monkeypatch.setattr(accounts, "get_current_account", lambda: None)

    row = transactions.api_transactions()["transactions"][0]
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "web" / "js" / "transactions.js").read_text(encoding="utf-8")

    assert row["listing_price"] == 12.34
    assert "上架价 / 较现价" in html
    assert "const gap = listingPrice - cur;" in script
    assert "${listingPriceCell}${afterTaxCell}" in script
