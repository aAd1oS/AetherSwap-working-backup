from app.notify import notify_buff_request_protection, send_configured_notification, send_lark


class _LarkResponse:
    status_code = 200

    def __init__(self, code=0):
        self._code = code

    def json(self):
        return {"code": self._code, "msg": "success" if self._code == 0 else "failed"}


def test_lark_sends_keyword_and_plain_text(monkeypatch):
    called = {}

    def fake_post(url, **kwargs):
        called["url"] = url
        called["json"] = kwargs["json"]
        return _LarkResponse()

    monkeypatch.setattr("app.notify.requests.post", fake_post)
    webhook = "https://open.larksuite.com/open-apis/bot/v2/hook/test-id"

    assert send_lark(webhook, "Buff 待付款", '<b>物品</b>: Test<br/><a href="https://example.com/pay">付款</a>')
    message = called["json"]["content"]["text"]
    assert message.startswith("AetherSwap\nBuff 待付款")
    assert "物品: Test" in message
    assert "付款 (https://example.com/pay)" in message


def test_lark_rejects_non_lark_webhook_without_request(monkeypatch):
    monkeypatch.setattr(
        "app.notify.requests.post",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("request must not be sent")),
    )

    assert send_lark("https://example.com/open-apis/bot/v2/hook/test-id", "title", "body") is False


def test_configured_notification_prefers_lark(monkeypatch):
    monkeypatch.setattr("app.notify.send_lark", lambda *_args: True)
    monkeypatch.setattr(
        "app.notify.send_pushplus",
        lambda *_args: (_ for _ in ()).throw(AssertionError("PushPlus must not be called")),
    )

    result = send_configured_notification(
        {"lark_webhook": "https://open.larksuite.com/open-apis/bot/v2/hook/test-id", "pushplus_token": "token"},
        "title",
        "body",
    )

    assert result == (True, "Lark")


def test_buff_protection_notification_uses_lark_and_includes_caps(monkeypatch):
    sent = []
    monkeypatch.setattr(
        "app.config_loader.load_app_config_validated",
        lambda: {
            "notify": {"lark_webhook": "https://open.larksuite.com/open-apis/bot/v2/hook/test-id"},
            "pipeline": {
                "iflow_top_n": 30,
                "buff_protection_recovery_candidate_cap": 18,
            },
        },
    )
    monkeypatch.setattr(
        "app.notify.send_lark",
        lambda webhook, title, content: sent.append((webhook, title, content)) or True,
    )

    assert notify_buff_request_protection("triggered", "BUFF HTTP 429") is True
    assert len(sent) == 1
    assert sent[0][1] == "BUFF 请求保护已触发"
    assert "正常每轮数量：30" in sent[0][2]
    assert "保护恢复期每轮数量：18" in sent[0][2]


def test_configured_notification_falls_back_to_pushplus(monkeypatch):
    monkeypatch.setattr("app.notify.send_lark", lambda *_args: False)
    monkeypatch.setattr("app.notify.send_pushplus", lambda *_args: True)

    result = send_configured_notification(
        {"lark_webhook": "https://open.larksuite.com/open-apis/bot/v2/hook/test-id", "pushplus_token": "token"},
        "title",
        "body",
    )

    assert result == (True, "PushPlus")
