import pytest

from app.steam_delist import _validate_delist_response


class _Response:
    def __init__(self, status=200, payload=None, json_error=None):
        self.status_code = status
        self._payload = payload
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise self._json_error
        return self._payload


@pytest.mark.parametrize(
    "response, expected",
    [
        (_Response(500, {"success": True}), "HTTP 500"),
        (_Response(200, json_error=ValueError("bad")), "非 JSON"),
        (_Response(200, None), "格式异常"),
        (_Response(200, {"success": False, "message": "no"}), "下架未成功"),
        (_Response(200, {}), "业务结果未明确成功"),
    ],
)
def test_delist_response_requires_explicit_business_success(response, expected):
    assert expected in _validate_delist_response(response)


@pytest.mark.parametrize("payload", [{"success": True}, {"success": 1}])
def test_delist_response_accepts_explicit_success(payload):
    assert _validate_delist_response(_Response(200, payload)) is None


@pytest.mark.parametrize(
    "response",
    [
        _Response(200, []),
        _Response(200, {}),
        _Response(200, json_error=ValueError("bad")),
    ],
)
def test_delist_response_accepts_ambiguous_body_only_after_listing_disappears(response):
    assert _validate_delist_response(response, listing_removed=True) is None


def test_delist_response_keeps_explicit_failure_even_after_listing_disappears():
    error = _validate_delist_response(
        _Response(200, {"success": False, "message": "no"}),
        listing_removed=True,
    )
    assert "下架未成功" in error
