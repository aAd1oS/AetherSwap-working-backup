from app import sell_pipeline
from steam.market import SELL_ITEM_URL, list_item


class _Response:
    status_code = 200
    text = "null"
    headers = {"Content-Type": "application/json; charset=utf-8"}
    url = SELL_ITEM_URL


class _Session:
    def __init__(self):
        self.call = None

    def post(self, url, **kwargs):
        self.call = (url, kwargs)
        return _Response()


class _Context:
    def __init__(self):
        self.logs = []

    def is_stop_requested(self):
        return False

    def set_status(self, *_args):
        pass

    def log(self, message, level="info", **_kwargs):
        self.logs.append((level, message))


def test_list_item_sends_market_post_headers_and_returns_diagnostics():
    session = _Session()

    result = list_item(session, "sid", 730, "2", "asset", 123)

    url, kwargs = session.call
    assert url == SELL_ITEM_URL
    assert kwargs["headers"]["Origin"] == "https://steamcommunity.com"
    assert kwargs["headers"]["Accept"].startswith("application/json")
    assert kwargs["headers"]["Content-Type"].startswith("application/x-www-form-urlencoded")
    assert result["status_code"] == 200
    assert result["content_type"].startswith("application/json")


def test_submit_listings_handles_json_null_without_attribute_error(monkeypatch):
    ctx = _Context()
    monkeypatch.setattr(
        sell_pipeline,
        "list_item",
        lambda *_args, **_kwargs: {
            "status_code": 200,
            "text": "null",
            "content_type": "application/json; charset=utf-8",
        },
    )
    monkeypatch.setattr(sell_pipeline, "jittered_sleep", lambda *_args, **_kwargs: None)

    listed = sell_pipeline._submit_listings(
        ctx,
        [{
            "it": {"appid": 730, "contextid": "2", "assetid": "asset"},
            "list_price": 1.23,
            "reason": "test",
            "price_cents": 123,
            "name": "Test Item",
            "aid": "asset",
        }],
        object(),
        "sid",
        0,
    )

    assert listed == []
    assert any("Steam 返回异常响应 HTTP 200" in message for _, message in ctx.logs)
    assert not any("AttributeError" in message for _, message in ctx.logs)


def test_submit_listings_blocks_price_above_configured_cap(monkeypatch):
    ctx = _Context()

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("价格超过上限时不应提交 Steam 请求")

    monkeypatch.setattr(sell_pipeline, "list_item", fail_if_called)
    listed = sell_pipeline._submit_listings(
        ctx,
        [{
            "it": {"appid": 730, "contextid": "2", "assetid": "asset"},
            "list_price": 50.01,
            "max_auto_listing_price_cny": 50.0,
            "reason": "test",
            "price_cents": 5001,
            "name": "Protected Item",
            "aid": "asset",
        }],
        object(),
        "sid",
        0,
    )

    assert listed == []
    assert any("已阻止提交 Steam 上架请求" in message for _, message in ctx.logs)
