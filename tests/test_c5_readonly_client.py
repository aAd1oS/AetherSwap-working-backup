import pytest

from app.services.c5_client import C5ApiError, C5ReadOnlyClient


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class _Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


def test_balance_request_uses_required_headers_and_query_key(monkeypatch):
    session = _Session([_Response({
        "success": True,
        "data": {
            "moneyAmount": 12.3,
            "depositAmount": 4,
            "tradeSettleAmount": 5,
            "creditMoney": 6,
            "creditDeposit": 7,
        },
    })])
    monkeypatch.setattr("app.services.c5_client._wait_for_rate_limit", lambda *_args: None)
    balance = C5ReadOnlyClient("secret-key", session=session).get_balance()

    assert balance.money_amount == pytest.approx(12.3)
    method, url, kwargs = session.calls[0]
    assert method == "GET"
    assert url.endswith("/merchant/account/v2/balance")
    assert kwargs["params"] == {"app-key": "secret-key"}
    assert kwargs["headers"]["Accept-Encoding"] == "gzip, br, zstd, deflate"


def test_executable_quote_uses_market_hash_name_and_lowest_valid_listing(monkeypatch):
    session = _Session([_Response({
        "success": True,
        "data": {
            "list": [
                {"productId": "high", "price": 10.2, "delivery": 2},
                {"productId": "low", "price": 9.8, "delivery": 1, "assetInfo": {"assetId": "42"}},
                {"productId": "", "price": 1},
            ],
        },
    })])
    monkeypatch.setattr("app.services.c5_client._wait_for_rate_limit", lambda *_args: None)
    quote = C5ReadOnlyClient("secret-key", session=session).get_executable_quote(
        "Recoil Case",
        reference_link="https://www.c5game.com/csgo/recoil",
    )

    assert quote.price == pytest.approx(9.8)
    assert quote.product_id == "low"
    assert quote.asset_id == "42"
    assert quote.listing_count == 2
    body = session.calls[0][2]["json"]
    assert body["marketHashName"] == "Recoil Case"
    assert body["appId"] == 730


def test_api_error_never_exposes_app_key(monkeypatch):
    session = _Session([_Response({
        "success": False,
        "errorCode": 400001,
        "errorMsg": "invalid secret-key",
    })])
    monkeypatch.setattr("app.services.c5_client._wait_for_rate_limit", lambda *_args: None)
    with pytest.raises(C5ApiError) as exc_info:
        C5ReadOnlyClient("secret-key", session=session).get_balance()
    assert "secret-key" not in str(exc_info.value)
    assert "***" in str(exc_info.value)
