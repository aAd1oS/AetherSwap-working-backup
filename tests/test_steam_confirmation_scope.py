from app.steam_confirm import SteamConfirmer, select_new_listing_confirmations


def _conf(cid, conf_type=3, creator_id=None):
    row = {"id": str(cid), "nonce": f"n-{cid}", "type": conf_type}
    if creator_id is not None:
        row["creator_id"] = str(creator_id)
    return row


def test_old_trade_and_market_confirmations_are_never_selected():
    before = [_conf("old-trade", 2), _conf("old-market", 3, "9")]
    after = before + [_conf("new-market", 3, "100")]
    selected, error = select_new_listing_confirmations(
        before,
        after,
        [{"listing_id": "100", "requires_confirmation": True}],
    )
    assert error == ""
    assert [row["id"] for row in selected] == ["new-market"]


def test_exact_creator_match_rejects_ambiguous_confirmation():
    after = [_conf("one", 3, "100"), _conf("two", 3, "100")]
    selected, error = select_new_listing_confirmations(
        [], after, [{"listing_id": "100", "requires_confirmation": True}]
    )
    assert selected == []
    assert "无法唯一匹配" in error


def test_fallback_requires_exact_market_only_count():
    listings = [
        {"listing_id": "", "requires_confirmation": True},
        {"listing_id": "", "requires_confirmation": True},
    ]
    selected, error = select_new_listing_confirmations(
        [], [_conf("one"), _conf("two")], listings
    )
    assert error == ""
    assert len(selected) == 2

    selected, error = select_new_listing_confirmations(
        [], [_conf("one"), _conf("trade", 2)], listings
    )
    assert selected == []
    assert error


def test_missing_type_fails_closed():
    selected, error = select_new_listing_confirmations(
        [], [{"id": "new", "nonce": "n"}],
        [{"listing_id": "", "requires_confirmation": True}],
    )
    assert selected == []
    assert error


def test_unconfirmed_success_is_not_inferred_as_requiring_confirmation():
    selected, error = select_new_listing_confirmations(
        [], [_conf("new")],
        [{"listing_id": "", "requires_confirmation": False}],
    )
    assert selected == []
    assert "没有明确要求" in error


def test_legacy_accept_all_is_hard_disabled_without_network_request():
    bot = SteamConfirmer.__new__(SteamConfirmer)

    ok, count, error = bot.accept_all([_conf("unrelated")])

    assert ok is False
    assert count == 0
    assert "已禁用" in error


def test_accept_selected_submits_only_caller_selected_confirmations():
    submitted = {}

    class Response:
        @staticmethod
        def json():
            return {"success": True}

    class Session:
        @staticmethod
        def post(url, params, files, timeout):
            submitted.update(url=url, params=params, files=files, timeout=timeout)
            return Response()

    bot = SteamConfirmer.__new__(SteamConfirmer)
    bot.device_id = "device"
    bot.steam_id = "steam"
    bot.session = Session()
    bot._signature = lambda _tag, _ts: "signature"

    ok, count, error = bot.accept_selected([_conf("selected", creator_id="100")])

    assert (ok, count, error) == (True, 1, "")
    assert submitted["url"].endswith("/multiajaxop")
    assert [value[1][1] for value in submitted["files"]] == ["selected", "n-selected"]


def test_auto_confirm_skips_fetch_when_confirmation_not_required(monkeypatch):
    from app import sell_pipeline

    class Context:
        def __init__(self):
            self.debug_messages = []

        def debug(self, message, **_kwargs):
            self.debug_messages.append(message)

    class Bot:
        def get_confirmations(self):
            raise AssertionError("无需确认时不应再次读取确认列表")

    monkeypatch.setattr(
        sell_pipeline,
        "load_app_config_validated",
        lambda: {"notify": {"lark_webhook": "test"}},
    )
    ctx = Context()
    sell_pipeline._auto_confirm_listings(
        ctx,
        (Bot(), []),
        [{"listing_id": "", "requires_confirmation": False}],
    )

    assert any("无需处理" in message for message in ctx.debug_messages)
