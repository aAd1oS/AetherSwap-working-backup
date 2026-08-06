from datetime import datetime, timezone
from pathlib import Path

from app.inventory_cs2 import _parse_cooldown


def test_parse_cooldown_supports_current_steam_date_tag():
    raw = (
        "This item is trade-protected and cannot be consumed, modified, "
        "or transferred until [date]1786442400[/date]"
    )

    text, timestamp = _parse_cooldown([{"value": raw}])

    assert text == raw
    assert timestamp == 1786442400
    assert datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat() == "2026-08-11T10:00:00+00:00"


def test_parse_cooldown_keeps_legacy_gmt_format_compatible():
    raw = (
        "This item is trade-protected and cannot be transferred "
        "until Aug 11, 2026 (10:00:00) GMT"
    )

    _, timestamp = _parse_cooldown([{"value": raw}])

    assert timestamp == 1786442400


def test_inventory_unlock_time_uses_fixed_local_format():
    script = (Path(__file__).resolve().parents[1] / "web" / "js" / "main.js").read_text(encoding="utf-8")

    assert "function formatInventoryUnlockTime(d)" in script
    assert "displayTime = formatInventoryUnlockTime(d);" in script
