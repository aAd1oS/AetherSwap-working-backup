from app import steam_history, steam_listings


class _Response:
    status_code = 200
    text = "json"

    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


def _page(row_number, assetid, price_text, total=2, currency_id=23):
    row_id = f"history_row_{row_number}_0"
    return {
        "success": True,
        "total_count": total,
        "wallet_currency": currency_id,
        "hovers": (
            "CreateItemHoverFromContainer("
            f"g_rgAssets, '{row_id}_name', 730, '2', '{assetid}'"
        ),
        "results_html": f'''
            <div class="market_listing_row" id="{row_id}">
              <div class="market_listing_listed_date_combined">Sold</div>
              <span class="market_listing_price">{price_text}</span>
            </div>
        ''',
    }


def test_history_fetches_all_pages_and_keeps_amount_semantics(monkeypatch):
    pages = [_page(0, "100", "¥ 10.00"), _page(1, "200", "US$ 2.00", currency_id=1)]
    starts = []
    monkeypatch.setattr(steam_history, "HISTORY_PAGE_SIZE", 1)
    monkeypatch.setattr(steam_history, "_load_rate_map", lambda: {"USD": 7.0})

    def fake_get(_url, params, _headers, _cookies, _debug):
        starts.append(params["start"])
        return _Response(pages.pop(0))

    monkeypatch.setattr(steam_listings, "_get_with_retry", fake_get)

    ok, sales, error = steam_history.fetch_my_history_sales({"steamLoginSecure": "ok"})

    assert ok is True
    assert error == ""
    assert starts == [0, 1]
    assert sales["100"] == {
        "assetid": "100",
        "display_amount": 10.0,
        "display_currency": "CNY",
        "seller_net_cny": 10.0,
        "gross_sale_price_cny": 11.5,
        "price_basis": "steam_history_seller_net",
        "gross_factor": 1.15,
    }
    assert sales["200"]["seller_net_cny"] == 14.0
    assert sales["200"]["gross_sale_price_cny"] == 16.1


def test_unknown_exchange_rate_fails_closed(monkeypatch):
    monkeypatch.setattr(steam_history, "HISTORY_PAGE_SIZE", 1)
    monkeypatch.setattr(steam_history, "_load_rate_map", lambda: {})
    monkeypatch.setattr(
        steam_listings,
        "_get_with_retry",
        lambda *_args, **_kwargs: _Response(_page(0, "300", "US$ 2.00", total=1, currency_id=1)),
    )

    ok, sales, error = steam_history.fetch_my_history_sales({"steamLoginSecure": "ok"})

    assert ok is False
    assert sales == {}
    assert "缺少 USD 兑 CNY 汇率" in error


def test_legacy_entrypoint_uses_paginated_implementation():
    assert steam_listings.fetch_my_history_sold is steam_history.fetch_my_history_sold
    assert steam_listings.fetch_my_history_sales is steam_history.fetch_my_history_sales


def test_localized_amount_parser_handles_both_separator_orders():
    assert steam_history._amount("€ 1.234,56") == 1234.56
    assert steam_history._amount("$1,234.56 USD") == 1234.56
