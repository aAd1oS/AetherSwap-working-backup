"""Persist the latest trustworthy BUFF available-funds observation."""

from __future__ import annotations

import json
import math
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional


_CACHE_FILE = Path(__file__).resolve().parents[2] / "config" / "buff_balance_cache.json"
_LOCK = threading.Lock()
_STALE_AFTER_SECONDS = 15 * 60


def _amount(value: Any) -> Optional[float]:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(amount) or amount < 0:
        return None
    return round(amount, 2)


def _load_unlocked() -> dict:
    if not _CACHE_FILE.exists():
        return {}
    try:
        data = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_unlocked(data: dict) -> None:
    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = _CACHE_FILE.with_suffix(_CACHE_FILE.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp_path, _CACHE_FILE)
    except OSError:
        # Balance display is observational and must never interrupt a purchase.
        return


def _public_view(data: dict) -> dict:
    balance = _amount(data.get("balance"))
    updated_at = _amount(data.get("updated_at"))
    age_seconds = max(0.0, time.time() - updated_at) if updated_at else None
    return {
        "has_value": balance is not None,
        "balance": balance,
        "updated_at": updated_at,
        "source": str(data.get("source") or ""),
        "estimated": data.get("source") == "confirmed_purchase_estimate",
        "stale": age_seconds is None or age_seconds > _STALE_AFTER_SECONDS,
        "uncertain": bool(data.get("uncertain", False)),
        "uncertainty_reason": str(data.get("uncertainty_reason") or ""),
        "last_observed_balance": _amount(data.get("last_observed_balance")),
        "last_observation_at": _amount(data.get("last_observation_at")),
    }


def get_buff_balance() -> dict:
    with _LOCK:
        return _public_view(_load_unlocked())


def get_buff_balance_probe() -> dict:
    with _LOCK:
        probe = _load_unlocked().get("probe") or {}
        return dict(probe) if isinstance(probe, dict) else {}


def clear_buff_balance_cache() -> None:
    with _LOCK:
        try:
            _CACHE_FILE.unlink(missing_ok=True)
        except OSError:
            pass


def invalidate_buff_balance_observation() -> None:
    """Drop balance data after BUFF credentials change, but keep the read-only probe."""
    with _LOCK:
        data = _load_unlocked()
        probe = data.get("probe") or {}
        if isinstance(probe, dict) and probe:
            _save_unlocked({"probe": dict(probe)})
            return
        try:
            _CACHE_FILE.unlink(missing_ok=True)
        except OSError:
            pass


def record_buff_balance_preview(
    preview: Optional[dict],
    *,
    game: str,
    goods_id: int,
    sell_order_id: str = "",
    price: Any = None,
) -> dict:
    preview = preview if isinstance(preview, dict) else {}
    observed = _amount(preview.get("balance"))
    if observed is None:
        observed = _amount(preview.get("reported_balance"))
    trust_flag = preview.get("balance_observation_trustworthy")
    observation_accepted = observed is not None and trust_flag is not False
    observation_reason = str(
        preview.get("balance_observation_reason")
        or preview.get("reason")
        or "购买预览未返回可核对的账号余额"
    )

    with _LOCK:
        data = _load_unlocked()
        previous_probe = data.get("probe") or {}
        if not isinstance(previous_probe, dict):
            previous_probe = {}
        probe = {
            "game": str(game or "csgo"),
            "goods_id": int(goods_id or 0),
        }
        order_id = str(sell_order_id or "").strip()
        price_text = str(price or "").strip()
        try:
            previous_goods_id = int(previous_probe.get("goods_id") or 0)
        except (TypeError, ValueError):
            previous_goods_id = 0
        same_goods = (
            str(previous_probe.get("game") or "csgo") == probe["game"]
            and previous_goods_id == probe["goods_id"]
        )
        if order_id:
            probe["sell_order_id"] = order_id
        elif same_goods and previous_probe.get("sell_order_id"):
            probe["sell_order_id"] = str(previous_probe["sell_order_id"])
        if price_text:
            probe["price"] = price_text
        elif same_goods and previous_probe.get("price"):
            probe["price"] = str(previous_probe["price"])
        data["probe"] = probe
        data.update({
            "last_observed_balance": observed,
            "last_observation_at": time.time(),
            "last_observation_trusted": observation_accepted,
            "last_observation_reason": observation_reason,
        })
        if observation_accepted:
            data.update({
                "balance": observed,
                "updated_at": time.time(),
                "source": "purchase_preview",
                "uncertain": False,
                "uncertainty_reason": "",
            })
        else:
            data.update({
                "uncertain": True,
                "uncertainty_reason": observation_reason,
            })
        _save_unlocked(data)
        view = _public_view(data)
        view["observation_accepted"] = observation_accepted
        view["observed_balance"] = observed
        return view


def record_buff_balance_amount(amount: Any, *, source: str = "account_asset") -> dict:
    """Persist a trusted account-level balance observation."""
    observed = _amount(amount)
    with _LOCK:
        data = _load_unlocked()
        if observed is None:
            view = _public_view(data)
            view["observation_accepted"] = False
            return view
        now = time.time()
        data.update({
            "balance": observed,
            "updated_at": now,
            "source": str(source or "account_asset"),
            "uncertain": False,
            "uncertainty_reason": "",
            "last_observed_balance": observed,
            "last_observation_at": now,
            "last_observation_trusted": True,
            "last_observation_reason": "BUFF 账户资产接口返回的可用资金",
        })
        _save_unlocked(data)
        view = _public_view(data)
        view["observation_accepted"] = True
        view["observed_balance"] = observed
        return view


def record_confirmed_buff_spend(amount: Any) -> dict:
    spend = _amount(amount)
    with _LOCK:
        data = _load_unlocked()
        current = _amount(data.get("balance"))
        if spend is not None and current is not None:
            data.update({
                "balance": round(max(0.0, current - spend), 2),
                "updated_at": time.time(),
                "source": "confirmed_purchase_estimate",
                "uncertain": False,
                "uncertainty_reason": "",
            })
            _save_unlocked(data)
        return _public_view(data)
